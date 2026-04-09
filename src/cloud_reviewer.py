"""Cloud LLM 어댑터 — GPT-4o, Claude Sonnet 4 호출 및 토큰 사용량 추적.

벤치마크 비교용으로 기존 Ollama 기반 Reviewer와 동일한 인터페이스를 제공한다.
openai / anthropic 패키지 의존성 없이 httpx로 직접 API를 호출한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

# 가격 (USD / 1M 토큰)
_PRICING: dict[str, dict[str, float]] = {
    "openai": {"input": 2.50, "output": 10.00},
    "anthropic": {"input": 3.00, "output": 15.00},
}

_OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
_ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"
_TIMEOUT = 300.0
_TEMPERATURE = 0.1


@dataclass
class CloudLLMResponse:
    """Cloud LLM 호출 결과 및 토큰 사용량."""

    response: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    model: str
    cost_usd: float


def _calc_cost(provider: str, input_tokens: int, output_tokens: int) -> float:
    """토큰 수와 provider 기준으로 USD 비용을 계산한다.

    Args:
        provider: "openai" 또는 "anthropic".
        input_tokens: 입력 토큰 수.
        output_tokens: 출력 토큰 수.

    Returns:
        예상 USD 비용.
    """
    pricing = _PRICING[provider]
    return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000


class CloudReviewer:
    """GPT-4o 및 Claude Sonnet 4 호출 어댑터.

    모델 이름 접두사로 provider를 자동 판별한다.
    - "gpt-" 로 시작하면 OpenAI
    - "claude-" 로 시작하면 Anthropic
    """

    def call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
    ) -> CloudLLMResponse:
        """Cloud LLM을 호출하여 응답과 토큰 사용량을 반환한다.

        Args:
            system_prompt: 시스템 프롬프트.
            user_prompt: 사용자 프롬프트.
            model: 호출할 모델 이름 (예: "gpt-4o", "claude-sonnet-4-20250514").

        Returns:
            CloudLLMResponse — 응답 텍스트, 토큰 수, 비용 포함.

        Raises:
            ValueError: 지원하지 않는 모델 이름.
            httpx.HTTPStatusError: API 오류 응답.
        """
        if model.startswith("gpt-"):
            return self._call_openai(system_prompt, user_prompt, model)
        elif model.startswith("claude-"):
            return self._call_anthropic(system_prompt, user_prompt, model)
        else:
            raise ValueError(f"지원하지 않는 모델: {model!r}. 'gpt-' 또는 'claude-' 로 시작해야 합니다.")

    def _call_openai(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
    ) -> CloudLLMResponse:
        """OpenAI Chat Completions API를 호출한다.

        Args:
            system_prompt: 시스템 메시지.
            user_prompt: 사용자 메시지.
            model: OpenAI 모델 이름.

        Returns:
            CloudLLMResponse.
        """
        if not settings.openai_api_key:
            raise ValueError("REVIEW_OPENAI_API_KEY 환경 변수가 설정되지 않았습니다.")

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": _TEMPERATURE,
        }
        headers = {
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        }

        logger.debug("OpenAI API 호출: model=%s", model)
        resp = httpx.post(
            _OPENAI_API_URL,
            json=payload,
            headers=headers,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        total_tokens = input_tokens + output_tokens
        cost = _calc_cost("openai", input_tokens, output_tokens)

        logger.info(
            "OpenAI 호출 완료: model=%s input=%d output=%d cost=$%.4f",
            model, input_tokens, output_tokens, cost,
        )
        return CloudLLMResponse(
            response=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            model=model,
            cost_usd=cost,
        )

    def _call_anthropic(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
    ) -> CloudLLMResponse:
        """Anthropic Messages API를 호출한다.

        Args:
            system_prompt: 시스템 메시지.
            user_prompt: 사용자 메시지.
            model: Anthropic 모델 이름.

        Returns:
            CloudLLMResponse.
        """
        if not settings.anthropic_api_key:
            raise ValueError("REVIEW_ANTHROPIC_API_KEY 환경 변수가 설정되지 않았습니다.")

        payload = {
            "model": model,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 4096,
            "temperature": _TEMPERATURE,
        }
        headers = {
            "x-api-key": settings.anthropic_api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }

        logger.debug("Anthropic API 호출: model=%s", model)
        resp = httpx.post(
            _ANTHROPIC_API_URL,
            json=payload,
            headers=headers,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        text = data["content"][0]["text"]
        usage = data.get("usage", {})
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        total_tokens = input_tokens + output_tokens
        cost = _calc_cost("anthropic", input_tokens, output_tokens)

        logger.info(
            "Anthropic 호출 완료: model=%s input=%d output=%d cost=$%.4f",
            model, input_tokens, output_tokens, cost,
        )
        return CloudLLMResponse(
            response=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            model=model,
            cost_usd=cost,
        )
