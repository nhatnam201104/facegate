"""Cham cong theo check-in: moi nhan vien 1 lan/ngay (lan dau camera xac nhan), so phut di muon, vang."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta

MAX_DAYS = 92   # gioi han khoang ngay moi lan truy van


@dataclass(frozen=True)
class Shift:
    start: dtime
    end: dtime
    grace: int                 # phut cho phep tre/som
    workdays: frozenset[int]   # 1 = Thu 2 ... 7 = Chu nhat

    @classmethod
    def from_cfg(cls, cfg: dict) -> "Shift":
        a = cfg.get("attendance", {})
        return cls(_hm(a.get("shift_start", "08:00")), _hm(a.get("shift_end", "17:00")),
                   int(a.get("grace_minutes", 5)), frozenset(int(d) for d in a.get("workdays", [1, 2, 3, 4, 5])))

    def as_dict(self) -> dict:
        return {"start": self.start.strftime("%H:%M"), "end": self.end.strftime("%H:%M"),
                "grace_minutes": self.grace, "workdays": sorted(self.workdays)}


def _hm(s: str) -> dtime:
    h, m = str(s).split(":")
    return dtime(int(h), int(m))


def day_range(d: date) -> tuple[float, float]:
    t0 = datetime.combine(d, dtime.min)
    return t0.timestamp(), (t0 + timedelta(days=1)).timestamp()


def _ceil_min(delta: timedelta) -> int:
    return -int(-delta.total_seconds() // 60)


def late_minutes(ts: float, shift: Shift) -> int:
    """So phut muon tinh tu gio vao ca; trong khoang grace thi coi nhu dung gio (0)."""
    dt = datetime.fromtimestamp(ts)
    start = datetime.combine(dt.date(), shift.start)
    return _ceil_min(dt - start) if dt > start + timedelta(minutes=shift.grace) else 0


def greeting(ts: float, shift: Shift) -> dict:
    """Trang thai hien tren man hinh chao khi vua check-in."""
    late = late_minutes(ts, shift)
    return {"status": "late" if late else "on_time", "minutes": late}


def _parse_day(s: str | None, default: date) -> date:
    return date.fromisoformat(s) if s else default


def timesheet(db, date_from: str | None, date_to: str | None, shift: Shift, code: str | None = None) -> dict:
    today = date.today()
    d0 = _parse_day(date_from, today)
    d1 = _parse_day(date_to, d0)
    if d1 < d0:
        d0, d1 = d1, d0
    if (d1 - d0).days >= MAX_DAYS:
        raise ValueError(f"Khoảng ngày tối đa {MAX_DAYS} ngày")

    t0, _ = day_range(d0)
    _, t1 = day_range(d1)
    from .ledger import effective_checkins_between
    eff_map = effective_checkins_between(db, d0, d1, code)
    by_key: dict[tuple[date, str], list[dict]] = {k: [v] for k, v in eff_map.items()}
    names: dict[str, str] = {v["code"]: v["name"] for v in eff_map.values()}

    # ten hien tai uu tien hon ten luu trong su kien cu; nhan vien dang hoat dong -> tinh vang
    for e in db.q("SELECT code, name FROM employees"):
        if e["code"] in names:
            names[e["code"]] = e["name"]
    active = db.q("SELECT code, name, approved_at FROM employees WHERE status='active'")
    for a in active:
        names[a["code"]] = a["name"]
    approved = {a["code"]: a["approved_at"] or 0 for a in active}
    now = datetime.now()
    late_mark = datetime.combine(today, shift.start) + timedelta(minutes=shift.grace)
    codes = [code] if code else sorted(names, key=lambda c: names[c])

    rows = []
    d = d1
    while d >= d0:
        day_t0, day_t1 = day_range(d)
        for c in codes:
            evs = by_key.get((d, c), [])
            if not evs:
                # vang: ngay lam viec da qua (hom nay: het ca), nhan vien da duoc duyet truoc ngay do
                # hom nay, qua gio vao ma chua thay -> "chua den"
                if c not in approved or d.isoweekday() not in shift.workdays or approved[c] >= day_t1:
                    continue
                if d < today or (d == today and now.time() > shift.end):
                    st = "absent"
                elif d == today and now > late_mark:
                    st = "not_arrived"
                else:
                    continue
                rows.append({"date": d.isoformat(), "code": c, "name": names.get(c, c), "status": st,
                             "check_in": None, "late_min": 0})
                continue
            t_in = evs[0]["ts"]   # lan check-in dau tien trong ngay
            late = late_minutes(t_in, shift)
            rows.append({"date": d.isoformat(), "code": c, "name": names.get(c, c),
                         "status": "late" if late else "ok", "check_in": t_in, "late_min": late,
                         "ledger_id": evs[0].get("ledger_id"),
                         "verification_tier": evs[0].get("verification_tier", "verified"),
                         "audit_status": evs[0].get("audit_status", "not_required")})
        d -= timedelta(days=1)

    return {"from": d0.isoformat(), "to": d1.isoformat(), "shift": shift.as_dict(),
            "rows": rows, "summary": _summary(rows)}


def _summary(rows: list[dict]) -> list[dict]:
    acc: dict[str, dict] = {}
    for r in rows:
        s = acc.setdefault(r["code"], {"code": r["code"], "name": r["name"], "days": 0, "on_time": 0,
                                       "late": 0, "late_min": 0, "absent": 0})
        if r["status"] == "absent":
            s["absent"] += 1
        elif r["status"] in ("ok", "late"):
            s["days"] += 1
            s["on_time" if r["status"] == "ok" else "late"] += 1
            s["late_min"] += r["late_min"]
    return sorted(acc.values(), key=lambda s: s["name"])


def today_stats(db, shift: Shift) -> dict:
    today = date.today()
    t0, t1 = day_range(today)
    from .ledger import effective_checkins_between
    eff = effective_checkins_between(db, today, today)
    first: dict[str, float] = {v["code"]: v["ts"] for v in eff.values()}
    total = db.q("SELECT COUNT(*) AS n FROM employees WHERE status='active'")[0]["n"]
    return {"present": len(first), "total": total,
            "late": sum(1 for ts in first.values() if late_minutes(ts, shift)),
            "spoof": db.count_kind_between("spoof", t0, t1),
            "unknown": db.count_kind_between("unknown", t0, t1),
            "shift": shift.as_dict()}
