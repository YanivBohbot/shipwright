"""Typed application settings, read from the environment / `.env`.

Real Jira and GitHub through their MCP servers, MongoDB for threads and memory, and a sandbox
for the code (`SANDBOX_PROVIDER`: local shell or LangSmith). A missing required value fails at
startup with its name.
"""

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]
_REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    openai_api_key: SecretStr
    model_strong: str = "openai:gpt-5.5"
    model_light: str = "openai:gpt-5.4-mini"

    # Where the developer runs code: "local" (this machine, not isolated) or "langsmith".
    sandbox_provider: Literal["local", "langsmith"] = "local"
    # LANGSMITH_API_KEY is reserved by `langgraph deploy` (not uploaded to the deployment):
    # on a deployment, set SHIPWRIGHT_LANGSMITH_API_KEY instead.
    langsmith_api_key: SecretStr | None = Field(
        None, validation_alias=AliasChoices("LANGSMITH_API_KEY", "SHIPWRIGHT_LANGSMITH_API_KEY")
    )

    # GitHub MCP server (remote) and the repository Shipwright works on.
    github_pat: SecretStr
    github_mcp_url: str = "https://api.githubcopilot.com/mcp/"
    shipwright_repo: str  # "owner/name", must be public (the sandbox clones it anonymously)

    # Jira through mcp-atlassian (stdio).
    jira_url: str
    jira_username: str
    jira_api_token: SecretStr
    jira_project: str = "SHIP"

    mongodb_uri: SecretStr
    mongodb_db: str = "shipwright"

    # Optional: knowledge base (Pinecone, else keyword search over knowledge/) and web search.
    pinecone_api_key: SecretStr | None = None
    pinecone_index: str = "shipwright-knowledge"
    pinecone_namespace: str = "taskboard-api"
    llama_cloud_api_key: SecretStr | None = None
    tavily_api_key: SecretStr | None = None
    knowledge_dir: Path = PROJECT_ROOT / "knowledge"

    max_test_attempts: int = 3
    max_review_cycles: int = 2
    orchestrator_model_call_limit: int = 150
    sandbox_idle_ttl_seconds: int = 3600

    @model_validator(mode="after")
    def _langsmith_sandbox_needs_a_key(self) -> "Settings":
        if self.sandbox_provider == "langsmith" and self.langsmith_api_key is None:
            raise ValueError("SANDBOX_PROVIDER=langsmith requires LANGSMITH_API_KEY")
        return self

    @field_validator(
        "langsmith_api_key",
        "pinecone_api_key",
        "llama_cloud_api_key",
        "tavily_api_key",
        mode="before",
    )
    @classmethod
    def _empty_secret_is_unset(cls, v: object) -> object:
        """`KEY=` placeholders in `.env` mean "not set" (None), never an empty credential."""
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("shipwright_repo", "jira_url", "jira_username")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty")
        return v.strip()

    @field_validator("shipwright_repo")
    @classmethod
    def _validate_repo(cls, v: str) -> str:
        if not _REPO_RE.match(v):
            raise ValueError("must have the format 'owner/name'")
        return v

    @property
    def repo_owner(self) -> str:
        return self.shipwright_repo.split("/")[0]

    @property
    def repo_name(self) -> str:
        return self.shipwright_repo.split("/")[1]

    @property
    def jira_base_url(self) -> str:
        return self.jira_url.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    # The required values come from the environment / .env, not from arguments.
    return Settings()  # pyright: ignore[reportCallIssue]
