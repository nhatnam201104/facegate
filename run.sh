#!/usr/bin/env bash
set -u
cd "$(dirname "$0")" || exit 1

if [ ! -x ".venv/bin/python" ]; then
  echo "Chưa cài đặt môi trường ảo. Hãy chạy ./setup.sh trước." >&2
  exit 1
fi

URL="$(.venv/bin/python -c '
from app.config import load_config
cfg = load_config()["server"]
host = "127.0.0.1" if cfg["host"] in ("0.0.0.0", "::") else cfg["host"]
print(f"http://{host}:{cfg[\"port\"]}")
' 2>/dev/null || echo "http://127.0.0.1:8090")"

if [ "${NO_BROWSER:-0}" != "1" ] && { [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; } && command -v xdg-open >/dev/null 2>&1; then
  (sleep 1.5 && xdg-open "$URL" >/dev/null 2>&1 || true) &
fi

exec .venv/bin/python -m app.main "$@"
