#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────
# DBT Platform — MongoDB Backup (Full + Incremental)
#
# Full backup:     mongodump --archive with gzip
# Incremental:     mongodump --oplog (captures oplog during
#                  the dump, enabling point-in-time restore)
#
# Usage:
#   ./backup-mongodb.sh             # full backup
#   ./backup-mongodb.sh --full       # full backup
#   ./backup-mongodb.sh --incremental  # incremental (oplog)
# ──────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/backup.conf"

MODE="${1:---full}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
# Business collections expected in a real database (not a fresh/empty one)
readonly MIN_EXPECTED_COLLECTIONS=15

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [MongoDB] $*" | tee -a "${LOG_FILE}"; }

# Verify we're targeting the correct MongoDB instance before backing up.
# The host also runs a system mongod (port 27017) which is stale/empty.
preflight_check() {
    local collection_count
    collection_count=$(docker exec "${MONGO_CONTAINER}" \
        mongosh --quiet \
            "mongodb://${MONGO_USER}:${MONGO_PASSWORD}@localhost:27017/${MONGO_DB}?authSource=${MONGO_AUTH_DB}" \
            --eval "print(db.getCollectionNames().length)" 2>/dev/null || echo "0")

    # Strip whitespace
    collection_count=$(echo "${collection_count}" | tr -d '[:space:]')

    if [[ "${collection_count}" -lt "${MIN_EXPECTED_COLLECTIONS}" ]]; then
        log "ERROR: Target MongoDB container '${MONGO_CONTAINER}' has only ${collection_count} collections."
        log "       Expected >= ${MIN_EXPECTED_COLLECTIONS}. This may be an empty instance!"
        log "       The host also runs a system mongod (port 27017) — ensure we're backing up"
        log "       the Docker MongoDB (dbt-mongodb-1, reachable via docker exec on port 27018 from host)."
        log "       Aborting backup to avoid overwriting good backups with empty data."
        return 1
    fi

    log "Preflight OK: ${collection_count} collections in ${MONGO_DB} on ${MONGO_CONTAINER}"
}

do_full_backup() {
    local backup_dir="${BACKUP_ROOT}/mongodb/full/${TIMESTAMP}"
    local archive_file="${backup_dir}/dbt_platform.archive.gz"

    mkdir -p "${backup_dir}"

    log "Starting FULL backup → ${archive_file}"

    docker exec "${MONGO_CONTAINER}" \
        mongodump \
            --host localhost \
            --username "${MONGO_USER}" \
            --password "${MONGO_PASSWORD}" \
            --authenticationDatabase "${MONGO_AUTH_DB}" \
            --db "${MONGO_DB}" \
            --archive \
            --gzip \
        2>&1 | gzip > "${archive_file}"

    # Metadata: full collection list with document counts
    docker exec "${MONGO_CONTAINER}" \
        mongosh --quiet \
            "mongodb://${MONGO_USER}:${MONGO_PASSWORD}@localhost:27017/${MONGO_DB}?authSource=${MONGO_AUTH_DB}" \
            --eval "
                db.getCollectionNames().forEach(function(c) {
                    var cnt = db.getCollection(c).countDocuments({});
                    print(cnt + '  ' + c);
                });
            " \
        > "${backup_dir}/collections.txt" 2>/dev/null || true

    local size
    size=$(du -sh "${archive_file}" | cut -f1)
    log "FULL backup complete: ${archive_file} (${size})"

    echo "${backup_dir}"
}

do_incremental_backup() {
    local backup_dir="${BACKUP_ROOT}/mongodb/incremental/${TIMESTAMP}"
    local oplog_file="${backup_dir}/oplog.archive.gz"

    mkdir -p "${backup_dir}"

    log "Starting INCREMENTAL (oplog) backup → ${oplog_file}"

    # mongodump --oplog captures all oplog entries that occur
    # during the dump window, enabling replay to a specific point in time
    docker exec "${MONGO_CONTAINER}" \
        mongodump \
            --host localhost \
            --username "${MONGO_USER}" \
            --password "${MONGO_PASSWORD}" \
            --authenticationDatabase "${MONGO_AUTH_DB}" \
            --db local \
            --collection oplog.rs \
            --query '{"ts":{"$gt":Timestamp('"$(date -d '24 hours ago' +%s)"',0)}}' \
            --archive \
            --gzip \
        2>&1 | gzip > "${oplog_file}"

    local size
    size=$(du -sh "${oplog_file}" | cut -f1)
    log "INCREMENTAL backup complete: ${oplog_file} (${size})"

    echo "${backup_dir}"
}

cleanup_old_backups() {
    local type="$1"  # full or incremental
    local retention_days="$2"

    local dir="${BACKUP_ROOT}/mongodb/${type}"
    if [[ ! -d "${dir}" ]]; then
        return
    fi

    local deleted
    deleted=$(find "${dir}" -maxdepth 1 -type d -mtime "+${retention_days}" ! -path "${dir}" -print -delete | wc -l)

    if [[ "${deleted}" -gt 0 ]]; then
        log "Cleaned up ${deleted} old ${type} backup(s) older than ${retention_days} days"
    fi
}

# ── Main ──
mkdir -p "${BACKUP_ROOT}/mongodb/full" "${BACKUP_ROOT}/mongodb/incremental"

if ! preflight_check; then
    log "Preflight check FAILED — backup aborted."
    exit 1
fi

case "${MODE}" in
    --full|full)
        do_full_backup
        cleanup_old_backups "full" "${RETENTION_FULL_DAYS}"
        ;;
    --incremental|incremental)
        do_incremental_backup
        cleanup_old_backups "incremental" "${RETENTION_INCREMENTAL_DAYS}"
        ;;
    *)
        log "ERROR: Unknown mode '${MODE}'. Use --full or --incremental."
        exit 1
        ;;
esac
