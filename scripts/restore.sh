#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────
# DBT Platform — Restore Orchestrator
#
# Interactive restore from a backup point.
#
# Usage:
#   ./restore.sh                          # Interactive: choose a backup
#   ./restore.sh <timestamp>              # Restore everything from timestamp
#   ./restore.sh <timestamp> --mongodb-only  # Restore only MongoDB
#   ./restore.sh --list                   # List all available backup points
#
# A backup "point" is defined by the timestamp (YYYYMMDD_HHMMSS).
# All component backups with the same or nearest-earlier timestamp
# will be used.
# ──────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/backup.conf"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [Restore] $*" | tee -a "${LOG_FILE}"; }

list_backup_points() {
    echo ""
    echo "=== Available MongoDB Full Backups ==="
    if [[ -d "${BACKUP_ROOT}/mongodb/full" ]]; then
        ls -1tr "${BACKUP_ROOT}/mongodb/full/" 2>/dev/null || echo "  (none)"
    fi
    echo ""
    echo "=== Available MongoDB Incremental Backups ==="
    if [[ -d "${BACKUP_ROOT}/mongodb/incremental" ]]; then
        ls -1tr "${BACKUP_ROOT}/mongodb/incremental/" 2>/dev/null || echo "  (none)"
    fi
    echo ""
    echo "=== Available Qdrant Backups ==="
    if [[ -d "${BACKUP_ROOT}/qdrant" ]]; then
        ls -1tr "${BACKUP_ROOT}/qdrant/" 2>/dev/null || echo "  (none)"
    fi
    echo ""
    echo "=== Available MinIO Backups ==="
    if [[ -d "${BACKUP_ROOT}/minio" ]]; then
        ls -1tr "${BACKUP_ROOT}/minio/" 2>/dev/null || echo "  (none)"
    fi
}

find_nearest_full() {
    local target_ts="$1"
    local dir="${BACKUP_ROOT}/mongodb/full"

    if [[ ! -d "${dir}" ]]; then
        echo ""
        return
    fi

    # Find the nearest full backup at or before the target timestamp
    ls -1tr "${dir}/" 2>/dev/null | awk -v target="${target_ts}" '$1 <= target' | tail -1
}

confirm() {
    local prompt="$1"
    echo ""
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║  WARNING: Restore will OVERWRITE current data!          ║"
    echo "║  The platform may need to be stopped first.             ║"
    echo "╚══════════════════════════════════════════════════════════╝"
    echo ""
    read -r -p "${prompt} [y/N]: " response
    case "${response}" in
        [yY][eE][sS]|[yY]) return 0 ;;
        *) return 1 ;;
    esac
}

do_full_restore() {
    local ts="$1"
    local scope="${2:-all}"  # all, mongodb-only

    log "Starting restore for timestamp: ${ts} (scope: ${scope})"

    # Stop the platform to ensure data consistency during restore
    log "Stopping the platform services..."
    cd "${PROJECT_ROOT}"
    docker compose down 2>/dev/null || true
    sleep 3

    # ── MongoDB ──
    if [[ "${scope}" == "all" || "${scope}" == "mongodb-only" ]]; then
        local mongo_full
        mongo_full="${BACKUP_ROOT}/mongodb/full/${ts}/dbt_platform.archive.gz"
        if [[ -f "${mongo_full}" ]]; then
            # Start only MongoDB for restore
            docker compose up -d mongodb 2>/dev/null
            sleep 5
            bash "${SCRIPT_DIR}/restore-mongodb.sh" "${mongo_full}"
        else
            # Try to find nearest
            local nearest
            nearest=$(find_nearest_full "${ts}")
            if [[ -n "${nearest}" ]]; then
                mongo_full="${BACKUP_ROOT}/mongodb/full/${nearest}/dbt_platform.archive.gz"
                log "Using nearest full backup: ${nearest}"
                docker compose up -d mongodb 2>/dev/null
                sleep 5
                bash "${SCRIPT_DIR}/restore-mongodb.sh" "${mongo_full}"
            else
                log "WARNING: No MongoDB full backup found for ${ts}"
            fi
        fi
    fi

    # ── Qdrant ──
    if [[ "${scope}" == "all" ]]; then
        local qdrant_snapshot
        qdrant_snapshot=$(find "${BACKUP_ROOT}/qdrant/${ts}/" -name "*.snapshot" 2>/dev/null | head -1 || echo "")
        if [[ -n "${qdrant_snapshot}" && -f "${qdrant_snapshot}" ]]; then
            docker compose up -d qdrant 2>/dev/null
            sleep 5
            bash "${SCRIPT_DIR}/restore-qdrant.sh" "${qdrant_snapshot}"
        else
            log "WARNING: No Qdrant backup found for ${ts} — skipping"
        fi
    fi

    # ── MinIO ──
    if [[ "${scope}" == "all" ]]; then
        if [[ -d "${BACKUP_ROOT}/minio/${ts}" ]]; then
            docker compose up -d minio 2>/dev/null
            sleep 5
            bash "${SCRIPT_DIR}/restore-minio.sh" "${BACKUP_ROOT}/minio/${ts}"
        else
            log "WARNING: No MinIO backup found for ${ts} — skipping"
        fi
    fi

    # ── Redis (just restart, it'll rebuild from scratch) ──
    if [[ "${scope}" == "all" ]]; then
        log "Redis will start fresh (cache/broker — acceptable)"
    fi

    # ── Start everything ──
    log "Starting the full platform..."
    docker compose up -d
    sleep 10

    log "=========================================="
    log "Restore complete. Verify at https://<domain>:10443/"
    log "Check health:  curl -k https://localhost:10443/health/"
    log "=========================================="
}

# ── Main ──
case "${1:-}" in
    --list)
        list_backup_points
        exit 0
        ;;
    "")
        list_backup_points
        echo ""
        read -r -p "Enter timestamp to restore (or Ctrl+C to cancel): " ts
        if [[ -z "${ts}" ]]; then
            echo "No timestamp entered. Exiting."
            exit 0
        fi
        scope="all"
        if [[ "${2:-}" == "--mongodb-only" ]]; then
            scope="mongodb-only"
        fi
        ;;
    *)
        ts="$1"
        scope="all"
        if [[ "${2:-}" == "--mongodb-only" ]]; then
            scope="mongodb-only"
        fi
        ;;
esac

if ! confirm "Restore platform data from backup point '${ts}'?"; then
    log "Restore cancelled by user."
    exit 0
fi

do_full_restore "${ts}" "${scope}"
