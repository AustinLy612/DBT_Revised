#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────
# DBT Platform — Backup Orchestrator
#
# Run full backups:   ./backup.sh
# Run incremental:    ./backup.sh --incremental
# Dry-run (no actual backup):  ./backup.sh --dry-run
#
# What gets backed up:
#   1. MongoDB          — full: mongodump + gzip, incr: oplog
#   2. Qdrant           — snapshot API
#   3. MinIO            — mc mirror (fallback: volume tar)
#   4. Redis            — RDB dump (best-effort)
#   5. App config       — .env, docker-compose.yml, nginx.conf
#   6. Application logs — /app/logs/
#
# Backup layout:
#   /backup/
#   ├── mongodb/full/YYYYMMDD_HHMMSS/
#   ├── mongodb/incremental/YYYYMMDD_HHMMSS/
#   ├── qdrant/YYYYMMDD_HHMMSS/
#   ├── minio/YYYYMMDD_HHMMSS/
#   ├── redis/YYYYMMDD_HHMMSS/
#   ├── config/YYYYMMDD_HHMMSS/
#   └── logs/YYYYMMDD_HHMMSS/
#
# Scheduled via cron (daily full + hourly incremental):
#   0  2 * * * /root/program/DBT/scripts/backup.sh           # Full at 2am
#   0  * * * * /root/program/DBT/scripts/backup.sh --incremental  # Incremental hourly
# ──────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/backup.conf"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
MODE="${1:---full}"
DRY_RUN=false

if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=true
    MODE="--full"
elif [[ "${1:-}" == "--incremental" ]]; then
    MODE="--incremental"
fi

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [Backup] $*" | tee -a "${LOG_FILE}"; }

die() {
    log "FATAL: $*"
    exit 1
}

# ── 1. Application Configuration ──
backup_config() {
    local backup_dir="${BACKUP_ROOT}/config/${TIMESTAMP}"
    mkdir -p "${backup_dir}"

    log "Backing up app configuration..."
    for f in "${APP_FILES_TO_BACKUP[@]}"; do
        if [[ -f "${PROJECT_ROOT}/${f}" ]]; then
            cp "${PROJECT_ROOT}/${f}" "${backup_dir}/$(basename "${f}")"
            log "  → ${f}"
        fi
    done

    # Also save a list of installed pip packages
    pip freeze > "${backup_dir}/requirements.frozen.txt" 2>/dev/null || true

    echo "${backup_dir}"
}

# ── 2. Application Logs ──
backup_logs() {
    local backup_dir="${BACKUP_ROOT}/logs/${TIMESTAMP}"
    mkdir -p "${backup_dir}"

    if [[ -d "${PROJECT_ROOT}/logs" ]]; then
        log "Backing up application logs..."
        tar czf "${backup_dir}/logs.tar.gz" -C "${PROJECT_ROOT}" logs/ 2>/dev/null || true
        local size
        size=$(du -sh "${backup_dir}/logs.tar.gz" 2>/dev/null | cut -f1)
        log "  → ${backup_dir}/logs.tar.gz (${size:-0})"
    fi

    echo "${backup_dir}"
}

# ── 3. Cleanup old backups (config + logs) ──
cleanup_misc() {
    for type in config logs; do
        local dir="${BACKUP_ROOT}/${type}"
        if [[ -d "${dir}" ]]; then
            local deleted
            deleted=$(find "${dir}" -maxdepth 1 -type d -mtime "+${RETENTION_FULL_DAYS}" ! -path "${dir}" -print -delete | wc -l)
            if [[ "${deleted}" -gt 0 ]]; then
                log "Cleaned up ${deleted} old ${type} backup(s) older than ${RETENTION_FULL_DAYS} days"
            fi
        fi
    done
}

# ── Main ──
log "=========================================="
log "DBT Platform Backup START — ${TIMESTAMP} — mode: ${MODE}"
log "=========================================="

if [[ "${DRY_RUN}" == "true" ]]; then
    log "DRY RUN — no actual backups will be performed"
    log "Would back up: MongoDB, Qdrant, MinIO, Redis, Config, Logs"
    log "Backup root: ${BACKUP_ROOT}"
    exit 0
fi

# Ensure backup root exists
mkdir -p "${BACKUP_ROOT}"

FAILURES=()

# Always run these (lightweight)
backup_config || FAILURES+=("config")
backup_logs || FAILURES+=("logs")
cleanup_misc

if [[ "${MODE}" == "--full" ]]; then
    # Full backup: all services
    log "Running FULL backup for all services..."

    bash "${SCRIPT_DIR}/backup-mongodb.sh" --full || FAILURES+=("mongodb-full")
    bash "${SCRIPT_DIR}/backup-qdrant.sh" || FAILURES+=("qdrant")
    bash "${SCRIPT_DIR}/backup-minio.sh" || FAILURES+=("minio")
    bash "${SCRIPT_DIR}/backup-redis.sh" || FAILURES+=("redis")

elif [[ "${MODE}" == "--incremental" ]]; then
    # Incremental: MongoDB oplog only (the only service that supports true incrementals)
    log "Running INCREMENTAL backup (MongoDB oplog)..."

    bash "${SCRIPT_DIR}/backup-mongodb.sh" --incremental || FAILURES+=("mongodb-incr")

else
    die "Unknown mode: ${MODE}"
fi

# ── Report ──
log "=========================================="
if [[ ${#FAILURES[@]} -eq 0 ]]; then
    log "Backup SUCCESS — all components backed up"
else
    log "Backup COMPLETED WITH WARNINGS — failures: ${FAILURES[*]}"
fi

# Disk usage summary
log "Backup disk usage:"
df -h "${BACKUP_ROOT}" 2>/dev/null | tail -1 || true
du -sh "${BACKUP_ROOT}" 2>/dev/null || true

log "=========================================="
