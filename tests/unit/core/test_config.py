import pytest
from pydantic import ValidationError

from shipwright.core.config import Settings
from tests.fakes import REQUIRED_FAKES, make_settings


def test_repo_parts_and_jira_base_url():
    s = make_settings(jira_url="https://acme.atlassian.net/")
    assert (s.repo_owner, s.repo_name) == ("acme", "taskboard-api")
    assert s.jira_base_url == "https://acme.atlassian.net"


@pytest.mark.parametrize("field", ["shipwright_repo", "jira_url", "jira_username"])
def test_required_values_must_not_be_blank(field):
    with pytest.raises(ValidationError, match=field):
        make_settings(**{field: "  "})


def test_missing_required_secret_is_reported():
    values = {k: v for k, v in REQUIRED_FAKES.items() if k != "github_pat"}
    with pytest.raises(ValidationError, match="github_pat"):
        Settings(_env_file=None, **values)


def test_repo_format_is_checked():
    with pytest.raises(ValidationError, match="owner/name"):
        make_settings(shipwright_repo="no-slash")


def test_empty_optional_secret_is_unset():
    assert make_settings(tavily_api_key="  ").tavily_api_key is None


def test_sandbox_provider_defaults_to_local_and_langsmith_needs_a_key():
    assert make_settings().sandbox_provider == "local"
    assert make_settings(langsmith_api_key=None).langsmith_api_key is None
    with pytest.raises(ValidationError, match="LANGSMITH_API_KEY"):
        make_settings(sandbox_provider="langsmith", langsmith_api_key=None)


@pytest.mark.parametrize(
    "value",
    [
        "acme/taskboard-api",
        "https://github.com/acme/taskboard-api",
        "https://github.com/acme/taskboard-api.git/",
    ],
)
def test_repo_accepts_a_github_url(value):
    assert make_settings(shipwright_repo=value).shipwright_repo == "acme/taskboard-api"


@pytest.mark.parametrize("field", ["openai_api_key", "github_pat", "jira_api_token", "mongodb_uri"])
def test_required_secrets_must_not_be_blank(field):
    with pytest.raises(ValidationError, match=field):
        make_settings(**{field: " "})
