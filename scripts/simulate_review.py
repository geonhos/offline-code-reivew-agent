"""GitLab 없이 전체 리뷰 파이프라인을 로컬 시뮬레이션.

실제 Ollama LLM을 호출하여 리뷰를 생성하고,
GitLab 코멘트 게시 대신 터미널에 결과를 출력한다.

Usage:
    python -m scripts.simulate_review
    python -m scripts.simulate_review --diff tests/fixtures/sample.diff
"""

import argparse
import time

from src.diff_parser import parse_diff
from src.gitlab_client import GitLabClient
from src.prompt import build_review_prompt
from src.reviewer import Reviewer, ReviewComment

# ─── 시뮬레이션용 diff 시나리오 ──────────────────────────────

SCENARIOS = {
    "security": {
        "name": "보안 이슈가 있는 Python 코드",
        "diff": """\
diff --git a/src/auth.py b/src/auth.py
index 1234567..abcdef0 100644
--- a/src/auth.py
+++ b/src/auth.py
@@ -1,5 +1,20 @@
 import os
+import sqlite3
+import hashlib

-def login(user):
-    pass
+DB_PASSWORD = "super_secret_123"
+API_KEY = "sk-1234567890abcdef"
+
+def get_user(name):
+    conn = sqlite3.connect("app.db")
+    query = f"SELECT * FROM users WHERE name = '{name}'"
+    result = conn.execute(query)
+    return result.fetchone()
+
+def verify_password(password):
+    return hashlib.md5(password.encode()).hexdigest()
+
+def process(data):
+    try:
+        return data["key"]
+    except:
+        pass
""",
    },
    "java": {
        "name": "Java 리소스 누수 + 네이밍 이슈",
        "diff": """\
diff --git a/src/main/java/com/example/UserService.java b/src/main/java/com/example/UserService.java
new file mode 100644
index 0000000..1234567
--- /dev/null
+++ b/src/main/java/com/example/UserService.java
@@ -0,0 +1,25 @@
+package com.example;
+
+import java.sql.*;
+import java.util.ArrayList;
+import java.util.List;
+
+public class UserService {
+    public List<String> Get_All_Users(String DB_URL) {
+        List<String> users = new ArrayList<>();
+        Connection conn = null;
+        try {
+            conn = DriverManager.getConnection(DB_URL);
+            Statement stmt = conn.createStatement();
+            ResultSet rs = stmt.executeQuery("SELECT * FROM users WHERE role = 'admin'");
+            while (rs.next()) {
+                users.add(rs.getString("name"));
+                System.out.println("Found user: " + rs.getString("name"));
+            }
+        } catch (Exception e) {
+            System.out.println("Error: " + e);
+            return null;
+        }
+        return users;
+    }
+}
""",
    },
    "performance": {
        "name": "Python N+1 쿼리 + 코드 품질",
        "diff": """\
diff --git a/src/report.py b/src/report.py
new file mode 100644
index 0000000..1234567
--- /dev/null
+++ b/src/report.py
@@ -0,0 +1,30 @@
+import time
+from database import db
+
+def generate_report(user_ids):
+    results = []
+    for uid in user_ids:
+        user = db.query(f"SELECT * FROM users WHERE id = {uid}")
+        orders = db.query(f"SELECT * FROM orders WHERE user_id = {uid}")
+        payments = db.query(f"SELECT * FROM payments WHERE user_id = {uid}")
+
+        total = 0
+        for o in orders:
+            total = total + o["amount"]
+        avg = total / len(orders)
+
+        results.append({
+            "user": user,
+            "order_count": len(orders),
+            "payment_count": len(payments),
+            "avg_amount": avg,
+            "generated_at": time.time()
+        })
+    return results
+
+def export_csv(data):
+    output = ""
+    for row in data:
+        output = output + str(row) + "\\n"
+    return output
""",
    },
    "clean": {
        "name": "잘 작성된 코드 (이슈 없어야 함)",
        "diff": """\
diff --git a/src/calculator.py b/src/calculator.py
new file mode 100644
index 0000000..1234567
--- /dev/null
+++ b/src/calculator.py
@@ -0,0 +1,20 @@
+\"\"\"수학 유틸리티 모듈.\"\"\"
+
+import logging
+from typing import Optional
+
+logger = logging.getLogger(__name__)
+
+
+def safe_divide(numerator: float, denominator: float) -> Optional[float]:
+    \"\"\"안전한 나눗셈. 0으로 나누면 None을 반환한다.\"\"\"
+    if denominator == 0:
+        logger.warning("0으로 나누기 시도: numerator=%s", numerator)
+        return None
+    return numerator / denominator
+
+
+def clamp(value: float, min_val: float, max_val: float) -> float:
+    \"\"\"값을 min_val ~ max_val 범위로 제한한다.\"\"\"
+    return max(min_val, min(value, max_val))
""",
    },
}


def print_header(text: str, width: int = 70):
    print(f"\n{'=' * width}")
    print(f"  {text}")
    print(f"{'=' * width}")


def print_comments(comments: list[ReviewComment]):
    """리뷰 코멘트를 터미널에 출력한다."""
    if not comments:
        print("  ✅ 이슈 없음 — 깨끗한 코드입니다.")
        return

    severity_color = {
        "critical": "\033[91m",  # red
        "warning": "\033[93m",   # yellow
        "info": "\033[94m",      # blue
    }
    reset = "\033[0m"

    for c in comments:
        color = severity_color.get(c.severity, "")
        print(f"  {color}[{c.severity.upper()}]{reset} {c.file}:L{c.line}")
        print(f"    → {c.message}")
        print()


def print_gitlab_preview(comments: list[ReviewComment]):
    """GitLab에 게시될 코멘트 미리보기."""
    if not comments:
        print("  📝 GitLab 코멘트:")
        print("  🤖 **AI 코드 리뷰 완료**")
        print("  이슈가 발견되지 않았습니다. ✅")
        return

    summary = GitLabClient._build_summary(comments)
    print("  📝 GitLab 요약 코멘트 미리보기:")
    print("  ─" * 35)
    for line in summary.split("\n"):
        print(f"  {line}")


def run_scenario(name: str, scenario: dict, reviewer: Reviewer) -> dict:
    """단일 시나리오를 실행한다."""
    print_header(f"시나리오: {scenario['name']}")

    diff_text = scenario["diff"]
    diff_result = parse_diff(diff_text)

    print(f"\n  파일 수: {len(diff_result.files)}")
    for f in diff_result.files:
        added = len(f.added_lines)
        deleted = len(f.deleted_lines)
        print(f"    📄 {f.filename} (+{added}, -{deleted})")

    print(f"\n  ⏳ Ollama에 리뷰 요청 중...")
    start = time.time()
    comments = reviewer.review(diff_text)
    elapsed = time.time() - start

    print(f"  ⏱  소요 시간: {elapsed:.1f}s")
    print(f"  📊 발견된 이슈: {len(comments)}건\n")

    print_comments(comments)
    print_gitlab_preview(comments)

    return {
        "name": name,
        "comments": len(comments),
        "time": elapsed,
        "details": comments,
    }


def run_from_file(filepath: str, reviewer: Reviewer) -> dict:
    """파일에서 diff를 읽어 리뷰한다."""
    print_header(f"파일 리뷰: {filepath}")

    with open(filepath) as f:
        diff_text = f.read()

    diff_result = parse_diff(diff_text)

    print(f"\n  전체 파일: {len(diff_result.files)}개")
    print(f"  리뷰 대상: {len(diff_result.reviewable_files)}개")
    for f in diff_result.reviewable_files:
        added = len(f.added_lines)
        deleted = len(f.deleted_lines)
        print(f"    📄 {f.filename} (+{added}, -{deleted})")

    print(f"\n  ⏳ Ollama에 리뷰 요청 중...")
    start = time.time()
    comments = reviewer.review(diff_text)
    elapsed = time.time() - start

    print(f"  ⏱  소요 시간: {elapsed:.1f}s")
    print(f"  📊 발견된 이슈: {len(comments)}건\n")

    print_comments(comments)
    print_gitlab_preview(comments)

    return {
        "name": filepath,
        "comments": len(comments),
        "time": elapsed,
        "details": comments,
    }


def print_summary(results: list[dict]):
    """전체 결과 요약을 출력한다."""
    print_header("전체 결과 요약")

    total_time = sum(r["time"] for r in results)
    total_comments = sum(r["comments"] for r in results)

    print(f"\n  {'시나리오':<30} {'이슈':>6} {'시간':>8}")
    print(f"  {'─' * 50}")
    for r in results:
        print(f"  {r['name']:<30} {r['comments']:>4}건 {r['time']:>6.1f}s")
    print(f"  {'─' * 50}")
    print(f"  {'합계':<30} {total_comments:>4}건 {total_time:>6.1f}s")
    print(f"  {'평균':<30} {total_comments/len(results):>5.1f}건 {total_time/len(results):>6.1f}s")


def main():
    parser = argparse.ArgumentParser(description="GitLab 없이 리뷰 파이프라인 시뮬레이션")
    parser.add_argument("--diff", help="리뷰할 diff 파일 경로")
    parser.add_argument(
        "--scenario",
        choices=list(SCENARIOS.keys()) + ["all"],
        default="all",
        help="실행할 시나리오 (기본: all)",
    )
    args = parser.parse_args()

    print("🤖 AI 코드 리뷰 시뮬레이션")
    print("─" * 40)

    # Retriever를 모킹 (DB 없이 실행)
    from unittest.mock import MagicMock
    mock_retriever = MagicMock()
    mock_retriever.search.return_value = []
    reviewer = Reviewer(retriever=mock_retriever)

    results = []

    if args.diff:
        result = run_from_file(args.diff, reviewer)
        results.append(result)
    elif args.scenario == "all":
        for name, scenario in SCENARIOS.items():
            result = run_scenario(name, scenario, reviewer)
            results.append(result)
    else:
        scenario = SCENARIOS[args.scenario]
        result = run_scenario(args.scenario, scenario, reviewer)
        results.append(result)

    if len(results) > 1:
        print_summary(results)


if __name__ == "__main__":
    main()
