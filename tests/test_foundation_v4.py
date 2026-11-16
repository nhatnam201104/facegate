from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from app.config import load_config
from app.db import DB
from app.ipc import IPCClient, IPCServer, TrackedObject
from scripts.replay_eval import evaluate_observations, wilson_upper_95


def test_config_v4_defaults_and_clamping(tmp_path: Path):
    cfg_file = tmp_path / "cfg.yaml"
    cfg_file.write_text("storage:\n  data_dir: data\npipeline:\n  detect_fps: 999\ndecision:\n  auto_enroll_online: true\n", encoding="utf-8")
    cfg = load_config(cfg_file)
    assert cfg["camera"]["model_profile"] == "tapo_c310"
    assert cfg["quality"]["max_yaw"] == 90
    assert cfg["pipeline"]["detect_fps"] == 30.0
    assert cfg["decision"]["auto_enroll_online"] is False  # H17 enforced


def test_tracked_object_validation_and_ndjson_ipc(tmp_path: Path):
    obj = TrackedObject(
        camera_id="gate-1",
        ts_server_ns=1794565200000000000,
        track_id=7,
        kind="sku",  # Phase 2 SKU compatibility (H21)
        bbox_norm=(-0.1, 0.2, 0.6, 1.2),
        vel_norm=(0.05, -0.01),
        state="IDENTIFIED",
        color_role="ok",
        label="SKU-BOX-01",
        verification_tier="verified",
        meta={"barcode": "8930001"},
    )
    assert obj.bbox_norm == (0.0, 0.2, 0.6, 1.0)
    restored = TrackedObject.from_dict(obj.to_dict())
    assert restored.kind == "sku" and restored.meta["barcode"] == "8930001"

    with pytest.raises(ValueError):
        TrackedObject(camera_id="gate-1", ts_server_ns=1, track_id=1, kind="invalid", bbox_norm=(0, 0, 1, 1))

    received: list[dict] = []
    cmds_received: list[dict] = []

    async def _roundtrip():
        sock = tmp_path / "perception.sock"
        srv = IPCServer(sock_path=sock, port=0, on_message=lambda m: received.append(m))
        await srv.start()
        cli = IPCClient(sock_path=sock, port=srv.bound_port)
        await cli.connect()
        await cli.send({"type": "frame_overlay", "objects": [obj.to_dict()]})
        for _ in range(20):
            if received:
                break
            await asyncio.sleep(0.02)
        await srv.send_command({"cmd": "reload_gallery", "version": 3})
        cmd = await asyncio.wait_for(cli.recv(), timeout=1.0)
        if cmd:
            cmds_received.append(cmd)
        await cli.close()
        await srv.close()

    asyncio.run(_roundtrip())
    assert len(received) == 1
    assert received[0]["objects"][0]["track_id"] == 7
    assert cmds_received == [{"cmd": "reload_gallery", "version": 3}]


def test_db_migration_from_main_and_ledger_idempotency(tmp_path: Path):
    db_path = tmp_path / "legacy_main.db"
    # Tao DB cu theo dung schema cua nhanh main (chua co pose_group, legal_hold, attendance_ledger)
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE employees (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', created_at REAL NOT NULL, approved_at REAL);
        CREATE TABLE embeddings (id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id INTEGER NOT NULL, vec BLOB NOT NULL, dim INTEGER NOT NULL, model_version TEXT NOT NULL, quality REAL, pose_bin TEXT, source TEXT NOT NULL DEFAULT 'enroll', crop_path TEXT, created_at REAL NOT NULL);
        CREATE TABLE enrollments (id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id INTEGER NOT NULL, created_at REAL NOT NULL, report TEXT);
        CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, camera TEXT, employee_id INTEGER, code TEXT, name TEXT, score REAL, margin REAL, direction TEXT, latency_ms REAL, frames INTEGER, snapshot TEXT, model_version TEXT, hrm_status TEXT, hrm_attempts INTEGER DEFAULT 0, hrm_response TEXT, label TEXT);
        CREATE TABLE appearances (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, employee_id INTEGER, code TEXT, name TEXT, start_ts REAL NOT NULL, end_ts REAL NOT NULL, score REAL, liveness REAL, snapshot TEXT, clip TEXT, clip_ready INTEGER NOT NULL DEFAULT 0, event_id INTEGER, camera TEXT);
        INSERT INTO employees(code, name, status, created_at, approved_at) VALUES ('NV001', 'Nguyen A', 'active', 100.0, 100.0);
    """)
    vec = np.ones(128, dtype=np.float32)
    conn.execute("INSERT INTO embeddings(employee_id,vec,dim,model_version,quality,pose_bin,created_at) VALUES (1,?,?, 'v1', 0.9, 'thang-giua', 100.0)", (vec.tobytes(), 128))
    conn.execute("INSERT INTO appearances(kind,employee_id,code,name,start_ts,end_ts) VALUES ('employee',1,'NV001','Nguyen A',100.0,102.0)")
    conn.commit()
    conn.close()

    # Khoi tao DB v4 -> tu dong _migrate() khong mat du lieu cu
    db = DB(db_path)
    emps = db.list_employees("v1")
    assert len(emps) == 1
    assert emps[0]["pose_coverage"]["frontal"] == 1
    assert emps[0]["pose_coverage"]["ready_90"] is False

    # Kiem tra rang buoc UNIQUE idempotency_key tren attendance_ledger
    lid1 = db.append_ledger(
        idempotency_key="auto:NV001:2026-11-13",
        action="checkin_created",
        employee_id=1,
        code="NV001",
        work_date="2026-11-13",
        effective_ts=1700000000.0,
        verification_tier="verified",
        actor="system:gate-1",
    )
    lid2 = db.append_ledger(
        idempotency_key="auto:NV001:2026-11-13",
        action="checkin_created",
        employee_id=1,
        code="NV001",
        work_date="2026-11-13",
        effective_ts=1700000005.0,
        verification_tier="verified",
        actor="system:gate-1",
    )
    assert lid1 == lid2
    assert len(db.q("SELECT * FROM attendance_ledger")) == 1


def test_replay_eval_wilson_ci_and_iso_30107(tmp_path: Path):
    assert 0.0009 <= wilson_upper_95(0, 3000) <= 0.0015
    obs = tmp_path / "observations.jsonl"
    v1 = np.eye(8, dtype=np.float32)[0].tolist()
    v2 = np.eye(8, dtype=np.float32)[1].tolist()
    lines = [
        {"frame_idx": 0, "decode_ms": 8.5, "layout": "frontal_corridor", "gt_label": "bonafide", "subject_code": "NV1",
         "faces": [{"quality": {"blur": 85.0}, "embedding": v1, "fas_prob": 0.92}]},
        {"frame_idx": 1, "decode_ms": 9.1, "layout": "frontal_corridor", "gt_label": "bonafide", "subject_code": "NV2",
         "faces": [{"quality": {"blur": 90.0}, "embedding": v2, "fas_prob": 0.88}]},
        {"frame_idx": 2, "decode_ms": 8.0, "layout": "side_walk", "gt_label": "spoof", "pai_type": "phone_video",
         "faces": [{"quality": {"blur": 42.0}, "embedding": v1, "fas_prob": 0.18}]},
    ]
    obs.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    rep = evaluate_observations(obs, threshold=0.42, fas_threshold=0.70)
    assert rep["embedding_far"]["impostor_pairs"] == 1
    assert rep["embedding_far"]["false_accepts"] == 0
    assert rep["iso_30107_3"]["apcer_by_pai"]["phone_video"]["apcer"] == 0.0
    assert rep["iso_30107_3"]["bpcer"]["bpcer"] == 0.0
