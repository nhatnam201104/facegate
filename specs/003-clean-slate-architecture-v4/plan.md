# Implementation Plan: Tái Kiến trúc Toàn diện FaceGate v4 (3 Mặt phẳng Độc lập, Chuẩn hóa cho Tapo C310 & Sẵn sàng Đa cổng / Phase 2 SKU)

**Branch**: `003-clean-slate-architecture-v4` | **Date**: 2026-11-13 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/003-clean-slate-architecture-v4/spec.md` & Biên bản phản biện kiến trúc 6 vòng `docs/PHAN_BIEN_KIEN_TRUC.md` (`H1`–`H35`)

---

## Summary

Bản kế hoạch này định nghĩa kiến trúc xây dựng lại có lộ trình (**Clean-Slate Phased Architecture v4**) cho hệ thống chấm công khuôn mặt **FaceGate**, dựa trên kết quả hội tụ 100% qua 6 vòng phản biện kỹ thuật (`0/35` lỗ hổng mở) và ràng buộc phần cứng thực tế tại hiện trường là camera **TP-Link Tapo C310** (`2304×1296 @ 15 fps`, H.264, tiêu cự `3.89 mm`, cảm biến `1/2.7"`, giới hạn `2+2` luồng RTSP, phát kèm audio `pcm_alaw` và không cho chỉnh GOP/tốc độ màn trập).

Kiến trúc mới tách khối đơn luồng hiện tại thành **3 Mặt phẳng Độc lập (Planes)** gắn với 7 trụ cột kỹ thuật đã kiểm chứng tại [research.md](research.md):
1. **Media Plane Không Mã hóa Lại (`MediaMTX` MIT)**: Đóng vai trò **Single RTSP Puller** duy nhất nối tới Tapo C310 qua bộ lọc ingest `ffmpeg -c:v copy -an` (tách sạch 100% âm thanh `pcm_alaw`), phát trực tiếp **WebRTC (WHEP)** kèm lớp phủ HTML5 `<canvas>` đồng bộ bằng `requestVideoFrameCallback` (`receiveTime + overlay_offset_ms`), và ghi vòng đệm `fMP4` 1 giây trên **`tmpfs` RAM** (`≤ 10 phút`, ~300 MB/cam) để trích xuất clip gốc sạch có chữ ký `HMAC-SHA256` (~0% CPU re-encode, hỗ trợ xuất MP4 burn-in FFmpeg theo yêu cầu).
2. **Perception Process Cô lập Lỗi & Khai thác 3MP Thực của C310**: Tách suy luận ra 1 tiến trình riêng (xóa bỏ `ctx.engine_lock` chung với Enrollment); giữ nóng bộ giải mã `/stream1` theo **Lịch thức ca làm việc** (`0 ms` chờ keyframe GOP vào giờ cao điểm), thu nhỏ chính khung `/stream1` về `640×360` tìm vùng người rồi **cắt vùng Đầu–Vai tỷ lệ 1:1 ngay trên cùng khung `2304×1296`** (`Δt = 0 ms`, bỏ giới hạn bóp khung `det_width = 960`).
3. **Lõi Quyết định Liên tục Tuân thủ Constitution & Crossing Quarantine**: Thay thế vách ngăn cứng `40° ± 5°` bằng hàm logistic liên tục 4 tham số $\text{LLR} = a\cdot s + b + c\cdot|\text{yaw}| + d\cdot q$ có cổng khử tương quan chuỗi frame (`Novelty Gate`), vẫn giữ rào cứng kép **`threshold` + `margin` $\mu(\text{yaw})$ siết chặt dần theo góc**; áp đặt cách ly `Crossing Quarantine` khi hai người mặc đồng phục đi giao cắt và tắt phạt `unattached_face` khi cắt khung đáy ảnh (`Viewport-Aware`).
4. **Sổ cái Chấm công Append-Only, Hậu kiểm Ngoại lệ & Bảo vệ `legal_hold`**: Ghi nhận bất biến (`checkin_created`, `checkin_revoked`, `checkin_manual`) có `idempotency_key` và đồng bộ thu hồi hai chiều sang ERPNext; tự động dọn đĩa `< 2 GB` nhưng bảo vệ tuyệt đối mọi clip có cờ `legal_hold = 1`; cấm tự học mẫu online chống đầu độc dữ liệu.
5. **Đăng ký Soi gương Tức thời (Client WASM) + Mẫu Chuẩn Miền Camera Cổng**: Trình duyệt chạy `MediaPipe Face Landmarker` WASM (Apache-2.0) phản hồi hướng dẫn góc `0 ms` trễ mạng, còn ảnh mẫu lưu DB bắt buộc chụp từ chính camera cổng và qua bước HR bấm Duyệt.

---

## Technical Context

**Language/Version**: Python 3.10+ (kiểm chứng trên Python 3.12 Linux & Windows) + Vanilla ES Modules JS trên trình duyệt

**Primary Dependencies**:
- **Control & Business Plane**: FastAPI, Uvicorn, SQLite3 WAL (1 cổng) / PostgreSQL `psycopg[binary]` tùy chọn (khi ≥ 2 cổng ở `K5`), PyYAML, `cryptography>=42.0` (Apache-2.0/BSD-3, phục vụ mã hóa nghỉ `AES-256-GCM`) & stdlib `hmac`/`hashlib` (`HMAC-SHA256`)
- **Media Plane**: `MediaMTX` (binary độc lập, giấy phép MIT) + `ffmpeg` CLI (`-c:v copy -an` ingest & on-demand burn-in export)
- **Perception Process**: OpenCV (`opencv-python-headless` trên Linux), NumPy, ONNX Runtime (`CPUExecutionProvider` mặc định; tùy chọn `OpenVINOExecutionProvider` ở `K4`), các mô hình thương mại hóa được (`EdgeFace-XS` MIT, `YuNet` MIT/Apache-2.0, `3DDFA_V2` MIT, `YOLOX-Nano` Apache-2.0, `MiniFASNet` Apache-2.0)
- **Client Guided UI**: HTML5 `<video>` WebRTC WHEP + `<canvas>` 2D + `MediaPipe Face Landmarker` WASM (Apache-2.0, nạp cục bộ cho tab Đăng ký)

**Storage**: SQLite WAL (`data/facegate.db` mở rộng qua `_migrate` tự động với bảng `attendance_ledger`, `audit_logs` và cờ `legal_hold`), vòng đệm ngắn hạn `tmpfs` (`/dev/shm/facegate_ring`, ~300 MB/camera), kho clip MP4 gốc + `.meta.json` ký HMAC tại `data/clips/`

**Testing**: `pytest` (unit, IPC fault-isolation, ledger idempotency, quarantine state-machine) + Bộ công cụ Replay ngoại tuyến deterministic trên gói dữ liệu thực địa `K0` (`scripts/replay_eval.py`) & `scripts/benchmark.py`

**Target Platform**: Linux Server (Ubuntu/Debian ưu tiên vận hành thật với `tmpfs` và `chrony` NTP nội bộ) & Windows 10/11 (môi trường phát triển/demo), chạy 100% trên CPU phổ thông, kết nối camera **TP-Link Tapo C310** qua cáp LAN RJ45 trong VLAN cô lập

**Project Type**: Multi-Plane Real-Time Edge Vision & Attendance System (Media Relay + Isolated AI Worker + Async Web Control Plane)

**Performance Goals**:
- Luồng xem trực tiếp đạt **≥ 90% tốc độ nguồn** (`13–15 fps` trên Tapo C310), độ trễ hiển thị LAN **p90 < 300 ms**, lệch vị trí khung `<canvas>` **p90 ≤ 2 frame**
- 5 người xem đồng thời làm tăng tải CPU máy chủ **< 10 điểm %**; ghi vòng đệm clip hàng ngày tốn **< 1% CPU**
- Chu kỳ suy luận (tính cả giải mã `/stream1` 3MP@15fps + cắt ROI Đầu–Vai 1:1 + AI) đạt trung bình **< 200 ms** ở tải tối đa **10 khuôn mặt đồng thời** (`K4`)
- Pre-roll của clip lịch sử đảm bảo nằm trong **`[2.0 s, 2.0 s + 1 GOP]`**, chứa **0 byte âm thanh**

**Constraints**:
- Không yêu cầu GPU rời; tuyệt đối không dùng thư viện/trọng số dính giấy phép `AGPL-3.0` (như `ultralytics`) hoặc non-commercial trong bản vận hành
- Không lượng tử hóa INT8 trên `EdgeFace-XS` và `3DDFA_V2`; tắt hoàn toàn tự động thêm mẫu online (`auto-enroll`)
- Trên phần cứng Tapo C310: bắt buộc cắm dây RJ45, không gắn thẻ `microSD`, tắt Tapo Care, không mở App Tapo xem trực tiếp khi hệ thống đang chạy, khóa cứng `Day Mode` + đèn trợ sáng cổng ≥ 250–300 lux, cô lập VLAN chặn Internet kèm DNAT `UDP 123` về `chrony` trên Server

**Scale/Scope**: Hỗ trợ 1–10 người đi qua đồng thời trên mỗi cổng, lộ trình mở rộng từ 1 cổng (SQLite WAL) lên nhiều cổng + Phase 2 nhận diện hàng hóa/SKU dùng chung schema `TrackedObject`

---

## Constitution Check

*GATE: Đánh giá nghiêm ngặt trước Phase 0 Research và tái xác nhận sau khi hoàn tất thiết kế Phase 1 đối chiếu với `.specify/memory/constitution.md`.*

| Nguyên tắc & Ràng buộc trong `constitution.md` | Pre-Phase 0 | Đánh giá Post-Phase 1 Design | Kết quả |
|---|---|---|---|
| **I. Precision-First Open-Set** (Không đoán tên, bắt buộc vượt đồng thời `threshold` + `margin` top-1/top-2 theo góc nhìn) | ✅ Đạt | Giữ nguyên rào cứng kép trên điểm tích lũy đã hiệu chỉnh: $\text{LLR}(\text{top1}) \ge \tau$ **và** $\text{LLR}(\text{top1}) - \text{LLR}(\text{top2}) \ge \mu_0 + \mu_1\cdot|\text{yaw}|/90^\circ$ (`H25`), cộng thêm `Crossing Quarantine` bắt vượt lại từ đầu sau giao cắt (`H3`). | **PASS** |
| **II. Defense-in-Depth Liveness** (Không ghi check-in thiếu xác thực sống, định tuyến đa lớp & gắn nhãn hậu kiểm) | ✅ Đạt | Đo kiểm thực nghiệm theo chuẩn **ISO/IEC 30107-3** (`APCER`/`BPCER`) ngay ở `K0`, giữ phân tầng `verified` / `profile_verified`, kết hợp `Viewport-Aware Context` và quy trình **Audit-by-Exception** gắn với sổ cái thu hồi `H22`. | **PASS** |
| **III. CPU-First Real-Time** (Chạy mượt trên CPU phổ thông, tách biệt hoàn toàn camera / stream / clip khỏi AI, `< 200 ms`/vòng) | ✅ Đạt | Thực thi ở mức triệt để nhất: `MediaMTX` gánh toàn bộ RTSP/WebRTC/fMP4 (`~0%` CPU re-encode), `Perception` chạy ở process riêng với Two-Stage ROI 1:1 trên khung 3MP và Lịch thức theo ca. | **PASS** |
| **IV. License & Privacy** (Model mặc định Apache-2.0/MIT/BSD, tôn trọng `retention_days`, xóa sinh trắc học ngay khi nghỉ việc) | ✅ Đạt | Mọi thành phần mới (`MediaMTX` MIT, `MediaPipe` Apache-2.0) đều sạch bản quyền thương mại; loại bỏ 100% âm thanh (`-an`, `H28`); vòng đệm `tmpfs` tự hủy ≤ 10 phút; cờ `legal_hold` tự hạ về `0` ngay khi đóng khiếu nại để tuân thủ `retention_days`. | **PASS** |
| **V. Test-First & Measurable Gates** (`pytest` cho logic lõi + công cụ đo định lượng FAR, FRR, Spoof-Pass, p50/p90) | ✅ Đạt | Đặt hẳn pha **`K0` (Baseline tuần tự tái lập + Gói Replay có crop gốc)** làm cổng bắt buộc đầu tiên, báo cáo FAR hai tầng kèm cận trên khoảng tin cậy (`H12`) trước khi thay thế bất kỳ module nào. | **PASS** |
| **Ràng buộc Kỹ thuật & Governance** (Python 3.10+, FastAPI, OpenCV, ORT CPU, SQLite WAL, HTML/CSS/JS thuần không build step; ngoại lệ ghi vào `Complexity Tracking`) | ⚠️ Ghi nhận Governance | Frontend giữ nguyên **HTML/CSS/JS thuần không build step** (`<video>` + `<canvas>`); việc bổ sung binary `MediaMTX` (`K1`), tùy chọn `OpenVINO EP` (`K4`) và `PostgreSQL` đa cổng (`K5`) được giải trình đầy đủ trong bảng **Complexity Tracking** bên dưới theo đúng mục Governance. | **PASS (Tracked)** |

---

## Project Structure

### Documentation (this feature)

```text
specs/003-clean-slate-architecture-v4/
├── spec.md                              # Đặc tả yêu cầu tổng thể & tiêu chí nghiệm thu v4
├── plan.md                              # Kế hoạch kiến trúc & lộ trình K0 -> K5 (file này)
├── plan.html                            # Báo cáo kế hoạch tương tác (Mermaid + Knowledge Graph + Ký duyệt)
├── research.md                          # Phase 0: 8 quyết định kiến trúc cốt lõi đóng 35 lỗ hổng H1-H35
├── data-model.md                        # Phase 1: TrackedObject, State Machine, Append-Only Ledger & Sidecar HMAC
├── quickstart.md                        # Phase 1: Quy trình chuẩn bị Tapo C310, lệnh đo K0 & nghiệm thu K1-K5
├── checklists/
│   └── requirements.md                  # Checklist kiểm định chất lượng đặc tả (9/9 PASS)
└── contracts/
    ├── media-and-ipc.md                 # Hợp đồng MediaMTX (-an, tmpfs, WHEP), IPC Socket & Canvas Sync
    └── config-and-hrm-ledger.md         # Hợp đồng config.yaml, REST API Sổ cái chấm công & Đồng bộ HRM 2 chiều
```

### Source Code (repository root — Kiến trúc mục tiêu theo lộ trình `K0` → `K5`)

```text
deploy/
├── mediamtx.yml                         # [MỚI - K1] Cấu hình Single RTSP Puller, lọc -an pcm_alaw, WHEP & tmpfs fMP4
└── vlan-ntp-chrony.md                   # [MỚI - K0] Cấu hình mẫu cô lập VLAN Tapo C310 & DNAT UDP 123 về chrony

app/
├── main.py                              # [CẬP NHẬT - K1/K2] Control Plane: REST API, WebSocket relay, Health & Auth/RBAC (K5)
├── ipc.py                               # [MỚI - K2] Giao thức NDJSON Socket kết nối bất đồng bộ với Perception Process
├── decision.py                          # [MỚI - K2] TrackDecisionState, hàm LLR liên tục 4 tham số, Novelty Gate & rào μ(yaw)
├── ledger.py                            # [MỚI - K2] Sổ cái chấm công append-only (created/revoked/manual) & Audit-by-Exception
├── media_clips.py                       # [MỚI - K1] Gọi MediaMTX Playback API cắt MP4 sạch, ký HMAC sidecar & xuất burn-in FFmpeg
├── worker_perception.py                 # [MỚI - K2] Điểm vào Perception Process độc lập (Lịch thức ca H34 + Two-Stage ROI 3MP H35)
├── tracker.py                           # [CẬP NHẬT - K3] Bổ sung Crossing Quarantine nghiêm ngặt & Viewport-Aware grace
├── context_detector.py                  # [CẬP NHẬT - K3] Miễn trừ unattached_face khi hộp mặt chạm sát biên dưới khung hình
├── db.py                                # [CẬP NHẬT - K2/K5] Migration thêm bảng attendance_ledger, audit_logs & cờ legal_hold
├── hrm.py                               # [CẬP NHẬT - K2] Lưu remote_doc_id, gửi idempotency_key & ánh xạ hủy chứng từ ERPNext
├── enrollment.py                        # [CẬP NHẬT - K2/K4] Chạy trên session riêng không khóa cổng, chụp mẫu chuẩn từ camera cổng
└── static/
    ├── index.html                       # [CẬP NHẬT - K1/K2] Thẻ <video> WebRTC WHEP + lớp <canvas> 2D & khu vực Hậu kiểm/Dự phòng
    ├── whep_canvas.js                   # [MỚI - K1] Kết nối WHEP, đồng bộ requestVideoFrameCallback + overlay_offset_ms
    ├── guided_wasm.js                   # [MỚI - K4] Phản hồi góc đầu soi gương 0ms bằng MediaPipe WASM cục bộ
    └── app.js                           # [CẬP NHẬT - K1/K2] Tích hợp cảnh báo heartbeat, thu hồi/nhập bù công & bật/tắt legal_hold

scripts/
├── record_k0_bundle.py                  # [MỚI - K0] Ghi gói đánh giá thực địa (manifest + tuần tự JSONL + ảnh crop gốc)
├── replay_eval.py                       # [MỚI - K0/K2] Chạy Replay ngoại tuyến đo FAR ghép chéo (kèm CI), FRR, APCER/BPCER
└── benchmark.py                         # [CẬP NHẬT - K0/K4] Đo chi phí decode 3MP@15fps, Laplacian blur theo cự ly & tải 10 mặt

tests/
├── test_decision_llr.py                 # [MỚI - K2] Kiểm thử Novelty Gate, tính đơn điệu μ(yaw) và tuân thủ Nguyên tắc I
├── test_ledger_and_hold.py              # [MỚI - K2] Kiểm thử sổ cái append-only, thu hồi ERPNext idempotent & bảo vệ legal_hold
├── test_crossing_quarantine.py          # [MỚI - K3] Kiểm thử chống tráo tên khi 2 người mặc đồng phục giao cắt & cắt khung đáy
├── test_ipc_fault_isolation.py          # [MỚI - K2] Kiểm thử kill Perception Process không làm gián đoạn API / Bảng công
└── [6 file test hiện hữu]               # [GIỮ NGUYÊN] Bảo đảm tương thích ngược và không hồi quy
```

**Structure Decision**: Giữ nguyên bố cục phẳng, rõ ràng của gói `app/` hiện tại (ánh xạ trực tiếp lên 5 lớp kiến trúc trong `docs/KNOWLEDGE_GRAPH.md`), tách rạch ròi điểm vào của tiến trình suy luận (`app/worker_perception.py`), máy trạng thái quyết định (`app/decision.py`), sổ cái bất biến (`app/ledger.py`) và cầu nối `MediaMTX` (`deploy/mediamtx.yml` + `app/media_clips.py`) để có thể triển khai cuốn chiếu từng pha mà không làm gãy hệ thống đang chạy.

---

## Phased Implementation Roadmap (`K0` → `K5` — Định hướng cho `/speckit-tasks`)

> **Ghi chú nhân sự & tiến độ (`H18`)**: Báo cáo song song **2 dải ước lượng** cho 1 kỹ sư thực hiện (tách riêng thời gian lịch chờ phê duyệt consent, mua sắm và đi dây mạng LAN trước `K0`): **Dải lạc quan: 19–27 ngày công** · **Dải thận trọng: 31–47 ngày công** *(Pha `K1` và `K2` độc lập nên có thể chạy song song nếu bố trí 2 kỹ sư)*.

| Pha | Nội dung triển khai trọng tâm | Cổng nghiệm thu bắt buộc (Exit Gate) | Ước lượng (Lạc quan / Thận trọng) |
|---|---|---|---|
| **`K0` — Hiện trường Tapo C310, Dữ liệu & Baseline** | Duyệt đơn đồng ý sinh trắc học (`H11`); cấu hình Tapo App (`Day Mode`, tắt auto-update, tạo RTSP account) → cắm dây **RJ45**, bỏ `microSD`, chiếu sáng cổng `≥ 250–300 lux` → bật `chrony` trên Server & cô lập VLAN kèm DNAT `UDP 123` (`H27`–`H30`). Đo GOP bằng `ffprobe`, đo px mặt theo khoảng cách (`H26`), đo Laplacian blur đi ngang vs. đi thẳng (`H32`), đo rớt luồng/giờ & CPU decode 3MP. Quay 2 bố cục ứng viên + `≥ 20` lượt mỗi loại PAI (`H13`); chạy baseline tuần tự (`H14`) & xuất gói Replay có lưu crop gốc. | Có bảng Baseline hoàn chỉnh cho cả 2 bố cục trên C310: FAR ghép chéo & sự kiện kèm cận trên CI (`H12`), FRR theo góc, APCER/BPCER từng PAI (`H5`, `H13`), phân phối blur, GOP thực tế và CPU decode 3MP. | **4–6 ngày** / **6–9 ngày công** *(+ lịch chờ lắp đặt & consent)* |
| **`K0.5` — Cổng Quyết định Chiến lược PM** | Trình báo cáo số liệu `K0` + báo giá nhà phân phối thực tế để PM/Pháp chế chốt **4 câu hỏi chiến lược** (mục dưới): bố cục cổng (`H2`), phương án phần cứng **`A / B / C / C'`** (`H6`, `H31`), quy chế clip đối soát lương (`H9`) và hạn lưu dữ liệu test (`H11`). | Biên bản phê duyệt phạm vi của PM; tránh viết code dư thừa cho các nhánh phần cứng không chọn. | **0.5 ngày (1 buổi họp)** |
| **`K1` — Media Plane (`MediaMTX` + WebRTC `<canvas>`)** | Dựng `MediaMTX` làm Single RTSP Puller (`H20`), ingest qua `ffmpeg -c:v copy -an` có giám sát tự khởi động lại (`H28`), phát WebRTC WHEP + vẽ `<canvas>` đồng bộ bằng `receiveTime + overlay_offset_ms` (`H8`), ghi `fMP4` 1 s lên `tmpfs` (`10m`) + Playback API cắt MP4 sạch kèm sidecar ký HMAC + API xuất burn-in FFmpeg (`P2`, `H24`), chuẩn hóa `TrackedObject` chờ Phase 2 (`H21`). | Tốc độ hiển thị **≥ 90% nguồn** (`13–15 fps` trên C310); trễ p90 `< 300 ms`; lệch khung bao **p90 ≤ 2 frame** trên bài test đồng hồ ms; 5 người xem tăng CPU `< 10` điểm %; clip chứa **0 track audio** và pre-roll đạt `[2 s, 2 s + 1 GOP]`. | **3–4 ngày** / **5–7 ngày công** |
| **`K2` — Perception Process, Lõi Quyết định & Sổ cái HRM** | Tách `worker_perception.py` chạy process riêng (`M1`, `P6`); áp dụng **Lịch thức theo ca** (`H34`) + **Two-Stage ROI 1:1 trên cùng khung `/stream1`** (`H33`, `H35`); State Machine dùng hàm LLR liên tục 4 tham số + `Novelty Gate` + rào cứng `threshold & margin μ(yaw)` (`H4`, `H25`); tắt auto-enroll (`H17`); sổ cái `attendance_ledger` append-only + thu hồi ERPNext idempotent (`H22`) + Audit-by-Exception 5% (`H7`); heartbeat giám sát camera/ffmpeg/worker, dọn đĩa `< 2 GB` tôn trọng `legal_hold` & kênh chấm công dự phòng (`H15`). | Trên Replay `K0`: cận trên FAR không tệ hơn baseline, FRR & thời gian chốt tốt hơn; thời gian thức ngoài giờ `≤ 1 GOP`; kill thử worker hoặc rút cáp camera thì API/bảng công vẫn sống và báo động `≤ 5 s`; thu hồi công đồng bộ đúng sang HRM. | **4–5 ngày** / **8–12 ngày công** |
| **`K3` — Crossing Quarantine & Viewport Tracking *(nếu `K0.5` giữ đi ngang)*** | Nâng cấp `tracker.py` & `context_detector.py` với cầu nối `YOLOX-Nano` + **Crossing Quarantine nghiêm ngặt** (bỏ mẫu lúc giao cắt, bắt vượt lại đủ Open-Set sau khi tách) + **Viewport-Aware Context** miễn phạt cắt khung đáy ảnh (`H3`); chỉ thử thêm detector `Head + Face` (Apache-2.0) nếu chưa đạt cổng. | **0 ca chốt nhầm tên** khi 2 người mặc đồng phục giao cắt trên Replay `K0`; không phạt nhầm `unattached_face` khi người đứng gần bị cắt vai. | **2–4 ngày** / **3–5 ngày công** |
| **`K4` — Runtime 10 Khuôn mặt & Đăng ký Soi gương WASM** | Đo tổng ngân sách `decode 3MP@15fps + suy luận`; bật giải mã phần cứng (`VAAPI`/`QSV`) nếu cần và thử `OpenVINOExecutionProvider` (FP32/FP16; chỉ xét INT8 cho detector hộp — `M2`); tích hợp `MediaPipe` WASM hướng dẫn góc 0 ms ở trình duyệt + chụp mẫu chuẩn từ camera cổng có HR duyệt (`H10`). | Tổng chu kỳ xử lý **< 200 ms** ở tải 10 khuôn mặt đồng thời; toàn bộ chỉ số FAR, FRR, APCER, BPCER trên Replay `K0` không suy giảm. | **2–3 ngày** / **3–5 ngày công** |
| **`K5` — Bảo mật Vận hành & Mở rộng Đa cổng** | Xác thực đăng nhập/RBAC, mã hóa kho ảnh mẫu & embedding ở trạng thái nghỉ, bảng `audit_logs` cho mọi lượt xem/xuất clip và chỉnh sửa công trước khi mở `0.0.0.0` ra mạng LAN; chuyển DB sang PostgreSQL khi gom từ ≥ 2 cổng. | Vượt checklist bảo mật nội bộ & pháp chế dữ liệu sinh trắc học trước khi vận hành chính thức. | **3–5 ngày** / **5–8 ngày công** |

---

## Cổng Quyết định Chiến lược Trình PM / Pháp chế (`K0` & `K0.5`)

1. **[Trước `K0` — `H11`] Phê duyệt Mẫu đơn Đồng ý Dữ liệu Sinh trắc học**: Xác nhận phạm vi quay thử nghiệm, cam kết **không thu âm** (`H28`), cô lập mạng LAN không đi qua cloud (`H29`), hạn xóa video gốc và điều khoản lưu ảnh mẫu nếu chọn thiết bị đầu cuối ở câu 3.
2. **[Tại `K0.5` — `H2` & `H26` & `H32`] Bố cục Lối đi tại Cổng**: Từ bảng đo kích thước mặt theo mét và độ nhòe 15 fps của C310 ở `K0`, chốt phương án dẫn hướng lối đi nhìn thẳng (`±40°`, khoảng cách `1.5–2.5 m`) hay bắt buộc hành lang đi ngang tự do (`±90°`).
3. **[Tại `K0.5` — `H6` & `H31`] Lựa chọn Phần cứng Dựa trên Báo giá Thật & Số đo APCER/BPCER**:
   - **Phương án A**: Mua thiết bị đầu cuối có sẵn cặp camera RGB+IR chống giả mạo phần cứng (buộc dừng nhịp ngắn nhìn máy, phải lưu ảnh gốc dài hạn để đẩy ISAPI) + FaceGate làm Middleware trung tâm & mở rộng Phase 2 SKU.
   - **Phương án B**: Giữ Tapo C310 + Yếu tố xác thực thứ 2 (Thẻ từ / QR động).
   - **Phương án C**: Giữ thuần Tapo C310 RGB (dây RJ45, `Day Mode` + đèn ≥ 250–300 lux, vận hành kèm **Audit-by-Exception** `H7`).
   - **Phương án C'**: Nâng cấp sang Camera IP dòng doanh nghiệp phổ thông (ống kính `4–6 mm`, `25–30 fps`, khóa được tốc độ màn trập, đặt cứng `GOP = 1 s`, tắt audio từ nguồn; tận dụng lại C310 làm camera giám sát chung).
4. **[Tại `K0.5` — `H9` & `H22`] Quy chế Bằng chứng Đối soát Lương**: Xác nhận tính hợp lệ nội bộ của cặp file MP4 gốc sạch + sidecar ký `HMAC-SHA256` (kèm xuất MP4 đóng dấu FFmpeg khi giải quyết khiếu nại) và quy trình thu hồi/nhập bù công hai chiều với HRM.

---

## Complexity Tracking

> Ghi nhận minh bạch các thành phần mở rộng so với ngăn xếp PoC ban đầu theo đúng điều khoản **Governance** tại `.specify/memory/constitution.md:30-33`:

| Thành phần bổ sung / Thay đổi | Lý do kỹ thuật bắt buộc | Vì sao phương án PoC đơn giản hơn bị loại bỏ |
|---|---|---|
| Binary `MediaMTX` (MIT) + `ffmpeg -c:v copy -an` ingest (`K1`) | Tách hoàn toàn việc kéo RTSP, phát WebRTC và ghi vòng đệm `fMP4` trên `tmpfs` khỏi Python; bảo vệ giới hạn `2+2` luồng và lọc bỏ audio `pcm_alaw` của Tapo C310 | Giữ MJPEG + `cv2.VideoWriter` trong Python gây mã hóa kép tốn CPU trên luồng 3MP, làm mất khung hình gốc sạch do burn-in và lỗi muxing khi gặp stream âm thanh G.711 |
| Tách `Perception Process` độc lập qua IPC Socket (`K2`) | Cô lập lỗi segfault/treo driver giải mã video khỏi API chấm công & HRM Outbox; giải phóng khóa `ctx.engine_lock` cho luồng Đăng ký | Chạy chung một tiến trình với FastAPI khiến mọi sự cố camera/C++ lúc vào ca làm sập đồng thời cả bảng công và kênh dự phòng |
| Tùy chọn `OpenVINOExecutionProvider` (`K4`), `cryptography>=42.0` & `PostgreSQL` (`K5`) | Đạt ngân sách `< 200 ms` khi nâng tải từ 3 lên 10 khuôn mặt đồng thời kèm decode 3MP, mã hóa dữ liệu sinh trắc học nghỉ chuẩn `AES-256-GCM` (stdlib Python không có sẵn AES-GCM) và chống khóa ghi khi gom nhiều cổng | `OpenVINO EP` và `PostgreSQL` chỉ bật có điều kiện sau khi đo trên bộ Replay `K0` (với 1 cổng giữ mặc định `CPUExecutionProvider` và `SQLite WAL`); `cryptography` có giấy phép kép Apache-2.0/BSD-3 sạch thương mại |
