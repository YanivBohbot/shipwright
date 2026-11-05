"""Typed application settings for Shipwright."""

import re
from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="", extra="ignore", populate_by_name=True
    )

    openai_api_key: SecretStr
    model_strong: str = "openai:gpt-5.5"
    model_light: str

    # LANGSMITH_API_KEY is reserved by `langgraph deploy` (not uploaded to the deployment):
    # on a deployment, set SHIPWRIGHT_LANGSMITH_API_KEY instead.
    langsmith_api_key: SecretStr = Field(
        validation_alias=AliasChoices("LANGSMITH_API_KEY", "SHIPWRIGHT_LANGSMITH_API_KEY")
    )

    mongodb_uri: SecretStr
    mongodb_db: str = "shipwright"

    pinecone_api_key: SecretStr
    pinecone_index: str = "shipwright-knowledge"
    pinecone_namespace: str = "taskboard-api"

    llama_cloud_api_key: SecretStr
    tavily_api_key: SecretStr

    github_pat: SecretStr
    github_mcp_url: str = "https://api.githubcopilot.com/mcp/"
    shipwright_repo: str

    jira_url: str
    jira_username: str
    jira_api_token: SecretStr
    jira_project: str = "SHIP"

    shipwright_sandbox_provider: Literal["langsmith", "local"] = "langsmith"
    max_test_attempts: int = 3
    max_review_cycles: int = 2
    orchestrator_model_call_limit: int = 150
    sandbox_idle_ttl_seconds: int = 3600
    org_id: str = "default-org"

    @field_validator("shipwright_repo")
    @classmethod
    def _validate_repo(cls, v: str) -> str:
        if not _REPO_RE.match(v):
            raise ValueError("shipwright_repo must have the format 'owner/name'")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
