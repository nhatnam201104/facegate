"""Gallery trong RAM + so khop open-set (nguong + margin top-2)."""
from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np


@dataclass
class Match:
    employee_id: int | None
    code: str | None
    name: str | None
    score: float          # cosine top-1
    second: float         # cosine top-2 (nguoi khac)
    accepted: bool

    @property
    def margin(self) -> float:
        return self.score - self.second


class Gallery:
    def __init__(self, db, model_version: str):
        self.db = db
        self.model_version = model_version
        self.lock = threading.Lock()
        self.mat = np.zeros((0, 1), np.float32)
        self.owner = np.zeros((0,), np.int64)
        self.people: dict[int, dict] = {}
        self.version = 0          # tang moi lan nap lai -> pipeline kiem tra lai cac track dang chay
        self.reload()

    def reload(self) -> None:
        rows = self.db.load_gallery(self.model_version)
        with self.lock:
            if rows:
                self.mat = np.stack([r["vec"] for r in rows]).astype(np.float32)
                self.owner = np.array([r["employee_id"] for r in rows], np.int64)
                self.groups = [str(r.get("pose_group") or "frontal") for r in rows]
            else:
                self.mat = np.zeros((0, 1), np.float32)
                self.owner = np.zeros((0,), np.int64)
                self.groups = []
            self.people = {int(r["employee_id"]): {"code": r["code"], "name": r["name"]} for r in rows}
            self.version += 1

    def has(self, employee_id: int | None) -> bool:
        with self.lock:
            return employee_id in self.people

    def get_person(self, employee_id: int | None) -> dict | None:
        with self.lock:
            return dict(self.people[employee_id]) if employee_id in self.people else None

    @property
    def size(self) -> int:
        return len(self.people)

    def all_employee_similarities(self, emb: np.ndarray, pose_group: str | None = None) -> dict[int, float]:
        """Tra ve diem cosine cao nhat cua tung nhan vien (uu tien cung pose_group neu co) de dua vao bo tich luy LLR."""
        with self.lock:
            if self.mat.shape[0] == 0 or self.mat.shape[1] != emb.size:
                return {}
            sims = self.mat @ emb
            best: dict[int, float] = {}
            for idx, (o, s) in enumerate(zip(self.owner.tolist(), sims.tolist())):
                if pose_group and pose_group != "frontal" and self.groups[idx] not in (pose_group, "frontal"):
                    continue
                if s > best.get(o, -1.0):
                    best[o] = float(s)
            if not best:
                for o, s in zip(self.owner.tolist(), sims.tolist()):
                    if s > best.get(o, -1.0):
                        best[o] = float(s)
            return best

    def match(self, emb: np.ndarray, threshold: float, margin: float) -> Match:
        with self.lock:
            if self.mat.shape[0] == 0 or self.mat.shape[1] != emb.size:
                return Match(None, None, None, 0.0, 0.0, False)
            sims = self.mat @ emb
            # diem cao nhat moi nguoi (so voi embedding gan nhat cua ho)
            best: dict[int, float] = {}
            for o, s in zip(self.owner.tolist(), sims.tolist()):
                if s > best.get(o, -1.0):
                    best[o] = s
            ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
            top_id, top = ranked[0]
            second = ranked[1][1] if len(ranked) > 1 else 0.0
            ok = top >= threshold and (top - second) >= margin
            p = self.people[top_id]
            return Match(top_id, p["code"], p["name"], float(top), float(second), ok)
