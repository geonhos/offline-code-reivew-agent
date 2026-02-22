"""관련 가이드라인 검색 - RAG의 Retrieval 단계."""

from src.config import settings
from src.embedding import embed_single
from src.vectorstore import GuidelineChunk, VectorStore


class Retriever:
    def __init__(self, store: VectorStore | None = None):
        self._store = store or VectorStore()

    def search(
        self,
        query: str,
        top_k: int | None = None,
        category: str | None = None,
        score_threshold: float | None = None,
    ) -> list[GuidelineChunk]:
        """쿼리 텍스트로 관련 가이드라인을 검색한다.

        Args:
            query: 검색할 텍스트 (코드 diff, 키워드 등).
            top_k: 반환할 최대 결과 수.
            category: 특정 카테고리로 필터링.
            score_threshold: 최소 유사도 점수.
        """
        query_embedding = embed_single(query)

        return self._store.search(
            query_embedding=query_embedding,
            top_k=top_k or settings.retriever_top_k,
            category=category,
            score_threshold=score_threshold or settings.score_threshold,
        )
