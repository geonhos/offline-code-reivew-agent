# Plan: GitLab 연동 강화 (Pragmatic)

## Approach
**Selected:** Approach C — Pragmatic (운영 안정성 + 필수 연동)
**Reasoning:** 실제 GitLab 운영에서 가장 큰 문제인 중복 리뷰, API 장애 복구, 대용량 Diff를 해결하면서, 라벨 기반 필터링으로 리뷰 대상을 제어할 수 있게 함. 자동 승인이나 webhook 등록 자동화는 이후 Phase 2로 추가 가능.
**Alternatives considered:** A (Core — 최소한만), B (Full — 자동승인/webhook등록 포함)

## Overview
- Complexity: moderate
- Tasks: 14
- Agents: ai-expert
- Parallel Groups: 4

## Validation
- Score: 10/10 ✅
- Issues: None

## Gap 분석

| # | Gap | 해결 태스크 |
|---|-----|------------|
| 1 | 중복 리뷰 방지 없음 | T-004, T-008 |
| 2 | 리뷰 이력 저장 없음 | T-001, T-004, T-013 |
| 3 | Retry/재시도 없음 | T-002, T-006, T-007 |
| 4 | 대용량 Diff 미처리 | T-005 |
| 5 | MR 라벨 필터링 없음 | T-007, T-008 |
| 6 | Health Check 얕음 | T-009 |

## Tasks

### Group 1 (parallel) — 기반 인프라
| ID | Type | Agent | File | Description |
|----|------|-------|------|-------------|
| T-001 | CREATE | ai-expert | scripts/init_review_history_db.sql | 리뷰 이력 테이블 DDL |
| T-002 | CREATE | ai-expert | src/retry.py | 지수 백오프 재시도 유틸리티 |
| T-003 | CREATE | ai-expert | tests/test_retry.py | retry 유틸리티 테스트 |

### Group 2 (after Group 1) — 핵심 로직
| ID | Type | Agent | File | Description |
|----|------|-------|------|-------------|
| T-004 | CREATE | ai-expert | src/review_history.py | 리뷰 이력 CRUD 클래스 |
| T-005 | MODIFY | ai-expert | src/reviewer.py | 대용량 Diff 분할/스킵 로직 |
| T-006 | MODIFY | ai-expert | src/reviewer.py | _call_llm에 retry 적용 |
| T-007 | MODIFY | ai-expert | src/gitlab_client.py | MR 라벨 조회 + API retry 적용 |

### Group 3 (after Group 2) — 서버 통합
| ID | Type | Agent | File | Description |
|----|------|-------|------|-------------|
| T-008 | MODIFY | ai-expert | src/server.py | 중복 리뷰 방지 + 라벨 필터링 |
| T-009 | MODIFY | ai-expert | src/server.py | Deep Health Check |
| T-013 | MODIFY | ai-expert | scripts/init_db.py | review_history 테이블 생성 추가 |
| T-014 | MODIFY | ai-expert | docker-compose.poc.yml | DDL 초기화 마운트 |

### Group 4 (after Group 3) — 테스트
| ID | Type | Agent | File | Description |
|----|------|-------|------|-------------|
| T-010 | CREATE | ai-expert | tests/test_review_history.py | 리뷰 이력 CRUD 테스트 |
| T-011 | MODIFY | ai-expert | tests/test_server.py | 중복방지 + 라벨 + 헬스체크 테스트 |
| T-012 | MODIFY | ai-expert | tests/test_reviewer.py | 대용량 Diff 스킵 테스트 |

## Critical Path
T-001 → T-004 → T-008 → T-011

## Risks
| Risk | Impact | Mitigation |
|------|--------|------------|
| DB 연결 실패 시 리뷰 이력 저장 불가 | M | 이력 저장 실패해도 리뷰는 진행 (graceful degradation) |
| Retry 과도 시 Ollama 부하 | L | max_retries=3 제한 + 지수 백오프 |
| GitLab API 버전별 라벨 응답 차이 | L | 라벨 조회 실패 시 필터링 스킵 (보수적) |
