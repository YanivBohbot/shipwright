"""The eval runner's pure parts: the trajectory collector and the outputs it hands the evaluators.

The stream items are shaped like `agent.astream(..., stream_mode=["updates", "tasks"],
subgraphs=True)` yields them: `(namespace, mode, chunk)`.
"""

from types import SimpleNamespace

from langchain_core.messages import AIMessage, ToolMessage

from evals.evaluators import subagent_tools
from evals.run_evals import Trajectory, outputs_from

ROOT: tuple[str, ...] = ()


def delegate(task_id: str, *subagents_and_descriptions: tuple[str, str]):
    """The orchestrator's `tools` task that runs one or more `task` calls."""
    calls = [
        {"name": "task", "args": {"subagent_type": s, "description": d}, "id": f"{task_id}-{i}"}
        for i, (s, d) in enumerate(subagents_and_descriptions)
    ]
    ai = AIMessage(content="", tool_calls=calls)
    return [
        (ROOT, "updates", {"model": {"messages": [ai]}}),
        (ROOT, "tasks", {"id": task_id, "name": "tools", "input": calls}),
    ]


def sub_calls(task_id: str, *names: str):
    calls = [{"name": n, "args": {}, "id": f"{task_id}-{n}"} for n in names]
    return [
        (
            ("tools:" + task_id,),
            "updates",
            {"model": {"messages": [AIMessage("", tool_calls=calls)]}},
        )
    ]


def report(task_id: str, text: str):
    """The subagent's final answer: the ToolMessage of the orchestrator's first `task` call."""
    msg = ToolMessage(content=text, name="task", tool_call_id=f"{task_id}-0")
    return [(ROOT, "updates", {"tools": {"messages": [msg]}})]


def feed(items) -> Trajectory:
    t = Trajectory()
    for namespace, mode, chunk in items:
        t.feed(namespace, mode, chunk)
    return t


def interrupt(name: str, **args):
    return [SimpleNamespace(value={"action_requests": [{"name": name, "args": args}]})]


def test_a_pr_run_records_subagents_in_order_their_tools_and_the_first_write():
    t = feed(
        delegate("a", ("code_analyst", "analyse"))
        + sub_calls("a", "read_file", "search_code")
        + delegate("b", ("developer", "implement"))
        + sub_calls("b", "edit_file", "execute")
        + report("b", "done\nSTATUS: TESTS_PASSING")
        + delegate("c", ("reviewer", "review"))
        + sub_calls("c", "show_workspace_diff")
        + delegate("d", ("github_publisher", "Title: x\nBody: ## Summary\n...\ndraft: false"))
        + sub_calls("d", "get_changed_files", "create_branch")
    )
    out = outputs_from(t, interrupt("create_branch", branch="shipwright/ship-1"))
    assert out["subagents"] == ["code_analyst", "developer", "reviewer", "github_publisher"]
    assert out["subagent_tools"]["developer"] == ["edit_file", "execute"]
    assert out["outcome"] == "pr"
    assert out["first_write"] == "create_branch"
    assert out["tests_passed"] is True
    assert out["draft"] is False
    assert "## Summary" in out["pr_body"]


def test_parallel_delegations_are_both_recorded():
    t = feed(
        delegate("a", ("researcher", "r"), ("code_analyst", "c"))
        + sub_calls("a", "knowledge_search")
    )
    out = outputs_from(t, [])
    assert out["subagents"] == ["researcher", "code_analyst"]
    assert out["subagent_tools"]["researcher"] == ["knowledge_search"]
    assert out["subagent_tools"]["code_analyst"] == ["knowledge_search"]  # shared task id


def test_outcome_comes_from_the_first_write_or_is_a_stop():
    assert outputs_from(Trajectory(), interrupt("jira_add_comment"))["outcome"] == "questions"
    assert outputs_from(Trajectory(), interrupt("jira_create_issue"))["outcome"] == "subtasks"
    assert outputs_from(Trajectory(), [])["outcome"] == "stopped"


def test_failing_tests_and_a_draft_are_read_from_the_reports():
    t = feed(
        delegate("b", ("developer", "implement"))
        + report("b", "AC-2 needs the real service\nSTATUS: TESTS_FAILING")
        + delegate("d", ("github_publisher", "Body: ...\n`draft`: true"))
    )
    out = outputs_from(t, interrupt("create_branch"))
    assert out["tests_passed"] is False
    assert out["draft"] is True


def test_no_publisher_means_no_pr_body():
    assert outputs_from(Trajectory(), interrupt("jira_add_comment"))["pr_body"] is None


def run(**outputs):
    return SimpleNamespace(outputs=outputs)


def test_subagent_tools_scores_each_called_subagent_on_its_key_tool():
    ok = run(
        subagent_tools={
            "developer": ["execute"],
            "reviewer": ["read_file", "show_workspace_diff"],
            "code_analyst": ["grep"],
        }
    )
    assert subagent_tools(ok, None)["score"] == 1.0
    bad = run(subagent_tools={"developer": ["edit_file"], "reviewer": ["show_workspace_diff"]})
    result = subagent_tools(bad, None)
    assert result["score"] == 0.5
    assert "developer" in result["comment"]


def test_subagent_tools_is_not_applicable_when_no_known_subagent_ran():
    assert subagent_tools(run(subagent_tools={}), None)["score"] is None
