"""CHANGELOG.md editing: add one bullet under `## [Unreleased]` (Keep a Changelog style).

Pure text function, used by `publish_pull_request`: the CHANGELOG entry is the one addition
published on top of the diff the human approved.
"""

import re

_UNRELEASED_RE = re.compile(r"^##\s*\[unreleased\]", re.IGNORECASE)
_H1_RE = re.compile(r"^#\s+\S")


def _bullet(entry: str) -> str:
    text = " ".join(entry.split())
    text = re.sub(r"^[-*]\s+", "", text)
    return f"- {text}"


def insert_changelog_entry(text: str | None, entry: str) -> str:
    """Insert `entry` as the first bullet under `## [Unreleased]`.

    The section is created right after the `# ...` title when missing (at the top if there
    is no title); the file is created when `text` is empty/None. Line endings are preserved
    and an entry already present verbatim is not duplicated.
    """
    bullet = _bullet(entry)
    if not text or not text.strip():
        return f"# Changelog\n\n## [Unreleased]\n\n{bullet}\n"
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    if bullet in (ln.rstrip() for ln in lines):
        return text

    def rest_after(i: int) -> list[str]:
        rest = lines[i:]
        while rest and not rest[0].strip():
            rest = rest[1:]
        return rest

    idx = next((i for i, ln in enumerate(lines) if _UNRELEASED_RE.match(ln)), None)
    if idx is not None:
        rest = rest_after(idx + 1)
        sep = [] if not rest or rest[0].lstrip().startswith(("- ", "* ")) else [""]
        new = [*lines[: idx + 1], "", bullet, *sep, *rest]
    else:
        title = next((i for i, ln in enumerate(lines) if _H1_RE.match(ln)), None)
        head = lines[: title + 1] + [""] if title is not None else []
        rest = rest_after(title + 1 if title is not None else 0)
        new = [*head, "## [Unreleased]", "", bullet, *([""] if rest else []), *rest]
    return newline.join(new) + newline
