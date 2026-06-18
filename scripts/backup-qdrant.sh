#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────
# DBT Platform — Qdrant Snapshot Backup
#
# Uses Qdrant's native snapshot API. Snapshots are
# collection-level and provide crash-consistent backups.
#
# Usage:
#   ./backup-qdrant.sh              # Create + download snapshot
#   ./backup-qdrant.sh --create-only  # Only create snapshot
# ──────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/backup.conf"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
QDRANT_API="http://127.0.0.1:${QDRANT_PORT}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [Qdrant] $*" | tee -a "${LOG_FILE}"; }

create_snapshot() {
    local collection="$1"

    log "Creating snapshot for collection '${collection}'..."

    local response
    response=$(curl -s -X POST \
        "${QDRANT_API}/collections/${collection}/snapshots" \
        -H "Content-Type: application/json" \
        -w "\n%{http_code}" 2>&1)

    local http_code
    http_code=$(echo "${response}" | tail -1)

    if [[ "${http_code}" != "200" && "${http_code}" != "202" ]]; then
        log "ERROR: Snapshot creation failed (HTTP ${http_code}): ${response}"
        return 1
    fi

    log "Snapshot created successfully for '${collection}'"
}

download_snapshots() {
    local backup_dir="${BACKUP_ROOT}/qdrant/${TIMESTAMP}"
    mkdir -p "${backup_dir}"

    local collection="$1"

    # List snapshots
    local snapshots
    snapshots=$(curl -s "${QDRANT_API}/collections/${collection}/snapshots" | \
        python3 -c "import sys,json; data=json.load(sys.stdin); [print(s['name']) for s in data.get('result',[])]" 2>/dev/null)

    if [[ -z "${snapshots}" ]]; then
        log "WARNING: No snapshots found for collection '${collection}'"
        return 1
    fi

    # Download the latest snapshot
    local latest
    latest=$(echo "${snapshots}" | tail -1)

    log "Downloading snapshot: ${latest}"
    curl -s -o "${backup_dir}/${collection}.snapshot" \
        "${QDRANT_API}/collections/${collection}/snapshots/${latest}"

    local size
    size=$(du -sh "${backup_dir}/${collection}.snapshot" | cut -f1)
    log "Qdrant snapshot downloaded: ${backup_dir}/${collection}.snapshot (${size})"

    # Save collection info for reference
    curl -s "${QDRANT_API}/collections/${collection}" | \
        python3 -c "
import sys, json
c = json.load(sys.stdin)['result']
print(f\"vectors: {c['config']['params']['vectors']}\")
print(f\"points_count: {c.get('points_count', 'unknown')}\")
print(f\"indexed_vectors_count: {c.get('indexed_vectors_count', 'unknown')}\")
" > "${backup_dir}/collection_info.txt" 2>/dev/null || true

    echo "${backup_dir}"
}

cleanup_old_backups() {
    local dir="${BACKUP_ROOT}/qdrant"
    if [[ ! -d "${dir}" ]]; then
        return
    fi

    local deleted
    deleted=$(find "${dir}" -maxdepth 1 -type d -mtime "+${RETENTION_FULL_DAYS}" ! -path "${dir}" -print -delete | wc -l)

    if [[ "${deleted}" -gt 0 ]]; then
        log "Cleaned up ${deleted} old Qdrant backup(s) older than ${RETENTION_FULL_DAYS} days"
    fi
}

# ── Main ──
mkdir -p "${BACKUP_ROOT}/qdrant"

# Check Qdrant is reachable
if ! curl -s "${QDRANT_API}/health" > /dev/null 2>&1; then
    log "ERROR: Cannot reach Qdrant at ${QDRANT_API}"
    exit 1
fi

create_snapshot "${QDRANT_COLLECTION}"
download_snapshots "${QDRANT_COLLECTION}"
cleanup_old_backups
