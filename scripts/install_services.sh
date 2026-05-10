#!/bin/bash
# Install and configure MinIO and Qdrant for DBT Platform.
# Uses domestic mirrors by default to avoid slow GitHub/MinIO downloads.

set -euo pipefail

MINIO_VERSION="${MINIO_VERSION:-RELEASE.2024-12-18T13-15-44Z}"
MINIO_URL="${MINIO_URL:-https://dl.minio.org.cn/server/minio/release/linux-amd64/archive/minio.${MINIO_VERSION}}"
QDRANT_URL="${QDRANT_URL:-https://ghfast.top/https://github.com/qdrant/qdrant/releases/latest/download/qdrant-x86_64-unknown-linux-gnu.tar.gz}"
PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
QDRANT_CONFIG="${QDRANT_CONFIG:-/etc/qdrant-local.yaml}"

download() {
  local url="$1"
  local out="$2"
  wget -q -c -O "$out" "$url"
}

echo "=== Installing MinIO ==="
download "$MINIO_URL" /tmp/minio
install -m 0755 /tmp/minio /usr/local/bin/minio
rm -f /tmp/minio
/usr/local/bin/minio --version

echo "=== Installing Qdrant ==="
rm -rf /tmp/qdrant-extract
mkdir -p /tmp/qdrant-extract
download "$QDRANT_URL" /tmp/qdrant.tar.gz
tar -xzf /tmp/qdrant.tar.gz -C /tmp/qdrant-extract
install -m 0755 /tmp/qdrant-extract/qdrant /usr/local/bin/qdrant
rm -rf /tmp/qdrant-extract /tmp/qdrant.tar.gz
/usr/local/bin/qdrant --version

echo "=== Starting MinIO ==="
mkdir -p /data/minio /var/log
if ! ss -lnt | grep -q ':9000 '; then
  nohup env MINIO_ROOT_USER=minioadmin MINIO_ROOT_PASSWORD=minioadmin \
    /usr/local/bin/minio server /data/minio --address ":9000" --console-address ":9001" \
    > /var/log/minio.log 2>&1 &
fi
for _ in $(seq 1 20); do
  if curl -fsS http://localhost:9000/minio/health/live >/dev/null; then
    break
  fi
  sleep 1
done

echo "=== Creating MinIO bucket ==="
python3 -m pip install -q -i "$PIP_INDEX_URL" minio
python3 <<'PY'
from minio import Minio

client = Minio(
    "localhost:9000",
    access_key="minioadmin",
    secret_key="minioadmin",
    secure=False,
)
bucket = "dbt-platform"
if not client.bucket_exists(bucket):
    client.make_bucket(bucket)
print(f"MinIO bucket {bucket} ready")
PY

echo "=== Starting Qdrant ==="
mkdir -p /data/qdrant/storage /data/qdrant/snapshots /data/qdrant/tmp
cat > "$QDRANT_CONFIG" <<'EOF'
log_level: INFO
storage:
  storage_path: /data/qdrant/storage
  snapshots_path: /data/qdrant/snapshots
  temp_path: /data/qdrant/tmp
service:
  host: 0.0.0.0
  http_port: 6333
  grpc_port: 6334
cluster:
  enabled: false
telemetry_disabled: true
EOF
if ! ss -lnt | grep -q ':6333 '; then
  nohup /usr/local/bin/qdrant --config-path "$QDRANT_CONFIG" > /var/log/qdrant.log 2>&1 &
fi
for _ in $(seq 1 20); do
  if curl -fsS http://localhost:6333/healthz >/dev/null; then
    break
  fi
  sleep 1
done

echo "=== Verifying ==="
curl -fsS http://localhost:9000/minio/health/live >/dev/null && echo "MinIO OK"
curl -fsS http://localhost:6333/healthz >/dev/null && echo "Qdrant OK"

echo "=== Done ==="
echo "MinIO API:     http://localhost:9000"
echo "MinIO Console: http://localhost:9001 (minioadmin / minioadmin)"
echo "Qdrant:        http://localhost:6333"
