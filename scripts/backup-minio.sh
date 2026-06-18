#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────
# DBT Platform — MinIO Backup
#
# Uses mc (MinIO Client) to mirror the bucket. Falls back
# to volume-level tar if mc is unavailable.
#
# Usage:
#   ./backup-minio.sh
# ──────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/backup.conf"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [MinIO] $*" | tee -a "${LOG_FILE}"; }

do_mc_mirror() {
    # Configure mc alias inside container
    local backup_dir="${BACKUP_ROOT}/minio/${TIMESTAMP}"
    mkdir -p "${backup_dir}"

    log "Using mc mirror to backup MinIO bucket '${MINIO_BUCKET}'..."

    # Use mc from the minio container (it's built in)
    docker exec "${MINIO_CONTAINER}" sh -c "
        mc alias set local http://localhost:9000 \${MINIO_ROOT_USER} \${MINIO_ROOT_PASSWORD} > /dev/null 2>&1
        mc mirror --overwrite local/${MINIO_BUCKET} /tmp/minio_backup/
    " || {
        log "WARNING: mc mirror inside container failed, falling back to volume tar"
        return 1
    }

    # Copy out of container
    docker cp "${MINIO_CONTAINER}:/tmp/minio_backup/." "${backup_dir}/"
    docker exec "${MINIO_CONTAINER}" rm -rf /tmp/minio_backup/

    local size
    size=$(du -sh "${backup_dir}" | cut -f1)
    log "MinIO backup complete: ${backup_dir} (${size})"

    echo "${backup_dir}"
}

do_volume_tar() {
    local backup_dir="${BACKUP_ROOT}/minio/${TIMESTAMP}"
    mkdir -p "${backup_dir}"
    local tar_file="${backup_dir}/minio_data.tar.gz"

    log "Backing up MinIO data via volume tar..."

    # Docker named volume: dbt_minio_data
    docker run --rm \
        -v "dbt_minio_data:/data:ro" \
        -v "${backup_dir}:/backup" \
        alpine:latest \
        tar czf "/backup/minio_data.tar.gz" -C /data .

    local size
    size=$(du -sh "${tar_file}" | cut -f1)
    log "MinIO volume backup complete: ${tar_file} (${size})"

    echo "${backup_dir}"
}

cleanup_old_backups() {
    local dir="${BACKUP_ROOT}/minio"
    if [[ ! -d "${dir}" ]]; then
        return
    fi

    local deleted
    deleted=$(find "${dir}" -maxdepth 1 -type d -mtime "+${RETENTION_FULL_DAYS}" ! -path "${dir}" -print -delete | wc -l)

    if [[ "${deleted}" -gt 0 ]]; then
        log "Cleaned up ${deleted} old MinIO backup(s) older than ${RETENTION_FULL_DAYS} days"
    fi
}

# ── Main ──
mkdir -p "${BACKUP_ROOT}/minio"

if ! do_mc_mirror; then
    do_volume_tar
fi

cleanup_old_backups
