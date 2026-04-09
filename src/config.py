from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    llm_model: str = "qwen2.5-coder:7b"  # 레거시 호환용 (컨텍스트 없을 때 사용)
    llm_model_primary: str = "qwen2.5-coder:14b"  # 정밀 리뷰용
    llm_model_fast: str = "qwen2.5-coder:7b"  # 빠른 검증용
    llm_num_ctx_primary: int = 32768
    llm_num_ctx: int = 8192
    embed_model: str = "nomic-embed-text"
    embed_dim: int = 768

    # PostgreSQL
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "reviewer"
    db_password: str = "reviewer"
    db_name: str = "review_db"

    # GitLab
    gitlab_url: str = "https://gitlab.example.com"
    gitlab_token: str = ""
    webhook_secret: str = ""

    # Review
    max_diff_lines: int = 500
    retriever_top_k: int = 5
    score_threshold: float = 0.3

    # Cloud LLM
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    openai_model: str = "gpt-4o"
    anthropic_model: str = "claude-sonnet-4-20250514"

    # Benchmark
    benchmark_offline_models: list[str] = [
        "codegemma:7b-instruct",
        "granite-code:8b",
        "starcoder2:15b",
        "codestral:22b",
    ]
    benchmark_cloud_models: list[str] = [
        "gpt-4o",
        "claude-sonnet-4-20250514",
    ]
    benchmark_num_ctx: int = 8192

    # CVE
    cve_scan_enabled: bool = True
    cve_severity_threshold: Literal["low", "medium", "high", "critical"] = "medium"

    # Hybrid Agent
    context_enrichment_enabled: bool = True
    review_validation_enabled: bool = True

    model_config = {"env_prefix": "REVIEW_", "env_file": ".env"}

    @property
    def database_url(self) -> str:
        return (
            f"host={self.db_host} port={self.db_port} "
            f"dbname={self.db_name} user={self.db_user} password={self.db_password}"
        )


settings = Settings()
