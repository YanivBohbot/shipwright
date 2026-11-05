"""Guarded PR publication: pushes exactly the diff the human approved at the review gate."""

import re
from typing import Annotated, Any

from langchain.tools import InjectedState
from langchain_core.tools import BaseTool, StructuredTool

from shipwright.config import Settings
from shipwright.integrations.github import FileChange, GitHubGateway
from shipwright.tickets import parse_ticket_key
from shipwright.workspace import Workspace, WorkspaceError

BASE_BRANCH = "main"
CHANGELOG_PATH = "CHANGELOG.md"
NOT_APPROVED = (
    "REFUSED: the current changes were not approved at the review gate. "
    "Call request_human_review first; nothing was published."
)
DIFF_CHANGED = (
    "REFUSED: the working tree changed since the human approved it (or the approved changes "
    "are gone, e.g. the sandbox was recreated). Nothing was published. Check the workspace "
    "and call request_human_review again."
)
NO_FILES = "REFUSED: there are no changed files to publish. Nothing was published."

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


def _title(key: str, title: str) -> str:
    stripped = re.sub(rf"^\s*\[{re.escape(key)}\]\s*", "", title, flags=re.IGNORECASE)
    return f"[{key}] {stripped.strip()}"


def _body(key: str, body: str) -> str:
    if key.lower() in body.lower():
        return body
    return f"Linked ticket: {key}\n\n{body}" if body.strip() else f"Linked ticket: {key}"


def make_publish_tool(workspace: Workspace, github: GitHubGateway, settings: Settings) -> BaseTool:
    repo = settings.shipwright_repo
    project = settings.jira_project

    async def publish_pull_request(
        ticket_key: str,
        title: str,
        body: str,
        changelog_entry: str,
        state: Annotated[dict[str, Any], InjectedState],
        draft: bool = False,
    ) -> str:
        """Publish the human-approved changes as a GitHub pull request against main.

        Requires human approval AND a prior approval at request_human_review for the exact
        current diff: it refuses if the working tree changed since. Pushes every changed
        file plus CHANGELOG.md (changelog_entry added under "## [Unreleased]") to branch
        shipwright/<ticket-key> (reused if it exists) and opens the PR, or updates the PR
        already open for that branch. Returns the PR URL, or a "REFUSED: ..." / error text.

        Args:
            ticket_key: Jira key of the ticket, e.g. "SHIP-2".
            title: PR title (it is prefixed with "[SHIP-2] " automatically).
            body: PR description in Markdown: acceptance criteria checked, test results,
                reviewer report. The ticket key is added if missing.
            changelog_entry: One-line CHANGELOG entry in English, e.g. "Fix 500 on empty
                task title (SHIP-1)".
            draft: Open the PR as a draft (e.g. when tests still fail and the human chose
                to publish a draft anyway). Ignored when an open PR is updated.
        """
        try:
            key = parse_ticket_key(ticket_key, project)
        except ValueError:
            return f"Ticket {ticket_key.strip()} not found in project {project}."
        if not changelog_entry.strip():
            return "REFUSED: changelog_entry must not be empty. Nothing was published."
        approved = (state or {}).get("approved_diff_hash")
        if not approved:
            return NOT_APPROVED

        try:
            if await workspace.diff_hash() != approved:
                return DIFF_CHANGED
            files = await workspace.changed_files()
            existing = next((f for f in files if f.path == CHANGELOG_PATH), None)
            if existing is not None and existing.content is not None:
                changelog = existing.content
            elif existing is not None:
                changelog = None  # deleted by the developer: recreate it
            else:
                changelog = await workspace.read_repo_file(CHANGELOG_PATH)
            # TOCTOU guard: what we read must still be exactly what was approved.
            if await workspace.diff_hash() != approved:
                return DIFF_CHANGED
        except WorkspaceError as e:
            return f"REFUSED: the workspace could not be read ({e}). Nothing was published."
        if not files:
            return NO_FILES

        to_push = [f for f in files if f.path != CHANGELOG_PATH]
        to_push.append(
            FileChange(CHANGELOG_PATH, insert_changelog_entry(changelog, changelog_entry))
        )
        branch = f"shipwright/{key.lower()}"
        pr_title, pr_body = _title(key, title), _body(key, body)

        step = "checking the branch"
        try:
            if not await github.branch_exists(repo, branch):
                step = "creating the branch"
                await github.create_branch(repo, branch, BASE_BRANCH)
            step = "pushing the files"
            await github.push_files(repo, branch, to_push, f"{key}: {pr_title[len(key) + 3 :]}")
            step = "looking up an open pull request"
            url = await github.find_open_pull_request(repo, branch)
            if url:
                step = "updating the pull request"
                await github.update_pull_request(repo, url, pr_title, pr_body)
                return url
            step = "creating the pull request"
            return await github.create_pull_request(
                repo, branch, BASE_BRANCH, pr_title, pr_body, draft=draft
            )
        except Exception as e:  # GitHub/MCP error: report to the model, never raise
            return f"Publishing failed while {step} on {repo} ({branch}): {e}"

    return StructuredTool.from_function(coroutine=publish_pull_request, name="publish_pull_request")
