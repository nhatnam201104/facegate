#!/usr/bin/env bash
# Khoi chay MediaMTX kem vong dem RAM tmpfs cho FaceGate v4 (Linux)
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export FACEGATE_RING_DIR="${FACEGATE_RING_DIR:-/dev/shm/facegate_ring}"
export CAM_USER="${CAM_USER:-admin}"
export CAM_PASS="${CAM_PASS:-changeme}"
export CAM_IP="${CAM_IP:-192.168.50.10}"

mkdir -p "$FACEGATE_RING_DIR"
MTX_BIN="${MEDIAMTX_BIN:-mediamtx}"
if ! command -v "$MTX_BIN" >/dev/null 2>&1; then
    echo "Khong tim thay binary mediamtx trong PATH (dat MEDIAMTX_BIN=/duong/dan/mediamtx)" >&2
    exit 1
fi
exec "$MTX_BIN" "$ROOT_DIR/deploy/mediamtx.yml"
