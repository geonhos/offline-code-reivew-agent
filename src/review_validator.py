"""리뷰 검증기 - 룰 기반 오탐 필터링 + 경량 LLM yes/no 판단.

코드 리뷰 결과에서 false positive를 제거하여 리뷰 품질을 높인다.
"""

import logging
import re
from dataclasses import dataclass

import httpx

from src.config import settings
from src.context_enricher import FileContext
from src.diff_parser import FileDiff
from src.reviewer import ReviewComment

logger = logging.getLogger(__name__)

# 파라미터화 쿼리 패턴
_PARAMETERIZED_PATTERNS = [
    r"%s",           # psycopg, mysql-connector
    r"\?",           # sqlite3
    r":\w+",         # sqlalchemy named params
    r"\$\d+",        # asyncpg
    r"execute\(.+?,\s*[\(\[]",  # execute(query, params)
]

# 환경변수 사용 패턴
_ENV_VAR_PATTERNS = [
    r"os\.environ",
    r"os\.getenv",
    r"settings\.",
    r"config\.",
    r"\.env",
    r"SECRET_KEY\s*=\s*os\.",
]


@dataclass
class ValidationResult:
    comment: ReviewComment
    is_valid: bool
    reason: str


class ReviewValidator:
    """리뷰 코멘트의 false positive를 필터링한다."""

    def __init__(
        self,
        ollama_base_url: str | None = None,
        fast_model: str | None = None,
    ):
        self._ollama_url = ollama_base_url or settings.ollama_base_url
        self._fast_model = fast_model or settings.llm_model_fast

    def validate(
        self,
        comments: list[ReviewComment],
        file_contexts: dict[str, FileContext],
        file_diffs: dict[str, FileDiff],
    ) -> list[ReviewComment]:
        """룰 기반 + LLM 검증을 수행하고 유효한 코멘트만 반환한다."""
        valid, filtered = self.validate_rules(comments, file_contexts, file_diffs)

        for f in filtered:
            logger.info("오탐 필터링: %s:%d — %s", f.file, f.line, f.message[:50])

        return valid

    def validate_rules(
        self,
        comments: list[ReviewComment],
        file_contexts: dict[str, FileContext],
        file_diffs: dict[str, FileDiff],
    ) -> tuple[list[ReviewComment], list[ReviewComment]]:
        """룰 기반으로 코멘트를 검증하고 (유효, 필터링) 쌍을 반환한다."""
        valid: list[ReviewComment] = []
        filtered: list[ReviewComment] = []

        for comment in comments:
            ctx = file_contexts.get(comment.file)
            diff = file_diffs.get(comment.file)
            full_source = ctx.enriched.full_source if ctx else ""

            if self._check_sql_injection_false_positive(comment, full_source):
                filtered.append(comment)
            elif self._check_hardcoded_secret_false_positive(comment, full_source):
                filtered.append(comment)
            elif diff and self._check_deleted_code_comment(comment, diff):
                filtered.append(comment)
            else:
                valid.append(comment)

        return valid, filtered

    @staticmethod
    def _check_sql_injection_false_positive(comment: ReviewComment, full_source: str) -> bool:
        """SQL 인젝션 지적이지만 파라미터화 쿼리를 사용하는 경우 오탐."""
        msg = comment.message.lower()
        if "sql" not in msg or "인젝션" not in msg:
            return False
        if not full_source:
            return False
        return any(re.search(p, full_source) for p in _PARAMETERIZED_PATTERNS)

    @staticmethod
    def _check_hardcoded_secret_false_positive(comment: ReviewComment, full_source: str) -> bool:
        """하드코딩 비밀번호 지적이지만 환경변수를 사용하는 경우 오탐."""
        msg = comment.message.lower()
        if not any(kw in msg for kw in ("하드코딩", "hardcod", "비밀번호", "password", "secret")):
            return False
        if not full_source:
            return False
        # 해당 라인 주변에 환경변수 패턴이 있는지 확인
        return any(re.search(p, full_source) for p in _ENV_VAR_PATTERNS)

    @staticmethod
    def _check_deleted_code_comment(comment: ReviewComment, file_diff: FileDiff) -> bool:
        """삭제된 코드에 대한 코멘트인 경우 필터링."""
        added_lines = {line.number for line in file_diff.added_lines}
        return comment.line > 0 and comment.line not in added_lines

    # ── LLM 기반 검증 (경량 모델) ─────────────────────────────

    def validate_with_llm(
        self, comment: ReviewComment, code_context: str
    ) -> bool:
        """경량 모델로 코멘트의 유효성을 판단한다. True=유효, False=오탐."""
        prompt = (
            "다음 코드와 리뷰 코멘트를 보고, 이 리뷰가 정확한지 판단하세요.\n\n"
            f"코드:\n```\n{code_context[:2000]}\n```\n\n"
            f"리뷰: {comment.message}\n\n"
            "이 리뷰가 정확합니까? 'yes' 또는 'no'만 답하세요."
        )
        try:
            response = self._call_fast_llm(prompt)
            return response.strip().lower().startswith("yes")
        except Exception:
            logger.warning("LLM 검증 실패, 코멘트 유지", exc_info=True)
            return True  # 실패 시 코멘트 유지 (보수적)

    def _call_fast_llm(self, prompt: str) -> str:
        """경량 모델(7b)을 호출한다."""
        resp = httpx.post(
            f"{self._ollama_url}/api/generate",
            json={
                "model": self._fast_model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.0, "num_ctx": 2048},
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()["response"]
