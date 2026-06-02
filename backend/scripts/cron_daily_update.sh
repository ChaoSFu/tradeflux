#!/bin/bash
# 每日收盘后定时更新脚本（由 crontab 调用）
# 与 UI 手动触发共享文件锁，避免并发执行

BACKEND_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$BACKEND_DIR/logs"
LOG_FILE="$LOG_DIR/daily_update_$(date +%Y-%m-%d).log"
LOCK_FILE="/tmp/tradeflux_daily_update.lock"
PYTHON="$BACKEND_DIR/.venv/bin/python"

mkdir -p "$LOG_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [CRON] $1" | tee -a "$LOG_FILE"
}

log "========================================"
log "定时任务触发，尝试获取锁..."

# 尝试获取排他锁（非阻塞）
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    log "❌ 锁已被占用（UI 手动触发或另一个定时任务正在运行），本次跳过"
    log "========================================"
    exit 0
fi

log "✅ 获取锁成功，开始执行每日数据更新"

cd "$BACKEND_DIR" || { log "❌ 无法进入目录 $BACKEND_DIR"; exit 1; }

"$PYTHON" scripts/daily_update.py >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    log "✅ 每日数据更新完成"
else
    log "❌ 每日数据更新失败，退出码: $EXIT_CODE"
fi

log "========================================"

# 释放锁（脚本退出时自动释放，这里显式关闭 fd）
flock -u 9
exec 9>&-

exit $EXIT_CODE
