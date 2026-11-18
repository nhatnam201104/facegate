"""Tai va kiem tra SHA-256 day du cac model thuong mai hoa duoc cua FaceGate (doc tu server/models/manifest.json).

    python server/scripts/download_models.py               # YuNet + SFace + YOLOX-Nano + MiniFASNet (theo manifest)
    python server/scripts/download_models.py --verify-only # chi kiem tra ton tai + checksum

Danh sach model, URL, SHA-256 va giay phep chi khai bao trong manifest.json (feature 002-license-compliant-models).
Khong con model phi thuong mai (EdgeFace, 3DDFA_V2/BFM, InsightFace SCRFD/ArcFace).
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from server.vision.license_guard import LicenseError, is_commercial_ok, load_manifest, sha256_file  # noqa: E402

MODELS_DIR = ROOT / "server" / "models"


def download(entry: dict, models_dir: Path) -> bool:
    name, url, expected, min_bytes = entry["file"], entry["source_url"], entry["sha256"], int(entry["min_bytes"])
    dst = models_dir / name
    if dst.exists():
        digest = sha256_file(dst)
        if digest != expected:
            print(f"[loi]  {name} đã có nhưng sai checksum ({digest[:16]}... != {expected[:16]}...), không ghi đè."
                  f" Xoá file thủ công rồi chạy lại.")
            return False
        print(f"[ok]   {name} đã có (sha256 khớp)")
        return True
    print(f"[tai]  {name} ...", end=" ", flush=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "facegate-model-fetch"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
    except Exception as e:  # noqa: BLE001
        print(f"LOI: {e}")
        return False
    if len(data) < min_bytes:
        print("LOI: file tải về quá nhỏ (có thể là con trỏ Git LFS)")
        return False
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected:
        print(f"LOI: sai checksum ({digest[:16]}... != {expected[:16]}...), đã huỷ file")
        return False
    dst.write_bytes(data)
    print(f"{len(data) / 1e6:.1f} MB (sha256 khớp)")
    return True


def verify(entry: dict, models_dir: Path) -> bool:
    p = models_dir / entry["file"]
    if not p.exists():
        print(f"[thieu] {entry['file']}")
        return False
    if p.stat().st_size < int(entry["min_bytes"]):
        print(f"[loi]  {entry['file']} nhỏ hơn min_bytes")
        return False
    if sha256_file(p) != entry["sha256"]:
        print(f"[loi]  {entry['file']} sai checksum")
        return False
    print(f"[ok]   {entry['file']} ({p.stat().st_size / 1e6:.2f} MB, {entry['license_weights']})")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="Tải model FaceGate theo server/models/manifest.json")
    ap.add_argument("--verify-only", action="store_true", help="chỉ kiểm tra tồn tại và SHA-256 đầy đủ")
    ap.add_argument("--models-dir", default=str(MODELS_DIR))
    args = ap.parse_args()
    models_dir = Path(args.models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)
    try:
        manifest = load_manifest(models_dir)
    except LicenseError as e:
        print(e)
        sys.exit(1)
    entries = [m for m in manifest["models"] if is_commercial_ok(manifest, m)]
    skipped = [m["file"] for m in manifest["models"] if not is_commercial_ok(manifest, m)]
    for name in skipped:
        print(f"[bo qua] {name}: không được phép thương mại theo manifest")
    fn = verify if args.verify_only else download
    ok = all([fn(m, models_dir) for m in entries])
    if not ok:
        if not args.verify_only:
            print("\nMột số model tải thất bại. Tải thủ công từ source_url trong server/models/manifest.json")
            print(f"và đặt vào thư mục: {models_dir}")
        sys.exit(1)
    print("\nXong.")


if __name__ == "__main__":
    main()
