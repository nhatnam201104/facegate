"""SQLite storage (demo). Khi len server se chuyen sang PostgreSQL + pgvector."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path

import numpy as np

# Phai trung voi server.api.security.MAGIC_PREFIX (khong import de tranh keo fastapi vao tang DB)
ENCRYPTED_PREFIX = b"FG1$"
SKU_CODE_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
SKU_EVENT_COLS = (
    "ts", "camera", "sku_id", "code", "name", "barcode", "method",
    "score", "inliers", "direction", "qty", "uom", "track_id",
    "snapshot", "clip", "meta_json", "stock_batch_id",
)


def validate_sku_code(code: str) -> str:
    c = (code or "").strip()
    if not SKU_CODE_RE.match(c) or c in {".", ".."}:
        raise ValueError("Mã SKU chỉ gồm chữ, số, - _ . (tối đa 64 ký tự)")
    return c


def validate_hex_color(color: str | None, default: str = "#3cc85a") -> str:
    c = (color or "").strip()
    if not c:
        return default
    if not HEX_COLOR_RE.match(c):
        raise ValueError("Màu vùng phải có định dạng hex #RRGGBB (vd #3cc85a)")
    return c.lower()

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

CREATE TABLE IF NOT EXISTS sku_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    barcode TEXT UNIQUE,
    uom TEXT NOT NULL DEFAULT 'pcs',
    status TEXT NOT NULL DEFAULT 'active',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sku_items_status ON sku_items(status);

CREATE TABLE IF NOT EXISTS sku_barcodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    sku_id INTEGER NOT NULL REFERENCES sku_items(id) ON DELETE CASCADE,
    barcode TEXT UNIQUE NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sku_bc_sku ON sku_barcodes(sku_id);

CREATE TABLE IF NOT EXISTS sku_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    sku_id INTEGER NOT NULL REFERENCES sku_items(id) ON DELETE CASCADE,
    feature_type TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    num_kps INTEGER NOT NULL,
    kps_blob BLOB NOT NULL,
    desc_blob BLOB NOT NULL,
    desc_dtype TEXT NOT NULL,
    desc_cols INTEGER NOT NULL,
    image_path TEXT,
    label TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sku_tpl_sku ON sku_templates(sku_id);

CREATE TABLE IF NOT EXISTS sku_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    ts REAL NOT NULL,
    camera TEXT,
    sku_id INTEGER,
    code TEXT,
    name TEXT,
    barcode TEXT,
    method TEXT NOT NULL,
    score REAL,
    inliers INTEGER,
    direction TEXT,
    qty REAL NOT NULL DEFAULT 1.0,
    uom TEXT,
    track_id INTEGER,
    snapshot TEXT,
    clip TEXT,
    meta_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_sku_ev_ts ON sku_events(ts);
CREATE INDEX IF NOT EXISTS ix_sku_ev_code ON sku_events(code);

CREATE TABLE IF NOT EXISTS sku_stock_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    idempotency_key TEXT UNIQUE NOT NULL,
    created_at REAL NOT NULL,
    actor TEXT NOT NULL,
    stock_entry_type TEXT NOT NULL DEFAULT 'Material Receipt',
    direction TEXT NOT NULL DEFAULT 'IN',
    warehouse TEXT,
    date_from TEXT,
    date_to TEXT,
    items_json TEXT NOT NULL,
    total_qty REAL NOT NULL DEFAULT 0.0,
    sync_status TEXT NOT NULL DEFAULT 'pending',
    remote_doc_id TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS ix_sku_batch_status ON sku_stock_batches(sync_status);

CREATE TABLE IF NOT EXISTS zones (
    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    camera_id TEXT NOT NULL DEFAULT 'gate-1',
    polygon_json TEXT NOT NULL,
    enter_confirm_sec REAL NOT NULL DEFAULT 1.0,
    min_dwell_sec REAL NOT NULL DEFAULT 3.0,
    exit_grace_sec REAL NOT NULL DEFAULT 2.0,
    alert_empty_sec REAL NOT NULL DEFAULT 0.0,
    min_persons INTEGER NOT NULL DEFAULT 1,
    color TEXT NOT NULL DEFAULT '#3cc85a',
    status TEXT NOT NULL DEFAULT 'active',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_zones_status ON zones(status);

CREATE TABLE IF NOT EXISTS zone_presence_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    zone_id INTEGER NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
    zone_code TEXT NOT NULL,
    zone_name TEXT NOT NULL,
    camera TEXT NOT NULL DEFAULT 'gate-1',
    person_track_id INTEGER NOT NULL,
    enter_ts REAL NOT NULL,
    exit_ts REAL,
    dwell_sec REAL,
    status TEXT NOT NULL DEFAULT 'open',
    snapshot TEXT
);
CREATE INDEX IF NOT EXISTS ix_zone_sess_enter ON zone_presence_sessions(enter_ts);
CREATE INDEX IF NOT EXISTS ix_zone_sess_zone ON zone_presence_sessions(zone_code);
"""

SKU_ITEM_COLUMNS = {
    "erp_item_code": "TEXT",
    "erp_warehouse": "TEXT",
}

SKU_EVENT_MIG_COLUMNS = {
    "stock_batch_id": "INTEGER",
}

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

SKU_BATCH_COLUMNS = {
    "next_retry_at": "REAL",
}

MIGRATED_COLUMNS = {
    "sku_stock_batches": SKU_BATCH_COLUMNS,
    "embeddings": EMBEDDING_COLUMNS,
    "events": EVENT_COLUMNS,
    "appearances": APPEARANCE_COLUMNS,
    "sku_items": SKU_ITEM_COLUMNS,
    "sku_events": SKU_EVENT_MIG_COLUMNS,
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
        self.x(
            """INSERT INTO sku_barcodes(sku_id, barcode, created_at)
               SELECT id, barcode, created_at FROM sku_items
               WHERE barcode IS NOT NULL AND barcode != ''
               ON CONFLICT(barcode) DO NOTHING"""
        )

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

    # --- sku (Phase 2: Hang hoa / Bao bi) ---
    @staticmethod
    def parse_barcodes(barcode: str | None = None, barcodes: list[str] | tuple[str, ...] | None = None) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        raw_parts: list[str] = []
        if barcodes is not None:
            for item in barcodes:
                if item is not None:
                    raw_parts.append(str(item))
        if barcode is not None:
            raw_parts.append(str(barcode))
        for chunk in raw_parts:
            for tok in re.split(r"[,;\r\n|]+", chunk):
                b = tok.strip()
                if b and b not in seen:
                    seen.add(b)
                    out.append(b)
        return out

    _SQL_IN_CHUNK = 500

    def _attach_sku_barcodes(self, items: list[dict]) -> list[dict]:
        if not items:
            return items
        ids = [int(it["id"]) for it in items]
        by_sku: dict[int, list[str]] = {}
        for i in range(0, len(ids), self._SQL_IN_CHUNK):
            chunk = ids[i:i + self._SQL_IN_CHUNK]
            ph = ",".join("?" * len(chunk))
            rows = self.q(
                f"SELECT sku_id, barcode FROM sku_barcodes WHERE sku_id IN ({ph}) ORDER BY id ASC",
                tuple(chunk),
            )
            for r in rows:
                by_sku.setdefault(int(r["sku_id"]), []).append(str(r["barcode"]))
        for it in items:
            bcs = list(by_sku.get(int(it["id"]), []))
            legacy = (it.get("barcode") or "").strip()
            if legacy and legacy not in bcs:
                bcs.insert(0, legacy)
            it["barcodes"] = bcs
            it["barcode"] = bcs[0] if bcs else None
        return items

    def get_sku_by_code(self, code: str) -> dict | None:
        r = self.q("SELECT * FROM sku_items WHERE code=?", (code.strip(),))
        return self._attach_sku_barcodes(r)[0] if r else None

    def get_sku_by_barcode(self, barcode: str) -> dict | None:
        b = (barcode or "").strip()
        if not b:
            return None
        r = self.q(
            """SELECT s.* FROM sku_barcodes b
               JOIN sku_items s ON s.id=b.sku_id
               WHERE b.barcode=?""",
            (b,),
        )
        if not r:
            r = self.q("SELECT * FROM sku_items WHERE barcode=?", (b,))
        return self._attach_sku_barcodes(r)[0] if r else None

    def upsert_sku_item(self, code: str, name: str, barcode: str | None = None,
                        uom: str = "pcs", status: str = "active",
                        erp_item_code: str | None = None,
                        erp_warehouse: str | None = None,
                        barcodes: list[str] | tuple[str, ...] | None = None,
                        replace_barcodes: bool = True) -> int:
        code_c = validate_sku_code(code)
        name_c = (name or "").strip()
        if not name_c:
            raise ValueError("Tên SKU không được rỗng")
        bc_list = self.parse_barcodes(barcode=barcode, barcodes=barcodes)
        uom_c = (uom or "pcs").strip() or "pcs"
        erp_code_c = (erp_item_code or "").strip() or None
        erp_wh_c = (erp_warehouse or "").strip() or None
        now = time.time()
        with self.lock:
            existing = self.get_sku_by_code(code_c)
            if existing and not replace_barcodes:
                existing_bcs = list(existing.get("barcodes") or [])
                bc_list = self.parse_barcodes(barcodes=existing_bcs + bc_list)
            b_c = bc_list[0] if bc_list else None
            for bc_val in bc_list:
                dup_bc = self.get_sku_by_barcode(bc_val)
                if dup_bc is not None and (not existing or int(dup_bc["id"]) != int(existing["id"])):
                    raise ValueError(f"Mã vạch '{bc_val}' đã thuộc về SKU '{dup_bc['code']}'")
            if existing:
                sku_id = int(existing["id"])
                final_erp_code = erp_code_c if erp_code_c is not None else existing.get("erp_item_code")
                final_erp_wh = erp_wh_c if erp_wh_c is not None else existing.get("erp_warehouse")
                self.x(
                    """UPDATE sku_items
                       SET name=?, barcode=?, uom=?, status=?, erp_item_code=?, erp_warehouse=?, updated_at=?
                       WHERE id=?""",
                    (name_c, b_c, uom_c, status, final_erp_code, final_erp_wh, now, sku_id),
                )
            else:
                sku_id = self.x(
                    """INSERT INTO sku_items(code,name,barcode,uom,status,erp_item_code,erp_warehouse,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (code_c, name_c, b_c, uom_c, status, erp_code_c, erp_wh_c, now, now),
                )
            self.x("DELETE FROM sku_barcodes WHERE sku_id=?", (sku_id,))
            for bc_val in bc_list:
                self.x(
                    "INSERT INTO sku_barcodes(sku_id, barcode, created_at) VALUES(?,?,?)",
                    (sku_id, bc_val, now),
                )
            return sku_id

    def _sku_filter_clause(self, q: str | None = None, status: str | None = None) -> tuple[str, list]:
        clauses: list[str] = []
        args: list = []
        if status and status in ("active", "disabled"):
            clauses.append("s.status=?")
            args.append(status)
        q_clean = (q or "").strip().lower()
        if q_clean:
            like = f"%{q_clean}%"
            clauses.append(
                "(LOWER(s.code) LIKE ? OR LOWER(s.name) LIKE ? "
                "OR LOWER(COALESCE(s.barcode, '')) LIKE ? "
                "OR EXISTS (SELECT 1 FROM sku_barcodes b WHERE b.sku_id=s.id AND LOWER(b.barcode) LIKE ?))"
            )
            args.extend([like, like, like, like])
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, args

    def count_sku_items(self, q: str | None = None, status: str | None = None) -> int:
        where, args = self._sku_filter_clause(q=q, status=status)
        rows = self.q(f"SELECT COUNT(*) AS n FROM sku_items s{where}", tuple(args))
        return int(rows[0]["n"]) if rows else 0

    def list_sku_items(
        self,
        q: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict]:
        where, args = self._sku_filter_clause(q=q, status=status)
        sql = f"""
            SELECT s.*,
              (SELECT COUNT(*) FROM sku_templates t WHERE t.sku_id=s.id) AS n_templates,
              (SELECT COALESCE(SUM(num_kps), 0) FROM sku_templates t WHERE t.sku_id=s.id) AS total_kps,
              (SELECT image_path FROM sku_templates t WHERE t.sku_id=s.id ORDER BY num_kps DESC, id DESC LIMIT 1) AS thumb
            FROM sku_items s{where}
            ORDER BY s.updated_at DESC, s.id DESC
        """
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            args.extend([max(1, int(limit)), max(0, int(offset))])
        return self._attach_sku_barcodes(self.q(sql, tuple(args)))

    def set_sku_status(self, sku_id: int, status: str) -> None:
        self.x("UPDATE sku_items SET status=?, updated_at=? WHERE id=?", (status, time.time(), sku_id))

    def delete_sku_item(self, sku_id: int) -> list[str]:
        imgs = [r["image_path"] for r in self.q("SELECT image_path FROM sku_templates WHERE sku_id=?", (sku_id,))]
        self.x("DELETE FROM sku_items WHERE id=?", (sku_id,))
        return [p for p in imgs if p]

    def add_sku_template(
        self,
        sku_id: int,
        feature_type: str,
        width: int,
        height: int,
        kps_xy: np.ndarray,
        descriptors: np.ndarray,
        image_path: str | None = None,
        label: str | None = None,
    ) -> int:
        pts = np.ascontiguousarray(kps_xy, dtype=np.float32)
        if pts.ndim != 2 or pts.shape[1] != 2:
            raise ValueError("kps_xy phai co kich thuoc (N, 2)")
        if descriptors.dtype == np.uint8:
            desc = np.ascontiguousarray(descriptors, dtype=np.uint8)
            dtype_name = "uint8"
        else:
            desc = np.ascontiguousarray(descriptors, dtype=np.float32)
            dtype_name = "float32"
        if desc.ndim != 2 or desc.shape[0] != pts.shape[0]:
            raise ValueError("descriptors phai co cung so hang N voi kps_xy")
        return self.x(
            """INSERT INTO sku_templates(
                   sku_id,feature_type,width,height,num_kps,kps_blob,desc_blob,
                   desc_dtype,desc_cols,image_path,label,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                int(sku_id), str(feature_type).lower(), int(width), int(height),
                int(pts.shape[0]), pts.tobytes(), desc.tobytes(),
                dtype_name, int(desc.shape[1]), image_path, label, time.time(),
            ),
        )

    def clear_sku_templates(self, sku_id: int, feature_type: str | None = None) -> None:
        if feature_type:
            self.x("DELETE FROM sku_templates WHERE sku_id=? AND feature_type=?", (sku_id, feature_type.lower()))
        else:
            self.x("DELETE FROM sku_templates WHERE sku_id=?", (sku_id,))

    def load_sku_gallery(
        self,
        feature_type: str | None = None,
        statuses: tuple = ("active",),
    ) -> tuple[list[dict], list[dict]]:
        ph = ",".join("?" * len(statuses))
        items = self._attach_sku_barcodes(
            self.q(f"SELECT * FROM sku_items WHERE status IN ({ph}) ORDER BY id", tuple(statuses))
        )
        sql = f"""
            SELECT t.*, s.code, s.name, s.barcode, s.uom
            FROM sku_templates t
            JOIN sku_items s ON s.id=t.sku_id
            WHERE s.status IN ({ph})
        """
        args: list = list(statuses)
        if feature_type:
            sql += " AND t.feature_type=?"
            args.append(feature_type.lower())
        rows = self.q(sql + " ORDER BY t.id", tuple(args))
        templates = []
        for r in rows:
            n = int(r["num_kps"])
            cols = int(r["desc_cols"])
            dt = np.uint8 if r["desc_dtype"] == "uint8" else np.float32
            kps = np.frombuffer(bytes(r["kps_blob"]), dtype=np.float32, count=n * 2).reshape(n, 2).copy()
            desc = np.frombuffer(bytes(r["desc_blob"]), dtype=dt, count=n * cols).reshape(n, cols).copy()
            templates.append({
                "template_id": int(r["id"]),
                "sku_id": int(r["sku_id"]),
                "code": r["code"],
                "name": r["name"],
                "barcode": r["barcode"],
                "uom": r["uom"],
                "feature_type": r["feature_type"],
                "width": int(r["width"]),
                "height": int(r["height"]),
                "num_kps": n,
                "kps_xy": kps,
                "descriptors": desc,
                "image_path": r["image_path"],
                "label": r["label"],
            })
        return items, templates

    def add_sku_event(self, **kw) -> int:
        data = dict(kw)
        if "meta" in data and "meta_json" not in data:
            meta_val = data.pop("meta")
            data["meta_json"] = json.dumps(meta_val, ensure_ascii=False) if meta_val is not None else None
        data.setdefault("ts", time.time())
        data.setdefault("qty", 1.0)
        unknown = set(data) - set(SKU_EVENT_COLS)
        if unknown:
            raise ValueError(f"Cột sku_events không hợp lệ: {', '.join(sorted(unknown))}")
        ordered_cols = [c for c in SKU_EVENT_COLS if c in data]
        return self.x(
            f"INSERT INTO sku_events({','.join(ordered_cols)}) VALUES({','.join('?' * len(ordered_cols))})",
            tuple(data[c] for c in ordered_cols),
        )

    def list_sku_events(
        self,
        t0: float | None = None,
        t1: float | None = None,
        code: str | None = None,
        method: str | None = None,
        limit: int | None = 200,
    ) -> list[dict]:
        conds, args = [], []
        if t0 is not None:
            conds.append("ts>=?")
            args.append(float(t0))
        if t1 is not None:
            conds.append("ts<?")
            args.append(float(t1))
        if code:
            conds.append("code=?")
            args.append(code)
        if method:
            conds.append("method=?")
            args.append(method)
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        if limit is None:
            return self.q(f"SELECT * FROM sku_events{where} ORDER BY ts DESC", tuple(args))
        return self.q(f"SELECT * FROM sku_events{where} ORDER BY ts DESC LIMIT ?", (*args, int(limit)))

    def summarize_sku_events(
        self,
        t0: float | None = None,
        t1: float | None = None,
        code: str | None = None,
    ) -> dict:
        """Gop san luong SKU truc tiep bang SQL (khong bi cat o 50k dong, tuong thich SQLite & Postgres)."""
        conds, args = [], []
        if t0 is not None:
            conds.append("ts>=?")
            args.append(float(t0))
        if t1 is not None:
            conds.append("ts<?")
            args.append(float(t1))
        if code:
            conds.append("code=?")
            args.append(code)
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        totals = self.q(
            f"""SELECT
                    COUNT(*) AS total_events,
                    COALESCE(SUM(CASE WHEN sku_id IS NULL OR code IS NULL OR code = '' THEN 1 ELSE 0 END), 0) AS unknown_count
                FROM sku_events{where}""",
            tuple(args),
        )[0]

        known_conds = ["sku_id IS NOT NULL", "code IS NOT NULL", "code != ''", *conds]
        known_where = " WHERE " + " AND ".join(known_conds)
        grouped = self.q(
            f"""SELECT
                    code,
                    COALESCE(method, 'unknown') AS method,
                    COALESCE(direction, 'IN') AS dir,
                    COUNT(*) AS cnt,
                    COALESCE(SUM(COALESCE(qty, 1.0)), 0.0) AS sum_qty,
                    MAX(ts) AS max_ts,
                    MAX(name) AS any_name,
                    MAX(uom) AS any_uom
                FROM sku_events{known_where}
                GROUP BY code, COALESCE(method, 'unknown'), COALESCE(direction, 'IN')""",
            tuple(args),
        )

        by_code: dict[str, dict] = {}
        meta_ts: dict[str, float] = {}
        total_qty = 0.0
        total_in = 0.0
        total_out = 0.0
        for r in grouped:
            c = str(r["code"])
            cnt = int(r["cnt"] or 0)
            sq = float(r["sum_qty"] or 0.0)
            mts = float(r["max_ts"] or 0.0)
            m = str(r["method"] or "unknown")
            d = str(r["dir"] or "IN").upper()
            item = by_code.get(c)
            if item is None:
                item = {
                    "code": c,
                    "name": r.get("any_name") or c,
                    "uom": r.get("any_uom") or "pcs",
                    "count": 0,
                    "qty": 0.0,
                    "in_qty": 0.0,
                    "out_qty": 0.0,
                    "last_ts": mts,
                    "methods": {},
                }
                by_code[c] = item
                meta_ts[c] = mts
            elif mts >= meta_ts[c]:
                if r.get("any_name"):
                    item["name"] = r["any_name"]
                if r.get("any_uom"):
                    item["uom"] = r["any_uom"]
                meta_ts[c] = mts
            item["count"] += cnt
            item["qty"] += sq
            if d == "OUT":
                item["out_qty"] += sq
                total_out += sq
            else:
                item["in_qty"] += sq
                total_in += sq
            item["last_ts"] = max(item["last_ts"], mts)
            item["methods"][m] = item["methods"].get(m, 0) + cnt
            total_qty += sq

        for item in by_code.values():
            item["qty"] = round(item["qty"], 3)
            item["in_qty"] = round(item["in_qty"], 3)
            item["out_qty"] = round(item["out_qty"], 3)
        summary = sorted(by_code.values(), key=lambda x: (-x["qty"], x["code"]))
        return {
            "total_events": int(totals["total_events"] or 0),
            "total_qty": round(total_qty, 3),
            "in_qty": round(total_in, 3),
            "out_qty": round(total_out, 3),
            "unknown_count": int(totals["unknown_count"] or 0),
            "sku_types": len(summary),
            "summary": summary,
        }

    # --- SKU ERP outbox (sku_stock_batches) ---
    def create_sku_stock_batch(
        self,
        *,
        idempotency_key: str,
        actor: str,
        direction: str,
        stock_entry_type: str,
        warehouse: str,
        date_from: str,
        date_to: str,
        items: list[dict],
        event_ids: list[int],
    ) -> dict:
        with self.lock:
            existing = self.q("SELECT * FROM sku_stock_batches WHERE idempotency_key=?", (idempotency_key,))
            if existing:
                row = existing[0]
                row["items"] = json.loads(row["items_json"]) if row.get("items_json") else []
                return row
            total_qty = round(sum(float(it.get("qty") or 0.0) for it in items), 3)
            batch_id = self.x(
                """INSERT INTO sku_stock_batches(
                       idempotency_key,created_at,actor,stock_entry_type,direction,
                       warehouse,date_from,date_to,items_json,total_qty,sync_status
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    idempotency_key, time.time(), actor, stock_entry_type, direction,
                    warehouse, date_from, date_to, json.dumps(items, ensure_ascii=False),
                    total_qty, "pending",
                ),
            )
            if event_ids:
                ph = ",".join("?" * len(event_ids))
                self.x(
                    f"UPDATE sku_events SET stock_batch_id=? WHERE id IN ({ph}) AND stock_batch_id IS NULL",
                    (batch_id, *event_ids),
                )
            row = self.q("SELECT * FROM sku_stock_batches WHERE id=?", (batch_id,))[0]
            row["items"] = items
            return row

    SKU_BATCH_BACKOFF_BASE_SEC = 10.0
    SKU_BATCH_BACKOFF_MAX_SEC = 1800.0

    def pending_sku_stock_batches(self, now: float | None = None) -> list[dict]:
        """Lo can day: pending hoac failed da den han thu lai (backoff luy thua, khong khoa vinh vien)."""
        ts = float(now if now is not None else time.time())
        rows = self.q(
            """SELECT * FROM sku_stock_batches
               WHERE sync_status IN ('pending', 'failed') AND (next_retry_at IS NULL OR next_retry_at <= ?)
               ORDER BY id ASC LIMIT 50""",
            (ts,),
        )
        for r in rows:
            r["items"] = json.loads(r["items_json"]) if r.get("items_json") else []
        return rows

    def set_sku_stock_batch_status(
        self,
        batch_id: int,
        status: str,
        remote_doc_id: str | None = None,
        error: str | None = None,
    ) -> None:
        with self.lock:
            cur = self.q("SELECT attempts FROM sku_stock_batches WHERE id=?", (int(batch_id),))
            attempts = int(cur[0]["attempts"] or 0) + 1 if cur else 1
            next_retry = None
            if status != "sent":
                delay = min(self.SKU_BATCH_BACKOFF_MAX_SEC, self.SKU_BATCH_BACKOFF_BASE_SEC * (2 ** min(attempts, 10)))
                next_retry = time.time() + delay
            self.x(
                """UPDATE sku_stock_batches
                   SET sync_status=?, remote_doc_id=COALESCE(?, remote_doc_id),
                       attempts=?, last_error=?, next_retry_at=?
                   WHERE id=?""",
                (status, remote_doc_id, attempts, error, next_retry, int(batch_id)),
            )

    def retry_sku_stock_batch(self, batch_id: int) -> bool:
        """Dat lai han thu lai de lo failed duoc day ngay; khong dong vao lo da sent."""
        with self.lock:
            rows = self.q("SELECT sync_status FROM sku_stock_batches WHERE id=?", (int(batch_id),))
            if not rows or rows[0]["sync_status"] == "sent":
                return False
            self.x("UPDATE sku_stock_batches SET next_retry_at=NULL WHERE id=?", (int(batch_id),))
            return True

    def list_sku_stock_batches(self, limit: int = 50) -> list[dict]:
        rows = self.q("SELECT * FROM sku_stock_batches ORDER BY id DESC LIMIT ?", (int(limit),))
        for r in rows:
            r["items"] = json.loads(r["items_json"]) if r.get("items_json") else []
        return rows

    # --- Polygon Zones & Presence Sessions (GAP-04) ---
    def upsert_zone(
        self,
        code: str,
        name: str,
        polygon: list[list[float]],
        *,
        camera_id: str = "gate-1",
        enter_confirm_sec: float = 1.0,
        min_dwell_sec: float = 3.0,
        exit_grace_sec: float = 2.0,
        alert_empty_sec: float = 0.0,
        min_persons: int = 1,
        color: str = "#3cc85a",
        status: str = "active",
    ) -> int:
        code_c = validate_sku_code(code)
        name_c = (name or "").strip()
        if not name_c:
            raise ValueError("Tên khu vực không được rỗng")
        if status not in ("active", "disabled"):
            raise ValueError("status không hợp lệ")
        color_c = validate_hex_color(color)
        poly_json = json.dumps([[round(float(p[0]), 4), round(float(p[1]), 4)] for p in polygon])
        now = time.time()
        existing = self.q("SELECT id FROM zones WHERE code=?", (code_c,))
        if existing:
            zid = int(existing[0]["id"])
            self.x(
                """UPDATE zones
                   SET name=?, camera_id=?, polygon_json=?, enter_confirm_sec=?, min_dwell_sec=?,
                       exit_grace_sec=?, alert_empty_sec=?, min_persons=?, color=?, status=?, updated_at=?
                   WHERE id=?""",
                (
                    name_c, camera_id, poly_json, float(enter_confirm_sec), float(min_dwell_sec),
                    float(exit_grace_sec), float(alert_empty_sec), max(1, int(min_persons)),
                    color_c, status, now, zid,
                ),
            )
            return zid
        return self.x(
            """INSERT INTO zones(
                   code,name,camera_id,polygon_json,enter_confirm_sec,min_dwell_sec,
                   exit_grace_sec,alert_empty_sec,min_persons,color,status,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                code_c, name_c, camera_id, poly_json, float(enter_confirm_sec), float(min_dwell_sec),
                float(exit_grace_sec), float(alert_empty_sec), max(1, int(min_persons)),
                color_c, status, now, now,
            ),
        )

    def list_zones(self, active_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM zones" + (" WHERE status='active'" if active_only else "") + " ORDER BY id ASC"
        rows = self.q(sql)
        for r in rows:
            r["polygon"] = json.loads(r["polygon_json"]) if r.get("polygon_json") else []
        return rows

    def set_zone_status(self, zone_id: int, status: str) -> None:
        self.x("UPDATE zones SET status=?, updated_at=? WHERE id=?", (status, time.time(), int(zone_id)))

    def delete_zone(self, zone_id: int) -> None:
        self.x("DELETE FROM zones WHERE id=?", (int(zone_id),))

    def open_zone_session(
        self,
        *,
        zone_id: int,
        zone_code: str,
        zone_name: str,
        camera: str,
        person_track_id: int,
        enter_ts: float,
        snapshot: str | None = None,
    ) -> int:
        return self.x(
            """INSERT INTO zone_presence_sessions(
                   zone_id,zone_code,zone_name,camera,person_track_id,enter_ts,status,snapshot
               ) VALUES(?,?,?,?,?,?,?,?)""",
            (int(zone_id), zone_code, zone_name, camera, int(person_track_id), float(enter_ts), "open", snapshot),
        )

    def close_zone_session(self, session_id: int, exit_ts: float, dwell_sec: float) -> None:
        self.x(
            "UPDATE zone_presence_sessions SET exit_ts=?, dwell_sec=?, status='closed' WHERE id=?",
            (float(exit_ts), round(float(dwell_sec), 2), int(session_id)),
        )

    def close_orphan_zone_sessions(self, now: float | None = None) -> int:
        ts_end = float(now if now is not None else time.time())
        open_rows = self.q("SELECT id, enter_ts FROM zone_presence_sessions WHERE status='open'")
        for r in open_rows:
            dw = max(0.0, ts_end - float(r["enter_ts"]))
            self.close_zone_session(int(r["id"]), ts_end, dw)
        return len(open_rows)

    def list_zone_sessions(
        self,
        t0: float | None = None,
        t1: float | None = None,
        zone_code: str | None = None,
        limit: int | None = 200,
    ) -> list[dict]:
        conds, args = [], []
        if t0 is not None:
            conds.append("enter_ts>=?")
            args.append(float(t0))
        if t1 is not None:
            conds.append("enter_ts<?")
            args.append(float(t1))
        if zone_code:
            conds.append("zone_code=?")
            args.append(zone_code)
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        if limit is None:
            return self.q(f"SELECT * FROM zone_presence_sessions{where} ORDER BY enter_ts DESC", tuple(args))
        return self.q(
            f"SELECT * FROM zone_presence_sessions{where} ORDER BY enter_ts DESC LIMIT ?",
            (*args, int(limit)),
        )

    def summarize_zone_sessions(
        self,
        t0: float | None = None,
        t1: float | None = None,
        zone_code: str | None = None,
    ) -> dict:
        conds = ["status='closed'"]
        args: list = []
        if t0 is not None:
            conds.append("enter_ts>=?")
            args.append(float(t0))
        if t1 is not None:
            conds.append("enter_ts<?")
            args.append(float(t1))
        if zone_code:
            conds.append("zone_code=?")
            args.append(zone_code)
        where = " WHERE " + " AND ".join(conds)
        rows = self.q(
            f"""SELECT
                    zone_code,
                    MAX(zone_name) AS zone_name,
                    COUNT(*) AS sessions,
                    COALESCE(SUM(COALESCE(dwell_sec, 0.0)), 0.0) AS total_dwell_sec,
                    COALESCE(AVG(COALESCE(dwell_sec, 0.0)), 0.0) AS avg_dwell_sec,
                    MAX(COALESCE(exit_ts, enter_ts)) AS last_ts
                FROM zone_presence_sessions{where}
                GROUP BY zone_code
                ORDER BY total_dwell_sec DESC, zone_code ASC""",
            tuple(args),
        )
        summary = [
            {
                "zone_code": r["zone_code"],
                "zone_name": r["zone_name"] or r["zone_code"],
                "sessions": int(r["sessions"] or 0),
                "total_dwell_sec": round(float(r["total_dwell_sec"] or 0.0), 1),
                "avg_dwell_sec": round(float(r["avg_dwell_sec"] or 0.0), 1),
                "last_ts": float(r["last_ts"] or 0.0),
            }
            for r in rows
        ]
        return {
            "total_sessions": sum(s["sessions"] for s in summary),
            "total_dwell_sec": round(sum(s["total_dwell_sec"] for s in summary), 1),
            "zones_with_activity": len(summary),
            "summary": summary,
        }
