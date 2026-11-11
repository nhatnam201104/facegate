#!/usr/bin/env bash
set -u
cd "$(dirname "$0")" || exit 1

if [ -d "$HOME/.local/bin" ]; then
  export PATH="$HOME/.local/bin:$PATH"
fi

echo "[1/3] Tạo môi trường ảo .venv ..."
if [ ! -x ".venv/bin/python" ]; then
  rm -rf .venv
  if command -v uv >/dev/null 2>&1; then
    uv venv --seed --python ">=3.10,<3.14" .venv 2>/dev/null || uv venv --seed .venv || exit 1
  else
    PY=""
    for cand in python3.12 python3.11 python3.13 python3.10 python3; do
      if command -v "$cand" >/dev/null 2>&1; then
        if "$cand" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
          PY="$cand"
          break
        fi
      fi
    done
    if [ -z "$PY" ]; then
      echo "Lỗi: Cần cài đặt Python 3.10 trở lên (ví dụ: sudo apt install -y python3 python3-venv)." >&2
      exit 1
    fi
    if ! "$PY" -m venv .venv; then
      rm -rf .venv
      echo "Lỗi: Thiếu gói tạo môi trường ảo cho $PY." >&2
      echo "Trên Ubuntu/Debian, hãy chạy: sudo apt install -y python3-venv python3-pip" >&2
      exit 1
    fi
  fi
fi

echo "[2/3] Cài đặt thư viện ..."
if command -v uv >/dev/null 2>&1; then
  uv pip install --python .venv/bin/python -r requirements.txt || exit 1
else
  .venv/bin/python -m pip install --upgrade pip && \
  .venv/bin/python -m pip install -r requirements.txt || exit 1
fi

echo "[3/3] Tải model ONNX ..."
.venv/bin/python scripts/download_models.py "$@" || exit 1

if compgen -G "/dev/video*" >/dev/null 2>&1; then
  for dev in /dev/video*; do
    if [ ! -r "$dev" ]; then
      echo ""
      echo "Lưu ý: Tài khoản hiện tại chưa có quyền đọc $dev."
      echo "Để dùng webcam trên Linux, chạy: sudo usermod -aG video \$USER (rồi đăng xuất/đăng nhập lại)."
      break
    fi
  done
fi

echo ""
echo "Cài đặt xong. Chạy ./run.sh để khởi động FaceGate."
