# Implementation Plan: Nhận diện khuôn mặt góc nghiêng đến 90° kèm xác thực chống giả mạo đa lớp

**Branch**: `001-profile-90-antispoof` | **Date**: 2026-11-11 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-profile-90-antispoof/spec.md`

---

## Summary

Nâng cấp hệ thống chấm công khuôn mặt **FaceGate** trên **1 camera RGB duy nhất chạy thuần CPU** để mở rộng dải góc nhận diện ngang từ **±35° hiện tại lên tới ±90° (full profile)** đồng thời bịt kín lỗ hổng giả mạo khi mô hình kết cấu chính diện (`MiniFASNet`) mất hiệu lực ở góc nghiêng sâu.

Giải pháp kỹ thuật cốt lõi (chi tiết tại [research.md](research.md)) kết hợp hai trụ cột:
1. **Nhận diện đa góc không trộn lẫn**: Bổ sung mô hình ước lượng lưới 3D nhẹ **`3DDFA_V2` (MIT, ~2 ms/mặt)** để lấy chính xác góc Euler `(yaw, pitch, roll)` và 68 điểm mốc 3D khi khuất 1 mắt; chia căn chỉnh `112×112`, ngăn tích lũy của `Tracker` và ma trận `Gallery` thành **3 nhóm tư thế độc lập** (`frontal`, `left_profile`, `right_profile`) dùng **`EdgeFace-XS` / `SFace`** kết hợp cơ chế **ưu tiên Best-Shot** (chốt bằng góc ≤ 40° bất cứ khi nào có ≥ 2 frame, chỉ dùng khớp Profile-to-Profile với ngưỡng chặt hơn khi toàn bộ hành trình đều nghiêng sâu).
2. **Chống giả mạo 4 lớp định tuyến theo tư thế (Pose-Gated Defense-in-Depth)**: Giữ `MiniFASNetV2 + V1SE` cho nhánh `frontal` (`verified`), và bắt buộc vượt qua tổ hợp 3 cổng cho nhánh `40° < |yaw| ≤ 90°` gồm: (i) phát hiện vật chứa giả mạo (`cell phone`, `tablet`, khung ảnh) & kiểm tra gắn kết hình học Đầu–Cơ thể bằng **`YOLOX-Nano` (Apache-2.0, ~10 ms/frame)**, (ii) kiểm định **Thị sai chuyển động 3D (*3D Motion Parallax*)** qua ≥ 6 frame quỹ đạo bước đi để loại ảnh phẳng 2D, và (iii) kiểm tra phổ **Moiré/chói màn hình** cục bộ, gắn nhãn phân tầng **`profile_verified`** kèm clip bắt buộc cho HR hậu kiểm.

---

## Technical Context

**Language/Version**: Python 3.10+ (đã kiểm chứng trên Python 3.12 Linux & Windows)

**Primary Dependencies**: FastAPI, Uvicorn, OpenCV (`opencv-python-headless>=4.8,<5` trên Linux, `opencv-python>=4.8,<5` trên Windows), ONNX Runtime (`CPUExecutionProvider`), NumPy, Pillow, PyYAML

**AI Models (ONNX CPU)**:
- Phát hiện mặt: `YuNet` (Apache-2.0, sẵn có)
- Ước lượng tư thế & 68 điểm mốc 3D: `3DDFA_V2 MobileNetV1 120×120` (MIT, mới)
- Trích đặc trưng khuôn mặt: `EdgeFace-XS` (BSD-3-Clause, mới) & `SFace` (Apache-2.0, sẵn có); tùy chọn nghiên cứu `AdaFace-IR18` / `ArcFace`
- Ngữ cảnh cơ thể & vật chứa giả mạo: `YOLOX-Nano COCO 416×416` (Apache-2.0, mới)
- Chống giả mạo kết cấu chính diện: `MiniFASNetV2 + MiniFASNetV1SE` (Apache-2.0, sẵn có)

**Storage**: SQLite (WAL mode) tại `data/facegate.db` (tự động migrate schema không mất dữ liệu cũ) + ảnh JPEG (`data/snapshots/`, `data/enroll/`) + video clip WebM/MP4 (`data/clips/`)

**Testing**: `pytest` (unit + integration với mock/synthetic 3D trajectories & real ONNX sessions) + `scripts/benchmark.py`

**Target Platform**: Linux (Ubuntu/Debian Desktop & Headless Server qua V4L2/RTSP) và Windows 10/11 (MSMF/DSHOW/RTSP), chỉ cần CPU x86_64/ARM64

**Project Type**: Monolithic Real-time Computer Vision Web Application (FastAPI Backend + Vanilla JS SPA)

**Performance Goals**: Nhịp phát hiện 3–5 FPS; tổng thời gian xử lý trung bình ~65–80 ms/chu kỳ (< 200 ms trần, p90 < 280 ms) cho tối đa 3 người đồng thời trên CPU 4 nhân; luồng MJPEG 15 FPS mượt

**Constraints**: Không dùng GPU rời; không dùng mô hình dính giấy phép AGPL-3.0 hoặc Non-commercial trong cấu hình mặc định; FAR toàn cục ≤ 1%; không hồi quy 100% nhân viên cũ đã đăng ký 5 góc

**Scale/Scope**: 20 – 200 nhân viên active trong RAM Gallery (~7–21 embeddings/người), 1 camera cổng/instance (~10 module Python cập nhật/thêm mới + UI 4 tab liên quan)

---

## Constitution Check

*GATE: Kiểm tra trước Phase 0 Research và tái xác nhận sau khi hoàn tất thiết kế Phase 1.*

| Nguyên tắc trong `constitution.md` | Trạng thái Pre-Phase 0 | Đánh giá Post-Phase 1 Design | Kết quả |
|---|---|---|---|
| **I. Precision-First Open-Set** (Không đoán tên, bắt buộc threshold + margin) | ✅ Đạt | Nhánh `profile` tách ma trận riêng trong `PoseGallery` và áp ngưỡng chặt hơn (`+0.06` threshold, `+0.02` margin), không trộn vector 0° với 90°. | **PASS** |
| **II. Defense-in-Depth Liveness** (Không ghi check-in thiếu xác thực sống) | ✅ Đạt | Nhánh `|yaw| > 40°` bắt buộc vượt đủ 3 lớp (`SpoofMedium + BodyCoupling`, `3D Motion Parallax ≥ 6 frames`, `Moiré FFT`) và gắn cờ `profile_verified` + lưu clip. | **PASS** |
| **III. CPU-First Real-Time** (< 200 ms/chu kỳ @ 3–5 FPS, tách luồng) | ✅ Đạt | Ngân sách đo lường trong `research.md` (R6) đạt ~65–76 ms cho 3 mặt đồng thời trên CPU; `YOLOX-Nano` tắt hoàn toàn khi `sleeping`. | **PASS** |
| **IV. License & Privacy** (Mặc định thương mại hóa được, tôn trọng vòng đời dữ liệu) | ✅ Đạt | Chọn `YOLOX-Nano` (Apache-2.0), `3DDFA_V2` (MIT), `EdgeFace-XS` (BSD-3-Clause) / `SFace` (Apache-2.0); loại bỏ phương án `YOLOv8/v11` (AGPL-3.0) và giữ `InsightFace` ở cờ nghiên cứu riêng. | **PASS** |
| **V. Test-First & Measurable Gates** (`pytest` + `benchmark.py` đo FAR/FRR/Spoof) | ✅ Đạt | Đã định nghĩa bộ test chuyên biệt (`test_pose3d.py`, `test_tracker_buckets.py`, `test_liveness_profile.py`) và mở rộng `/api/metrics` + `benchmark.py`. | **PASS** |

---

## Project Structure

### Documentation (this feature)

```text
specs/001-profile-90-antispoof/
├── spec.md                      # Đặc tả yêu cầu nghiệp vụ & tiêu chí nghiệm thu
├── plan.md                      # Kế hoạch kiến trúc & lộ trình triển khai (file này)
├── plan.html                    # Bản trình bày kế hoạch trực quan (Mermaid + Knowledge Graph + Ký duyệt)
├── research.md                  # Phase 0: Quyết định mô hình, căn chỉnh 3D, chống giả mạo & ngân sách CPU
├── data-model.md                # Phase 1: Lược đồ DB di trú, phân loại tư thế & máy trạng thái xác thực
├── quickstart.md                # Phase 1: Hướng dẫn cài đặt, chạy kiểm thử và kịch bản nghiệm thu
├── checklists/
│   └── requirements.md          # Checklist kiểm định chất lượng đặc tả
└── contracts/
    ├── api-and-websocket.md     # Hợp đồng REST API, WebSocket và CSV xuất báo cáo
    └── config-schema.md         # Hợp đồng cấu hình config.yaml tương thích ngược
```

### Source Code (repository root)

```text
app/
├── pose3d.py                    # [MỚI] Wrapper 3DDFA_V2 ONNX: 68 điểm 3D, góc Euler (yaw, pitch, roll), sai số homography phẳng
├── context_detector.py          # [MỚI] Wrapper YOLOX-Nano ONNX: phát hiện person + vật chứa giả mạo (điện thoại/tablet/khung ảnh) & gắn kết Đầu–Thân
├── engines.py                   # [CẬP NHẬT] Bổ sung EdgeFaceEngine + căn chỉnh 3 khung (frontal / left_profile / right_profile) từ landmark 3D
├── quality.py                   # [CẬP NHẬT] Đánh giá độ nét/ánh sáng chỉ trên vùng da mặt nhìn thấy (visible ROI), mở dải yaw tới ±90°
├── tracker.py                   # [CẬP NHẬT] Pose-Bucketed Tracker: neo thêm person_bbox, tách 3 ngăn embedding, lưu lịch sử quỹ đạo 3D
├── gallery.py                   # [CẬP NHẬT] Ma trận con theo pose_group, ngưỡng riêng cho profile, thống kê pose_coverage từng nhân viên
├── liveness.py                  # [CẬP NHẬT] Bộ điều phối chống giả mạo 4 lớp: MiniFASNet (≤40°) + Medium/Body + 3D Parallax + Moiré FFT (>40°)
├── pipeline.py                  # [CẬP NHẬT] Điều phối Best-shot selection, nâng hạng xác thực, xuất verification_tier & spoof_reason
├── enrollment.py                # [CẬP NHẬT] Tách nhóm kiểm tra outlier theo pose_group (không xóa nhầm mẫu 80°), lưu pose_group/yaw/pitch
├── guided.py                    # [CẬP NHẬT] Mở rộng phiên đăng ký hướng dẫn 7 bước (thêm nghiêng sâu trái/phải ~75°–85°) & chế độ quay bổ sung
├── db.py                        # [CẬP NHẬT] Tự động migrate các cột pose_group, yaw, verification_tier, liveness_mode, liveness_detail, spoof_reason
├── attendance.py                # [CẬP NHẬT] Bổ sung trường verification_tier cho từng dòng bảng công trong timesheet()
├── main.py                      # [CẬP NHẬT] Lifespan tự động backfill embedding model mới + mở rộng /api/status, /api/employees, /api/events, /api/metrics
├── records.py                   # [CẬP NHẬT] Lọc theo verification_tier trên /api/appearances và thêm cột mức xác thực khi xuất CSV
└── static/
    ├── index.html               # [CẬP NHẬT] Giao diện hướng dẫn 7 góc, bộ lọc mức xác thực và huy hiệu Đủ góc 90°
    ├── app.js                   # [CẬP NHẬT] Hiển thị thẻ chào phân tầng, từ điển REASON mở rộng, chi tiết lý do giả mạo và trạng thái mẫu 90°
    ├── attendance.js            # [CẬP NHẬT] Hiển thị huy hiệu mức xác thực trên bảng chi tiết chấm công theo ngày
    ├── history.js               # [CẬP NHẬT] Bộ lọc nhanh các lượt chấm công profile_verified để HR xem clip đối soát
    └── style.css                # [CẬP NHẬT] Định kiểu huy hiệu phân tầng xác thực, cảnh báo giả mạo và tiến độ 7 góc

scripts/
├── download_models.py           # [CẬP NHẬT] Tải thêm 3ddfa_v2_mb1_120x120.onnx, edgeface_xs_gamma_06.onnx, yolox_nano_coco.onnx
└── benchmark.py                 # [CẬP NHẬT] Báo cáo độ trễ từng tầng và thống kê độ chính xác tách theo dải góc yaw

tests/
├── test_camera.py               # [GIỮ NGUYÊN] Kiểm thử V4L2/camera Linux & vẽ nhãn tiếng Việt
├── test_guided.py               # [CẬP NHẬT] Kiểm thử cả chế độ 5 bước và 7 bước có góc nghiêng sâu ±80°
├── test_pose3d.py               # [MỚI] Kiểm thử phân nhóm tư thế, hysteresis và căn chỉnh 3 khung
├── test_tracker_buckets.py      # [MỚI] Kiểm thử không trộn vector khi xoay đầu 0°↔90° và ưu tiên Best-shot
└── test_liveness_profile.py     # [MỚI] Kiểm thử chặn điện thoại/2 mặt trên 1 thân, chặn ảnh phẳng không thị sai 3D và duyệt quỹ đạo 3D thật
```

**Structure Decision**: Giữ nguyên cấu trúc phẳng, gọn nhẹ đặc trưng của dự án `app/` hiện tại (mỗi module đảm nhiệm trọn vẹn một trách nhiệm rõ ràng, không tạo tầng thư mục lồng nhau dư thừa), bổ sung đúng **2 module chuyên biệt mới** (`app/pose3d.py` và `app/context_detector.py`) để cô lập hoàn toàn suy luận ONNX mới và dễ dàng mock/kiểm thử độc lập.

---

## Phased Implementation Roadmap (Định hướng cho `/speckit-tasks`)

1. **Giai đoạn A — Nền tảng Mô hình 3D Pose, Căn chỉnh 3 Khung & Di trú DB**:
   - Cập nhật `scripts/download_models.py`, `app/config.py`, `config.yaml` và `app/db.py` (`_migrate`).
   - Xây dựng `app/pose3d.py` (`3DDFA_V2`) + nâng cấp `app/quality.py` (visible skin ROI) và `app/engines.py` (3 khung căn chỉnh + `EdgeFaceEngine`).
   - Viết và vượt qua `tests/test_pose3d.py`.
2. **Giai đoạn B — Pose-Bucketed Tracker, Gallery Đa góc & Best-Shot Matching**:
   - Nâng cấp `app/gallery.py` (ma trận tách theo `pose_group`, ngưỡng riêng `profile_threshold`/`profile_margin`, `pose_coverage`).
   - Nâng cấp `app/tracker.py` (3 ngăn embedding, liên kết `person_bbox`, không cắt track khi xoay đầu).
   - Viết và vượt qua `tests/test_tracker_buckets.py`.
3. **Giai đoạn C — Chống giả mạo Đa lớp Không phụ thuộc Góc nhìn (`> 40°`)**:
   - Xây dựng `app/context_detector.py` (`YOLOX-Nano`: phát hiện `person` + vật chứa màn hình/khung ảnh + kiểm tra tỷ lệ Đầu–Cơ thể).
   - Nâng cấp `app/liveness.py` hợp nhất `MiniFASNet` (nhánh ≤ 40°) với `Context + 3D Motion Parallax + Moiré FFT` (nhánh > 40°).
   - Tích hợp máy trạng thái xác thực và `verification_tier` vào `app/pipeline.py`.
   - Viết và vượt qua `tests/test_liveness_profile.py`.
4. **Giai đoạn D — Đăng ký Hướng dẫn 7 Bước, Giao diện Hậu kiểm HR & Benchmark**:
   - Nâng cấp `app/enrollment.py` (lọc outlier theo từng `pose_group`) và `app/guided.py` (7 bước + chế độ quay bổ sung 2 góc nghiêng cho nhân viên cũ).
   - Cập nhật `app/main.py`, `app/records.py`, UI `app/static/*` và `scripts/benchmark.py`.
   - Chạy toàn bộ `pytest` và kiểm thử tích hợp end-to-end.

---

## Complexity Tracking

> Không có vi phạm nào đối với `constitution.md` (tất cả 5 cổng kiểm tra đều PASS). Bảng dưới đây ghi nhận lý do bổ sung 2 mô hình ONNX nhẹ để đảm bảo tính minh bạch kiến trúc:

| Thành phần bổ sung | Mục đích bắt buộc | Vì sao giải pháp đơn giản hơn bị loại bỏ |
|---|---|---|
| `3DDFA_V2` (~2 ms/mặt) | Lấy góc `yaw` thực tới ±90°, căn chỉnh khi khuất 1 mắt và đo thị sai 3D qua chuỗi frame | 5 điểm mốc 2D của `YuNet` bị chập lại khi góc > 65°, không phân biệt được mặt 3D thật với ảnh phẳng 2D |
| `YOLOX-Nano` (~10 ms/frame) | Bắt điện thoại/tablet cầm ngang mặt và ràng buộc 1 mặt – 1 cơ thể ở góc 90° | Ở góc 90°, `MiniFASNet` báo sai hàng loạt trên người thật; nếu không quét vật chứa và cơ thể thì ảnh/video nghiêng trên điện thoại sẽ lọt |

