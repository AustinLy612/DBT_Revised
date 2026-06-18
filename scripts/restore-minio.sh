#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────
# DBT Platform — MinIO Restore
#
# Usage:
#   ./restore-minio.sh <backup_dir>     # Restore from backup directory
#   ./restore-minio.sh --list           # List backups
# ──────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/backup.conf"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [MinIORestore] $*" | tee -a "${LOG_FILE}"; }

list_backups() {
    echo "=== MinIO backup directories ==="
    if [[ -d "${BACKUP_ROOT}/minio" ]]; then
        ls -lh "${BACKUP_ROOT}/minio/" 2>/dev/null || echo "  (none)"
    fi
}

do_restore() {
    local backup_dir="$1"

    if [[ ! -d "${backup_dir}" ]]; then
        log "ERROR: Backup directory not found: ${backup_dir}"
        exit 1
    fi

    log "WARNING: Restoring MinIO data will OVERWRITE existing objects."
    log "Waiting 5 seconds for Ctrl+C..."
    sleep 5

    if [[ -f "${backup_dir}/minio_data.tar.gz" ]]; then
        # Volume-level tar restore
        log "Restoring from volume tar..."

        docker run --rm \
            -v "dbt_minio_data:/data" \
            -v "${backup_dir}:/backup:ro" \
            alpine:latest \
            sh -c "rm -rf /data/* /data/.* 2>/dev/null; tar xzf /backup/minio_data.tar.gz -C /data"

        log "MinIO volume data restored."
    else
        # mc mirror restore
        log "Restoring via mc mirror..."

        docker exec "${MINIO_CONTAINER}" sh -c "
            mc alias set local http://localhost:9000 \${MINIO_ROOT_USER} \${MINIO_ROOT_PASSWORD} > /dev/null 2>&1
            mc mirror --overwrite /tmp/minio_restore/ local/${MINIO_BUCKET}/
        "

        # First copy backup into container
        docker cp "${backup_dir}/." "${MINIO_CONTAINER}:/tmp/minio_restore/"

        docker exec "${MINIO_CONTAINER}" sh -c "
            mc alias set local http://localhost:9000 \${MINIO_ROOT_USER} \${MINIO_ROOT_PASSWORD} > /dev/null 2>&1
            mc mirror --overwrite /tmp/minio_restore/ local/${MINIO_BUCKET}/
            rm -rf /tmp/minio_restore/
        "

        log "MinIO data restored via mc mirror."
    fi

    log "MinIO restore complete."
}

# ── Main ──
case "${1:-}" in
    --list)
        list_backups
        ;;
    *)
        if [[ -z "${1:-}" ]]; then
            echo "Usage: $0 <backup_dir> | --list"
            exit 1
        fi
        do_restore "$1"
        ;;
esac
