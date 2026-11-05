"""Scenario 1 (bugfix) end to end with a REAL OpenAI model, fake Jira/GitHub, local workspace.

Run: uv run pytest -m llm tests/integration/test_scenario_bugfix.py -v
Needs a `.env` with OPENAI_API_KEY (other services are faked; no MCP, no Pinecone).
"""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.types import Command

from shipwright.agent.hitl import hitl_resume, review_gate_resume
from tests.scenarios import build_scenario_env, is_green

pytestmark = pytest.mark.llm
MAX_PAUSES = 10


async def test_scenario_1_bugfix_reaches_review_gate(tmp_path):
    from dotenv import load_dotenv

    load_dotenv()
    env = await build_scenario_env(tmp_path, 1)
    config = env.new_config()
    run_input: Any = {"messages": [{"role": "user", "content": "Process Jira ticket SHIP-1."}]}
    seen: list[str] = []
    for _ in range(MAX_PAUSES):
        await env.agent.ainvoke(run_input, config, context=env.context)
        state = await env.agent.aget_state(config)
        if not state.interrupts:
            break
        (pending,) = state.interrupts
        value = pending.value
        if value.get("kind") == "review_gate":
            if not seen:  # first pause: the review gate on a green, relevant diff
                assert is_green(value["test_report"]), value["test_report"]
                files = [
                    line.split(" b/", 1)[-1]
                    for line in value["diff"].splitlines()
                    if line.startswith("diff --git ")
                ]
                assert any(f.startswith("app/") for f in files), files
                assert any(f.startswith("tests/") for f in files), files
            seen.append("review_gate")
            run_input = Command(resume=review_gate_resume(value, "approve"))
        else:
            names = [a["name"] for a in value["action_requests"]]
            seen.extend(names)
            run_input = Command(resume=hitl_resume(value, ["approve"] * len(names)))

    assert seen[0] == "review_gate"
    assert "publish_pull_request" in seen and "update_jira_ticket" in seen
    assert seen.index("publish_pull_request") < seen.index("update_jira_ticket")
    assert len(env.github.prs) == 1
    pr_url = next(iter(env.github.open_prs.values()))
    assert any(key == "SHIP-1" and pr_url in body for key, body in env.jira.comments)
    assert ("SHIP-1", "In Review") in env.jira.transitions
