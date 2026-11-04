"""SQLite storage (demo). Khi len server se chuyen sang PostgreSQL + pgvector."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',      -- pending | active | disabled
    created_at REAL NOT NULL,
    approved_at REAL
);
CREATE TABLE IF NOT EXISTS embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    vec BLOB NOT NULL,
    dim INTEGER NOT NULL,
    model_version TEXT NOT NULL,
    quality REAL,
    pose_bin TEXT,
    source TEXT NOT NULL DEFAULT 'enroll',
    crop_path TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS enrollments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    report TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    camera TEXT,
    employee_id INTEGER,
    code TEXT,
    name TEXT,
    score REAL,
    margin REAL,
    direction TEXT,
    latency_ms REAL,
    frames INTEGER,
    snapshot TEXT,
    model_version TEXT,
    hrm_status TEXT,             -- pending | sent | failed | skipped
    hrm_attempts INTEGER DEFAULT 0,
    hrm_response TEXT,
    label TEXT                   -- correct | wrong | unknown_employee | unknown_stranger
);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);
-- Moi lan mot khuon mat xuat hien truoc camera (1 track) + clip video
CREATE TABLE IF NOT EXISTS appearances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,          -- employee | unknown | spoof
    employee_id INTEGER,         -- khong rang buoc FK: giu lich su khi xoa nhan vien
    code TEXT,
    name TEXT,
    start_ts REAL NOT NULL,
    end_ts REAL NOT NULL,
    score REAL,
    liveness REAL,
    snapshot TEXT,
    clip TEXT,
    clip_ready INTEGER NOT NULL DEFAULT 0,
    event_id INTEGER,
    camera TEXT
);
CREATE INDEX IF NOT EXISTS ix_app_ts ON appearances(start_ts);
CREATE INDEX IF NOT EXISTS ix_app_code ON appearances(code);
"""

# Cot them sau phien ban dau (DB cu duoc nang cap khi khoi dong)
EVENT_COLUMNS = {
    "kind": "TEXT",              # checkin | unknown | spoof
    "att_type": "TEXT",          # in (du lieu cu co the co out)
    "liveness": "REAL",          # xac suat nguoi that trung binh cua track
}


class DB:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.lock = threading.RLock()
        self._migrate()

    def _migrate(self) -> None:
        have = {r["name"] for r in self.q("PRAGMA table_info(events)")}
        for col, typ in EVENT_COLUMNS.items():
            if col not in have:
                self.x(f"ALTER TABLE events ADD COLUMN {col} {typ}")
        self.x("""UPDATE events SET kind = CASE WHEN employee_id IS NULL THEN 'unknown' ELSE 'checkin' END
                  WHERE kind IS NULL""")

    def q(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def x(self, sql: str, args: tuple = ()) -> int:
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur.lastrowid

    # --- employees ---
    def get_employee_by_code(self, code: str) -> dict | None:
        r = self.q("SELECT * FROM employees WHERE code=?", (code,))
        return r[0] if r else None

    def create_employee(self, code: str, name: str) -> int:
        return self.x("INSERT INTO employees(code,name,status,created_at) VALUES(?,?,?,?)",
                      (code, name, "pending", time.time()))

    def list_employees(self, model_version: str) -> list[dict]:
        return self.q("""
            SELECT e.*,
              (SELECT COUNT(*) FROM embeddings m WHERE m.employee_id=e.id AND m.model_version=?) AS n_emb,
              (SELECT crop_path FROM embeddings m WHERE m.employee_id=e.id ORDER BY quality DESC LIMIT 1) AS thumb,
              (SELECT report FROM enrollments n WHERE n.employee_id=e.id ORDER BY id DESC LIMIT 1) AS report
            FROM employees e ORDER BY e.created_at DESC""", (model_version,))

    def set_status(self, emp_id: int, status: str) -> None:
        self.x("UPDATE employees SET status=?, approved_at=? WHERE id=?",
               (status, time.time() if status == "active" else None, emp_id))

    def delete_employee(self, emp_id: int) -> list[str]:
        crops = [r["crop_path"] for r in self.q("SELECT crop_path FROM embeddings WHERE employee_id=?", (emp_id,))]
        self.x("DELETE FROM employees WHERE id=?", (emp_id,))
        return [c for c in crops if c]

    # --- embeddings ---
    def add_embedding(self, emp_id: int, vec: np.ndarray, model_version: str, quality: float,
                      pose_bin: str, crop_path: str | None, source: str = "enroll") -> None:
        v = vec.astype(np.float32)
        self.x("""INSERT INTO embeddings(employee_id,vec,dim,model_version,quality,pose_bin,source,crop_path,created_at)
                  VALUES(?,?,?,?,?,?,?,?,?)""",
               (emp_id, v.tobytes(), v.size, model_version, quality, pose_bin, source, crop_path, time.time()))

    def clear_embeddings(self, emp_id: int, model_version: str) -> None:
        self.x("DELETE FROM embeddings WHERE employee_id=? AND model_version=?", (emp_id, model_version))

    def load_gallery(self, model_version: str, statuses: tuple = ("active",)) -> list[dict]:
        ph = ",".join("?" * len(statuses))
        rows = self.q(f"""SELECT m.vec, m.dim, e.id AS employee_id, e.code, e.name, e.status
                          FROM embeddings m JOIN employees e ON e.id=m.employee_id
                          WHERE m.model_version=? AND e.status IN ({ph})""", (model_version, *statuses))
        for r in rows:
            r["vec"] = np.frombuffer(r["vec"], dtype=np.float32, count=r["dim"])
        return rows

    def add_enrollment(self, emp_id: int, report: dict) -> None:
        self.x("INSERT INTO enrollments(employee_id,created_at,report) VALUES(?,?,?)",
               (emp_id, time.time(), json.dumps(report, ensure_ascii=False)))

    # --- events ---
    def add_event(self, **kw) -> int:
        cols = ",".join(kw)
        return self.x(f"INSERT INTO events({cols}) VALUES({','.join('?' * len(kw))})", tuple(kw.values()))

    def list_events(self, limit: int = 200) -> list[dict]:
        return self.q("SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,))

    def first_checkin_between(self, emp_id: int, t0: float, t1: float) -> float | None:
        r = self.q("SELECT MIN(ts) AS t FROM events WHERE employee_id=? AND kind='checkin' AND ts>=? AND ts<?",
                   (emp_id, t0, t1))
        return r[0]["t"] if r else None

    def checkins_between(self, t0: float, t1: float, code: str | None = None) -> list[dict]:
        sql = "SELECT * FROM events WHERE kind='checkin' AND ts>=? AND ts<?"
        args: tuple = (t0, t1)
        if code:
            sql, args = sql + " AND code=?", (*args, code)
        return self.q(sql + " ORDER BY ts", args)

    def count_kind_between(self, kind: str, t0: float, t1: float) -> int:
        return self.q("SELECT COUNT(*) AS n FROM events WHERE kind=? AND ts>=? AND ts<?", (kind, t0, t1))[0]["n"]

    # --- appearances ---
    def add_appearance(self, **kw) -> int:
        cols = ",".join(kw)
        return self.x(f"INSERT INTO appearances({cols}) VALUES({','.join('?' * len(kw))})", tuple(kw.values()))

    def set_clip(self, app_id: int, rel_path: str | None) -> None:
        """rel_path: duong dan clip da ghi xong (tuong doi thu muc data), None neu khong co clip."""
        self.x("UPDATE appearances SET clip=?, clip_ready=? WHERE id=?", (rel_path, 1 if rel_path else 0, app_id))

    def list_appearances(self, t0: float, t1: float, code: str | None = None, kind: str | None = None,
                         limit: int = 500) -> list[dict]:
        sql, args = "SELECT * FROM appearances WHERE start_ts>=? AND start_ts<?", [t0, t1]
        if code:
            sql += " AND code=?"
            args.append(code)
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        return self.q(sql + " ORDER BY start_ts DESC LIMIT ?", (*args, limit))

    def people_seen(self) -> list[dict]:
        """Nguoi da tung xuat hien/cham cong (ke ca nhan vien da xoa) de loc lich su."""
        return self.q("""SELECT code, name FROM (
                            SELECT code, name, start_ts AS t FROM appearances WHERE code IS NOT NULL AND kind='employee'
                            UNION ALL SELECT code, name, ts FROM events WHERE kind='checkin')
                         GROUP BY code ORDER BY name""")

    def label_event(self, ev_id: int, label: str | None) -> None:
        self.x("UPDATE events SET label=? WHERE id=?", (label, ev_id))

    def pending_hrm(self, max_attempts: int = 5) -> list[dict]:
        return self.q("""SELECT * FROM events WHERE hrm_status IN ('pending','failed')
                         AND hrm_attempts < ? ORDER BY ts LIMIT 20""", (max_attempts,))

    def set_hrm(self, ev_id: int, status: str, response: str) -> None:
        self.x("UPDATE events SET hrm_status=?, hrm_response=?, hrm_attempts=hrm_attempts+1 WHERE id=?",
               (status, response[:500], ev_id))
