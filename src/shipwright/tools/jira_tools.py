"""Composite Jira tools: read a ticket (no interrupt), comment/transition, create subtasks.

The two write tools are guarded by `interrupt_on` (see `shipwright.tools.HITL_INTERRUPTS`).
"""

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from shipwright.config import Settings
from shipwright.integrations.jira import JiraGateway
from shipwright.tickets import Ticket, TicketNotFound, parse_ticket_key


class SubtaskDraft(BaseModel):
    """A subtask to create under a parent ticket."""

    summary: str = Field(description="One-line title of the subtask.")
    description: str = Field(description="What must be done, in English.")


def not_found(key: str, project: str) -> str:
    return f"Ticket {key} not found in project {project}."


def _normalize(raw: str, project: str) -> tuple[str | None, str]:
    """(normalized key or None, error text if invalid)."""
    try:
        return parse_ticket_key(raw, project), ""
    except ValueError:
        return None, not_found(raw.strip(), project)


def render_ticket(ticket: Ticket) -> str:
    if ticket.acceptance_criteria:
        criteria = "Acceptance criteria:\n" + "\n".join(
            f"- {c}" for c in ticket.acceptance_criteria
        )
    else:
        criteria = "Acceptance criteria: NONE FOUND"
    refs = ", ".join(ticket.spec_refs) if ticket.spec_refs else "NONE"
    return (
        f"Ticket {ticket.key}: {ticket.summary}\n"
        f"Type: {ticket.issue_type}\n"
        f"Status: {ticket.status}\n"
        f"Description:\n{ticket.description.strip() or '(empty)'}\n\n"
        f"{criteria}\n"
        f"Spec references: {refs}"
    )


def make_jira_tools(jira: JiraGateway, settings: Settings) -> list[BaseTool]:
    project = settings.jira_project

    async def read_jira_ticket(key: str) -> str:
        """Read a Jira ticket of the project (read-only, no side effect).

        Returns the summary, type, status, description, the extracted acceptance criteria
        ("Acceptance criteria: NONE FOUND" when the ticket has none: do not implement such a
        ticket, ask for clarification instead) and the spec references to look up with
        knowledge_search. If the key is malformed or the ticket does not exist, returns
        "Ticket <key> not found in project <project>." - then stop and report it.

        Args:
            key: Ticket key such as "SHIP-42" (case and surrounding spaces are ignored).
        """
        normalized, error = _normalize(key, project)
        if normalized is None:
            return error
        try:
            ticket = await jira.get_issue(normalized)
        except TicketNotFound:
            return not_found(normalized, project)
        except Exception as e:  # gateway/network error: report to the model, never raise
            return f"Failed to read ticket {normalized}: {e}"
        return render_ticket(ticket)

    async def update_jira_ticket(key: str, comment: str, transition_to: str | None = None) -> str:
        """Post a comment on a Jira ticket and optionally move it to another status.

        Requires human approval. Use it once per outcome: e.g. after the PR is published
        (comment with the PR URL, transition_to="In Review"), or to ask for clarification
        on a ticket without acceptance criteria (comment only).

        Args:
            key: Ticket key such as "SHIP-42".
            comment: Comment body in English (Markdown allowed).
            transition_to: Target status name (e.g. "In Review"); omit to keep the status.
        """
        normalized, error = _normalize(key, project)
        if normalized is None:
            return error
        try:
            await jira.add_comment(normalized, comment)
        except TicketNotFound:
            return not_found(normalized, project)
        except Exception as e:  # gateway/network error: report to the model, never raise
            return f"Failed to comment on {normalized}: {e}"
        if not transition_to:
            return f"Comment added to {normalized}."
        try:
            await jira.transition(normalized, transition_to)
        except Exception as e:
            return f"Comment added to {normalized}, but transition to {transition_to!r} failed: {e}"
        return f"Comment added to {normalized} and ticket moved to {transition_to}."

    async def create_jira_subtasks(parent_key: str, subtasks: list[SubtaskDraft]) -> str:
        """Split a Jira ticket into subtasks (one Jira Subtask per item).

        Requires human approval. Use it when a ticket is too large for one PR. Returns the
        keys of the created subtasks.

        Args:
            parent_key: Key of the parent ticket, e.g. "SHIP-7".
            subtasks: Subtasks to create, each with a summary and a description.
        """
        normalized, error = _normalize(parent_key, project)
        if normalized is None:
            return error
        if not subtasks:
            return "No subtasks given: nothing was created."
        created: list[str] = []
        for draft in subtasks:
            if isinstance(draft, dict):
                draft = SubtaskDraft.model_validate(draft)
            try:
                created.append(
                    await jira.create_subtask(normalized, draft.summary, draft.description)
                )
            except Exception as e:
                done = ", ".join(created) or "none"
                return (
                    f"Failed to create subtask {draft.summary!r} under {normalized}: {e}. "
                    f"Created so far: {done}."
                )
        return f"Created {len(created)} subtasks under {normalized}: {', '.join(created)}."

    return [
        StructuredTool.from_function(coroutine=read_jira_ticket, name="read_jira_ticket"),
        StructuredTool.from_function(coroutine=update_jira_ticket, name="update_jira_ticket"),
        StructuredTool.from_function(coroutine=create_jira_subtasks, name="create_jira_subtasks"),
    ]
