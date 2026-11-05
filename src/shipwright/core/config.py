"""Typed application settings (env / `.env`), with two run modes: `local` (demo) and `live`.

Local mode needs only OPENAI_API_KEY; the secrets of the live services are checked only when
`mode == "live"`.
"""

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]
LOCAL_REPO = "local/taskboard-api"  # `owner/name` used for the demo repo in local mode

_REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")
_LIVE_REQUIRED = (
    "github_pat",
    "shipwright_repo",
    "jira_url",
    "jira_username",
    "jira_api_token",
    "mongodb_uri",
    "langsmith_api_key",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="", extra="ignore", populate_by_name=True
    )

    mode: Literal["local", "live"] = Field(
        "local", validation_alias=AliasChoices("SHIPWRIGHT_MODE", "mode")
    )

    openai_api_key: SecretStr
    model_strong: str = "openai:gpt-5.5"
    model_light: str = "openai:gpt-5.4-mini"

    # LANGSMITH_API_KEY is reserved by `langgraph deploy` (not uploaded to the deployment):
    # on a deployment, set SHIPWRIGHT_LANGSMITH_API_KEY instead.
    langsmith_api_key: SecretStr | None = Field(
        None, validation_alias=AliasChoices("LANGSMITH_API_KEY", "SHIPWRIGHT_LANGSMITH_API_KEY")
    )

    mongodb_uri: SecretStr | None = None
    mongodb_db: str = "shipwright"

    pinecone_api_key: SecretStr | None = None
    pinecone_index: str = "shipwright-knowledge"
    pinecone_namespace: str = "taskboard-api"

    llama_cloud_api_key: SecretStr | None = None
    tavily_api_key: SecretStr | None = None

    github_pat: SecretStr | None = None
    github_mcp_url: str = "https://api.githubcopilot.com/mcp/"
    shipwright_repo: str | None = None

    jira_url: str | None = None
    jira_username: str | None = None
    jira_api_token: SecretStr | None = None
    jira_project: str = "SHIP"

    demo_dir: Path = PROJECT_ROOT / "demo"
    knowledge_dir: Path = PROJECT_ROOT / "knowledge"
    tmp_dir: Path = PROJECT_ROOT / "tmp"

    max_test_attempts: int = 3
    max_review_cycles: int = 2
    orchestrator_model_call_limit: int = 150
    sandbox_idle_ttl_seconds: int = 3600
    org_id: str = "default-org"

    @field_validator("shipwright_repo")
    @classmethod
    def _validate_repo(cls, v: str | None) -> str | None:
        if v is not None and not _REPO_RE.match(v):
            raise ValueError("shipwright_repo must have the format 'owner/name'")
        return v

    @model_validator(mode="after")
    def _require_live_secrets(self) -> "Settings":
        if self.mode == "live":
            missing = [f for f in _LIVE_REQUIRED if getattr(self, f) in (None, "")]
            if missing:
                names = ", ".join(f.upper() for f in missing)
                raise ValueError(f"SHIPWRIGHT_MODE=live requires: {names}")
        return self

    @property
    def repo(self) -> str:
        """The target repo: `shipwright_repo` in live mode, the local demo repo otherwise."""
        return self.shipwright_repo or LOCAL_REPO


@lru_cache
def get_settings() -> Settings:
    return Settings()
