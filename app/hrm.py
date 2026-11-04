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
import urllib.request
from datetime import datetime

log = logging.getLogger("hrm")


class MockHRM:
    def __init__(self, cfg: dict):
        self.path = cfg["_paths"]["data"] / "hrm_mock.jsonl"

    def push(self, ev: dict) -> tuple[bool, str]:
        rec = {"employee": ev["code"], "time": _fmt(ev["ts"]), "log_type": ev.get("direction") or None,
               "device_id": ev.get("camera"), "event_id": ev["id"]}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True, "mock: ghi vao hrm_mock.jsonl"


class ERPNextHRM:
    def __init__(self, cfg: dict):
        c = cfg["hrm"]["erpnext"]
        self.url = c["url"].rstrip("/") + "/api/resource/Employee%20Checkin"
        self.auth = f"token {c['api_key']}:{c['api_secret']}"
        self.device = c.get("device_id")

    def push(self, ev: dict) -> tuple[bool, str]:
        body = {"employee": ev["code"], "time": _fmt(ev["ts"]), "device_id": self.device or ev.get("camera")}
        if ev.get("direction") in ("IN", "OUT"):
            body["log_type"] = ev["direction"]
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": self.auth, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return True, f"{r.status}"
        except urllib.error.HTTPError as e:
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

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=10)
            self._wake.clear()
            for ev in self.db.pending_hrm():
                ok, resp = self.adapter.push(ev)
                self.db.set_hrm(ev["id"], "sent" if ok else "failed", resp)
                if not ok:
                    log.warning("Gui HRM that bai event %s: %s", ev["id"], resp)
                if self.on_update:
                    self.on_update(ev["id"], "sent" if ok else "failed")
            time.sleep(0.2)
