#!/bin/bash
# Certbot post-renewal hook for DBT Platform
# Copies renewed certificates to docker/certs/ and restarts nginx

set -e

CERT_DIR="/etc/letsencrypt/live/genaidbt.top"
DOCKER_CERT_DIR="/root/program/DBT/docker/certs"

if [ -f "$CERT_DIR/fullchain.pem" ] && [ -f "$CERT_DIR/privkey.pem" ]; then
    cp "$CERT_DIR/fullchain.pem" "$DOCKER_CERT_DIR/fullchain.pem"
    cp "$CERT_DIR/privkey.pem" "$DOCKER_CERT_DIR/privkey.pem"
    chmod 644 "$DOCKER_CERT_DIR/fullchain.pem"
    chmod 600 "$DOCKER_CERT_DIR/privkey.pem"

    # Restart nginx container to pick up new certs
    cd /root/program/DBT
    docker compose restart nginx 2>&1 || true

    echo "[$(date)] Certificates renewed and nginx restarted"
else
    echo "[$(date)] ERROR: Certificate files not found" >&2
    exit 1
fi
