"""Pure git-diff helpers, checked against real git output (offline, subprocess git)."""

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from shipwright.sandbox.git_diff import (
    ZERO_SHA,
    GitDiffError,
    RawEntry,
    git_blob_sha,
    parse_blob_ids,
    parse_raw_diff,
    unpublishable_reason,
    unsafe_entries,
    verify_files_match_diff,
)

DIFF_FLAGS = [
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
    "--no-renames",
    "--binary",
    "--full-index",
    "--text",
]


@dataclass(frozen=True)
class FC:
    path: str
    content: str | None


def git(repo: Path, *args: str, input: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
        cwd=repo,
        input=input,
        check=True,
        capture_output=True,
    ).stdout


@pytest.fixture
def repo(tmp_path) -> tuple[Path, str]:
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "core.autocrlf", "false")
    for name, text in {"a.txt": b"a\n", "b.txt": b"b\n", "d.txt": b"d\n"}.items():
        (r / name).write_bytes(text)
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "init")
    return r, git(r, "rev-parse", "HEAD").decode().strip()


def snapshot(r: Path, base: str) -> tuple[bytes, list[FC]]:
    """What the Workspace does: `add -N .`, the reviewed patch, then the worktree files."""
    git(r, "add", "-N", ".")
    patch = git(r, "diff", *DIFF_FLAGS, base)
    entries = parse_raw_diff(git(r, "diff", "--raw", "-z", "--no-renames", base))
    files = [
        FC(e.path, None if e.status == "D" else (r / e.path).read_bytes().decode("utf-8"))
        for e in entries
    ]
    return patch, files


# ------------------------------------------------------------------ blob sha / raw parsing


@pytest.mark.parametrize("data", [b"", b"a\n", "café ✓\r\n".encode(), bytes(range(256))])
def test_git_blob_sha_matches_git_hash_object(tmp_path, data):
    expected = git(tmp_path, "hash-object", "--stdin", input=data).decode().strip()
    assert git_blob_sha(data) == expected


def test_parse_raw_diff_and_utf8_guard():
    raw = b":100644 100644 a b M\x00app/a.py\x00:000000 100644 0 b A\x00n w.py\x00"
    assert parse_raw_diff(raw) == [
        RawEntry("100644", "100644", "M", "app/a.py"),
        RawEntry("000000", "100644", "A", "n w.py"),
    ]
    with pytest.raises(GitDiffError, match="not valid UTF-8"):
        parse_raw_diff(b":100644 100644 a b M\x00\xff.py\x00")
    with pytest.raises(GitDiffError, match="Unexpected"):
        parse_raw_diff(b":100644 a M\x00x\x00")


@pytest.mark.parametrize(
    ("old", "new", "reason"),
    [
        ("100644", "100644", None),
        ("000000", "100644", None),
        ("100644", "000000", None),
        ("000000", "120000", "symlink"),
        ("100644", "160000", "gitlink (embedded repository or submodule)"),
        ("100644", "100755", "mode change 100644 -> 100755"),
        ("000000", "100755", "new file with mode 100755"),
    ],
)
def test_unpublishable_reason(old, new, reason):
    assert unpublishable_reason(RawEntry(old, new, "M", "p")) == reason


def test_unsafe_entries_names_paths():
    entries = [RawEntry("100644", "100644", "M", "ok"), RawEntry("000000", "120000", "A", "ln")]
    assert unsafe_entries(entries) == ["ln (symlink)"]


# ------------------------------------------------------------------ patch parsing


def test_parse_blob_ids_from_real_patch(repo):
    r, base = repo
    (r / "a.txt").write_bytes(b"a2\n")
    (r / "d.txt").unlink()
    (r / "empty.txt").write_bytes(b"")
    (r / "n w.txt").write_bytes(b"spaced\n")
    (r / "café.txt").write_bytes(b"accent\n")
    patch, _ = snapshot(r, base)
    ids = parse_blob_ids(patch)
    assert set(ids) == {"a.txt", "d.txt", "empty.txt", "n w.txt", "café.txt"}
    assert ids["a.txt"] == (git_blob_sha(b"a\n"), git_blob_sha(b"a2\n"))
    assert ids["d.txt"] == (git_blob_sha(b"d\n"), ZERO_SHA)
    assert ids["empty.txt"] == (ZERO_SHA, "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391")
    assert ids["café.txt"][1] == git_blob_sha(b"accent\n")
    assert b'"a/caf\\303\\251.txt"' in patch  # git C-quoted the path (core.quotePath)


def test_parse_blob_ids_unquotes_escapes():
    index = b"index " + b"1" * 40 + b".." + b"2" * 40
    patch = b'diff --git "a/q\\"u\\tx" "b/q\\"u\\tx"\n' + index + b"\n"
    assert parse_blob_ids(patch) == {'q"u\tx': ("1" * 40, "2" * 40)}


def _section(path: bytes, *header: bytes) -> bytes:
    return b"diff --git a/" + path + b" b/" + path + b"\n" + b"\n".join(header) + b"\n"


@pytest.mark.parametrize(
    ("patch", "match"),
    [
        (_section(b"x", b"--- a/x"), "No index line"),
        (_section(b"x", b"index 1234567..89abcde 100644"), "Unexpected index line"),
        (_section(b"x", b"old mode 100644", b"new mode 100755"), "Mode change"),
        (
            _section(b"x", b"new file mode 100755", b"index " + b"0" * 40 + b".." + b"1" * 40),
            "has mode 100755",
        ),
        (_section(b"x", b"index " + b"1" * 40 + b".." + b"2" * 40) * 2, "Duplicate"),
        (b"diff --git a/x b/y\n", "Unexpected diff header"),
    ],
)
def test_parse_blob_ids_fails_closed(patch, match):
    with pytest.raises(GitDiffError, match=match):
        parse_blob_ids(patch)


def test_index_like_content_lines_are_ignored():
    sha = git_blob_sha(b"x\n")
    fake_index = b"+index " + b"f" * 40 + b".." + b"f" * 40
    patch = _section(
        b"x",
        b"index " + b"1" * 40 + b".." + sha.encode() + b" 100644",
        b"--- a/x",
        b"+++ b/x",
        b"@@ -1 +1 @@",
        fake_index,
    )
    assert parse_blob_ids(patch) == {"x": ("1" * 40, sha)}


# ------------------------------------------------------------------ verify


def test_verify_accepts_exact_change_set(repo):
    r, base = repo
    (r / "a.txt").write_bytes("a2 ✓\n".encode())
    (r / "d.txt").unlink()
    (r / "empty.txt").write_bytes(b"")
    patch, files = snapshot(r, base)
    assert [f.path for f in files] == ["a.txt", "d.txt", "empty.txt"]
    assert verify_files_match_diff(patch, files) == []
    assert verify_files_match_diff(b"", []) == []


def test_verify_reports_every_mismatch(repo):
    r, base = repo
    (r / "a.txt").write_bytes(b"a2\n")
    (r / "d.txt").unlink()
    patch, _ = snapshot(r, base)
    problems = verify_files_match_diff(
        patch, [FC("a.txt", "tampered\n"), FC("extra.txt", "x"), FC("extra.txt", "x")]
    )
    assert any(p.startswith("a.txt: content (blob") for p in problems)
    assert "extra.txt: not in the approved diff" in problems
    assert "extra.txt: listed twice" in problems
    assert "d.txt: in the approved diff but not published" in problems


def test_verify_checks_deletions_both_ways(repo):
    r, base = repo
    (r / "a.txt").write_bytes(b"a2\n")
    (r / "d.txt").unlink()
    patch, _ = snapshot(r, base)
    problems = verify_files_match_diff(patch, [FC("a.txt", None), FC("d.txt", "d\n")])
    assert problems == [
        "a.txt: deleted, but the approved diff modifies it",
        "d.txt: has content, but the approved diff deletes it",
    ]


def test_verify_turns_parse_errors_into_problems():
    problems = verify_files_match_diff(b"diff --git a/x b/x\n", [FC("x", "")])
    assert problems == ["No index line for x"]


# ------------------------------------------------------------------ the v1 hole, reproduced


def test_clean_filter_hides_content_from_diff_but_verify_refuses(repo, tmp_path):
    r, base = repo
    attrs = tmp_path / "attrs"
    attrs.write_bytes(b"a.txt filter=x\n")
    git(r, "config", "core.attributesFile", attrs.as_posix())
    git(r, "config", "filter.x.clean", "git hash-object --stdin")  # diff shows a sha, not code
    (r / "a.txt").write_bytes(b"import os; os.system('evil')\n")
    patch, files = snapshot(r, base)
    assert b"evil" not in patch  # the human would approve a harmless-looking diff
    assert [f.path for f in files] == ["a.txt"] and "evil" in files[0].content
    (problem,) = verify_files_match_diff(patch, files)
    assert problem.startswith("a.txt: content (blob")


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_index_flags_hide_worktree_but_verify_refuses(repo, flag):
    r, base = repo
    (r / "b.txt").write_bytes(b"b-approved\n")
    git(r, "add", "b.txt")
    git(r, "update-index", flag, "b.txt")
    (r / "b.txt").write_bytes(b"b-secret\n")
    patch, files = snapshot(r, base)
    assert b"+b-approved" in patch and b"secret" not in patch
    assert files == [FC("b.txt", "b-secret\n")]
    (problem,) = verify_files_match_diff(patch, files)
    assert "b.txt: content" in problem
