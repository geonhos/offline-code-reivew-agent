-- 리뷰 이력 테이블 — MR 리뷰 추적 및 중복 방지용
-- init_db.py 또는 docker-entrypoint-initdb.d로 실행된다.

CREATE TABLE IF NOT EXISTS review_history (
    id              SERIAL PRIMARY KEY,
    project_id      INTEGER NOT NULL,
    mr_iid          INTEGER NOT NULL,
    commit_sha      VARCHAR(40) NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'pending',
    comment_count   INTEGER DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),

    CONSTRAINT uq_review_history UNIQUE (project_id, mr_iid, commit_sha),
    CONSTRAINT chk_status CHECK (status IN ('pending', 'completed', 'failed', 'skipped'))
);

CREATE INDEX IF NOT EXISTS idx_review_history_project_mr
    ON review_history (project_id, mr_iid);

CREATE INDEX IF NOT EXISTS idx_review_history_status
    ON review_history (status);
