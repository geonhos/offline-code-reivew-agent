"""오프라인 CVE 데이터 로딩 스크립트 - NVD/OSV JSON → PostgreSQL cve_entries.

폐쇄망 환경에서 USB 등으로 전달받은 CVE JSON 파일을 DB에 적재한다.

Usage:
    python scripts/load_cve_data.py --sample          # 테스트용 샘플 데이터 로딩
    python scripts/load_cve_data.py --input cve.json   # JSON 파일에서 로딩
"""

import argparse
import json
import logging
import sys
from pathlib import Path

import psycopg

from src.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [CVE-Loader] %(message)s")
logger = logging.getLogger(__name__)

# 테스트/데모용 샘플 CVE 데이터
SAMPLE_CVE_DATA = [
    {
        "cve_id": "CVE-2023-30861",
        "package_name": "flask",
        "affected_version_start": "0.1",
        "affected_version_end": "2.3.2",
        "fixed_version": "2.3.2",
        "severity": "high",
        "description": "Flask에서 세션 쿠키가 크로스 사이트 요청에 전송되어 세션 하이재킹이 가능한 취약점",
        "published_date": "2023-05-02",
    },
    {
        "cve_id": "CVE-2023-32681",
        "package_name": "requests",
        "affected_version_start": "2.3.0",
        "affected_version_end": "2.31.0",
        "fixed_version": "2.31.0",
        "severity": "medium",
        "description": "requests 라이브러리에서 Proxy-Authorization 헤더가 리다이렉트 시 유출되는 취약점",
        "published_date": "2023-05-26",
    },
    {
        "cve_id": "CVE-2023-36053",
        "package_name": "django",
        "affected_version_start": "2.0",
        "affected_version_end": "4.2.2",
        "fixed_version": "4.2.2",
        "severity": "high",
        "description": "Django의 EmailValidator/URLValidator에서 정규식 ReDoS 공격이 가능한 취약점",
        "published_date": "2023-07-03",
    },
    {
        "cve_id": "CVE-2024-34064",
        "package_name": "jinja2",
        "affected_version_start": "0.1",
        "affected_version_end": "3.1.4",
        "fixed_version": "3.1.4",
        "severity": "medium",
        "description": "Jinja2 xmlattr 필터에서 키에 대한 입력값 검증 누락으로 XSS 공격이 가능한 취약점",
        "published_date": "2024-05-06",
    },
    {
        "cve_id": "CVE-2024-6345",
        "package_name": "setuptools",
        "affected_version_start": "0.1",
        "affected_version_end": "70.0.0",
        "fixed_version": "70.0.0",
        "severity": "high",
        "description": "setuptools의 package_index 모듈에서 원격 코드 실행이 가능한 취약점",
        "published_date": "2024-07-15",
    },
    {
        "cve_id": "CVE-2023-44487",
        "package_name": "urllib3",
        "affected_version_start": "1.0",
        "affected_version_end": "2.0.7",
        "fixed_version": "2.0.7",
        "severity": "high",
        "description": "urllib3에서 HTTP/2 Rapid Reset 공격에 취약한 취약점",
        "published_date": "2023-10-10",
    },
    {
        "cve_id": "CVE-2023-37920",
        "package_name": "certifi",
        "affected_version_start": "2015.4.28",
        "affected_version_end": "2023.7.22",
        "fixed_version": "2023.7.22",
        "severity": "critical",
        "description": "certifi에서 신뢰할 수 없는 e-Tugra 루트 인증서가 포함된 취약점",
        "published_date": "2023-07-25",
    },
    {
        "cve_id": "CVE-2024-3651",
        "package_name": "idna",
        "affected_version_start": "0.1",
        "affected_version_end": "3.7",
        "fixed_version": "3.7",
        "severity": "medium",
        "description": "idna 라이브러리에서 입력값에 의한 ReDoS 공격이 가능한 취약점",
        "published_date": "2024-04-11",
    },
]


def load_from_json(filepath: str) -> list[dict]:
    """JSON 파일에서 CVE 데이터를 로딩한다."""
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {filepath}")

    data = json.loads(path.read_text(encoding="utf-8"))

    # NVD JSON Feed 형식 지원
    if isinstance(data, dict) and "CVE_Items" in data:
        return _parse_nvd_format(data)

    # 직접 리스트 형식
    if isinstance(data, list):
        return data

    raise ValueError("지원하지 않는 JSON 형식입니다")


def _parse_nvd_format(data: dict) -> list[dict]:
    """NVD JSON Feed 형식을 내부 형식으로 변환한다."""
    entries = []
    for item in data.get("CVE_Items", []):
        cve_meta = item.get("cve", {}).get("CVE_data_meta", {})
        cve_id = cve_meta.get("ID", "")

        # 설명 추출
        desc_data = item.get("cve", {}).get("description", {}).get("description_data", [])
        description = desc_data[0].get("value", "") if desc_data else ""

        # 심각도 추출
        impact = item.get("impact", {})
        cvss_v3 = impact.get("baseMetricV3", {}).get("cvssV3", {})
        severity = cvss_v3.get("baseSeverity", "medium").lower()

        # 영향받는 패키지 정보 추출
        nodes = item.get("configurations", {}).get("nodes", [])
        for node in nodes:
            for cpe in node.get("cpe_match", []):
                cpe_uri = cpe.get("cpe23Uri", "")
                parts = cpe_uri.split(":")
                if len(parts) >= 5:
                    package_name = parts[4]
                    entries.append({
                        "cve_id": cve_id,
                        "package_name": package_name,
                        "affected_version_start": cpe.get("versionStartIncluding", ""),
                        "affected_version_end": cpe.get("versionEndExcluding", ""),
                        "fixed_version": cpe.get("versionEndExcluding", ""),
                        "severity": severity,
                        "description": description,
                        "published_date": item.get("publishedDate", ""),
                    })
    return entries


def upsert_cve_entries(entries: list[dict]) -> int:
    """CVE 데이터를 DB에 upsert한다."""
    params = [
        (
            entry["cve_id"],
            entry["package_name"],
            entry.get("affected_version_start", ""),
            entry.get("affected_version_end", ""),
            entry.get("fixed_version", ""),
            entry.get("severity", "medium"),
            entry.get("description", ""),
            entry.get("published_date") or None,
        )
        for entry in entries
    ]
    with psycopg.connect(settings.database_url) as conn:
        cur = conn.cursor()
        cur.executemany(
            """
            INSERT INTO cve_entries
                (cve_id, package_name, affected_version_start,
                 affected_version_end, fixed_version, severity,
                 description, published_date, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::timestamptz, now())
            ON CONFLICT (cve_id) DO UPDATE SET
                package_name = EXCLUDED.package_name,
                affected_version_start = EXCLUDED.affected_version_start,
                affected_version_end = EXCLUDED.affected_version_end,
                fixed_version = EXCLUDED.fixed_version,
                severity = EXCLUDED.severity,
                description = EXCLUDED.description,
                updated_at = now()
            """,
            params,
        )
        conn.commit()
    return len(params)


def main():
    parser = argparse.ArgumentParser(description="CVE 데이터를 PostgreSQL에 로딩합니다.")
    parser.add_argument("--input", help="CVE JSON 파일 경로")
    parser.add_argument("--sample", action="store_true", help="테스트용 샘플 데이터 로딩")
    args = parser.parse_args()

    if not args.input and not args.sample:
        parser.print_help()
        sys.exit(1)

    if args.sample:
        entries = SAMPLE_CVE_DATA
        logger.info("샘플 CVE 데이터 %d건 로딩 시작", len(entries))
    else:
        entries = load_from_json(args.input)
        logger.info("JSON 파일에서 %d건 로딩 시작", len(entries))

    count = upsert_cve_entries(entries)
    logger.info("CVE 데이터 %d건 적재 완료", count)


if __name__ == "__main__":
    main()
