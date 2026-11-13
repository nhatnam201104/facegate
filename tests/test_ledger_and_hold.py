from __future__ import annotations

import http.server
import json
import socketserver
import threading
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pytest

from app.attendance import Shift, timesheet
from app.db import DB
from app.hrm import ERPNextHRM, HRMWorker
from app.ledger import (
    list_audit_queue,
    record_auto_checkin,
    record_manual_checkin,
    resolve_audit_item,
    revoke_checkin,
)
from app.media_clips import cleanup_clips_safe
from app.security import BiometricCipher, issue_rbac_token, verify_rbac_token


def test_append_only_ledger_timesheet_derivation_and_erpnext_2way(tmp_path: Path):
    received_reqs: list[tuple[str, str, dict]] = []

    class _ERPHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            ln = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(ln).decode("utf-8")) if ln else {}
            received_reqs.append(("POST", self.path, {"idem": self.headers.get("X-Idempotency-Key"), **body}))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"data": {"name": "EMP-CKIN-2026-00099"}}).encode("utf-8"))

        def do_DELETE(self):  # noqa: N802
            received_reqs.append(("DELETE", self.path, {"idem": self.headers.get("X-Idempotency-Key")}))
            self.send_response(202)
            self.end_headers()

        def log_message(self, *args):
            pass

    with socketserver.TCPServer(("127.0.0.1", 0), _ERPHandler) as srv:
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()

        cfg = {
            "_paths": {"data": tmp_path},
            "attendance": {"shift_start": "08:00", "shift_end": "17:00", "grace_minutes": 5, "workdays": [1, 2, 3, 4, 5]},
            "hrm": {"adapter": "erpnext", "erpnext": {"url": f"http://127.0.0.1:{port}", "api_key": "k", "api_secret": "s"}},
        }
        db = DB(tmp_path / "fg.db")
        emp_id = db.create_employee("NV010", "Trần Thị B")
        db.set_status(emp_id, "active")
        # Dat approved_at som hon ngay test
        db.x("UPDATE employees SET approved_at=1000.0 WHERE id=?", (emp_id,))

        day_str = "2026-11-13"  # Thu Sau (workday=5)
        ts_in = datetime(2026, 11, 13, 8, 12, 0).timestamp()  # Di muon 12 phut
        lid = record_auto_checkin(db, emp_id, "NV010", ts_in, verification_tier="verified", score=2.5)

        worker = HRMWorker(cfg, db)
        worker.sync_once()
        worker.stop()

        row = db.q("SELECT * FROM attendance_ledger WHERE id=?", (lid,))[0]
        assert row["hrm_sync_status"] == "sent"
        assert row["remote_doc_id"] == "EMP-CKIN-2026-00099"
        assert received_reqs[0][0] == "POST" and received_reqs[0][2]["idem"] == f"auto:NV010:{day_str}"

        shift = Shift.from_cfg(cfg)
        ts1 = timesheet(db, day_str, day_str, shift, code="NV010")
        assert ts1["rows"][0]["status"] == "late" and ts1["rows"][0]["late_min"] == 12

        # HR thu hoi luot check-in nhan nham -> dong bo DELETE sang ERPNext & bang cong cap nhat ngay!
        rev_id = revoke_checkin(db, lid, idempotency_key="rev-1", reason="Nhầm đồng nghiệp đi lướt", actor="hr:lan")
        assert rev_id > lid
        erp = ERPNextHRM(cfg)
        rev_row = db.q("SELECT * FROM attendance_ledger WHERE id=?", (rev_id,))[0]
        ok, resp = erp.revoke(rev_row)
        assert ok and "EMP-CKIN-2026-00099" in resp
        assert received_reqs[1][0] == "DELETE" and "EMP-CKIN-2026-00099" in received_reqs[1][1]

        # HR nhap bu cong du phong dung gio 07:55
        man_id = record_manual_checkin(db, "NV010", day_str, "07:55:00", "man-1", "Mất điện cổng chính", actor="hr:lan")
        assert man_id > rev_id
        ts2 = timesheet(db, day_str, day_str, shift, code="NV010")
        assert ts2["rows"][0]["status"] == "ok" and ts2["rows"][0]["verification_tier"] == "manual_fallback"
        # Tat ca 3 but toan van con nguyen trong so cai append-only + audit_logs
        assert len(db.q("SELECT * FROM attendance_ledger")) == 3
        assert len(db.q("SELECT * FROM audit_logs")) >= 2
        srv.shutdown()


def test_audit_by_exception_and_legal_hold_disk_protection(tmp_path: Path):
    db = DB(tmp_path / "hold.db")
    emp_id = db.create_employee("NV020", "Lê Văn C")
    db.set_status(emp_id, "active")

    clips_dir = tmp_path / "clips" / "20261113"
    clips_dir.mkdir(parents=True)
    c1 = clips_dir / "old_normal.mp4"
    c2 = clips_dir / "disputed_hold.mp4"
    c1.write_bytes(b"0" * 1024)
    c2.write_bytes(b"1" * 1024)

    a1 = db.add_appearance(kind="employee", employee_id=emp_id, code="NV020", name="Lê Văn C",
                           start_ts=1000.0, end_ts=1003.0, clip="clips/20261113/old_normal.mp4", clip_ready=1)
    ev_id = db.add_event(ts=2000.0, kind="checkin", employee_id=emp_id, code="NV020", name="Lê Văn C")
    a2 = db.add_appearance(kind="employee", employee_id=emp_id, code="NV020", name="Lê Văn C",
                           start_ts=2000.0, end_ts=2003.0, event_id=ev_id,
                           clip="clips/20261113/disputed_hold.mp4", clip_ready=1)

    # Ca profile_verified co diem sat bien -> tu dong bat pending_audit + legal_hold = 1!
    lid = record_auto_checkin(
        db, emp_id, "NV020", 2000.0, verification_tier="profile_verified",
        score=0.50, tau_threshold=0.45, event_id=ev_id, appearance_id=a2,
    )
    assert len(list_audit_queue(db)) == 1
    assert db.q("SELECT legal_hold FROM appearances WHERE id=?", (a2,))[0]["legal_hold"] == 1

    # Gia lap dia trong chi con 0.8 GB (< 2.0 GB): a1 bi xoa, nhung a2 co legal_hold=1 duoc BAO VE TUYET DOI!
    res = cleanup_clips_safe(db, tmp_path, retention_days=30.0, min_free_disk_gb=2.0, simulated_free_gb=0.8)
    assert res["protected_legal_hold"] == 1
    assert not c1.exists()
    assert c2.exists()

    # Sau khi HR xac nhan dong ho so audit -> ha legal_hold = 0 de tuan thu retention_days (Nguyen tac IV)
    resolve_audit_item(db, lid, confirm=True, reason="Xem clip đúng người", actor="hr:lan")
    assert db.q("SELECT legal_hold FROM appearances WHERE id=?", (a2,))[0]["legal_hold"] == 0


def test_aes256_gcm_biometric_encryption_and_rbac_tokens(tmp_path: Path):
    cipher = BiometricCipher(b"super-secret-32-byte-key-1234567")
    db = DB(tmp_path / "sec.db", cipher=cipher)
    eid = db.create_employee("NV099", "Phạm Bảo Mật")
    db.set_status(eid, "active")
    orig = np.linspace(-1.0, 1.0, 128, dtype=np.float32)
    orig /= np.linalg.norm(orig)
    db.add_embedding(eid, orig, "edgeface_v4", 0.95, "thang-giua", None, pose_group="frontal", yaw=2.0, pitch=0.0)

    # Kiem tra du lieu tho tren dia da bi ma hoa AES-GCM (bat dau bang FG1$, khong doc duoc float32 goc)
    raw_blob = bytes(db.q("SELECT vec FROM embeddings WHERE employee_id=?", (eid,))[0]["vec"])
    assert raw_blob.startswith(b"FG1$")
    # Giai ma trong RAM khop tuyet doi 100%
    loaded = db.load_gallery("edgeface_v4")
    assert len(loaded) == 1 and np.allclose(loaded[0]["vec"], orig, atol=1e-6)

    tok = issue_rbac_token("lan.hr", "hr", "jwt-test-secret", ttl_sec=3600)
    claims = verify_rbac_token(tok, "jwt-test-secret")
    assert claims["sub"] == "lan.hr" and claims["role"] == "hr"
