import pytest
from pydantic import ValidationError

from shipwright.core.config import LOCAL_REPO, Settings

LIVE_ENV = {
    "GITHUB_PAT": "ghp_supersecretvalue",
    "SHIPWRIGHT_REPO": "acme/taskboard-api",
    "JIRA_URL": "https://acme.atlassian.net",
    "JIRA_USERNAME": "bot@acme.com",
    "JIRA_API_TOKEN": "jira-fake",
    "MONGODB_URI": "mongodb://fake:27017",
    "LANGSMITH_API_KEY": "ls-fake",
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Only what each test sets is visible (no real env, no `.env`: `_env_file=None`)."""
    names = {n.upper() for n in Settings.model_fields}
    names |= {"SHIPWRIGHT_MODE", "SHIPWRIGHT_LANGSMITH_API_KEY"}
    for name in names:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-openai")


def test_local_mode_needs_only_openai_key():
    s = Settings(_env_file=None)
    assert s.mode == "local"
    assert s.model_strong == "openai:gpt-5.5"
    assert s.model_light == "openai:gpt-5.4-mini"
    assert s.github_pat is None and s.mongodb_uri is None and s.pinecone_api_key is None
    assert s.repo == LOCAL_REPO
    assert s.jira_project == "SHIP"
    assert s.max_test_attempts == 3 and s.max_review_cycles == 2


def test_openai_key_is_always_required(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(ValidationError, match="openai_api_key"):
        Settings(_env_file=None)


def test_mode_is_read_from_shipwright_mode(monkeypatch):
    for k, v in LIVE_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("SHIPWRIGHT_MODE", "live")
    s = Settings(_env_file=None)
    assert s.mode == "live"
    assert s.repo == "acme/taskboard-api"


def test_live_mode_names_every_missing_secret(monkeypatch):
    monkeypatch.setenv("SHIPWRIGHT_MODE", "live")
    monkeypatch.setenv("JIRA_URL", "https://acme.atlassian.net")
    with pytest.raises(ValidationError) as err:
        Settings(_env_file=None)
    msg = str(err.value)
    for name in (
        "GITHUB_PAT",
        "SHIPWRIGHT_REPO",
        "JIRA_USERNAME",
        "JIRA_API_TOKEN",
        "MONGODB_URI",
        "LANGSMITH_API_KEY",
    ):
        assert name in msg
    assert "JIRA_URL," not in msg and not msg.rstrip().endswith("JIRA_URL")


def test_invalid_mode_is_rejected(monkeypatch):
    monkeypatch.setenv("SHIPWRIGHT_MODE", "staging")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_langsmith_key_accepts_deployment_alias(monkeypatch):
    monkeypatch.setenv("SHIPWRIGHT_LANGSMITH_API_KEY", "ls-deploy")
    s = Settings(_env_file=None)
    assert s.langsmith_api_key is not None
    assert s.langsmith_api_key.get_secret_value() == "ls-deploy"


def test_bad_repo_format_is_rejected(monkeypatch):
    monkeypatch.setenv("SHIPWRIGHT_REPO", "noslash")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_secrets_not_in_repr(monkeypatch):
    for k, v in LIVE_ENV.items():
        monkeypatch.setenv(k, v)
    s = Settings(_env_file=None)
    assert "ghp_supersecretvalue" not in repr(s) and "sk-fake-openai" not in str(s)
