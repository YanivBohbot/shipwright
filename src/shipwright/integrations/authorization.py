"""Authorization and audit of every MCP tool call: `MultiServerMCPClient` tool interceptors.

An interceptor wraps the real MCP call (`handler`). It runs for every agent and subagent, and
AFTER the human approval, so it also checks arguments a human typed with `edit`.

`authorize_interceptor` refuses (an MCP error result the agent reads, nothing is sent) any call
outside Shipwright's scope:
- GitHub: `owner/repo` must be the configured repo; `search_code` must be limited to it;
  branches written must be `shipwright/*`; a PR goes from `shipwright/*` into `main`.
- Jira: issue keys and new issues must belong to the configured project; a search must name it.

`audit_interceptor` records one `AuditEvent` per write tool call (allowed or refused).
"""

import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from langchain_mcp_adapters.interceptors import MCPToolCallRequest, ToolCallInterceptor
from mcp.types import CallToolResult, TextContent

from shipwright.audit import AuditEvent, AuditSink, redact_secrets
from shipwright.core.config import Settings

logger = logging.getLogger(__name__)
# The handler returns a CallToolResult, a ToolMessage or a Command (the adapters' union).
Handler = Callable[[MCPToolCallRequest], Awaitable[Any]]

BRANCH_PREFIX = "shipwright/"
GITHUB_BRANCH_WRITES = ("create_branch", "push_files")
_QUALIFIER = re.compile(r"\b(repo|org|user):(\S+)", re.IGNORECASE)


def refusal(reason: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=f"NOT AUTHORIZED: {reason}")], isError=True
    )


def _github_problem(name: str, args: dict[str, Any], repo: str) -> str | None:
    if name == "search_code":
        qualifiers = _QUALIFIER.findall(str(args.get("query", "")))
        if not qualifiers or any(
            kind.lower() != "repo" or value.lower() != repo.lower() for kind, value in qualifiers
        ):
            return f"search_code must be limited to repo:{repo} (no other repo/org/user)."
        return None
    target = f"{args.get('owner', '')}/{args.get('repo', '')}"
    if target.lower() != repo.lower():
        return f"only the repository {repo} may be used, not {target}."
    if name in GITHUB_BRANCH_WRITES and not str(args.get("branch", "")).startswith(BRANCH_PREFIX):
        return f"only branches named {BRANCH_PREFIX}* may be written."
    if name == "create_branch" and args.get("from_branch") not in (None, "main"):
        return "a branch must start from main."
    if name == "create_pull_request" and (
        not str(args.get("head", "")).startswith(BRANCH_PREFIX) or args.get("base") != "main"
    ):
        return f"a pull request must go from a {BRANCH_PREFIX}* branch into main."
    return None


def _jira_problem(name: str, args: dict[str, Any], project: str) -> str | None:
    project = project.upper()
    if name == "jira_search":
        jql = str(args.get("jql", ""))
        if not re.search(rf"\bproject\s*(=|in)\s*\(?\s*\"?{re.escape(project)}\b", jql, re.I):
            return f"a Jira search must be limited to project = {project}."
        return None
    if name == "jira_create_issue":
        if str(args.get("project_key", "")).upper() != project:
            return f"issues may only be created in project {project}."
        return None
    key = str(args.get("issue_key", ""))
    if not re.fullmatch(rf"{re.escape(project)}-\d+", key.upper()):
        return f"only issues of project {project} may be used, not {key!r}."
    return None


def authorization_problem(request: MCPToolCallRequest, settings: Settings) -> str | None:
    """Why this call is outside Shipwright's scope, or None if it is allowed."""
    if request.server_name == "github":
        return _github_problem(request.name, request.args, settings.shipwright_repo)
    if request.server_name == "atlassian":
        return _jira_problem(request.name, request.args, settings.jira_project)
    return f"unknown MCP server {request.server_name!r}."


def authorize_interceptor(settings: Settings) -> ToolCallInterceptor:
    async def authorize(request: MCPToolCallRequest, handler: Handler) -> Any:
        problem = authorization_problem(request, settings)
        if problem:
            return refusal(problem)
        return await handler(request)

    return authorize


def _preview(result: Any) -> tuple[str, str]:
    """(`ok` | `error`, text) of an MCP result, for the audit log."""
    is_error = bool(getattr(result, "isError", False))
    content = getattr(result, "content", result)
    if isinstance(content, list):
        content = " ".join(str(getattr(c, "text", c)) for c in content)
    return ("error" if is_error else "ok"), str(redact_secrets(str(content)))[:500]


def audit_interceptor(sink: AuditSink, write_tools: set[str]) -> ToolCallInterceptor:
    async def audit(request: MCPToolCallRequest, handler: Handler) -> Any:
        if request.name not in write_tools:
            return await handler(request)
        result = await handler(request)
        runtime = request.runtime
        config = getattr(runtime, "config", None) or {}
        outcome, preview = _preview(result)
        event = AuditEvent(
            ts=datetime.now(UTC),
            thread_id=str((config.get("configurable") or {}).get("thread_id", "unknown")),
            user_id=str(getattr(getattr(runtime, "context", None), "user_id", "unknown")),
            tool=request.name,
            args=redact_secrets(dict(request.args)),
            outcome=outcome,  # type: ignore[arg-type]
            result_preview=preview,
        )
        try:
            await sink.record(event)
        except Exception:
            logger.warning("audit sink failed to record %s", request.name, exc_info=True)
        return result

    return audit
