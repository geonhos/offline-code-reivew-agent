"""FastAPI 웹훅 서버 - GitLab MR 이벤트를 수신하고 AI 리뷰를 실행한다."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

import httpx
import psycopg
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

from src.config import settings
from src.gitlab_client import GitLabClient
from src.logging_config import setup_logging
from src.review_history import ReviewHistory
from src.reviewer import Reviewer

logger = logging.getLogger(__name__)

# 리뷰 제어 라벨
LABEL_SKIP_REVIEW = "no-review"
LABEL_FORCE_REVIEW = "force-review"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작 시 로깅 설정을 초기화한다."""
    json_log = os.environ.get("REVIEW_LOG_FORMAT", "text") == "json"
    log_level = os.environ.get("REVIEW_LOG_LEVEL", "INFO")
    setup_logging(level=log_level, json_format=json_log)
    logger.info(
        "AI Code Review Agent 시작 (primary=%s, fast=%s)",
        settings.llm_model_primary, settings.llm_model_fast,
    )
    yield
    logger.info("AI Code Review Agent 종료")


app = FastAPI(
    title="AI Code Review Agent",
    description="폐쇄망 환경 AI 코드 리뷰 봇",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    """Deep 헬스체크 — Ollama 모델, DB 연결을 실제로 확인한다."""
    checks = {
        "model_primary": settings.llm_model_primary,
        "model_fast": settings.llm_model_fast,
        "embed_model": settings.embed_model,
        "context_enrichment": settings.context_enrichment_enabled,
        "review_validation": settings.review_validation_enabled,
    }

    # Ollama 연결 확인
    ollama_ok = False
    try:
        resp = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=5.0)
        resp.raise_for_status()
        models = [m.get("name", "") for m in resp.json().get("models", [])]
        ollama_ok = True
        checks["ollama_models"] = models
    except Exception as e:
        checks["ollama_error"] = str(e)

    # DB 연결 확인
    db_ok = False
    try:
        with psycopg.connect(settings.database_url) as conn:
            conn.execute("SELECT 1")
        db_ok = True
    except Exception as e:
        checks["db_error"] = str(e)

    checks["ollama"] = "ok" if ollama_ok else "error"
    checks["database"] = "ok" if db_ok else "error"

    if ollama_ok and db_ok:
        checks["status"] = "ok"
    else:
        checks["status"] = "degraded"

    return checks


@app.post("/webhook")
async def webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_gitlab_token: Optional[str] = Header(None),
):
    """GitLab MR 웹훅을 수신한다.

    GitLab에서 Merge Request 이벤트가 발생하면 이 엔드포인트로 POST 요청이 온다.
    시크릿 토큰을 검증한 후, 백그라운드에서 리뷰를 수행한다.
    """
    # 시크릿 토큰 검증
    if settings.webhook_secret:
        if x_gitlab_token != settings.webhook_secret:
            raise HTTPException(status_code=401, detail="Invalid webhook token")

    payload = await request.json()

    # MR 이벤트만 처리
    if payload.get("object_kind") != "merge_request":
        return {"status": "ignored", "reason": "not a merge_request event"}

    attrs = payload.get("object_attributes", {})
    action = attrs.get("action")

    # open, update 액션만 리뷰 (close, merge 등은 무시)
    if action not in ("open", "update", "reopen"):
        return {"status": "ignored", "reason": f"action '{action}' not reviewable"}

    project_id = payload.get("project", {}).get("id")
    mr_iid = attrs.get("iid")

    if not project_id or not mr_iid:
        raise HTTPException(status_code=400, detail="Missing project_id or mr_iid")

    # MR 라벨 기반 필터링
    mr_labels = attrs.get("labels", [])
    label_names = [lb.get("title", "") if isinstance(lb, dict) else lb for lb in mr_labels]

    if LABEL_SKIP_REVIEW in label_names:
        logger.info("리뷰 스킵 (no-review 라벨): project=%s, mr_iid=%s", project_id, mr_iid)
        return {"status": "skipped", "reason": "no-review label"}

    force_review = LABEL_FORCE_REVIEW in label_names

    logger.info("MR 리뷰 요청: project=%s, mr_iid=%s, action=%s", project_id, mr_iid, action)

    # 백그라운드에서 리뷰 실행 (웹훅 타임아웃 방지)
    background_tasks.add_task(run_review, project_id, mr_iid, force_review)

    return {
        "status": "accepted",
        "project_id": project_id,
        "mr_iid": mr_iid,
    }


def run_review(project_id: int, mr_iid: int, force: bool = False):
    """MR에 대한 AI 코드 리뷰를 실행하고 결과를 게시한다.

    BackgroundTasks에서 호출되는 동기 함수.
    """
    logger.info("리뷰 시작: project=%s, mr_iid=%s", project_id, mr_iid)
    history = ReviewHistory()

    try:
        with GitLabClient() as gitlab:
            # 0. 커밋 SHA 조회 + 중복 리뷰 방지
            commit_sha = ""
            try:
                commit_sha = gitlab.get_mr_head_sha(project_id, mr_iid)
            except Exception:
                logger.warning("커밋 SHA 조회 실패 — 중복 체크 건너뜀")

            if commit_sha and not force:
                if history.is_reviewed(project_id, mr_iid, commit_sha):
                    logger.info(
                        "이미 리뷰됨 (스킵): project=%s, mr_iid=%s, sha=%s",
                        project_id, mr_iid, commit_sha[:8],
                    )
                    return

            # 1. MR diff 조회
            diff_text = gitlab.get_mr_diff_text(project_id, mr_iid)

            if not diff_text.strip():
                logger.info("변경 사항 없음: project=%s, mr_iid=%s", project_id, mr_iid)
                if commit_sha:
                    history.save_review(project_id, mr_iid, commit_sha, "skipped", 0)
                return

            # 2. 리뷰 실행
            reviewer = Reviewer()
            comments = reviewer.review(diff_text, project_id=project_id, mr_iid=mr_iid)

            logger.info(
                "리뷰 완료: project=%s, mr_iid=%s, comments=%d",
                project_id, mr_iid, len(comments),
            )

            # 3. 결과 게시
            result = gitlab.post_review(project_id, mr_iid, comments)
            logger.info(
                "게시 완료: inline=%d, summary=%s, errors=%d",
                result["posted_inline"],
                result["posted_summary"],
                len(result["errors"]),
            )

            # 4. 이력 저장
            if commit_sha:
                history.save_review(
                    project_id, mr_iid, commit_sha, "completed", len(comments),
                )

    except Exception:
        logger.exception("리뷰 실패: project=%s, mr_iid=%s", project_id, mr_iid)
        if commit_sha:
            history.save_review(project_id, mr_iid, commit_sha, "failed", 0)
