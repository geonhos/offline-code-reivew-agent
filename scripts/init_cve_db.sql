-- CVE 취약점 데이터베이스 스키마
-- 오프라인 환경에서 NVD/OSV 데이터를 로컬에 저장하여 의존성 취약점을 검사한다.

CREATE TABLE IF NOT EXISTS cve_entries (
    id                     SERIAL PRIMARY KEY,
    cve_id                 VARCHAR(20) NOT NULL UNIQUE,
    package_name           VARCHAR(255) NOT NULL,
    affected_version_start VARCHAR(50),
    affected_version_end   VARCHAR(50),
    fixed_version          VARCHAR(50),
    severity               VARCHAR(10) NOT NULL DEFAULT 'medium',
    description            TEXT,
    published_date         TIMESTAMPTZ,
    updated_at             TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cve_package_name
    ON cve_entries (package_name);

CREATE INDEX IF NOT EXISTS idx_cve_package_severity
    ON cve_entries (package_name, severity);
