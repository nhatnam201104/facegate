"""SQLite storage (demo). Khi len server se chuyen sang PostgreSQL + pgvector."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import numpy as np

# Phai trung voi app.security.MAGIC_PREFIX (khong import de tranh keo fastapi vao tang DB)
ENCRYPTED_PREFIX = b"FG1$"

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

CREATE TABLE IF NOT EXISTS attendance_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    idempotency_key TEXT UNIQUE NOT NULL,
    action TEXT NOT NULL,
    target_ledger_id INTEGER NULL,
    employee_id INTEGER NOT NULL,
    code TEXT NOT NULL,
    work_date TEXT NOT NULL,
    effective_ts REAL NOT NULL,
    verification_tier TEXT NOT NULL,
    audit_status TEXT NOT NULL DEFAULT 'not_required',
    actor TEXT NOT NULL,
    reason TEXT NULL,
    event_id INTEGER NULL,
    hrm_sync_status TEXT NOT NULL DEFAULT 'pending',
    remote_doc_id TEXT NULL,
    hrm_attempts INTEGER DEFAULT 0,
    hrm_last_error TEXT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ledger_date_code ON attendance_ledger(work_date, code);
CREATE INDEX IF NOT EXISTS ix_ledger_hrm ON attendance_ledger(hrm_sync_status);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    ts REAL NOT NULL,
    actor TEXT NOT NULL,
    ip_addr TEXT,
    action TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    detail_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_audit_ts ON audit_logs(ts);
"""

EMBEDDING_COLUMNS = {
    "pose_group": "TEXT NOT NULL DEFAULT 'frontal'",
    "yaw": "REAL",
    "pitch": "REAL",
}

# Cot them sau phien ban dau (DB cu duoc nang cap khi khoi dong)
EVENT_COLUMNS = {
    "kind": "TEXT",              # checkin | unknown | spoof
    "att_type": "TEXT",          # in (du lieu cu co the co out)
    "liveness": "REAL",          # xac suat nguoi that trung binh cua track
    "pose_group": "TEXT",
    "yaw": "REAL",
    "verification_tier": "TEXT",
    "liveness_mode": "TEXT",
    "liveness_detail": "TEXT",
}

APPEARANCE_COLUMNS = {
    "pose_group": "TEXT",
    "min_abs_yaw": "REAL",
    "verification_tier": "TEXT",
    "spoof_reason": "TEXT",
    "legal_hold": "INTEGER NOT NULL DEFAULT 0",
    "legal_hold_reason": "TEXT",
    "clip_sha256": "TEXT",
    "sidecar_path": "TEXT",
}

MIGRATED_COLUMNS = {
    "embeddings": EMBEDDING_COLUMNS,
    "events": EVENT_COLUMNS,
    "appearances": APPEARANCE_COLUMNS,
}


class DB:
    def __init__(self, path: Path, database_url: str = "", cipher=None):
        self.database_url = (database_url or "").strip()
        self.is_postgres = self.database_url.startswith(("postgresql://", "postgres://"))
        self.cipher = cipher
        self.lock = threading.RLock()
        if self.is_postgres:
            import psycopg
            from psycopg.rows import dict_row
            self.conn = psycopg.connect(self.database_url, autocommit=True, row_factory=dict_row)
            self._init_postgres()
        else:
            self.conn = sqlite3.connect(str(path), check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(SCHEMA)
            self._migrate()

    def _init_postgres(self) -> None:
        pg_schema = (
            SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
            .replace("BLOB", "BYTEA")
            .replace("REAL", "DOUBLE PRECISION")
        )
        with self.lock, self.conn.cursor() as cur:
            for stmt in [s.strip() for s in pg_schema.split(";") if s.strip()]:
                cur.execute(stmt)
            # Cac cot them sau phien ban dau: SQLite them trong _migrate, Postgres phai them o day (CR7)
            for table, cols in MIGRATED_COLUMNS.items():
                for col, typ in cols.items():
                    pg_typ = typ.replace("BLOB", "BYTEA").replace("REAL", "DOUBLE PRECISION")
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {pg_typ}")
        self._backfill_columns()

    def _migrate(self) -> None:
        for table, cols in MIGRATED_COLUMNS.items():
            have = {r["name"] for r in self.q(f"PRAGMA table_info({table})")}
            for col, typ in cols.items():
                if col not in have:
                    self.x(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        self._backfill_columns()

    def _backfill_columns(self) -> None:
        """Dien gia tri mac dinh cho dong cu sau khi them cot (chung cho SQLite va Postgres)."""
        self.x("UPDATE embeddings SET pose_group='frontal' WHERE pose_group IS NULL OR pose_group=''")
        self.x("""UPDATE events SET kind = CASE WHEN employee_id IS NULL THEN 'unknown' ELSE 'checkin' END
                  WHERE kind IS NULL""")
        self.x("""UPDATE events SET
                    pose_group = COALESCE(pose_group, 'frontal'),
                    verification_tier = COALESCE(verification_tier,
                        CASE WHEN kind='checkin' THEN 'verified' WHEN kind='spoof' THEN 'rejected_spoof' ELSE 'unverified' END)""")
        self.x("UPDATE appearances SET pose_group = COALESCE(pose_group, 'frontal'), legal_hold = COALESCE(legal_hold, 0)")

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.is_postgres else sql

    def q(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.lock:
            if self.is_postgres:
                with self.conn.cursor() as cur:
                    cur.execute(self._sql(sql), args)
                    return [dict(r) for r in cur.fetchall()]
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def x(self, sql: str, args: tuple = ()) -> int:
        with self.lock:
            if self.is_postgres:
                with self.conn.cursor() as cur:
                    s = self._sql(sql)
                    if s.lstrip().upper().startswith("INSERT") and "RETURNING" not in s.upper():
                        cur.execute(s + " RETURNING id", args)
                        row = cur.fetchone()
                        return int(row["id"]) if row and "id" in row else 0
                    cur.execute(s, args)
                    return 0
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur.lastrowid

    def x_rowcount(self, sql: str, args: tuple = ()) -> int:
        """Chay UPDATE/DELETE va tra ve so dong bi anh huong (dung cho cap nhat co dieu kien)."""
        with self.lock:
            if self.is_postgres:
                with self.conn.cursor() as cur:
                    cur.execute(self._sql(sql), args)
                    return int(cur.rowcount or 0)
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return int(cur.rowcount or 0)

    # --- employees ---
    def get_employee_by_code(self, code: str) -> dict | None:
        r = self.q("SELECT * FROM employees WHERE code=?", (code,))
        return r[0] if r else None

    def create_employee(self, code: str, name: str) -> int:
        return self.x("INSERT INTO employees(code,name,status,created_at) VALUES(?,?,?,?)",
                      (code, name, "pending", time.time()))

    def list_employees(self, model_version: str) -> list[dict]:
        rows = self.q("""
            SELECT e.*,
              (SELECT COUNT(*) FROM embeddings m WHERE m.employee_id=e.id AND m.model_version=?) AS n_emb,
              (SELECT COUNT(*) FROM embeddings m WHERE m.employee_id=e.id AND m.model_version=? AND m.pose_group='frontal') AS n_frontal,
              (SELECT COUNT(*) FROM embeddings m WHERE m.employee_id=e.id AND m.model_version=? AND m.pose_group='left_profile') AS n_left_profile,
              (SELECT COUNT(*) FROM embeddings m WHERE m.employee_id=e.id AND m.model_version=? AND m.pose_group='right_profile') AS n_right_profile,
              COALESCE(
                (SELECT crop_path FROM embeddings m WHERE m.employee_id=e.id AND m.model_version=? ORDER BY quality DESC LIMIT 1),
                (SELECT crop_path FROM embeddings m WHERE m.employee_id=e.id ORDER BY quality DESC LIMIT 1)
              ) AS thumb,
              (SELECT report FROM enrollments n WHERE n.employee_id=e.id ORDER BY id DESC LIMIT 1) AS report
            FROM employees e ORDER BY e.created_at DESC""",
            (model_version, model_version, model_version, model_version, model_version))
        for r in rows:
            nf = int(r.pop("n_frontal", 0) or 0)
            nl = int(r.pop("n_left_profile", 0) or 0)
            nr = int(r.pop("n_right_profile", 0) or 0)
            r["pose_coverage"] = {
                "frontal": nf,
                "left_profile": nl,
                "right_profile": nr,
                "ready_90": bool(nf > 0 and nl > 0 and nr > 0),
            }
        return rows

    def set_status(self, emp_id: int, status: str) -> None:
        self.x("UPDATE employees SET status=?, approved_at=? WHERE id=?",
               (status, time.time() if status == "active" else None, emp_id))

    def delete_employee(self, emp_id: int) -> list[str]:
        crops = [r["crop_path"] for r in self.q("SELECT crop_path FROM embeddings WHERE employee_id=?", (emp_id,))]
        self.x("DELETE FROM employees WHERE id=?", (emp_id,))
        return [c for c in crops if c]

    # --- embeddings ---
    def add_embedding(self, emp_id: int, vec: np.ndarray, model_version: str, quality: float,
                      pose_bin: str, crop_path: str | None, source: str = "enroll",
                      pose_group: str = "frontal", yaw: float | None = None, pitch: float | None = None) -> None:
        v = vec.astype(np.float32)
        blob = self.cipher.encrypt_bytes(v.tobytes()) if self.cipher is not None else v.tobytes()
        self.x("""INSERT INTO embeddings(employee_id,vec,dim,model_version,quality,pose_bin,source,crop_path,created_at,pose_group,yaw,pitch)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
               (emp_id, blob, v.size, model_version, quality, pose_bin, source, crop_path, time.time(),
                pose_group or "frontal", yaw, pitch))

    def clear_embeddings(self, emp_id: int, model_version: str) -> None:
        self.x("DELETE FROM embeddings WHERE employee_id=? AND model_version=?", (emp_id, model_version))

    def load_gallery(self, model_version: str, statuses: tuple = ("active",)) -> list[dict]:
        ph = ",".join("?" * len(statuses))
        rows = self.q(f"""SELECT m.vec, m.dim, m.pose_group, m.yaw, e.id AS employee_id, e.code, e.name, e.status
                          FROM embeddings m JOIN employees e ON e.id=m.employee_id
                          WHERE m.model_version=? AND e.status IN ({ph})""", (model_version, *statuses))
        for r in rows:
            r["vec"] = self.decode_vec(r["vec"], r["dim"])
        return rows

    def decode_vec(self, blob, dim: int) -> np.ndarray:
        """Giai ma (neu can) va doc 1 embedding. Fail-closed: blob da ma hoa ma khong co khoa -> loi ro rang,
        khong bao gio doc ciphertext thanh vector rac."""
        raw = bytes(blob)
        if raw.startswith(ENCRYPTED_PREFIX):
            if self.cipher is None:
                raise RuntimeError(
                    "Embedding da duoc ma hoa nhung tien trinh nay khong co FACEGATE_BIOMETRIC_KEY "
                    "(BiometricCipher). Kiem tra cau hinh khoa sinh trac."
                )
            raw = self.cipher.decrypt_bytes(raw)
        return np.frombuffer(raw, dtype=np.float32, count=int(dim)).copy()

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

    def set_clip(self, app_id: int, rel_path: str | None, clip_sha256: str | None = None,
                 sidecar_path: str | None = None) -> None:
        """rel_path: duong dan clip da ghi xong (tuong doi thu muc data), None neu khong co clip."""
        self.x("""UPDATE appearances SET clip=?, clip_ready=?,
                    clip_sha256=COALESCE(?, clip_sha256), sidecar_path=COALESCE(?, sidecar_path)
                  WHERE id=?""",
               (rel_path, 1 if rel_path else 0, clip_sha256, sidecar_path, app_id))

    def set_legal_hold(self, app_id: int, hold: bool, reason: str | None = None) -> None:
        self.x("UPDATE appearances SET legal_hold=?, legal_hold_reason=? WHERE id=?",
               (1 if hold else 0, reason if hold else None, app_id))

    def add_audit_log(self, actor: str, action: str, target_type: str | None = None,
                      target_id: str | None = None, detail: dict | None = None, ip_addr: str | None = None) -> int:
        return self.x(
            "INSERT INTO audit_logs(ts,actor,ip_addr,action,target_type,target_id,detail_json) VALUES(?,?,?,?,?,?,?)",
            (time.time(), actor, ip_addr, action, target_type, target_id,
             json.dumps(detail or {}, ensure_ascii=False)),
        )

    def append_ledger(self, **kw) -> int:
        existing = self.q("SELECT id FROM attendance_ledger WHERE idempotency_key=?", (kw["idempotency_key"],))
        if existing:
            return int(existing[0]["id"])
        kw.setdefault("created_at", time.time())
        cols = ",".join(kw)
        return self.x(f"INSERT INTO attendance_ledger({cols}) VALUES({','.join('?' * len(kw))})", tuple(kw.values()))

    def pending_ledger_hrm(self, max_attempts: int = 5) -> list[dict]:
        return self.q(
            """SELECT * FROM attendance_ledger
               WHERE hrm_sync_status IN ('pending','failed') AND hrm_attempts < ?
               ORDER BY id LIMIT 20""",
            (max_attempts,),
        )

    def set_ledger_hrm(self, ledger_id: int, status: str, remote_doc_id: str | None = None,
                       error: str | None = None) -> None:
        # Dong da bi huy (thu hoi truoc khi sync) khong duoc quay lai 'failed' de retry;
        # chi 'sent' moi ghi de, de HRMWorker biet phai gui lenh xoa bu.
        self.x(
            """UPDATE attendance_ledger
               SET hrm_sync_status=CASE WHEN hrm_sync_status='cancelled' AND ?<>'sent'
                                        THEN 'cancelled' ELSE ? END,
                   remote_doc_id=COALESCE(?, remote_doc_id),
                   hrm_last_error=?, hrm_attempts=hrm_attempts+1
               WHERE id=?""",
            (status, status, remote_doc_id, (error or "")[:500] if error else None, ledger_id),
        )

    def cancel_ledger_hrm(self, ledger_id: int) -> bool:
        """Huy dong ledger chua sync de HRMWorker khong day len nua. Tra ve True neu da huy."""
        n = self.x_rowcount(
            """UPDATE attendance_ledger SET hrm_sync_status='cancelled'
               WHERE id=? AND hrm_sync_status IN ('pending','failed')""",
            (ledger_id,),
        )
        return n > 0

    def requeue_revoke_after_late_sync(self, target_ledger_id: int, remote_doc_id: str) -> int:
        """Ban goc da bi thu hoi nhung van day len HRM thanh cong (race): mo lai lenh xoa."""
        return self.x_rowcount(
            """UPDATE attendance_ledger SET hrm_sync_status='pending', remote_doc_id=?, hrm_attempts=0
               WHERE action='checkin_revoked' AND target_ledger_id=? AND hrm_sync_status='skipped'""",
            (remote_doc_id, target_ledger_id),
        )

    def mirror_event_hrm(self, ev_id: int, status: str, response: str) -> None:
        """Phan chieu trang thai sync cua ledger ve events de UI lich su van hien dung."""
        self.x("UPDATE events SET hrm_status=?, hrm_response=? WHERE id=?", (status, (response or "")[:500], ev_id))

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
