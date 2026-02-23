"""GitLab API v4 클라이언트 - MR diff 조회 및 코멘트 게시."""

import logging

import httpx

from src.config import settings

logger = logging.getLogger(__name__)


class GitLabClient:
    """GitLab Self-hosted API v4 클라이언트."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
    ):
        self._base_url = (base_url or settings.gitlab_url).rstrip("/")
        self._token = token or settings.gitlab_token
        self._client = httpx.Client(
            base_url=f"{self._base_url}/api/v4",
            headers={"PRIVATE-TOKEN": self._token},
            timeout=30.0,
        )

    # ── MR diff 조회 ──────────────────────────────────────────

    def get_mr_changes(self, project_id: int, mr_iid: int) -> dict:
        """MR의 변경 사항(diff)을 조회한다.

        Returns:
            GitLab MR changes API 응답 전체 dict.
            changes 키에 파일별 diff가 포함된다.
        """
        resp = self._client.get(
            f"/projects/{project_id}/merge_requests/{mr_iid}/changes",
        )
        resp.raise_for_status()
        return resp.json()

    def get_mr_diff_text(self, project_id: int, mr_iid: int) -> str:
        """MR의 변경 사항을 unified diff 텍스트로 변환한다.

        GitLab API는 파일별로 diff를 반환하므로,
        파서가 처리할 수 있는 unified diff 형식으로 조합한다.
        """
        data = self.get_mr_changes(project_id, mr_iid)
        changes = data.get("changes", [])

        diff_parts: list[str] = []
        for change in changes:
            old_path = change.get("old_path", "")
            new_path = change.get("new_path", "")
            diff = change.get("diff", "")

            if not diff:
                continue

            # unified diff 헤더 생성
            diff_parts.append(f"diff --git a/{old_path} b/{new_path}")

            if change.get("new_file"):
                diff_parts.append("new file mode 100644")
                diff_parts.append("--- /dev/null")
                diff_parts.append(f"+++ b/{new_path}")
            elif change.get("deleted_file"):
                diff_parts.append("deleted file mode 100644")
                diff_parts.append(f"--- a/{old_path}")
                diff_parts.append("+++ /dev/null")
            elif change.get("renamed_file"):
                diff_parts.append(f"--- a/{old_path}")
                diff_parts.append(f"+++ b/{new_path}")
            else:
                diff_parts.append(f"--- a/{old_path}")
                diff_parts.append(f"+++ b/{new_path}")

            diff_parts.append(diff)

        return "\n".join(diff_parts)

    # ── MR 코멘트 게시 ────────────────────────────────────────

    def post_mr_comment(
        self,
        project_id: int,
        mr_iid: int,
        body: str,
    ) -> dict:
        """MR에 일반 코멘트(Discussion)를 게시한다.

        리뷰 요약용으로 사용한다.
        """
        resp = self._client.post(
            f"/projects/{project_id}/merge_requests/{mr_iid}/discussions",
            json={"body": body},
        )
        resp.raise_for_status()
        return resp.json()

    def post_inline_comment(
        self,
        project_id: int,
        mr_iid: int,
        body: str,
        new_path: str,
        new_line: int,
        base_sha: str,
        start_sha: str,
        head_sha: str,
        old_path: str | None = None,
    ) -> dict:
        """MR의 특정 파일/라인에 인라인 코멘트를 게시한다.

        GitLab Discussion API의 position 파라미터를 사용하여
        diff의 정확한 위치에 코멘트를 남긴다.
        """
        position = {
            "base_sha": base_sha,
            "start_sha": start_sha,
            "head_sha": head_sha,
            "position_type": "text",
            "new_path": new_path,
            "new_line": new_line,
            "old_path": old_path or new_path,
        }

        resp = self._client.post(
            f"/projects/{project_id}/merge_requests/{mr_iid}/discussions",
            json={"body": body, "position": position},
        )
        resp.raise_for_status()
        return resp.json()

    def get_mr_versions(self, project_id: int, mr_iid: int) -> list[dict]:
        """MR의 diff 버전 목록을 조회한다.

        인라인 코멘트의 base_sha, start_sha, head_sha를 얻기 위해 사용한다.
        """
        resp = self._client.get(
            f"/projects/{project_id}/merge_requests/{mr_iid}/versions",
        )
        resp.raise_for_status()
        return resp.json()

    # ── 리뷰 결과 게시 (통합) ─────────────────────────────────

    def post_review(
        self,
        project_id: int,
        mr_iid: int,
        comments: list,
    ) -> dict:
        """리뷰 결과를 MR에 게시한다.

        인라인 코멘트 게시를 시도하고, 실패 시 전체 코멘트로 폴백한다.
        마지막에 리뷰 요약을 별도 코멘트로 남긴다.

        Args:
            project_id: GitLab 프로젝트 ID.
            mr_iid: MR IID (Internal ID).
            comments: ReviewComment 리스트.

        Returns:
            {"posted_inline": int, "posted_summary": bool, "errors": list}
        """
        result = {"posted_inline": 0, "posted_summary": False, "errors": []}

        if not comments:
            # 이슈 없음 코멘트
            self.post_mr_comment(
                project_id, mr_iid,
                "🤖 **AI 코드 리뷰 완료**\n\n이슈가 발견되지 않았습니다. ✅",
            )
            result["posted_summary"] = True
            return result

        # SHA 정보 조회 (인라인 코멘트용)
        sha_info = self._get_latest_sha(project_id, mr_iid)

        # 인라인 코멘트 시도
        for comment in comments:
            severity_emoji = {
                "critical": "🔴",
                "warning": "🟡",
                "info": "🔵",
            }.get(comment.severity, "⚪")

            body = f"{severity_emoji} **[{comment.severity.upper()}]** {comment.message}"

            if sha_info and comment.line > 0:
                try:
                    self.post_inline_comment(
                        project_id=project_id,
                        mr_iid=mr_iid,
                        body=body,
                        new_path=comment.file,
                        new_line=comment.line,
                        **sha_info,
                    )
                    result["posted_inline"] += 1
                    continue
                except httpx.HTTPStatusError as e:
                    logger.warning(
                        "인라인 코멘트 실패 (%s:%d): %s",
                        comment.file, comment.line, e.response.status_code,
                    )
                    result["errors"].append(
                        f"{comment.file}:{comment.line} - {e.response.status_code}"
                    )

        # 리뷰 요약 코멘트
        summary = self._build_summary(comments)
        self.post_mr_comment(project_id, mr_iid, summary)
        result["posted_summary"] = True

        return result

    def _get_latest_sha(self, project_id: int, mr_iid: int) -> dict | None:
        """최신 diff 버전의 SHA 정보를 조회한다."""
        try:
            versions = self.get_mr_versions(project_id, mr_iid)
            if not versions:
                return None
            latest = versions[0]
            return {
                "base_sha": latest["base_commit_sha"],
                "start_sha": latest["start_commit_sha"],
                "head_sha": latest["head_commit_sha"],
            }
        except httpx.HTTPStatusError:
            logger.warning("MR 버전 조회 실패")
            return None

    @staticmethod
    def _build_summary(comments: list) -> str:
        """리뷰 코멘트 요약 마크다운을 생성한다."""
        by_severity: dict[str, list] = {"critical": [], "warning": [], "info": []}
        for c in comments:
            by_severity.get(c.severity, by_severity["info"]).append(c)

        lines = ["🤖 **AI 코드 리뷰 완료**\n"]

        total = len(comments)
        critical = len(by_severity["critical"])
        warning = len(by_severity["warning"])
        info = len(by_severity["info"])

        lines.append(
            f"총 **{total}**건의 이슈 발견: "
            f"🔴 Critical {critical} | 🟡 Warning {warning} | 🔵 Info {info}\n"
        )

        lines.append("| 파일 | 라인 | 심각도 | 내용 |")
        lines.append("|------|------|--------|------|")
        for c in comments:
            severity_emoji = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(
                c.severity, "⚪"
            )
            lines.append(
                f"| `{c.file}` | L{c.line} | {severity_emoji} {c.severity} | {c.message} |"
            )

        return "\n".join(lines)

    def close(self):
        """HTTP 클라이언트를 닫는다."""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
