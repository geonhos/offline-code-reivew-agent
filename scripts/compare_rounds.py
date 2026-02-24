"""리뷰 품질 개선 라운드별 비교 테스트.

Round 1: Baseline (RAG 없음, 기본 프롬프트)
Round 2: RAG 가이드라인 추가
Round 3: Java 전용 Few-shot 추가
Round 4: 체크리스트 프롬프트

Usage:
    python -m scripts.compare_rounds
    python -m scripts.compare_rounds --round 2
"""

import argparse
import json
import re
import time
from pathlib import Path
from unittest.mock import MagicMock

import httpx

from src.config import settings
from src.diff_parser import parse_diff
from src.prompt import (
    FEW_SHOT_EXAMPLES,
    REVIEW_PROMPT_TEMPLATE,
    SYSTEM_PROMPT,
    format_diff,
    format_guidelines,
)
from src.vectorstore import GuidelineChunk

DIFF_FILE = Path("tests/fixtures/springboot-ddd.diff")

# ─── 기대 이슈 정의 ──────────────────────────────────────────

EXPECTED_ISSUES = {
    "DB 비밀번호 하드코딩": {
        "keywords": ["password", "비밀번호", "하드코딩", "환경 변수", "평문"],
        "category": "보안",
    },
    "ddl-auto: update": {
        "keywords": ["ddl-auto", "update", "validate", "none", "데이터 유실"],
        "category": "보안",
    },
    "System.out.println": {
        "keywords": ["system.out", "println", "로거", "logger", "slf4j", "로깅"],
        "category": "코드 품질",
    },
    "e.printStackTrace()": {
        "keywords": ["printstacktrace", "스택 트레이스", "로거", "log.error"],
        "category": "코드 품질",
    },
    "RuntimeException 남용": {
        "keywords": ["runtimeexception", "커스텀 예외", "비즈니스 예외", "custom exception"],
        "category": "예외 처리",
    },
    "@Valid 누락": {
        "keywords": ["@valid", "검증", "validation", "bean validation", "입력 검증"],
        "category": "입력 검증",
    },
    "findAll() 성능": {
        "keywords": ["findall", "전체 조회", "메모리", "페이지", "쿼리", "집계", "count", "sum"],
        "category": "성능",
    },
    "RestTemplate 타임아웃": {
        "keywords": ["timeout", "타임아웃", "connecttimeout", "readtimeout"],
        "category": "성능",
    },
    "분산 트랜잭션": {
        "keywords": ["트랜잭션", "transaction", "saga", "이벤트", "보상", "재고", "외부 호출"],
        "category": "아키텍처",
    },
    "cancel() 상태 검증": {
        "keywords": ["상태 검증", "상태 확인", "illegalstate", "취소할 수 없", "상태 전이"],
        "category": "도메인 로직",
    },
    "RuntimeException catch 노출": {
        "keywords": ["runtimeexception", "내부 오류", "예외 메시지", "노출", "e.getmessage"],
        "category": "보안",
    },
}

# ─── Java 전용 Few-shot ──────────────────────────────────────

JAVA_FEW_SHOT = """\
## 리뷰 예시 (Java/Spring Boot)

### 좋은 리뷰 (구체적, Spring 패턴 지적):
```json
[
  {
    "file": "src/main/resources/application.yml",
    "line": 5,
    "severity": "critical",
    "message": "DB 비밀번호가 평문으로 작성되어 있습니다. 환경 변수(${DB_PASSWORD})로 대체하세요."
  },
  {
    "file": "src/main/resources/application.yml",
    "line": 9,
    "severity": "critical",
    "message": "ddl-auto: update는 운영 환경에서 데이터 유실 위험이 있습니다. validate 또는 none으로 변경하세요."
  },
  {
    "file": "OrderController.java",
    "line": 20,
    "severity": "warning",
    "message": "@RequestBody에 @Valid가 누락되었습니다. 입력 검증 없이 서비스 레이어로 전달됩니다."
  },
  {
    "file": "OrderService.java",
    "line": 30,
    "severity": "warning",
    "message": "RuntimeException 대신 OrderNotFoundException 등 커스텀 예외를 사용하세요."
  },
  {
    "file": "RestTemplateConfig.java",
    "line": 12,
    "severity": "warning",
    "message": "RestTemplate에 타임아웃이 설정되지 않았습니다. 외부 서비스 장애 시 스레드 풀이 고갈됩니다."
  }
]
```

### 나쁜 리뷰 (모호, Spring 컨텍스트 무시):
```json
[
  {
    "file": "OrderService.java",
    "line": 0,
    "severity": "info",
    "message": "코드를 개선하세요."
  }
]
```
"""

# ─── 체크리스트 프롬프트 ──────────────────────────────────────

CHECKLIST_SYSTEM_PROMPT = """\
You are an expert code reviewer specializing in Java and Spring Boot applications.

Review the code changes and check EVERY item in this checklist:

### Security Checklist
- [ ] Hardcoded passwords, API keys, or secrets in code or config files
- [ ] SQL injection vulnerabilities (string concatenation in queries)
- [ ] Sensitive information exposed in error messages or logs
- [ ] ddl-auto set to update/create in production config

### Input Validation Checklist
- [ ] @Valid annotation on @RequestBody parameters
- [ ] Bean Validation annotations on DTO fields (@NotNull, @NotBlank, etc.)
- [ ] Proper null checks before using objects

### Exception Handling Checklist
- [ ] Using RuntimeException directly instead of custom exceptions
- [ ] Using System.out.println() or e.printStackTrace() instead of SLF4J logger
- [ ] Catching generic Exception/RuntimeException
- [ ] Swallowing exceptions without logging

### Performance Checklist
- [ ] N+1 query problems (querying in loops)
- [ ] Loading all records with findAll() when filtering/aggregating
- [ ] Missing timeout configuration on HTTP clients (RestTemplate/WebClient)
- [ ] Division by zero risk (dividing by collection size without empty check)

### Architecture Checklist
- [ ] Domain state changes without validation (e.g., cancel without checking current status)
- [ ] External service calls inside transactions (distributed transaction risk)
- [ ] Mixing concerns across layers (DDD violations)

For EACH issue found, report it with the exact file and line number.
Classify severity: critical (security, data loss), warning (bugs, bad practice), info (style, improvement).
Respond ONLY with the JSON array. Write comments in Korean.
"""


def call_llm(system_prompt: str, user_prompt: str) -> str:
    try:
        resp = httpx.post(
            f"{settings.ollama_base_url}/api/generate",
            json={
                "model": settings.llm_model,
                "system": system_prompt,
                "prompt": user_prompt,
                "stream": False,
                "options": {"temperature": 0.1, "num_ctx": 8192},
            },
            timeout=600.0,
        )
        resp.raise_for_status()
        return resp.json()["response"]
    except httpx.ReadTimeout:
        print("    ⚠️  타임아웃 — 건너뜀")
        return "[]"


def try_parse(resp: str) -> list[dict] | None:
    match = re.search(r"\[.*]", resp, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


def make_fake_guidelines(texts: list[tuple[str, str]]) -> list[GuidelineChunk]:
    """테스트용 가이드라인 청크를 직접 생성한다."""
    chunks = []
    for i, (content, category) in enumerate(texts):
        chunks.append(GuidelineChunk(
            id=i + 1,
            content=content,
            category=category,
            source="springboot_guide.md",
            chunk_index=i,
            score=0.9,
        ))
    return chunks


def check_issue(comments: list[dict], keywords: list[str]) -> bool:
    text = json.dumps(comments, ensure_ascii=False).lower()
    return any(kw.lower() in text for kw in keywords)


def run_round(round_num: int, diff_text: str) -> dict:
    """라운드별 설정을 적용하여 리뷰를 실행한다."""
    diff_result = parse_diff(diff_text)
    reviewable = diff_result.reviewable_files

    all_comments: list[dict] = []
    total_time = 0

    for file_diff in reviewable:
        if not file_diff.added_lines:
            continue

        # 라운드별 프롬프트 구성
        if round_num == 1:
            # Baseline: 기본 프롬프트, RAG 없음
            system = SYSTEM_PROMPT + "\n" + FEW_SHOT_EXAMPLES
            guidelines_text = format_guidelines([])
        elif round_num == 2:
            # RAG 가이드라인 추가
            system = SYSTEM_PROMPT + "\n" + FEW_SHOT_EXAMPLES
            guidelines = _get_guidelines_for_file(file_diff.filename)
            guidelines_text = format_guidelines(guidelines)
        elif round_num == 3:
            # Java 전용 Few-shot
            system = SYSTEM_PROMPT + "\n" + JAVA_FEW_SHOT
            guidelines = _get_guidelines_for_file(file_diff.filename)
            guidelines_text = format_guidelines(guidelines)
        elif round_num == 4:
            # 체크리스트 프롬프트
            system = CHECKLIST_SYSTEM_PROMPT + "\n" + JAVA_FEW_SHOT
            guidelines = _get_guidelines_for_file(file_diff.filename)
            guidelines_text = format_guidelines(guidelines)
        else:
            raise ValueError(f"Unknown round: {round_num}")

        diff_content = format_diff(file_diff)
        user_prompt = REVIEW_PROMPT_TEMPLATE.format(
            guidelines=guidelines_text,
            filename=file_diff.filename,
            diff_content=diff_content,
        )

        start = time.time()
        resp = call_llm(system, user_prompt)
        elapsed = time.time() - start
        total_time += elapsed

        parsed = try_parse(resp)
        if parsed:
            all_comments.extend(parsed)

    return {
        "round": round_num,
        "comments": all_comments,
        "time": total_time,
    }


def _get_guidelines_for_file(filename: str) -> list[GuidelineChunk]:
    """파일 종류에 따라 관련 가이드라인을 반환한다."""
    guidelines = []

    if filename.endswith(".yml") or filename.endswith(".yaml"):
        guidelines.extend(make_fake_guidelines([
            (
                "application.yml의 민감 값(비밀번호, API 키)은 환경 변수(${ENV_VAR})로 주입한다. 평문 하드코딩 금지.",
                "security",
            ),
            (
                "spring.jpa.hibernate.ddl-auto는 운영 환경에서 반드시 none 또는 validate로 설정한다. "
                "update나 create는 데이터 유실 위험이 있다. show-sql: true도 운영에서는 비활성화한다.",
                "security",
            ),
        ]))
    elif "Controller" in filename or "api/" in filename:
        guidelines.extend(make_fake_guidelines([
            (
                "Controller의 @RequestBody에는 반드시 @Valid를 추가한다. "
                "Request DTO에 @NotNull, @NotBlank, @Size 등 Bean Validation 어노테이션을 사용한다.",
                "security",
            ),
            (
                "RuntimeException을 직접 throw하지 않는다. OrderNotFoundException 등 커스텀 예외를 정의하여 사용한다.",
                "error_handling",
            ),
            (
                "e.printStackTrace()를 사용하지 않는다. log.error(\"메시지\", e)로 SLF4J 로거를 사용한다. "
                "예외 메시지를 클라이언트에 그대로 노출하지 않는다.",
                "error_handling",
            ),
        ]))
    elif "Service" in filename:
        guidelines.extend(make_fake_guidelines([
            (
                "RuntimeException을 직접 throw하지 않는다. 비즈니스 예외(OrderNotFoundException 등)를 정의하여 사용한다.",
                "error_handling",
            ),
            (
                "findAll()로 전체 조회 후 메모리에서 필터링하지 않는다. "
                "DB 수준의 쿼리(GROUP BY, COUNT, SUM)나 페이지네이션을 사용한다.",
                "performance",
            ),
            (
                "트랜잭션 내에서 외부 서비스(HTTP)를 직접 호출하지 않는다. "
                "실패 시 DB는 롤백되지만 외부 호출은 취소되지 않는다. 이벤트 기반 처리를 권장한다.",
                "performance",
            ),
        ]))
    elif "Config" in filename or "config/" in filename:
        guidelines.extend(make_fake_guidelines([
            (
                "RestTemplate에 반드시 connectTimeout과 readTimeout을 설정한다. "
                "타임아웃 미설정 시 외부 서비스 장애가 전파되어 스레드 풀이 고갈될 수 있다.",
                "performance",
            ),
        ]))
    elif "Client" in filename or "client/" in filename:
        guidelines.extend(make_fake_guidelines([
            (
                "System.out.println()을 사용하지 않는다. SLF4J 로거(log.info, log.error)를 사용한다.",
                "code_structure",
            ),
            (
                "예외를 catch할 때 구체적인 예외 타입을 명시한다. Exception 전체를 catch하지 않는다. "
                "null을 반환하는 대신 예외를 던지거나 Optional을 사용한다.",
                "error_handling",
            ),
        ]))
    elif filename.endswith(".java"):
        # 도메인 파일 등
        guidelines.extend(make_fake_guidelines([
            (
                "도메인 엔티티의 상태 변경 메서드에서 현재 상태를 검증한다. "
                "유효하지 않은 상태 전이(예: COMPLETED → CANCELLED)는 IllegalStateException을 발생시킨다.",
                "code_structure",
            ),
        ]))

    return guidelines


ROUND_NAMES = {
    1: "Baseline (RAG 없음)",
    2: "RAG 가이드라인 추가",
    3: "Java 전용 Few-shot",
    4: "체크리스트 프롬프트",
}


def print_round_result(result: dict):
    round_num = result["round"]
    comments = result["comments"]

    print(f"\n{'=' * 70}")
    print(f"  Round {round_num}: {ROUND_NAMES[round_num]}")
    print(f"  이슈 {len(comments)}건 | 소요 {result['time']:.1f}s")
    print(f"{'=' * 70}")

    # 코멘트 출력
    for c in comments:
        sev = c.get("severity", "?")
        line = c.get("line", "?")
        f = c.get("file", "?")
        msg = c.get("message", "")[:120]
        print(f"  [{sev}] {f}:L{line} — {msg}")

    # 기대 이슈 검출 여부
    print(f"\n  {'기대 이슈':<25} {'카테고리':<10} {'탐지':>6}")
    print(f"  {'─' * 50}")
    detected = 0
    for label, info in EXPECTED_ISSUES.items():
        found = check_issue(comments, info["keywords"])
        if found:
            detected += 1
        print(f"  {label:<25} {info['category']:<10} {'✅' if found else '❌':>6}")

    print(f"\n  탐지율: {detected}/{len(EXPECTED_ISSUES)} ({detected/len(EXPECTED_ISSUES)*100:.0f}%)")


def print_comparison(results: list[dict]):
    print(f"\n{'=' * 70}")
    print("  라운드별 비교 요약")
    print(f"{'=' * 70}")

    # 헤더
    headers = [f"R{r['round']}" for r in results]
    print(f"\n  {'기대 이슈':<25}", end="")
    for h in headers:
        print(f" {h:>6}", end="")
    print()
    print(f"  {'─' * (25 + 7 * len(results))}")

    # 각 이슈별
    for label, info in EXPECTED_ISSUES.items():
        print(f"  {label:<25}", end="")
        for r in results:
            found = check_issue(r["comments"], info["keywords"])
            print(f" {'✅':>6}" if found else f" {'❌':>6}", end="")
        print()

    # 요약
    print(f"  {'─' * (25 + 7 * len(results))}")
    print(f"  {'탐지율':<25}", end="")
    for r in results:
        detected = sum(1 for info in EXPECTED_ISSUES.values()
                       if check_issue(r["comments"], info["keywords"]))
        rate = f"{detected}/{len(EXPECTED_ISSUES)}"
        print(f" {rate:>6}", end="")
    print()

    print(f"  {'이슈 수':<25}", end="")
    for r in results:
        print(f" {len(r['comments']):>5}건", end="")
    print()

    print(f"  {'소요 시간':<25}", end="")
    for r in results:
        print(f" {r['time']:>4.0f}s", end="")
    print()


def main():
    parser = argparse.ArgumentParser(description="리뷰 품질 개선 라운드별 비교")
    parser.add_argument("--round", type=int, choices=[1, 2, 3, 4], help="특정 라운드만 실행")
    args = parser.parse_args()

    diff_text = DIFF_FILE.read_text()

    print(f"🤖 리뷰 품질 개선 비교 테스트")
    print(f"모델: {settings.llm_model}")
    print(f"diff: {DIFF_FILE} ({len(parse_diff(diff_text).reviewable_files)}개 파일)")
    print(f"기대 이슈: {len(EXPECTED_ISSUES)}개")

    if args.round:
        rounds = [args.round]
    else:
        rounds = [1, 2, 3, 4]

    results = []
    for r in rounds:
        print(f"\n▶ Round {r}/{max(rounds)}: {ROUND_NAMES[r]}...")
        result = run_round(r, diff_text)
        results.append(result)
        print_round_result(result)

    if len(results) > 1:
        print_comparison(results)


if __name__ == "__main__":
    main()
