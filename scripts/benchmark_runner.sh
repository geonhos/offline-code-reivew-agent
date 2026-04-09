#!/usr/bin/env bash
# LLM 벤치마크 실행기 — RAM 관리 포함
#
# 시스템 RAM: 48GB
# 전략: 오프라인 모델을 하나씩 pull → 벤치마크 → 제거하여 RAM 절약
#
# Usage:
#   ./scripts/benchmark_runner.sh           # 전체 실행 (offline + cloud)
#   ./scripts/benchmark_runner.sh offline   # offline만
#   ./scripts/benchmark_runner.sh cloud     # cloud만

set -euo pipefail

# ──────────────────────────────────────────
# 색상 출력 헬퍼
# ──────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

log_info()    { echo -e "${CYAN}[$(timestamp)]${RESET} $*"; }
log_success() { echo -e "${GREEN}[$(timestamp)] ✔ $*${RESET}"; }
log_warn()    { echo -e "${YELLOW}[$(timestamp)] ⚠ $*${RESET}"; }
log_error()   { echo -e "${RED}[$(timestamp)] ✘ $*${RESET}" >&2; }
log_step()    { echo -e "\n${BOLD}${YELLOW}$*${RESET}"; }

timestamp() { date '+%H:%M:%S'; }

# ──────────────────────────────────────────
# 설정
# ──────────────────────────────────────────

# 오프라인 모델 목록 (작은 것부터 큰 순서)
OFFLINE_MODELS=(
    "codegemma:7b-instruct"   # ~6-8GB
    "granite-code:8b"          # ~6-8GB
    "starcoder2:15b"           # ~10-12GB
    "codestral:22b"            # ~14-16GB
)

# 절대 삭제하면 안 되는 모델 (임베딩용)
PROTECTED_MODEL="nomic-embed-text"

# 결과 저장 디렉토리 (프로젝트 루트 기준)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RESULTS_DIR="${PROJECT_ROOT}/results"

# 실행 모드: all | offline | cloud (기본값: all)
MODE="${1:-all}"

# 전체 시작 시각 기록 (elapsed time 계산용)
TOTAL_START=$(date +%s)

# ──────────────────────────────────────────
# 유틸리티 함수
# ──────────────────────────────────────────

# 모델 이름에서 파일명 안전 문자열 생성 (예: codegemma:7b-instruct → codegemma_7b-instruct)
safe_name() {
    echo "$1" | tr ':' '_'
}

# 경과 시간을 "Xm Ys" 형식으로 반환
elapsed_since() {
    local start_sec="$1"
    local end_sec
    end_sec=$(date +%s)
    local diff=$(( end_sec - start_sec ))
    echo "$(( diff / 60 ))m $(( diff % 60 ))s"
}

# 모델 보호 검사: nomic-embed-text 삭제 방지
assert_not_protected() {
    local model="$1"
    if [[ "${model}" == *"${PROTECTED_MODEL}"* ]]; then
        log_error "보호된 모델 '${PROTECTED_MODEL}'은 삭제할 수 없습니다. 스크립트를 종료합니다."
        exit 1
    fi
}

# ──────────────────────────────────────────
# results/ 디렉토리 생성
# ──────────────────────────────────────────
prepare_results_dir() {
    mkdir -p "${RESULTS_DIR}"
    log_info "결과 디렉토리 준비 완료: ${RESULTS_DIR}"
}

# ──────────────────────────────────────────
# 단일 오프라인 모델 벤치마크
# ──────────────────────────────────────────
run_offline_benchmark() {
    local model="$1"
    local index="$2"
    local total="$3"

    local name
    name="$(safe_name "${model}")"
    local output_file="${RESULTS_DIR}/benchmark_${name}.json"
    local model_start
    model_start=$(date +%s)

    log_step "=== [${index}/${total}] ${model} 모델 준비 ==="

    # ── 1. 모델 Pull ──
    log_info "${model} pulling 중..."
    if ! ollama pull "${model}"; then
        log_error "${model} pull 실패 — 다음 모델로 넘어갑니다."
        return 1
    fi
    log_success "${model} pull 완료"

    # ── 2. 모델 로드 대기 (Ollama 내부 초기화 시간)
    log_info "모델 안정화 대기 중 (5초)..."
    sleep 5

    # ── 3. 벤치마크 실행 ──
    log_info "${model} 벤치마크 시작 → ${output_file}"
    if ! python -m scripts.benchmark_models \
            --models "${model}" \
            --output "${output_file}"; then
        log_error "${model} 벤치마크 실패 — 모델 제거 후 다음으로 넘어갑니다."
        # 실패해도 RAM 확보를 위해 제거 시도
        ollama rm "${model}" 2>/dev/null || true
        return 1
    fi
    log_success "${model} 벤치마크 완료 → ${output_file}"

    # ── 4. 모델 제거 (RAM 확보) ──
    assert_not_protected "${model}"
    log_info "${model} RAM 확보를 위해 제거 중..."
    if ! ollama rm "${model}"; then
        log_warn "${model} 제거 실패 — 계속 진행합니다."
    else
        log_success "${model} 제거 완료"
    fi

    log_success "[${index}/${total}] ${model} 완료 (소요: $(elapsed_since "${model_start}"))"
}

# ──────────────────────────────────────────
# 전체 오프라인 벤치마크
# ──────────────────────────────────────────
run_all_offline() {
    local total="${#OFFLINE_MODELS[@]}"
    local failed_models=()

    log_step "==============================="
    log_step " 오프라인 모델 벤치마크 시작"
    log_step " 대상: ${total}개 모델"
    log_step "==============================="

    for i in "${!OFFLINE_MODELS[@]}"; do
        local model="${OFFLINE_MODELS[$i]}"
        local index=$(( i + 1 ))

        # 에러 발생 시 목록에 추가하되 계속 진행 (set -e 우회)
        if ! run_offline_benchmark "${model}" "${index}" "${total}"; then
            failed_models+=("${model}")
        fi
    done

    # 실패 요약
    if [[ ${#failed_models[@]} -gt 0 ]]; then
        log_warn "실패한 오프라인 모델 (${#failed_models[@]}개):"
        for m in "${failed_models[@]}"; do
            log_warn "  - ${m}"
        done
    else
        log_success "모든 오프라인 모델 벤치마크 성공"
    fi
}

# ──────────────────────────────────────────
# 클라우드 모델 벤치마크
# ──────────────────────────────────────────
run_cloud_benchmark() {
    local cloud_start
    cloud_start=$(date +%s)
    local output_file="${RESULTS_DIR}/benchmark_cloud.json"

    log_step "==============================="
    log_step " 클라우드 모델 벤치마크 시작"
    log_step " 대상: gpt-4o, claude-sonnet-4-20250514"
    log_step "==============================="

    log_info "클라우드 벤치마크 실행 중..."
    if ! python -m scripts.benchmark_models \
            --cloud-only \
            --output "${output_file}"; then
        log_error "클라우드 벤치마크 실패"
        return 1
    fi

    log_success "클라우드 벤치마크 완료 → ${output_file} (소요: $(elapsed_since "${cloud_start}"))"
}

# ──────────────────────────────────────────
# 최종 요약 출력
# ──────────────────────────────────────────
print_summary() {
    local total_elapsed
    total_elapsed="$(elapsed_since "${TOTAL_START}")"

    echo ""
    log_step "==============================="
    log_step " 벤치마크 완료 요약"
    log_step "==============================="
    log_info "결과 디렉토리: ${RESULTS_DIR}"

    # 생성된 결과 파일 목록
    if compgen -G "${RESULTS_DIR}/benchmark_*.json" > /dev/null 2>&1; then
        log_info "생성된 결과 파일:"
        for f in "${RESULTS_DIR}"/benchmark_*.json; do
            echo -e "  ${GREEN}•${RESET} $(basename "${f}")"
        done
    else
        log_warn "생성된 결과 파일이 없습니다."
    fi

    echo ""
    log_success "총 소요 시간: ${total_elapsed}"
    echo ""
}

# ──────────────────────────────────────────
# 모드 유효성 검사
# ──────────────────────────────────────────
validate_mode() {
    case "${MODE}" in
        all|offline|cloud) ;;
        *)
            log_error "알 수 없는 모드: '${MODE}'"
            echo "Usage: $0 [all|offline|cloud]" >&2
            exit 1
            ;;
    esac
}

# ──────────────────────────────────────────
# 메인 진입점
# ──────────────────────────────────────────
main() {
    validate_mode
    prepare_results_dir

    log_info "실행 모드: ${MODE}"
    log_info "프로젝트 루트: ${PROJECT_ROOT}"

    case "${MODE}" in
        all)
            run_all_offline
            run_cloud_benchmark
            ;;
        offline)
            run_all_offline
            ;;
        cloud)
            run_cloud_benchmark
            ;;
    esac

    print_summary
}

main
