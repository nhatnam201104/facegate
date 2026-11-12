"""Kiểm thử hợp đồng API & CSV cho phân tầng mức xác thực (US4) và hiệu năng khởi động."""
import json
import time

from app.attendance import Shift, timesheet
from app.config import load_config
from app.db import DB


def test_events_appearances_tier_filters_and_timesheet_propagation(tmp_path):
    cfg = load_config()
    db = DB(tmp_path / "api_test.db")
    emp_id = db.create_employee("NV901", "Trần Minh Quân")
    db.set_status(emp_id, "active")

    now = time.time()
    ev_prof = db.add_event(
        ts=now - 60,
        camera="gate-1",
        employee_id=emp_id,
        code="NV901",
        name="Trần Minh Quân",
        score=0.53,
        margin=0.11,
        direction="IN",
        latency_ms=890.0,
        frames=6,
        snapshot=None,
        model_version="edgeface_xs_v1+3ddfa_v2",
        hrm_status="sent",
        kind="checkin",
        att_type="in",
        liveness=0.86,
        pose_group="left_profile",
        yaw=-81.2,
        verification_tier="profile_verified",
        liveness_mode="context_parallax",
        liveness_detail=json.dumps({"parallax_ok": True, "moire_ok": True, "reason": None}),
    )
    db.add_event(
        ts=now - 10,
        camera="gate-1",
        employee_id=None,
        code="NV901",
        name="Trần Minh Quân",
        score=0.51,
        margin=0.09,
        direction=None,
        latency_ms=420.0,
        frames=3,
        snapshot=None,
        model_version="edgeface_xs_v1+3ddfa_v2",
        hrm_status="skipped",
        kind="spoof",
        att_type=None,
        liveness=0.05,
        pose_group="left_profile",
        yaw=-79.0,
        verification_tier="rejected_spoof",
        liveness_mode="context_parallax",
        liveness_detail=json.dumps({"medium_clean": False, "reason": "screen_detected"}),
    )

    db.add_appearance(
        kind="employee",
        employee_id=emp_id,
        code="NV901",
        name="Trần Minh Quân",
        start_ts=now - 65,
        end_ts=now - 58,
        score=0.53,
        liveness=0.86,
        snapshot=None,
        event_id=ev_prof,
        camera="gate-1",
        pose_group="left_profile",
        min_abs_yaw=78.5,
        verification_tier="profile_verified",
        spoof_reason=None,
    )

    # Kiểm tra lọc sự kiện theo tier & pose_group
    only_prof = db.list_events(100, tier="profile_verified")
    assert len(only_prof) == 1 and only_prof[0]["code"] == "NV901"
    only_spoof = db.list_events(100, tier="rejected_spoof")
    assert len(only_spoof) == 1 and only_spoof[0]["kind"] == "spoof"

    # Kiểm tra lọc lịch sử xuất hiện theo tier
    apps_prof = db.list_appearances(now - 3600, now + 60, tier="profile_verified")
    assert len(apps_prof) == 1 and apps_prof[0]["min_abs_yaw"] == 78.5

    # Kiểm tra bảng chấm công lan truyền đúng verification_tier
    ts = timesheet(db, None, None, Shift.from_cfg(cfg))
    assert len(ts["rows"]) == 1
    assert ts["rows"][0]["verification_tier"] == "profile_verified"
    assert ts["rows"][0]["pose_group"] == "left_profile"


def test_employee_tab_no_inline_onclick_xss_and_name_validation():
    import re
    from pathlib import Path
    import pytest
    from fastapi import HTTPException
    from app.main import _check

    static_dir = Path(__file__).resolve().parent.parent / "app" / "static"
    for js_name in ("app.js", "attendance.js"):
        src = (static_dir / js_name).read_text(encoding="utf-8")
        # Không được nội suy dữ liệu người dùng vào bên trong thuộc tính onclick="..."
        inline_handlers = re.findall(r'onclick="[^"]*\$\{[^"]*\}[^"]*"', src)
        assert not inline_handlers, f"{js_name} còn chứa inline onclick nội suy biến động: {inline_handlers}"

    # Tên có dấu nháy đơn hợp lệ (như O'Neil) vẫn được phép đăng ký
    _check("NV_ONEIL", "Sean O'Neil")

    # Từ chối tên chứa ký tự điều khiển hoặc vượt quá giới hạn độ dài hợp lý
    with pytest.raises(HTTPException) as exc_ctrl:
        _check("NV_CTRL", "Nguyen\x00Van A")
    assert exc_ctrl.value.status_code == 400

    with pytest.raises(HTTPException) as exc_long:
        _check("NV_LONG", "A" * 150)
    assert exc_long.value.status_code == 400
