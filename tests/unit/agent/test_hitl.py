"""The terminal approval loop builds the resume values HumanInTheLoopMiddleware expects."""

from types import SimpleNamespace

from shipwright.agent.hitl import ask_decisions, build_resume

PAYLOAD = {
    "action_requests": [
        {"name": "jira_add_comment", "args": {"issue_key": "SHIP-1", "body": "hi"}},
        {"name": "push_files", "args": {"branch": "b"}},
    ],
    "review_configs": [],
}


def answers(*replies):
    it = iter(replies)
    return lambda prompt: next(it)


def test_approve_and_reject_with_reason():
    resume = ask_decisions(PAYLOAD, answers("approve", "reject", "add a test first"))
    assert resume == {
        "decisions": [
            {"type": "approve"},
            {"type": "reject", "message": "add a test first"},
        ]
    }


def test_edit_reasks_until_valid_json():
    payload = {"action_requests": [PAYLOAD["action_requests"][0]]}
    resume = ask_decisions(payload, answers("edit", "{bad", "edit", '{"body": "hello"}'))
    assert resume["decisions"] == [
        {"type": "edit", "edited_action": {"name": "jira_add_comment", "args": {"body": "hello"}}}
    ]


def test_unknown_choice_is_asked_again():
    payload = {"action_requests": [PAYLOAD["action_requests"][0]]}
    assert ask_decisions(payload, answers("yes", "approve")) == {"decisions": [{"type": "approve"}]}


def test_build_resume_single_and_several_interrupts():
    one = SimpleNamespace(id="i1", value={"action_requests": [PAYLOAD["action_requests"][0]]})
    two = SimpleNamespace(id="i2", value={"action_requests": [PAYLOAD["action_requests"][1]]})
    assert build_resume([one], answers("approve")).resume == {"decisions": [{"type": "approve"}]}
    several = build_resume([one, two], answers("approve", "approve")).resume
    assert set(several) == {"i1", "i2"}
