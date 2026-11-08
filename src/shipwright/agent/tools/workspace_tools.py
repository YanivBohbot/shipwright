"""Read-only tools over the sandbox's git working tree.

Deep Agents concept: **custom tools** for what no MCP server can know, here the developer's
uncommitted changes inside the sandbox. They never write anything.
- `show_workspace_diff` (reviewer + orchestrator): the unified diff to review.
- `get_changed_files` (orchestrator): the exact `files` argument for GitHub's `push_files`.
"""

import json

from deepagents.backends.protocol import SandboxBackendProtocol
from langchain.tools import tool
from langchain_core.tools import BaseTool

from shipwright.sandbox.workspace import WorkspaceError, changed_files, workspace_diff

MAX_DIFF_CHARS = 20_000


def build_workspace_tools(backend: SandboxBackendProtocol) -> tuple[BaseTool, BaseTool]:
    @tool
    async def show_workspace_diff() -> str:
        """Show the unified diff of the repository's working tree (new files included)."""
        try:
            diff = await workspace_diff(backend)
        except WorkspaceError as e:
            return f"Cannot show the diff: {e}"
        if not diff.strip():
            return "No changes."
        return diff if len(diff) <= MAX_DIFF_CHARS else diff[:MAX_DIFF_CHARS] + "\n[truncated]"

    @tool
    async def get_changed_files() -> str:
        """List the changed files with their full content, as JSON.

        Returns `{"files": [{"path", "content"}], "deleted": [paths]}`. Pass `files`
        unchanged as the `files` argument of `push_files`.
        """
        try:
            result = await changed_files(backend)
        except WorkspaceError as e:
            return f"Cannot read the changed files: {e}"
        return json.dumps(result, ensure_ascii=False)

    return show_workspace_diff, get_changed_files
