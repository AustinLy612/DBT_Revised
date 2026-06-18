#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────
# DBT Platform — Qdrant Snapshot Restore
#
# Usage:
#   ./restore-qdrant.sh <snapshot_file>    # Restore from snapshot
#   ./restore-qdrant.sh --list             # List available snapshots
# ──────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/backup.conf"

QDRANT_API="http://127.0.0.1:${QDRANT_PORT}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [QdrantRestore] $*" | tee -a "${LOG_FILE}"; }

list_backups() {
    echo "=== Qdrant backup directories ==="
    if [[ -d "${BACKUP_ROOT}/qdrant" ]]; then
        find "${BACKUP_ROOT}/qdrant" -name "*.snapshot" -exec ls -lh {} \; 2>/dev/null || echo "  (none)"
    fi
}

do_restore() {
    local snapshot_file="$1"

    if [[ ! -f "${snapshot_file}" ]]; then
        log "ERROR: Snapshot file not found: ${snapshot_file}"
        exit 1
    fi

    log "WARNING: This will REPLACE the '${QDRANT_COLLECTION}' collection in Qdrant."
    log "Waiting 5 seconds for Ctrl+C..."
    sleep 5

    # Qdrant snapshot restore: upload the snapshot file, then recover from it
    # First, upload the snapshot
    log "Uploading snapshot to Qdrant..."

    local snapshot_name
    snapshot_name=$(basename "${snapshot_file}")

    curl -s -X POST \
        "${QDRANT_API}/collections/${QDRANT_COLLECTION}/snapshots/upload" \
        -F "snapshot=@${snapshot_file}" \
        -H "Content-Type: multipart/form-data"

    log "Snapshot uploaded. Recovering collection..."

    # Recover from snapshot
    curl -s -X PUT \
        "${QDRANT_API}/collections/${QDRANT_COLLECTION}/snapshots/recover" \
        -H "Content-Type: application/json" \
        -d "{\"location\": \"${snapshot_name}\"}"

    log "Restore initiated. Collection '${QDRANT_COLLECTION}' will be recovered."
    log "Check status with: curl ${QDRANT_API}/collections/${QDRANT_COLLECTION}"
}

# ── Main ──
case "${1:-}" in
    --list)
        list_backups
        ;;
    *.snapshot)
        do_restore "$1"
        ;;
    *)
        echo "Usage:"
        echo "  $0 <snapshot_file>    Restore Qdrant collection from snapshot"
        echo "  $0 --list             List available snapshots"
        exit 1
        ;;
esac
