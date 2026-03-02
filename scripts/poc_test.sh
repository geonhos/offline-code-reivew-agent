#!/usr/bin/env bash
# POC E2E 테스트 — Mock GitLab으로 빠르게 리뷰 파이프라인 검증
#
# 사용법:
#   ./scripts/poc_test.sh          # 전체 실행
#   ./scripts/poc_test.sh cleanup  # 정리

set -euo pipefail

COMPOSE_FILE="docker-compose.poc.yml"
MOCK_GITLAB="http://localhost:8929"
REVIEW_SERVICE="http://localhost:8000"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
log_ok()    { echo -e "${GREEN}[ OK ]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[FAIL]${NC} $*"; }

cleanup() {
    log_info "Docker 환경 정리 중..."
    docker compose -f "$COMPOSE_FILE" down -v --remove-orphans 2>/dev/null || true
    log_ok "정리 완료"
}

if [ "${1:-}" = "cleanup" ]; then
    cleanup
    exit 0
fi

echo -e "${BLUE}"
echo "╔══════════════════════════════════════════════════════════╗"
echo "║   POC E2E Test — Mock GitLab + AI Code Review          ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# ── Step 1: 사전 조건 ────────────────────────────────────────────
log_info "Step 1/6: 사전 조건 확인"

if ! curl -sf --max-time 3 "http://localhost:11434/api/tags" > /dev/null 2>&1; then
    log_error "Ollama가 실행 중이지 않습니다."
    exit 1
fi
log_ok "Ollama 실행 중"

# ── Step 2: 기동 ─────────────────────────────────────────────────
log_info "Step 2/6: Docker 기동 (빌드 + 시작)"

docker compose -f "$COMPOSE_FILE" up -d --build 2>&1 | tail -5
log_ok "컨테이너 기동 완료"

# Mock GitLab 대기 (거의 즉시)
log_info "Mock GitLab 대기..."
for i in $(seq 1 30); do
    if curl -sf --max-time 2 "${MOCK_GITLAB}/api/v4/version" > /dev/null 2>&1; then
        break
    fi
    sleep 1
done
log_ok "Mock GitLab 준비 완료"

# Review Service 대기
log_info "Review Service 대기..."
for i in $(seq 1 60); do
    if curl -sf --max-time 2 "${REVIEW_SERVICE}/health" > /dev/null 2>&1; then
        break
    fi
    sleep 1
done
log_ok "Review Service 준비 완료"

# ── Step 3: DB 초기화 + 가이드라인 적재 ──────────────────────────
log_info "Step 3/6: DB 초기화 + 가이드라인 임베딩"

docker compose -f "$COMPOSE_FILE" exec -T review-service \
    python -m scripts.init_db 2>&1
log_ok "DB 스키마 초기화 완료"

docker compose -f "$COMPOSE_FILE" exec -T review-service \
    python -m scripts.ingest --source docs/sample_guidelines/ 2>&1
log_ok "가이드라인 적재 완료"

# ── Step 4: 브라우저 열기 ────────────────────────────────────────
log_info "Step 4/6: 대시보드 열기"
echo ""
echo -e "  ${GREEN}🌐 브라우저에서 리뷰 결과를 실시간으로 확인하세요:${NC}"
echo -e "  ${BLUE}   → ${MOCK_GITLAB}${NC}"
echo ""

# macOS에서 자동으로 브라우저 열기
if command -v open &> /dev/null; then
    open "${MOCK_GITLAB}" 2>/dev/null || true
fi

# ── Step 5: 웹훅 트리거 ─────────────────────────────────────────
log_info "Step 5/6: 웹훅 전송 (MR 리뷰 요청)"

WEBHOOK_PAYLOAD='{
  "object_kind": "merge_request",
  "project": {"id": 1},
  "object_attributes": {
    "iid": 1,
    "action": "open",
    "title": "feat: add user management module",
    "source_branch": "feature/security-test",
    "target_branch": "main"
  }
}'

WEBHOOK_RESP=$(curl -sf --max-time 10 \
    -X POST \
    -H "Content-Type: application/json" \
    -H "X-Gitlab-Token: poc-secret" \
    -d "$WEBHOOK_PAYLOAD" \
    "${REVIEW_SERVICE}/webhook" 2>&1) || {
    log_error "웹훅 전송 실패"
    echo "  $WEBHOOK_RESP"
    exit 1
}
log_ok "웹훅 전송 완료: $WEBHOOK_RESP"

# ── Step 6: 리뷰 완료 대기 + 결과 확인 ──────────────────────────
log_info "Step 6/6: AI 리뷰 완료 대기 (Ollama 추론 중...)"

MAX_WAIT=300
ELAPSED=0
while [ $ELAPSED -lt $MAX_WAIT ]; do
    RESULT=$(curl -sf "${MOCK_GITLAB}/_e2e/results" 2>/dev/null || echo '{"total":0}')
    TOTAL=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('total',0))" 2>/dev/null || echo 0)

    if [ "$TOTAL" -gt 0 ]; then
        echo ""
        break
    fi

    sleep 5
    ELAPSED=$((ELAPSED + 5))
    echo -ne "  ⏳ 대기 중... ${ELAPSED}s (Ollama 추론 + RAG 검색)\r"
done

if [ "$TOTAL" -eq 0 ]; then
    echo ""
    log_error "리뷰 타임아웃 (${MAX_WAIT}s)"
    log_info "로그 확인: docker compose -f $COMPOSE_FILE logs review-service"
    exit 1
fi

# 결과 출력
RESULT=$(curl -sf "${MOCK_GITLAB}/_e2e/results")
INLINE=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['inline_count'])")
GENERAL=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['general_count'])")
SUMMARY=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['summary_found'])")

echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  ✅ POC E2E 테스트 통과!${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo -e "  💬 총 코멘트:     ${TOTAL}개"
echo -e "  📍 인라인 코멘트: ${INLINE}개"
echo -e "  📝 일반 코멘트:   ${GENERAL}개"
echo -e "  📋 요약 포함:     ${SUMMARY}"
echo ""
echo -e "  ${BLUE}🌐 브라우저에서 확인: ${MOCK_GITLAB}${NC}"
echo ""
echo -e "  정리하려면: ${YELLOW}$0 cleanup${NC}"
