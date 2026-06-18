#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────
# DBT Platform — MongoDB Restore
#
# Usage:
#   ./restore-mongodb.sh <backup_archive.gz>     # restore full
#   ./restore-mongodb.sh --list                   # list backups
#   ./restore-mongodb.sh --point-in-time <full_archive> <oplog_archive>  # PITR
# ──────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/backup.conf"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [MongoRestore] $*" | tee -a "${LOG_FILE}"; }

list_backups() {
    echo "=== Full backups ==="
    if [[ -d "${BACKUP_ROOT}/mongodb/full" ]]; then
        find "${BACKUP_ROOT}/mongodb/full" -name "*.archive.gz" -exec ls -lh {} \; 2>/dev/null || echo "  (none)"
    fi
    echo ""
    echo "=== Incremental (oplog) backups ==="
    if [[ -d "${BACKUP_ROOT}/mongodb/incremental" ]]; then
        find "${BACKUP_ROOT}/mongodb/incremental" -name "*.archive.gz" -exec ls -lh {} \; 2>/dev/null || echo "  (none)"
    fi
}

do_restore_full() {
    local archive_file="$1"

    if [[ ! -f "${archive_file}" ]]; then
        log "ERROR: Archive not found: ${archive_file}"
        exit 1
    fi

    log "WARNING: This will DROP all existing data in '${MONGO_DB}' and replace with backup."
    log "Waiting 5 seconds for Ctrl+C..."
    sleep 5

    log "Restoring from: ${archive_file}"

    gunzip -c "${archive_file}" | docker exec -i "${MONGO_CONTAINER}" \
        mongorestore \
            --host localhost \
            --username "${MONGO_USER}" \
            --password "${MONGO_PASSWORD}" \
            --authenticationDatabase "${MONGO_AUTH_DB}" \
            --db "${MONGO_DB}" \
            --archive \
            --gzip \
            --drop

    log "Restore complete from: ${archive_file}"
}

do_pitr_restore() {
    local full_archive="$1"
    local oplog_archive="$2"

    if [[ ! -f "${full_archive}" ]]; then
        log "ERROR: Full archive not found: ${full_archive}"
        exit 1
    fi
    if [[ ! -f "${oplog_archive}" ]]; then
        log "ERROR: Oplog archive not found: ${oplog_archive}"
        exit 1
    fi

    log "Point-in-time restore: full + oplog replay"

    # Step 1: Restore full backup with --oplogReplay
    log "Step 1/2: Restoring full backup..."
    gunzip -c "${full_archive}" | docker exec -i "${MONGO_CONTAINER}" \
        mongorestore \
            --host localhost \
            --username "${MONGO_USER}" \
            --password "${MONGO_PASSWORD}" \
            --authenticationDatabase "${MONGO_AUTH_DB}" \
            --archive \
            --gzip \
            --drop

    # Step 2: Replay oplog
    log "Step 2/2: Replaying oplog entries..."
    gunzip -c "${oplog_archive}" | docker exec -i "${MONGO_CONTAINER}" \
        mongorestore \
            --host localhost \
            --username "${MONGO_USER}" \
            --password "${MONGO_PASSWORD}" \
            --authenticationDatabase "${MONGO_AUTH_DB}" \
            --oplogReplay \
            --archive \
            --gzip

    log "Point-in-time restore complete."
}

# ── Main ──
case "${1:-}" in
    --list)
        list_backups
        ;;
    --point-in-time|--pitr)
        if [[ $# -lt 3 ]]; then
            echo "Usage: $0 --point-in-time <full_archive.gz> <oplog_archive.gz>"
            exit 1
        fi
        do_pitr_restore "$2" "$3"
        ;;
    *.gz|*.archive)
        do_restore_full "$1"
        ;;
    *)
        echo "Usage:"
        echo "  $0 <backup_archive.gz>              Restore full backup"
        echo "  $0 --point-in-time <full> <oplog>   Point-in-time restore"
        echo "  $0 --list                            List available backups"
        exit 1
        ;;
esac
