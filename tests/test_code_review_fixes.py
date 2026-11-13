"""Kiem thu hoi quy cho cac loi code review CR1-CR10 (xem docs/PHAN_BIEN_CODE_REVIEW.md)."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from app import guided
from app.clips import ClipRecorder
from app.config import load_config
from app.db import DB
from app.engines import Face
from app.enrollment import backfill_legacy_embeddings
from app.guided import NO_FRONTAL_MSG, VERIFY_FRONT_STEP, GuidedSession, known_frontal_embeddings
from app.hrm import HRMWorker, MockHRM
from app.ledger import hold_clip_if_pending_audit, record_auto_checkin, revoke_checkin
from app.media_clips import cleanup_clips_safe
from app.security import BiometricCipher


# ------------------------------------------------------------------ tien ich
class _Engine:
    model_version = "fake-1"
    name = "fake"

    def __init__(self, vec=None):
        self.vec = vec if vec is not None else np.ones(8, dtype=np.float32) / np.sqrt(8)

    def detect(self, img):
        return [Face(bbox=np.array([10.0, 10.0, 90.0, 90.0]), score=0.99,
                     kps=np.zeros((5, 2), dtype=np.float32))]

    def align(self, img, face):
        return img

    def embed_aligned(self, aligned):
        return self.vec.copy()


def _hrm_worker(db, tmp_path, adapter=None) -> HRMWorker:
    """HRMWorker khong khoi dong thread nen (goi sync_once thu cong)."""
    w = HRMWorker.__new__(HRMWorker)
    w.adapter = adapter or MockHRM({"_paths": {"data": tmp_path}})
    w.kind, w.db, w.on_update = "mock", db, None
    w._wake, w._stop = threading.Event(), threading.Event()
    return w


def _employee(db, code="NV001") -> int:
    emp_id = db.create_employee(code, "Nguyen Van A")
    db.set_status(emp_id, "active")
    return emp_id


def _event(db, emp_id, code="NV001") -> int:
    return db.add_event(ts=time.time(), camera="gate-1", employee_id=emp_id, code=code, name="A",
                        score=0.9, margin=0.2, direction="IN", latency_ms=1.0, frames=3, snapshot=None,
                        model_version="fake-1", hrm_status="skipped", kind="checkin", att_type="in")


# ------------------------------------------------------------------ CR1
def test_cr1_backfill_with_real_lock_does_not_deadlock(tmp_path: Path):
    db = DB(tmp_path / "t.db")
    emp_id = _employee(db)
    enroll = tmp_path / "enroll" / "NV001"
    enroll.mkdir(parents=True)
    cv2.imwrite(str(enroll / "0_thang-giua.jpg"), np.full((100, 100, 3), 128, np.uint8))
    lock = threading.Lock()
    ctx = SimpleNamespace(db=db, enroll_engine=_Engine(), enroll_lock=lock, pose=None,
                          cfg={"_paths": {"enroll": tmp_path / "enroll"}})
    out: list[int] = []
    th = threading.Thread(target=lambda: out.append(backfill_legacy_embeddings(ctx)), daemon=True)
    th.start()
    th.join(timeout=5.0)
    assert not th.is_alive(), "backfill bi treo (deadlock)"
    assert out == [1]
    assert not lock.locked()
    n = db.q("SELECT COUNT(*) AS n FROM embeddings WHERE employee_id=?", (emp_id,))[0]["n"]
    assert n == 1


# ------------------------------------------------------------------ CR3
def test_cr3_checkin_pushed_to_hrm_exactly_once_and_mirrored(tmp_path: Path):
    db = DB(tmp_path / "t.db")
    emp_id = _employee(db)
    ev_id = _event(db, emp_id)
    record_auto_checkin(db, employee_id=emp_id, code="NV001", ts=time.time(), event_id=ev_id)
    w = _hrm_worker(db, tmp_path)
    w.sync_once()
    w.sync_once()
    lines = (tmp_path / "hrm_mock.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    ev = db.q("SELECT hrm_status FROM events WHERE id=?", (ev_id,))[0]
    assert ev["hrm_status"] == "sent"


def test_cr3_pipeline_does_not_queue_events_for_hrm():
    src = Path("app/pipeline.py").read_text(encoding="utf-8")
    assert 'hrm_status="pending"' not in src


# ------------------------------------------------------------------ CR6
def test_cr6_revoke_before_sync_cancels_original(tmp_path: Path):
    db = DB(tmp_path / "t.db")
    emp_id = _employee(db)
    lid = record_auto_checkin(db, employee_id=emp_id, code="NV001", ts=time.time(), event_id=_event(db, emp_id))
    db.set_ledger_hrm(lid, "failed", error="HRM down")
    rev = revoke_checkin(db, lid, idempotency_key="rv-1", reason="nham nguoi")
    rows = {r["id"]: r for r in db.q("SELECT id, hrm_sync_status FROM attendance_ledger")}
    assert rows[lid]["hrm_sync_status"] == "cancelled"
    assert rows[rev]["hrm_sync_status"] == "skipped"
    assert db.pending_ledger_hrm() == []
    # Lan day sau that bai cung khong duoc dua dong da huy ve 'failed'
    db.set_ledger_hrm(lid, "failed", error="x")
    assert db.q("SELECT hrm_sync_status FROM attendance_ledger WHERE id=?", (lid,))[0]["hrm_sync_status"] == "cancelled"


def test_cr6_revoke_during_inflight_push_requeues_delete(tmp_path: Path):
    db = DB(tmp_path / "t.db")
    emp_id = _employee(db)
    lid = record_auto_checkin(db, employee_id=emp_id, code="NV001", ts=time.time(), event_id=_event(db, emp_id))
    calls: list[tuple[str, str | None]] = []

    class _RacyHRM:
        def push(self, item):
            # HR thu hoi dung luc dong goc dang duoc gui
            revoke_checkin(db, item["id"], idempotency_key="rv-race", reason="thu hoi")
            calls.append(("push", None))
            return True, "DOC-1"

        def revoke(self, item):
            calls.append(("revoke", item.get("remote_doc_id")))
            return True, "deleted"

    w = _hrm_worker(db, tmp_path, adapter=_RacyHRM())
    w.sync_once()   # day ban goc (va bi thu hoi giua chung)
    w.sync_once()   # phai gui lenh xoa bu
    assert calls == [("push", None), ("revoke", "DOC-1")]


# ------------------------------------------------------------------ CR4 / CR9
def test_cr4_encrypted_embeddings_fail_closed_without_cipher(tmp_path: Path):
    cipher = BiometricCipher(b"k" * 32)
    db = DB(tmp_path / "t.db", cipher=cipher)
    emp_id = _employee(db)
    vec = np.arange(8, dtype=np.float32)
    db.add_embedding(emp_id, vec, "fake-1", 0.9, "thang-giua", None)
    assert np.allclose(db.load_gallery("fake-1")[0]["vec"], vec)
    no_key = DB(tmp_path / "t.db")
    with pytest.raises(RuntimeError, match="FACEGATE_BIOMETRIC_KEY"):
        no_key.load_gallery("fake-1")


def test_cr4_worker_db_gets_cipher():
    src = Path("app/worker_perception.py").read_text(encoding="utf-8")
    assert "cipher=BiometricCipher.from_env_or_cfg(cfg)" in src


def _supplement_session(tmp_path, db, vec):
    cfg = load_config()
    cfg["_paths"] = {**cfg["_paths"], "enroll": tmp_path / "enroll"}
    ctx = SimpleNamespace(cfg=cfg, db=db, engine=_Engine(vec), engine_lock=threading.Lock(),
                          threshold=0.4, gallery=SimpleNamespace(reload=lambda: None),
                          camera=SimpleNamespace(kind="webcam", connected=True))
    g = GuidedSession(ctx, "NV001", "A", supplement_only=True)
    best = {"frame": np.zeros((100, 100, 3), np.uint8),
            "face": Face(bbox=np.array([10.0, 10.0, 90.0, 90.0]), score=0.99,
                         kps=np.zeros((5, 2), dtype=np.float32))}
    return g, best


def test_cr9_supplement_without_frontal_is_rejected(tmp_path: Path):
    db = DB(tmp_path / "t.db")
    _employee(db)
    g, best = _supplement_session(tmp_path, db, np.ones(8, np.float32) / np.sqrt(8))
    err, emb = g._commit(VERIFY_FRONT_STEP, best, [], None)
    assert err == NO_FRONTAL_MSG and emb is None


def test_cr9_supplement_unknown_code_is_rejected(tmp_path: Path):
    db = DB(tmp_path / "t.db")
    g, best = _supplement_session(tmp_path, db, np.ones(8, np.float32) / np.sqrt(8))
    err, _ = g._commit(VERIFY_FRONT_STEP, best, [], None)
    assert err == NO_FRONTAL_MSG


def test_cr9_supplement_verifies_against_encrypted_frontal(tmp_path: Path):
    db = DB(tmp_path / "t.db", cipher=BiometricCipher(b"k" * 32))
    emp_id = _employee(db)
    me = np.ones(8, np.float32) / np.sqrt(8)
    db.add_embedding(emp_id, me, "fake-1", 0.9, "thang-giua", None, pose_group="frontal")
    assert len(known_frontal_embeddings(db, "NV001", "fake-1")) == 1
    g, best = _supplement_session(tmp_path, db, me)
    assert g._commit(VERIFY_FRONT_STEP, best, [], None)[0] is None
    other = np.zeros(8, np.float32)
    other[0], other[1] = 1.0, -1.0          # truc giao voi mau -> cos = 0
    other /= np.linalg.norm(other)
    g2, best2 = _supplement_session(tmp_path, db, other)
    assert "không khớp" in g2._commit(VERIFY_FRONT_STEP, best2, [], None)[0]


# ------------------------------------------------------------------ CR5 / CR8
def _clip(data_root: Path, rel: str, age_days: float) -> Path:
    p = data_root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    t = time.time() - age_days * 86400
    os.utime(p, (t, t))
    return p


def test_cr5_clip_recorder_no_longer_deletes_files():
    assert not hasattr(ClipRecorder, "_cleanup")


def test_cr8_retention_zero_keeps_clips(tmp_path: Path):
    db = DB(tmp_path / "t.db")
    p = _clip(tmp_path, "clips/20250101/a.mp4", 400)
    db.add_appearance(kind="unknown", start_ts=time.time() - 400 * 86400, end_ts=time.time(),
                      clip="clips/20250101/a.mp4", camera="gate-1")
    orphan = _clip(tmp_path, "clips/20250101/orphan.mp4", 400)
    st = cleanup_clips_safe(db, tmp_path, retention_days=0, min_free_disk_gb=0.0, simulated_free_gb=100.0)
    assert p.exists() and orphan.exists()
    assert st["deleted_expired"] == 0 and st["deleted_orphan"] == 0


def test_cr5_orphans_expire_but_held_clips_survive(tmp_path: Path):
    db = DB(tmp_path / "t.db")
    held = _clip(tmp_path, "clips/20250101/held.mp4", 60)
    held_meta = _clip(tmp_path, "clips/20250101/held.meta.json", 60)
    db.add_appearance(kind="spoof", start_ts=time.time() - 60 * 86400, end_ts=time.time(),
                      clip="clips/20250101/held.mp4", camera="gate-1", legal_hold=1,
                      legal_hold_reason="unreviewed_spoof")
    orphan = _clip(tmp_path, "clips/20250101/orphan.mp4", 60)
    fresh_orphan = _clip(tmp_path, "clips/20250101/new.mp4", 0)
    st = cleanup_clips_safe(db, tmp_path, retention_days=30, min_free_disk_gb=0.0, simulated_free_gb=100.0)
    assert held.exists() and held_meta.exists()
    assert not orphan.exists() and fresh_orphan.exists()
    assert st["deleted_orphan"] == 1 and st["protected_legal_hold"] == 1


# ------------------------------------------------------------------ CR10
def test_cr10_pending_audit_checkin_holds_clip(tmp_path: Path):
    db = DB(tmp_path / "t.db")
    emp_id = _employee(db)
    ev_id = _event(db, emp_id)
    # score sat nguong -> pending_audit (near_threshold_boundary)
    record_auto_checkin(db, employee_id=emp_id, code="NV001", ts=time.time(),
                        verification_tier="profile_verified", score=0.5, tau_threshold=0.45, event_id=ev_id)
    app_id = db.add_appearance(kind="employee", employee_id=emp_id, code="NV001", start_ts=time.time(),
                               end_ts=time.time(), event_id=ev_id, camera="gate-1")
    assert hold_clip_if_pending_audit(db, app_id, ev_id)
    row = db.q("SELECT legal_hold, legal_hold_reason FROM appearances WHERE id=?", (app_id,))[0]
    assert row["legal_hold"] == 1 and row["legal_hold_reason"].startswith("pending_audit")


def test_cr10_worker_track_event_mapping(monkeypatch):
    from app import main
    monkeypatch.setattr(main, "ctx", SimpleNamespace())
    main._remember_track_event("gate-1", 7, 101, 5, time.time())
    assert main._pop_track_event("gate-1", 7, 9) is None          # khac nhan vien -> khong gan
    main._remember_track_event("gate-1", 7, 101, 5, time.time())
    assert main._pop_track_event("gate-1", 7, 5) == 101
    assert main._pop_track_event("gate-1", 7, 5) is None          # da lay ra


# ------------------------------------------------------------------ CR2
def test_cr2_pipeline_in_isolated_mode_never_recognizes(tmp_path: Path):
    from app.pipeline import Pipeline

    class _Cam:
        fid = 0
        fps_in = 25.0
        connected, black = True, False

        def read(self):
            self.fid += 1
            time.sleep(0.005)
            return self.fid, np.zeros((120, 160, 3), np.uint8)

    cfg = load_config()
    cfg["recognition"]["thresholds"]["fake"] = 0.4
    db = DB(tmp_path / "t.db")
    pl = Pipeline(cfg, db, _Engine(), SimpleNamespace(version=0, size=0), _Cam(),
                  SimpleNamespace(notify=lambda: None), lambda m: None, recognize=False)
    called: list[int] = []
    pl._process = lambda frame, now: called.append(1)
    pl.start()
    time.sleep(0.4)
    pl.stop()
    assert called == []
    assert pl.jpeg is not None   # van co khung xem truoc


# ------------------------------------------------------------------ CR7
def test_cr7_postgres_init_adds_migrated_columns(monkeypatch):
    executed: list[str] = []

    class _Cur:
        rowcount = 0

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, args=()):
            executed.append(" ".join(sql.split()))

        def fetchall(self):
            return []

    class _Conn:
        def cursor(self):
            return _Cur()

    import sys
    fake_pg = SimpleNamespace(connect=lambda *a, **k: _Conn())
    monkeypatch.setitem(sys.modules, "psycopg", fake_pg)
    monkeypatch.setitem(sys.modules, "psycopg.rows", SimpleNamespace(dict_row=None))
    DB(Path("unused.db"), database_url="postgresql://u:p@localhost/fg")
    joined = "\n".join(executed)
    assert "ALTER TABLE appearances ADD COLUMN IF NOT EXISTS legal_hold INTEGER NOT NULL DEFAULT 0" in joined
    assert "ALTER TABLE events ADD COLUMN IF NOT EXISTS verification_tier TEXT" in joined
    assert "ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS yaw DOUBLE PRECISION" in joined
    assert "PRAGMA" not in joined
