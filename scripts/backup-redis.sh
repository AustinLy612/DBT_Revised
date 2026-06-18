#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────
# DBT Platform — Redis Backup
#
# Triggers BGSAVE and copies the RDB dump file.
# Redis is a cache/broker, so this is best-effort — the
# primary source of truth is MongoDB.
#
# Usage:
#   ./backup-redis.sh
# ──────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/backup.conf"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [Redis] $*" | tee -a "${LOG_FILE}"; }

do_backup() {
    local backup_dir="${BACKUP_ROOT}/redis/${TIMESTAMP}"
    mkdir -p "${backup_dir}"

    log "Triggering Redis BGSAVE..."

    docker exec "${REDIS_CONTAINER}" redis-cli BGSAVE > /dev/null 2>&1 || {
        log "WARNING: Redis BGSAVE failed — Redis may not be running or accessible"
        return 0
    }

    # Wait for BGSAVE to complete
    local waited=0
    while [[ ${waited} -lt 30 ]]; do
        local status
        status=$(docker exec "${REDIS_CONTAINER}" redis-cli LASTSAVE 2>/dev/null || echo "0")
        if [[ -n "${status}" && "${status}" != "0" ]]; then
            break
        fi
        sleep 1
        waited=$((waited + 1))
    done

    # Copy RDB dump
    docker cp "${REDIS_CONTAINER}:/data/dump.rdb" "${backup_dir}/dump.rdb" 2>/dev/null || {
        log "WARNING: Could not copy Redis dump — continuing"
        return 0
    }

    local size
    size=$(du -sh "${backup_dir}/dump.rdb" | cut -f1)
    log "Redis backup complete: ${backup_dir}/dump.rdb (${size})"

    echo "${backup_dir}"
}

cleanup_old_backups() {
    local dir="${BACKUP_ROOT}/redis"
    if [[ ! -d "${dir}" ]]; then
        return
    fi

    local deleted
    deleted=$(find "${dir}" -maxdepth 1 -type d -mtime "+${RETENTION_FULL_DAYS}" ! -path "${dir}" -print -delete | wc -l)

    if [[ "${deleted}" -gt 0 ]]; then
        log "Cleaned up ${deleted} old Redis backup(s) older than ${RETENTION_FULL_DAYS} days"
    fi
}

# ── Main ──
mkdir -p "${BACKUP_ROOT}/redis"
do_backup
cleanup_old_backups
