import pytest


@pytest.fixture(autouse=True)
def _fake_openai_key(monkeypatch):
    """Subagent models are built from strings ("openai:...") and read the key at build time."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-openai")
