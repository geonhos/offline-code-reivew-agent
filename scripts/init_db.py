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
    ON guidelines USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 10);

CREATE INDEX IF NOT EXISTS idx_guidelines_category
    ON guidelines (category);
"""


def init_db() -> None:
    with psycopg.connect(settings.database_url) as conn:
        conn.execute(SCHEMA_SQL)
        conn.commit()
    print("DB schema initialized successfully.")


if __name__ == "__main__":
    init_db()
