"""DB 스키마 초기화 - pgvector 확장 활성화 및 임베딩 테이블 생성."""

import psycopg

from src.config import settings

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS guidelines (
    id          SERIAL PRIMARY KEY,
    content     TEXT NOT NULL,
    category    VARCHAR(50),
    source      VARCHAR(255),
    chunk_index INTEGER DEFAULT 0,
    embedding   vector(768),
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_guidelines_embedding
    ON guidelines USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_guidelines_category
    ON guidelines (category);

CREATE TABLE IF NOT EXISTS review_history (
    id              SERIAL PRIMARY KEY,
    project_id      INTEGER NOT NULL,
    mr_iid          INTEGER NOT NULL,
    commit_sha      VARCHAR(40) NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'pending',
    comment_count   INTEGER DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),

    CONSTRAINT uq_review_history UNIQUE (project_id, mr_iid, commit_sha),
    CONSTRAINT chk_status CHECK (status IN ('pending', 'completed', 'failed', 'skipped'))
);

CREATE INDEX IF NOT EXISTS idx_review_history_project_mr
    ON review_history (project_id, mr_iid);

CREATE INDEX IF NOT EXISTS idx_review_history_status
    ON review_history (status);
"""


def init_db() -> None:
    with psycopg.connect(settings.database_url) as conn:
        conn.execute(SCHEMA_SQL)
        conn.commit()
    print("DB schema initialized successfully.")


if __name__ == "__main__":
    init_db()
