from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db_name: str = "codebase_memory"
    mongodb_mcp_url: str = "http://localhost:8081"
    mcp_bridge_token: str = ""
    ingestion_tmp_dir: str = "/tmp/codebase-memory"

    vertex_ai_project: str = ""
    vertex_ai_location: str = "us-central1"
    vertex_ai_model_ingest: str = "gemini-2.5-flash"
    vertex_ai_model_chat: str = "gemini-2.5-pro"
    vertex_ai_staging_bucket: str = ""

    github_token: str = ""

    allowed_origins: str = "http://localhost:5173"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
