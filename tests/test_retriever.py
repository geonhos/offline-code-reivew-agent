"""Retriever 테스트 - 임베딩 검색 파이프라인 검증."""

from unittest.mock import patch

import pytest

from src.retriever import Retriever
from src.vectorstore import GuidelineChunk, VectorStore


@pytest.fixture()
def store():
    vs = VectorStore()
    vs.delete_all()
    yield vs
    vs.delete_all()


@pytest.fixture()
def seeded_store(store):
    """테스트용 가이드라인이 적재된 VectorStore.

    실제 임베딩 대신 방향이 다른 가짜 벡터를 사용한다.
    """
    base = [0.0] * 768

    vec_naming = base.copy()
    vec_naming[0] = 1.0

    vec_security = base.copy()
    vec_security[1] = 1.0

    vec_error = base.copy()
    vec_error[2] = 1.0

    store.insert_batch([
        {
            "content": "변수명은 snake_case를 사용한다. 함수명은 동사로 시작한다.",
            "embedding": vec_naming,
            "category": "naming",
            "source": "python_guide.md",
            "chunk_index": 0,
        },
        {
            "content": "SQL 쿼리에 파라미터 바인딩을 사용한다. 민감 정보를 하드코딩하지 않는다.",
            "embedding": vec_security,
            "category": "security",
            "source": "python_guide.md",
            "chunk_index": 1,
        },
        {
            "content": "빈 except 절은 금지한다. 구체적인 예외 타입을 명시한다.",
            "embedding": vec_error,
            "category": "error_handling",
            "source": "python_guide.md",
            "chunk_index": 2,
        },
    ])
    return store


def make_mock_embed(target_vec):
    """embed_single이 특정 벡터를 반환하도록 모킹."""
    return patch("src.retriever.embed_single", return_value=target_vec)


class TestRetriever:
    def test_search_returns_results(self, seeded_store):
        vec_query = [0.0] * 768
        vec_query[0] = 1.0  # naming 방향

        with make_mock_embed(vec_query):
            retriever = Retriever(store=seeded_store)
            results = retriever.search("변수 네이밍 규칙", score_threshold=0.0)

        assert len(results) > 0
        assert all(isinstance(r, GuidelineChunk) for r in results)

    def test_search_naming_query_returns_naming_first(self, seeded_store):
        vec_query = [0.0] * 768
        vec_query[0] = 1.0  # naming 방향

        with make_mock_embed(vec_query):
            retriever = Retriever(store=seeded_store)
            results = retriever.search("변수명을 camelCase로 작성", score_threshold=0.0)

        assert results[0].category == "naming"

    def test_search_security_query_returns_security_first(self, seeded_store):
        vec_query = [0.0] * 768
        vec_query[1] = 1.0  # security 방향

        with make_mock_embed(vec_query):
            retriever = Retriever(store=seeded_store)
            results = retriever.search("SQL 인젝션 방지", score_threshold=0.0)

        assert results[0].category == "security"

    def test_search_with_category_filter(self, seeded_store):
        vec_query = [0.0] * 768
        vec_query[0] = 0.5
        vec_query[1] = 0.5  # naming + security 중간

        with make_mock_embed(vec_query):
            retriever = Retriever(store=seeded_store)
            results = retriever.search(
                "코딩 규칙",
                category="security",
                score_threshold=0.0,
            )

        assert all(r.category == "security" for r in results)

    def test_search_with_high_threshold_filters_low_scores(self, seeded_store):
        vec_query = [0.0] * 768
        vec_query[0] = 1.0  # naming 방향만

        with make_mock_embed(vec_query):
            retriever = Retriever(store=seeded_store)
            results = retriever.search("네이밍", score_threshold=0.99)

        # 정확히 일치하는 naming만 반환
        assert len(results) == 1
        assert results[0].category == "naming"
