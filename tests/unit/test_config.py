import pytest
from pydantic import ValidationError

from shipwright.config import Settings, get_settings

REQUIRED_ENV = {
    "OPENAI_API_KEY": "sk-fake-openai",
    "MODEL_LIGHT": "openai:gpt-5.5-mini",
    "LANGSMITH_API_KEY": "ls-fake",
    "MONGODB_URI": "mongodb://fake:27017",
    "PINECONE_API_KEY": "pc-fake",
    "LLAMA_CLOUD_API_KEY": "llx-fake",
    "TAVILY_API_KEY": "tvly-fake",
    "GITHUB_PAT": "ghp_supersecretvalue",
    "SHIPWRIGHT_REPO": "acme/taskboard-api",
    "JIRA_URL": "https://acme.atlassian.net",
    "JIRA_USERNAME": "bot@acme.com",
    "JIRA_API_TOKEN": "jira-fake",
}


def _set_env(monkeypatch, **overrides):
    for k, v in {**REQUIRED_ENV, **overrides}.items():
        monkeypatch.setenv(k, v)


def test_settings_reads_required_fields(monkeypatch):
    _set_env(monkeypatch)
    s = Settings(_env_file=None)
    assert s.jira_project == "SHIP"
    assert s.shipwright_sandbox_provider == "langsmith"
    assert s.max_test_attempts == 3 and s.max_review_cycles == 2
    assert s.orchestrator_model_call_limit == 150
    assert s.sandbox_idle_ttl_seconds == 3600
    assert s.model_strong == "openai:gpt-5.5"
    assert s.org_id == "default-org"
    assert s.github_pat.get_secret_value() == REQUIRED_ENV["GITHUB_PAT"]


def test_settings_rejects_bad_repo_format(monkeypatch):
    _set_env(monkeypatch, SHIPWRIGHT_REPO="noslash")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_secrets_not_in_repr(monkeypatch):
    _set_env(monkeypatch)
    s = Settings(_env_file=None)
    assert REQUIRED_ENV["GITHUB_PAT"] not in repr(s)
    assert REQUIRED_ENV["GITHUB_PAT"] not in str(s)


@pytest.mark.parametrize("name", ["LANGSMITH_API_KEY", "SHIPWRIGHT_LANGSMITH_API_KEY"])
def test_langsmith_key_accepts_either_env_name(monkeypatch, name):
    _set_env(monkeypatch)
    monkeypatch.delenv("LANGSMITH_API_KEY")
    monkeypatch.setenv(name, "ls-either")
    s = Settings(_env_file=None)
    assert s.langsmith_api_key.get_secret_value() == "ls-either"
    assert "ls-either" not in repr(s)


def test_langsmith_key_missing_fails(monkeypatch):
    _set_env(monkeypatch)
    monkeypatch.delenv("LANGSMITH_API_KEY")
    monkeypatch.delenv("SHIPWRIGHT_LANGSMITH_API_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_get_settings_is_cached(monkeypatch):
    _set_env(monkeypatch)
    get_settings.cache_clear()
    assert get_settings() is get_settings()
    get_settings.cache_clear()
