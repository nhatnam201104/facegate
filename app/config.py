import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: Path | None = None) -> dict:
    path = path or Path(os.environ.get("FACEGATE_CONFIG", ROOT / "config.yaml"))
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    data_dir = ROOT / cfg["storage"]["data_dir"]
    cfg["_paths"] = {
        "root": ROOT,
        "models": ROOT / "models",
        "data": data_dir,
        "db": data_dir / "facegate.db",
        "snapshots": data_dir / "snapshots",
        "enroll": data_dir / "enroll",
        "uploads": data_dir / "uploads",
        "sources": data_dir / "sources",   # video dung lam nguon nhan dien
        "clips": data_dir / "clips",       # clip moi lan xuat hien truoc camera
    }
    for key in ("data", "snapshots", "enroll", "uploads", "sources", "clips"):
        cfg["_paths"][key].mkdir(parents=True, exist_ok=True)
    return cfg
