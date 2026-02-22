"""pgvector 기반 벡터 저장소 - 가이드라인 임베딩 CRUD 및 유사도 검색."""

from dataclasses import dataclass

import psycopg
from pgvector.psycopg import register_vector

from src.config import settings


@dataclass
class GuidelineChunk:
    id: int
    content: str
    category: str | None
    source: str | None
    chunk_index: int
    score: float = 0.0


class VectorStore:
    def __init__(self, conninfo: str | None = None):
        self._conninfo = conninfo or settings.database_url

    def _connect(self) -> psycopg.Connection:
        conn = psycopg.connect(self._conninfo)
        register_vector(conn)
        return conn

    def insert(
        self,
        content: str,
        embedding: list[float],
        category: str | None = None,
        source: str | None = None,
        chunk_index: int = 0,
    ) -> int:
        """단일 가이드라인 청크를 저장하고 id를 반환한다."""
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO guidelines (content, category, source, chunk_index, embedding)
                VALUES (%s, %s, %s, %s, %s::vector)
                RETURNING id
                """,
                (content, category, source, chunk_index, str(embedding)),
            ).fetchone()
            conn.commit()
            return row[0]

    def insert_batch(
        self,
        items: list[dict],
    ) -> list[int]:
        """여러 청크를 한 번에 저장한다.

        Args:
            items: [{"content", "embedding", "category", "source", "chunk_index"}] 리스트
        """
        ids = []
        with self._connect() as conn:
            for item in items:
                row = conn.execute(
                    """
                    INSERT INTO guidelines (content, category, source, chunk_index, embedding)
                    VALUES (%s, %s, %s, %s, %s::vector)
                    RETURNING id
                    """,
                    (
                        item["content"],
                        item.get("category"),
                        item.get("source"),
                        item.get("chunk_index", 0),
                        str(item["embedding"]),
                    ),
                ).fetchone()
                ids.append(row[0])
            conn.commit()
        return ids

    def search(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        category: str | None = None,
        score_threshold: float = 0.0,
    ) -> list[GuidelineChunk]:
        """코사인 유사도 기반으로 가장 관련 높은 가이드라인을 검색한다.

        Args:
            query_embedding: 쿼리 임베딩 벡터.
            top_k: 반환할 최대 결과 수.
            category: 특정 카테고리로 필터링 (None이면 전체 검색).
            score_threshold: 이 값 이상의 유사도만 반환 (0~1, 코사인 유사도).
        """
        # cosine distance = 1 - cosine_similarity 이므로
        # score = 1 - distance 로 변환
        if category:
            query = """
                SELECT id, content, category, source, chunk_index,
                       1 - (embedding <=> %s::vector) AS score
                FROM guidelines
                WHERE category = %s
                ORDER BY embedding <=> %s::vector
                LIMIT %s
            """
            params = (str(query_embedding), category, str(query_embedding), top_k)
        else:
            query = """
                SELECT id, content, category, source, chunk_index,
                       1 - (embedding <=> %s::vector) AS score
                FROM guidelines
                ORDER BY embedding <=> %s::vector
                LIMIT %s
            """
            params = (str(query_embedding), str(query_embedding), top_k)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()

        results = []
        for row in rows:
            chunk = GuidelineChunk(
                id=row[0],
                content=row[1],
                category=row[2],
                source=row[3],
                chunk_index=row[4],
                score=row[5],
            )
            if chunk.score >= score_threshold:
                results.append(chunk)
        return results

    def delete_all(self) -> int:
        """모든 가이드라인을 삭제한다. 테스트용."""
        with self._connect() as conn:
            row = conn.execute("DELETE FROM guidelines RETURNING id").fetchall()
            conn.commit()
            return len(row)

    def count(self) -> int:
        """저장된 가이드라인 수를 반환한다."""
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM guidelines").fetchone()
            return row[0]
