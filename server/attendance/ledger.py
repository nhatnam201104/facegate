"""So cai cham cong bat bien Append-Only (attendance_ledger) & Quy trinh Hau kiem Ngoai le Audit-by-Exception (H7, H15, H22)."""
from __future__ import annotations

import hashlib
from datetime import date, datetime, time as dtime

VALID_ACTIONS = {"checkin_created", "checkin_revoked", "checkin_manual"}


def _deterministic_sample_hit(idempotency_key: str, sample_rate: float) -> bool:
    if sample_rate <= 0.0:
        return False
    if sample_rate >= 1.0:
        return True
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).digest()
    val = int.from_bytes(digest[:4], "big") / float(0xFFFFFFFF)
    return val < sample_rate


def should_flag_for_audit(
    db,
    verification_tier: str,
    score: float,
    tau_threshold: float,
    ts: float,
    idempotency_key: str,
    audit_cfg: dict | None = None,
) -> tuple[bool, str | None]:
    """Audit-by-Exception (H7): tu dong gan pending_audit cho luot profile_verified khi:
    (a) sat bien xac suat / LLR (< tau + 0.35), (b) co canh bao spoof trong 5 phut lan can, hoac (c) lay mau ngau nhien 5%.
    """
    if verification_tier != "profile_verified":
        return False, None
    ac = audit_cfg or {}
    if float(score) < float(tau_threshold) + 0.35:
        return True, "near_threshold_boundary"
    win = float(ac.get("near_spoof_window_sec", 300.0))
    spoof_near = db.count_kind_between("spoof", ts - win, ts + win)
    if spoof_near > 0:
        return True, "near_spoof_alert"
    rate = float(ac.get("profile_audit_sample_rate", 0.05))
    if _deterministic_sample_hit(idempotency_key, rate):
        return True, "random_5pct_audit_sample"
    return False, None


def record_auto_checkin(
    db,
    employee_id: int,
    code: str,
    ts: float,
    verification_tier: str = "verified",
    score: float = 1.0,
    tau_threshold: float = 0.45,
    camera_id: str = "gate-1",
    event_id: int | None = None,
    appearance_id: int | None = None,
    audit_cfg: dict | None = None,
) -> int:
    work_date = datetime.fromtimestamp(ts).date().isoformat()
    idem_key = f"auto:{code}:{work_date}"
    flagged, reason = should_flag_for_audit(
        db, verification_tier, score, tau_threshold, ts, idem_key, audit_cfg=audit_cfg,
    )
    audit_st = "pending_audit" if flagged else "not_required"
    lid = db.append_ledger(
        idempotency_key=idem_key,
        action="checkin_created",
        employee_id=int(employee_id),
        code=code,
        work_date=work_date,
        effective_ts=float(ts),
        verification_tier=verification_tier,
        audit_status=audit_st,
        actor=f"system:{camera_id}",
        reason=reason,
        event_id=event_id,
        hrm_sync_status="pending",
    )
    if flagged and appearance_id is not None:
        db.set_legal_hold(appearance_id, True, f"pending_audit:{reason}")
    return lid


def hold_clip_if_pending_audit(db, appearance_id: int | None, event_id: int | None) -> bool:
    """Appearance thuong duoc tao SAU check-in (khi track dong), nen record_auto_checkin chua the giu clip.
    Goi ham nay ngay khi tao appearance: neu luot check-in cua event dang pending_audit thi dat legal_hold (CR10)."""
    if not appearance_id or not event_id:
        return False
    rows = db.q(
        """SELECT reason FROM attendance_ledger
           WHERE event_id=? AND action='checkin_created' AND audit_status='pending_audit'""",
        (int(event_id),),
    )
    if not rows:
        return False
    db.set_legal_hold(int(appearance_id), True, f"pending_audit:{rows[0].get('reason') or ''}")
    return True


def revoke_checkin(
    db,
    target_ledger_id: int,
    idempotency_key: str,
    reason: str,
    actor: str = "hr:admin",
    ip_addr: str | None = None,
) -> int:
    if not reason or not reason.strip():
        raise ValueError("Phai nhap ly do khi thu hoi cham cong")
    rows = db.q("SELECT * FROM attendance_ledger WHERE id=?", (target_ledger_id,))
    if not rows:
        raise LookupError(f"Khong tim thay but toan #{target_ledger_id}")
    orig = rows[0]
    if orig["action"] == "checkin_revoked":
        raise ValueError("But toan nay da la lenh thu hoi")
    already = db.q(
        "SELECT id FROM attendance_ledger WHERE action='checkin_revoked' AND target_ledger_id=?",
        (target_ledger_id,),
    )
    if already:
        return int(already[0]["id"])

    # Danh dau audit_revoked TRUOC khi huy sync: neu HRMWorker dang day dong goc dung luc nay
    # va thanh cong, no se thay audit_revoked va mo lai lenh xoa (requeue_revoke_after_late_sync).
    db.x("UPDATE attendance_ledger SET audit_status='audit_revoked' WHERE id=?", (target_ledger_id,))
    already_synced = bool(orig.get("remote_doc_id") or orig.get("hrm_sync_status") == "sent")
    # Ban goc chua len HRM: ghi lenh thu hoi o trang thai 'skipped' TRUOC, roi moi huy ban goc.
    # Thu tu nay dam bao HRMWorker (neu dang day dong goc) luon tim thay dong thu hoi de mo lai.
    sync_st = "pending" if already_synced else "skipped"
    lid = db.append_ledger(
        idempotency_key=idempotency_key,
        action="checkin_revoked",
        target_ledger_id=int(target_ledger_id),
        employee_id=int(orig["employee_id"]),
        code=orig["code"],
        work_date=orig["work_date"],
        effective_ts=float(orig["effective_ts"]),
        verification_tier=orig["verification_tier"],
        audit_status="audit_revoked",
        actor=actor,
        reason=reason.strip(),
        event_id=orig.get("event_id"),
        remote_doc_id=orig.get("remote_doc_id"),
        hrm_sync_status=sync_st,
    )
    if not already_synced and not db.cancel_ledger_hrm(int(target_ledger_id)):
        # Khong huy duoc = ban goc vua len HRM giua chung -> chuyen lenh thu hoi sang 'pending'
        cur = db.q("SELECT hrm_sync_status, remote_doc_id FROM attendance_ledger WHERE id=?",
                   (target_ledger_id,))[0]
        if cur.get("hrm_sync_status") == "sent" or cur.get("remote_doc_id"):
            db.requeue_revoke_after_late_sync(int(target_ledger_id), cur.get("remote_doc_id") or "")
    db.add_audit_log(actor, "revoke_checkin", "attendance_ledger", str(target_ledger_id),
                     {"revoke_ledger_id": lid, "reason": reason.strip()}, ip_addr=ip_addr)
    return lid


def record_manual_checkin(
    db,
    employee_code: str,
    work_date: str,
    effective_time: str,
    idempotency_key: str,
    reason: str,
    actor: str = "hr:admin",
    ip_addr: str | None = None,
) -> int:
    if not reason or not reason.strip():
        raise ValueError("Phai nhap ly do khi cham cong du phong / nhap bu")
    emp = db.get_employee_by_code(employee_code)
    if not emp:
        raise LookupError(f"Khong tim thay nhan vien ma {employee_code}")
    d = date.fromisoformat(work_date)
    parts = [int(x) for x in effective_time.strip().split(":")]
    tm = dtime(parts[0], parts[1], parts[2] if len(parts) > 2 else 0)
    eff_ts = datetime.combine(d, tm).timestamp()

    lid = db.append_ledger(
        idempotency_key=idempotency_key,
        action="checkin_manual",
        employee_id=int(emp["id"]),
        code=emp["code"],
        work_date=d.isoformat(),
        effective_ts=eff_ts,
        verification_tier="manual_fallback",
        audit_status="audit_confirmed",
        actor=actor,
        reason=reason.strip(),
        hrm_sync_status="pending",
    )
    db.add_audit_log(actor, "manual_checkin", "attendance_ledger", str(lid),
                     {"code": emp["code"], "work_date": d.isoformat(), "reason": reason.strip()}, ip_addr=ip_addr)
    return lid


def resolve_audit_item(db, ledger_id: int, confirm: bool, reason: str = "",
                       actor: str = "hr:admin", ip_addr: str | None = None) -> dict:
    """HR duyet xac nhan hoac thu hoi 1 muc pending_audit; dong ho so thi ha legal_hold = 0 de tuan thu retention_days (H15)."""
    rows = db.q("SELECT * FROM attendance_ledger WHERE id=?", (ledger_id,))
    if not rows:
        raise LookupError(f"Khong tim thay but toan #{ledger_id}")
    item = rows[0]
    if confirm:
        db.x("UPDATE attendance_ledger SET audit_status='audit_confirmed' WHERE id=?", (ledger_id,))
        if item.get("event_id"):
            apps = db.q("SELECT id, legal_hold_reason FROM appearances WHERE event_id=?", (item["event_id"],))
            for a in apps:
                if str(a.get("legal_hold_reason") or "").startswith("pending_audit"):
                    db.set_legal_hold(int(a["id"]), False, None)
        db.add_audit_log(actor, "confirm_audit", "attendance_ledger", str(ledger_id), {"reason": reason}, ip_addr=ip_addr)
        return {"ok": True, "status": "audit_confirmed"}

    rev_id = revoke_checkin(
        db, ledger_id,
        idempotency_key=f"audit-revoke-{ledger_id}",
        reason=reason or "Thu hoi qua hau kiem clip",
        actor=actor, ip_addr=ip_addr,
    )
    if item.get("event_id"):
        apps = db.q("SELECT id, legal_hold_reason FROM appearances WHERE event_id=?", (item["event_id"],))
        for a in apps:
            if str(a.get("legal_hold_reason") or "").startswith("pending_audit"):
                db.set_legal_hold(int(a["id"]), False, None)
    return {"ok": True, "status": "audit_revoked", "revoke_ledger_id": rev_id}


def list_audit_queue(db, date_from: str | None = None, date_to: str | None = None) -> list[dict]:
    sql = "SELECT * FROM attendance_ledger WHERE audit_status='pending_audit'"
    args: list = []
    if date_from:
        sql += " AND work_date>=?"
        args.append(date_from)
    if date_to:
        sql += " AND work_date<=?"
        args.append(date_to)
    return db.q(sql + " ORDER BY work_date DESC, id DESC", tuple(args))


def effective_checkins_between(db, d0: date, d1: date, code: str | None = None) -> dict[tuple[date, str], dict]:
    """Suy dan trang thai cham cong hieu luc tu so cai append-only (ket hop bang events cu de tuong thich nguoc)."""
    t0 = datetime.combine(d0, dtime.min).timestamp()
    t1 = datetime.combine(d1, dtime.max).timestamp()

    result: dict[tuple[date, str], dict] = {}
    name_map = {r["code"]: r["name"] for r in db.q("SELECT code, name FROM employees")}

    # 1. Du lieu cu tu bang events (neu chua co trong attendance_ledger)
    ev_rows = db.checkins_between(t0, t1 + 1.0, code)
    for e in ev_rows:
        day = datetime.fromtimestamp(e["ts"]).date()
        key = (day, e["code"])
        name_map.setdefault(e["code"], e.get("name") or e["code"])
        if key not in result or e["ts"] < result[key]["ts"]:
            result[key] = {
                "ts": float(e["ts"]),
                "code": e["code"],
                "name": name_map.get(e["code"], e["code"]),
                "ledger_id": None,
                "verification_tier": e.get("verification_tier") or "verified",
                "audit_status": "not_required",
            }

    # 2. Ap dung tuan tu chuoi su kien bat bien trong attendance_ledger (co quyen ghi de / thu hoi)
    sql = "SELECT * FROM attendance_ledger WHERE work_date>=? AND work_date<=?"
    args: list = [d0.isoformat(), d1.isoformat()]
    if code:
        sql += " AND code=?"
        args.append(code)
    ledger_rows = db.q(sql + " ORDER BY id ASC", tuple(args))

    active_by_id: dict[int, dict] = {}
    days_managed_by_ledger: set[tuple[date, str]] = set()
    for lr in ledger_rows:
        day = date.fromisoformat(lr["work_date"])
        days_managed_by_ledger.add((day, lr["code"]))
        act = lr["action"]
        if act in ("checkin_created", "checkin_manual"):
            active_by_id[int(lr["id"])] = lr
        elif act == "checkin_revoked" and lr.get("target_ledger_id") is not None:
            active_by_id.pop(int(lr["target_ledger_id"]), None)

    for key in days_managed_by_ledger:
        result.pop(key, None)

    for lr in active_by_id.values():
        day = date.fromisoformat(lr["work_date"])
        key = (day, lr["code"])
        cand = {
            "ts": float(lr["effective_ts"]),
            "code": lr["code"],
            "name": name_map.get(lr["code"], lr["code"]),
            "ledger_id": int(lr["id"]),
            "verification_tier": lr["verification_tier"],
            "audit_status": lr["audit_status"],
        }
        if key not in result or cand["ts"] < result[key]["ts"]:
            result[key] = cand

    return result
