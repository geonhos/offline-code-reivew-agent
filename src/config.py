from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    llm_model: str = "qwen2.5-coder:7b"
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

    model_config = {"env_prefix": "REVIEW_"}

    @property
    def database_url(self) -> str:
        return (
            f"host={self.db_host} port={self.db_port} "
            f"dbname={self.db_name} user={self.db_user} password={self.db_password}"
        )


settings = Settings()
