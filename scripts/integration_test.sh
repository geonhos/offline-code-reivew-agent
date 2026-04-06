#!/usr/bin/env bash
# GitLab 연동 강화 통합 테스트
#
# POC 환경(docker-compose.poc.yml)에서 신규 기능을 검증한다:
#   1. 정상 리뷰 (첫 실행)
#   2. 중복 리뷰 방지 (같은 SHA → 스킵)
#   3. force-review 라벨 (중복이어도 강제 실행)
#   4. no-review 라벨 (리뷰 스킵)
#   5. Deep Health Check
#   6. SHA 변경 시 새 리뷰 실행
#
# 사용법:
#   ./scripts/integration_test.sh          # 전체 실행 (Docker 기동 포함)
#   ./scripts/integration_test.sh --skip-setup  # Docker 이미 실행 중일 때
#   ./scripts/integration_test.sh cleanup  # 정리

set -euo pipefail

COMPOSE_FILE="docker-compose.poc.yml"
MOCK_GITLAB="http://localhost:8929"
REVIEW_SERVICE="http://localhost:8000"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

PASS=0
FAIL=0
TOTAL=0

log_info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
log_ok()    { echo -e "${GREEN}[ OK ]${NC} $*"; }
log_fail()  { echo -e "${RED}[FAIL]${NC} $*"; }
log_test()  { echo -e "\n${CYAN}━━━ TEST: $* ━━━${NC}"; }

assert_eq() {
    local desc="$1" expected="$2" actual="$3"
    TOTAL=$((TOTAL + 1))
    if [ "$expected" = "$actual" ]; then
        log_ok "$desc (expected=$expected)"
        PASS=$((PASS + 1))
    else
        log_fail "$desc (expected=$expected, actual=$actual)"
        FAIL=$((FAIL + 1))
    fi
}

assert_contains() {
    local desc="$1" needle="$2" haystack="$3"
    TOTAL=$((TOTAL + 1))
    if echo "$haystack" | grep -q "$needle"; then
        log_ok "$desc"
        PASS=$((PASS + 1))
    else
        log_fail "$desc (expected to contain: $needle)"
        FAIL=$((FAIL + 1))
    fi
}

cleanup() {
    log_info "Docker 환경 정리 중..."
    docker compose -f "$COMPOSE_FILE" down -v --remove-orphans 2>/dev/null || true
    log_ok "정리 완료"
}

if [ "${1:-}" = "cleanup" ]; then
    cleanup
    exit 0
fi

echo -e "${CYAN}"
echo "╔══════════════════════════════════════════════════════════╗"
echo "║   Integration Test — GitLab 연동 강화 검증              ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# ── Setup ────────────────────────────────────────────────────────
if [ "${1:-}" != "--skip-setup" ]; then
    log_info "Step 0: 사전 조건 확인"

    if ! curl -sf --max-time 3 "http://localhost:11434/api/tags" > /dev/null 2>&1; then
        log_fail "Ollama가 실행 중이지 않습니다. 먼저 Ollama를 시작하세요."
        exit 1
    fi
    log_ok "Ollama 실행 중"

    log_info "Step 1: Docker 기동"
    docker compose -f "$COMPOSE_FILE" up -d --build 2>&1 | tail -3

    log_info "서비스 대기 중..."
    for i in $(seq 1 30); do
        if curl -sf --max-time 2 "${MOCK_GITLAB}/api/v4/version" > /dev/null 2>&1; then
            break
        fi
        sleep 1
    done

    for i in $(seq 1 60); do
        if curl -sf --max-time 2 "${REVIEW_SERVICE}/health" > /dev/null 2>&1; then
            break
        fi
        sleep 1
    done
    log_ok "모든 서비스 준비 완료"

    log_info "Step 2: DB 초기화 + 가이드라인 적재"
    docker compose -f "$COMPOSE_FILE" exec -T review-service \
        python -m scripts.init_db 2>&1 | tail -1
    docker compose -f "$COMPOSE_FILE" exec -T review-service \
        python -m scripts.ingest --source docs/sample_guidelines/ 2>&1 | tail -1
    log_ok "DB 준비 완료"
fi

# ── Helper: 웹훅 전송 ────────────────────────────────────────────
send_webhook() {
    local action="${1:-open}"
    local labels_json="${2:-[]}"

    curl -sf --max-time 10 \
        -X POST \
        -H "Content-Type: application/json" \
        -H "X-Gitlab-Token: poc-secret" \
        -d "{
          \"object_kind\": \"merge_request\",
          \"project\": {\"id\": 1},
          \"object_attributes\": {
            \"iid\": 1,
            \"action\": \"${action}\",
            \"title\": \"feat: add user management module\",
            \"source_branch\": \"feature/security-test\",
            \"target_branch\": \"main\",
            \"labels\": ${labels_json}
          }
        }" \
        "${REVIEW_SERVICE}/webhook" 2>/dev/null
}

# 리뷰 완료 대기
wait_for_review() {
    local max_wait="${1:-180}"
    local elapsed=0
    while [ $elapsed -lt $max_wait ]; do
        local total
        total=$(curl -sf "${MOCK_GITLAB}/_e2e/results" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('total',0))" 2>/dev/null || echo 0)
        if [ "$total" -gt 0 ]; then
            echo "$total"
            return 0
        fi
        sleep 3
        elapsed=$((elapsed + 3))
        echo -ne "  ⏳ 대기 중... ${elapsed}s\r" >&2
    done
    echo "0"
    return 1
}

# ══════════════════════════════════════════════════════════════════
# TEST 1: Deep Health Check
# ══════════════════════════════════════════════════════════════════
log_test "1. Deep Health Check"

HEALTH=$(curl -sf "${REVIEW_SERVICE}/health" 2>/dev/null)
HEALTH_STATUS=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))")
HEALTH_OLLAMA=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ollama',''))")
HEALTH_DB=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('database',''))")

assert_eq "Health status" "ok" "$HEALTH_STATUS"
assert_eq "Ollama check" "ok" "$HEALTH_OLLAMA"
assert_eq "Database check" "ok" "$HEALTH_DB"

# ══════════════════════════════════════════════════════════════════
# TEST 2: no-review 라벨 → 리뷰 스킵
# ══════════════════════════════════════════════════════════════════
log_test "2. no-review 라벨 → 리뷰 스킵"

# MR 상태 설정: no-review 라벨
curl -sf -X POST -H "Content-Type: application/json" \
    -d '{"labels": ["no-review"]}' \
    "${MOCK_GITLAB}/_e2e/set_mr_state" > /dev/null

RESP=$(send_webhook "open" '[{"title":"no-review"}]')
RESP_STATUS=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))")

assert_eq "Webhook returns skipped" "skipped" "$RESP_STATUS"

# ══════════════════════════════════════════════════════════════════
# TEST 3: 정상 리뷰 (첫 실행)
# ══════════════════════════════════════════════════════════════════
log_test "3. 정상 리뷰 (첫 실행)"

# 라벨 초기화 + SHA 설정
curl -sf -X POST -H "Content-Type: application/json" \
    -d '{"labels": [], "sha": "integration-test-sha-001"}' \
    "${MOCK_GITLAB}/_e2e/set_mr_state" > /dev/null

# 결과 초기화
curl -sf -X DELETE "${MOCK_GITLAB}/_e2e/reset" > /dev/null

RESP=$(send_webhook "open" '[]')
RESP_STATUS=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))")
assert_eq "Webhook accepted" "accepted" "$RESP_STATUS"

log_info "리뷰 완료 대기 중 (Ollama 추론)..."
TOTAL_COMMENTS=$(wait_for_review 300)

TOTAL=$((TOTAL + 1))
if [ "$TOTAL_COMMENTS" -gt 0 ]; then
    log_ok "리뷰 완료: ${TOTAL_COMMENTS}개 코멘트"
    PASS=$((PASS + 1))
else
    log_fail "리뷰 타임아웃 — 코멘트 0개"
    FAIL=$((FAIL + 1))
fi

# 요약 코멘트 확인
RESULTS=$(curl -sf "${MOCK_GITLAB}/_e2e/results" 2>/dev/null)
SUMMARY=$(echo "$RESULTS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('summary_found',False))")
assert_eq "Summary comment posted" "True" "$SUMMARY"

# ══════════════════════════════════════════════════════════════════
# TEST 4: 중복 리뷰 방지 (같은 SHA → 스킵)
# ══════════════════════════════════════════════════════════════════
log_test "4. 중복 리뷰 방지 (같은 SHA)"

# 이전 결과 기록
PREV_TOTAL="$TOTAL_COMMENTS"

# 결과 초기화
curl -sf -X DELETE "${MOCK_GITLAB}/_e2e/reset" > /dev/null

# 같은 SHA로 다시 웹훅 전송
RESP=$(send_webhook "update" '[]')
RESP_STATUS=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))")
assert_eq "Webhook accepted (duplicate)" "accepted" "$RESP_STATUS"

# 잠시 대기 — 중복이면 코멘트가 안 올라와야 함
sleep 10

DUP_TOTAL=$(curl -sf "${MOCK_GITLAB}/_e2e/results" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('total',0))" 2>/dev/null || echo 0)
assert_eq "No new comments (duplicate skipped)" "0" "$DUP_TOTAL"

# ══════════════════════════════════════════════════════════════════
# TEST 5: force-review 라벨 → 중복이어도 강제 실행
# ══════════════════════════════════════════════════════════════════
log_test "5. force-review 라벨 → 강제 리뷰"

# 결과 초기화
curl -sf -X DELETE "${MOCK_GITLAB}/_e2e/reset" > /dev/null

# force-review 라벨로 웹훅 전송 (같은 SHA)
curl -sf -X POST -H "Content-Type: application/json" \
    -d '{"labels": ["force-review"]}' \
    "${MOCK_GITLAB}/_e2e/set_mr_state" > /dev/null

RESP=$(send_webhook "update" '[{"title":"force-review"}]')
RESP_STATUS=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))")
assert_eq "Webhook accepted (force)" "accepted" "$RESP_STATUS"

log_info "강제 리뷰 완료 대기 중..."
FORCE_TOTAL=$(wait_for_review 300)

TOTAL=$((TOTAL + 1))
if [ "$FORCE_TOTAL" -gt 0 ]; then
    log_ok "강제 리뷰 완료: ${FORCE_TOTAL}개 코멘트"
    PASS=$((PASS + 1))
else
    log_fail "강제 리뷰 실패 — 코멘트 0개"
    FAIL=$((FAIL + 1))
fi

# ══════════════════════════════════════════════════════════════════
# TEST 6: SHA 변경 시 새 리뷰 실행
# ══════════════════════════════════════════════════════════════════
log_test "6. SHA 변경 → 새 리뷰 실행"

# 결과 초기화 + 새 SHA 설정
curl -sf -X DELETE "${MOCK_GITLAB}/_e2e/reset" > /dev/null
curl -sf -X POST -H "Content-Type: application/json" \
    -d '{"labels": [], "sha": "integration-test-sha-002"}' \
    "${MOCK_GITLAB}/_e2e/set_mr_state" > /dev/null

RESP=$(send_webhook "update" '[]')
RESP_STATUS=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))")
assert_eq "Webhook accepted (new SHA)" "accepted" "$RESP_STATUS"

log_info "새 SHA 리뷰 완료 대기 중..."
NEW_SHA_TOTAL=$(wait_for_review 300)

TOTAL=$((TOTAL + 1))
if [ "$NEW_SHA_TOTAL" -gt 0 ]; then
    log_ok "새 SHA 리뷰 완료: ${NEW_SHA_TOTAL}개 코멘트"
    PASS=$((PASS + 1))
else
    log_fail "새 SHA 리뷰 실패 — 코멘트 0개"
    FAIL=$((FAIL + 1))
fi

# ══════════════════════════════════════════════════════════════════
# 결과 요약
# ══════════════════════════════════════════════════════════════════
echo ""
echo -e "${CYAN}══════════════════════════════════════════════════════════${NC}"
if [ $FAIL -eq 0 ]; then
    echo -e "${GREEN}  ✅ 모든 테스트 통과! (${PASS}/${TOTAL})${NC}"
else
    echo -e "${RED}  ❌ 실패: ${FAIL}/${TOTAL}${NC}"
fi
echo -e "${CYAN}══════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  정리하려면: ${YELLOW}$0 cleanup${NC}"

exit $FAIL
