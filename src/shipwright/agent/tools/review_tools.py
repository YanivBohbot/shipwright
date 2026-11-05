"""Review tools: `show_workspace_diff` (read-only) and `request_human_review` (the review gate).

Deep Agents concept: **human-in-the-loop with `interrupt()` inside a tool**, plus a tool that
writes custom **state** through `Command(update=...)`. The gate shows the human the exact diff
and its sha256 `diff_hash`; an approval must echo that hash (see `agent/hitl.py`) and is stored
as `approved_diff_hash` / `approved_ticket_key`, which `publish_pull_request` requires.
"""

from typing import Annotated, Any

from langchain.tools import InjectedToolCallId
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.types import Command, interrupt

from shipwright.domain.tickets import parse_ticket_key
from shipwright.sandbox.workspace import Workspace, WorkspaceError

ALLOWED = ["approve", "feedback", "reject"]
MAX_DIFF_CHARS = 20_000
NO_CHANGES = "No changes."
NOTHING_TO_REVIEW = "Nothing to review: the working tree has no changes."
APPROVED = "APPROVED. You may now call publish_pull_request."
FEEDBACK = (
    "CHANGES REQUESTED by human: {message}. Send this feedback to the developer, then request "
    "review again. If the feedback is a reusable preference, record it in "
    "/memories/preferences.md."
)
REJECTED = "REJECTED by human: {message}. Stop working on this ticket and report."
STALE = (
    "NOT APPROVED: the working tree changed during the review (the diff the human approved is "
    "not the current diff). Nothing was approved; call request_human_review again."
)


def parse_review_decision(payload: Any) -> tuple[str, str, str | None]:
    """Validate a resume value; return `(type, message, echoed diff_hash)`. Raises ValueError.

    Also accepts the value wrapped as `{"decisions": [value]}` or `[value]`.
    """
    if isinstance(payload, dict) and "decisions" in payload:
        payload = payload["decisions"]
    if isinstance(payload, list):
        if len(payload) != 1:
            raise ValueError(f"Expected exactly one review decision, got {len(payload)}")
        payload = payload[0]
    if not isinstance(payload, dict) or payload.get("type") not in ALLOWED:
        raise ValueError(f"Invalid review decision {payload!r}: 'type' must be one of {ALLOWED}")
    kind = payload["type"]
    message = payload.get("message", "")
    if not isinstance(message, str) or (kind == "feedback" and not message.strip()):
        raise ValueError(f"Review decision {kind!r} needs a string 'message'")
    echoed = payload.get("diff_hash")
    if echoed is not None and not isinstance(echoed, str):
        raise ValueError("'diff_hash' must be a string")
    if kind == "approve" and not echoed:
        raise ValueError("approve requires diff_hash from the interrupt payload")
    return kind, message.strip(), echoed


def build_review_tools(workspace: Workspace, jira_project: str) -> list[BaseTool]:
    async def show_workspace_diff() -> str:
        """Show the full unified diff of the workspace repo against its base commit.

        Read-only. Includes modified, deleted and new files. Returns "No changes." when nothing
        changed; output longer than 20 000 characters ends with "[diff truncated]".
        """
        try:
            diff, _ = await workspace.diff_and_hash()
        except WorkspaceError as e:
            return f"Cannot show the diff: {e}"
        if not diff.strip():
            return NO_CHANGES
        return diff if len(diff) <= MAX_DIFF_CHARS else diff[:MAX_DIFF_CHARS] + "\n[diff truncated]"

    async def request_human_review(
        ticket_key: str,
        summary: str,
        test_report: str,
        review_report: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command:
        """Ask the human to review the exact current diff before anything is published.

        Call it once the tests pass and the reviewer subagent has reported. If the tests still
        fail after the developer's attempts, call it anyway and say the tests are FAILING in
        `summary` and `test_report`. The human sees the full diff, your summary, the test
        report and the review report, and answers approve / feedback / reject. On approval,
        publish_pull_request becomes possible for this ticket; ANY later change to the working
        tree voids it. On feedback, relay it to the developer and call this tool again. On
        reject, stop working on the ticket.

        Args:
            ticket_key: Jira key of the ticket being worked on, e.g. "SHIP-2".
            summary: What was changed and why, mapped to the acceptance criteria (English).
            test_report: Result of the test run (summary line and relevant output).
            review_report: The reviewer subagent's verdict, criteria checks and risks.
        """

        def reply(text: str, approved_hash: str | None = None, key: str | None = None) -> Command:
            return Command(
                update={
                    "approved_diff_hash": approved_hash,
                    "approved_ticket_key": key,
                    "messages": [ToolMessage(text, tool_call_id=tool_call_id)],
                }
            )

        try:
            key = parse_ticket_key(ticket_key, jira_project)
        except ValueError:
            return reply(f"Ticket {ticket_key.strip()} not found in project {jira_project}.")
        # LangGraph re-runs this tool from the top on resume: the hash is recomputed then and
        # must equal the one the human saw (echoed in the resume value).
        try:
            diff, diff_hash = await workspace.diff_and_hash()
        except WorkspaceError as e:  # unreadable, or unpublishable (symlink, mode change...)
            return reply(f"Cannot review: {e}. Nothing was approved.")
        if not diff.strip():
            return reply(NOTHING_TO_REVIEW)

        decision = interrupt(
            {
                "kind": "review_gate",
                "ticket_key": key,
                "summary": summary,
                "diff": diff,
                "diff_hash": diff_hash,
                "test_report": test_report,
                "review_report": review_report,
                "allowed": list(ALLOWED),
            }
        )
        kind, message, echoed = parse_review_decision(decision)
        if kind == "approve":
            return reply(APPROVED, diff_hash, key) if echoed == diff_hash else reply(STALE)
        if kind == "feedback":
            return reply(FEEDBACK.format(message=message.rstrip(".")))
        return reply(REJECTED.format(message=message.rstrip(".") or "no reason given"))

    return [
        StructuredTool.from_function(coroutine=show_workspace_diff, name="show_workspace_diff"),
        StructuredTool.from_function(coroutine=request_human_review, name="request_human_review"),
    ]
