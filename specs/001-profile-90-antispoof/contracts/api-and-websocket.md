# Interface Contracts: REST API, WebSocket & CSV Export

**Feature**: `001-profile-90-antispoof`
**Date**: 2026-11-11
**Base URL**: `http://127.0.0.1:8090`

Tất cả các trường mới đều được bổ sung tương thích ngược (các client/script hiện tại đọc các khóa cũ vẫn hoạt động không lỗi).

---

## 1. Hệ thống & Trạng thái Trực tiếp

### `GET /api/status`
Trả về trạng thái thời gian thực của pipeline nhận diện và cấu hình đa góc / chống giả mạo.

```json
{
  "backend": "edgeface",
  "model_version": "edgeface_xs_v1+3ddfa_v2",
  "max_supported_yaw": 90,
  "source": {
    "kind": "webcam",
    "label": "/dev/video0",
    "connected": true,
    "black": false
  },
  "camera_connected": true,
  "camera_fps": 29.8,
  "detect_fps": 5.0,
  "detect_ms": 68.4,
  "gallery": 24,
  "gallery_profile_ready": 19,
  "tracks": 1,
  "threshold": 0.42,
  "profile_threshold": 0.48,
  "margin": 0.06,
  "profile_margin": 0.08,
  "liveness": true,
  "liveness_modes": ["texture_fas", "context_medium", "body_coupling", "parallax_3d", "moire_fft"],
  "sleeping": false,
  "sleep_since": null,
  "idle_seconds": 30.0,
  "motion_zone": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
  "wakes_rejected": 2,
  "clips": true,
  "direction": "none",
  "hrm": "mock"
}
```

---

## 2. Đăng ký Khuôn mặt Mở rộng 7 Bước (Tới Góc Nghiêng Sâu ±85°)

### `POST /api/enroll/guided/start`
- **Form Parameters**:
  - `code` (`string`, required): Mã nhân viên (`^[A-Za-z0-9_.\-]{1,32}$`).
  - `name` (`string`, required): Họ tên nhân viên.
  - `profile_90` (`bool`, optional, default `true`): Thu thập đầy đủ 7 bước (5 bước chữ thập chuẩn + 2 bước nghiêng sườn sâu trái/phải `70°–85°`).
  - `supplement_only` (`bool`, optional, default `false`): Chỉ quay bổ sung 2 bước góc nghiêng sâu cho nhân viên cũ đã có sẵn 5 góc chính diện.

### `GET /api/enroll/guided/status`
```json
{
  "id": "9f81c204ab11",
  "state": "running",
  "step": 5,
  "total": 7,
  "instruction": "Quay hẳn mặt sang phải (góc nghiêng sườn ~80°)",
  "target": {"h": "right_deep", "v": null, "target_yaw": -78},
  "current_pose": {"yaw": -74.2, "pitch": 2.1, "roll": -1.4},
  "hint": "Đúng rồi, giữ nguyên",
  "ok": true,
  "hold": 0.72,
  "steps": [
    {"bin": "thang-giua", "pose_group": "frontal", "text": "Nhìn thẳng vào camera", "done": true, "h": 0, "v": 0},
    {"bin": "trai-giua", "pose_group": "frontal", "text": "Quay đầu nhẹ sang phải (~25°)", "done": true, "h": -1, "v": 0},
    {"bin": "phai-giua", "pose_group": "frontal", "text": "Quay đầu nhẹ sang trái (~25°)", "done": true, "h": 1, "v": 0},
    {"bin": "thang-ngang", "pose_group": "frontal", "text": "Ngẩng đầu lên", "done": true, "h": 0, "v": -1},
    {"bin": "thang-cui", "pose_group": "frontal", "text": "Cúi đầu xuống", "done": true, "h": 0, "v": 1},
    {"bin": "nghieng-sau-trai", "pose_group": "left_profile", "text": "Quay hẳn mặt sang phải (~80°)", "done": false, "h": -2, "v": 0},
    {"bin": "nghieng-sau-phai", "pose_group": "right_profile", "text": "Quay hẳn mặt sang trái (~80°)", "done": false, "h": 2, "v": 0}
  ],
  "error": null,
  "report": null
}
```

### `GET /api/employees`
Mỗi phần tử nhân viên bổ sung trường `pose_coverage` để UI hiển thị trạng thái hỗ trợ góc 90°:
```json
{
  "id": 12,
  "code": "NV012",
  "name": "Trần Thị Minh",
  "status": "active",
  "n_emb": 7,
  "pose_coverage": {
    "frontal": 5,
    "left_profile": 1,
    "right_profile": 1,
    "ready_90": true
  },
  "thumb": "enroll/NV012/00_thang-giua.jpg",
  "report": { "..." : "..." }
}
```

---

## 3. Sự kiện, Lịch sử Xuất hiện & Hậu kiểm HR

### `GET /api/events`
- **Query Parameters**:
  - `limit` (`int`, default `200`, max `2000`)
  - `tier` (`string`, optional): Lọc theo `'verified'` | `'profile_verified'` | `'rejected_spoof'`
  - `pose_group` (`string`, optional): Lọc theo `'frontal'` | `'left_profile'` | `'right_profile'`
- **Response Item**:
```json
{
  "id": 104,
  "ts": 1794391502.41,
  "camera": "gate-1",
  "kind": "checkin",
  "att_type": "in",
  "employee_id": 12,
  "code": "NV012",
  "name": "Trần Thị Minh",
  "score": 0.5412,
  "margin": 0.112,
  "pose_group": "left_profile",
  "yaw": -81.4,
  "verification_tier": "profile_verified",
  "liveness": 0.88,
  "liveness_mode": "context_parallax",
  "liveness_detail": {
    "fas": null,
    "medium_clean": true,
    "body_coupled": true,
    "parallax_ok": true,
    "parallax_score": 0.91,
    "moire_ok": true,
    "reason": null
  },
  "latency_ms": 940.5,
  "frames": 6,
  "snapshot": "snapshots/20261111/075502_3_NV012.jpg",
  "hrm_status": "sent",
  "label": null
}
```

### `GET /api/appearances`
- **Query Parameters bổ sung**:
  - `tier` (`string`, optional): `'verified'` | `'profile_verified'` (hỗ trợ HR lọc nhanh các lượt chấm công góc nghiêng kèm video clip để duyệt cuối tháng).

### `GET /api/metrics`
Bổ sung các chỉ số đo lường tách theo góc nhìn và phương thức chống giả mạo:
```json
{
  "events_total": 320,
  "recognized": 280,
  "recognized_frontal": 215,
  "recognized_profile": 65,
  "unknown": 28,
  "spoof": 12,
  "spoof_by_reason": {
    "screen_detected": 6,
    "multi_face_single_body": 3,
    "planar_no_parallax": 2,
    "texture_spoof": 1
  },
  "false_accept_rate": 0.0,
  "false_accept_rate_profile": 0.0,
  "false_reject_rate": 0.021,
  "spoof_false_rate": 0.0,
  "latency_ms_p50": 580.0,
  "latency_ms_p90": 1050.0
}
```

### `GET /api/events.csv` & `GET /api/attendance.csv`
- `events.csv` bổ sung các cột: `pose_group`, `yaw`, `verification_tier`, `liveness_mode`.
- `attendance.csv` bổ sung cột `Mức xác thực` (`Chính diện` / `Góc nghiêng (có clip)`).

---

## 4. Hợp đồng Gói tin WebSocket (`WS /ws`)

Gói tin `type: "event"` giữ nguyên cấu trúc gốc và đính kèm thêm thông tin góc & phân tầng để màn hình Trực tiếp hiển thị rõ:
```json
{
  "type": "event",
  "event": {
    "id": 104,
    "kind": "checkin",
    "code": "NV012",
    "name": "Trần Thị Minh",
    "pose_group": "left_profile",
    "yaw": -81.4,
    "verification_tier": "profile_verified",
    "liveness_mode": "context_parallax"
  },
  "greet": {
    "status": "on_time",
    "minutes": 0
  }
}
```
Khi `kind == "spoof"`, trường `liveness_detail.reason` cung cấp thông điệp tiếng Việt cụ thể trên HUD/thẻ cảnh báo (ví dụ: *"Phát hiện màn hình điện thoại"*, *"Ảnh phẳng không có chiều sâu 3D"*, *"Hai khuôn mặt trên cùng người"*).
