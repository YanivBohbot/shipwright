"""Read-only workspace tools (used by the reviewer and the orchestrator)."""

from langchain_core.tools import BaseTool, StructuredTool

from shipwright.workspace import Workspace

MAX_DIFF_CHARS = 20_000
NO_CHANGES = "No changes."
TRUNCATED_NOTE = "\n[diff truncated]"


def make_diff_tool(workspace: Workspace) -> BaseTool:
    async def show_workspace_diff() -> str:
        """Show the full unified diff of the workspace repo against its base commit.

        Read-only. Includes modified, deleted and newly created files, and changes the
        developer already committed locally. Returns "No changes." when nothing changed.
        Output longer than 20 000 characters is cut and ends with "[diff truncated]".
        """
        diff = await workspace.diff()
        if not diff.strip():
            return NO_CHANGES
        if len(diff) > MAX_DIFF_CHARS:
            return diff[:MAX_DIFF_CHARS] + TRUNCATED_NOTE
        return diff

    return StructuredTool.from_function(coroutine=show_workspace_diff, name="show_workspace_diff")
