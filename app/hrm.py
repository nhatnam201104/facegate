"""Adapter day su kien cham cong sang HRM, co outbox + retry.

- mock    : ghi ra data/hrm_mock.jsonl (demo khi chua co HRM)
- erpnext : POST /api/resource/Employee Checkin (ERPNext / Frappe HRMS)
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

log = logging.getLogger("hrm")


class MockHRM:
    def __init__(self, cfg: dict):
        self.path = cfg["_paths"]["data"] / "hrm_mock.jsonl"

    def push(self, ev: dict) -> tuple[bool, str]:
        rec = {
            "employee": ev["code"],
            "time": _fmt(ev.get("ts") or ev.get("effective_ts")),
            "log_type": ev.get("direction") or "IN",
            "action": ev.get("action", "checkin_created"),
            "idempotency_key": ev.get("idempotency_key"),
            "device_id": ev.get("camera") or "gate-1",
            "event_id": ev["id"],
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True, f"MOCK-CKIN-{ev['id']}"

    def revoke(self, ev: dict) -> tuple[bool, str]:
        rec = {
            "action": "checkin_revoked",
            "remote_doc_id": ev.get("remote_doc_id"),
            "idempotency_key": ev.get("idempotency_key"),
            "employee": ev["code"],
            "reason": ev.get("reason"),
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True, f"revoked:{ev.get('remote_doc_id')}"


class ERPNextHRM:
    def __init__(self, cfg: dict):
        c = cfg["hrm"]["erpnext"]
        self.base_url = c["url"].rstrip("/")
        self.url = self.base_url + "/api/resource/Employee%20Checkin"
        self.auth = f"token {c['api_key']}:{c['api_secret']}"
        self.device = c.get("device_id")

    def push(self, ev: dict) -> tuple[bool, str]:
        ts_val = ev.get("ts") or ev.get("effective_ts")
        body = {
            "employee": ev["code"],
            "time": _fmt(ts_val),
            "device_id": self.device or ev.get("camera") or "gate-1",
        }
        if ev.get("direction") in ("IN", "OUT"):
            body["log_type"] = ev["direction"]
        headers = {"Authorization": self.auth, "Content-Type": "application/json"}
        if ev.get("idempotency_key"):
            headers["X-Idempotency-Key"] = str(ev["idempotency_key"])
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                raw = r.read().decode("utf-8", errors="replace")
                doc_id = ""
                try:
                    doc_id = str((json.loads(raw).get("data") or {}).get("name") or "")
                except Exception:  # noqa: BLE001
                    pass
                return True, doc_id or f"{r.status}"
        except urllib.error.HTTPError as e:
            return False, f"HTTP {e.code}: {e.read()[:200]!r}"
        except Exception as e:  # noqa: BLE001
            return False, str(e)

    def revoke(self, ev: dict) -> tuple[bool, str]:
        doc_id = (ev.get("remote_doc_id") or "").strip()
        if not doc_id:
            return True, "skipped:no_remote_doc_id"
        del_url = f"{self.url}/{urllib.parse.quote(doc_id)}"
        headers = {"Authorization": self.auth}
        if ev.get("idempotency_key"):
            headers["X-Idempotency-Key"] = str(ev["idempotency_key"])
        req = urllib.request.Request(del_url, method="DELETE", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return True, f"deleted:{doc_id}:{r.status}"
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return True, f"already_deleted:{doc_id}"
            return False, f"HTTP {e.code}: {e.read()[:200]!r}"
        except Exception as e:  # noqa: BLE001
            return False, str(e)


def _fmt(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


class HRMWorker:
    """Doc event pending trong DB (outbox) va gui sang HRM; that bai thi thu lai."""

    def __init__(self, cfg: dict, db, on_update=None):
        kind = cfg["hrm"]["adapter"]
        self.adapter = ERPNextHRM(cfg) if kind == "erpnext" else MockHRM(cfg)
        self.kind = kind
        self.db = db
        self.on_update = on_update
        self._wake = threading.Event()
        self._stop = threading.Event()
        threading.Thread(target=self._run, daemon=True, name="hrm").start()

    def notify(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def sync_once(self) -> None:
        for ev in self.db.pending_hrm():
            ok, resp = self.adapter.push(ev)
            self.db.set_hrm(ev["id"], "sent" if ok else "failed", resp)
            if not ok:
                log.warning("Gui HRM that bai event %s: %s", ev["id"], resp)
            if self.on_update:
                self.on_update(ev["id"], "sent" if ok else "failed")

        if hasattr(self.db, "pending_ledger_hrm"):
            for item in self.db.pending_ledger_hrm():
                if item["action"] == "checkin_revoked":
                    ok, resp = self.adapter.revoke(item)
                    self.db.set_ledger_hrm(item["id"], "sent" if ok else "failed", error=None if ok else resp)
                else:
                    ok, resp = self.adapter.push(item)
                    self.db.set_ledger_hrm(
                        item["id"],
                        "sent" if ok else "failed",
                        remote_doc_id=resp if ok else None,
                        error=None if ok else resp,
                    )
                    if ok:
                        # Race: HR thu hoi trong luc dang day -> ban goc da len HRM, phai gui lenh xoa bu.
                        cur = self.db.q("SELECT audit_status FROM attendance_ledger WHERE id=?", (item["id"],))
                        if cur and cur[0]["audit_status"] == "audit_revoked":
                            if self.db.requeue_revoke_after_late_sync(int(item["id"]), resp):
                                log.warning("Ledger #%s da bi thu hoi khi dang day HRM -> xep lenh xoa %s",
                                            item["id"], resp)
                                self._wake.set()
                    if item.get("event_id") and item["action"] == "checkin_created":
                        st = "sent" if ok else "failed"
                        self.db.mirror_event_hrm(int(item["event_id"]), st, resp)
                        if self.on_update:
                            self.on_update(int(item["event_id"]), st)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=10)
            self._wake.clear()
            self.sync_once()
            time.sleep(0.2)
