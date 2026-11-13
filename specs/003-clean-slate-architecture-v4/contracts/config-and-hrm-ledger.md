# Interface Contract: Cấu hình `config.yaml`, Sổ cái Chấm công & Tích hợp HRM Hai chiều

**Feature**: `003-clean-slate-architecture-v4`  
**Date**: 2026-11-13

---

## 1. Lược đồ Cấu hình Mở rộng (`config.yaml` — Tương thích ngược)

```yaml
camera:
  id: gate-1
  model_profile: tapo_c310           # tapo_c310 | enterprise_rtsp | webcam
  main_source: rtsp://127.0.0.1:8554/gate1_main   # kéo qua MediaMTX Single Puller (đã lọc -an)
  sub_source: rtsp://127.0.0.1:8554/gate1_sub     # dùng cho canh ngủ ngoài giờ cao điểm
  whep_url: /media/gate1_main/whep
  playback_url: http://127.0.0.1:9996
  overlay_offset_ms: 0               # hiệu chuẩn ở K1 bằng bài test đồng hồ mili-giây (H8)

pipeline:
  backend: edgeface                  # giữ EdgeFace-XS (MIT) FP32/FP16
  execution_provider: cpu            # cpu | openvino (chỉ bật sau khi vượt cổng Replay K4)
  detect_fps: 6                      # nhịp suy luận trên luồng 15 fps của C310
  max_faces: 10                      # nâng từ 3 lên 10 mặt ở K4
  two_stage_roi: true                # resize chính frame /stream1 về 640px tìm người -> cắt 1:1 trên 3MP (H33, H35)
  fallback_det_width: 1280           # khi đứng quá sát không thấy thân người

quality:
  min_face: 60                       # cạnh ngắn khuôn mặt tối thiểu (px) trên vùng cắt 1:1 3MP
  min_blur: 35                       # phương sai Laplacian tối thiểu trên vùng da nhìn thấy
  min_brightness: 45
  max_brightness: 225
  max_yaw: 90                        # mở khóa toàn dải góc ngang ±90° (từ mức 35° của main)
  max_pitch: 35                      # chặn góc ngẩng/cúi quá dốc
  max_roll: 35                       # chặn nghiêng đầu quá mức

decision:
  novelty_min_dt_ms: 250             # khử tương quan chuỗi frame liên tiếp (H4)
  novelty_min_dyaw_deg: 8.0
  llr_weights:                       # hiệu chỉnh trên cặp ghép chéo K0 (H25)
    a_cos: 12.0
    b_bias: -4.8
    c_abs_yaw: -0.015
    d_quality: 0.60
  tau_threshold: 0.45                # rào cứng 1: LLR(top1) >= tau (Nguyên tắc I)
  margin_base: 0.70                  # rào cứng 2: LLR(top1) - LLR(top2) >= margin_base + margin_yaw_slope * |yaw|/90
  margin_yaw_slope: 0.30
  auto_enroll_online: false          # CẤM tuyệt đối tự học mẫu online chống đầu độc (H17)

tracking:
  crossing_face_iou: 0.25            # kích hoạt Crossing Quarantine khi 2 mặt/đầu giao cắt (H3)
  crossing_person_iou: 0.40
  viewport_bottom_grace: 0.80        # y2 >= 0.80*H coi là cắt khung hợp lệ, tắt phạt unattached_face (H3)

power:
  sleep: true
  active_windows:                    # Lịch thức theo ca: luôn giữ nóng decode /stream1, 0ms chờ GOP (H34)
    - ["06:30", "09:30"]
    - ["16:30", "18:30"]
  idle_seconds: 30

audit_and_safety:
  profile_audit_sample_rate: 0.05    # lấy mẫu ngẫu nhiên 5% lượt profile_verified cuối tuần (H7)
  near_spoof_window_sec: 300         # tự gắn pending_audit nếu có cảnh báo spoof trong 5 phút lân cận
  min_free_disk_gb: 2.0              # tự dọn clip cũ không vướng legal_hold khi đĩa trống < 2 GB (H15)
  heartbeat_timeout_sec: 5.0         # báo động mất luồng / chết worker / chết ffmpeg ingest (H15, H30)
```

---

## 2. Hợp đồng REST API Sổ cái Chấm công, Hậu kiểm & Fail-safe (`H7`, `H15`, `H22`)

Tất cả các endpoint thay đổi dữ liệu yêu cầu xác thực phiên đăng nhập có vai trò `hr` hoặc `admin` (pha `K5`) và ghi vết vào `audit_logs`.

### 2.1. Thu hồi Lượt Chấm công Nhận nhầm / Sai sót (`POST /api/attendance/revoke`)
- **Request Body**:
  ```json
  {
    "target_ledger_id": 104,
    "idempotency_key": "revoke-104-20261113",
    "reason": "Hậu kiểm clip thấy đồng nghiệp đi sượt qua trước mặt"
  }
  ```
- **Hành vi**:
  1. Kiểm tra `target_ledger_id` tồn tại và chưa bị thu hồi; chèn bút toán mới `action = "checkin_revoked"` vào `attendance_ledger`.
  2. Nếu bút toán gốc đã đồng bộ sang ERPNext (`remote_doc_id` khác rỗng), đẩy tác vụ hủy vào Outbox để gọi `DELETE /api/resource/Employee%20Checkin/{remote_doc_id}` (hoặc endpoint hủy chứng từ tương ứng của Frappe).
  3. Tính lại trạng thái đi làm của nhân viên trong ngày `work_date` đó ngay lập tức.

### 2.2. Chấm công Dự phòng / Nhập bù Có Kiểm toán (`POST /api/attendance/manual` — `H15`)
- **Request Body**:
  ```json
  {
    "employee_code": "NV002",
    "work_date": "2026-11-13",
    "effective_time": "07:56:00",
    "idempotency_key": "manual-NV002-2026-11-13",
    "reason": "Mất điện khu vực cổng chính từ 07:50 đến 08:10, đối chiếu sổ bảo vệ"
  }
  ```
- **Hành vi**: Chèn bút toán `action = "checkin_manual"`, `verification_tier = "manual_fallback"`, xếp hàng đẩy sang HRM Outbox kèm `idempotency_key`.

### 2.3. Quản lý Hàng chờ Hậu kiểm Ngoại lệ & Khóa `legal_hold` (`H7`, `H15`)
- `GET /api/attendance/audit-queue?from=YYYY-MM-DD&to=YYYY-MM-DD`: Trả về danh sách các bút toán có `audit_status = "pending_audit"` kèm liên kết phát clip gốc + lớp phủ sidecar.
- `POST /api/appearances/{id}/legal-hold`:
  ```json
  {
    "hold": true,
    "reason": "Đang giải quyết thắc mắc bảng công tháng 11"
  }
  ```
  - Khi HR hoàn tất xử lý (`hold: false` hoặc duyệt xong mục `pending_audit`), hệ thống hạ `legal_hold = 0` để clip quay về chu kỳ xóa tự động `retention_days` theo Nguyên tắc IV của Constitution.

### 2.4. Endpoint Sức khỏe Vận hành (`GET /api/health` — `H15`, `H30`)
- Trả về trạng thái tổng hợp `{ok, camera_stream1, ffmpeg_ingest_alive, perception_worker_alive, free_disk_gb, legal_hold_clips_count, fallback_mode_active}` để màn hình lễ tân/bảo vệ bật ngay biểu ngữ cảnh báo đỏ và hướng dẫn chuyển sang kênh chấm công dự phòng khi gặp sự cố lúc vào ca.
