"""Scenarios 2-7 end to end with a REAL OpenAI model, fake Jira/GitHub/web, local provider.

Run: uv run pytest -m llm tests/integration/test_scenarios.py -v
Needs a `.env` with OPENAI_API_KEY / MODEL_* (other services are faked; no MCP, no Pinecone).
Human decisions are scripted in `shipwright.evaluation.decisions` (shared with the evals).
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.checkpoint.memory import InMemorySaver

from shipwright.agent import build_agent
from shipwright.evaluation.decisions import decider_for
from tests.fakes import RecordingChatModel
from tests.integration.helpers import ScenarioEnv, build_scenario_env, drive, is_green

pytestmark = pytest.mark.llm

FORBIDDEN_WRITES = {"request_human_review", "publish_pull_request"}


async def run_scenario(env: ScenarioEnv, scenario: int):
    return await drive(
        env.agent,
        env.key,
        decide=decider_for(scenario),
        config=env.new_config(),
        context=env.context,
    )


def gates(outcome) -> list[dict]:
    return [i for i in outcome.interrupts if i.get("kind") == "review_gate"]


async def test_s2_feature_uses_researcher_and_code_analyst(tmp_path):
    env = await build_scenario_env(tmp_path, 2)
    outcome = await run_scenario(env, 2)

    assert {"researcher", "code_analyst", "developer", "reviewer"} <= set(outcome.subagents_called)
    assert len(env.github.prs) == 1
    assert "AC-1" in env.github.prs[0]["body"]


async def test_s3_external_knowledge_uses_web_search(tmp_path):
    env = await build_scenario_env(tmp_path, 3)
    await run_scenario(env, 3)

    # the researcher's own tool calls are not visible from the orchestrator: the fake records them
    assert env.web.calls, "web_search was never called"


async def test_s4_ambiguous_ticket_asks_and_stops(tmp_path):
    env = await build_scenario_env(tmp_path, 4)
    outcome = await run_scenario(env, 4)

    assert not FORBIDDEN_WRITES & set(outcome.tool_calls)
    assert not env.github.prs
    (comment,) = [body for key, body in env.jira.comments if key == env.key]
    assert sum("?" in line for line in comment.splitlines()) >= 2, comment


async def test_s5_failing_tests_escalates(tmp_path):
    env = await build_scenario_env(tmp_path, 5)
    outcome = await run_scenario(env, 5)

    (gate, *_) = gates(outcome)
    assert not is_green(gate["test_report"]), gate["test_report"]
    assert len(env.github.prs) == 1
    assert env.github.prs[0]["draft"] is True


async def test_s6_feedback_loop_and_memory(tmp_path):
    env = await build_scenario_env(tmp_path, 6, user_id="alice")
    outcome = await run_scenario(env, 6)

    first, second, *_ = gates(outcome)
    assert first["diff_hash"] != second["diff_hash"]

    # the preference was stored for the user (store read, not LLM behaviour)
    item = await env.store.aget(("users", "alice"), "/preferences.md")
    assert item is not None and "dataclass" in item.value["content"].lower()

    # new thread, same user, scenario 1: the loaded system prompt contains the preference
    model = RecordingChatModel(messages=iter([AIMessage(content="ok")]))
    fresh = build_agent(
        env.settings,
        env.deps,
        env.workspace,
        checkpointer=InMemorySaver(),
        store=env.store,
        model=model,
    )
    await fresh.ainvoke(
        {"messages": [{"role": "user", "content": "Process Jira ticket SHIP-1."}]},
        env.new_config(),
        context=env.context,
    )
    system = next(m for m in model.seen[0] if isinstance(m, SystemMessage))
    assert "dataclass" in str(system.content).lower()


async def test_s7_large_ticket_proposes_subtasks(tmp_path):
    env = await build_scenario_env(tmp_path, 7)
    outcome = await run_scenario(env, 7)

    assert "create_jira_subtasks" in outcome.tool_calls
    assert len(env.jira.subtasks) >= 2
    assert not env.github.prs
