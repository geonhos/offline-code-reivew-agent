"""Mock GitLab API + 리뷰 대시보드 — E2E POC용 경량 서버.

GitLab CE 없이 리뷰 에이전트의 전체 파이프라인을 검증한다.
브라우저에서 http://localhost:8929 을 열면 리뷰 결과를 실시간 확인 가능.

Usage:
    uvicorn scripts.mock_gitlab:app --host 0.0.0.0 --port 8929
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [MockGitLab] %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Mock GitLab API", version="17.8.1-mock")

# ── 수집된 리뷰 코멘트 저장소 ────────────────────────────────────
posted_discussions: list[dict] = []

# ── E2E 제어용 상태 ─────────────────────────────────────────────
mr_state: dict = {
    "sha": "abc123head",
    "labels": [],
}

# ── 테스트 소스코드 (라인 번호 매핑용) ────────────────────────────
SOURCE_LINES = [
    '"""주문 처리 서비스 - 결제·재고·알림 통합 모듈."""',
    "",
    "import hashlib",
    "import os",
    "import pickle",
    "import sqlite3",
    "import subprocess",
    "import xml.etree.ElementTree as ET",
    "from datetime import datetime",
    "",
    "# 하드코딩된 크레덴셜",
    'DB_PASSWORD = "super_secret_123"',
    'API_KEY = "sk-live-1234567890abcdef"',
    'STRIPE_SECRET = "sk_live_abcdefghijklmnop"',
    'JWT_SECRET = "my-jwt-secret"',
    "",
    "",
    "class OrderService:",
    '    """주문 처리 핵심 서비스."""',
    "",
    "    def __init__(self):",
    '        self.db = sqlite3.connect("orders.db")',
    "",
    "    def find_order(self, order_id: str) -> dict:",
    '        """주문을 조회한다."""',
    "        query = f\"SELECT * FROM orders WHERE id = '{order_id}'\"",
    "        cursor = self.db.execute(query)",
    "        row = cursor.fetchone()",
    "        return dict(row) if row else {}",
    "",
    "    def search_orders(self, user_input: str) -> list:",
    '        """주문을 검색한다."""',
    '        sql = "SELECT * FROM orders WHERE status = \'" + user_input + "\'"',
    "        return self.db.execute(sql).fetchall()",
    "",
    "    def process_payment(self, card_number: str, amount: float) -> dict:",
    '        """결제를 처리한다."""',
    "        # 카드 번호를 로그에 평문 저장",
    "        print(f\"Processing payment: card={card_number}, amount={amount}\")",
    "",
    "        # 약한 해시로 결제 토큰 생성",
    "        token = hashlib.md5(card_number.encode()).hexdigest()",
    "",
    "        self.db.execute(",
    '            f"INSERT INTO payments VALUES (\'{token}\', {amount}, \'{card_number}\')"',
    "        )",
    "        self.db.commit()",
    "        return {\"token\": token, \"status\": \"ok\"}",
    "",
    "    def update_inventory(self, product_id, quantity):",
    '        """재고를 업데이트한다."""',
    "        try:",
    "            self.db.execute(",
    "                f\"UPDATE products SET stock = stock - {quantity} \"",
    "                f\"WHERE id = '{product_id}'\"",
    "            )",
    "            self.db.commit()",
    "        except:",
    "            pass",
    "",
    "    def export_report(self, user_query: str) -> str:",
    '        """리포트를 생성한다."""',
    "        result = subprocess.run(",
    "            f\"echo {user_query} | generate_report\",",
    "            shell=True, capture_output=True, text=True",
    "        )",
    "        return result.stdout",
    "",
    "    def load_config(self, data: bytes) -> dict:",
    '        """설정을 로드한다."""',
    "        return pickle.loads(data)",
    "",
    "    def parse_order_xml(self, xml_string: str) -> dict:",
    '        """XML 주문 데이터를 파싱한다."""',
    "        root = ET.fromstring(xml_string)",
    "        return {",
    '            "id": root.find("id").text,',
    '            "amount": root.find("amount").text,',
    "        }",
    "",
    "    def create_temp_export(self, filename: str):",
    '        """임시 내보내기 파일을 생성한다."""',
    "        path = f\"/tmp/exports/{filename}\"",
    "        with open(path, \"w\") as f:",
    '            f.write(self.export_report("all"))',
    "",
    "    def send_notification(self, user_email, message):",
    '        """알림을 발송한다."""',
    "        os.system(f'echo \"{message}\" | mail -s \"Order Update\" {user_email}')",
    "",
    "",
    "def verify_token(token: str) -> bool:",
    '    """토큰을 검증한다."""',
    "    if token == JWT_SECRET:",
    "        return True",
    "    return False",
    "",
    "",
    "def calculate_discount(price, discount_code):",
    '    """할인을 계산한다."""',
    "    result = eval(f\"{price} * (1 - {discount_code})\")",
    "    return result",
    "",
    "",
    "def get_user_password(username: str) -> str:",
    '    """사용자 비밀번호를 반환한다."""',
    '    conn = sqlite3.connect("users.db")',
    "    row = conn.execute(",
    "        f\"SELECT password FROM users WHERE name = '{username}'\"",
    "    ).fetchone()",
    "    return row[0] if row else \"\"",
]

TEST_DIFF = "\n".join(f"+{line}" for line in SOURCE_LINES)
TEST_DIFF = "@@ -0,0 +1,%d @@\n%s" % (len(SOURCE_LINES), TEST_DIFF)

# ── 취약한 라이브러리가 포함된 requirements.txt ──────────────────
SOURCE_LINES_REQ = [
    "# 프로젝트 의존성",
    "flask==2.0.0",
    "requests==2.25.0",
    "django==3.2.0",
    "jinja2==3.1.0",
    "setuptools==65.0.0",
    "urllib3==1.26.0",
    "certifi==2022.12.7",
    "idna==3.4",
    "pydantic==2.5.0",
    "uvicorn==0.24.0",
]

TEST_DIFF_REQ = "\n".join(f"+{line}" for line in SOURCE_LINES_REQ)
TEST_DIFF_REQ = "@@ -0,0 +1,%d @@\n%s" % (len(SOURCE_LINES_REQ), TEST_DIFF_REQ)


# ── GitLab API Endpoints ─────────────────────────────────────────

@app.get("/api/v4/version")
async def version():
    return {"version": "17.8.1-mock", "revision": "mock-e2e"}


@app.get("/api/v4/projects/{project_id}/repository/files/{file_path:path}/raw")
async def get_file_raw(project_id: int, file_path: str, ref: str = "HEAD"):
    """저장소 파일 내용 반환 (Mock)."""
    from urllib.parse import unquote
    decoded = unquote(file_path)
    logger.info("GET /repository/files/%s/raw (ref=%s)", decoded, ref)
    file_map = {
        "app/user_manager.py": "\n".join(SOURCE_LINES),
        "requirements.txt": "\n".join(SOURCE_LINES_REQ),
    }
    if decoded in file_map:
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(file_map[decoded])
    from fastapi.responses import JSONResponse
    return JSONResponse({"error": "404 File Not Found"}, status_code=404)


@app.get("/api/v4/projects/{project_id}/merge_requests/{mr_iid}")
async def mr_detail(project_id: int, mr_iid: int):
    """MR 상세 정보 — sha, labels 포함."""
    logger.info("GET /merge_requests/%d (project=%d)", mr_iid, project_id)
    return {
        "id": mr_iid,
        "iid": mr_iid,
        "title": "feat: add user management module",
        "state": "opened",
        "sha": mr_state["sha"],
        "labels": mr_state["labels"],
        "source_branch": "feature/security-test",
        "target_branch": "main",
    }


@app.get("/api/v4/projects/{project_id}/merge_requests/{mr_iid}/changes")
async def mr_changes(project_id: int, mr_iid: int):
    logger.info("GET /changes (project=%d, mr=%d)", project_id, mr_iid)
    return {
        "id": mr_iid,
        "iid": mr_iid,
        "title": "feat: add user management module",
        "state": "opened",
        "changes": [
            {
                "old_path": "app/user_manager.py",
                "new_path": "app/user_manager.py",
                "new_file": True,
                "renamed_file": False,
                "deleted_file": False,
                "diff": TEST_DIFF,
            },
            {
                "old_path": "requirements.txt",
                "new_path": "requirements.txt",
                "new_file": True,
                "renamed_file": False,
                "deleted_file": False,
                "diff": TEST_DIFF_REQ,
            },
        ],
    }


@app.get("/api/v4/projects/{project_id}/merge_requests/{mr_iid}/versions")
async def mr_versions(project_id: int, mr_iid: int):
    logger.info("GET /versions (project=%d, mr=%d)", project_id, mr_iid)
    return [
        {
            "id": 1,
            "head_commit_sha": "abc123head",
            "base_commit_sha": "abc123base",
            "start_commit_sha": "abc123start",
        }
    ]


@app.post("/api/v4/projects/{project_id}/merge_requests/{mr_iid}/discussions")
async def create_discussion(project_id: int, mr_iid: int, request: Request):
    body = await request.json()
    entry = {
        "project_id": project_id,
        "mr_iid": mr_iid,
        "body": body.get("body", ""),
        "position": body.get("position"),
        "timestamp": datetime.now().isoformat(),
    }
    posted_discussions.append(entry)
    tag = "inline" if entry["position"] else "general"
    logger.info("POST comment [%s] %.100s...", tag, entry["body"].replace("\n", " "))

    return {
        "id": f"disc-{len(posted_discussions)}",
        "notes": [{"id": len(posted_discussions), "body": entry["body"]}],
    }


@app.get("/api/v4/projects/{project_id}/merge_requests/{mr_iid}/discussions")
async def list_discussions(project_id: int, mr_iid: int):
    result = []
    for i, d in enumerate(posted_discussions, 1):
        if d["project_id"] == project_id and d["mr_iid"] == mr_iid:
            note = {"id": i, "body": d["body"], "position": d["position"]}
            result.append({"id": f"disc-{i}", "notes": [note]})
    return result


# ── 결과 JSON API (스크립트용) ────────────────────────────────────

async def _load_cached_results():
    """캐시된 리뷰 결과를 3초 딜레이 후 posted_discussions에 로딩한다."""
    await asyncio.sleep(3)
    cache_path = Path(__file__).parent / "cached_review_results.json"
    if not cache_path.exists():
        logger.warning("캐시 파일 없음: %s", cache_path)
        return
    data = json.loads(cache_path.read_text())
    posted_discussions.clear()
    posted_discussions.extend(data.get("discussions", []))
    logger.info("캐시 결과 로딩 완료: %d건", len(posted_discussions))


@app.post("/_e2e/trigger")
async def e2e_trigger():
    """대시보드 버튼에서 호출 — 캐시 모드이면 즉시 반환, 아니면 실제 웹훅 전송."""
    use_cache = os.getenv("DEMO_USE_CACHE", "false").lower() == "true"

    if use_cache:
        asyncio.create_task(_load_cached_results())
        logger.info("Trigger (cached mode) — 3초 후 결과 로딩")
        return {"status": "triggered", "mode": "cached"}

    review_url = os.getenv("REVIEW_SERVICE_URL", "http://localhost:8000")
    webhook_secret = os.getenv("REVIEW_WEBHOOK_SECRET", "poc-secret")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{review_url}/webhook",
            json={
                "object_kind": "merge_request",
                "project": {"id": 1},
                "object_attributes": {
                    "iid": 1,
                    "action": "open",
                    "title": "feat: add user management module",
                    "source_branch": "feature/security-test",
                    "target_branch": "main",
                    "labels": [{"title": lb} for lb in mr_state.get("labels", [])],
                },
            },
            headers={
                "Content-Type": "application/json",
                "X-Gitlab-Token": webhook_secret,
            },
            timeout=10,
        )
    logger.info("Trigger webhook → %s", resp.json())
    return {"status": "triggered", "review_response": resp.json()}


@app.delete("/_e2e/reset")
async def e2e_reset():
    """결과를 초기화한다."""
    posted_discussions.clear()
    return {"status": "reset"}


@app.post("/_e2e/set_mr_state")
async def e2e_set_mr_state(request: Request):
    """MR 상태(sha, labels)를 제어한다."""
    body = await request.json()
    if "sha" in body:
        mr_state["sha"] = body["sha"]
    if "labels" in body:
        mr_state["labels"] = body["labels"]
    logger.info("MR 상태 변경: sha=%s, labels=%s", mr_state["sha"], mr_state["labels"])
    return {"status": "updated", "mr_state": mr_state}


@app.get("/_e2e/results")
async def e2e_results():
    inline = [d for d in posted_discussions if d["position"]]
    general = [d for d in posted_discussions if not d["position"]]
    return {
        "total": len(posted_discussions),
        "inline_count": len(inline),
        "general_count": len(general),
        "summary_found": any("AI 코드 리뷰 완료" in d["body"] for d in posted_discussions),
        "discussions": posted_discussions,
    }


# ── 브라우저 대시보드 ────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>AI Code Review — E2E POC</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0d1117; color: #c9d1d9; }

  .header { background: #161b22; border-bottom: 1px solid #30363d; padding: 16px 24px;
            display: flex; align-items: center; gap: 16px; }
  .header h1 { font-size: 20px; color: #58a6ff; }
  .badge { padding: 4px 12px; border-radius: 12px; font-size: 12px; font-weight: 600; }
  .badge.waiting { background: #f0883e33; color: #f0883e; }
  .badge.done { background: #3fb95033; color: #3fb950; }
  .badge.dual { background: #a371f733; color: #a371f7; margin-left: 4px; }
  .pipeline-bar { background: #0d1117; border-bottom: 1px solid #21262d; padding: 8px 24px;
                  display: flex; align-items: center; gap: 8px; font-size: 12px; color: #484f58; }
  .pipeline-step { padding: 3px 10px; border-radius: 4px; background: #21262d; color: #8b949e; }
  .pipeline-step.active { background: #1f6feb33; color: #58a6ff; }
  .pipeline-step.done { background: #23862233; color: #3fb950; }
  .pipeline-arrow { color: #30363d; }
  .stats { margin-left: auto; display: flex; gap: 16px; font-size: 14px; }
  .stat-item { display: flex; align-items: center; gap: 4px; }

  .container { display: flex; height: calc(100vh - 60px); }

  /* 코드 패널 */
  .code-panel { width: 420px; min-width: 360px; overflow-y: auto; border-right: 1px solid #30363d; }
  .file-header { background: #161b22; padding: 8px 16px; font-size: 13px;
                 color: #8b949e; border-bottom: 1px solid #30363d;
                 position: sticky; top: 0; z-index: 10; }
  .code-table { width: 100%; border-collapse: collapse; font-size: 13px; }
  .code-table td { padding: 0 12px; white-space: pre; font-family: 'SF Mono', Consolas, monospace;
                   line-height: 22px; vertical-align: top; }
  .line-num { color: #484f58; text-align: right; width: 50px; user-select: none;
              border-right: 1px solid #30363d; }
  .line-add { background: #12261e; }
  .line-add .line-num { background: #0c2215; }
  .line-comment { background: #1c1f24; border-top: 2px solid #f85149; }
  .comment-badge { display: inline-block; padding: 2px 8px; border-radius: 4px;
                   font-size: 11px; font-weight: 600; margin-right: 6px; }
  .sev-critical { background: #f8514933; color: #f85149; }
  .sev-warning { background: #d29922aa; color: #e3b341; }
  .sev-info { background: #58a6ff33; color: #58a6ff; }
  .comment-text { color: #c9d1d9; font-family: -apple-system, sans-serif;
                  font-size: 13px; padding: 8px 12px; white-space: pre-wrap; }

  /* 요약 패널 */
  .summary-panel { flex: 1; overflow-y: auto; padding: 16px; }
  .summary-panel h2 { font-size: 16px; margin-bottom: 12px; color: #58a6ff; }
  .summary-card { background: #161b22; border: 1px solid #30363d; border-radius: 8px;
                  padding: 16px; margin-bottom: 12px; }
  .summary-card h3 { font-size: 14px; margin-bottom: 8px; }
  .summary-body { font-size: 15px; line-height: 1.7;
                  max-height: 600px; overflow-y: auto; }
  .summary-body p { margin-bottom: 10px; }
  .summary-body .summary-title { font-size: 17px; margin-bottom: 8px; }
  .summary-body .summary-stats { color: #8b949e; margin-bottom: 14px; }
  .review-table { width: 100%; border-collapse: collapse; font-size: 14px; margin-top: 10px; }
  .review-table th { text-align: left; padding: 8px 10px; border-bottom: 2px solid #30363d;
                     color: #8b949e; font-weight: 600; }
  .review-table td { padding: 8px 10px; border-bottom: 1px solid #21262d; vertical-align: top; }
  .review-table tr:hover { background: #1c2128; }
  .review-table .cell-file { color: #58a6ff; font-family: 'SF Mono', Consolas, monospace; font-size: 13px; }
  .review-table .cell-line { color: #8b949e; text-align: center; }
  .review-table .cell-sev { text-align: center; }
  .pill { display: inline-block; padding: 3px 10px; border-radius: 10px; font-size: 13px; font-weight: 600; }
  .pill-critical { background: #f8514933; color: #f85149; }
  .pill-warning { background: #d2992233; color: #e3b341; }
  .pill-info { background: #58a6ff33; color: #58a6ff; }
  .spinner { display: inline-block; width: 16px; height: 16px;
             border: 2px solid #30363d; border-top-color: #58a6ff;
             border-radius: 50%; animation: spin 1s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .empty-state { text-align: center; padding: 60px 20px; color: #484f58; }
  .empty-state .spinner { width: 32px; height: 32px; margin-bottom: 16px; }

  /* 파일 탭 */
  .file-tabs { display: flex; background: #161b22; border-bottom: 1px solid #30363d;
               position: sticky; top: 0; z-index: 11; }
  .file-tab { padding: 8px 16px; font-size: 13px; color: #8b949e; cursor: pointer;
              border-bottom: 2px solid transparent; transition: all 0.15s; }
  .file-tab:hover { color: #c9d1d9; background: #1c2128; }
  .file-tab.active { color: #58a6ff; border-bottom-color: #58a6ff; }

  /* CVE 섹션 */
  .cve-section { margin-top: 16px; }
  .cve-card { background: #1c1215; border: 1px solid #f8514944; border-radius: 8px;
              padding: 14px 18px; margin-bottom: 10px; }
  .cve-card h4 { font-size: 15px; color: #f85149; margin-bottom: 6px; }
  .cve-card .cve-pkg { color: #58a6ff; font-family: 'SF Mono', Consolas, monospace; font-size: 14px; }
  .cve-card .cve-desc { color: #8b949e; font-size: 14px; margin-top: 6px; line-height: 1.6; }
  .cve-card .cve-fix { color: #3fb950; font-size: 14px; margin-top: 6px; }
  .cve-header { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }
  .cve-header h3 { font-size: 17px; color: #f85149; }
  .cve-count { background: #f8514933; color: #f85149; padding: 2px 8px;
               border-radius: 10px; font-size: 12px; font-weight: 600; }
</style>
</head>
<body>

<div class="header">
  <h1>🤖 AI Code Review — E2E POC</h1>
  <span class="badge waiting" id="statusBadge">⏳ 리뷰 대기중</span>
  <span class="badge dual">🧠 Dual Model: 14b + 7b</span>
  <button id="btnReview" onclick="requestReview()" style="
    background: #238636; color: #fff; border: 1px solid #2ea043; border-radius: 6px;
    padding: 8px 20px; font-size: 14px; font-weight: 600; cursor: pointer;
    transition: background 0.15s;">🚀 코드 리뷰 요청</button>
  <div class="stats">
    <div class="stat-item">🔴 Critical: <strong id="cntCritical">0</strong></div>
    <div class="stat-item">🟡 Warning: <strong id="cntWarning">0</strong></div>
    <div class="stat-item">🔵 Info: <strong id="cntInfo">0</strong></div>
    <div class="stat-item">💬 Total: <strong id="cntTotal">0</strong></div>
  </div>
</div>

<div class="pipeline-bar" id="pipelineBar">
  <span>파이프라인:</span>
  <span class="pipeline-step" id="stepDiff">📄 Diff 파싱</span>
  <span class="pipeline-arrow">→</span>
  <span class="pipeline-step" id="stepCtx">🔍 컨텍스트 수집</span>
  <span class="pipeline-arrow">→</span>
  <span class="pipeline-step" id="stepReview">🤖 AI 리뷰 (14b)</span>
  <span class="pipeline-arrow">→</span>
  <span class="pipeline-step" id="stepValidate">✅ 검증 (7b)</span>
  <span class="pipeline-arrow">→</span>
  <span class="pipeline-step" id="stepPost">📝 게시</span>
</div>
<div class="container">
  <div class="code-panel">
    <div class="file-tabs">
      <div class="file-tab active" onclick="switchFile('code')" id="tabCode">📄 app/user_manager.py</div>
      <div class="file-tab" onclick="switchFile('req')" id="tabReq">📦 requirements.txt</div>
    </div>
    <table class="code-table" id="codeTable"><tbody></tbody></table>
    <table class="code-table" id="reqTable" style="display:none"><tbody></tbody></table>
  </div>
  <div class="summary-panel" id="summaryPanel">
    <h2>📋 리뷰 요약</h2>
    <div class="empty-state" id="emptyState">
      <p style="font-size:15px;">🔍 코드 리뷰를 요청해주세요</p>
      <p style="margin-top:8px; font-size:12px;">상단의 "코드 리뷰 요청" 버튼을 클릭하세요</p>
    </div>
    <div class="empty-state" id="analyzingState" style="display:none;">
      <div class="spinner"></div>
      <p>AI가 코드를 분석하고 있습니다...</p>
    </div>
  </div>
</div>

<script>
const SOURCE = LINES_PLACEHOLDER;
const SOURCE_REQ = REQ_LINES_PLACEHOLDER;

// 파일 탭 전환
function switchFile(tab) {
  document.getElementById('tabCode').className = 'file-tab' + (tab==='code' ? ' active' : '');
  document.getElementById('tabReq').className = 'file-tab' + (tab==='req' ? ' active' : '');
  document.getElementById('codeTable').style.display = tab==='code' ? '' : 'none';
  document.getElementById('reqTable').style.display = tab==='req' ? '' : 'none';
}

// 코드 테이블 렌더링
function renderCode(comments) {
  _renderFileTable('codeTable', SOURCE, comments, 'app/user_manager.py');
  _renderFileTable('reqTable', SOURCE_REQ, comments, 'requirements.txt');
}

function _renderFileTable(tableId, lines, comments, filepath) {
  const tbody = document.querySelector('#' + tableId + ' tbody');
  tbody.innerHTML = '';
  const commentsByLine = {};
  comments.forEach(c => {
    if (c.position && c.position.new_line && c.position.new_path === filepath) {
      if (!commentsByLine[c.position.new_line]) commentsByLine[c.position.new_line] = [];
      commentsByLine[c.position.new_line].push(c);
    }
  });

  lines.forEach((line, idx) => {
    const lineNum = idx + 1;
    const tr = document.createElement('tr');
    tr.className = 'line-add';
    tr.innerHTML = `<td class="line-num">${lineNum}</td><td>+${escapeHtml(line)}</td>`;
    tbody.appendChild(tr);

    if (commentsByLine[lineNum]) {
      commentsByLine[lineNum].forEach(c => {
        const cr = document.createElement('tr');
        cr.className = 'line-comment';
        const sev = detectSeverity(c.body);
        cr.innerHTML = `<td class="line-num" style="background:#1c1f24">💬</td>
          <td class="comment-text"><span class="comment-badge sev-${sev}">${sev.toUpperCase()}</span>${escapeHtml(c.body.replace(/^[🔴🟡🔵⚪]\\s*\\*\\*\\[\\w+\\]\\*\\*\\s*/, ''))}</td>`;
        tbody.appendChild(cr);
      });
    }
  });
}

function detectSeverity(body) {
  if (body.includes('CRITICAL') || body.includes('🔴')) return 'critical';
  if (body.includes('WARNING') || body.includes('🟡')) return 'warning';
  return 'info';
}

function escapeHtml(t) {
  return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// 요약 패널 렌더링
function renderSummary(discussions) {
  const panel = document.getElementById('summaryPanel');
  const general = discussions.filter(d => !d.position);
  if (general.length === 0 && !discussions.some(d => isCveComment(d.body))) return;

  document.getElementById('emptyState').style.display = 'none';
  document.getElementById('analyzingState').style.display = 'none';
  // 기존 카드 제거
  panel.querySelectorAll('.summary-card').forEach(c => c.remove());
  panel.querySelectorAll('.cve-section').forEach(c => c.remove());

  general.forEach(d => {
    const card = document.createElement('div');
    card.className = 'summary-card';
    card.innerHTML = `<div class="summary-body">${formatMarkdown(d.body)}</div>`;
    panel.appendChild(card);
  });

  // CVE 취약점 섹션
  const cveComments = discussions.filter(d => isCveComment(d.body));
  if (cveComments.length > 0) {
    const section = document.createElement('div');
    section.className = 'cve-section';
    section.innerHTML = `<div class="cve-header"><h3>🛡️ 보안 취약점</h3><span class="cve-count">${cveComments.length}건</span></div>`;
    cveComments.forEach(d => {
      const card = document.createElement('div');
      card.className = 'cve-card';
      const cveMatch = d.body.match(/CVE-[0-9-]+/);
      const cveId = cveMatch ? cveMatch[0] : '';
      const pkgMatch = d.body.match(/— ([^.]+)\\./);
      const pkg = pkgMatch ? pkgMatch[1] : '';
      const fixMatch = d.body.match(/업그레이드하세요|대체 라이브러리/);
      const sev = detectSeverity(d.body);
      card.innerHTML = `
        <h4><span class="comment-badge sev-${sev}">${sev.toUpperCase()}</span> ${cveId}</h4>
        <div class="cve-pkg">${escapeHtml(pkg)}</div>
        <div class="cve-desc">${escapeHtml(d.body.replace(/^.*?— /, '').split('. ').slice(1,2).join('. '))}</div>
        <div class="cve-fix">${fixMatch ? '✅ ' + escapeHtml(d.body.split('. ').pop()) : ''}</div>`;
      section.appendChild(card);
    });
    panel.appendChild(section);
  }
}

function isCveComment(body) {
  return body.includes('CVE-') && body.includes('취약점');
}

function formatMarkdown(text) {
  const lines = text.split('\\n');
  let html = '';
  let inTable = false;
  let tableRows = [];

  for (const line of lines) {
    // 구분선 |---|---| 스킵
    if (/^\\|[-\\s|]+\\|$/.test(line.trim())) continue;

    // 테이블 행
    if (line.trim().startsWith('|') && line.trim().endsWith('|')) {
      const cells = line.trim().slice(1, -1).split('|').map(c => c.trim());
      if (!inTable) {
        inTable = true;
        // 첫 행 = 헤더
        html += '<table class="review-table"><thead><tr>';
        cells.forEach(c => { html += `<th>${fmtInline(c)}</th>`; });
        html += '</tr></thead><tbody>';
      } else {
        html += '<tr>';
        cells.forEach((c, i) => {
          if (i === 0) html += `<td class="cell-file">${fmtInline(c)}</td>`;
          else if (i === 1) html += `<td class="cell-line">${fmtInline(c)}</td>`;
          else if (i === 2) html += `<td class="cell-sev">${fmtSeverityPill(c)}</td>`;
          else html += `<td>${fmtInline(c)}</td>`;
        });
        html += '</tr>';
      }
      continue;
    }

    // 테이블 종료
    if (inTable) { html += '</tbody></table>'; inTable = false; }

    // 빈 줄
    if (!line.trim()) { html += '<br>'; continue; }

    // 일반 텍스트
    html += `<p>${fmtInline(escapeHtml(line))}</p>`;
  }
  if (inTable) html += '</tbody></table>';
  return html;
}

function fmtInline(t) {
  return t
    .replace(/`([^`]+)`/g, '<code style="background:#21262d;padding:1px 5px;border-radius:3px;font-size:11px">$1</code>')
    .replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>');
}

function fmtSeverityPill(t) {
  const raw = t.replace(/[🔴🟡🔵⚪]/g, '').trim().toLowerCase();
  let cls = 'pill-info';
  if (raw.includes('critical')) cls = 'pill-critical';
  else if (raw.includes('warning')) cls = 'pill-warning';
  const label = raw.charAt(0).toUpperCase() + raw.slice(1);
  return `<span class="pill ${cls}">${label}</span>`;
}

// 카운터 업데이트
function updateCounts(discussions) {
  let critical=0, warning=0, info=0;
  discussions.forEach(d => {
    const s = detectSeverity(d.body);
    if (s==='critical') critical++;
    else if (s==='warning') warning++;
    else info++;
  });
  document.getElementById('cntCritical').textContent = critical;
  document.getElementById('cntWarning').textContent = warning;
  document.getElementById('cntInfo').textContent = info;
  document.getElementById('cntTotal').textContent = discussions.length;

  if (discussions.length > 0) {
    const badge = document.getElementById('statusBadge');
    badge.textContent = '✅ 리뷰 완료';
    badge.className = 'badge done';
    const btn = document.getElementById('btnReview');
    btn.innerHTML = '✅ 리뷰 완료';
    btn.style.background = '#238636';
    btn.style.borderColor = '#2ea043';
    setPipelineDone();
  }
}

// 폴링
let lastCount = 0;
async function poll() {
  try {
    const resp = await fetch('/_e2e/results');
    const data = await resp.json();
    if (data.total !== lastCount) {
      lastCount = data.total;
      renderCode(data.discussions);
      renderSummary(data.discussions);
      updateCounts(data.discussions);
    }
  } catch(e) {}
}

// 파이프라인 단계 애니메이션
function setPipelineStep(stepId) {
  ['stepDiff','stepCtx','stepReview','stepValidate','stepPost'].forEach(id => {
    const el = document.getElementById(id);
    el.className = 'pipeline-step';
  });
  if (stepId) document.getElementById(stepId).className = 'pipeline-step active';
}
function setPipelineDone() {
  ['stepDiff','stepCtx','stepReview','stepValidate','stepPost'].forEach(id => {
    document.getElementById(id).className = 'pipeline-step done';
  });
}

// 코드 리뷰 요청 버튼
async function requestReview() {
  const btn = document.getElementById('btnReview');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner" style="width:14px;height:14px;border-width:2px;vertical-align:middle;margin-right:6px"></span>리뷰 진행중...';
  btn.style.background = '#1f6feb';
  btn.style.borderColor = '#388bfd';
  document.getElementById('emptyState').style.display = 'none';
  document.getElementById('analyzingState').style.display = '';

  // 파이프라인 애니메이션
  setPipelineStep('stepDiff');
  setTimeout(() => setPipelineStep('stepCtx'), 1500);
  setTimeout(() => setPipelineStep('stepReview'), 3000);
  setTimeout(() => setPipelineStep('stepValidate'), 6000);
  setTimeout(() => setPipelineStep('stepPost'), 8000);

  const resp = await fetch('/_e2e/trigger', { method: 'POST' });
  const badge = document.getElementById('statusBadge');
  badge.textContent = '🔍 AI 분석중';
  badge.style.background = '#58a6ff33';
  badge.style.color = '#58a6ff';
}

// 초기 렌더링 + 2초 폴링
renderCode([]);
setInterval(poll, 2000);
poll();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    import json
    html = DASHBOARD_HTML.replace("REQ_LINES_PLACEHOLDER", json.dumps(SOURCE_LINES_REQ, ensure_ascii=False))
    html = html.replace("LINES_PLACEHOLDER", json.dumps(SOURCE_LINES, ensure_ascii=False))
    return html
