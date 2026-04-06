"""리뷰어 테스트 - diff → 리뷰 코멘트 생성 파이프라인 검증."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.reviewer import ReviewComment, Reviewer

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def sample_diff_text() -> str:
    return (FIXTURES_DIR / "sample.diff").read_text()


@pytest.fixture()
def mock_reviewer():
    """Retriever와 LLM 호출을 모킹한 Reviewer."""
    mock_retriever = MagicMock()
    mock_retriever.search.return_value = []

    reviewer = Reviewer(retriever=mock_retriever)
    return reviewer, mock_retriever


class TestParseResponse:
    def test_parse_json_in_code_block(self, mock_reviewer):
        reviewer, _ = mock_reviewer
        response = '''```json
[
  {"file": "src/main.py", "line": 5, "severity": "critical", "message": "보안 이슈"}
]
```'''
        result = reviewer._parse_response(response, "src/main.py")
        assert len(result) == 1
        assert result[0].severity == "critical"
        assert result[0].line == 5

    def test_parse_bare_json(self, mock_reviewer):
        reviewer, _ = mock_reviewer
        response = '[{"file": "a.py", "line": 1, "severity": "info", "message": "ok"}]'
        result = reviewer._parse_response(response, "a.py")
        assert len(result) == 1

    def test_parse_empty_array(self, mock_reviewer):
        reviewer, _ = mock_reviewer
        result = reviewer._parse_response("[]", "a.py")
        assert result == []

    def test_parse_invalid_json(self, mock_reviewer):
        reviewer, _ = mock_reviewer
        result = reviewer._parse_response("이것은 JSON이 아닙니다", "a.py")
        assert result == []

    def test_parse_multiple_comments(self, mock_reviewer):
        reviewer, _ = mock_reviewer
        response = '''```json
[
  {"file": "a.py", "line": 3, "severity": "critical", "message": "SQL 인젝션 위험"},
  {"file": "a.py", "line": 10, "severity": "warning", "message": "빈 except 절"},
  {"file": "a.py", "line": 15, "severity": "info", "message": "타입 힌트 추가 권장"}
]
```'''
        result = reviewer._parse_response(response, "a.py")
        assert len(result) == 3
        assert result[0].severity == "critical"
        assert result[1].severity == "warning"
        assert result[2].severity == "info"

    def test_parse_defaults_missing_fields(self, mock_reviewer):
        reviewer, _ = mock_reviewer
        response = '[{"message": "문제 발견"}]'
        result = reviewer._parse_response(response, "fallback.py")
        assert result[0].file == "fallback.py"
        assert result[0].line == 0
        assert result[0].severity == "info"


class TestReviewerPipeline:
    def test_review_calls_retriever_for_each_file(self, mock_reviewer, sample_diff_text):
        reviewer, mock_retriever = mock_reviewer

        llm_response = '[]'
        with patch.object(reviewer, "_call_llm", return_value=llm_response):
            reviewer.review(sample_diff_text)

        # reviewable 파일 수만큼 retriever.search 호출
        assert mock_retriever.search.call_count > 0

    def test_review_returns_comments(self, mock_reviewer, sample_diff_text):
        reviewer, _ = mock_reviewer

        llm_response = '''```json
[{"file": "src/main.py", "line": 12, "severity": "warning", "message": "password 변수가 정의되지 않았습니다."}]
```'''
        with patch.object(reviewer, "_call_llm", return_value=llm_response):
            comments = reviewer.review(sample_diff_text)

        assert len(comments) > 0
        assert all(isinstance(c, ReviewComment) for c in comments)

    def test_review_skips_binary_files(self, mock_reviewer):
        reviewer, _ = mock_reviewer

        diff_text = """diff --git a/image.png b/image.png
new file mode 100644
Binary files /dev/null and b/image.png differ"""

        with patch.object(reviewer, "_call_llm") as mock_llm:
            reviewer.review(diff_text)

        # 바이너리 파일은 LLM 호출하지 않음
        mock_llm.assert_not_called()


class TestBuildSearchQuery:
    def test_query_from_added_lines(self, mock_reviewer, sample_diff_text):
        reviewer, _ = mock_reviewer
        from src.diff_parser import parse_diff

        diff_result = parse_diff(sample_diff_text)
        main_py = next(f for f in diff_result.files if f.filename == "src/main.py")

        query = reviewer._build_search_query(main_py)
        assert len(query) > 0
        assert len(query) <= 500

    def test_query_truncated_to_500_chars(self, mock_reviewer):
        reviewer, _ = mock_reviewer
        from src.diff_parser import FileDiff, Hunk, Line

        # 매우 긴 추가 라인
        long_file = FileDiff(
            filename="long.py",
            hunks=[Hunk(
                old_start=1, old_count=1, new_start=1, new_count=100,
                lines=[Line(number=i, content=f"line {i} " * 20, type="add") for i in range(100)],
            )],
        )

        query = reviewer._build_search_query(long_file)
        assert len(query) <= 500


class TestLargeDiffSkip:
    """대용량 Diff 스킵 테스트."""

    def test_skips_file_exceeding_max_diff_lines(self):
        """max_diff_lines를 초과하는 파일은 리뷰를 건너뛴다."""
        from src.reviewer import Reviewer, ReviewComment

        # max_diff_lines를 5로 설정
        with patch("src.reviewer.settings") as mock_settings:
            mock_settings.max_diff_lines = 5
            mock_settings.context_enrichment_enabled = False
            mock_settings.review_validation_enabled = False
            mock_settings.cve_scan_enabled = False

            # 10줄의 added lines가 있는 diff (5 초과)
            large_diff = """\
diff --git a/big.py b/big.py
--- a/big.py
+++ b/big.py
@@ -1,1 +1,11 @@
 existing
+line1
+line2
+line3
+line4
+line5
+line6
+line7
+line8
+line9
+line10
"""
            mock_retriever = MagicMock()
            reviewer = Reviewer(retriever=mock_retriever)
            comments = reviewer.review(large_diff)

        # 스킵 info 코멘트가 포함되어야 함
        assert len(comments) == 1
        assert comments[0].severity == "info"
        assert "big.py" in comments[0].message
        assert "max_diff_lines" in comments[0].message

    def test_reviews_file_within_max_diff_lines(self):
        """max_diff_lines 이내인 파일은 정상 리뷰."""
        from src.reviewer import Reviewer

        with patch("src.reviewer.settings") as mock_settings:
            mock_settings.max_diff_lines = 100
            mock_settings.context_enrichment_enabled = False
            mock_settings.review_validation_enabled = False
            mock_settings.cve_scan_enabled = False
            mock_settings.llm_model = "test"
            mock_settings.llm_num_ctx = 8192
            mock_settings.ollama_base_url = "http://localhost:11434"

            small_diff = """\
diff --git a/small.py b/small.py
--- a/small.py
+++ b/small.py
@@ -1,1 +1,2 @@
 existing
+new_line
"""
            mock_retriever = MagicMock()
            mock_retriever.search.return_value = []

            reviewer = Reviewer(retriever=mock_retriever)

            with patch.object(reviewer, "_call_llm", return_value='[]'):
                comments = reviewer.review(small_diff)

        # 스킵되지 않음 (빈 리뷰 결과)
        assert len(comments) == 0
