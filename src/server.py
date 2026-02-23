"""FastAPI 웹훅 서버 - GitLab MR 이벤트를 수신하고 AI 리뷰를 실행한다."""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

from src.config import settings
from src.gitlab_client import GitLabClient
from src.logging_config import setup_logging
from src.reviewer import Reviewer

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작 시 로깅 설정을 초기화한다."""
    json_log = os.environ.get("REVIEW_LOG_FORMAT", "text") == "json"
    log_level = os.environ.get("REVIEW_LOG_LEVEL", "INFO")
    setup_logging(level=log_level, json_format=json_log)
    logger.info("AI Code Review Agent 시작 (model=%s)", settings.llm_model)
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
    """헬스체크 엔드포인트."""
    return {
        "status": "ok",
        "model": settings.llm_model,
        "embed_model": settings.embed_model,
    }


@app.post("/webhook")
async def webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_gitlab_token: str | None = Header(None),
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

    logger.info("MR 리뷰 요청: project=%s, mr_iid=%s, action=%s", project_id, mr_iid, action)

    # 백그라운드에서 리뷰 실행 (웹훅 타임아웃 방지)
    background_tasks.add_task(run_review, project_id, mr_iid)

    return {
        "status": "accepted",
        "project_id": project_id,
        "mr_iid": mr_iid,
    }


def run_review(project_id: int, mr_iid: int):
    """MR에 대한 AI 코드 리뷰를 실행하고 결과를 게시한다.

    BackgroundTasks에서 호출되는 동기 함수.
    """
    logger.info("리뷰 시작: project=%s, mr_iid=%s", project_id, mr_iid)

    try:
        gitlab = GitLabClient()

        # 1. MR diff 조회
        diff_text = gitlab.get_mr_diff_text(project_id, mr_iid)

        if not diff_text.strip():
            logger.info("변경 사항 없음: project=%s, mr_iid=%s", project_id, mr_iid)
            return

        # 2. 리뷰 실행
        reviewer = Reviewer()
        comments = reviewer.review(diff_text)

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

    except Exception:
        logger.exception("리뷰 실패: project=%s, mr_iid=%s", project_id, mr_iid)
