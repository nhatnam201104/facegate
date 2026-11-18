"""Migration gallery sang model_version moi (feature 002, T019)."""
import hashlib

import cv2
import numpy as np

from server.scripts.migrate_gallery import migrate
from server.store.db import DB
from server.vision.engines import Face

OLD, NEW = "old_v1", "new_v1"


class StubEngine:
    model_version = NEW

    def detect(self, img):
        return [Face(np.array([5, 5, 60, 70], np.float32), 0.9,
                     np.array([[20, 25], [45, 25], [32, 40], [22, 55], [43, 55]], np.float32))]

    def align(self, img, face, pose=None, pose_group=None):
        return cv2.resize(img, (112, 112))

    def embed_aligned(self, aligned):
        h = hashlib.sha256(aligned.tobytes()).digest()
        v = np.frombuffer(h * 4, dtype=np.uint8).astype(np.float32)[:128] - 127.5
        return v / np.linalg.norm(v)


def _db(tmp_path):
    db = DB(tmp_path / "t.db")
    data = tmp_path / "data"
    (data / "enroll").mkdir(parents=True)
    return db, data


def _employee(db, data, code, crops: dict[str, str], status="active"):
    eid = db.create_employee(code, code)
    db.x("UPDATE employees SET status=? WHERE id=?", (status, eid))
    folder = data / "enroll" / code
    folder.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(abs(hash(code)) % 2**32)
    for name, grp in crops.items():
        rel = f"enroll/{code}/{name}"
        if not name.startswith("missing"):
            cv2.imwrite(str(data / rel), rng.integers(0, 255, (80, 70, 3), dtype=np.uint8))
        db.add_embedding(eid, np.ones(64, np.float32), OLD, 0.8, name.split(".")[0], rel,
                         source="enroll", pose_group=grp, yaw=-55.0 if grp == "left_profile" else 0.0, pitch=0.0)
    return eid


def _rows(db, eid, ver=NEW):
    return db.q("SELECT pose_group, source, crop_path, yaw FROM embeddings WHERE employee_id=? AND model_version=?",
                (eid, ver))


def test_migrates_active_employees_and_copies_metadata(tmp_path):
    db, data = _db(tmp_path)
    a = _employee(db, data, "NV1", {"00.jpg": "frontal", "05.jpg": "left_profile"})
    b = _employee(db, data, "NV2", {"00.jpg": "frontal"})
    res = migrate(db, StubEngine(), data, OLD)
    assert sorted(res.migrated) == ["NV1", "NV2"] and not res.needs_reenroll
    ra = _rows(db, a)
    assert {r["pose_group"] for r in ra} == {"frontal", "left_profile"}
    assert all(r["source"] == "migrate" for r in ra)
    assert {r["crop_path"] for r in ra} == {"enroll/NV1/00.jpg", "enroll/NV1/05.jpg"}
    assert len(db.load_gallery(NEW)) >= 3
    assert len(_rows(db, b)) == 1


def test_missing_crop_marks_needs_reenroll(tmp_path):
    db, data = _db(tmp_path)
    eid = _employee(db, data, "NV3", {"missing_00.jpg": "frontal"})
    res = migrate(db, StubEngine(), data, OLD)
    assert res.needs_reenroll == ["NV3"] and _rows(db, eid) == []


def test_dry_run_does_not_write(tmp_path):
    db, data = _db(tmp_path)
    eid = _employee(db, data, "NV4", {"00.jpg": "frontal"})
    n_audit = len(db.q("SELECT id FROM audit_logs"))
    res = migrate(db, StubEngine(), data, OLD, dry_run=True)
    assert res.migrated == ["NV4"] and res.vectors_added == 1
    assert _rows(db, eid) == [] and len(db.q("SELECT id FROM audit_logs")) == n_audit


def test_idempotent_and_fills_partial_gallery(tmp_path):
    db, data = _db(tmp_path)
    eid = _employee(db, data, "NV5", {"00.jpg": "frontal", "05.jpg": "left_profile"})
    # Da co san mau chinh dien o version moi (giong NV002 thuc te) -> van phai bo sung mau nghieng
    db.add_embedding(eid, np.ones(128, np.float32) / np.sqrt(128), NEW, 0.9, "00", "enroll/NV5/00.jpg",
                     source="enroll", pose_group="frontal", yaw=0.0, pitch=0.0)
    res1 = migrate(db, StubEngine(), data, OLD)
    assert res1.migrated == ["NV5"] and res1.vectors_added == 1
    assert sorted(r["pose_group"] for r in _rows(db, eid)) == ["frontal", "left_profile"]
    res2 = migrate(db, StubEngine(), data, OLD)
    assert res2.up_to_date == ["NV5"] and res2.vectors_added == 0
    assert len(_rows(db, eid)) == 2


def test_audit_log_written(tmp_path):
    db, data = _db(tmp_path)
    _employee(db, data, "NV6", {"00.jpg": "frontal"})
    migrate(db, StubEngine(), data, OLD)
    logs = db.q("SELECT action, detail_json FROM audit_logs WHERE action='gallery_migrate'")
    assert len(logs) == 1 and '"status": "migrated"' in logs[0]["detail_json"]


def test_mirror_profiles_adds_opposite_group_once(tmp_path):
    db, data = _db(tmp_path)
    eid = _employee(db, data, "NV7", {"00.jpg": "frontal", "05.jpg": "left_profile"})
    migrate(db, StubEngine(), data, OLD, mirror_profiles=True)
    rows = _rows(db, eid)
    mirror = [r for r in rows if r["source"] == "migrate_mirror"]
    assert len(mirror) == 1 and mirror[0]["pose_group"] == "right_profile" and mirror[0]["yaw"] == 55.0
    migrate(db, StubEngine(), data, OLD, mirror_profiles=True)
    assert len(_rows(db, eid)) == len(rows)


def test_inactive_employees_skipped(tmp_path):
    db, data = _db(tmp_path)
    eid = _employee(db, data, "NV8", {"00.jpg": "frontal"}, status="disabled")
    res = migrate(db, StubEngine(), data, OLD)
    assert res.migrated == [] and _rows(db, eid) == []
