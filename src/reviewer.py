"""리뷰 생성 - diff 파싱 → 컨텍스트 수집 → LLM 호출 → 검증 → 구조화된 리뷰 반환.

하이브리드 에이전트 아키텍처:
- 컨텍스트 수집: 코드 기반 (AST 분석, 전체 파일 fetch)
- 리뷰 생성: 주 모델 (14b)
- 리뷰 검증: 룰 기반 + 경량 모델 (7b)
"""

import json
import logging
import re
from dataclasses import dataclass

import httpx

from src.config import settings
from src.cve_formatter import format_cve_comments
from src.cve_scanner import CveScanner
from src.dependency_parser import parse_dependencies_from_diff
from src.diff_parser import DiffResult, FileDiff, parse_diff
from src.prompt import build_enriched_review_prompt, build_review_prompt, format_diff
from src.retriever import Retriever

logger = logging.getLogger(__name__)


@dataclass
class ReviewComment:
    file: str
    line: int
    severity: str  # "critical", "warning", "info"
    message: str


class Reviewer:
    def __init__(
        self,
        retriever: Retriever | None = None,
        cve_scanner: CveScanner | None = None,
    ):
        self._retriever = retriever or Retriever()
        self._cve_scanner = cve_scanner
        self._context_enricher = None
        self._review_validator = None

    def review(
        self,
        diff_text: str,
        project_id: int | None = None,
        mr_iid: int | None = None,
    ) -> list[ReviewComment]:
        """diff 텍스트를 분석하여 리뷰 코멘트를 생성한다.

        project_id가 제공되면 하이브리드 에이전트 파이프라인이 활성화된다:
        1. 컨텍스트 수집 (AST 분석, 전체 파일 fetch)
        2. 강화 프롬프트로 리뷰 (14b 모델)
        3. 룰 기반 + LLM 오탐 검증 (7b 모델)
        """
        diff_result = parse_diff(diff_text)
        all_comments: list[ReviewComment] = []

        # 컨텍스트 수집 (하이브리드 모드)
        file_contexts = {}
        if settings.context_enrichment_enabled and project_id:
            file_contexts = self._collect_context(project_id, mr_iid, diff_result)

        # 코드 리뷰
        for file_diff in diff_result.reviewable_files:
            if not file_diff.added_lines and not file_diff.deleted_lines:
                continue

            ctx = file_contexts.get(file_diff.filename)
            comments = self._review_file(file_diff, ctx)
            all_comments.extend(comments)

        # 리뷰 검증 (하이브리드 모드)
        if settings.review_validation_enabled and file_contexts:
            all_comments = self._validate_comments(all_comments, file_contexts, diff_result)

        # CVE 취약점 스캔
        if settings.cve_scan_enabled:
            try:
                cve_comments = self._scan_cve(
                    diff_result,
                    list(file_contexts.values()) if file_contexts else None,
                )
                all_comments.extend(cve_comments)
            except Exception:
                logger.warning("CVE 스캔 중 오류 발생 — 코드 리뷰 결과만 반환합니다", exc_info=True)

        return all_comments

    def _collect_context(
        self, project_id: int, mr_iid: int | None, diff_result: DiffResult
    ) -> dict[str, "FileContext"]:
        """컨텍스트를 수집한다. 실패해도 빈 dict를 반환한다."""
        try:
            from src.context_enricher import ContextEnricher
            from src.gitlab_client import GitLabClient

            if self._context_enricher is None:
                self._context_enricher = ContextEnricher(GitLabClient())
            contexts = self._context_enricher.enrich(
                project_id, mr_iid or 0, diff_result.reviewable_files
            )
            return {ctx.file_path: ctx for ctx in contexts}
        except Exception:
            logger.warning("컨텍스트 수집 실패 — 기본 모드로 진행합니다", exc_info=True)
            return {}

    def _validate_comments(
        self,
        comments: list[ReviewComment],
        file_contexts: dict[str, "FileContext"],
        diff_result: DiffResult,
    ) -> list[ReviewComment]:
        """리뷰 코멘트를 검증한다."""
        try:
            from src.review_validator import ReviewValidator

            if self._review_validator is None:
                self._review_validator = ReviewValidator()
            file_diffs = {f.filename: f for f in diff_result.reviewable_files}
            return self._review_validator.validate(comments, file_contexts, file_diffs)
        except Exception:
            logger.warning("리뷰 검증 실패 — 원본 코멘트를 반환합니다", exc_info=True)
            return comments

    def _scan_cve(
        self, diff_result: DiffResult, file_contexts: list | None = None
    ) -> list[ReviewComment]:
        """diff에서 의존성을 추출하고 CVE를 스캔한다."""
        deps = parse_dependencies_from_diff(diff_result)
        if not deps:
            return []
        if self._cve_scanner is None:
            self._cve_scanner = CveScanner()
        results = self._cve_scanner.scan_dependencies(deps, file_contexts)
        return format_cve_comments(results)

    def _review_file(self, file_diff: FileDiff, file_context=None) -> list[ReviewComment]:
        """단일 파일에 대한 리뷰를 생성한다."""
        # 1. 변경 코드 기반 관련 가이드라인 검색
        query = self._build_search_query(file_diff)
        guidelines = self._retriever.search(query)

        # 2. 프롬프트 조립 (컨텍스트가 있으면 강화 프롬프트)
        if file_context:
            system_prompt, user_prompt = build_enriched_review_prompt(
                file_diff, guidelines, file_context
            )
            model = settings.llm_model_primary
            num_ctx = 32768
        else:
            system_prompt, user_prompt = build_review_prompt(file_diff, guidelines)
            model = settings.llm_model
            num_ctx = 8192

        # 3. LLM 호출
        response = self._call_llm(system_prompt, user_prompt, model=model, num_ctx=num_ctx)

        # 4. 응답 파싱
        return self._parse_response(response, file_diff.filename)

    def _build_search_query(self, file_diff: FileDiff) -> str:
        """파일 변경 사항에서 검색 쿼리를 생성한다."""
        added = [l.content for l in file_diff.added_lines if l.content.strip()]
        if not added:
            added = [l.content for l in file_diff.deleted_lines if l.content.strip()]
        query = "\n".join(added)
        return query[:500]

    def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        num_ctx: int = 8192,
    ) -> str:
        """Ollama API를 호출하여 리뷰를 생성한다."""
        resp = httpx.post(
            f"{settings.ollama_base_url}/api/generate",
            json={
                "model": model or settings.llm_model,
                "system": system_prompt,
                "prompt": user_prompt,
                "stream": False,
                "options": {
                    "temperature": 0.1,
                    "num_ctx": num_ctx,
                },
            },
            timeout=300.0,
        )
        resp.raise_for_status()
        return resp.json()["response"]

    def _parse_response(self, response: str, filename: str) -> list[ReviewComment]:
        """LLM 응답에서 JSON 배열을 추출하여 ReviewComment 리스트로 변환한다."""
        json_match = re.search(r"```(?:json)?\s*(\[.*?])\s*```", response, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            json_match = re.search(r"\[.*]", response, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
            else:
                logger.warning("LLM 응답에서 JSON을 찾을 수 없음: %s", response[:200])
                return []

        try:
            items = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning("JSON 파싱 실패: %s", json_str[:200])
            return []

        if not isinstance(items, list):
            return []

        comments = []
        for item in items:
            if not isinstance(item, dict):
                continue
            comments.append(ReviewComment(
                file=item.get("file", filename),
                line=item.get("line", 0),
                severity=item.get("severity", "info"),
                message=item.get("message", ""),
            ))
        return comments
