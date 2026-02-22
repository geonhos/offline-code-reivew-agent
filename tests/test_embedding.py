"""임베딩 생성 테스트 - Ollama nomic-embed-text 연동 확인."""

from unittest.mock import patch

import pytest

from src.embedding import embed, embed_single


@pytest.fixture()
def mock_ollama_embed():
    """Ollama /api/embed 응답을 모킹한다."""
    fake_vector = [0.1] * 768

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"embeddings": [fake_vector]}

    with patch("src.embedding.httpx.post", return_value=FakeResponse()) as mock_post:
        yield mock_post, fake_vector


@pytest.fixture()
def mock_ollama_embed_batch():
    """배치 임베딩 응답을 모킹한다."""
    fake_vectors = [[0.1] * 768, [0.2] * 768, [0.3] * 768]

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"embeddings": fake_vectors}

    with patch("src.embedding.httpx.post", return_value=FakeResponse()) as mock_post:
        yield mock_post, fake_vectors


class TestEmbed:
    def test_single_text_returns_768_dim(self, mock_ollama_embed):
        _, fake_vector = mock_ollama_embed

        result = embed("함수명은 snake_case를 사용한다")

        assert len(result) == 1
        assert len(result[0]) == 768

    def test_single_text_calls_ollama_api(self, mock_ollama_embed):
        mock_post, _ = mock_ollama_embed

        embed("테스트 텍스트")

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert "nomic-embed-text" in str(call_kwargs)

    def test_batch_texts_returns_multiple_vectors(self, mock_ollama_embed_batch):
        _, fake_vectors = mock_ollama_embed_batch

        result = embed(["텍스트1", "텍스트2", "텍스트3"])

        assert len(result) == 3
        for vec in result:
            assert len(vec) == 768

    def test_embed_single_returns_flat_vector(self, mock_ollama_embed):
        _, fake_vector = mock_ollama_embed

        result = embed_single("단일 텍스트")

        assert isinstance(result, list)
        assert len(result) == 768
        assert not isinstance(result[0], list)
