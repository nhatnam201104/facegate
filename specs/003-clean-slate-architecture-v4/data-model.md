# Phase 1 Data Model: Tái Kiến trúc Toàn diện FaceGate v4

**Feature**: `003-clean-slate-architecture-v4`  
**Date**: 2026-11-13  
**Storage Strategy**: Tương thích ngược 100% trên SQLite WAL cho triển khai 1 cổng (`data/facegate.db` với `_migrate` tự động), sẵn sàng chuyển đổi sang PostgreSQL khi gom từ ≥ 2 cổng ở pha `K5`; vòng đệm video thô ngắn hạn đặt trên `tmpfs` (RAM).

---

## 1. Kiến trúc Luồng Dữ liệu Giữa 3 Mặt phẳng

```text
[Tapo C310: /stream1 3MP + /stream2 360p]
       │ (RTSP TCP, ffmpeg -c:v copy -an loại bỏ pcm_alaw)
       ▼
[1. MediaMTX Media Plane] ──(fMP4 1s segments)──► [tmpfs RAM Buffer (≤ 10 phút, ~300MB/cam)]
       │                  ◄─(Playback HTTP /get)──────────┐
       ├──(WebRTC WHEP H.264)──► [Trình duyệt <video>]    │ (Trích MP4 sạch + SHA-256)
       │                                                  │
       ▼ (Local RTSP Loopback)                            │
[2. Perception Process] ──(Unix Socket: TrackedObject)──► [3. FastAPI Control & Business Plane]
                                                          ├──► WebSocket Metadata ──► <canvas>
                                                          ├──► SQLite/PG (Append-Only Ledger)
                                                          ├──► Clip Sidecar (.meta.json + HMAC)
                                                          └──► HRM Outbox (Create / Revoke)
```

---

## 2. Thực thể Giao tiếp Thời gian thực & Replay (In-Memory / IPC / Sidecar)

### 2.1. `TrackedObject` (Schema Tổng quát Chờ Sẵn Phase 2 SKU — `H16`, `H21`)

Thực thể chuẩn hóa dùng chung cho luồng IPC từ `Perception Process`, gói tin WebSocket vẽ `<canvas>` và mảng `timeline` trong tệp sidecar `.meta.json` của clip bằng chứng.

| Trường | Kiểu dữ liệu | Bắt buộc | Quy tắc & Ý nghĩa |
|---|---|---|---|
| `camera_id` | `str` | Có | Mã định danh nguồn camera (vd: `"gate-1"`, `"warehouse-sku-1"`). |
| `ts_server_ns` | `int` | Có | Dấu thời gian đơn điệu/NTP của Server ngay lúc nhận khung hình (`time.time_ns()`), là nguồn giờ chuẩn duy nhất (`H16`). |
| `track_id` | `int` | Có | Định danh hành trình duy nhất trong phiên hoạt động của worker. |
| `kind` | `str` | Có | `"face"` \| `"person"` \| `"sku"` (cho phép Phase 2 dùng lại nguyên vẹn Media Plane & UI). |
| `bbox_norm` | `tuple[float, float, float, float]` | Có | Tọa độ chuẩn hóa `[x1, y1, x2, y2]` trong miền `[0.0, 1.0]` để `<canvas>` vẽ chính xác độc lập độ phân giải hiển thị. |
| `vel_norm` | `tuple[float, float]` | Có | Vận tốc chuẩn hóa `(vx, vy)` (đơn vị/giây) phục vụ nội suy ngắn hạn `≤ 120 ms` trên trình duyệt (`H8`). |
| `state` | `str` | Có | Trạng thái máy quyết định: `"SEEN"` \| `"COLLECTING"` \| `"QUARANTINED"` \| `"IDENTIFIED"` \| `"LIVE_CONFIRMED"` \| `"COMMITTED"` \| `"REJECTED_SPOOF"` \| `"UNKNOWN"`. |
| `color_role` | `str` | Có | `"ok"` (xanh lá) \| `"checking"` (cam) \| `"alert"` (đỏ) \| `"low_quality"` (xám). |
| `label` | `str` | Có | Chuỗi tiếng Việt hiển thị trên khung bao (vd: `"Nguyễn Văn A · Đúng giờ"`, `"Đang xác minh lại..."`). |
| `verification_tier` | `str \| None` | Không | `"verified"` \| `"profile_verified"` \| `"rejected_spoof"` \| `"unverified"`. |
| `meta` | `dict` | Có | Thông tin mở rộng theo `kind` (với `"face"`: `employee_code`, `yaw`, `llr_top1`, `llr_margin`, `liveness_prob`, `quarantine: bool`; với `"sku"`: `sku_code`, `barcode`). |

---

### 2.2. Máy Trạng thái Quyết định Liên tục (`TrackDecisionState` — `H3`, `H4`, `H25`)

Mỗi hành trình khuôn mặt trong `Perception Process` vận hành theo máy trạng thái hữu hạn tường minh:

```text
  [SEEN] ──(frame đạt chất lượng cơ bản)──► [COLLECTING]
                                                │   ▲
          (2 hộp giao cắt IoU > 0.25: xóa mẫu   │   │ (tách nhau xong: thu thập lại từ đầu
           giao cắt, cấm chốt check-in)         ▼   │  đủ min_frames + threshold + margin)
                                          [QUARANTINED]
                                                │
  [COLLECTING] ──(LLR_top1 ≥ τ VÀ LLR_top1 - LLR_top2 ≥ μ(yaw))──► [IDENTIFIED(tier)]
       │                                                                   │
       │ (kết thúc track không vượt ngưỡng)        (vượt cổng Liveness)    │ (vi phạm Spoof)
       ▼                                                   ▼               ▼
   [UNKNOWN]                                       [LIVE_CONFIRMED]   [REJECTED_SPOOF]
                                                           │
                                              (qua vạch cửa / rule ca)
                                                           ▼
                                                      [COMMITTED]
```

- **Quy tắc Tích lũy Điểm & Khử Tương quan (`Novelty Gate` — `H4`, `H25`)**:
  - Mẫu khung hình mới tại thời điểm $t$ chỉ được cộng vào bộ tích lũy khi cách mẫu được chấp nhận gần nhất $\Delta t \ge 250\text{ ms}$ **hoặc** góc đầu xoay thêm $|\Delta\text{yaw}| \ge 8^\circ$.
  - Điểm đóng góp của mẫu đối với ứng viên $k$: $\ell_k(t) = a \cdot s_k(t) + b + c \cdot |\text{yaw}(t)| + d \cdot q(t)$ với $a > 0$.
  - Điều kiện bắt buộc để chuyển sang `IDENTIFIED`: $\overline{\text{LLR}}_{\text{top1}} \ge \tau$ **và** $\overline{\text{LLR}}_{\text{top1}} - \overline{\text{LLR}}_{\text{top2}} \ge \mu_0 + \mu_1 \cdot \frac{|\overline{\text{yaw}}|}{90^\circ}$.

---

### 2.3. Định dạng Sidecar Bằng chứng Ký số (`.meta.json` — `P2`, `H9`, `H28`)

Mỗi clip lịch sử lưu tại `data/clips/YYYYMMDD/<hhmmss>_<appearance_id>.mp4` (chỉ chứa 1 video stream H.264 gốc, 0 audio stream) đi kèm một tệp metadata JSON nguyên khối cùng tên `.meta.json`:

| Trường | Kiểu | Mô tả |
|---|---|---|
| `appearance_id` | `int` | Khóa chính bản ghi trong bảng `appearances`. |
| `camera_id` | `str` | Định danh camera. |
| `clip_rel_path` | `str` | Đường dẫn tương đối tới file `.mp4` gốc sạch. |
| `clip_sha256` | `str` | Mã băm SHA-256 của toàn bộ nội dung file `.mp4` gốc. |
| `clip_start_server_ns` | `int` | Thời điểm bắt đầu thực tế của keyframe đầu tiên trong file MP4. |
| `event_offset_ms` | `int` | Độ lệch (ms) từ đầu file MP4 tới khoảnh khắc khuôn mặt xuất hiện/chốt sự kiện (`2000..2000+GOP_ms`). |
| `audio_stripped` | `bool` | Luôn bằng `true` (`H28`). |
| `timeline` | `list[dict]` | Chuỗi `{offset_ms, objects: [TrackedObject, ...]}` để trình phát trên Web hoặc FFmpeg vẽ lại chính xác lớp phủ. |
| `hmac_sha256` | `str` | Chữ ký `HMAC-SHA256(secret_key, canonical_json_without_hmac)` chống chỉnh sửa hậu kỳ. |

---

### 2.4. Gói Dữ liệu Đánh giá & Replay Ngoại tuyến (`K0 Observation Bundle` — `H1`, `H11`, `H14`)

Lưu ngoài Git tại thư mục được phân quyền chặt có thời hạn xóa theo đơn đồng ý `H11`:
- `manifest.json`: Thông tin phiên quay (cấu hình góc lắp `frontal_corridor` hay `side_walk`, độ rọi `lux`, khoảng cách mốc mét, chế độ `day_mode` hay `ir_night`, GOP đo bằng `ffprobe`, danh sách tình nguyện viên đã ký consent).
- `observations.jsonl`: Mỗi dòng ghi một khung hình giải mã tuần tự `{frame_idx, ts_ns, person_boxes, raw_faces, crops_rel_paths, quality_metrics, pose3d, embeddings_by_model, liveness_signals}`.
- `crops/`: Ảnh cắt vùng Đầu–Vai gốc nén PNG / JPEG-95 để khi sang `K3`/`K4` thay đổi thuật toán `align_by_pose` hoặc runtime suy luận vẫn tái tạo lại được vector chính xác mà không cần giữ file video 3MP dung lượng lớn.

---

## 3. Lược đồ Cơ sở Dữ liệu Mở rộng (`data/facegate.db` — Tương thích ngược qua `_migrate`)

Giữ nguyên toàn bộ các bảng hiện hữu (`employees`, `embeddings`, `enrollments`, `events`, `appearances`) để không phá vỡ truy vấn cũ, đồng thời bổ sung bảng sổ cái bất biến và các cột kiểm soát vận hành:

### 3.1. Bảng mới `attendance_ledger` (Sổ cái Chấm công Append-Only — `H22`, `H7`)

Không bao giờ `UPDATE` ghi đè giờ công hay `DELETE` lịch sử chấm công; trạng thái bảng công hàng ngày luôn được suy dẫn từ chuỗi sự kiện theo thứ tự `id` tăng dần.

| Cột | Kiểu SQLite / PG | Ràng buộc | Mô tả |
|---|---|---|---|
| `id` | `INTEGER PRIMARY KEY AUTOINCREMENT` | `NOT NULL` | Thứ tự bất biến của bút toán. |
| `idempotency_key` | `TEXT UNIQUE` | `NOT NULL` | Khóa chống ghi trùng (vd: `"auto:emp12:2026-11-13"` hoặc UUID của thao tác HR). |
| `action` | `TEXT` | `NOT NULL` | `"checkin_created"` \| `"checkin_revoked"` \| `"checkin_manual"`. |
| `target_ledger_id` | `INTEGER` | `NULL` | Tham chiếu tới `attendance_ledger.id` gốc bị thu hồi (khi `action = 'checkin_revoked'`). |
| `employee_id` | `INTEGER` | `NOT NULL` | Mã nội bộ nhân viên. |
| `code` | `TEXT` | `NOT NULL` | Mã nhân viên (giữ lịch sử ngay cả khi xóa hồ sơ sinh trắc học). |
| `work_date` | `TEXT` | `NOT NULL` | Ngày tính ca `YYYY-MM-DD`. |
| `effective_ts` | `REAL` | `NOT NULL` | Thời điểm check-in có hiệu lực (lấy từ `ts_server_ns / 1e9` hoặc giờ HR nhập bù). |
| `verification_tier` | `TEXT` | `NOT NULL` | `"verified"` \| `"profile_verified"` \| `"manual_fallback"`. |
| `audit_status` | `TEXT` | `NOT NULL` | `"not_required"` \| `"pending_audit"` (ca sát biên / gần spoof / mẫu 5%) \| `"audit_confirmed"` \| `"audit_revoked"`. |
| `actor` | `TEXT` | `NOT NULL` | `"system:gate-1"` hoặc tài khoản HR thực hiện (`"hr:lan.nguyen"`). |
| `reason` | `TEXT` | `NULL` | Lý do bắt buộc khi `checkin_revoked` hoặc `checkin_manual`. |
| `event_id` | `INTEGER` | `NULL` | Liên kết tới `events.id` / `appearances.id` (nếu phát sinh từ camera). |
| `hrm_sync_status` | `TEXT` | `NOT NULL` | `"pending"` \| `"sent"` \| `"failed"` \| `"skipped"`. |
| `remote_doc_id` | `TEXT` | `NULL` | Mã chứng từ trả về từ ERPNext (vd: `"EMP-CKIN-2026-00042"`) để gọi hủy chính xác khi `checkin_revoked`. |
| `hrm_attempts` | `INTEGER` | `DEFAULT 0` | Số lần thử đồng bộ Outbox. |
| `hrm_last_error` | `TEXT` | `NULL` | Phản hồi lỗi gần nhất từ HRM. |
| `created_at` | `REAL` | `NOT NULL` | Thời điểm ghi sổ trên Server. |

### 3.2. Cột mở rộng trên các bảng hiện hữu của nhánh `main` (`embeddings`, `events`, `appearances`)

| Bảng | Cột thêm qua `_migrate` | Kiểu | Mô tả |
|---|---|---|---|
| `embeddings` | `pose_group` | `TEXT NOT NULL DEFAULT 'frontal'` | `"frontal"` \| `"left_profile"` \| `"right_profile"` phục vụ đăng ký 7 góc & `supplement_only`. |
| `embeddings` | `yaw`, `pitch` | `REAL` | Góc ước lượng 3D của mẫu đăng ký. |
| `events` | `verification_tier` | `TEXT` | `"verified"` \| `"profile_verified"` \| `"rejected_spoof"` \| `"unverified"`. |
| `events` | `pose_group`, `yaw` | `TEXT`, `REAL` | Nhóm tư thế và góc yaw trung bình dùng để chốt sự kiện. |
| `events` | `liveness_mode`, `liveness_detail` | `TEXT`, `TEXT` | Phương thức xác thực thực thể sống và JSON chi tiết từng tín hiệu. |
| `appearances` | `pose_group`, `min_abs_yaw`, `verification_tier`, `spoof_reason` | `TEXT`, `REAL`, `TEXT`, `TEXT` | Thông tin góc nhìn nhỏ nhất, hạng tin cậy và lý do từ chối giả mạo của lượt xuất hiện. |
| `appearances` | `legal_hold` | `INTEGER NOT NULL DEFAULT 0` | `1`: Khóa bảo vệ tuyệt đối không cho bộ dọn ổ cứng (`< 2 GB` lẫn `retention_days`) tự động xóa clip (tự bật khi `audit_status = 'pending_audit'`, sự kiện `spoof` chưa đóng, hoặc khiếu nại lương; hạ về `0` ngay khi HR đóng hồ sơ). |
| `appearances` | `legal_hold_reason` | `TEXT` | Lý do đang giữ khóa bảo vệ. |
| `appearances` | `clip_sha256` | `TEXT` | Băm SHA-256 của file MP4 gốc sạch. |
| `appearances` | `sidecar_path` | `TEXT` | Đường dẫn tương đối tới tệp `.meta.json` chứa timeline lớp phủ & chữ ký HMAC. |

### 3.3. Bảng mới `audit_logs` (Kiểm toán Truy cập Dữ liệu Sinh trắc học — `K5`)

Ghi nhận mọi thao tác nhạy cảm (`view_clip`, `export_burnin_mp4`, `approve_employee`, `delete_biometrics`, `revoke_checkin`, `manual_checkin`) gồm `ts`, `actor`, `ip_addr`, `action`, `target_type`, `target_id` và `detail_json`.
