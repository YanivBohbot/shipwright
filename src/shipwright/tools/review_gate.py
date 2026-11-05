"""Human review gate: shows the exact diff and records the hash of the approved diff."""

from typing import Annotated, Any

from langchain.tools import InjectedToolCallId
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.types import Command, interrupt

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
    "NOT APPROVED: the diff changed while the review was pending (the approved diff hash does "
    "not match the current one). Request human review again."
)


def parse_review_decision(payload: Any) -> tuple[str, str, str | None]:
    """Validate a resume payload; return `(type, message, echoed diff_hash or None)`.

    Accepted shapes: `{"type": "approve"}`, `{"type": "feedback", "message": str}`,
    `{"type": "reject", "message": str}` (message optional for reject), optionally with
    `"diff_hash"`; the same dict wrapped as `{"decisions": [<dict>]}` (langchain HITL
    middleware shape) or as a one-element list. Anything else raises ValueError.
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
    return kind, message, echoed


def make_review_gate_tool(workspace: Workspace) -> BaseTool:
    async def request_human_review(
        summary: str,
        test_report: str,
        review_report: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command:
        """Ask the human to review the exact current diff before anything is published.

        Call this only when the tests pass and the reviewer subagent has reported. The
        human sees the full diff, your summary, the test report and the review report, and
        answers approve / feedback / reject. On approval the exact diff is recorded and
        publish_pull_request becomes possible; ANY later change to the working tree voids
        the approval. On feedback, relay it to the developer and call this tool again. On
        reject, stop working on the ticket.

        Resume payload (human side): {"type": "approve"} | {"type": "feedback", "message":
        "..."} | {"type": "reject", "message": "..."}, optionally with the "diff_hash" shown
        in the interrupt; also accepted wrapped as {"decisions": [ ... ]} or a one-item list.

        Args:
            summary: What was changed and why, mapped to the acceptance criteria (English).
            test_report: Result of the test run (summary line and relevant output).
            review_report: The reviewer subagent's verdict, criteria checks and risks.
        """

        def reply(text: str, approved_hash: str | None) -> Command:
            return Command(
                update={
                    "approved_diff_hash": approved_hash,
                    "messages": [ToolMessage(text, tool_call_id=tool_call_id)],
                }
            )

        # Re-runs from the top on resume: the hash below is the one of the diff in the
        # interrupt payload of THIS execution (text and hash come from one git read).
        try:
            diff, diff_hash = await workspace.diff_and_hash()
        except WorkspaceError as e:
            return reply(f"Cannot review: the workspace is unavailable ({e}).", None)
        if not diff.strip():
            return reply(NOTHING_TO_REVIEW, None)

        decision = interrupt(
            {
                "kind": "review_gate",
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
            if echoed is not None and echoed != diff_hash:
                return reply(STALE, None)
            return reply(APPROVED, diff_hash)
        if kind == "feedback":
            return reply(FEEDBACK.format(message=message.rstrip(".")), None)
        return reply(REJECTED.format(message=message.rstrip(".") or "no reason given"), None)

    return StructuredTool.from_function(coroutine=request_human_review, name="request_human_review")
