"""리뷰 검증기 테스트 - 룰 기반 오탐 필터링 + LLM 검증."""

from unittest.mock import patch

import pytest

from src.context_enricher import EnrichedContext, FileContext
from src.diff_parser import FileDiff, Hunk, Line
from src.review_validator import ReviewValidator
from src.reviewer import ReviewComment


@pytest.fixture()
def validator():
    return ReviewValidator(ollama_base_url="http://localhost:11434", fast_model="qwen2.5-coder:7b")


@pytest.fixture()
def sql_injection_comment():
    return ReviewComment(
        file="app/service.py", line=10, severity="critical",
        message="SQL 인젝션 취약점: f-string으로 쿼리를 구성하고 있습니다.",
    )


@pytest.fixture()
def hardcoded_secret_comment():
    return ReviewComment(
        file="app/config.py", line=5, severity="critical",
        message="하드코딩된 비밀번호가 포함되어 있습니다.",
    )


class TestSqlInjectionFalsePositive:
    def test_true_positive_with_string_format(self, validator, sql_injection_comment):
        """f-string 쿼리 → 진짜 SQL 인젝션."""
        source = 'query = f"SELECT * FROM users WHERE id = \'{user_id}\'"'
        assert validator._check_sql_injection_false_positive(sql_injection_comment, source) is False

    def test_false_positive_with_parameterized_query(self, validator, sql_injection_comment):
        """파라미터화 쿼리 사용 → 오탐."""
        source = 'cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))'
        assert validator._check_sql_injection_false_positive(sql_injection_comment, source) is True

    def test_false_positive_with_named_params(self, validator, sql_injection_comment):
        source = 'session.execute("SELECT * FROM users WHERE id = :user_id", {"user_id": uid})'
        assert validator._check_sql_injection_false_positive(sql_injection_comment, source) is True

    def test_non_sql_comment_skipped(self, validator):
        comment = ReviewComment(file="a.py", line=1, severity="warning", message="빈 except 절")
        assert validator._check_sql_injection_false_positive(comment, "any source") is False


class TestHardcodedSecretFalsePositive:
    def test_true_positive_with_hardcoded_value(self, validator, hardcoded_secret_comment):
        source = 'PASSWORD = "admin123"'
        assert validator._check_hardcoded_secret_false_positive(hardcoded_secret_comment, source) is False

    def test_false_positive_with_env_var(self, validator, hardcoded_secret_comment):
        source = 'PASSWORD = os.environ["DB_PASSWORD"]'
        assert validator._check_hardcoded_secret_false_positive(hardcoded_secret_comment, source) is True

    def test_false_positive_with_settings(self, validator, hardcoded_secret_comment):
        source = 'password = settings.db_password'
        assert validator._check_hardcoded_secret_false_positive(hardcoded_secret_comment, source) is True


class TestDeletedCodeComment:
    def test_comment_on_deleted_line(self, validator):
        comment = ReviewComment(file="a.py", line=5, severity="info", message="개선 필요")
        diff = FileDiff(
            filename="a.py",
            hunks=[Hunk(old_start=1, old_count=5, new_start=1, new_count=3,
                        lines=[Line(number=1, content="kept", type="add"),
                               Line(number=2, content="new", type="add")])],
        )
        # line 5는 added_lines에 없음
        assert validator._check_non_added_line_comment(comment, diff) is True

    def test_comment_on_added_line(self, validator):
        comment = ReviewComment(file="a.py", line=1, severity="info", message="개선 필요")
        diff = FileDiff(
            filename="a.py",
            hunks=[Hunk(old_start=1, old_count=0, new_start=1, new_count=1,
                        lines=[Line(number=1, content="new line", type="add")])],
        )
        assert validator._check_non_added_line_comment(comment, diff) is False


class TestValidateRules:
    def test_returns_separated_lists(self, validator):
        comments = [
            ReviewComment(file="a.py", line=1, severity="critical",
                          message="SQL 인젝션 취약점 발견"),
            ReviewComment(file="a.py", line=2, severity="warning",
                          message="빈 except 절입니다"),
        ]
        # 파라미터화 쿼리가 있는 소스 → SQL 인젝션은 오탐
        ctx = FileContext(
            file_path="a.py",
            enriched=EnrichedContext(
                full_source='cursor.execute("SELECT %s", (val,))\ntry:\n    pass\nexcept:\n    pass',
            ),
        )
        diff = FileDiff(
            filename="a.py",
            hunks=[Hunk(old_start=1, old_count=0, new_start=1, new_count=2,
                        lines=[Line(number=1, content="l1", type="add"),
                               Line(number=2, content="l2", type="add")])],
        )

        valid, filtered = validator.validate_rules(
            comments, {"a.py": ctx}, {"a.py": diff}
        )
        assert len(filtered) == 1
        assert "SQL" in filtered[0].message
        assert len(valid) == 1
        assert "except" in valid[0].message


class TestValidateWithLlm:
    def test_llm_says_yes(self, validator):
        comment = ReviewComment(file="a.py", line=1, severity="warning", message="테스트")
        with patch.object(validator, "_call_fast_llm", return_value="yes"):
            assert validator.validate_with_llm(comment, "some code") is True

    def test_llm_says_no(self, validator):
        comment = ReviewComment(file="a.py", line=1, severity="warning", message="테스트")
        with patch.object(validator, "_call_fast_llm", return_value="no, this is wrong"):
            assert validator.validate_with_llm(comment, "some code") is False

    def test_llm_failure_keeps_comment(self, validator):
        comment = ReviewComment(file="a.py", line=1, severity="warning", message="테스트")
        with patch.object(validator, "_call_fast_llm", side_effect=Exception("timeout")):
            assert validator.validate_with_llm(comment, "some code") is True
