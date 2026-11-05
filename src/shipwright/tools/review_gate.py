"""Human review gate: shows the exact diff and records the hash of the approved diff.

Interrupt payload (to the human):
    {"kind": "review_gate", "ticket_key": "SHIP-2", "summary": ..., "diff": <full diff>,
     "diff_hash": <sha256 hex of the raw diff bytes>, "test_report": ..., "review_report": ...,
     "allowed": ["approve", "feedback", "reject"]}

Resume payload (from the human, via `Command(resume=...)`):
    {"type": "approve", "diff_hash": "<diff_hash copied from the interrupt payload>"}
    {"type": "feedback", "message": "<what to change>"}
    {"type": "reject", "message": "<why>"}                 (message optional)
The same dict may be wrapped as {"decisions": [<dict>]} (langchain HITL middleware shape) or as
a one-element list. `diff_hash` is MANDATORY for approve: the approval is bound to the diff the
human actually saw. If the tree changed during the pause (the hash recomputed on resume differs)
the approval is refused and review must be requested again. Anything else raises ValueError.
"""

from typing import Annotated, Any

from langchain.tools import InjectedToolCallId
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.types import Command, interrupt

from shipwright.tickets import parse_ticket_key
from shipwright.workspace import Workspace, WorkspaceError

ALLOWED = ["approve", "feedback", "reject"]
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
APPROVE_NEEDS_HASH = "approve requires diff_hash from the interrupt payload"


def parse_review_decision(payload: Any) -> tuple[str, str, str | None]:
    """Validate a resume payload; return `(type, message, echoed diff_hash or None)`.

    See the module docstring for the accepted shapes. Raises ValueError on anything else,
    including an approve without `diff_hash`.
    """
    if isinstance(payload, dict) and "decisions" in payload:
        payload = payload["decisions"]
    if isinstance(payload, list):
        if len(payload) != 1:
            raise ValueError(f"Expected exactly one review decision, got {len(payload)}")
        payload = payload[0]
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid review decision {payload!r}: expected a dict with 'type'")
    kind = payload.get("type")
    if kind not in ALLOWED:
        raise ValueError(f"Invalid review decision type {kind!r}: expected one of {ALLOWED}")
    message = payload.get("message", "" if kind != "feedback" else None)
    if not isinstance(message, str):
        raise ValueError(f"Review decision {kind!r} needs a string 'message'")
    message = message.strip()
    if kind == "feedback" and not message:
        raise ValueError("Review decision 'feedback' needs a non-empty 'message'")
    echoed = payload.get("diff_hash")
    if echoed is not None and not isinstance(echoed, str):
        raise ValueError("'diff_hash' must be a string")
    if kind == "approve" and not echoed:
        raise ValueError(APPROVE_NEEDS_HASH)
    return kind, message, echoed


def make_review_gate_tool(workspace: Workspace, jira_project: str = "SHIP") -> BaseTool:
    async def request_human_review(
        ticket_key: str,
        summary: str,
        test_report: str,
        review_report: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command:
        """Ask the human to review the exact current diff before anything is published.

        Normally call this once the tests pass and the reviewer subagent has reported. If
        the tests still fail after the developer's attempts, call it anyway and say that the
        tests are FAILING explicitly in `summary` and `test_report` (the human may then
        approve a draft PR, give a hint, or reject). The human sees the full diff, your
        summary, the test report and the review report, and answers approve / feedback /
        reject. On approval the exact diff and the ticket key
        are recorded and publish_pull_request becomes possible for that ticket; ANY later
        change to the working tree voids the approval. On feedback, relay it to the
        developer and call this tool again. On reject, stop working on the ticket.

        Human resume payload: {"type": "approve", "diff_hash": "<from the interrupt>"} |
        {"type": "feedback", "message": "..."} | {"type": "reject", "message": "..."}
        (also accepted wrapped as {"decisions": [ ... ]} or a one-item list).

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
        # LangGraph re-runs this tool from the top on resume: `diff_hash` below is recomputed
        # then, and must equal the hash the human saw (echoed in the resume payload).
        try:
            diff, diff_hash = await workspace.diff_and_hash()
        except WorkspaceError as e:
            # unreadable workspace OR unpublishable change set (symlink, mode change...): the
            # error names the paths so the developer can fix them; nothing is approvable.
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
            if echoed != diff_hash:
                return reply(STALE)
            return reply(APPROVED, diff_hash, key)
        if kind == "feedback":
            return reply(FEEDBACK.format(message=message.rstrip(".")))
        return reply(REJECTED.format(message=message.rstrip(".") or "no reason given"))

    return StructuredTool.from_function(coroutine=request_human_review, name="request_human_review")
