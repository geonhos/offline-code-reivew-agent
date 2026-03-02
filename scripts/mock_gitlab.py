"""Mock GitLab API + 리뷰 대시보드 — E2E POC용 경량 서버.

GitLab CE 없이 리뷰 에이전트의 전체 파이프라인을 검증한다.
브라우저에서 http://localhost:8929 을 열면 리뷰 결과를 실시간 확인 가능.

Usage:
    uvicorn scripts.mock_gitlab:app --host 0.0.0.0 --port 8929
"""

import logging
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [MockGitLab] %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Mock GitLab API", version="17.8.1-mock")

# ── 수집된 리뷰 코멘트 저장소 ────────────────────────────────────
posted_discussions: list[dict] = []

# ── 테스트 소스코드 (라인 번호 매핑용) ────────────────────────────
SOURCE_LINES = [
    '"""사용자 관리 모듈 - 보안 이슈 테스트용."""',
    "",
    "import hashlib",
    "import sqlite3",
    "",
    "# 하드코딩된 크레덴셜 (Critical)",
    'DB_PASSWORD = "super_secret_123"',
    'API_KEY = "sk-1234567890abcdef"',
    "",
    "def get_user(name: str) -> dict:",
    '    """사용자 정보를 조회한다."""',
    '    conn = sqlite3.connect("users.db")',
    "    cursor = conn.cursor()",
    "    # SQL Injection 취약점 (Critical)",
    '    query = f"SELECT * FROM users WHERE name = \'{name}\'"',
    "    cursor.execute(query)",
    "    result = cursor.fetchone()",
    "    conn.close()",
    '    return {"name": result[0], "email": result[1]} if result else {}',
    "",
    "def hash_password(password: str) -> str:",
    '    """비밀번호를 해시한다."""',
    "    # 약한 해시 알고리즘 (Warning)",
    "    return hashlib.md5(password.encode()).hexdigest()",
    "",
    "def process_data(data):",
    '    """데이터를 처리한다."""',
    "    try:",
    "        result = int(data) * 2",
    "        return result",
    "    except:",
    "        # bare except 절 (Warning)",
    "        return None",
    "",
    "def create_temp_file(filename):",
    '    """임시 파일을 생성한다."""',
    "    # 경로 순회 취약점 가능 (Warning)",
    '    with open(f"/tmp/{filename}", "w") as f:',
    '        f.write("temp data")',
]

TEST_DIFF = "\n".join(f"+{line}" for line in SOURCE_LINES)
TEST_DIFF = "@@ -0,0 +1,%d @@\n%s" % (len(SOURCE_LINES), TEST_DIFF)


# ── GitLab API Endpoints ─────────────────────────────────────────

@app.get("/api/v4/version")
async def version():
    return {"version": "17.8.1-mock", "revision": "mock-e2e"}


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
            }
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
  .stats { margin-left: auto; display: flex; gap: 16px; font-size: 14px; }
  .stat-item { display: flex; align-items: center; gap: 4px; }

  .container { display: flex; height: calc(100vh - 60px); }

  /* 코드 패널 */
  .code-panel { flex: 1; overflow-y: auto; border-right: 1px solid #30363d; }
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
  .summary-panel { width: 400px; overflow-y: auto; padding: 16px; }
  .summary-panel h2 { font-size: 16px; margin-bottom: 12px; color: #58a6ff; }
  .summary-card { background: #161b22; border: 1px solid #30363d; border-radius: 8px;
                  padding: 16px; margin-bottom: 12px; }
  .summary-card h3 { font-size: 14px; margin-bottom: 8px; }
  .summary-body { font-size: 13px; line-height: 1.6;
                  max-height: 500px; overflow-y: auto; }
  .summary-body p { margin-bottom: 8px; }
  .summary-body .summary-title { font-size: 15px; margin-bottom: 6px; }
  .summary-body .summary-stats { color: #8b949e; margin-bottom: 12px; }
  .review-table { width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 8px; }
  .review-table th { text-align: left; padding: 6px 8px; border-bottom: 2px solid #30363d;
                     color: #8b949e; font-weight: 600; }
  .review-table td { padding: 6px 8px; border-bottom: 1px solid #21262d; vertical-align: top; }
  .review-table tr:hover { background: #1c2128; }
  .review-table .cell-file { color: #58a6ff; font-family: 'SF Mono', Consolas, monospace; font-size: 11px; }
  .review-table .cell-line { color: #8b949e; text-align: center; }
  .review-table .cell-sev { text-align: center; }
  .pill { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; }
  .pill-critical { background: #f8514933; color: #f85149; }
  .pill-warning { background: #d2992233; color: #e3b341; }
  .pill-info { background: #58a6ff33; color: #58a6ff; }
  .spinner { display: inline-block; width: 16px; height: 16px;
             border: 2px solid #30363d; border-top-color: #58a6ff;
             border-radius: 50%; animation: spin 1s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .empty-state { text-align: center; padding: 60px 20px; color: #484f58; }
  .empty-state .spinner { width: 32px; height: 32px; margin-bottom: 16px; }
</style>
</head>
<body>

<div class="header">
  <h1>🤖 AI Code Review — E2E POC</h1>
  <span class="badge waiting" id="statusBadge">⏳ 리뷰 대기중</span>
  <div class="stats">
    <div class="stat-item">🔴 Critical: <strong id="cntCritical">0</strong></div>
    <div class="stat-item">🟡 Warning: <strong id="cntWarning">0</strong></div>
    <div class="stat-item">🔵 Info: <strong id="cntInfo">0</strong></div>
    <div class="stat-item">💬 Total: <strong id="cntTotal">0</strong></div>
  </div>
</div>

<div class="container">
  <div class="code-panel">
    <div class="file-header">📄 app/user_manager.py (new file)</div>
    <table class="code-table" id="codeTable"><tbody></tbody></table>
  </div>
  <div class="summary-panel" id="summaryPanel">
    <h2>📋 리뷰 요약</h2>
    <div class="empty-state" id="emptyState">
      <div class="spinner"></div>
      <p>AI가 코드를 분석하고 있습니다...</p>
      <p style="margin-top:8px; font-size:12px;">webhook 전송 후 30초~2분 소요</p>
    </div>
  </div>
</div>

<script>
const SOURCE = LINES_PLACEHOLDER;

// 코드 테이블 렌더링
function renderCode(comments) {
  const tbody = document.querySelector('#codeTable tbody');
  tbody.innerHTML = '';
  const commentsByLine = {};
  comments.forEach(c => {
    if (c.position && c.position.new_line) {
      if (!commentsByLine[c.position.new_line]) commentsByLine[c.position.new_line] = [];
      commentsByLine[c.position.new_line].push(c);
    }
  });

  SOURCE.forEach((line, idx) => {
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
  if (general.length === 0) return;

  document.getElementById('emptyState').style.display = 'none';
  // 기존 카드 제거
  panel.querySelectorAll('.summary-card').forEach(c => c.remove());

  general.forEach(d => {
    const card = document.createElement('div');
    card.className = 'summary-card';
    card.innerHTML = `<div class="summary-body">${formatMarkdown(d.body)}</div>`;
    panel.appendChild(card);
  });
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
    html = DASHBOARD_HTML.replace("LINES_PLACEHOLDER", json.dumps(SOURCE_LINES, ensure_ascii=False))
    return html
