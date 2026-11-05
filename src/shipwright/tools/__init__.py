"""Shipwright tools. Only the composite tools of this package write to GitHub/Jira."""

# `interrupt_on` config for the orchestrator (D1): every GitHub/Jira write is approved by a human.
HITL_INTERRUPTS: dict[str, dict[str, list[str]]] = {
    "publish_pull_request": {"allowed_decisions": ["approve", "reject"]},
    "update_jira_ticket": {"allowed_decisions": ["approve", "edit", "reject"]},
    "create_jira_subtasks": {"allowed_decisions": ["approve", "edit", "reject"]},
}
