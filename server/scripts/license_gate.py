"""Cong kiem tra giay phep model (feature 002, data-model.md §ModelArtifact quy tac 1-5).

    python server/scripts/license_gate.py            # exit 0 = pass, 1 = fail; legal_ack PENDING chi canh bao
    python server/scripts/license_gate.py --strict   # nghiem thu: legal_ack PENDING cung la fail
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from server.vision.license_guard import (  # noqa: E402
    LicenseError, is_commercial_ok, load_manifest, sha256_file,
)

MODEL_EXTS = {".onnx", ".bin", ".task", ".pth", ".pt"}
FORBIDDEN_RE = re.compile(r"(?i)(tddfa_|bfm|3ddfa|edgeface|w600k|scrfd)")


def git_tracked_files(root: Path) -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    return [line for line in out.splitlines() if line.strip()]


def run_gate(models_dir: Path, tracked_files: list[str], strict: bool = False) -> tuple[bool, list[str], list[dict]]:
    """Tra ve (ok, messages, rows). Khong in, khong exit - de test duoc."""
    errors: list[str] = []
    warns: list[str] = []
    rows: list[dict] = []
    try:
        manifest = load_manifest(models_dir)
    except LicenseError as e:
        return False, [f"FAIL {e}"], rows
    entries = {m["file"]: m for m in manifest["models"]}

    # Quy tac 1: moi file model tren dia phai co trong manifest
    on_disk = sorted(p for p in models_dir.iterdir() if p.is_file() and p.suffix.lower() in MODEL_EXTS)
    for p in on_disk:
        if p.name not in entries:
            errors.append(f"FAIL [1] {p.name}: có trên đĩa nhưng không có trong manifest")

    for name, m in entries.items():
        status = "ok"
        # Quy tac 2: thuong mai + allow-list
        if not is_commercial_ok(manifest, m):
            errors.append(f"FAIL [2] {name}: commercial_use={m['commercial_use']} license_weights={m['license_weights']}")
            status = "fail"
        # Quy tac 3: rui ro du lieu huan luyen
        if m["training_data_risk"] == "blocking":
            errors.append(f"FAIL [3] {name}: training_data_risk=blocking")
            status = "fail"
        elif m["training_data_risk"] == "residual_accepted" and str(m["legal_ack"].get("by", "")).upper() == "PENDING":
            msg = f"[3] {name}: legal_ack chưa ký (PENDING)"
            if strict:
                errors.append("FAIL " + msg)
                status = "fail"
            else:
                warns.append("WARN " + msg)
                status = "warn" if status == "ok" else status
        # Quy tac 5: checksum
        p = models_dir / name
        if p.exists():
            if p.stat().st_size < int(m["min_bytes"]):
                errors.append(f"FAIL [5] {name}: nhỏ hơn min_bytes (có thể là con trỏ Git LFS)")
                status = "fail"
            elif sha256_file(p) != m["sha256"]:
                errors.append(f"FAIL [5] {name}: sai checksum")
                status = "fail"
        rows.append({"file": name, "license_weights": m["license_weights"],
                     "commercial_use": m["commercial_use"], "status": status})

    # Quy tac 4: cam file phi thuong mai trong repo va thu muc model
    for rel in tracked_files:
        if FORBIDDEN_RE.search(Path(rel).name):
            errors.append(f"FAIL [4] git đang theo dõi file cấm: {rel}")
    for p in sorted(models_dir.iterdir()):
        if p.is_file() and FORBIDDEN_RE.search(p.name):
            errors.append(f"FAIL [4] thư mục model chứa file cấm: {p.name}")

    return not errors, errors + warns, rows


def main() -> None:
    ap = argparse.ArgumentParser(description="Cổng kiểm tra giấy phép model FaceGate")
    ap.add_argument("--models-dir", default=str(ROOT / "server" / "models"))
    ap.add_argument("--strict", action="store_true", help="legal_ack PENDING cũng là fail (dùng khi nghiệm thu)")
    args = ap.parse_args()
    ok, messages, rows = run_gate(Path(args.models_dir), git_tracked_files(ROOT), strict=args.strict)
    if rows:
        w = max(len(r["file"]) for r in rows)
        print(f"{'file'.ljust(w)} | license_weights | commercial_use | status")
        for r in rows:
            print(f"{r['file'].ljust(w)} | {r['license_weights']:<15} | {r['commercial_use']:<14} | {r['status']}")
        print()
    for m in messages:
        print(m)
    print("\nLICENSE GATE:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
