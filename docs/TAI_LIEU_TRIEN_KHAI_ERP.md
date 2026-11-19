# Tài liệu Chuẩn bị Triển khai ERP & Mở rộng Nền tảng Camera AI (HRM + Sản xuất)

**Phiên bản**: 1.1 (Hợp nhất sau phản biện: Camera-Agnostic, 2 Cam Cổng, Tách tải & Siết bảo mật)  
**Ngày lập**: 2026-11-18  
**Cơ sở đối chiếu**: Repo `msoftware-ai-camera-attendance` (Kiến trúc FaceGate v4, 73/73 tests pass) vs Điều kiện triển khai thực địa (Server Linux CPU hiện có + Camera IP RTSP bất kỳ từ phổ thông/kém hơn đến 1080p/3MP).

---

## 1. Tóm tắt Điều hành (Executive Summary)

Mã nguồn hiện tại trong repository (`server/`, `webapp/`, `deploy/`) đã hoàn thiện vững chắc phần **lõi nhận diện khuôn mặt đa góc (`~±60°`, sau feature 002), sổ cái chấm công bất biến, bảo mật sinh trắc học (`AES-256-GCM`, `RBAC`, `HMAC-SHA256`), Media Plane (`MediaMTX` + `WebRTC WHEP`)** và có sẵn module `server/vision/sku_scanner.py` (QR/Barcode + ORB/SIFT RANSAC).

Qua phản biện kỹ thuật đối chiếu với điều kiện triển khai thực tế, tài liệu v1.1 chuẩn hóa kiến trúc theo **4 nguyên tắc sống còn** trước khi Go-Live ERPNext:
1. **Camera-Agnostic (Không phụ thuộc 1 model camera đặc thù)**: Hệ thống phải chạy ổn định cả trên camera kém hơn (720p, 10–15 FPS, nén mạnh, 1 stream duy nhất hoặc H.265) thông qua `Auto-Probe (ffprobe)` + `Capability Tier`, dùng ngưỡng thời gian thực (giây/ms) thay cho đếm frame cứng, và **tuyệt đối không hạ ngưỡng nhận diện LLR khi ảnh xấu**.
2. **Chuẩn hóa Kịch bản 2 Camera Cổng (`hrm_in` / `hrm_out`)**: Gán vai trò theo hướng cổng (loại bỏ `auto_alternate` rất dễ lệch pha tính lương), đặt **Global Cooldown** tại `main.py` chống trùng chéo giữa 2 cam và ánh xạ bắt buộc về `erp_employee_id` (`HR-EMP-xxxx`).
3. **Kiểm soát CPU Thực chất & Đo Full-Pipeline**: Không dùng số đo đơn lẻ của bước embed (`23.5 ms`, đo thời EdgeFace) làm ngân sách tải; giữ bước embed `SFace` lấy mẫu chu kỳ nhẹ để bảo vệ `split_cosine`, giảm fallback `YuNet` toàn khung, giới hạn thread ONNX và **tách Pha 2 (SKU/Zone xưởng — tắt FaceID nặng)** sau khi Pha 1 đạt cổng nghiệm thu.
4. **Khép kín lỗ hổng Bảo mật RBAC**: Băm mật khẩu user (bỏ plain-text tại `main.py:485`), khóa `require_role` cho `/files/*`, `/video*`, `/ws`, `/api/employees`, `/api/appearances*`.

---

## 2. Phân tích Khoảng trống (Gap Analysis) Cần Giải quyết Trước khi Go-Live ERP

### 2.1. Nhóm Khoảng trống chưa có trong Repo (Gaps — Must Build)

| Mã | Hạng mục chưa có | Tác động nếu thiếu khi triển khai ERP | Giải pháp & Thiết kế triển khai |
|---|---|---|---|
| **GAP-01** | **Chấm công Ra/Vào (IN/OUT), 2 Cam Cổng & Ca đêm** (`server/attendance/ledger.py`, `shift.py`, `pipeline.py:460`) | Hiện hệ thống khóa cứng `1 lần IN/ngày` và chặn `if d == "OUT": d = None`. Không thể tính giờ làm, tăng ca hoặc ca đêm trên ERPNext. | Mở rộng `attendance_ledger` hỗ trợ `IN`/`OUT`/`RAW` theo `shift_date`. Chuẩn hóa 2 cam cổng `hrm_in`/`hrm_out` (hoặc `line` 2 chiều / raw timestamp cho ERPNext Shift). **Bỏ `auto_alternate`**; đặt **Global Cooldown** tại `main.py` theo `(employee_id, log_type)`. |
| **GAP-02** | **Đồng bộ Danh mục & Map Bắt buộc `erp_employee_id`** | Gửi `ev["code"]` (`NV001`) trực tiếp sẽ bị ERPNext trả HTTP 417 nếu `Employee.name` là `HR-EMP-00001`; người nghỉ việc (`Left`) vẫn nhận diện được ở cổng. | Xây `server/attendance/erp_sync.py` kéo `Employee` & `Item` từ ERPNext REST API; map bắt buộc `erp_employee_id`; chặn đẩy ERP nếu thiếu mapping; tự `disable` sinh trắc học khi `Employee.status == "Left"`. |
| **GAP-03** | **Tích hợp Runtime SKU Pha 2 (Ưu tiên QR Chuẩn + RANSAC Thử nghiệm)** | `sku_scanner.py` và 3 bảng `sku_*` chưa nối vào `main.py`, worker, UI; chưa hỗ trợ đa vật thể cùng loại và `zxing-cpp`. | Nối `sku_routes.py` + `sku.js` ở **Pha 2**, camera xưởng **tắt FaceID nặng**. Dùng `zxing-cpp` QR/Barcode làm nguồn chuẩn đẩy **Draft Stock Entry**; `Multi-Instance ORB/SIFT` gắn nhãn *Experimental* có kiểm tra ánh sáng/ROI. |
| **GAP-04** | **Đa Camera, Polygon Zone & Rule Engine (Time-Based)** | Hiện chỉ chạy 1 camera (`gate-1`), 1 `motion_zone`. Giao diện `whep_canvas.js` đang là singleton 1 camera. | Tạo bảng `cameras` (có `capability_tier`), `zones`, `camera_rules`, `zone_presence_sessions`; refactor `whep_canvas.js` thành multi-instance; xây `zone_engine.py` dùng ngưỡng **thời gian thực (giây/ms)**, không suy diễn năng suất. |
| **GAP-05** | **Hardening RBAC Toàn diện, Băm Mật khẩu & RFID/QR Fallback** | `main.py:485` đang so sánh password plain-text; `/files/*`, `/video*`, `/ws`, `/api/employees`, `/api/appearances*` chưa khóa `require_role`; chưa có UI Login và RFID/QR fallback. | Thêm Login RBAC trên `webapp/`, băm mật khẩu (`scrypt`/`pbkdf2`), khóa `require_role` toàn diện cho HTTP/WS/tệp sinh trắc học; thêm `/api/attendance/badge-checkin` cho đầu đọc RFID/QR dự phòng. |

### 2.2. Nhóm Điểm Cần Cải thiện để Đạt Hiệu năng & Chịu được Camera Kém (Improvements)

| Mã | Điểm hiện tại cần cải thiện | Nguyên nhân & Rủi ro khi chạy thực tế | Phương án tối ưu kỹ thuật |
|---|---|---|---|
| **IMP-01** | **Chuyển sang `Camera-Agnostic` (`Auto-Probe` + `Capability Tiers`)** | Khóa cứng profile Tapo/Hikvision khiến hệ thống gãy khi dùng camera 720p, 10–15fps, 1 stream hoặc H.265. `whep_canvas.js:91` xóa hộp sau `400ms` làm nhấp nháy khung. | Chạy `ffprobe` tự phân cấp `high \| standard \| constrained`; điều chỉnh `min_face`/`min_blur` mà **không hạ LLR**; tính `overlay_stale_ms` & `unattached_confirm_sec` theo **giây**; hỗ trợ `Motion Wake-up` trên 1 stream và fallback MJPEG khi H.265 WebRTC lỗi. |
| **IMP-02** | **Frame-Drain, Safe Event-Driven Gating & Kiểm soát Contention** | `worker_perception.py:450` đọc `cap.read()` đồng bộ; `always_fallback_unmatched=true` chạy YuNet 1280px mọi frame; bỏ hẳn bước embed khi `COMMITTED` sẽ làm vô hiệu hóa `split_cosine`. | Thêm Latest-Frame Drain Thread + Mid-Frame Tracker; chỉ chạy `YuNet` toàn khung mỗi `N` chu kỳ (không upscale cam 720p); **tắt `MiniFASNet` sau `LIVE_CONFIRMED`, giữ bước embed `SFace` chu kỳ `0.6–0.8s`** cho `split_cosine`; giới hạn `intra_op_num_threads`. |
| **IMP-03** | **Full-Pipeline Multi-Cam Benchmark (thay thế số đo `23.5 ms` đơn lẻ)** | `benchmark.py --ten-faces-bench` chỉ đo hàm `embed_batch`, bỏ qua `decode + YOLOX + YuNet + pose + MiniFASNet` và xung đột 2+ worker chạy chung CPU. | Mở rộng `benchmark.py` đo **trọn chuỗi pipeline** trên video quay từ **camera yếu nhất dự kiến dùng** khi chạy song song 2+ worker trước khi công bố năng lực trần. |
| **IMP-04** | **Chống trùng Thực chất ở ERPNext & Bảo vệ tải ERP** | Frappe/ERPNext mặc định bỏ qua `X-Idempotency-Key`; timeout HTTP khi retry có thể tạo 2 bản ghi trùng. | Trước khi retry `POST` (`hrm_attempts > 0`), gọi `GET` kiểm tra `(employee, time ± 30s, log_type)`; thêm custom unique field nếu ERP cho phép; thêm **Circuit Breaker** & giới hạn `≤ 10 req/s`. |
| **IMP-05** | **Hỗ trợ `OpenVINOExecutionProvider` đồng bộ & `VectorIndex` (FAISS)** | `PoseEstimator3D`, `ContextDetector`, `Liveness` đang hardcode `CPUExecutionProvider`; thiếu wrapper `faiss-cpu` cho mở rộng lớn; `max_frames` đang là `21`. | Truyền `_resolve_ort_providers(ep)` vào cả 4 engine ONNX; bọc `Gallery` qua `VectorIndex` (`NumPy` `<2000` / `FAISS` `≥2000`); chuẩn hóa enrollment `8–15` frame. |

---

## 3. Kiến trúc Triển khai ERP Đề xuất (Target Deployment Topology)

```text
[CỔNG HRM (2 CAM) & KHU VỰC XƯỞNG - MẠNG LAN / PoE]
  ├── Pha 1 — Camera Cổng Vào (hrm_in) & Cổng Ra (hrm_out) [Camera IP RTSP bất kỳ: 720p..3MP]
  ├── Đầu đọc RFID USB / Quét QR dự phòng (khi camera yếu / 2FA tính lương)
  ├── Pha 2 — Camera Xưởng/Kho (prod_zone / sku_station) [Tắt FaceID nặng]
  └── LAN / Tailscale Subnet Router (0 Port-Forwarding ra Internet) ──────────────┐
                                                                                 ▼
[LINUX SERVER CPU HIỆN CÓ (Ubuntu/Debian · 0đ GPU · chrony NTP · tmpfs RAM)]
  ├── 1. MediaMTX + ffprobe Auto-Probe (-an, TCP, tự fallback MJPEG nếu H.265)
  │        ├── WebRTC WHEP / MJPEG ──► Trình duyệt Multi-Cam <canvas> (overlay_stale_ms động)
  │        └── Vòng đệm tmpfs fMP4 1s ──► Clip bằng chứng MP4 + HMAC-SHA256
  ├── 2. Perception Worker Pool (giới hạn intra_op_num_threads mỗi worker)
  │        ├── Frame-Drain Thread (luôn lấy frame mới nhất) + Mid-Frame Tracker
  │        ├── Pha 1 (Cổng): YOLOX → YuNet (adaptive) → PnP 5 điểm → SFace (giữ split_cosine) → MiniFASNet (tắt sau LIVE_CONFIRMED)
  │        └── Pha 2 (Xưởng): Tắt FaceID nặng; YOLOX Zone (time-based) + zxing-cpp QR (chuẩn) / ORB-SIFT (thử nghiệm)
  └── 3. Control & ERP Integration Plane (FastAPI + SQLite WAL / PostgreSQL)
           ├── Global Cooldown theo (employee_id, log_type) + attendance_ledger (IN/OUT/RAW)
           ├── zone_presence_sessions & sku_events (Pha 2)
           ├── erp_outbox + HRMWorker (bắt buộc erp_employee_id + GET-before-retry + Circuit Breaker)
           └── Bảo mật: AES-256-GCM + Băm mật khẩu + RBAC toàn diện (/files/*, /video*, /ws)
                                                                                 │
                                      REST HTTPS (API Key:Secret + Dedup + CB)   │
                                                                                 ▼
[ERPNext / Frappe HRMS & Kho / Sản xuất (Staging ──► Production)]
  ├── Pull: Employee (bắt buộc HR-EMP-xxxx, khóa Left), Item (danh mục SKU/barcode)
  └── Push: Employee Checkin (IN/OUT) · Vision Zone Presence · Draft Stock Entry (từ QR/Barcode chuẩn)
```

---

## 4. Đặc tả Quy trình Nghiệp vụ Chuẩn bị Go-Live ERP

### 4.1. Pha 1 — Phân hệ HRM (2 Camera Cổng, Camera-Agnostic & Đối soát Lương)
1. **Quy trình Onboarding Nhân viên & Pháp lý**:
   - Bước 1: Tạo nhân viên trên **ERPNext** (`Employee`, vd `HR-EMP-00012`) → đồng bộ về FaceGate (`erp_employee_id` bắt buộc có trước khi đẩy công).
   - Bước 2: Nhân viên ký Consent (`consent_ref`, `consent_signed_at`).
   - Bước 3: HR/nhân viên đăng nhập RBAC tải lên **1 video ngắn 6–10 giây** (hoặc Guided Capture) → trích **8–15 frame** rõ nhất theo góc nhìn, mã hóa `AES-256-GCM`, **xóa ngay video gốc**, trạng thái `pending` chờ HR duyệt.
2. **Quy trình Ghi nhận Công Hàng ngày (`IN` / `OUT`)**:
   - **Chuẩn 2 Camera Cổng**: `gate-in` (`hrm_in`) chỉ phát `IN`; `gate-out` (`hrm_out`) chỉ phát `OUT`. Control Plane `main.py` khóa trùng toàn cục theo `(employee_id, log_type, cooldown_minutes)` để người đi lướt rìa cam không sinh bản ghi kép.
   - **Khi sử dụng Camera kém hơn (720p, mờ, thiếu sáng)**: Hệ thống giữ nguyên rào cứng LLR (trả `Unknown` nếu dưới chuẩn thay vì đoán tên), chuyển ca sát biên vào **Audit Queue** hoặc hướng dẫn dùng **RFID/QR (`/api/attendance/badge-checkin`)**.
   - **Hậu kiểm 5% & Thu hồi Công Sai**: Giữ clip `legal_hold=1` cho ca cần duyệt; HR thu hồi sẽ ghi bút toán `checkin_revoked` và gọi `DELETE Employee Checkin` trên ERPNext.

### 4.2. Pha 2 — Phân hệ Sản xuất & Kho vận (Mở rộng sau khi Pha 1 Đạt Gate)
1. **Tách tải AI**: Camera khu vực xưởng/kho tắt `SFace`, ước lượng pose, `MiniFASNet` để tránh tranh chấp CPU với 2 cam cổng HRM.
2. **Khai báo SKU & Kiểm đếm cho ERP**:
   - Ưu tiên **QR/Barcode (`zxing-cpp`)** dán trên kiện/thùng để tạo **Draft Stock Entry** (chờ thủ kho duyệt) trên ERPNext.
   - Chế độ **ORB/SIFT Multi-Instance RANSAC** gắn nhãn *Experimental* (chỉ dùng tại bàn/băng tải cố định ánh sáng ổn định và sau khi đo đạt sai số thực địa).
3. **Zone & Rule Hiện diện**:
   - Dùng **ngưỡng thời gian thực (`enter_confirm_sec`, `min_dwell_sec`, `exit_grace_sec`)** thay cho đếm frame cứng để chạy nhất quán ở cả 12 FPS lẫn 25 FPS; chỉ lưu dữ liệu vị trí/thời gian khách quan, tuyệt đối không suy diễn KPI/năng suất.

---

## 5. Ma trận Trách nhiệm & Điều kiện Đầu vào Trước khi Triển khai (Pre-Deployment Checklist)

| Hạng mục kiểm tra | Phụ trách | Tiêu chí đạt (Go / No-Go) |
|---|---|---|
| **Hạ tầng Camera-Agnostic & Mạng** | IT Hạ tầng | Lắp 2 camera cổng (`hrm_in` / `hrm_out`, cự ly `1.4–2.0 m` nếu ống kính rộng/720p); `ffprobe` xác nhận luồng qua LAN/Tailscale (`0` port-forward Internet); `rtsp_transport;tcp` ổn định 24h. |
| **Server Linux & Môi trường CPU** | DevOps / Backend | Server CPU chạy MediaMTX + `tmpfs` + `chrony` NTP; thiết lập `intra_op_num_threads`, `FACEGATE_BIOMETRIC_KEY`, `FACEGATE_HMAC_SECRET`, `FACEGATE_AUTH_SECRET`, mật khẩu RBAC đã băm và khóa `/files/*`, `/ws`. |
| **ERPNext Staging & Mapping** | Team ERP | Integration User có API Key/Secret đọc `Employee`/`Item`, tạo/xóa `Employee Checkin` và Draft `Stock Entry`; toàn bộ nhân viên chấm công đều ánh xạ hợp lệ `erp_employee_id` (`HR-EMP-xxxx`). |
| **Pháp chế & Dữ liệu Sinh trắc học** | HR & Pháp chế | Phê duyệt mẫu Consent (`deploy/consent-template-k0.md`), chính sách `retention_days = 30`, kiểm tra thực tế mọi endpoint ảnh/clip đều đòi quyền RBAC. |
| **Cổng Nghiệm thu Full-Pipeline Benchmark** | QA & AI Lead | Chạy **full-pipeline benchmark 2 worker song song** (`3 → 5 → 10 mặt`) trên footage của **camera yếu nhất dự kiến dùng**; kiểm chứng FAR/FRR/ID-Switch/CPU/RAM/ERP latency trước khi mở Pha 2 SKU/Zone. |

---

## 6. Kế hoạch Thực hiện theo 2 Pha (Implementation Roadmap)

| Giai đoạn | Phạm vi công việc chính | Tiêu chí bàn giao |
|---|---|---|
| **Pha 1A (Sprint 1): Camera-Agnostic, Tối ưu CPU & Hardening RBAC** | Xây `Auto-Probe (ffprobe)` + `Capability Tier`, chuyển ngưỡng `quality`/`whep_canvas`/`tracking` sang **time-based**, refactor `whep_canvas.js` multi-cam, thêm Frame-Drain + Safe Gating (giữ bước embed `SFace` cho `split_cosine`) + giới hạn thread ONNX; băm password & khóa `require_role` cho `/files/*`, `/video*`, `/ws`, `/api/employees`. | Xem đồng thời 2 cam cổng không nhấp nháy trên cả luồng 720p@12fps & 1080p@25fps; Full-Pipeline Benchmark 2 worker đạt ngưỡng CPU an toàn; `pytest` xanh 100%; RBAC chặn 100% truy cập trái phép. |
| **Pha 1B (Sprint 2): 2 Cam Cổng IN/OUT & Tích hợp ERPNext Hoàn chỉnh** | Nâng cấp `ledger.py` & `shift.py` hỗ trợ `IN`/`OUT`/`RAW` + ca đêm + **Global Cooldown** tại `main.py` (bỏ `auto_alternate`); xây `erp_sync.py` đồng bộ & bắt buộc `erp_employee_id`, `GET-before-retry`, Circuit Breaker; thêm `/api/attendance/badge-checkin` (RFID/QR). | Test end-to-end với ERPNext staging cho 2 cam cổng: `IN` qua `gate-in`, `OUT` qua `gate-out`, lọt rìa cam không tạo trùng, thu hồi `DELETE`, khóa người `Left`, ngắt mạng 60p đồng bộ đủ không trùng. |
| **Pha 2A (Sprint 3): Nối SKU Runtime (QR Chuẩn + RANSAC Thử nghiệm) & Zone Sản xuất** | Tách cấu hình tắt FaceID nặng cho camera xưởng; nâng cấp `sku_scanner.py` (`zxing-cpp` + `Multi-Instance RANSAC` *Experimental* + `SkuTracker`), mở `sku_routes.py` & `sku.js`; xây `zone_engine.py` (time-based), `zone_routes.py`, `zones_rules.js` & đẩy `Draft Stock Entry` qua `erp_outbox`. | QR/Barcode đẩy `Draft Stock Entry` chuẩn xác; Zone ghi nhận đúng `enter_ts`/`exit_ts`/`dwell_sec` ở cả 12fps & 25fps; đo riêng hiệu năng & sai số ORB/SIFT trên bao bì thật của kho. |
| **Pha 2B (Sprint 4): Đo kiểm Hiện trường Đa Camera & Bàn giao Go-Live** | Chạy `k0_field_runner.py` & `replay_eval.py` trên các camera thật tại cổng và xưởng (bao gồm camera kém nhất); chốt biên bản ngưỡng năng lực tối đa & SOP vận hành. | Biên bản nghiệm thu thực địa đầy đủ chỉ số trước khi vận hành chính thức toàn nhà máy. |
