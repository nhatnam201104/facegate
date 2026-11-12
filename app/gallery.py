"""Gallery trong RAM phan tach 3 ma tran con theo pose_group + so khop open-set (nguong + margin top-2)."""
from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np

POSE_GROUPS = ("frontal", "left_profile", "right_profile")


@dataclass
class Match:
    employee_id: int | None
    code: str | None
    name: str | None
    score: float          # cosine top-1
    second: float         # cosine top-2 (nguoi khac)
    accepted: bool
    pose_group: str = "frontal"

    @property
    def margin(self) -> float:
        return self.score - self.second


class Gallery:
    def __init__(self, db, model_version: str):
        self.db = db
        self.model_version = model_version
        self.lock = threading.Lock()
        self.mats: dict[str, np.ndarray] = {g: np.zeros((0, 1), np.float32) for g in POSE_GROUPS}
        self.owners: dict[str, np.ndarray] = {g: np.zeros((0,), np.int64) for g in POSE_GROUPS}
        self.coverage: dict[int, set[str]] = {}
        self.people: dict[int, dict] = {}
        self.version = 0          # tang moi lan nap lai -> pipeline kiem tra lai cac track dang chay
        self.reload()

    def reload(self) -> None:
        rows = self.db.load_gallery(self.model_version)
        grouped: dict[str, list[dict]] = {g: [] for g in POSE_GROUPS}
        cov: dict[int, set[str]] = {}
        people: dict[int, dict] = {}
        for r in rows:
            grp = r.get("pose_group") or "frontal"
            if grp not in grouped:
                grp = "frontal"
            grouped[grp].append(r)
            eid = int(r["employee_id"])
            cov.setdefault(eid, set()).add(grp)
            people[eid] = {"code": r["code"], "name": r["name"]}

        with self.lock:
            for g in POSE_GROUPS:
                items = grouped[g]
                if items:
                    self.mats[g] = np.stack([it["vec"] for it in items]).astype(np.float32)
                    self.owners[g] = np.array([it["employee_id"] for it in items], np.int64)
                else:
                    self.mats[g] = np.zeros((0, 1), np.float32)
                    self.owners[g] = np.zeros((0,), np.int64)
            self.coverage = cov
            self.people = people
            self.version += 1

    def has(self, employee_id: int | None) -> bool:
        with self.lock:
            return employee_id in self.people

    @property
    def size(self) -> int:
        return len(self.people)

    @property
    def profile_ready_count(self) -> int:
        with self.lock:
            return sum(1 for groups in self.coverage.values() if set(POSE_GROUPS).issubset(groups))

    def employee_pose_coverage(self, employee_id: int) -> dict:
        with self.lock:
            groups = self.coverage.get(employee_id, set())
            return {
                "frontal": int("frontal" in groups),
                "left_profile": int("left_profile" in groups),
                "right_profile": int("right_profile" in groups),
                "ready_90": set(POSE_GROUPS).issubset(groups),
            }

    def employee_similarity(self, employee_id: int, emb: np.ndarray | None, pose_group: str) -> float | None:
        """Trả về cosine tốt nhất giữa emb và các mẫu của đúng employee_id trong pose_group, hoặc None nếu chưa có mẫu."""
        if emb is None:
            return None
        grp = pose_group if pose_group in POSE_GROUPS else "frontal"
        with self.lock:
            if grp not in self.coverage.get(employee_id, set()):
                return None
            mat = self.mats[grp]
            owner = self.owners[grp]
            if mat.shape[0] == 0 or mat.shape[1] != emb.size:
                return None
            mask = owner == employee_id
            if not np.any(mask):
                return None
            return float(np.max(mat[mask] @ emb))

    def match(
        self,
        emb: np.ndarray | None,
        threshold: float,
        margin: float,
        pose_group: str = "frontal",
        profile_threshold: float | None = None,
        profile_margin: float | None = None,
    ) -> Match:
        if emb is None:
            return Match(None, None, None, 0.0, 0.0, False, pose_group)
        grp = pose_group if pose_group in POSE_GROUPS else "frontal"
        eff_thr = threshold if grp == "frontal" else float(profile_threshold if profile_threshold is not None else threshold + 0.06)
        eff_mar = margin if grp == "frontal" else float(profile_margin if profile_margin is not None else margin + 0.02)

        with self.lock:
            mat = self.mats[grp]
            owner = self.owners[grp]
            if mat.shape[0] == 0 or mat.shape[1] != emb.size:
                return Match(None, None, None, 0.0, 0.0, False, grp)
            sims = mat @ emb
            best: dict[int, float] = {}
            for o, s in zip(owner.tolist(), sims.tolist()):
                if s > best.get(o, -1.0):
                    best[o] = s
            ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
            top_id, top = ranked[0]
            second = ranked[1][1] if len(ranked) > 1 else 0.0
            ok = top >= eff_thr and (top - second) >= eff_mar
            p = self.people[top_id]
            return Match(top_id, p["code"], p["name"], float(top), float(second), ok, grp)
