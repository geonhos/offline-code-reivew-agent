"""대규모 종합 품질 테스트 — 4모델 × 8설정 비교.

Enterprise E-Commerce 코드(28파일, 5 마이크로서비스, 25개 이슈)로
모델/파라미터 최적 조합을 찾는다.

Usage:
    python -m scripts.compare_ecommerce
    python -m scripts.compare_ecommerce --config A
    python -m scripts.compare_ecommerce --config A B C
"""

import argparse
import json
import os
import re
import time
from pathlib import Path

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

DIFF_FILE = Path("tests/fixtures/ecommerce-platform.diff")
RESULTS_DIR = Path("results")

# ─── 25개 기대 이슈 정의 ──────────────────────────────────────

EXPECTED_ISSUES = {
    "1. JWT 시크릿 하드코딩": {
        "keywords": ["jwt", "secret", "시크릿", "하드코딩", "환경 변수", "secretkey"],
        "category": "보안",
        "difficulty": "쉬움",
    },
    "2. 비밀번호 MD5 해싱": {
        "keywords": ["md5", "bcrypt", "해싱", "비밀번호", "passwordencoder", "scrypt", "argon"],
        "category": "보안",
        "difficulty": "보통",
    },
    "3. CORS 와일드카드": {
        "keywords": ["cors", "와일드카드", "allowedorigins", "origin", '"*"', "모든 도메인"],
        "category": "보안",
        "difficulty": "보통",
    },
    "4. SQL Injection": {
        "keywords": ["sql injection", "sql 인젝션", "문자열 연결", "파라미터 바인딩", "concatenat", "인젝션"],
        "category": "보안",
        "difficulty": "쉬움",
    },
    "5. 신용카드 번호 로그": {
        "keywords": ["카드번호", "cardnumber", "로그", "마스킹", "pci", "민감", "신용카드"],
        "category": "보안",
        "difficulty": "쉬움",
    },
    "6. DB 비밀번호 하드코딩": {
        "keywords": ["password", "비밀번호", "하드코딩", "환경 변수", "평문", "${"],
        "category": "보안",
        "difficulty": "쉬움",
    },
    "7. SMTP 비밀번호 하드코딩": {
        "keywords": ["smtp", "mail", "비밀번호", "하드코딩", "gmail", "환경 변수"],
        "category": "보안",
        "difficulty": "쉬움",
    },
    "8. TLS 검증 비활성화": {
        "keywords": ["tls", "ssl", "trustmanager", "인증서", "검증", "certificate", "mitm"],
        "category": "보안",
        "difficulty": "보통",
    },
    "9. N+1 쿼리": {
        "keywords": ["n+1", "n\\+1", "개별 조회", "join fetch", "entitygraph", "루프", "반복 조회"],
        "category": "성능",
        "difficulty": "보통",
    },
    "10. findAll() 메모리 필터링": {
        "keywords": ["findall", "전체 조회", "메모리", "필터링", "집계", "count", "sum", "group by"],
        "category": "성능",
        "difficulty": "보통",
    },
    "11. 페이지네이션 없는 검색": {
        "keywords": ["페이지", "pagination", "pageable", "page", "limit", "offset", "전체 결과"],
        "category": "성능",
        "difficulty": "보통",
    },
    "12. 동기 이메일 발송": {
        "keywords": ["동기", "블로킹", "blocking", "@async", "비동기", "메시지 큐", "kafka", "rabbitmq", "스레드"],
        "category": "성능",
        "difficulty": "어려움",
    },
    "13. HTTP 타임아웃 미설정": {
        "keywords": ["timeout", "타임아웃", "connecttimeout", "readtimeout", "무한 대기", "시간 제한"],
        "category": "성능",
        "difficulty": "보통",
    },
    "14. RuntimeException 직접 throw": {
        "keywords": ["runtimeexception", "커스텀 예외", "비즈니스 예외", "custom exception"],
        "category": "예외 처리",
        "difficulty": "보통",
    },
    "15. e.printStackTrace()": {
        "keywords": ["printstacktrace", "스택 트레이스", "log.error", "slf4j", "로거"],
        "category": "예외 처리",
        "difficulty": "쉬움",
    },
    "16. catch(Exception) 포괄 처리": {
        "keywords": ["catch", "exception", "포괄", "구체적", "예외 타입", "broad"],
        "category": "예외 처리",
        "difficulty": "보통",
    },
    "17. 에러 메시지 클라이언트 노출": {
        "keywords": ["e.getmessage", "에러 메시지", "노출", "내부 오류", "클라이언트"],
        "category": "예외 처리",
        "difficulty": "보통",
    },
    "18. @Valid 누락": {
        "keywords": ["@valid", "검증", "validation", "bean validation", "입력 검증"],
        "category": "입력 검증",
        "difficulty": "쉬움",
    },
    "19. 가격 음수 검증 없음": {
        "keywords": ["음수", "가격", "price", "@positive", "@min", "negative", "양수"],
        "category": "입력 검증",
        "difficulty": "보통",
    },
    "20. 이메일 형식 검증 없음": {
        "keywords": ["이메일", "email", "@email", "형식", "format", "pattern", "검증"],
        "category": "입력 검증",
        "difficulty": "보통",
    },
    "21. 분산 트랜잭션 미보장": {
        "keywords": ["트랜잭션", "transaction", "saga", "이벤트", "보상", "분산", "외부 호출"],
        "category": "아키텍처",
        "difficulty": "어려움",
    },
    "22. 멱등성 키 없음": {
        "keywords": ["멱등", "idempoten", "중복 결제", "재시도", "고유 키", "idempotency-key"],
        "category": "아키텍처",
        "difficulty": "어려움",
    },
    "23. Dead Letter Queue 미구현": {
        "keywords": ["dlq", "dead letter", "메시지 유실", "재처리", "재시도", "큐"],
        "category": "아키텍처",
        "difficulty": "어려움",
    },
    "24. 낙관적 락 없음": {
        "keywords": ["@version", "낙관적", "optimistic", "경합", "동시성", "lock", "concurrent"],
        "category": "동시성",
        "difficulty": "어려움",
    },
    "25. System.out.println": {
        "keywords": ["system.out", "println", "로거", "logger", "slf4j", "로깅"],
        "category": "코드 품질",
        "difficulty": "쉬움",
    },
}

# ─── Java 전용 Few-shot ──────────────────────────────────────

JAVA_FEW_SHOT = """\
## 리뷰 예시 (Java/Spring Boot E-Commerce)

### 좋은 리뷰 (구체적, Spring 패턴 지적):
```json
[
  {
    "file": "application.yml",
    "line": 8,
    "severity": "critical",
    "message": "DB 비밀번호가 평문으로 작성되어 있습니다. 환경 변수(${DB_PASSWORD})로 대체하세요."
  },
  {
    "file": "JwtTokenProvider.java",
    "line": 11,
    "severity": "critical",
    "message": "JWT 시크릿이 하드코딩되어 있습니다. 최소 256bit 이상의 키를 환경 변수로 관리하세요."
  },
  {
    "file": "AuthService.java",
    "line": 28,
    "severity": "critical",
    "message": "MD5는 비밀번호 해싱에 부적합합니다. BCryptPasswordEncoder를 사용하세요."
  },
  {
    "file": "OrderController.java",
    "line": 20,
    "severity": "warning",
    "message": "@RequestBody에 @Valid가 누락되었습니다. 입력 검증 없이 서비스 레이어로 전달됩니다."
  },
  {
    "file": "OrderService.java",
    "line": 35,
    "severity": "warning",
    "message": "@Transactional 내에서 외부 서비스를 호출하면 분산 트랜잭션 정합성이 보장되지 않습니다. Saga 패턴을 고려하세요."
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

# ─── E-Commerce 체크리스트 프롬프트 ──────────────────────────

ECOMMERCE_CHECKLIST_PROMPT = """\
You are an expert code reviewer specializing in Java/Spring Boot E-Commerce microservice systems.

Review the code changes and check EVERY item in this checklist:

### Security Checklist (E-Commerce Critical)
- [ ] JWT secrets hardcoded (must use environment variables, min 256-bit key)
- [ ] Weak password hashing (MD5/SHA instead of bcrypt/scrypt)
- [ ] CORS wildcard (*) allowing all origins
- [ ] SQL injection via string concatenation in queries
- [ ] Credit card numbers or PII logged in plaintext
- [ ] Hardcoded database/SMTP credentials in config files
- [ ] TLS certificate verification disabled (TrustManager override)
- [ ] Sensitive data in error messages exposed to clients

### Performance Checklist
- [ ] N+1 query problems (querying in loops without JOIN FETCH)
- [ ] Loading all records with findAll() then filtering in memory
- [ ] Search APIs without pagination (returning unbounded results)
- [ ] Synchronous blocking calls (email/SMS) in request thread
- [ ] HTTP client timeout not configured (infinite wait risk)

### Exception Handling Checklist
- [ ] RuntimeException thrown directly instead of custom exceptions
- [ ] e.printStackTrace() instead of SLF4J logger
- [ ] Catching generic Exception without specific handling
- [ ] Internal error messages exposed in API responses via e.getMessage()

### Input Validation Checklist
- [ ] @Valid missing on @RequestBody parameters
- [ ] Negative price/quantity not validated (@Positive, @Min)
- [ ] Email format not validated (@Email annotation)

### Architecture Checklist (Microservice)
- [ ] Distributed transaction not guaranteed (stock + payment in single @Transactional)
- [ ] Missing idempotency key for payment APIs (duplicate payment risk)
- [ ] No Dead Letter Queue for failed async messages (message loss)
- [ ] No optimistic locking (@Version) for concurrent updates (race condition)
- [ ] System.out.println instead of SLF4J logger

For EACH issue found, report with exact file and line number.
Classify: critical (security, data loss), warning (bugs, bad practice), info (style).
Respond ONLY with JSON array. Write comments in Korean.
"""

# ─── 8가지 테스트 설정 ───────────────────────────────────────

CONFIGS = {
    "A": {
        "name": "Baseline (qwen 7B, 기본)",
        "model": "qwen2.5-coder:7b",
        "use_rag": False,
        "few_shot": "python",
        "system_prompt": "basic",
        "temperature": 0.1,
        "num_ctx": 8192,
    },
    "B": {
        "name": "RAG 추가 (qwen 7B)",
        "model": "qwen2.5-coder:7b",
        "use_rag": True,
        "few_shot": "python",
        "system_prompt": "basic",
        "temperature": 0.1,
        "num_ctx": 8192,
    },
    "C": {
        "name": "Java Few-shot (qwen 7B)",
        "model": "qwen2.5-coder:7b",
        "use_rag": True,
        "few_shot": "java",
        "system_prompt": "basic",
        "temperature": 0.1,
        "num_ctx": 8192,
    },
    "D": {
        "name": "컨텍스트 확장 16K (qwen 7B)",
        "model": "qwen2.5-coder:7b",
        "use_rag": True,
        "few_shot": "java",
        "system_prompt": "basic",
        "temperature": 0.1,
        "num_ctx": 16384,
    },
    "E": {
        "name": "커스텀 모델 (reviewer)",
        "model": "qwen2.5-coder-reviewer",
        "use_rag": True,
        "few_shot": "java",
        "system_prompt": "basic",
        "temperature": 0.1,
        "num_ctx": 8192,
    },
    "F": {
        "name": "EXAONE 7.8B",
        "model": "exaone3.5:7.8b",
        "use_rag": True,
        "few_shot": "java",
        "system_prompt": "basic",
        "temperature": 0.1,
        "num_ctx": 8192,
    },
    "G": {
        "name": "GPT-OSS 20B",
        "model": "gpt-oss:20b",
        "use_rag": True,
        "few_shot": "java",
        "system_prompt": "basic",
        "temperature": 0.1,
        "num_ctx": 8192,
    },
    "H": {
        "name": "GPT-OSS 20B + 체크리스트 16K",
        "model": "gpt-oss:20b",
        "use_rag": True,
        "few_shot": "java",
        "system_prompt": "checklist",
        "temperature": 0.1,
        "num_ctx": 16384,
    },
}


# ─── 유틸리티 ────────────────────────────────────────────────

def make_fake_guidelines(texts: list[tuple[str, str]]) -> list[GuidelineChunk]:
    """테스트용 가이드라인 청크를 직접 생성한다."""
    chunks = []
    for i, (content, category) in enumerate(texts):
        chunks.append(GuidelineChunk(
            id=i + 1,
            content=content,
            category=category,
            source="ecommerce_security_guide.md",
            chunk_index=i,
            score=0.9,
        ))
    return chunks


def get_guidelines_for_file(filename: str) -> list[GuidelineChunk]:
    """파일 종류에 따라 E-Commerce 특화 가이드라인을 반환한다."""
    guidelines = []

    if filename.endswith(".yml") or filename.endswith(".yaml"):
        guidelines.extend(make_fake_guidelines([
            (
                "application.yml의 민감 값(DB 비밀번호, API 키, SMTP 비밀번호)은 "
                "환경 변수(${ENV_VAR})로 주입한다. 평문 하드코딩 금지.",
                "security",
            ),
            (
                "spring.jpa.hibernate.ddl-auto는 운영 환경에서 none 또는 validate로 설정한다.",
                "security",
            ),
            (
                "SMTP 비밀번호도 환경 변수로 관리한다. gmail_app_password 같은 평문 금지.",
                "security",
            ),
        ]))
    elif "JwtTokenProvider" in filename or "jwt" in filename.lower():
        guidelines.extend(make_fake_guidelines([
            (
                "JWT 서명 키를 소스 코드에 하드코딩하지 않는다. "
                "환경 변수(${JWT_SECRET})로 주입하고, 최소 256bit 이상의 랜덤 값을 사용한다.",
                "security",
            ),
        ]))
    elif "AuthService" in filename:
        guidelines.extend(make_fake_guidelines([
            (
                "MD5, SHA-1을 비밀번호 해싱에 사용하지 않는다. "
                "BCryptPasswordEncoder를 사용한다.",
                "security",
            ),
            (
                "RuntimeException을 직접 throw하지 않는다. 비즈니스 예외를 정의하여 사용한다.",
                "error_handling",
            ),
        ]))
    elif "SecurityConfig" in filename:
        guidelines.extend(make_fake_guidelines([
            (
                "CORS allowedOrigins에 와일드카드(*)를 사용하지 않는다. "
                "운영 환경에서는 허용 도메인을 명시한다.",
                "security",
            ),
        ]))
    elif "SearchService" in filename:
        guidelines.extend(make_fake_guidelines([
            (
                "JPQL/SQL 쿼리에 문자열 연결로 사용자 입력을 포함하지 않는다. "
                "파라미터 바인딩(:param)을 사용한다. SQL Injection 위험.",
                "security",
            ),
            (
                "검색 API는 반드시 페이지네이션을 적용한다. "
                "전체 결과를 반환하면 메모리와 네트워크 부하가 발생한다.",
                "performance",
            ),
            (
                "System.out.println()을 사용하지 않는다. SLF4J 로거를 사용한다.",
                "code_quality",
            ),
        ]))
    elif "Controller" in filename:
        guidelines.extend(make_fake_guidelines([
            (
                "Controller의 @RequestBody에는 반드시 @Valid를 추가한다.",
                "validation",
            ),
            (
                "가격, 수량 등 숫자 필드에 @Positive, @Min(0) 등 범위 검증을 추가한다.",
                "validation",
            ),
            (
                "이메일 필드에 @Email 어노테이션을 사용한다.",
                "validation",
            ),
            (
                "에러 메시지(e.getMessage())를 클라이언트에 그대로 노출하지 않는다. "
                "내부 오류 정보가 유출될 수 있다.",
                "security",
            ),
            (
                "결제 API는 멱등성 키(Idempotency-Key)를 요구해야 한다. "
                "네트워크 재시도 시 중복 결제를 방지한다.",
                "architecture",
            ),
        ]))
    elif "OrderService" in filename or "Service" in filename:
        guidelines.extend(make_fake_guidelines([
            (
                "RuntimeException을 직접 throw하지 않는다. 비즈니스 예외를 정의한다.",
                "error_handling",
            ),
            (
                "findAll()로 전체 조회 후 메모리에서 필터링하지 않는다. "
                "DB 수준 쿼리(GROUP BY, COUNT, SUM)나 페이지네이션을 사용한다.",
                "performance",
            ),
            (
                "트랜잭션 내에서 외부 서비스(HTTP)를 호출하지 않는다. "
                "Saga 패턴 또는 이벤트 기반 처리를 사용한다.",
                "architecture",
            ),
        ]))
    elif "ProductService" in filename:
        guidelines.extend(make_fake_guidelines([
            (
                "루프 내에서 개별 엔티티를 조회하지 않는다 (N+1 쿼리 문제). "
                "@EntityGraph 또는 JOIN FETCH를 사용한다.",
                "performance",
            ),
        ]))
    elif "PaymentService" in filename:
        guidelines.extend(make_fake_guidelines([
            (
                "신용카드 번호를 로그에 그대로 출력하지 않는다. "
                "마스킹 처리(****1234)하거나 로깅에서 제외한다. PCI DSS 규정 위반.",
                "security",
            ),
            (
                "e.printStackTrace()를 사용하지 않는다. log.error('메시지', e)를 사용한다.",
                "error_handling",
            ),
        ]))
    elif "GatewayClient" in filename or "Client" in filename:
        guidelines.extend(make_fake_guidelines([
            (
                "외부 API 호출 시 TLS 인증서 검증을 비활성화하지 않는다. "
                "TrustManager를 오버라이드하여 모든 인증서를 허용하는 코드는 금지.",
                "security",
            ),
            (
                "HTTP 클라이언트에 connectTimeout과 readTimeout을 반드시 설정한다. "
                "미설정 시 외부 장애가 전파되어 스레드 풀이 고갈된다.",
                "performance",
            ),
        ]))
    elif "NotificationService" in filename:
        guidelines.extend(make_fake_guidelines([
            (
                "이메일/SMS 발송은 @Async 또는 메시지 큐로 비동기 처리한다. "
                "요청 스레드에서 동기적으로 처리하면 응답 지연이 발생한다.",
                "performance",
            ),
            (
                "catch(Exception e) 포괄 처리를 피하고 구체적 예외를 구분한다.",
                "error_handling",
            ),
            (
                "비동기 메시지 처리 실패 시 Dead Letter Queue(DLQ)에 저장한다. "
                "재처리 메커니즘 없이 메시지를 유실하지 않는다.",
                "architecture",
            ),
        ]))
    elif filename.endswith(".java"):
        guidelines.extend(make_fake_guidelines([
            (
                "동시 수정이 가능한 엔티티에 @Version 필드를 추가한다 (낙관적 락). "
                "재고 차감, 주문 상태 변경 등 경합 발생 시 필수.",
                "architecture",
            ),
        ]))

    return guidelines


def call_llm(
    system_prompt: str,
    user_prompt: str,
    model: str,
    temperature: float = 0.1,
    num_ctx: int = 8192,
) -> str:
    """Ollama API를 호출한다."""
    try:
        resp = httpx.post(
            f"{settings.ollama_base_url}/api/generate",
            json={
                "model": model,
                "system": system_prompt,
                "prompt": user_prompt,
                "stream": False,
                "options": {"temperature": temperature, "num_ctx": num_ctx},
            },
            timeout=600.0,
        )
        resp.raise_for_status()
        return resp.json()["response"]
    except httpx.ReadTimeout:
        print("    ⚠️  타임아웃 — 건너뜀")
        return "[]"
    except httpx.HTTPStatusError as e:
        print(f"    ⚠️  HTTP 오류 {e.response.status_code} — 건너뜀")
        return "[]"
    except httpx.ConnectError:
        print("    ⚠️  Ollama 연결 실패 — 건너뜀")
        return "[]"


def try_parse(resp: str) -> list[dict] | None:
    """LLM 응답에서 JSON 배열을 추출한다."""
    match = re.search(r"\[.*]", resp, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


def check_issue(comments: list[dict], keywords: list[str]) -> bool:
    """코멘트 목록에서 키워드 매칭으로 이슈 탐지 여부를 판단한다."""
    text = json.dumps(comments, ensure_ascii=False).lower()
    return any(kw.lower() in text for kw in keywords)


# ─── 라운드 실행 ─────────────────────────────────────────────

def run_config(config_key: str, diff_text: str) -> dict:
    """설정에 따라 전체 diff를 리뷰한다."""
    cfg = CONFIGS[config_key]
    diff_result = parse_diff(diff_text)
    reviewable = diff_result.reviewable_files

    all_comments: list[dict] = []
    total_time = 0

    for file_diff in reviewable:
        if not file_diff.added_lines:
            continue

        # 시스템 프롬프트 구성
        if cfg["system_prompt"] == "checklist":
            system = ECOMMERCE_CHECKLIST_PROMPT
        else:
            system = SYSTEM_PROMPT

        # Few-shot 선택
        if cfg["few_shot"] == "java":
            system += "\n" + JAVA_FEW_SHOT
        else:
            system += "\n" + FEW_SHOT_EXAMPLES

        # RAG 가이드라인
        if cfg["use_rag"]:
            guidelines = get_guidelines_for_file(file_diff.filename)
            guidelines_text = format_guidelines(guidelines)
        else:
            guidelines_text = format_guidelines([])

        diff_content = format_diff(file_diff)
        user_prompt = REVIEW_PROMPT_TEMPLATE.format(
            guidelines=guidelines_text,
            filename=file_diff.filename,
            diff_content=diff_content,
        )

        start = time.time()
        resp = call_llm(
            system, user_prompt,
            model=cfg["model"],
            temperature=cfg["temperature"],
            num_ctx=cfg["num_ctx"],
        )
        elapsed = time.time() - start
        total_time += elapsed

        parsed = try_parse(resp)
        if parsed:
            all_comments.extend(parsed)

        # 진행 상황 표시
        print(f"    {file_diff.filename} → {len(parsed or [])}건 ({elapsed:.1f}s)")

    return {
        "config": config_key,
        "config_name": cfg["name"],
        "model": cfg["model"],
        "comments": all_comments,
        "time": total_time,
    }


# ─── 결과 출력 ───────────────────────────────────────────────

def print_config_result(result: dict):
    """단일 설정 결과를 출력한다."""
    config_key = result["config"]
    cfg = CONFIGS[config_key]
    comments = result["comments"]

    print(f"\n{'=' * 80}")
    print(f"  설정 {config_key}: {cfg['name']}")
    print(f"  모델: {cfg['model']} | num_ctx: {cfg['num_ctx']} | RAG: {cfg['use_rag']}")
    print(f"  이슈 {len(comments)}건 | 소요 {result['time']:.1f}s")
    print(f"{'=' * 80}")

    # 코멘트 출력 (최대 30건)
    for c in comments[:30]:
        sev = c.get("severity", "?")
        line = c.get("line", "?")
        f = c.get("file", "?")
        msg = c.get("message", "")[:100]
        print(f"  [{sev}] {f}:L{line} — {msg}")
    if len(comments) > 30:
        print(f"  ... 외 {len(comments) - 30}건")

    # 기대 이슈 검출 여부
    print(f"\n  {'기대 이슈':<30} {'카테고리':<8} {'난이도':<6} {'탐지':>4}")
    print(f"  {'─' * 60}")
    detected = 0
    for label, info in EXPECTED_ISSUES.items():
        found = check_issue(comments, info["keywords"])
        if found:
            detected += 1
        mark = "✅" if found else "❌"
        print(f"  {label:<30} {info['category']:<8} {info['difficulty']:<6} {mark:>4}")

    total = len(EXPECTED_ISSUES)
    print(f"\n  탐지율: {detected}/{total} ({detected / total * 100:.0f}%)")


def print_category_analysis(results: list[dict]):
    """카테고리별 탐지율 분석을 출력한다."""
    categories = {}
    for info in EXPECTED_ISSUES.values():
        cat = info["category"]
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(info)

    print(f"\n{'=' * 80}")
    print("  카테고리별 탐지율")
    print(f"{'=' * 80}")

    headers = [f"  {r['config']}" for r in results]
    print(f"\n  {'카테고리':<12} {'이슈수':>4}", end="")
    for h in headers:
        print(f" {h:>6}", end="")
    print()
    print(f"  {'─' * (16 + 7 * len(results))}")

    for cat, issues in categories.items():
        print(f"  {cat:<12} {len(issues):>3}건", end="")
        for r in results:
            cat_detected = sum(
                1 for info in EXPECTED_ISSUES.values()
                if info["category"] == cat
                and check_issue(r["comments"], info["keywords"])
            )
            rate = f"{cat_detected}/{len(issues)}"
            print(f" {rate:>6}", end="")
        print()


def print_difficulty_analysis(results: list[dict]):
    """난이도별 탐지율 분석을 출력한다."""
    difficulties = {"쉬움": [], "보통": [], "어려움": []}
    for label, info in EXPECTED_ISSUES.items():
        difficulties[info["difficulty"]].append(info)

    print(f"\n{'=' * 80}")
    print("  난이도별 탐지율")
    print(f"{'=' * 80}")

    headers = [f"  {r['config']}" for r in results]
    print(f"\n  {'난이도':<8} {'이슈수':>4}", end="")
    for h in headers:
        print(f" {h:>6}", end="")
    print()
    print(f"  {'─' * (12 + 7 * len(results))}")

    for diff, issues in difficulties.items():
        print(f"  {diff:<8} {len(issues):>3}건", end="")
        for r in results:
            diff_detected = sum(
                1 for info in EXPECTED_ISSUES.values()
                if info["difficulty"] == diff
                and check_issue(r["comments"], info["keywords"])
            )
            rate = f"{diff_detected}/{len(issues)}"
            print(f" {rate:>6}", end="")
        print()


def print_comparison(results: list[dict]):
    """전체 비교표를 출력한다."""
    print(f"\n{'=' * 80}")
    print("  전체 비교 요약")
    print(f"{'=' * 80}")

    # 헤더
    headers = [f"  {r['config']}" for r in results]
    print(f"\n  {'기대 이슈':<30}", end="")
    for h in headers:
        print(f" {h:>6}", end="")
    print()
    print(f"  {'─' * (30 + 7 * len(results))}")

    # 각 이슈별
    for label, info in EXPECTED_ISSUES.items():
        print(f"  {label:<30}", end="")
        for r in results:
            found = check_issue(r["comments"], info["keywords"])
            print(f" {'✅':>6}" if found else f" {'❌':>6}", end="")
        print()

    # 요약
    print(f"  {'─' * (30 + 7 * len(results))}")
    print(f"  {'탐지율':<30}", end="")
    for r in results:
        detected = sum(
            1 for info in EXPECTED_ISSUES.values()
            if check_issue(r["comments"], info["keywords"])
        )
        rate = f"{detected}/{len(EXPECTED_ISSUES)}"
        print(f" {rate:>6}", end="")
    print()

    print(f"  {'이슈 수':<30}", end="")
    for r in results:
        print(f" {len(r['comments']):>5}건", end="")
    print()

    print(f"  {'소요 시간':<30}", end="")
    for r in results:
        print(f" {r['time']:>4.0f}s", end="")
    print()

    print(f"  {'모델':<30}", end="")
    for r in results:
        model_short = r["model"].split(":")[0][:12]
        print(f" {model_short:>6}", end="")
    print()

    # 핵심 비교 축 분석
    print(f"\n{'=' * 80}")
    print("  핵심 비교 축 분석")
    print(f"{'=' * 80}")

    result_map = {r["config"]: r for r in results}

    comparisons = [
        ("A → B", "A", "B", "RAG 가이드라인 효과"),
        ("B → C", "B", "C", "Java Few-shot vs Python Few-shot"),
        ("C → D", "C", "D", "num_ctx 8K → 16K 효과"),
        ("C → E", "C", "E", "커스텀 Modelfile 효과"),
        ("C → F", "C", "F", "동급 모델 교체 (qwen 7B vs exaone 7.8B)"),
        ("C → G", "C", "G", "모델 크기 효과 (7B → 20B)"),
        ("G → H", "G", "H", "20B에서 체크리스트 효과"),
    ]

    for comp_name, key_a, key_b, desc in comparisons:
        if key_a in result_map and key_b in result_map:
            det_a = sum(
                1 for info in EXPECTED_ISSUES.values()
                if check_issue(result_map[key_a]["comments"], info["keywords"])
            )
            det_b = sum(
                1 for info in EXPECTED_ISSUES.values()
                if check_issue(result_map[key_b]["comments"], info["keywords"])
            )
            diff = det_b - det_a
            sign = "+" if diff > 0 else ""
            pct_a = det_a / len(EXPECTED_ISSUES) * 100
            pct_b = det_b / len(EXPECTED_ISSUES) * 100
            pct_diff = pct_b - pct_a
            sign_pct = "+" if pct_diff > 0 else ""
            print(f"  {comp_name}: {desc}")
            print(f"    {det_a}/{len(EXPECTED_ISSUES)} ({pct_a:.0f}%) → "
                  f"{det_b}/{len(EXPECTED_ISSUES)} ({pct_b:.0f}%) "
                  f"[{sign}{diff}건, {sign_pct}{pct_diff:.0f}%p]")


def save_result(result: dict):
    """결과를 JSON 파일로 저장한다."""
    RESULTS_DIR.mkdir(exist_ok=True)
    filepath = RESULTS_DIR / f"config_{result['config']}_result.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(
            {
                "config": result["config"],
                "config_name": result["config_name"],
                "model": result["model"],
                "settings": CONFIGS[result["config"]],
                "comments": result["comments"],
                "time": result["time"],
                "detected": {
                    label: check_issue(result["comments"], info["keywords"])
                    for label, info in EXPECTED_ISSUES.items()
                },
                "detection_rate": sum(
                    1 for info in EXPECTED_ISSUES.values()
                    if check_issue(result["comments"], info["keywords"])
                ) / len(EXPECTED_ISSUES),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"  💾 결과 저장: {filepath}")


# ─── 메인 ────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="E-Commerce 대규모 종합 품질 테스트")
    parser.add_argument(
        "--config",
        nargs="*",
        choices=list(CONFIGS.keys()),
        help="실행할 설정 (기본: 전체)",
    )
    args = parser.parse_args()

    diff_text = DIFF_FILE.read_text()
    diff_result = parse_diff(diff_text)
    num_files = len(diff_result.reviewable_files)

    config_keys = args.config if args.config else list(CONFIGS.keys())

    print("🤖 E-Commerce 대규모 종합 품질 테스트")
    print(f"diff: {DIFF_FILE} ({num_files}개 파일)")
    print(f"기대 이슈: {len(EXPECTED_ISSUES)}개")
    print(f"테스트 설정: {', '.join(config_keys)} ({len(config_keys)}개)")
    print()

    for key in config_keys:
        cfg = CONFIGS[key]
        print(f"  {key}: {cfg['name']} — {cfg['model']}, "
              f"RAG={cfg['use_rag']}, Few-shot={cfg['few_shot']}, "
              f"num_ctx={cfg['num_ctx']}")

    results = []
    for i, key in enumerate(config_keys, 1):
        print(f"\n▶ 설정 {key}/{config_keys[-1]} ({i}/{len(config_keys)}): "
              f"{CONFIGS[key]['name']}...")
        result = run_config(key, diff_text)
        results.append(result)
        print_config_result(result)
        save_result(result)

    if len(results) > 1:
        print_comparison(results)
        print_category_analysis(results)
        print_difficulty_analysis(results)

    # 최고 탐지율 설정 출력
    if results:
        best = max(
            results,
            key=lambda r: sum(
                1 for info in EXPECTED_ISSUES.values()
                if check_issue(r["comments"], info["keywords"])
            ),
        )
        best_detected = sum(
            1 for info in EXPECTED_ISSUES.values()
            if check_issue(best["comments"], info["keywords"])
        )
        print(f"\n{'=' * 80}")
        print(f"  🏆 최고 탐지율: 설정 {best['config']} ({best['config_name']})")
        print(f"     {best_detected}/{len(EXPECTED_ISSUES)} "
              f"({best_detected / len(EXPECTED_ISSUES) * 100:.0f}%)")
        print(f"     모델: {best['model']} | 소요: {best['time']:.0f}s")
        print(f"{'=' * 80}")


if __name__ == "__main__":
    main()
