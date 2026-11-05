"""What the agent needs from Jira: the `JiraGateway` protocol (+ its `TicketNotFound` error).

Implemented by `McpJira` (live, mcp-atlassian) and `LocalJira` (demo tickets + a JSON file).
"""

from typing import Protocol

from shipwright.domain.tickets import Ticket, TicketNotFound

__all__ = ["JiraGateway", "Ticket", "TicketNotFound"]


class JiraGateway(Protocol):
    async def get_issue(self, key: str) -> Ticket: ...
    async def add_comment(self, key: str, body: str) -> str: ...
    async def transition(self, key: str, to_status: str) -> None: ...
    async def create_subtask(self, parent_key: str, summary: str, description: str) -> str: ...
    async def create_issue(
        self, project: str, summary: str, description: str, issue_type: str
    ) -> str: ...
