"""Jira gateway: a Protocol plus an implementation over the `mcp-atlassian` MCP tools."""

import json
from typing import Any, Protocol

from langchain_core.tools import BaseTool

from shipwright.integrations.mcp_client import normalize_tool_result
from shipwright.tickets import (
    Ticket,
    TicketNotFound,
    extract_acceptance_criteria,
    extract_spec_refs,
)

# Tool names exposed by mcp-atlassian 0.23.1 (fastmcp namespace "jira").
TOOL_GET_ISSUE = "jira_get_issue"
TOOL_ADD_COMMENT = "jira_add_comment"
TOOL_GET_TRANSITIONS = "jira_get_transitions"
TOOL_TRANSITION_ISSUE = "jira_transition_issue"
TOOL_CREATE_ISSUE = "jira_create_issue"

SUBTASK_ISSUE_TYPE = "Subtask"  # mcp-atlassian: "use 'Subtask' (not 'Sub-task')"

_NOT_FOUND_MARKERS = ("not found", "does not exist", "404", "no issue")


class JiraGateway(Protocol):
    async def get_issue(self, key: str) -> Ticket: ...
    async def add_comment(self, key: str, body: str) -> str: ...
    async def transition(self, key: str, to_status: str) -> None: ...
    async def create_subtask(self, parent_key: str, summary: str, description: str) -> str: ...
    async def create_issue(
        self, project: str, summary: str, description: str, issue_type: str
    ) -> str: ...


def _name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or "")
    return str(value or "")


class McpJiraGateway:
    def __init__(self, tools: dict[str, BaseTool]) -> None:
        self._tools = tools

    async def _call(self, tool: str, **args: Any) -> Any:
        if tool not in self._tools:
            raise KeyError(f"MCP tool '{tool}' is not available on the Atlassian server")
        return normalize_tool_result(await self._tools[tool].ainvoke(args))

    async def get_issue(self, key: str) -> Ticket:
        try:
            data = await self._call(TOOL_GET_ISSUE, issue_key=key)
        except KeyError:
            raise
        except Exception as exc:
            if any(m in str(exc).lower() for m in _NOT_FOUND_MARKERS):
                raise TicketNotFound(key) from exc
            raise
        if not isinstance(data, dict):
            raise TicketNotFound(key)
        description = data.get("description") or ""
        return Ticket(
            key=str(data.get("key") or key),
            summary=str(data.get("summary") or ""),
            description=description,
            status=_name(data.get("status")),
            acceptance_criteria=extract_acceptance_criteria(description),
            spec_refs=extract_spec_refs(description),
            issue_type=_name(data.get("issue_type")),
        )

    async def add_comment(self, key: str, body: str) -> str:
        data = await self._call(TOOL_ADD_COMMENT, issue_key=key, body=body)
        if isinstance(data, dict) and data.get("id") is not None:
            return str(data["id"])
        return ""

    async def transition(self, key: str, to_status: str) -> None:
        data = await self._call(TOOL_GET_TRANSITIONS, issue_key=key)
        if isinstance(data, dict):
            data = data.get("transitions", [])
        transitions = [t for t in data if isinstance(t, dict)] if isinstance(data, list) else []
        wanted = to_status.strip().lower()
        for t in transitions:
            names = {
                str(t.get("name", "")).lower(),
                str(t.get("to_status", "")).lower(),
                _name(t.get("to")).lower(),
            }
            if wanted in names:
                await self._call(TOOL_TRANSITION_ISSUE, issue_key=key, transition_id=str(t["id"]))
                return
        available = ", ".join(sorted({str(t.get("name")) for t in transitions}))
        raise ValueError(f"No transition to status {to_status!r} for {key}. Available: {available}")

    async def _create(self, args: dict[str, Any]) -> str:
        data = await self._call(TOOL_CREATE_ISSUE, **args)
        issue = data.get("issue", data) if isinstance(data, dict) else {}
        key = issue.get("key") if isinstance(issue, dict) else None
        if not key:
            raise RuntimeError(f"Jira did not return an issue key: {data!r}")
        return str(key)

    async def create_subtask(self, parent_key: str, summary: str, description: str) -> str:
        return await self._create(
            {
                "project_key": parent_key.rsplit("-", 1)[0],
                "summary": summary,
                "issue_type": SUBTASK_ISSUE_TYPE,
                "description": description,
                "additional_fields": json.dumps({"parent": parent_key}),
            }
        )

    async def create_issue(
        self, project: str, summary: str, description: str, issue_type: str
    ) -> str:
        return await self._create(
            {
                "project_key": project,
                "summary": summary,
                "issue_type": issue_type,
                "description": description,
            }
        )
