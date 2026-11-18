"""Kiem tra license guard + license gate (feature 002, T007)."""
import hashlib
import json
from pathlib import Path

import pytest

from server.scripts.license_gate import run_gate
from server.vision import license_guard
from server.vision.license_guard import LicenseError, check_artifact, load_manifest, require

ALLOWED = ["MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "Zlib", "CC0-1.0", "CC-BY-4.0"]


def _entry(name: str, data: bytes, **over) -> dict:
    e = {
        "file": name, "role": "face_embed", "version": "v1",
        "source_url": "https://example.invalid/m.onnx", "upstream_repo": "https://example.invalid/repo",
        "sha256": hashlib.sha256(data).hexdigest(), "min_bytes": 1,
        "license_code": "Apache-2.0", "license_weights": "Apache-2.0",
        "license_file": "third_party/licenses/x-LICENSE", "training_data": ["synthetic"],
        "training_data_risk": "residual_accepted", "commercial_use": "allowed",
        "legal_ack": {"by": "Phap che", "date": "2026-11-18", "ref": "PC-001"},
    }
    e.update(over)
    return e


def _setup(tmp_path: Path, entries: list[dict], files: dict[str, bytes], schema_version=1) -> Path:
    d = tmp_path / "models"
    d.mkdir()
    for n, b in files.items():
        (d / n).write_bytes(b)
    (d / "manifest.json").write_text(json.dumps(
        {"schema_version": schema_version, "allowed_weight_licenses": ALLOWED, "models": entries}), encoding="utf-8")
    return d


def _cfg(models_dir: Path, research: bool = False) -> dict:
    return {"_paths": {"models": models_dir}, "license": {"research_mode": research}}


def test_valid_manifest_passes(tmp_path):
    data = b"model-bytes"
    d = _setup(tmp_path, [_entry("m.onnx", data)], {"m.onnx": data})
    ok, msgs, rows = run_gate(d, [], strict=True)
    assert ok, msgs
    assert rows[0]["status"] == "ok"
    assert require(_cfg(d), "m.onnx")["file"] == "m.onnx"


def test_rogue_model_not_in_manifest_fails(tmp_path):
    data = b"model-bytes"
    d = _setup(tmp_path, [_entry("m.onnx", data)], {"m.onnx": data, "rogue.onnx": b"x"})
    ok, msgs, _ = run_gate(d, [])
    assert not ok
    assert any("rogue.onnx" in m and "[1]" in m for m in msgs)
    with pytest.raises(LicenseError, match="rogue.onnx không có trong manifest"):
        require(_cfg(d), "rogue.onnx")


def test_forbidden_commercial_use_raises(tmp_path):
    data = b"m"
    d = _setup(tmp_path, [_entry("m.onnx", data, commercial_use="forbidden")], {"m.onnx": data})
    with pytest.raises(LicenseError, match="không được phép thương mại"):
        require(_cfg(d), "m.onnx")
    assert not run_gate(d, [])[0]


def test_weight_license_outside_allow_list_raises(tmp_path):
    data = b"m"
    d = _setup(tmp_path, [_entry("m.onnx", data, license_weights="CC-BY-NC-SA-4.0")], {"m.onnx": data})
    with pytest.raises(LicenseError, match="không được phép thương mại"):
        require(_cfg(d), "m.onnx")


def test_checksum_mismatch_raises(tmp_path):
    d = _setup(tmp_path, [_entry("m.onnx", b"original")], {"m.onnx": b"tampered"})
    with pytest.raises(LicenseError, match="sai checksum"):
        require(_cfg(d), "m.onnx")
    ok, msgs, _ = run_gate(d, [])
    assert not ok and any("[5]" in m for m in msgs)


def test_forbidden_file_names_fail_rule_4(tmp_path):
    data = b"m"
    d = _setup(tmp_path, [_entry("m.onnx", data)], {"m.onnx": data})
    ok, msgs, _ = run_gate(d, ["server/models/tddfa_u_base.bin", "README.md"])
    assert not ok
    assert any("[4]" in m and "tddfa_u_base.bin" in m for m in msgs)
    (d / "edgeface_xs_gamma_06.onnx").write_bytes(b"x")
    ok, msgs, _ = run_gate(d, [])
    assert any("[4]" in m and "edgeface" in m for m in msgs)


def test_research_mode_downgrades_model_errors_to_warning(tmp_path):
    data = b"m"
    d = _setup(tmp_path, [_entry("m.onnx", data, commercial_use="forbidden")], {"m.onnx": data})
    assert require(_cfg(d, research=True), "m.onnx") is None


def test_strict_mode_fails_on_pending_legal_ack(tmp_path):
    data = b"m"
    pending = {"by": "PENDING", "date": "1970-01-01", "ref": "PENDING"}
    d = _setup(tmp_path, [_entry("m.onnx", data, legal_ack=pending)], {"m.onnx": data})
    ok, msgs, _ = run_gate(d, [], strict=False)
    assert ok and any(m.startswith("WARN") for m in msgs)
    ok, msgs, _ = run_gate(d, [], strict=True)
    assert not ok and any("PENDING" in m for m in msgs)


def test_missing_manifest_raises_even_in_research_mode(tmp_path):
    d = tmp_path / "models"
    d.mkdir()
    with pytest.raises(LicenseError, match="thiếu"):
        require(_cfg(d, research=True), "m.onnx")
    ok, msgs, _ = run_gate(d, [])
    assert not ok and "thiếu" in msgs[0]


def test_corrupt_manifest_raises(tmp_path):
    d = tmp_path / "models"
    d.mkdir()
    (d / "manifest.json").write_text("{", encoding="utf-8")
    with pytest.raises(LicenseError, match="không hợp lệ"):
        require(_cfg(d, research=True), "m.onnx")


def test_wrong_schema_version_raises(tmp_path):
    data = b"m"
    d = _setup(tmp_path, [_entry("m.onnx", data)], {"m.onnx": data}, schema_version=2)
    with pytest.raises(LicenseError, match="schema_version"):
        load_manifest(d)


def test_residual_risk_requires_legal_ack(tmp_path):
    data = b"m"
    e = _entry("m.onnx", data)
    del e["legal_ack"]
    d = _setup(tmp_path, [e], {"m.onnx": data})
    with pytest.raises(LicenseError, match="legal_ack"):
        load_manifest(d)


def test_blocking_training_data_fails_gate(tmp_path):
    data = b"m"
    d = _setup(tmp_path, [_entry("m.onnx", data, training_data_risk="blocking")], {"m.onnx": data})
    ok, msgs, _ = run_gate(d, [])
    assert not ok and any("[3]" in m for m in msgs)


def test_check_artifact_skips_sha_when_file_absent(tmp_path):
    d = _setup(tmp_path, [_entry("m.onnx", b"m")], {})
    assert check_artifact(load_manifest(d), "m.onnx", d)["file"] == "m.onnx"


def test_summary_lists_models(tmp_path):
    data = b"m"
    d = _setup(tmp_path, [_entry("m.onnx", data)], {"m.onnx": data})
    assert license_guard.summary(load_manifest(d)) == [
        {"file": "m.onnx", "license_weights": "Apache-2.0", "commercial_use": "allowed"}]


def test_repo_manifest_is_structurally_valid():
    """Manifest that cua repo phai hop le va khong chua model phi thuong mai."""
    models_dir = Path(__file__).resolve().parent.parent / "models"
    m = load_manifest(models_dir)
    for e in m["models"]:
        assert license_guard.is_commercial_ok(m, e), e["file"]
        assert not any(bad in e["file"].lower() for bad in ("edgeface", "3ddfa", "tddfa", "scrfd", "w600k"))


# --- T011: create_engine chan backend phi thuong mai ---------------------------------------------

def _engine_cfg(models_dir: Path, backend: str) -> dict:
    return {"pipeline": {"backend": backend, "det_score": 0.55}, "_paths": {"models": models_dir},
            "license": {"research_mode": False}}


@pytest.mark.parametrize("backend", ["edgeface", "onnx"])
def test_removed_backends_raise_license_error(tmp_path, backend):
    from server.vision.engines import create_engine
    with pytest.raises(LicenseError, match="đã bị gỡ"):
        create_engine(_engine_cfg(tmp_path, backend))


def test_removed_backend_raises_even_in_research_mode(tmp_path):
    from server.vision.engines import create_engine
    cfg = _engine_cfg(tmp_path, "edgeface")
    cfg["license"]["research_mode"] = True
    with pytest.raises(LicenseError, match="đã bị gỡ"):
        create_engine(cfg)


def test_opencv_backend_checks_manifest(tmp_path, monkeypatch):
    import cv2
    from server.vision.engines import create_engine
    det, rec = b"yunet", b"sface"
    d = _setup(tmp_path, [
        _entry("face_detection_yunet_2023mar.onnx", det, role="face_detect"),
        _entry("face_recognition_sface_2021dec.onnx", rec),
    ], {"face_detection_yunet_2023mar.onnx": det, "face_recognition_sface_2021dec.onnx": rec})
    monkeypatch.setattr(cv2.FaceDetectorYN, "create", staticmethod(lambda *a, **k: object()))
    monkeypatch.setattr(cv2.FaceRecognizerSF, "create", staticmethod(lambda *a, **k: object()))
    eng = create_engine(_engine_cfg(d, "opencv"))
    assert eng.name == "opencv" and eng.model_version == "yunet2023mar+sface2021dec"
    # Sua file SFace -> sai checksum -> tu choi
    (d / "face_recognition_sface_2021dec.onnx").write_bytes(b"tampered")
    with pytest.raises(LicenseError, match="sai checksum"):
        create_engine(_engine_cfg(d, "opencv"))
