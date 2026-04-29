#!/bin/bash
# 맥북 일일 데이터 갱신 cron (매일 18:00 KST)
#
# Render는 가벼운 조회만 담당하고, 모든 무거운 수집/계산은 여기서 수행.
# 결과를 git push로 Render에 전달.
#
# crontab 등록:
#   crontab -e
#   0 18 * * * /Users/beomjun/stock-dashboard/scripts/daily_macbook_cron.sh
#
# 수동 실행:
#   bash /Users/beomjun/stock-dashboard/scripts/daily_macbook_cron.sh

set -u  # 변수 미정의 시 에러 (set -e는 일부 단계 실패 허용 위해 미사용)

PROJECT_DIR="/Users/beomjun/stock-dashboard"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/cron_$(date +%Y%m%d).log"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR" || exit 1

# Python 경로 (homebrew Python 3.14 우선, 없으면 시스템 python3)
PYTHON="$(command -v python3)"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

run_step() {
  local name="$1"; shift
  log "▶  $name 시작"
  local t0=$(date +%s)
  if "$@" >> "$LOG_FILE" 2>&1; then
    local t=$(($(date +%s) - t0))
    log "✓  $name 완료 (${t}s)"
  else
    local t=$(($(date +%s) - t0))
    log "✗  $name 실패 (${t}s) — 다음 단계 계속 진행"
  fi
}

log "=========================================="
log "  daily_macbook_cron.sh 시작"
log "=========================================="

# ── 1. DART 분기 재무 (실패/미수집 종목만 retry) ──
run_step "DART retry"          "$PYTHON" dart_collector.py retry

# ── 2. 5년 OHLCV 갱신 (KR 82종목) ──
run_step "OHLCV 5y collector"  "$PYTHON" ohlcv_5y_collector.py all

# ── 3. data_fetcher.py (네이버 테마 시세) ──
run_step "data_fetcher (Naver 테마)"  "$PYTHON" data_fetcher.py

# ── 4. 밸류에이션 밴드 재계산 ──
run_step "Valuation band"      "$PYTHON" valuation_calculator.py all

# ── 5. 컨센서스 (네이버 + yfinance) ──
run_step "Consensus collector" "$PYTHON" consensus_collector.py all

# ── 6. TAM 모델링 → JSON export ──
run_step "TAM modeler"         "$PYTHON" tam_modeler.py all

# ── 7. DB Gist 백업 (db_backup.CORE_TABLES만) ──
run_step "Gist DB backup" "$PYTHON" -c "from db_backup import backup_db; print(backup_db())"

# ── 8. git commit + push (data.json만 — DB는 .gitignore) ──
log "▶  git commit + push"
{
  git add data.json 2>&1
  if git diff --cached --quiet; then
    log "  변경사항 없음 — push 생략"
  else
    git commit -m "auto: daily refresh $(date +%Y-%m-%d)" 2>&1 | tee -a "$LOG_FILE"
    git push 2>&1 | tee -a "$LOG_FILE"
    log "✓  git push 완료 (Render 자동 배포 트리거)"
  fi
} >> "$LOG_FILE" 2>&1

log "=========================================="
log "  daily_macbook_cron.sh 종료"
log "=========================================="

# 30일 이상 된 로그 정리
find "$LOG_DIR" -name "cron_*.log" -mtime +30 -delete 2>/dev/null

exit 0
