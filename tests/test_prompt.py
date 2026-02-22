"""프롬프트 조립 테스트 - 가이드라인 + diff가 올바르게 조합되는지 검증."""

from src.diff_parser import FileDiff, Hunk, Line
from src.prompt import (
    build_review_prompt,
    format_diff,
    format_guidelines,
)
from src.vectorstore import GuidelineChunk


def _make_file_diff() -> FileDiff:
    return FileDiff(
        filename="src/main.py",
        status="modified",
        hunks=[
            Hunk(
                old_start=1,
                old_count=3,
                new_start=1,
                new_count=5,
                lines=[
                    Line(number=1, content="import os", type="context"),
                    Line(number=2, content='password = "admin123"', type="delete"),
                    Line(number=2, content="import sys", type="add"),
                    Line(number=3, content='API_KEY = os.environ["API_KEY"]', type="add"),
                ],
            )
        ],
    )


def _make_guidelines() -> list[GuidelineChunk]:
    return [
        GuidelineChunk(
            id=1,
            content="비밀번호, API 키 등 민감 정보를 코드에 하드코딩하지 않는다.",
            category="security",
            source="python_guide.md",
            chunk_index=0,
            score=0.92,
        ),
        GuidelineChunk(
            id=2,
            content="변수명은 snake_case를 사용한다.",
            category="naming",
            source="python_guide.md",
            chunk_index=1,
            score=0.78,
        ),
    ]


class TestFormatGuidelines:
    def test_format_with_chunks(self):
        result = format_guidelines(_make_guidelines())
        assert "가이드라인 1" in result
        assert "[security]" in result
        assert "하드코딩" in result

    def test_format_empty(self):
        result = format_guidelines([])
        assert "관련 가이드라인 없음" in result


class TestFormatDiff:
    def test_format_diff_has_markers(self):
        result = format_diff(_make_file_diff())
        assert result.startswith("@@")
        assert '+API_KEY = os.environ["API_KEY"]' in result
        assert '-password = "admin123"' in result
        assert " import os" in result


class TestBuildReviewPrompt:
    def test_returns_system_and_user(self):
        system, user = build_review_prompt(_make_file_diff(), _make_guidelines())
        assert isinstance(system, str)
        assert isinstance(user, str)

    def test_system_prompt_has_role(self):
        system, _ = build_review_prompt(_make_file_diff(), _make_guidelines())
        assert "expert code reviewer" in system

    def test_system_prompt_has_few_shot(self):
        system, _ = build_review_prompt(
            _make_file_diff(), _make_guidelines(), include_few_shot=True
        )
        assert "좋은 리뷰" in system
        assert "나쁜 리뷰" in system

    def test_system_prompt_without_few_shot(self):
        system, _ = build_review_prompt(
            _make_file_diff(), _make_guidelines(), include_few_shot=False
        )
        assert "좋은 리뷰" not in system

    def test_user_prompt_has_guidelines(self):
        _, user = build_review_prompt(_make_file_diff(), _make_guidelines())
        assert "가이드라인" in user
        assert "하드코딩" in user

    def test_user_prompt_has_diff(self):
        _, user = build_review_prompt(_make_file_diff(), _make_guidelines())
        assert "src/main.py" in user
        assert "API_KEY" in user

    def test_user_prompt_has_json_format(self):
        _, user = build_review_prompt(_make_file_diff(), _make_guidelines())
        assert '"severity"' in user
        assert '"line"' in user
        assert '"message"' in user
