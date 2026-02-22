"""리뷰 생성 - diff 파싱 → 가이드라인 검색 → LLM 호출 → 구조화된 리뷰 반환."""

import json
import logging
import re
from dataclasses import dataclass

import httpx

from src.config import settings
from src.diff_parser import FileDiff, parse_diff
from src.prompt import build_review_prompt, format_diff
from src.retriever import Retriever

logger = logging.getLogger(__name__)


@dataclass
class ReviewComment:
    file: str
    line: int
    severity: str  # "critical", "warning", "info"
    message: str


class Reviewer:
    def __init__(self, retriever: Retriever | None = None):
        self._retriever = retriever or Retriever()

    def review(self, diff_text: str) -> list[ReviewComment]:
        """diff 텍스트를 분석하여 리뷰 코멘트를 생성한다."""
        diff_result = parse_diff(diff_text)
        all_comments: list[ReviewComment] = []

        for file_diff in diff_result.reviewable_files:
            if not file_diff.added_lines and not file_diff.deleted_lines:
                continue

            comments = self._review_file(file_diff)
            all_comments.extend(comments)

        return all_comments

    def _review_file(self, file_diff: FileDiff) -> list[ReviewComment]:
        """단일 파일에 대한 리뷰를 생성한다."""
        # 1. 변경 코드 기반 관련 가이드라인 검색
        query = self._build_search_query(file_diff)
        guidelines = self._retriever.search(query)

        # 2. 프롬프트 조립
        system_prompt, user_prompt = build_review_prompt(file_diff, guidelines)

        # 3. LLM 호출
        response = self._call_llm(system_prompt, user_prompt)

        # 4. 응답 파싱
        return self._parse_response(response, file_diff.filename)

    def _build_search_query(self, file_diff: FileDiff) -> str:
        """파일 변경 사항에서 검색 쿼리를 생성한다.

        추가된 라인의 코드를 기반으로 관련 가이드라인을 검색한다.
        너무 긴 경우 앞부분만 사용한다.
        """
        added = [l.content for l in file_diff.added_lines if l.content.strip()]
        if not added:
            # 삭제만 있는 경우 삭제 라인 사용
            added = [l.content for l in file_diff.deleted_lines if l.content.strip()]

        # 검색 쿼리는 500자 이내로 제한
        query = "\n".join(added)
        return query[:500]

    def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        """Ollama API를 호출하여 리뷰를 생성한다."""
        resp = httpx.post(
            f"{settings.ollama_base_url}/api/generate",
            json={
                "model": settings.llm_model,
                "system": system_prompt,
                "prompt": user_prompt,
                "stream": False,
                "options": {
                    "temperature": 0.1,
                    "num_ctx": 8192,
                },
            },
            timeout=300.0,
        )
        resp.raise_for_status()
        return resp.json()["response"]

    def _parse_response(self, response: str, filename: str) -> list[ReviewComment]:
        """LLM 응답에서 JSON 배열을 추출하여 ReviewComment 리스트로 변환한다."""
        # JSON 블록 추출 (```json ... ``` 또는 bare JSON)
        json_match = re.search(r"```(?:json)?\s*(\[.*?])\s*```", response, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            # bare JSON 시도
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
