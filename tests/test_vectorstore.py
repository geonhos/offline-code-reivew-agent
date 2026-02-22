"""벡터 저장소 테스트 - pgvector CRUD 및 유사도 검색 검증.

NOTE: 이 테스트는 PostgreSQL + pgvector가 실행 중이어야 합니다.
      docker-compose up -d postgres && python scripts/init_db.py
"""

import pytest

from src.vectorstore import VectorStore


@pytest.fixture()
def store():
    vs = VectorStore()
    vs.delete_all()
    yield vs
    vs.delete_all()


@pytest.fixture()
def sample_embeddings():
    """서로 다른 방향의 테스트용 임베딩 벡터 3개."""
    # 실제 임베딩이 아닌 테스트용 가짜 벡터
    # 서로 다른 값으로 유사도 차이를 만든다
    base = [0.0] * 768
    vec_a = base.copy()
    vec_a[0] = 1.0  # naming 방향

    vec_b = base.copy()
    vec_b[1] = 1.0  # security 방향

    vec_c = base.copy()
    vec_c[0] = 0.9  # naming과 유사한 방향
    vec_c[1] = 0.1

    return vec_a, vec_b, vec_c


class TestVectorStoreInsert:
    def test_insert_single(self, store, sample_embeddings):
        vec_a, _, _ = sample_embeddings

        doc_id = store.insert(
            content="변수명은 snake_case를 사용한다.",
            embedding=vec_a,
            category="naming",
            source="guidelines.md",
        )

        assert isinstance(doc_id, int)
        assert doc_id > 0

    def test_insert_batch(self, store, sample_embeddings):
        vec_a, vec_b, _ = sample_embeddings

        ids = store.insert_batch([
            {
                "content": "변수명은 snake_case를 사용한다.",
                "embedding": vec_a,
                "category": "naming",
                "source": "guidelines.md",
                "chunk_index": 0,
            },
            {
                "content": "SQL 쿼리에 파라미터 바인딩을 사용한다.",
                "embedding": vec_b,
                "category": "security",
                "source": "guidelines.md",
                "chunk_index": 1,
            },
        ])

        assert len(ids) == 2

    def test_count(self, store, sample_embeddings):
        vec_a, _, _ = sample_embeddings

        assert store.count() == 0
        store.insert(content="test", embedding=vec_a)
        assert store.count() == 1


class TestVectorStoreSearch:
    def test_search_returns_top_k(self, store, sample_embeddings):
        vec_a, vec_b, vec_c = sample_embeddings

        store.insert_batch([
            {"content": "변수명은 snake_case를 사용한다.", "embedding": vec_a, "category": "naming"},
            {"content": "SQL 인젝션을 방지한다.", "embedding": vec_b, "category": "security"},
            {"content": "함수명도 snake_case를 사용한다.", "embedding": vec_c, "category": "naming"},
        ])

        results = store.search(query_embedding=vec_a, top_k=3)

        # top_k는 상한값. 직교 벡터(score=0)는 제외될 수 있다
        assert 2 <= len(results) <= 3

    def test_search_order_by_similarity(self, store, sample_embeddings):
        vec_a, vec_b, vec_c = sample_embeddings

        store.insert_batch([
            {"content": "변수명은 snake_case를 사용한다.", "embedding": vec_a, "category": "naming"},
            {"content": "SQL 인젝션을 방지한다.", "embedding": vec_b, "category": "security"},
            {"content": "함수명도 snake_case를 사용한다.", "embedding": vec_c, "category": "naming"},
        ])

        # vec_a 방향으로 검색하면 vec_a가 가장 유사(score=1.0), vec_c가 그 다음
        results = store.search(query_embedding=vec_a, top_k=3)

        assert results[0].content == "변수명은 snake_case를 사용한다."
        assert results[0].score >= results[1].score
        # 직교 벡터(vec_b)는 score=0 이므로 결과에 포함되지 않을 수 있다
        for i in range(len(results) - 1):
            assert results[i].score >= results[i + 1].score

    def test_search_with_category_filter(self, store, sample_embeddings):
        vec_a, vec_b, vec_c = sample_embeddings

        store.insert_batch([
            {"content": "변수명은 snake_case를 사용한다.", "embedding": vec_a, "category": "naming"},
            {"content": "SQL 인젝션을 방지한다.", "embedding": vec_b, "category": "security"},
            {"content": "함수명도 snake_case를 사용한다.", "embedding": vec_c, "category": "naming"},
        ])

        results = store.search(query_embedding=vec_a, top_k=10, category="naming")

        assert len(results) == 2
        assert all(r.category == "naming" for r in results)

    def test_search_with_score_threshold(self, store, sample_embeddings):
        vec_a, vec_b, _ = sample_embeddings

        store.insert_batch([
            {"content": "변수명은 snake_case를 사용한다.", "embedding": vec_a, "category": "naming"},
            {"content": "SQL 인젝션을 방지한다.", "embedding": vec_b, "category": "security"},
        ])

        # 높은 threshold로 검색하면 정확히 일치하는 것만 반환
        results = store.search(query_embedding=vec_a, top_k=10, score_threshold=0.99)

        assert len(results) == 1
        assert results[0].content == "변수명은 snake_case를 사용한다."
