from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from fastapi.testclient import TestClient

import app.main as main_mod
from app.attendance import Shift
from app.config import load_config
from app.db import DB
from app.hrm import HRMWorker
from app.media_clips import write_signed_sidecar
from app.state import ctx


def _setup_test_ctx(tmp_path: Path, auth_enabled: bool = False):
    cfg = load_config()
    data_dir = tmp_path / "data"
    for sub in ("snapshots", "enroll", "uploads", "sources", "clips", "run"):
        (data_dir / sub).mkdir(parents=True, exist_ok=True)
    cfg["_paths"] = {
        **cfg["_paths"],
        "data": data_dir,
        "db": data_dir / "facegate.db",
        "snapshots": data_dir / "snapshots",
        "enroll": data_dir / "enroll",
        "uploads": data_dir / "uploads",
        "sources": data_dir / "sources",
        "clips": data_dir / "clips",
        "run": data_dir / "run",
    }
    cfg["security"] = {
        "auth_enabled": auth_enabled,
        "jwt_secret": "test-rbac-secret",
        "users": {
            "guard": {"password": "123", "role": "viewer"},
            "lan_hr": {"password": "456", "role": "hr"},
        },
    }
    ctx.cfg = cfg
    ctx.db = DB(cfg["_paths"]["db"])
    ctx.shift = Shift.from_cfg(cfg)
    ctx.hrm = HRMWorker(cfg, ctx.db)
    ctx.camera = type("DummyCam", (), {"connected": True, "black": False, "fps_in": 15.0, "kind": "rtsp",
                                        "describe": lambda self: {"kind": "rtsp", "label": "Tapo C310", "connected": True, "black": False}})()
    ctx.pipeline = type("DummyPipe", (), {
        "_thread": type("T", (), {"is_alive": lambda self: True})(),
        "status": lambda self: {"backend": "edgeface", "camera_connected": True, "camera_fps": 15.0,
                                "detect_fps": 6.0, "detect_ms": 42.0, "gallery": 1, "threshold": 0.40,
                                "source": ctx.camera.describe(), "sleeping": False, "motion_zone": [], "hrm": "mock"},
    })()
    return TestClient(main_mod.app, raise_server_exceptions=True)


def test_health_manual_revoke_legal_hold_burnin_and_rbac_endpoints(tmp_path: Path):
    client = _setup_test_ctx(tmp_path, auth_enabled=True)
    try:
        emp_id = ctx.db.create_employee("NV100", "Đỗ Minh K")
        ctx.db.set_status(emp_id, "active")

        # 1. Health endpoint khong can auth de man hinh cong luon hien thi canh bao Fail-safe
        r_health = client.get("/api/health")
        assert r_health.status_code == 200 and r_health.json()["ok"] is True

        # 2. Chua dang nhap goi endpoint nhay cam -> 401
        assert client.post("/api/attendance/manual", json={
            "employee_code": "NV100", "work_date": "2026-11-13", "effective_time": "07:55:00", "reason": "test"
        }).status_code == 401

        # 3. Vai tro viewer dang nhap nhung khong du quyen hr -> 403
        r_guard = client.post("/api/auth/login", data={"username": "guard", "password": "123"})
        assert r_guard.status_code == 200
        guard_hdr = {"Authorization": f"Bearer {r_guard.json()['token']}"}
        assert client.get("/api/attendance/audit-queue", headers=guard_hdr).status_code == 403

        # 4. Vai tro hr dang nhap -> thuc hien nhap bu & thu hoi cong thanh cong
        r_hr = client.post("/api/auth/login", data={"username": "lan_hr", "password": "456"})
        hr_hdr = {"Authorization": f"Bearer {r_hr.json()['token']}"}

        r_man = client.post("/api/attendance/manual", headers=hr_hdr, json={
            "employee_code": "NV100", "work_date": "2026-11-13",
            "effective_time": "07:55:00", "reason": "Mất mạng cổng 5 phút",
        })
        assert r_man.status_code == 200
        lid = r_man.json()["ledger_id"]

        r_rev = client.post("/api/attendance/revoke", headers=hr_hdr, json={
            "target_ledger_id": lid, "reason": "Nhập nhầm mã nhân viên",
        })
        assert r_rev.status_code == 200

        # 5. Tao clip MP4 + sidecar ky HMAC, bat legal_hold va goi API export-burnin
        clip_p = ctx.cfg["_paths"]["clips"] / "20261113" / "080000_1.mp4"
        clip_p.parent.mkdir(parents=True, exist_ok=True)
        vw = cv2.VideoWriter(str(clip_p), cv2.VideoWriter_fourcc(*"mp4v"), 15.0, (320, 240))
        for _ in range(5):
            vw.write(np.zeros((240, 320, 3), dtype=np.uint8))
        vw.release()

        app_id = ctx.db.add_appearance(
            kind="employee", employee_id=emp_id, code="NV100", name="Đỗ Minh K",
            start_ts=1700000000.0, end_ts=1700000002.0,
        )
        sc_p, sha, _ = write_signed_sidecar(
            clip_p, ctx.cfg["_paths"]["data"], app_id, "gate-1",
            1700000000_000_000_000, 500,
            [{"offset_ms": 0, "objects": [{"track_id": 1, "kind": "face", "bbox_norm": [0.1, 0.1, 0.4, 0.5], "color_role": "ok", "label": "NV100"}]}],
            cfg=ctx.cfg,
        )
        ctx.db.set_clip(app_id, "clips/20261113/080000_1.mp4", sha, sc_p.relative_to(ctx.cfg["_paths"]["data"]).as_posix())

        r_hold = client.post(f"/api/appearances/{app_id}/legal-hold", headers=hr_hdr, json={
            "hold": True, "reason": "Đối soát bảng lương tháng 11",
        })
        assert r_hold.status_code == 200 and r_hold.json()["legal_hold"] == 1

        r_burn = client.post(f"/api/appearances/{app_id}/export-burnin", headers=hr_hdr)
        assert r_burn.status_code == 200 and len(r_burn.content) > 512
    finally:
        ctx.hrm.stop()
