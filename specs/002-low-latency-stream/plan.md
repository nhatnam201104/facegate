# Implementation Plan: Tối ưu độ trễ và hiệu năng luồng hiển thị video trực tiếp cho triển khai máy chủ

**Branch**: `002-low-latency-stream` | **Date**: 2026-11-12 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/002-low-latency-stream/spec.md`

---

## Summary

Giải quyết triệt để tình trạng luồng hình trực tiếp trên màn hình bị giật cục (*stutter*) và chậm hơn rõ rệt so với ứng dụng xem gốc của camera, đồng thời chuẩn hóa kiến trúc phân phối hình ảnh để khi đưa **FaceGate** lên máy chủ từ xa (Linux/Windows Server qua mạng LAN/VPN hoặc sau Reverse Proxy) không xảy ra hiện tượng trễ dồn (*latency drift*) hay quá tải CPU.

Giải pháp kỹ thuật cốt lõi (phân tích chi tiết tại [research.md](research.md)) gồm **4 trụ cột không thêm bất kỳ thư viện phụ thuộc nặng nào**:
1. **Tách rời hoàn toàn Luồng Xuất hình `StreamComposer` (24 FPS) khỏi Luồng Suy luận AI `Pipeline` (5 FPS)**: `Pipeline` chỉ xuất bản đối tượng bất biến `OverlaySnapshot` nguyên tử sau mỗi chu kỳ nhận diện; `StreamComposer` đọc khung hình mới nhất từ `Camera` và ghép `OverlaySnapshot` ở tốc độ 24 FPS hoàn toàn không khóa chờ ONNX Runtime.
2. **Tối ưu Vẽ Nhãn Tiếng Việt bằng `LabelSpriteCache` Cắt Dán Vùng ROI (~0.25 ms/frame)**: Thay thế việc chuyển đổi `BGR ↔ RGB ↔ Pillow` trên toàn khung hình `960×540` (~1.55 MB × 3 mảng) bằng bộ nhớ đệm LRU cho từng mảnh chữ nhỏ (`~200×24 px`), tăng tốc `draw_overlay()` gấp **20–35 lần** mà vẫn giữ nguyên dấu tiếng Việt sắc nét trên cả luồng trực tiếp lẫn clip lịch sử.
3. **Phân phối Khung hình Bất đồng bộ `AsyncIO FrameHub` + Cờ RTSP Low-Delay + Anti-Buffering Headers**: Nén JPEG (`quality=72`) tối đa **1 lần duy nhất** mỗi nhịp cho toàn bộ người xem `/video.mjpg` (và tương tự cho `/video_raw.mjpg`), loại bỏ `run_in_threadpool` trên từng frame, tự động nhảy cóc qua frame cũ cho client mạng chậm (*Latest-Frame Drop*), tự ngắt nén `0% CPU` khi `viewer_count == 0`, đồng thời bổ sung cờ FFmpeg `nobuffer`/`low_delay` và HTTP header `X-Accel-Buffering: no`.
4. **Đồng bộ Vòng đời Kết nối Theo Tab & Hiển thị (`syncVideoStreams`)**: Tự động ngắt socket MJPEG ngay khi người dùng rời tab có video hoặc ẩn cửa sổ trình duyệt, và kết nối thẳng vào khoảnh khắc hiện tại ngay khi mở lại.

---

## Technical Context

**Language/Version**: Python 3.10+ (đã kiểm chứng trên Python 3.12 Linux & Windows)

**Primary Dependencies**: FastAPI, Uvicorn, OpenCV (`opencv-python-headless>=4.8,<5` trên Linux, `opencv-python>=4.8,<5` trên Windows), ONNX Runtime (`CPUExecutionProvider`), NumPy, Pillow, PyYAML *(Không thêm package bên ngoài mới)*

**Storage**: Không thay đổi lược đồ SQLite `data/facegate.db`; chỉ bổ sung các cấu trúc đồng bộ hóa trong RAM (`OverlaySnapshot`, `FrameHub`, `LabelSpriteCache`) và khóa cấu hình tương thích ngược trong `config.yaml`

**Testing**: `pytest` (unit + async streaming concurrency tests trong `tests/test_stream_latency.py` + trọn bộ 6 file test hiện hữu) & `scripts/benchmark.py --stream-bench`

**Target Platform**: Linux Server (Ubuntu/Debian Headless qua RTSP/V4L2, hỗ trợ đứng sau Nginx/Caddy/Ingress) và Windows 10/11 (MSMF/DSHOW/RTSP), chạy thuần CPU phổ thông

**Project Type**: Monolithic Real-time Computer Vision Web Application (FastAPI Async Backend + Vanilla JS SPA)

**Performance Goals**:
- Luồng hiển thị đạt **24 FPS** ổn định (mặc định mới, cấu hình được `1..60 FPS`); sụt giảm **≤ 15%** khi có 3 người đồng thời trong khung hình
- Độ trễ đầu-cuối trên LAN đạt **~65–120 ms** (mục tiêu p50 **≤ 250 ms**, p95 **≤ 400 ms**, chênh lệch với ứng dụng camera gốc **≤ 300 ms**)
- Thời gian `draw_overlay + JPEG encode` **< 3.5 ms/frame** trên CPU 4 nhân
- Tăng từ 1 lên 5 người xem đồng thời chỉ tăng **< 2–5% CPU** (mục tiêu **≤ 10 điểm %**); khi `0` người xem và không có người qua cửa, tải nén JPEG giảm về **0%**

**Constraints**: Không dùng GPU rời; không cài đặt WebRTC/PyAV native stack nặng; không làm giảm độ chính xác nhận diện góc ±90°, chống giả mạo 4 lớp, cơ chế vùng cửa Chế độ chờ hay chất lượng clip bằng chứng WebM/MP4

**Scale/Scope**: 1–5 màn hình xem trực tiếp đồng thời trên mỗi instance cổng camera; tác động gọn sạch trên **1 module mới** (`app/stream.py`), **6 module cập nhật** (`app/draw.py`, `app/camera.py`, `app/pipeline.py`, `app/main.py`, `app/config.py`, `app/static/app.js`), `config.yaml`, `scripts/benchmark.py` và **1 bộ kiểm thử mới** (`tests/test_stream_latency.py`)

---

## Constitution Check

*GATE: Kiểm tra trước Phase 0 Research và tái xác nhận sau khi hoàn tất thiết kế Phase 1.*

| Nguyên tắc trong `constitution.md` | Trạng thái Pre-Phase 0 | Đánh giá Post-Phase 1 Design | Kết quả |
|---|---|---|---|
| **I. Precision-First Open-Set** (Không đoán tên, bắt buộc threshold + margin) | ✅ Đạt | Luồng suy luận `_process`, `Gallery` và `IoUTracker` giữ nguyên 100% toán học so khớp và ngưỡng Open-Set. | **PASS** |
| **II. Defense-in-Depth Liveness** (Không ghi check-in thiếu xác thực sống) | ✅ Đạt | Toàn bộ 4 lớp chống giả mạo (`MiniFASNet`, `YOLOX-Nano`, `3D Parallax`, `Moiré FFT`) và luồng ghi clip bằng chứng không bị cắt giảm. | **PASS** |
| **III. CPU-First Real-Time** (< 200 ms/chu kỳ @ 3–5 FPS, tách luồng camera / MJPEG / clip khỏi AI) | ✅ Đạt | Thực hiện triệt để tinh thần của Nguyên tắc III: tách hẳn `StreamComposer` + `AsyncIO FrameHub` khỏi thread suy luận AI, giảm chi phí vẽ tiếng Việt ~25×, trả lại ~10–15 ms CPU mỗi chu kỳ cho hệ thống. | **PASS** |
| **IV. License & Privacy** (Mặc định thương mại hóa được, tôn trọng vòng đời dữ liệu) | ✅ Đạt | Không thêm bất kỳ thư viện hay mô hình mới nào; giữ nguyên cơ chế tự động dọn clip theo `retention_days` và xóa dữ liệu sinh trắc học. | **PASS** |
| **V. Test-First & Measurable Gates** (`pytest` + `benchmark.py` đo kiểm định lượng) | ✅ Đạt | Bổ sung `tests/test_stream_latency.py` kiểm chứng tách luồng, Latest-Frame Drop, Zero-Viewer Idle, Sprite Cache và cờ `--stream-bench` trong `scripts/benchmark.py`. | **PASS** |

---

## Project Structure

### Documentation (this feature)

```text
specs/002-low-latency-stream/
├── spec.md                      # Đặc tả yêu cầu nghiệp vụ & tiêu chí nghiệm thu
├── plan.md                      # Kế hoạch kiến trúc & lộ trình triển khai (file này)
├── plan.html                    # Báo cáo kế hoạch tương tác (Mermaid + Knowledge Graph + Ký duyệt)
├── research.md                  # Phase 0: Chẩn đoán 6 nút thắt gây lag & quyết định kiến trúc
├── data-model.md                # Phase 1: Thực thể đồng bộ RAM (OverlaySnapshot, FrameHub, SpriteCache)
├── quickstart.md                # Phase 1: Hướng dẫn chạy kiểm thử, benchmark và nghiệm thu mạng Server
├── checklists/
│   └── requirements.md          # Checklist kiểm định chất lượng đặc tả (16/16 PASS)
└── contracts/
    ├── api-and-stream.md        # Hợp đồng HTTP MJPEG headers, /api/status và vòng đời trình duyệt
    └── config-schema.md         # Hợp đồng cấu hình config.yaml tương thích ngược
```

### Source Code (repository root)

```text
app/
├── stream.py                    # [MỚI] OverlayBox, OverlaySnapshot, AsyncIO FrameHub (live/raw) & StreamComposer (24 FPS on-demand)
├── draw.py                      # [CẬP NHẬT] LabelSpriteCache (LRU 256) vẽ nhãn tiếng Việt trên lát cắt ROI thay vì copy toàn khung BGR↔RGB
├── camera.py                    # [CẬP NHẬT] Cờ FFmpeg RTSP low-latency (nobuffer, low_delay), read_latest() kèm frame_ts, đồng bộ idle với raw viewer
├── pipeline.py                  # [CẬP NHẬT] Tách _render/_publish khỏi vòng lặp _run, xuất bản OverlaySnapshot bất biến, mở rộng status()
├── config.py                    # [CẬP NHẬT] Chuẩn hóa mặc định stream_fps=24, stream_jpeg_quality=72, interpolate_boxes, rtsp_low_latency
├── main.py                      # [CẬP NHẬT] Quản lý vòng đời StreamComposer, phục vụ /video.mjpg & /video_raw.mjpg qua FrameHub + anti-buffering headers
└── static/
    └── app.js                   # [CẬP NHẬT] Hàm syncVideoStreams() ngắt/mở stream theo tab & visibilitychange, hiển thị stream FPS, tự phục hồi kết nối

config.yaml                      # [CẬP NHẬT] Cập nhật tham số mặc định cho luồng stream độ trễ thấp sẵn sàng lên server

scripts/
└── benchmark.py                 # [CẬP NHẬT] Bổ sung chế độ --stream-bench đo tốc độ ROI overlay, JPEG encode và FPS đầu ra

tests/
├── test_stream_latency.py       # [MỚI] Kiểm thử tách luồng khi AI chậm, Latest-Frame Drop, Zero-Viewer Idle, Sprite Cache & HTTP headers
├── test_camera.py               # [GIỮ NGUYÊN / MỞ RỘNG NHẸ] Kiểm thử cờ RTSP low-latency & tương thích draw_overlay
├── test_api_contracts.py        # [CẬP NHẬT NHẸ] Xác nhận khóa mới stream_fps, stream_target_fps, stream_viewers trong status
├── test_pose3d.py               # [GIỮ NGUYÊN] Đảm bảo không hồi quy ước lượng tư thế 3D
├── test_tracker_buckets.py      # [GIỮ NGUYÊN] Đảm bảo không hồi quy bám vết đa góc
├── test_liveness_profile.py     # [GIỮ NGUYÊN] Đảm bảo không hồi quy chống giả mạo 4 lớp
└── test_guided.py               # [GIỮ NGUYÊN] Đảm bảo không hồi quy đăng ký hướng dẫn 7 bước
```

**Structure Decision**: Tuân thủ cấu trúc module phẳng, đơn trách nhiệm của thư mục `app/` hiện có (được phản ánh trong `docs/KNOWLEDGE_GRAPH.md`), chỉ bổ sung duy nhất **1 module chuyên trách `app/stream.py`** để gom toàn bộ logic đồng bộ luồng hiển thị (`StreamComposer`, `FrameHub`, `OverlaySnapshot`), giữ cho `app/pipeline.py` gọn sạch đúng vai trò xử lý nhận diện & nghiệp vụ chấm công.

---

## Phased Implementation Roadmap (Định hướng cho `/speckit-tasks`)

1. **Giai đoạn A — Tối ưu Thu nhận Nguồn hình (`camera.py`) & Vẽ Nhãn Tiếng Việt Tốc độ cao (`draw.py`)**:
   - Cập nhật `config.yaml` và `app/config.py` bổ sung `stream_fps: 24`, `stream_jpeg_quality: 72`, `interpolate_boxes: true`, `rtsp_low_latency: true`, `grab_drain: true`.
   - Nâng cấp `app/camera.py` hỗ trợ cấu hình FFmpeg RTSP độ trễ thấp, phương thức `read_latest()` trả kèm `frame_ts` và cơ chế giữ thức khi có người xem preview.
   - Tái cấu trúc `app/draw.py` sử dụng `LabelSpriteCache` (LRU) dán trực tiếp vùng chữ lên mảng BGR với kiểm tra cắt biên an toàn.
   - Viết unit test kiểm tra hiệu năng và biên cắt của `draw_overlay`.
2. **Giai đoạn B — Tách Luồng `StreamComposer` & Khung hình Dùng chung `FrameHub` (`stream.py` + `pipeline.py`)**:
   - Xây dựng `app/stream.py` gồm `OverlayBox`, `OverlaySnapshot`, `FrameHub` (hỗ trợ `asyncio` pub/sub không dùng threadpool, đếm `viewer_count` chính xác) và `StreamComposer` (chạy 24 FPS, hỗ trợ ngoại suy ngắn hạn vị trí hộp mặt, đẩy `ClipRecorder` độc lập và nghỉ `0% CPU` khi không có người xem/clip).
   - Cập nhật `app/pipeline.py` gỡ bỏ việc tự gọi `cv2.resize`/`draw_overlay`/`cv2.imencode` trong vòng lặp suy luận; thay bằng xuất bản `OverlaySnapshot` và bổ sung `stream_fps`, `stream_target_fps`, `stream_viewers` vào `status()`.
3. **Giai đoạn C — Endpoint Streaming Chuẩn Server & Chống Đệm Proxy (`main.py`)**:
   - Kết nối `StreamComposer` vào `lifespan` của FastAPI trong `app/main.py` (bao gồm cập nhật tự động khi đổi nguồn qua `POST /api/source`).
   - Chuyển `/video.mjpg` và `/video_raw.mjpg` sang đọc bất đồng bộ từ `live_hub` và `raw_hub` theo cơ chế *Latest-Frame Drop*, đính kèm bộ headers chống đệm (`Cache-Control: no-store...`, `X-Accel-Buffering: no`).
4. **Giai đoạn D — Quản lý Vòng đời Stream trên Trình duyệt (`app.js`), Kiểm thử & Benchmark**:
   - Cập nhật `app/static/app.js`: bổ sung `syncVideoStreams()` lắng nghe chuyển tab và `document.visibilitychange`, hiển thị `stream FPS` trên `#statusLine`, tự động làm mới luồng khi nguồn video khôi phục.
   - Hoàn thiện `tests/test_stream_latency.py` và mở rộng `scripts/benchmark.py --stream-bench`.
   - Chạy toàn bộ `pytest` xác nhận 100% bài kiểm thử vượt qua không hồi quy.

---

## Complexity Tracking

> Không có bất kỳ vi phạm nào đối với `constitution.md` (tất cả 5 cổng kiểm tra đều **PASS** và không cài thêm thư viện ngoài). Bảng dưới đây ghi nhận quyết định tách module `app/stream.py` để minh bạch thiết kế:

| Thành phần kiến trúc | Mục đích kỹ thuật | Vì sao phương án đơn giản hơn bị loại bỏ |
|---|---|---|
| `app/stream.py` (`StreamComposer` + `FrameHub`) | Tách nhịp xuất hình 24 FPS khỏi nhịp AI 5 FPS và chia sẻ 1 bản nén JPEG qua `asyncio` cho N người xem | Giữ chung trong `Pipeline._run` khiến video luôn bị đứng hình 100–200 ms mỗi lần chạy ONNX; còn dùng `run_in_threadpool` cho từng client làm cạn worker thread khi mở nhiều tab trên server |
| `LabelSpriteCache` trong `app/draw.py` | Giảm thời gian vẽ chữ tiếng Việt từ ~8 ms xuống ~0.25 ms/frame | Vẽ chữ bằng HTML/SVG thuần ở client sẽ làm mất chữ tiếng Việt trong các file clip bằng chứng `.webm`/`.mp4` lưu cho HR hậu kiểm |
