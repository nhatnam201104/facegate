#### Summary

Implement **FaceGate v4** PoC — CPU-only face-attendance gate demo structured with `server/` (organized by domain) and `webapp/` at the repository root, including passive anti-spoofing, guided 5-angle enrollment, attendance tracking, appearance clip recording, idle sleep mode, and HRM outbox integration:

1. **Vision & AI Pipeline** (`server/vision/`, `server/models/`):
   - Support `opencv` backend (YuNet face detection + SFace recognition, Apache-2.0) by default and `onnx` backend (SCRFD-500M + ArcFace MobileFaceNet) for benchmarking (`engines.py`).
   - Multi-face IoU tracking (`tracker.py`), frame quality gate (`quality.py`), passive anti-spoofing with `MiniFASNetV2` + `MiniFASNetV1SE` (`liveness.py`), Vietnamese overlay rendering (`draw.py`), and threaded camera/RTSP/video capture (`camera.py`).
   - Real-time processing loop with idle sleep mode, configurable polygon motion zone (`motion_zone`), confirmation window, and per-track `.webm` clip recording with pre/post-roll buffers (`pipeline.py`, `clips.py`).

2. **Enrollment, Attendance, Store & API** (`server/enrollment/`, `server/attendance/`, `server/store/`, `server/api/`, `server/main.py`, `server/config.py`):
   - Guided 5-angle cross-pose camera enrollment (`guided.py`), video-upload enrollment with pose-bin deduplication (`video.py`), and margin-based RAM gallery matching (`gallery.py`).
   - Daily first-check-in attendance calculation, late minutes with configurable grace period, absent/not-arrived tracking, and outbox-backed `HRMWorker` supporting `MockHRM` and `ERPNextHRM` (`shift.py`, `hrm.py`).
   - SQLite WAL storage with automatic schema migration (`db.py`) and FastAPI REST/WebSocket/MJPEG endpoints (`main.py`, `api/records.py`, `api/state.py`).

3. **Web UI, Scripts, Docs & Tests** (`webapp/`, `server/scripts/`, `server/tests/`, `setup.sh`, `run.sh`, `setup.bat`, `run.bat`, `docs/`):
   - 6-tab web dashboard (**Trực tiếp**, **Chấm công**, **Lịch sử**, **Đăng ký**, **Nhân viên**, **Sự kiện & đánh giá**) with live MJPEG stream, WebSocket updates, interactive motion-zone polygon editor, and dynamic source switching.
   - Linux/Windows setup & run scripts, model downloader (`server/scripts/download_models.py`), benchmark script (`server/scripts/benchmark.py`), documentation (`README.md`, `docs/*`), unit tests (`server/tests/test_camera.py`, `server/tests/test_guided.py`), `AGENTS.md`, and `.github/PULL_REQUEST_TEMPLATE.md` synced from `msw-chat`.

#### QA Steps

1. Run unit tests (`pytest -v`) and verify all 12 tests in `server/tests/test_camera.py` and `server/tests/test_guided.py` pass.
2. Run `./setup.sh` (or `setup.bat` on Windows) to install dependencies and download ONNX models into `server/models/`, then start the server with `./run.sh` (or `run.bat`) and open `http://127.0.0.1:8090`.
3. In **Đăng ký**, enroll a test employee using either guided 5-angle camera capture or a short video upload, then click **Duyệt** in **Nhân viên**.
4. Return to **Trực tiếp** and verify real-time face recognition, greeting banner, on-time/late status, and anti-spoofing alert (`GIẢ MẠO`) when presenting a phone/printed photo.
5. Verify **Chấm công** (daily attendance table + CSV export), **Lịch sử** (recorded `.webm` clips + ZIP export), **Sự kiện & đánh giá** (event labeling + metrics + CSV export), and polygon **Vùng cửa** saving/resetting.

#### Release Note
```release-note
Added FaceGate v4 PoC organized under server/ (api, vision, enrollment, attendance, store, models, scripts, tests) and webapp/, featuring FastAPI REST/WebSocket/MJPEG endpoints, dual-backend face detection/recognition, MiniFASNet anti-spoofing, guided 5-angle enrollment, attendance calculation, clip recording, HRM outbox integration, setup scripts, docs, unit tests, AGENTS.md, and PR template.
```
