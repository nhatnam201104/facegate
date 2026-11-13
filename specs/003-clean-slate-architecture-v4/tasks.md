---

description: "Danh sách tác vụ triển khai chi tiết cho Tái Kiến trúc Toàn diện FaceGate v4 (3 Mặt phẳng Độc lập, Chuẩn hóa cho Tapo C310 & Sẵn sàng Đa cổng / Phase 2 SKU)"
---

# Tasks: Tái Kiến trúc Toàn diện FaceGate v4 (Chuẩn hóa Tapo C310 & Vận hành Máy chủ)

**Input**: Hồ sơ thiết kế tại `/specs/003-clean-slate-architecture-v4/` (`plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/media-and-ipc.md`, `contracts/config-and-hrm-ledger.md`, `quickstart.md`) trên nhánh `003-clean-slate-architecture-v4` xuất phát trực tiếp từ `main` (`7789243`).

**Prerequisites**: `plan.md` (bắt buộc), `spec.md` (4 User Stories: US1 [P1], US2 [P1], US3 [P2], US4 [P2]), `research.md`, `data-model.md`, `contracts/`

**Tests**: Bắt buộc viết kiểm thử tự động (`pytest`) trước khi triển khai ở từng giai đoạn theo **Nguyên tắc V** của `.specify/memory/constitution.md` (*Test-First & Measurable Gates*).

**Organization**: Các tác vụ được phân rã theo lộ trình có cổng đo `K0` → `K0.5` → từng User Story độc lập (`US1`..`US4`) → `K4`/`K5` để mỗi giai đoạn đều kiểm chứng và bàn giao độc lập được ngay từ nền mã nguồn `main`.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Có thể thực thi song song (khác tệp mã nguồn, không phụ thuộc tác vụ chưa hoàn thành trong cùng pha)
- **[Story]**: Nhãn User Story tương ứng (`[US1]`, `[US2]`, `[US3]`, `[US4]`) — chỉ gắn ở các pha User Story (Phase 3 đến Phase 6)
- Mọi tác vụ đều chỉ định đường dẫn tệp chính xác tính từ thư mục gốc dự án

---

## Phase 1: Setup (Hạ tầng Hiện trường Tapo C310, Tải Mô hình Thương mại & Cấu hình Nền tảng)

**Purpose**: Chuẩn bị cấu hình triển khai hạ tầng mạng/media cho **TP-Link Tapo C310**, cập nhật công cụ tải đủ bộ mô hình giấy phép thương mại (Apache-2.0 / MIT) trên nền nhánh `main`, và chuẩn hóa cấu hình mở rộng tương thích ngược.

- [X] T001 [P] Tạo tài liệu cấu hình hạ tầng hiện trường `deploy/vlan-ntp-chrony.md` và mẫu đơn đồng ý dữ liệu sinh trắc học thử nghiệm `deploy/consent-template-k0.md` quy định đúng trình tự bắt buộc cho Tapo C310 (`H11`, `H27`–`H30`): cấu hình `Video Mode = Day Mode` + tạo tài khoản RTSP + tắt auto-update trên Tapo App trước, cắm cáp `RJ45`, không gắn `microSD`, chiếu sáng cổng `≥ 250–300 lux`, bật `chrony` trên Server rồi cô lập VLAN chặn Internet kèm rule `DNAT UDP 123` về IP Server
- [X] T002 [P] Cập nhật `requirements.txt` (bổ sung `cryptography>=42.0` giấy phép Apache-2.0/BSD-3 cho mã hóa sinh trắc học `AES-256-GCM` và ghi chú tùy chọn `psycopg[binary]>=3.1` cho đa cổng) và nâng cấp `scripts/download_models.py` để tải, kiểm tra checksum đầy đủ các mô hình thương mại phục vụ kiến trúc v4 trên nền `main`: `edgeface_xs_gamma_06.onnx` (MIT), `3ddfa_v2_mb1_120x120.onnx` kèm `tddfa_*.bin` (MIT), `yolox_nano_coco.onnx` (Apache-2.0), cùng bộ `YuNet` / `SFace` / `MiniFASNet` sẵn có (tuyệt đối không thêm gói `ultralytics` AGPL-3.0)
- [X] T003 [P] Mở rộng bộ nạp cấu hình tương thích ngược trong `app/config.py` và cập nhật `config.yaml` theo đúng hợp đồng `contracts/config-and-hrm-ledger.md`: nâng khối `quality` mở khóa góc nghiêng từ nền `main` (`max_yaw: 90`, `min_blur: 35`, `max_pitch: 35`, `max_roll: 35`) và bổ sung các khối `camera` (`id`, `model_profile: "tapo_c310"`, `main_source`, `sub_source`, `whep_url`, `playback_url`, `overlay_offset_ms: 0`), `pipeline` (`backend: "edgeface"`, `execution_provider: "cpu"`, `detect_fps: 6`, `max_faces: 10`, `two_stage_roi: true`, `fallback_det_width: 1280`), `decision` (`novelty_min_dt_ms: 250`, `novelty_min_dyaw_deg: 8.0`, `llr_weights`, `tau_threshold`, `margin_base`, `margin_yaw_slope`, `auto_enroll_online: false`), `tracking` (`crossing_face_iou: 0.25`, `crossing_person_iou: 0.40`, `viewport_bottom_grace: 0.80`), `power.active_windows` và `audit_and_safety` (`profile_audit_sample_rate: 0.05`, `near_spoof_window_sec: 300`, `min_free_disk_gb: 2.0`, `heartbeat_timeout_sec: 5.0`)

---

## Phase 2: Foundational — Pha `K0` (Replay & Baseline), Cổng `K0.5` & Hạ tầng Dữ liệu/IPC Dùng Chung

**Purpose**: Xây dựng bộ công cụ đo thực địa `K0` + Replay ngoại tuyến, hoàn tất thực thể giao tiếp chuẩn `TrackedObject`, giao thức IPC Socket và migration cơ sở dữ liệu nền tảng trước khi triển khai các User Story.

**⚠️ CRITICAL**: Phải hoàn tất Pha này (bao gồm biên bản quyết định phạm vi với PM tại cổng `K0.5`) trước khi triển khai nhánh nhận diện sâu ở các pha User Story.

- [X] T004 [P] Tạo mô-đun giao thức và thực thể dùng chung `app/ipc.py` định nghĩa `@dataclass TrackedObject` theo nguyên văn ràng buộc tại `data-model.md`: `camera_id: str` (bắt buộc), `ts_server_ns: int` (bắt buộc, nguồn giờ chuẩn NTP duy nhất từ `time.time_ns()`), `track_id: int` (bắt buộc), `kind: str` thuộc `{"face", "person", "sku"}`, `bbox_norm: tuple[float, float, float, float]` trong miền `[0.0, 1.0]`, `vel_norm: tuple[float, float]`, `state: str` thuộc `{"SEEN", "COLLECTING", "QUARANTINED", "IDENTIFIED", "LIVE_CONFIRMED", "COMMITTED", "REJECTED_SPOOF", "UNKNOWN"}`, `color_role: str` thuộc `{"ok", "checking", "alert", "low_quality"}`, `label: str`, `verification_tier: str | None` thuộc `{"verified", "profile_verified", "rejected_spoof", "unverified", None}`, `meta: dict`, cùng client/server NDJSON bất đồng bộ qua Unix Domain Socket (`data/run/perception.sock` trên Linux) hoặc Localhost TCP (`127.0.0.1:18091` trên Windows)
- [X] T005 [P] Nâng cấp lớp lưu trữ và `_migrate()` tự động tương thích ngược trên nền nhánh `main` trong `app/db.py` theo đúng `data-model.md`: (1) tạo bảng mới `attendance_ledger` với `id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL`, `idempotency_key TEXT UNIQUE NOT NULL`, `action TEXT NOT NULL` thuộc `{"checkin_created", "checkin_revoked", "checkin_manual"}`, `target_ledger_id INTEGER NULL`, `employee_id INTEGER NOT NULL`, `code TEXT NOT NULL`, `work_date TEXT NOT NULL`, `effective_ts REAL NOT NULL`, `verification_tier TEXT NOT NULL` thuộc `{"verified", "profile_verified", "manual_fallback"}`, `audit_status TEXT NOT NULL` thuộc `{"not_required", "pending_audit", "audit_confirmed", "audit_revoked"}`, `actor TEXT NOT NULL`, `reason TEXT NULL`, `event_id INTEGER NULL`, `hrm_sync_status TEXT NOT NULL` thuộc `{"pending", "sent", "failed", "skipped"}`, `remote_doc_id TEXT NULL`, `hrm_attempts INTEGER DEFAULT 0`, `hrm_last_error TEXT NULL`, `created_at REAL NOT NULL`; (2) migrate bổ sung các cột thiếu so với `main` gồm `pose_group TEXT NOT NULL DEFAULT 'frontal'`, `yaw REAL`, `pitch REAL` trên `embeddings`, `verification_tier TEXT`, `pose_group TEXT`, `yaw REAL`, `liveness_mode TEXT`, `liveness_detail TEXT` trên `events`, và `pose_group TEXT`, `min_abs_yaw REAL`, `verification_tier TEXT`, `spoof_reason TEXT`, `legal_hold INTEGER NOT NULL DEFAULT 0`, `legal_hold_reason TEXT`, `clip_sha256 TEXT`, `sidecar_path TEXT` trên `appearances`; (3) tạo bảng kiểm toán `audit_logs`
- [X] T006 [P] Xây dựng công cụ thu thập gói dữ liệu thực địa `scripts/record_k0_bundle.py` và công cụ đánh giá ngoại tuyến `scripts/replay_eval.py` (`H1`, `H12`–`H14`, `H26`, `H32`): (1) chạy tuần tự không rớt frame trên video thực địa Tapo C310 để đo Baseline hệ thống hiện tại; (2) xuất gói Replay (`manifest.json` ghi GOP đo bằng `ffprobe`, độ rọi lux, bố cục góc quay + `observations.jsonl` + thư mục `crops/` lưu ảnh cắt Đầu–Vai PNG/JPEG-95); (3) tính toán hai tầng FAR (ghép chéo cặp embedding > 2.500 cặp khác người + FAR sự kiện kèm cận trên khoảng tin cậy 95% Wilson / `3/n`), FRR theo góc, chỉ số `APCER` riêng từng loại PAI (≥ 20 lượt) và `BPCER` theo chuẩn **ISO/IEC 30107-3**, cùng phân phối Laplacian blur và chi phí CPU giải mã `/stream1` 3MP@15fps
- [X] T007 [P] Tạo bài kiểm thử nền tảng `tests/test_foundation_v4.py` xác minh: tính tuần tự hóa NDJSON của `TrackedObject` qua IPC Socket, migration tự động `_migrate()` trên DB gốc `main` bổ sung đủ cột cho cả `embeddings`/`events`/`appearances` không làm mất dữ liệu hiện hữu, ràng buộc `UNIQUE NOT NULL` của `idempotency_key` trong `attendance_ledger` và tính toàn vẹn của trình đọc/ghi gói Replay `observations.jsonl`
- [X] T008 Tổng hợp kết quả đo thực tế của 2 bố cục ứng viên trên Tapo C310 từ `scripts/replay_eval.py` vào báo cáo cổng quyết định `docs/BAO_CAO_K0_5_PM.md` để trình PM/Pháp chế ký chốt 4 câu hỏi chiến lược (`H11` consent, `H2` đi thẳng vs. đi ngang, `H6+H31` chọn phần cứng `A / B / C / C'`, `H9` quy chế clip bằng chứng HMAC) trước khi kích hoạt nhánh mở rộng tương ứng

**Checkpoint**: Đã có số liệu Baseline thực địa trên Tapo C310, công cụ Replay ngoại tuyến, biên bản chốt `K0.5` và nền tảng DB/IPC. *(Quy tắc rẽ nhánh theo quyết định `K0.5`: nếu PM chọn `C`, `C'` hoặc `B` thì thực thi đầy đủ `Phase 3`–`Phase 7`; nếu PM chọn `A` — đầu đọc RGB+IR phần cứng — thì giữ nguyên `Phase 3` + `Phase 5` + `Phase 7` và thay nhánh góc nghiêng `T016`–`T019` của `Phase 4` bằng adapter nhận webhook sự kiện ISAPI).*

---

## Phase 3: User Story 1 - Xem trực tiếp độ trễ cực thấp & Ghi clip bằng chứng gốc không tốn CPU nén lại trên phần cứng Tapo C310 (Priority: P1) 🎯 MVP

**Goal**: Đưa `MediaMTX` vào làm Single RTSP Puller cho Tapo C310, loại bỏ hoàn toàn track âm thanh `pcm_alaw` (`-an`), phát trực tiếp H.264 qua WebRTC WHEP kết hợp vẽ hộp tiếng Việt trên HTML5 `<canvas>` đồng bộ khung hình (`p90 ≤ 2 frame`), và trích xuất clip lịch sử MP4 gốc sạch từ vòng đệm `tmpfs` RAM kèm sidecar ký `HMAC-SHA256` + xuất MP4 burn-in FFmpeg theo yêu cầu.

**Independent Test**: Mở 5 tab trình duyệt xem trực tiếp luồng Tapo C310 qua WebRTC WHEP + `<canvas>` cùng màn hình chạy đồng hồ mili-giây; xác nhận đạt `13–15 fps` (`≥ 90%` nguồn), độ trễ `p90 < 300 ms`, lệch khung bao `p90 ≤ 2 frame`, mức tăng CPU `< 10` điểm %, và file clip xuất ra chứa `0` track âm thanh, khớp chữ ký `HMAC-SHA256` cũng như xuất được bản MP4 burn-in.

### Tests for User Story 1 ⚠️

> **NOTE: Viết các bài kiểm thử này TRƯỚC, xác nhận FAIL trước khi viết code triển khai**

- [X] T009 [P] [US1] Tạo bài kiểm thử `tests/test_media_plane_clips.py` kiểm chứng: (1) cấu hình `deploy/mediamtx.yml` chứa đầy đủ cờ `-map 0:v:0 -c:v copy -an`, `runOnInitRestart: yes`, `recordFormat: fmp4` và `recordDeleteAfter: 10m`; (2) dịch vụ cắt clip từ MediaMTX Playback API tính đúng `clip_sha256`, `event_offset_ms`, `audio_stripped: true` và xác thực chữ ký `HMAC-SHA256` trong `.meta.json` (phát hiện ngay nếu sửa 1 byte trong file `.mp4` hoặc timeline); (3) endpoint `POST /api/appearances/{id}/export-burnin` kết xuất thành công file MP4 đóng dấu bằng FFmpeg

### Implementation for User Story 1

- [X] T010 [P] [US1] Tạo tệp cấu hình `deploy/mediamtx.yml` (sử dụng biến môi trường `${FACEGATE_RING_DIR}` trỏ vào `/dev/shm/facegate_ring` trên Linux hoặc `%TEMP%/facegate_ring` trên Windows dev) cùng cặp script khởi chạy kèm giám sát đa nền tảng `scripts/run_mediamtx.sh` và `scripts/run_mediamtx.bat` cho 2 đường dẫn `gate1_main` (`/stream1` 3MP, bật `record: yes` fMP4 1 s xóa sau 10 phút) và `gate1_sub` (`/stream2` 360p, `record: no`), cả hai đều lọc bỏ audio `pcm_alaw` bằng `ffmpeg -rtsp_transport tcp ... -map 0:v:0 -c:v copy -an` theo `contracts/media-and-ipc.md`
- [X] T011 [P] [US1] Xây dựng mô-đun `app/media_clips.py` thay thế cơ chế mã hóa lại VP8 CPU cũ: gọi HTTP `GET /get?path=gate1_main&start=...&duration=...` của MediaMTX Playback Server để lưu thẳng file `.mp4` H.264 gốc sạch vào `data/clips/YYYYMMDD/`, băm `clip_sha256`, xuất tệp sidecar `.meta.json` chứa mảng `timeline` các `TrackedObject` kèm chữ ký `HMAC-SHA256`, và cung cấp hàm `render_burnin_mp4(appearance_id)` vẽ hộp + nhãn tiếng Việt + dấu thời gian server bằng FFmpeg theo yêu cầu
- [X] T012 [P] [US1] Tạo mô-đun trình duyệt `app/static/whep_canvas.js` và cập nhật `app/static/index.html`, `app/static/history.js` (giữ HTML/CSS/JS thuần không build step): kết nối WebRTC WHEP tới `whep_url`, chồng lớp `<canvas>` độ phân giải cao lên `<video>`, đồng bộ khung bao theo từng frame bằng `video.requestVideoFrameCallback` (`metadata.receiveTime + overlay_offset_ms`) kèm nội suy tuyến tính vận tốc `vel_norm` kẹp biên `|dt| <= 120 ms` và tự ẩn hộp khi quá `400 ms` không nhận gói mới; cập nhật trình phát ở tab Lịch sử để vẽ lại lớp phủ trực tiếp từ `.meta.json` trên video MP4 sạch
- [X] T013 [US1] Cập nhật `app/main.py` và `app/records.py` để chuyển tiếp gói tin `frame_overlay` qua WebSocket `/ws` kèm `ws_send_mono_ms`, bổ sung endpoint `POST /api/appearances/{appearance_id}/export-burnin` (kiểm tra chữ ký HMAC trước khi xuất), phục vụ tệp `.meta.json` và tự động ngắt kết nối WHEP trên `app/static/app.js` khi người dùng chuyển khỏi tab Trực tiếp hoặc ẩn cửa sổ trình duyệt

**Checkpoint**: User Story 1 hoàn tất — hệ thống đạt chuẩn xem trực tiếp WebRTC `< 300 ms` và lưu clip bằng chứng H.264 gốc có chữ ký số với `~0%` CPU mã hóa lại trên Tapo C310.

---

## Phase 4: User Story 2 - Nhận diện Open-Set liên tục tới ±90°, Chống tráo người đồng phục & Cô lập lỗi Process trên dòng 3MP 15fps (Priority: P1)

**Goal**: Đưa toàn bộ luồng nhận diện sang tiến trình riêng `app/worker_perception.py` cô lập lỗi, tích hợp `EdgeFace-XS` + `3DDFA_V2` + `YOLOX-Nano` sạch trên nhánh mới, thực thi Lịch thức theo ca (`0 ms` chờ GOP) + cắt vùng Đầu–Vai tỷ lệ 1:1 ngay trên cùng khung `/stream1` 3MP (`Δt = 0 ms`), thay ngưỡng cắt cứng 40° bằng máy trạng thái LLR liên tục 4 tham số giữ rào cứng `threshold + margin μ(yaw)`, và kích hoạt `Crossing Quarantine` + `Viewport-Aware Context`.

**Independent Test**: Chạy `scripts/replay_eval.py` trên gói dữ liệu thực địa `K0` xác nhận cận trên FAR không vượt baseline, `0` ca chốt nhầm tên khi 2 người mặc đồng phục đi giao cắt, không phạt oan `unattached_face` khi đứng sát cắt vai ở đáy ảnh, và thử `kill -9` tiến trình `worker_perception.py` xác nhận Web API vẫn hoạt động 100% và tự khôi phục worker.

### Tests for User Story 2 ⚠️

- [X] T014 [P] [US2] Tạo bài kiểm thử `tests/test_decision_llr.py` kiểm chứng máy trạng thái `TrackDecisionState`: (1) cổng `Novelty Gate` bỏ qua các frame đứng yên có `Δt < 250 ms` và `|Δyaw| < 8°`; (2) hàm `LLR = a·s + b + c·|yaw| + d·q` liên tục không nhảy bậc ở góc 40°; (3) rào cứng kép bắt buộc vượt đồng thời `LLR(top1) >= tau_threshold` VÀ `LLR(top1) - LLR(top2) >= margin_base + margin_yaw_slope * |yaw| / 90°` theo đúng Nguyên tắc I của Constitution
- [X] T015 [P] [US2] Tạo bài kiểm thử `tests/test_crossing_quarantine.py` và `tests/test_ipc_fault_isolation.py` kiểm chứng: (1) khi 2 hộp theo dõi giao cắt (`IoU > 0.25` mặt hoặc `IoU > 0.40` người), toàn bộ frame lúc giao cắt bị loại bỏ, trạng thái chuyển sang `"QUARANTINED"` cấm phát `checkin`, và sau khi tách ra bắt buộc thu thập vượt lại từ đầu đủ ngưỡng Open-Set; (2) hộp mặt có `y2 >= 0.80 * H` được miễn lỗi `unattached_face`; (3) cắt ROI Đầu–Vai 1:1 trên khung giả lập `2304×1296` giữ nguyên kích thước pixel gốc không bị thu về `960 px`; (4) dừng đột ngột tiến trình `worker_perception` không làm lỗi `GET /api/status` hay `GET /api/attendance`

### Implementation for User Story 2

- [X] T016 [P] [US2] Bổ sung `EdgeFaceEngine` (`edgeface_xs_gamma_06.onnx`, MIT) kèm căn chỉnh hình học theo góc nhìn trong `app/engines.py`, tạo mô-đun ước lượng tư thế 3D `app/pose3d.py` (`3DDFA_V2` ONNX), nâng cấp `app/quality.py` để nhận `PoseResult` 3D mở tới `max_yaw = 90°` và chỉ đo độ sáng/phương sai Laplacian trên vùng da mặt thực sự nhìn thấy ở góc nghiêng, đồng thời tạo mô-đun ngữ cảnh `app/context_detector.py` (`YOLOX-Nano` ONNX có quy tắc `Viewport-Aware`: miễn trừ `unattached_face` khi đáy hộp mặt `y2 >= viewport_bottom_grace * H` hoặc chồng lên hộp người đứng trước ở tiền cảnh)
- [X] T017 [P] [US2] Xây dựng mô-đun lõi quyết định `app/decision.py` cài đặt máy trạng thái hữu hạn `TrackDecisionState` (`SEEN → COLLECTING → QUARANTINED → IDENTIFIED → LIVE_CONFIRMED → COMMITTED / REJECTED_SPOOF / UNKNOWN`), cổng khử tương quan `Novelty Gate`, bộ tích lũy `LLR` liên tục 4 tham số kèm công cụ fit tham số hồi quy logistic có ràng buộc `a_cos > 0` từ cặp ghép chéo của tập `K0`, và rào cứng `tau_threshold` + `margin μ(yaw)`
- [X] T018 [US2] Nâng cấp `app/tracker.py` và `app/gallery.py` hỗ trợ cầu nối `person_bbox` tạm thời khi xoay đầu sâu, cơ chế **Crossing Quarantine** nghiêm ngặt (xóa mẫu pha giao cắt, khóa `quarantine = True`, buộc xác thực lại đầy đủ sau khi tách nhau) và truy vấn đồng thời điểm tương đồng `top1`, `top2` từ ma trận `numpy` trong RAM để cấp đầu vào cho `app/decision.py`
- [X] T019 [US2] Xây dựng điểm vào tiến trình suy luận độc lập `app/worker_perception.py` kết nối tới `MediaMTX` localhost: thực thi **Lịch thức theo ca** (`power.active_windows` giữ nóng bộ giải mã `/stream1` `2304×1296` để đạt `0 ms` chờ GOP vào giờ chấm công; ngoài giờ chỉ canh chuyển động trên `/stream2` `640×360` với vùng đánh thức mở rộng), khi thức thực hiện `cv2.resize` chính khung `/stream1` về `640×360` tìm hộp người rồi **cắt ROI Đầu–Vai 1:1 ngay trên cùng khung `/stream1` đó** (`Δt = 0 ms`), và phát luồng `frame_overlay`, `track_event`, `heartbeat` qua `app/ipc.py`
- [X] T020 [US2] Cập nhật `app/main.py` và `run.sh` / `run.bat` để quản lý vòng đời tiến trình con `worker_perception.py` (tự động khởi động lại khi tiến trình con thoát bất thường), nhận sự kiện qua IPC Socket đẩy vào DB / `media_clips.py` và gửi lệnh `{"cmd": "reload_gallery"}` không khóa khi danh sách nhân viên thay đổi

**Checkpoint**: User Story 2 hoàn tất — nhận diện trên luồng 3MP 15fps của Tapo C310 chính xác, không nhảy ngăn góc 40°, chống tráo tên đồng phục và cô lập hoàn toàn sự cố tiến trình AI.

---

## Phase 5: User Story 3 - Sổ cái Chấm công Bất biến (Append-Only Ledger), Hậu kiểm Ngoại lệ & Bảo vệ Clip Khiếu nại (Priority: P2)

**Goal**: Chuyển toàn bộ nghiệp vụ ghi nhận/sửa chữa chấm công sang sổ cái bất biến `attendance_ledger`, triển khai quy trình hậu kiểm ngoại lệ 5% (`Audit-by-Exception`), đồng bộ cả tạo mới lẫn hủy chứng từ sang ERPNext có `idempotency_key`, bảo vệ tuyệt đối các clip `legal_hold = 1` khi dọn ổ cứng `< 2 GB`, và cung cấp kênh chấm công dự phòng kèm cảnh báo `/api/health`.

**Independent Test**: Phát sinh sự kiện check-in tự động đẩy sang HRM, bật cờ `legal_hold`, giả lập ổ cứng trống `< 2 GB` chạy hàm dọn dẹp, sau đó gọi `POST /api/attendance/revoke` và `POST /api/attendance/manual`; xác nhận clip `legal_hold` không bị xóa, bảng công suy dẫn chính xác từ sổ cái và chứng từ ERPNext được hủy idempotent không sinh bản ghi trùng.

### Tests for User Story 3 ⚠️

- [X] T021 [P] [US3] Tạo bài kiểm thử `tests/test_ledger_and_hold.py` kiểm chứng: (1) tính bất biến của `attendance_ledger` qua chuỗi `checkin_created → checkin_revoked → checkin_manual` và chống ghi trùng theo `idempotency_key`; (2) luật phân loại `pending_audit` cho ca sát biên xác suất, ca nằm trong `near_spoof_window_sec = 300` quanh sự kiện `spoof` và lấy mẫu ngẫu nhiên `5%`; (3) bộ dọn đĩa khi dung lượng trống `< 2.0 GB` hoặc quá `retention_days` tuyệt đối bỏ qua mọi bản ghi có `legal_hold = 1`, và tự hạ `legal_hold = 0` khi HR đóng hồ sơ audit; (4) `ERPNextHRM` lưu đúng `remote_doc_id` khi `POST` và gọi đúng lệnh hủy khi `checkin_revoked`

### Implementation for User Story 3

- [X] T022 [P] [US3] Xây dựng mô-đun nghiệp vụ sổ cái `app/ledger.py` và nâng cấp `app/attendance.py`: suy dẫn bảng công ngày/tuần/tháng/CSV trực tiếp từ `attendance_ledger`, tự động đánh dấu `audit_status = "pending_audit"` (đồng thời bật `legal_hold = 1` trên `appearances` liên quan) theo bộ quy tắc **Audit-by-Exception**, và hạ `legal_hold = 0` ngay khi HR xác nhận hoặc đóng khiếu nại
- [X] T023 [P] [US3] Nâng cấp `app/hrm.py` hỗ trợ đồng bộ hai chiều với Outbox: đính kèm `idempotency_key` chống tạo trùng khi retry mạng, trích xuất và lưu mã chứng từ `name` từ JSON phản hồi của ERPNext vào cột `remote_doc_id`, và thực thi hủy chứng từ `DELETE /api/resource/Employee%20Checkin/{remote_doc_id}` khi xử lý bút toán `action = "checkin_revoked"`
- [X] T024 [US3] Cài đặt bộ giám sát sức khỏe & dọn đĩa an toàn trong `app/media_clips.py` và bổ sung các endpoint REST trong `app/records.py` & `app/main.py` theo `contracts/config-and-hrm-ledger.md`: `POST /api/attendance/revoke`, `POST /api/attendance/manual`, `GET /api/attendance/audit-queue`, `POST /api/appearances/{id}/legal-hold` và `GET /api/health` (báo động trong `≤ 5 s` khi mất kết nối camera, chết `ffmpeg` ingest, mất heartbeat `worker_perception` hoặc ổ cứng trống `< 2 GB`)
- [X] T025 [US3] Cập nhật giao diện quản trị trong `app/static/attendance.js`, `app/static/history.js` và `app/static/app.js`: bổ sung bộ lọc hàng chờ Hậu kiểm ngoại lệ (`pending_audit`), nút Thu hồi công / Nhập bù công dự phòng (bắt buộc nhập lý do), công tắc khóa bảo vệ bằng chứng `legal_hold` và thanh cảnh báo đỏ Fail-safe trên đầu màn hình khi `/api/health` báo lỗi

**Checkpoint**: User Story 3 hoàn tất — chu trình tính lương, đối soát khiếu nại, bảo vệ bằng chứng và dự phòng sự cố lúc vào ca khép kín hai chiều với HRM.

---

## Phase 6: User Story 4 - Đăng ký Khuôn mặt Phản hồi Gương Tức thời (Client WASM) & Lấy Mẫu Chuẩn Miền Camera Cổng (Priority: P2)

**Goal**: Mang lại trải nghiệm soi gương hướng dẫn đa góc mượt tức thì (`0 ms` trễ mạng) ngay trên trình duyệt bằng `MediaPipe Face Landmarker` WASM (Apache-2.0), trong khi ảnh gốc dùng để trích xuất vector lưu DB bắt buộc chụp từ chính luồng 3MP của camera cổng trên session suy luận riêng (không khóa cổng chấm công) và cấm tuyệt đối tự học mẫu online.

**Independent Test**: Mở tab Đăng ký thực hiện phiên quay hướng dẫn 7 góc trong lúc có người khác đi qua cổng chấm công; kiểm tra giao diện phản hồi góc đầu mượt tức thời, chu kỳ nhận diện tại cổng không bị chậm đi, ảnh mẫu trong `data/enroll/` có nguồn từ camera cổng và nhân viên ở trạng thái `pending` chờ HR bấm Duyệt.

### Tests for User Story 4 ⚠️

- [X] T026 [P] [US4] Mở rộng `tests/test_guided.py` kiểm chứng: (1) luồng xử lý đăng ký 7 góc (5 góc chữ thập + 2 góc nghiêng sườn `~80°`) và chế độ bổ sung mẫu `supplement_only` chạy trên instance engine riêng hoàn toàn không chạm tới `worker_perception`; (2) từ chối mọi yêu cầu tự động thêm mẫu từ luồng cổng khi `decision.auto_enroll_online == False`; (3) kiểm tra chéo danh tính giữa góc nghiêng sâu và tâm chính diện trước khi lưu vào bảng `embeddings` kèm `pose_group`

### Implementation for User Story 4

- [X] T027 [P] [US4] Tạo mô-đun trình duyệt `app/static/guided_wasm.js` (sử dụng gói `MediaPipe Tasks Vision` WASM giấy phép Apache-2.0 lưu cục bộ trong `app/static/vendor/` để không phụ thuộc Internet khi vận hành VLAN nội bộ) tính toán tức thời góc `yaw`, `pitch`, `roll` trên luồng video xem trước để vẽ vòng cung hướng dẫn 7 bước mượt 60 FPS tại client và phát tín hiệu yêu cầu Server chụp khung gốc mỗi khi người dùng giữ đúng góc
- [X] T028 [US4] Mở rộng `app/guided.py` trên nền `main` (bổ sung 2 bước góc nghiêng sườn `~80°` nâng lên 7 bước + chế độ cập nhật mẫu `supplement_only` kèm bước xác minh chính diện), nâng cấp `app/enrollment.py` (lưu `pose_group`, `yaw`, `pitch` và kiểm tra chéo với tâm chính diện) cùng các endpoint đăng ký trong `app/main.py`: chụp khung hình gốc trực tiếp từ luồng chuẩn `gate1_main` (cùng miền quang học với cổng chấm công), trích xuất đặc trưng trên session ONNX ưu tiên thấp riêng của tiến trình API (không khóa cổng), hỗ trợ `backfill` tự động từ `data/enroll/` khi nâng cấp `model_version` và giữ nguyên quy tắc bắt buộc HR bấm Duyệt (`pending → active`)

**Checkpoint**: User Story 4 hoàn tất — đăng ký khuôn mặt đa góc phản hồi tức thì, đồng nhất miền quang học với camera cổng và không gây nghẽn luồng trực chấm công.

---

## Phase 7: Polish & Cross-Cutting Concerns (`K4` Runtime 10 Khuôn mặt & `K5` Bảo mật RBAC, Mã hóa Sinh trắc học, Đa cổng)

**Purpose**: Tối ưu hiệu năng tổng thể để đạt ngân sách `< 200 ms` ở tải 10 người đồng thời trên luồng 3MP, khóa chặt bảo mật sinh trắc học trước khi mở ra mạng LAN nội bộ (`0.0.0.0`), và nghiệm thu đầu-cuối toàn hệ thống.

- [X] T029 [P] Nâng cấp `app/worker_perception.py` và `scripts/benchmark.py` cho pha `K4`: hỗ trợ tùy chọn giải mã phần cứng (`VAAPI`/`QSV` qua FFmpeg/OpenCV) cho luồng `/stream1` 3MP@15fps, hỗ trợ chuyển đổi cấu hình `pipeline.execution_provider: "openvino"` (FP32/FP16; giữ nguyên FP32 cho `EdgeFace-XS` và `3DDFA_V2`), gom batch suy luận khi có 3–10 khuôn mặt đồng thời và kiểm định lại toàn bộ chỉ số FAR/FRR/APCER/BPCER trên `scripts/replay_eval.py`
- [X] T030 [P] Xây dựng mô-đun bảo mật `app/security.py` và tích hợp vào `app/main.py`, `app/db.py` cho pha `K5` trước khi mở `server.host: 0.0.0.0`: (1) xác thực đăng nhập phiên/JWT với 3 vai trò RBAC (`viewer` màn hình cổng chỉ xem thông báo chào, `hr` duyệt nhân viên / đối soát bảng công / thu hồi công, `admin` cấu hình hệ thống); (2) mã hóa `AES-256-GCM` ở trạng thái nghỉ cho các vector `embeddings.vec` và ảnh mẫu trong `data/enroll/` bằng khóa nạp từ biến môi trường; (3) ghi vết bắt buộc vào bảng `audit_logs` cho mọi thao tác xem/xuất clip, duyệt/xóa sinh trắc học và chỉnh sửa công
- [X] T031 [P] Bổ sung adapter kết nối `PostgreSQL` tùy chọn trong `app/db.py` (kích hoạt qua cấu hình `storage.database_url` khi triển khai gom từ `≥ 2` cổng camera về chung 1 máy chủ, giữ mặc định `SQLite WAL` cho 1 cổng) và cập nhật tài liệu vận hành tổng thể tại `README.md` cùng `docs/HUONG_DAN_KY_THUAT.html`
- [X] T032 Chạy kiểm định tổng thể toàn bộ danh mục nghiệm thu tại `specs/003-clean-slate-architecture-v4/quickstart.md`: `.venv/bin/pytest -v`, kiểm tra `0` audio stream bằng `ffprobe`, kiểm tra lệch khung `<canvas>` `p90 ≤ 2 frame`, kiểm tra tải 10 mặt `< 200 ms` và đối chiếu tuân thủ 5 Nguyên tắc trong `.specify/memory/constitution.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 — Setup (`T001`–`T003`)**: Khởi chạy ngay lập tức trên nhánh `003-clean-slate-architecture-v4`, cả 3 tác vụ đều độc lập `[P]`.
- **Phase 2 — Foundational & Gate `K0`/`K0.5` (`T004`–`T008`)**: Phụ thuộc Phase 1 — **CHẶN** việc chốt nhánh phần cứng cho các pha sau cho tới khi hoàn tất đo thực địa `K0` (`T006`) và ký biên bản PM `K0.5` (`T008`).
- **Phase 3 (`US1` — Media Plane) & Phase 4 (`US2` — Perception Process)**: Sau khi hoàn tất `T004`–`T008`, **Phase 3 (`US1` — tương ứng `K1`) và Phase 4 (`US2` — tương ứng `K2`+`K3`) hoàn toàn độc lập về mặt giao diện qua `TrackedObject` IPC nên có thể triển khai song song nếu có 2 kỹ sư**.
- **Phase 5 (`US3` — Append-Only HRM Ledger)**: Phụ thuộc bảng `attendance_ledger` ở `T005`; có thể triển khai song song với cuối Phase 4 và khớp nối clip `legal_hold` với `T011` của `US1`.
- **Phase 6 (`US4` — Đăng ký WASM & Camera Cổng)**: Phụ thuộc `T010` (luồng chuẩn `gate1_main` từ `MediaMTX`) và `T016` (`EdgeFaceEngine` + `pose3d.py`).
- **Phase 7 — Polish, `K4` & `K5` (`T029`–`T032`)**: Thực hiện sau khi hoàn tất các User Story mục tiêu và trước khi mở cổng truy cập mạng nội bộ `0.0.0.0`.

### User Story Dependencies

- **User Story 1 (P1 — Media Plane `K1`)**: Bắt đầu ngay sau Foundational — không phụ thuộc logic AI mới (có thể phát thử `TrackedObject` giả lập để nghiệm thu WebRTC `<canvas>` và cắt clip fMP4).
- **User Story 2 (P1 — Perception & Tracking `K2`–`K3`)**: Bắt đầu ngay sau Foundational — kiểm thử độc lập 100% qua bộ Replay `K0` (`scripts/replay_eval.py`) ngay cả khi chưa bật trình duyệt.
- **User Story 3 (P2 — Sổ cái Chấm công & HRM 2 chiều)**: Phụ thuộc lược đồ `T005` — kiểm thử độc lập toàn bộ luồng tạo/thu hồi/nhập bù công và khóa `legal_hold` qua REST API.
- **User Story 4 (P2 — Đăng ký Gương WASM)**: Tích hợp nhẹ với luồng chụp khung gốc `gate1_main` của `US1` và bộ trích đặc trưng của `US2`, kiểm thử độc lập trên tab Đăng ký.

---

## Parallel Opportunities & Examples

### Ví dụ chạy song song trong Phase 2 (Foundational)
```bash
# Chạy đồng thời 3 tác vụ độc lập khác tệp:
Task T004: "Xây dựng thực thể TrackedObject và NDJSON IPC Socket trong app/ipc.py"
Task T005: "Bổ sung migration bảng attendance_ledger, audit_logs và cột legal_hold trong app/db.py"
Task T006: "Xây dựng công cụ thu thập K0 và đánh giá Replay ngoại tuyến trong scripts/record_k0_bundle.py & scripts/replay_eval.py"
```

### Ví dụ chạy song song giữa 2 kỹ sư sau Cổng `K0.5` (US1 ∥ US2)
```bash
# Kỹ sư A — Triển khai Media Plane (US1):
Task T010: "Cấu hình deploy/mediamtx.yml (-c:v copy -an, WHEP, tmpfs fMP4)"
Task T011: "Xây dựng app/media_clips.py cắt MP4 sạch + ký HMAC sidecar + xuất burn-in FFmpeg"
Task T012: "Xây dựng app/static/whep_canvas.js đồng bộ requestVideoFrameCallback"

# Kỹ sư B — Triển khai Perception Process & Lõi Quyết định (US2):
Task T016: "Tích hợp EdgeFaceEngine, app/pose3d.py và Viewport-Aware app/context_detector.py"
Task T017: "Xây dựng máy trạng thái LLR liên tục & Novelty Gate trong app/decision.py"
Task T019: "Xây dựng tiến trình độc lập app/worker_perception.py (Lịch thức ca + ROI 1:1 3MP)"
```

---

## Implementation Strategy

### 1. Cổng Thực chứng Trước Tiên (`K0` → `K0.5`)
1. Hoàn thành Phase 1 (`T001`–`T003`) và công cụ đo Phase 2 (`T004`–`T007`).
2. Lắp thử chiếc **Tapo C310** theo đúng quy trình `RJ45 + Day Mode + đèn ≥ 250–300 lux + VLAN NTP`, thu dữ liệu 2 góc lắp và họp PM chốt `T008` (`K0.5`).
   - *Lưu ý tiết kiệm công sức*: Nếu tại `K0.5` PM chọn **Phương án A** (đầu đọc RGB+IR phần cứng) hoặc chốt **bố trí lối đi nhìn thẳng ±40°**, khối lượng tinh chỉnh góc nghiêng sâu ở `US2` (`T018`) được rút gọn tối đa mà vẫn giữ nguyên lợi ích của `US1` (Media Plane) và `US3` (Sổ cái HRM 2 chiều).

### 2. Lát cắt MVP Đầu tiên (`US1` + `US2` — Vận hành Cổng Thời gian thực Chuẩn Server)
1. Hoàn thành Phase 3 (`US1`) → Xác nhận xem trực tiếp 13–15 fps mượt, 0% CPU re-encode, 0 byte âm thanh, cắt clip gốc chuẩn.
2. Hoàn thành Phase 4 (`US2`) → Xác nhận nhận diện trên ROI 1:1 khung 3MP vượt baseline trên bộ Replay `K0`, cô lập hoàn toàn sự cố process.

### 3. Hoàn thiện Chu trình Tính Lương & Mở rộng Sản xuất (`US3` → `US4` → Phase 7)
1. Bổ sung Phase 5 (`US3`) để HR có công cụ hậu kiểm 5%, thu hồi/nhập bù công đồng bộ ERPNext và bảo vệ clip khiếu nại `legal_hold`.
2. Bổ sung Phase 6 (`US4`) cho trải nghiệm đăng ký soi gương WASM 0 ms và khóa bảo mật RBAC / mã hóa `AES-256-GCM` ở Phase 7 trước khi mở truy cập toàn mạng nội bộ.
