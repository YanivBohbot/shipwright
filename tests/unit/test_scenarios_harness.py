"""`tests/scenarios.py`: drive(), RunOutcome and the scripted decisions (offline: scripted models).

The orchestrator and the `code_analyst` subagent run the REAL graph; both models are scripted.
"""

from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, SystemMessage

from shipwright.agent.hitl import hitl_resume
from tests.fakes import FakeWebTool, RecordingChatModel, ScriptedChatModel, make_settings
from tests.scenarios import (
    S6_FEEDBACK,
    SCENARIO_DECISIONS,
    RunOutcome,
    build_scenario_env,
    decider_for,
    drive,
    extract_outcome,
    is_green,
)


@pytest.fixture(autouse=True)
def _offline_models(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-for-tests")
    monkeypatch.setattr(
        "langchain.agents.middleware.model_fallback.init_chat_model",
        lambda name: GenericFakeChatModel(messages=iter([])),
    )


def _call(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def _stub_subagent_model(monkeypatch, *replies: str) -> None:
    """Subagent models are strings resolved by deepagents: hand out scripted models instead."""
    monkeypatch.setattr(
        "deepagents._models.init_chat_model",
        lambda *a, **k: ScriptedChatModel(messages=iter(AIMessage(content=r) for r in replies)),
    )


async def test_drive_resolves_both_interrupts_and_extracts_outcome(tmp_path, monkeypatch):
    _stub_subagent_model(monkeypatch, "The code lives in app/main.py.")
    settings = make_settings()
    model = ScriptedChatModel(
        messages=iter(
            [
                _call(
                    "task",
                    {"description": "Where is the health route?", "subagent_type": "code_analyst"},
                    "t1",
                ),
                _call(
                    "request_human_review",
                    {
                        "ticket_key": "SHIP-1",
                        "summary": "Fix",
                        "test_report": "3 passed",
                        "review_report": "Verdict: pass",
                    },
                    "r1",
                ),
                _call(
                    "publish_pull_request",
                    {
                        "ticket_key": "SHIP-1",
                        "title": "Fix",
                        "body": "AC-1 done",
                        "changelog_entry": "Fix (SHIP-1)",
                    },
                    "p1",
                ),
                AIMessage(content="PR published."),
            ]
        )
    )
    env = await build_scenario_env(tmp_path, 1, model=model, settings=settings)
    (Path(env.workspace.shell_repo_dir) / "NOTES.md").write_text("change\n", newline="\n")

    outcome = await drive(
        env.agent,
        env.key,
        decide=decider_for(1),
        config=env.new_config(),
        context=env.context,
    )

    assert isinstance(outcome, RunOutcome)
    assert outcome.subagents_called == ["code_analyst"]
    assert outcome.tool_calls[:3] == ["task", "request_human_review", "publish_pull_request"]
    assert len(outcome.interrupts) == 2
    assert outcome.interrupts[0]["kind"] == "review_gate"
    assert outcome.interrupts[1]["action_requests"][0]["name"] == "publish_pull_request"
    assert outcome.final_message == "PR published."
    assert len(env.github.prs) == 1


async def test_drive_raises_on_runaway_pauses():
    class Pending:
        id = "i1"
        value = {"kind": "review_gate", "diff_hash": "h"}

    class State:
        interrupts = (Pending(),)
        values = {"messages": []}

    class StuckAgent:
        invocations = 0

        async def ainvoke(self, run_input, config, context=None):
            self.invocations += 1

        async def aget_state(self, config):
            return State()

    agent = StuckAgent()
    with pytest.raises(RuntimeError, match="still paused"):
        await drive(
            agent,
            "SHIP-1",
            decide=decider_for(1),
            config={},
            context=None,
            max_pauses=2,
        )
    assert agent.invocations == 3


def test_extract_outcome_without_tool_calls():
    outcome = extract_outcome([AIMessage(content="hello")], [])
    assert outcome == RunOutcome([], [], [], "hello")


def test_is_green():
    assert is_green("3 passed in 0.1s")
    assert not is_green("1 failed, 2 passed")
    assert not is_green("no tests ran")
    assert not is_green("TESTS_FAILING 2 passed")


# ------------------------------------------------------------------ scripted decisions


def _gate() -> dict:
    return {"kind": "review_gate", "diff_hash": "abc"}


def _hitl(*names: str) -> dict:
    return {
        "action_requests": [{"name": n, "args": {}} for n in names],
        "review_configs": [
            {"action_name": n, "allowed_decisions": ["approve", "edit", "reject"]} for n in names
        ],
    }


def test_decisions_cover_scenarios_1_to_7():
    assert set(SCENARIO_DECISIONS) == set(range(1, 8))
    with pytest.raises(ValueError):
        decider_for(8)


def test_s6_feedback_then_approve_and_deciders_are_fresh():
    decide = decider_for(6)
    assert decide(_gate()) == {"type": "feedback", "message": S6_FEEDBACK}
    assert decide(_gate()) == {"type": "approve", "diff_hash": "abc"}
    assert decide(_gate()) == {"type": "approve", "diff_hash": "abc"}  # last step repeats
    assert decider_for(6)(_gate())["type"] == "feedback"  # a new decider starts over


def test_hitl_decisions_approve_every_action():
    assert decider_for(1)(_hitl("publish_pull_request")) == hitl_resume(
        _hitl("publish_pull_request"), ["approve"]
    )
    approved = decider_for(4)(_hitl("a", "b"))
    assert [d["type"] for d in approved["decisions"]] == ["approve", "approve"]
    assert decider_for(4)(_gate())["type"] == "reject"  # the gate must not be reached


def test_unsupported_interrupt_payload():
    with pytest.raises(ValueError, match="Unsupported"):
        decider_for(1)({"foo": 1})


# ------------------------------------------------------------------ memory reaches the prompt


async def test_stored_preference_is_loaded_into_system_prompt_of_new_thread(tmp_path):
    """Scenario 6, last check, offline: a preference in the user's store appears in the system
    prompt of a brand new thread of the same user (and not in another user's)."""
    from deepagents.backends.utils import create_file_data

    model = RecordingChatModel(messages=iter([AIMessage(content="ok"), AIMessage(content="ok")]))
    settings = make_settings()
    env = await build_scenario_env(tmp_path, 1, model=model, settings=settings)
    data = create_file_data("- Use a dataclass for payloads, not a dict\n")
    value = {k: data[k] for k in ("content", "encoding", "created_at", "modified_at")}
    await env.store.aput(("users", "alice"), "/preferences.md", value)

    await env.agent.ainvoke(
        {"messages": [{"role": "user", "content": "Process Jira ticket SHIP-1."}]},
        env.new_config(),
        context=env.context,
    )
    system = next(m for m in model.seen[0] if isinstance(m, SystemMessage))
    assert "dataclass" in str(system.content)


def test_fake_web_tool_records_calls():
    web = FakeWebTool("hit")
    assert web.tool.invoke({"query": "fastapi csv"}) == "hit"
    assert web.calls == ["fastapi csv"]
