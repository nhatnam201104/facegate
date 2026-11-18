"""Kiem tra giay phep model truoc khi nap (feature 002-license-compliant-models).

Nguon su that: server/models/manifest.json (schema: specs/002-license-compliant-models/contracts/model-manifest.schema.json).
Hanh vi (contracts/runtime-license-guard.md §2):
- Thieu / hong manifest, sai schema_version -> LicenseError (FAIL-CLOSED, khong co ngoai le research_mode).
- Model khong co trong manifest / khong duoc thuong mai / sai SHA-256 -> LicenseError,
  tru khi license.research_mode=true thi chi canh bao.
Moi process (chinh va worker) tu nap manifest, khong tin ket qua qua IPC.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

log = logging.getLogger("license_guard")

MANIFEST_NAME = "manifest.json"
SCHEMA_VERSION = 1
ROLES = {"face_detect", "face_embed", "pose", "liveness", "context_detect"}
RISKS = {"none", "residual_accepted", "blocking"}
COMMERCIAL = {"allowed", "forbidden", "unknown"}
REQUIRED_FIELDS = (
    "file", "role", "version", "source_url", "upstream_repo", "sha256", "min_bytes",
    "license_code", "license_weights", "license_file", "training_data",
    "training_data_risk", "commercial_use",
)

_sha_cache: dict[tuple[str, int, int], str] = {}


class LicenseError(RuntimeError):
    """Loi giay phep model: server phai tu choi khoi dong."""


def _invalid(detail: str) -> LicenseError:
    return LicenseError(f"LICENSE: manifest.json không hợp lệ: {detail}")


def _validate_entry(i: int, m: dict) -> None:
    if not isinstance(m, dict):
        raise _invalid(f"models[{i}] không phải object")
    missing = [k for k in REQUIRED_FIELDS if k not in m]
    if missing:
        raise _invalid(f"models[{i}] thiếu field {', '.join(missing)}")
    sha = str(m["sha256"])
    if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        raise _invalid(f"models[{i}].sha256 phải là 64 ký tự hex thường")
    if m["role"] not in ROLES:
        raise _invalid(f"models[{i}].role={m['role']!r}")
    if m["training_data_risk"] not in RISKS:
        raise _invalid(f"models[{i}].training_data_risk={m['training_data_risk']!r}")
    if m["commercial_use"] not in COMMERCIAL:
        raise _invalid(f"models[{i}].commercial_use={m['commercial_use']!r}")
    if not isinstance(m["training_data"], list):
        raise _invalid(f"models[{i}].training_data phải là mảng")
    if int(m["min_bytes"]) < 1:
        raise _invalid(f"models[{i}].min_bytes phải >= 1")
    if m["training_data_risk"] == "residual_accepted":
        ack = m.get("legal_ack")
        if not isinstance(ack, dict) or not all(k in ack for k in ("by", "date", "ref")):
            raise _invalid(f"models[{i}] có training_data_risk=residual_accepted nhưng thiếu legal_ack{{by,date,ref}}")


def load_manifest(models_dir: Path) -> dict:
    """Nap va kiem tra cau truc manifest. Luon fail-closed."""
    p = Path(models_dir) / MANIFEST_NAME
    if not p.exists():
        raise LicenseError("LICENSE: thiếu server/models/manifest.json")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise _invalid(str(e)) from e
    if not isinstance(data, dict):
        raise _invalid("gốc không phải object")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise _invalid(f"schema_version={data.get('schema_version')!r}")
    allowed = data.get("allowed_weight_licenses")
    if not isinstance(allowed, list) or not allowed:
        raise _invalid("allowed_weight_licenses rỗng hoặc thiếu")
    models = data.get("models")
    if not isinstance(models, list):
        raise _invalid("models phải là mảng")
    seen: set[str] = set()
    for i, m in enumerate(models):
        _validate_entry(i, m)
        if m["file"] in seen:
            raise _invalid(f"file trùng lặp: {m['file']}")
        seen.add(m["file"])
    return data


def sha256_file(path: Path) -> str:
    path = Path(path)
    st = path.stat()
    key = (str(path.resolve()), int(st.st_mtime_ns), int(st.st_size))
    if key in _sha_cache:
        return _sha_cache[key]
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    digest = h.hexdigest()
    _sha_cache[key] = digest
    return digest


def find_entry(manifest: dict, file_name: str) -> dict | None:
    for m in manifest.get("models", []):
        if m.get("file") == file_name:
            return m
    return None


def is_commercial_ok(manifest: dict, entry: dict) -> bool:
    return entry.get("commercial_use") == "allowed" and entry.get("license_weights") in manifest.get(
        "allowed_weight_licenses", [])


def check_artifact(manifest: dict, file_name: str, models_dir: Path, verify_sha: bool = True) -> dict:
    entry = find_entry(manifest, file_name)
    if entry is None:
        raise LicenseError(f"LICENSE: {file_name} không có trong manifest")
    if not is_commercial_ok(manifest, entry):
        raise LicenseError(f"LICENSE: {file_name} không được phép thương mại")
    p = Path(models_dir) / file_name
    if verify_sha and p.exists() and sha256_file(p) != entry["sha256"]:
        raise LicenseError(f"LICENSE: {file_name} sai checksum")
    return entry


def research_mode(cfg: dict | None) -> bool:
    return bool(((cfg or {}).get("license") or {}).get("research_mode", False))


def require(cfg: dict | None, file_name: str, models_dir: Path | None = None) -> dict | None:
    """Kiem tra 1 model sap nap. Thieu/hong manifest luon raise; loi model chi canh bao khi research_mode."""
    if models_dir is None:
        models_dir = Path((cfg or {})["_paths"]["models"])
    manifest = load_manifest(models_dir)
    try:
        return check_artifact(manifest, file_name, models_dir)
    except LicenseError as e:
        if research_mode(cfg):
            log.warning("%s (bỏ qua vì license.research_mode=true - KHÔNG dùng cho khách hàng)", e)
            return None
        raise


def summary(manifest: dict) -> list[dict]:
    return [
        {"file": m["file"], "license_weights": m["license_weights"], "commercial_use": m["commercial_use"]}
        for m in manifest.get("models", [])
    ]
