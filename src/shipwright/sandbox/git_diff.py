"""Pure git-diff helpers for the publish guard: parse `--raw`, refuse unsafe modes, blob shas.

`verify_files_match_diff` binds the files about to be pushed to the exact diff bytes the human
approved: each file's git blob sha must equal the `index <old>..<new>` id of that path.
"""

import hashlib
import re
from dataclasses import dataclass

REGULAR_MODE = "100644"
ABSENT_MODE = "000000"
SYMLINK_MODE = "120000"
GITLINK_MODE = "160000"
ZERO_SHA = "0" * 40

_INDEX_RE = re.compile(rb"^index ([0-9a-f]{40})\.\.([0-9a-f]{40})(?: ([0-7]{6}))?$")
_HEADER_END = (b"--- ", b"+++ ", b"@@", b"GIT binary patch", b"Binary files")
_ESCAPES = {b"a": 7, b"b": 8, b"t": 9, b"n": 10, b"v": 11, b"f": 12, b"r": 13, b'"': 34, b"\\": 92}


class GitDiffError(ValueError):
    """Git output could not be parsed safely."""


@dataclass(frozen=True)
class RawEntry:
    """One `git diff --raw` entry vs the base commit."""

    old_mode: str
    new_mode: str
    status: str  # A / M / D / T ...
    path: str


def parse_raw_diff(raw: bytes) -> list[RawEntry]:
    """Parse `git diff --raw -z --no-renames` output (`:old new sha sha STATUS\\0path\\0`)."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise GitDiffError("A changed file path is not valid UTF-8") from e
    fields = text.split("\0")
    entries: list[RawEntry] = []
    for meta, path in zip(fields[0::2], fields[1::2], strict=False):
        if not meta:
            continue
        parts = meta.lstrip(":").split()
        if len(parts) != 5 or not path:
            raise GitDiffError(f"Unexpected git diff --raw entry: {meta!r} {path!r}")
        entries.append(RawEntry(parts[0], parts[1], parts[4][:1], path))
    return entries


def unpublishable_reason(entry: RawEntry) -> str | None:
    """Why `entry` cannot be published as a plain 100644 write/delete, or None if it can."""
    modes = (entry.old_mode, entry.new_mode)
    if SYMLINK_MODE in modes:
        return "symlink"
    if GITLINK_MODE in modes:
        return "gitlink (embedded repository or submodule)"
    if ABSENT_MODE not in modes and entry.old_mode != entry.new_mode:
        return f"mode change {entry.old_mode} -> {entry.new_mode}"
    if entry.old_mode == ABSENT_MODE and entry.new_mode != REGULAR_MODE:
        return f"new file with mode {entry.new_mode}"
    return None


def unsafe_entries(entries: list[RawEntry]) -> list[str]:
    """`"<path> (<reason>)"` for every entry that cannot be published exactly as diffed."""
    return [f"{e.path} ({reason})" for e in entries if (reason := unpublishable_reason(e))]


def git_blob_sha(data: bytes) -> str:
    """The id `git hash-object --no-filters` gives `data`: sha1 of `blob <len>\\0<data>`."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _unquote(token: bytes) -> bytes:
    """Undo git's C-style path quoting (`"a/x\\"y"`, octal `\\303\\251`); plain tokens as-is."""
    if not token.startswith(b'"'):
        return token
    if len(token) < 2 or not token.endswith(b'"'):
        raise GitDiffError(f"Malformed quoted path: {token!r}")
    body, out, i = token[1:-1], bytearray(), 0
    while i < len(body):
        c = body[i : i + 1]
        if c != b"\\":
            out += c
            i += 1
        elif body[i + 1 : i + 2] in _ESCAPES:
            out.append(_ESCAPES[body[i + 1 : i + 2]])
            i += 2
        elif re.fullmatch(rb"[0-3][0-7]{2}", body[i + 1 : i + 4]):
            out.append(int(body[i + 1 : i + 4], 8))
            i += 4
        else:
            raise GitDiffError(f"Malformed quoted path: {token!r}")
    return bytes(out)


def _header_path(rest: bytes) -> str:
    """Path of a `diff --git a/P b/P` header (no renames: both halves name the same P)."""
    half = (len(rest) - 1) // 2
    a, sep, b = rest[:half], rest[half : half + 1], rest[half + 1 :]
    a, b = _unquote(a), _unquote(b)
    if sep != b" " or not a.startswith(b"a/") or not b.startswith(b"b/") or a[2:] != b[2:]:
        raise GitDiffError(f"Unexpected diff header: diff --git {rest!r}")
    try:
        return a[2:].decode("utf-8")
    except UnicodeDecodeError as e:
        raise GitDiffError("A changed file path is not valid UTF-8") from e


def parse_blob_ids(patch: bytes) -> dict[str, tuple[str, str]]:
    """`{path: (old_blob, new_blob)}` from a `--full-index --no-renames` patch.

    Each file section must carry exactly one full `index` line; mode lines are refused.
    """
    ids: dict[str, tuple[str, str]] = {}
    path: str | None = None
    in_header = False
    for line in patch.split(b"\n"):  # never splitlines(): a lone \r must not start a line
        if line.startswith(b"diff --git "):
            if path is not None and path not in ids:
                raise GitDiffError(f"No index line for {path}")
            path, in_header = _header_path(line[len(b"diff --git ") :]), True
            if path in ids:
                raise GitDiffError(f"Duplicate diff section for {path}")
        elif in_header and line.startswith(_HEADER_END):
            in_header = False
        elif in_header and line.startswith((b"old mode ", b"new mode ")):
            raise GitDiffError(f"Mode change in diff for {path}")
        elif in_header and line.startswith(b"new file mode ") and line[14:] != b"100644":
            raise GitDiffError(f"New file {path} has mode {line[14:].decode(errors='replace')}")
        elif in_header and line.startswith(b"index "):
            m = _INDEX_RE.match(line)
            if path is None or m is None or path in ids:
                raise GitDiffError(f"Unexpected index line for {path}: {line!r}")
            ids[path] = (m.group(1).decode(), m.group(2).decode())
    if path is not None and path not in ids:
        raise GitDiffError(f"No index line for {path}")
    return ids


def verify_files_match_diff(patch: bytes, files: list) -> list[str]:
    """Problems that make `files` differ from the approved `patch` ([] = they match exactly).

    `files` are FileChange-like (`.path`, `.content`: str, or None for a deletion).
    """
    try:
        ids = parse_blob_ids(patch)
    except GitDiffError as e:
        return [str(e)]
    problems: list[str] = []
    seen: set[str] = set()
    for f in files:
        if f.path in seen:
            problems.append(f"{f.path}: listed twice")
            continue
        seen.add(f.path)
        if f.path not in ids:
            problems.append(f"{f.path}: not in the approved diff")
            continue
        new = ids[f.path][1]
        if f.content is None:
            if new != ZERO_SHA:
                problems.append(f"{f.path}: deleted, but the approved diff modifies it")
        elif new == ZERO_SHA:
            problems.append(f"{f.path}: has content, but the approved diff deletes it")
        elif (actual := git_blob_sha(f.content.encode("utf-8"))) != new:
            problems.append(
                f"{f.path}: content (blob {actual}) differs from the approved diff (blob {new})"
            )
    problems += [f"{p}: in the approved diff but not published" for p in ids if p not in seen]
    return problems
