"""`publish_pull_request`: push EXACTLY the human-approved diff to a new branch and open a PR.

Deep Agents concept: a guarded write tool that reads custom **state** (`InjectedState`) and
clears it with `Command(update=...)`. Guards, in order (any failure -> "REFUSED: ...", nothing
pushed): an approval for this ticket exists; `Workspace.files_matching_approval` recomputes
the patch, checks its sha256 against the approval and blob-verifies every file against it;
main has not moved; the new branch starts at the workspace base. CHANGELOG.md is the one
documented addition (read from the base commit unless the approved diff changes it).
"""

import re
from typing import Annotated, Any

from langchain.tools import InjectedState, InjectedToolCallId
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.types import Command

from shipwright.domain.changelog import insert_changelog_entry
from shipwright.domain.tickets import branch_root, parse_ticket_key
from shipwright.integrations.github.gateway import FileChange, GitHubGateway
from shipwright.sandbox.workspace import Workspace, WorkspaceError

BASE_BRANCH = "main"
CHANGELOG_PATH = "CHANGELOG.md"
MAX_REVISIONS = 20  # shipwright/<key>, then -r2 ... -r20
NOT_APPROVED = (
    "REFUSED: the current changes were not approved at the review gate. "
    "Call request_human_review first; nothing was published."
)
WRONG_TICKET = "REFUSED: the approved changes belong to {approved}, not {key}. Nothing published."
NOT_MATCHING = (
    "REFUSED: the workspace no longer matches the diff the human approved ({error}). Nothing "
    "was published. Check the workspace and call request_human_review again."
)
NO_FILES = "REFUSED: there are no changed files to publish. Nothing was published."
BASE_MOVED = (
    "REFUSED: main moved since the workspace was cloned (main {main}, base {base}); publishing "
    "would revert upstream changes. Nothing was published. Restart on a fresh thread."
)
BRANCH_MOVED = (
    "REFUSED: the new branch {branch} starts at {sha}, not at the workspace base {base}. No "
    "files were pushed; the empty branch can be deleted. Restart on a fresh thread."
)


def next_branch(root: str, existing: set[str]) -> tuple[str | None, list[str]]:
    """`(first free branch among root, root-r2..root-r20 or None, earlier existing ones)`."""
    candidates = [root] + [f"{root}-r{n}" for n in range(2, MAX_REVISIONS + 1)]
    free = next((c for c in candidates if c not in existing), None)
    return free, [c for c in candidates if c in existing]


def pr_title(key: str, title: str) -> str:
    stripped = re.sub(rf"^\s*\[{re.escape(key)}\]\s*", "", title, flags=re.IGNORECASE)
    return f"[{key}] {stripped.strip()}"


def pr_body(key: str, body: str) -> str:
    if re.search(rf"(?<![\w-]){re.escape(key)}(?!\d)", body, re.IGNORECASE):
        return body
    return f"Linked ticket: {key}\n\n{body}" if body.strip() else f"Linked ticket: {key}"


async def _approved_files(workspace: Workspace, approved: str) -> tuple[list[FileChange], str]:
    """Verified files + the CHANGELOG text to extend (a WorkspaceError means: refuse)."""
    files = await workspace.files_matching_approval(approved)
    changelog = next((f for f in files if f.path == CHANGELOG_PATH), None)
    if changelog is None:
        return files, await workspace.read_base_file(CHANGELOG_PATH) or ""
    return [f for f in files if f is not changelog], changelog.content or ""


def build_github_tools(workspace: Workspace, github: GitHubGateway, project: str) -> list[BaseTool]:
    repo = workspace.repo  # publish to the repo that was cloned and reviewed, never another one

    async def publish_pull_request(
        ticket_key: str,
        title: str,
        body: str,
        changelog_entry: str,
        state: Annotated[dict[str, Any], InjectedState],
        tool_call_id: Annotated[str, InjectedToolCallId],
        draft: bool = False,
    ) -> str | Command:
        """Publish the human-approved changes as a NEW pull request against main.

        Requires human approval AND a prior APPROVED from request_human_review for the exact
        current diff of THIS ticket. Pushes the changed files plus CHANGELOG.md (with
        changelog_entry under "## [Unreleased]") to a fresh branch shipwright/<key>, or
        shipwright/<key>-r2, -r3... if earlier ones exist (the PR then says which PR it
        supersedes). Returns the PR URL, or "REFUSED: ..." / an error text: then follow it,
        do not retry blindly. The approval is consumed by a successful publication.

        Args:
            ticket_key: Jira key of the ticket, e.g. "SHIP-2".
            title: PR title (it is prefixed with "[SHIP-2] " automatically).
            body: PR description in Markdown (see the pr-template skill).
            changelog_entry: One English line, e.g. "Fix 500 on empty assignee (SHIP-1)".
            draft: Open the PR as a draft (e.g. the human approved despite failing tests).
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
        if (approved_key := state.get("approved_ticket_key")) != key:
            return WRONG_TICKET.format(approved=approved_key or "(unknown)", key=key)
        try:
            files, changelog = await _approved_files(workspace, approved)
            base_sha = await workspace.base_sha()
        except WorkspaceError as e:
            return NOT_MATCHING.format(error=e)
        if not files:
            return NO_FILES
        to_push = [
            *files,
            FileChange(CHANGELOG_PATH, insert_changelog_entry(changelog, changelog_entry)),
        ]
        title, body = pr_title(key, title), pr_body(key, body)

        step, branch = "reading the head of main", branch_root(key)
        try:
            if (main_sha := await github.get_branch_sha(repo, BASE_BRANCH)) != base_sha:
                return BASE_MOVED.format(main=main_sha[:12], base=base_sha[:12])
            step = "choosing a branch"
            free, earlier = next_branch(branch, await github.list_branch_names(repo))
            if free is None:
                last = f"{branch}-r{MAX_REVISIONS}"
                return f"REFUSED: branches {branch} to {last} exist; delete old ones."
            branch = free
            step = "looking up superseded pull requests"
            old_prs = [u for b in earlier if (u := await github.find_open_pull_request(repo, b))]
            if old_prs:
                body += "\n\n" + "\n".join(f"Supersedes {u}" for u in old_prs)
            step = "creating the branch"
            await github.create_branch(repo, branch, BASE_BRANCH)
            if (branch_sha := await github.get_branch_sha(repo, branch)) != base_sha:
                return BRANCH_MOVED.format(branch=branch, sha=branch_sha[:12], base=base_sha[:12])
            step = "pushing the files"
            await github.push_files(repo, branch, to_push, f"{key}: {title[len(key) + 3 :]}")
            step = "creating the pull request"
            url = await github.create_pull_request(
                repo, branch, BASE_BRANCH, title, body, draft=draft
            )
        except Exception as e:  # GitHub/MCP error: report to the model, never raise
            return f"Publishing failed while {step} on {repo} ({branch}): {e}"
        return Command(
            update={
                "approved_diff_hash": None,
                "approved_ticket_key": None,
                "messages": [ToolMessage(url, tool_call_id=tool_call_id)],
            }
        )

    return [
        StructuredTool.from_function(coroutine=publish_pull_request, name="publish_pull_request")
    ]
