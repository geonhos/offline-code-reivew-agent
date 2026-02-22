"""Few-shot 적용 전/후 리뷰 품질 비교 테스트.

Usage:
    python -m scripts.compare_fewshot
"""

import json
import re
import time

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

# ─── 테스트 케이스 정의 ─────────────────────────────────────

TEST_CASES = [
    {
        "name": "Case 1: Python 보안 이슈",
        "description": "하드코딩 비밀번호 + SQL 인젝션 + 빈 except",
        "diff": """\
diff --git a/src/auth.py b/src/auth.py
index 1234567..abcdef0 100644
--- a/src/auth.py
+++ b/src/auth.py
@@ -1,8 +1,15 @@
 import os
+import sqlite3

-def login(user):
-    pass
+DB_PASSWORD = "super_secret_123"
+
+def get_user(name):
+    conn = sqlite3.connect("app.db")
+    result = conn.execute(f"SELECT * FROM users WHERE name = '{name}'")
+    return result.fetchone()
+
+def process(data):
+    try:
+        return data["key"]
+    except:
+        pass
""",
        "expected_issues": {
            "하드코딩 비밀번호": ["password", "하드코딩", "환경 변수", "secret"],
            "SQL 인젝션": ["sql", "injection", "인젝션", "f-string", "파라미터"],
            "빈 except 절": ["except", "예외"],
        },
    },
    {
        "name": "Case 2: Java 네이밍 + 리소스 누수",
        "description": "네이밍 규칙 위반 + 리소스 미정리 + System.out.println",
        "diff": """\
diff --git a/src/main/java/com/example/DataProcessor.java b/src/main/java/com/example/DataProcessor.java
new file mode 100644
index 0000000..1234567
--- /dev/null
+++ b/src/main/java/com/example/DataProcessor.java
@@ -0,0 +1,20 @@
+package com.example;
+
+import java.sql.*;
+
+public class DataProcessor {
+    public String process_data(String Input) {
+        String x = Input.trim();
+        Connection Conn = null;
+        try {
+            Conn = DriverManager.getConnection("jdbc:mysql://localhost/db");
+            Statement stmt = Conn.createStatement();
+            ResultSet rs = stmt.executeQuery("SELECT * FROM data WHERE val = " + x);
+            System.out.println("Result: " + rs.getString(1));
+            return rs.getString(1);
+        } catch (Exception e) {
+            System.out.println("Error: " + e);
+            return null;
+        }
+    }
+}
""",
        "expected_issues": {
            "메서드명 camelCase 위반": ["snake_case", "camelcase", "메서드명", "process_data", "네이밍"],
            "리소스 미정리": ["close", "try-with-resources", "리소스", "connection"],
            "System.out.println 사용": ["system.out", "logger", "로거", "로깅"],
            "SQL 인젝션": ["sql", "injection", "인젝션", "preparedstatement", "파라미터"],
        },
    },
    {
        "name": "Case 3: Python 성능 + 코드 품질",
        "description": "N+1 쿼리 패턴 + 타입 힌트 누락 + 긴 함수",
        "diff": """\
diff --git a/src/service.py b/src/service.py
index 1234567..abcdef0 100644
--- a/src/service.py
+++ b/src/service.py
@@ -1,5 +1,25 @@
+import time
+from database import db
+
+def get_all_user_profiles(user_ids):
+    results = []
+    for uid in user_ids:
+        user = db.query(f"SELECT * FROM users WHERE id = {uid}")
+        profile = db.query(f"SELECT * FROM profiles WHERE user_id = {uid}")
+        orders = db.query(f"SELECT * FROM orders WHERE user_id = {uid}")
+        total = 0
+        for o in orders:
+            total = total + o["amount"]
+        avg = total / len(orders)
+        results.append({
+            "user": user,
+            "profile": profile,
+            "order_count": len(orders),
+            "avg_amount": avg,
+            "fetched_at": time.time()
+        })
+    return results
""",
        "expected_issues": {
            "N+1 쿼리": ["n+1", "반복문", "루프", "쿼리", "join", "한 번"],
            "타입 힌트 누락": ["타입", "type hint", "반환", "파라미터"],
            "0 나누기 위험": ["division", "zero", "0", "나누기", "len"],
        },
    },
    {
        "name": "Case 4: 클린코드 (이슈 없는 코드)",
        "description": "잘 작성된 코드 — 불필요한 지적이 없는지 확인",
        "diff": """\
diff --git a/src/calculator.py b/src/calculator.py
new file mode 100644
index 0000000..1234567
--- /dev/null
+++ b/src/calculator.py
@@ -0,0 +1,15 @@
+\"\"\"계산 유틸리티 모듈.\"\"\"
+
+import logging
+
+logger = logging.getLogger(__name__)
+
+
+def safe_divide(numerator: float, denominator: float) -> float:
+    \"\"\"안전한 나눗셈. 0으로 나누면 0.0을 반환한다.\"\"\"
+    if denominator == 0:
+        logger.warning("0으로 나누기 시도: numerator=%s", numerator)
+        return 0.0
+    return numerator / denominator
""",
        "expected_issues": {},  # 이슈가 없어야 함
    },
]


def call_llm(system_prompt: str, user_prompt: str) -> str:
    resp = httpx.post(
        f"{settings.ollama_base_url}/api/generate",
        json={
            "model": settings.llm_model,
            "system": system_prompt,
            "prompt": user_prompt,
            "stream": False,
            "options": {"temperature": 0.1, "num_ctx": 8192},
        },
        timeout=300.0,
    )
    resp.raise_for_status()
    return resp.json()["response"]


def try_parse(resp: str) -> list[dict] | None:
    match = re.search(r"\[.*]", resp, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


def check_issue(comments: list[dict], keywords: list[str]) -> bool:
    """코멘트 목록에서 키워드 중 하나라도 포함되면 검출로 판정."""
    text = json.dumps(comments, ensure_ascii=False).lower()
    return any(kw.lower() in text for kw in keywords)


def run_single_case(case: dict) -> dict:
    """단일 테스트 케이스 실행."""
    diff_result = parse_diff(case["diff"])
    file_diff = diff_result.reviewable_files[0]

    guidelines_text = format_guidelines([])
    diff_content = format_diff(file_diff)
    user_prompt = REVIEW_PROMPT_TEMPLATE.format(
        guidelines=guidelines_text,
        filename=file_diff.filename,
        diff_content=diff_content,
    )

    # Without Few-shot
    start = time.time()
    resp_without = call_llm(SYSTEM_PROMPT, user_prompt)
    time_without = time.time() - start

    # With Few-shot
    start = time.time()
    resp_with = call_llm(SYSTEM_PROMPT + "\n" + FEW_SHOT_EXAMPLES, user_prompt)
    time_with = time.time() - start

    return {
        "name": case["name"],
        "description": case["description"],
        "expected_issues": case["expected_issues"],
        "without": {"response": resp_without, "parsed": try_parse(resp_without), "time": time_without},
        "with": {"response": resp_with, "parsed": try_parse(resp_with), "time": time_with},
    }


def print_case_result(result: dict):
    name = result["name"]
    desc = result["description"]
    without = result["without"]
    with_ = result["with"]

    print(f"\n{'=' * 70}")
    print(f"  {name}")
    print(f"  {desc}")
    print(f"{'=' * 70}")

    # 응답 출력
    for label, data in [("WITHOUT FEW-SHOT", without), ("WITH FEW-SHOT", with_)]:
        print(f"\n--- {label} (⏱ {data['time']:.1f}s) ---")
        if data["parsed"] is not None:
            for c in data["parsed"]:
                sev = c.get("severity", "?")
                line = c.get("line", "?")
                msg = c.get("message", "")[:100]
                print(f"  [{sev}] L{line}: {msg}")
            if not data["parsed"]:
                print("  (이슈 없음 — 빈 배열)")
        else:
            print("  ❌ JSON 파싱 실패")
            print(f"  Raw: {data['response'][:200]}")

    # 기대 이슈 검출 여부
    expected = result["expected_issues"]
    if expected:
        print(f"\n  {'기대 이슈':<30} {'Without':>10} {'With':>10}")
        print(f"  {'─' * 50}")
        for label, keywords in expected.items():
            found_without = check_issue(without["parsed"] or [], keywords)
            found_with = check_issue(with_["parsed"] or [], keywords)
            print(f"  {label:<30} {'✅' if found_without else '❌':>10} {'✅' if found_with else '❌':>10}")
    else:
        # 클린 코드 케이스: 불필요한 지적이 없는지 확인
        n_without = len(without["parsed"]) if without["parsed"] else 0
        n_with = len(with_["parsed"]) if with_["parsed"] else 0
        print(f"\n  클린 코드 테스트 (이슈 0개가 이상적)")
        print(f"  Without: {n_without}개 코멘트, With: {n_with}개 코멘트")


def print_summary(results: list[dict]):
    print(f"\n{'=' * 70}")
    print("  종합 결과")
    print(f"{'=' * 70}")

    total_without_time = sum(r["without"]["time"] for r in results)
    total_with_time = sum(r["with"]["time"] for r in results)

    # JSON 파싱 성공률
    parse_without = sum(1 for r in results if r["without"]["parsed"] is not None)
    parse_with = sum(1 for r in results if r["with"]["parsed"] is not None)

    # 기대 이슈 검출률
    total_expected = 0
    detected_without = 0
    detected_with = 0
    for r in results:
        for label, keywords in r["expected_issues"].items():
            total_expected += 1
            if check_issue(r["without"]["parsed"] or [], keywords):
                detected_without += 1
            if check_issue(r["with"]["parsed"] or [], keywords):
                detected_with += 1

    n = len(results)
    print(f"\n  {'':>25} {'Without':>12} {'With':>12}")
    print(f"  {'─' * 50}")
    print(f"  {'JSON 파싱 성공':<25} {f'{parse_without}/{n}':>12} {f'{parse_with}/{n}':>12}")
    print(f"  {'이슈 검출률':<25} {f'{detected_without}/{total_expected}':>12} {f'{detected_with}/{total_expected}':>12}")
    print(f"  {'총 소요 시간':<25} {f'{total_without_time:.1f}s':>12} {f'{total_with_time:.1f}s':>12}")
    print(f"  {'평균 소요 시간':<25} {f'{total_without_time/n:.1f}s':>12} {f'{total_with_time/n:.1f}s':>12}")


def main():
    print(f"모델: {settings.llm_model}")
    print(f"테스트 케이스: {len(TEST_CASES)}개\n")

    results = []
    for i, case in enumerate(TEST_CASES, 1):
        print(f"\n▶ Running {i}/{len(TEST_CASES)}: {case['name']}...")
        result = run_single_case(case)
        results.append(result)
        print_case_result(result)

    print_summary(results)


if __name__ == "__main__":
    main()
