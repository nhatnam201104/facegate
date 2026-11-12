---

description: "Danh sách tác vụ triển khai cho tính năng Tối ưu độ trễ và hiệu năng luồng hiển thị video trực tiếp cho triển khai máy chủ"
---

# Tasks: Tối ưu độ trễ và hiệu năng luồng hiển thị video trực tiếp cho triển khai máy chủ

**Input**: Tài liệu thiết kế từ `/specs/002-low-latency-stream/` (`plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/api-and-stream.md`, `contracts/config-schema.md`, `quickstart.md`)

**Prerequisites**: `plan.md` (bắt buộc), `spec.md` (4 User Stories: US1 [P1], US2 [P1], US3 [P2], US4 [P3]), `research.md`, `data-model.md`, `contracts/`

**Tests**: Có bao gồm kiểm thử tự động (`pytest`) trước từng giai đoạn theo Nguyên tắc V của `.specify/memory/constitution.md` (*Test-First & Measurable Gates*) và yêu cầu kiểm chứng tại `quickstart.md`.

**Organization**: Các tác vụ được tổ chức theo từng User Story độc lập để mỗi giai đoạn tạo thành một lát cắt giá trị hoàn chỉnh, có thể kiểm thử và bàn giao riêng biệt.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Có thể thực thi song song (khác file, không phụ thuộc tác vụ chưa hoàn thành trong cùng pha)
- **[Story]**: Nhãn User Story tương ứng (`[US1]`, `[US2]`, `[US3]`, `[US4]`) — chỉ gắn ở các pha User Story
- Mọi tác vụ đều chỉ định đường dẫn file chính xác tính từ thư mục gốc dự án

---

## Phase 1: Setup (Cấu hình Luồng Phát & Tham số Tương thích Ngược)

**Purpose**: Chuẩn hóa các khóa cấu hình luồng hiển thị độ trễ thấp trong `config.yaml` và bộ chuẩn hóa mặc định tương thích ngược trong `app/config.py`.

- [ ] T001 [P] Bổ sung và ràng buộc miền hợp lệ (clamp) các tham số cấu hình mới trong `_apply_defaults()` của `app/config.py`: `camera.rtsp_low_latency: bool = True`, `camera.grab_drain: bool = True`, `pipeline.stream_fps: float = 24.0` (clamp `[1.0, 60.0]`), `pipeline.stream_width: int = 960` (clamp `[320, 1920]`), `pipeline.stream_jpeg_quality: int = 72` (clamp `[40, 95]`) và `pipeline.interpolate_boxes: bool = True`
- [ ] T002 [P] Cập nhật file cấu hình trung tâm `config.yaml` với các khóa mặc định mới (`camera.rtsp_low_latency: true`, `camera.grab_drain: true`, nâng `pipeline.stream_fps` từ `15` lên `24`, thêm `pipeline.stream_jpeg_quality: 72` và `pipeline.interpolate_boxes: true`) kèm chú thích hướng dẫn tinh chỉnh khi đưa lên máy chủ LAN/VPN

---

## Phase 2: Foundational (Hạ tầng Thu nhận Độ trễ Thấp & Thực thể Phân phối Khung hình)

**Purpose**: Nâng cấp tầng thu nhận `Camera` giảm đệm đầu vào và xây dựng các cấu trúc dữ liệu đồng bộ nền tảng (`OverlayBox`, `OverlaySnapshot`, `FrameHub`) dùng chung cho mọi User Story.

**⚠️ CRITICAL**: Không bắt đầu bất kỳ User Story nào trước khi hoàn tất Pha này.

- [ ] T003 [P] Nâng cấp `app/camera.py` để giảm độ trễ thu nhận đầu vào: (1) cấu hình cờ FFmpeg RTSP độ trễ thấp `rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|max_delay;250000` khi `rtsp_low_latency=True` và môi trường chưa ghi đè `OPENCV_FFMPEG_CAPTURE_OPTIONS`; (2) hỗ trợ `grab_drain` xả frame cũ để giữ duy nhất `LatestCameraFrame` mới nhất; (3) bổ sung phương thức `read_latest() -> tuple[int, float, np.ndarray | None]` trả đồng thời `(frame_id, frame_ts, frame)` (giữ nguyên `read() -> tuple[int, np.ndarray | None]` tương thích ngược) và thuộc tính `preview_active: bool = False` để không giảm tốc giải mã xuống `idle_interval` khi có người xem preview
- [ ] T004 [P] Khởi tạo mô-đun `app/stream.py` với các thực thể nền tảng theo `data-model.md`: `@dataclass(frozen=True) OverlayBox` (`track_id: int`, `bbox: tuple[float, float, float, float]`, `vx: float`, `vy: float`, `color: tuple[int, int, int]`, `label: str`, `updated_at: float`), `@dataclass(frozen=True) OverlaySnapshot` (`version: int`, `created_at: float`, `sleeping: bool`, `boxes: tuple[OverlayBox, ...]`, `line: tuple[tuple[float, float], tuple[float, float]] | None`, `motion_zone: tuple[tuple[float, float], ...] | None`, `hud: str`), hằng số `STREAM_HEADERS` (`Cache-Control: no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0`, `Pragma: no-cache`, `Expires: 0`, `X-Accel-Buffering: no`) và lớp `FrameHub` (`name: str` thuộc `{"live", "raw"}`, `seq: int`, `ts: float`, `jpeg: bytes | None`, `fps_out: float`, `viewer_count: int >= 0`) đánh thức các coroutine chờ qua `loop.call_soon_threadsafe` không chiếm giữ `ThreadPoolExecutor`
- [ ] T005 [P] Mở rộng kiểm thử đơn vị trong `tests/test_camera.py` và `tests/test_api_contracts.py` để xác minh: cấu hình mặc định & clamp miền giá trị của `_apply_defaults()`, phương thức `Camera.read_latest()` trả đúng `frame_ts` tăng đơn điệu và hành vi cờ `rtsp_low_latency` / `preview_active`

**Checkpoint**: Tầng thu nhận camera độ trễ thấp và `FrameHub` bất đồng bộ đã sẵn sàng — có thể bắt đầu triển khai các User Stories.

---

## Phase 3: User Story 1 - Xem hình ảnh trực tiếp mượt mà, bám sát thời gian thực ngay cả khi đang nhận diện nhiều người (Priority: P1) 🎯 MVP

**Goal**: Tách rời hoàn toàn chu kỳ dựng/xuất hình `24 FPS` khỏi chu kỳ suy luận AI `5 FPS` và tối ưu vẽ nhãn tiếng Việt bằng bộ nhớ đệm ROI Sprite để luồng Trực tiếp luôn mượt, độ lệch dưới `300 ms` so với camera gốc ngay cả khi có 1–3 người đồng thời trong khung hình.

**Independent Test**: Chạy luồng video trực tiếp khi có 1–3 người đi qua (hoặc giả lập `_process` tốn `220 ms`); xác nhận video vẫn phát mượt `≥ 20–24 FPS`, thời gian vẽ nhãn tiếng Việt `< 1.0 ms/frame` và toàn bộ nhận diện ±90° / chống giả mạo / ghi clip lịch sử hoạt động nguyên vẹn.

### Tests for User Story 1 ⚠️

> **NOTE: Viết các bài kiểm thử này TRƯỚC, xác nhận FAIL trước khi viết code triển khai**

- [ ] T006 [P] [US1] Tạo file `tests/test_stream_latency.py` với các bài kiểm thử cho US1: (1) kiểm thử `LabelSpriteCache` trong `draw_overlay()` vẽ đúng chữ tiếng Việt có dấu, tự động cắt biên an toàn (clamp không ném `IndexError`/`ValueError` khi `x1 < 0` hoặc `x1 + sprite_w > W` hoặc `ty + sprite_h > H`) và đạt thời gian trung bình `< 1.0 ms/frame` trên ảnh `960×540` sau khi ấm cache; (2) kiểm thử `StreamComposer` vẫn xuất đều đặn `≥ 20 FPS` trong lúc luồng suy luận AI bị giữ bận `220 ms/chu kỳ`

### Implementation for User Story 1

- [ ] T007 [P] [US1] Tối ưu hóa `app/draw.py` bằng `LabelSpriteCache` (bộ nhớ đệm LRU `maxsize=256` theo khóa `(text, bg_color_bgr, font_size)`): chỉ dùng Pillow dựng mảng BGR nhỏ kích thước đúng bằng hộp nhãn/HUD rồi gán trực tiếp lên lát cắt ROI của khung hình đích với kiểm tra cắt biên an toàn tại cả 4 cạnh, loại bỏ hoàn toàn phép chuyển đổi `COLOR_BGR2RGB` / `COLOR_RGB2BGR` toàn khung hình mà vẫn giữ nguyên chữ ký hàm `draw_overlay(frame, boxes, hud, line=None)`
- [ ] T008 [US1] Cài đặt lớp `StreamComposer` trong `app/stream.py` chạy trên daemon thread riêng ở nhịp `pipeline.stream_fps` (`24 FPS`): đọc `camera.read_latest()`, ghép `OverlaySnapshot` mới nhất từ `Pipeline` (hỗ trợ ngoại suy mượt vị trí hộp mặt theo vận tốc `(vx, vy)` giới hạn tối đa `≤ 120 ms` khi `interpolate_boxes=True`, vẽ khung mờ vùng cửa khi `sleeping=True`), đẩy khung hình đã vẽ sang `ClipRecorder.push()` theo nhịp `clips.fps` và nén JPEG (`cv2.IMWRITE_JPEG_QUALITY = stream_jpeg_quality`) đẩy vào `live_hub`
- [ ] T009 [US1] Tái cấu trúc `app/pipeline.py` để gỡ bỏ hoàn toàn việc gọi trực tiếp `cv2.resize`, `draw_overlay` và `cv2.imencode` khỏi vòng lặp suy luận `_run()`: trích xuất vận tốc `(vx, vy)` từ `Track.vel`, tạo và xuất bản nguyên tử `OverlaySnapshot` bất biến ở cuối mỗi chu kỳ `_process()`, `_sleep()` và `_wake()`, đồng thời cung cấp phương thức `overlay_snapshot()` cho `StreamComposer` đọc không khóa

**Checkpoint**: User Story 1 hoàn tất — luồng hiển thị trực tiếp đạt độ mượt thời gian thực `24 FPS` hoàn toàn độc lập với tải suy luận ONNX.

---

## Phase 4: User Story 2 - Vận hành trên máy chủ từ xa không bị trễ dồn theo thời gian hoặc quá tải khi nhiều người cùng xem (Priority: P1)

**Goal**: Phân phối 1 khung hình JPEG nén dùng chung cho mọi người xem qua `asyncio`, tự động bỏ qua khung hình trễ cho người xem mạng yếu (*Latest-Frame Drop*), gắn HTTP headers chống đệm Reverse Proxy và tự ngưng nén JPEG (`0% CPU`) khi không có trình duyệt nào đang mở xem.

**Independent Test**: Mở đồng thời 5 kết nối `/video.mjpg` (trong đó 1 kết nối đọc chậm `250 ms/lần`), kiểm tra mỗi khung hình chỉ nén JPEG đúng 1 lần, client chậm lập tức nhảy tới `seq` mới nhất không bị trễ dồn, và khi đóng hết các tab xem video thì `viewer_count == 0` và số lần gọi `cv2.imencode` giảm về `0`.

### Tests for User Story 2 ⚠️

- [ ] T010 [P] [US2] Mở rộng `tests/test_stream_latency.py` với bộ kiểm thử cho US2: (1) *Latest-Frame Drop* — client chậm ngủ `250 ms` giữa hai lần đọc nhận ngay `seq` mới nhất (bỏ qua các frame trung gian) và không làm chậm client nhanh chạy song song; (2) *Shared Broadcast Encoding* — 5 người xem đồng thời chỉ kích hoạt đúng 1 lần nén JPEG mỗi nhịp frame; (3) *Zero-Viewer Idle* — khi `live_hub.viewer_count == 0` và `raw_hub.viewer_count == 0`, hàm nén JPEG không chạy lần nào nhưng `ClipRecorder` vẫn nhận đủ frame khi có sự kiện người đi qua; (4) endpoint `/video.mjpg` trả đầy đủ `STREAM_HEADERS` (`X-Accel-Buffering: no`, `Cache-Control: no-store...`)

### Implementation for User Story 2

- [ ] T011 [US2] Hoàn thiện cơ chế điều tiết theo nhu cầu (*Demand-Driven Idle & Shared Broadcast*) trong `FrameHub.stream_mjpeg()` và `StreamComposer._run()` tại `app/stream.py`: quản lý chính xác `viewer_count` trong cặp `try / finally` của async generator, nhảy thẳng tới `self.seq` mới nhất sau mỗi lần `yield` multipart chunk, chỉ gọi `cv2.imencode` cho `live_hub` khi `live_hub.viewer_count > 0`, chỉ dựng ảnh cho `ClipRecorder` ở nhịp `clips.fps` khi `viewer_count == 0` mà `clips.enabled`, và nghỉ hoàn toàn (`Event.wait`) khi không có người xem lẫn không cần dựng clip
- [ ] T012 [US2] Tích hợp `StreamComposer` vào `lifespan`, `_switch_camera()` và viết lại endpoint `GET /video.mjpg` trong `app/main.py` để trả `StreamingResponse` trực tiếp từ async generator của `ctx.stream.live_hub` kèm `headers=STREAM_HEADERS` (loại bỏ hoàn toàn `run_in_threadpool(wait_frame)` cũ)
- [ ] T013 [P] [US2] Nâng cấp quản lý vòng đời kết nối luồng hình trên trình duyệt trong `app/static/app.js`: cài đặt hàm trung tâm `syncVideoStreams()` gắn vào sự kiện chuyển tab (`nav button.onclick`) và `document.addEventListener("visibilitychange", ...)`, tự động gỡ thuộc tính `src` của `#video` ngay khi rời tab `live` hoặc ẩn tab trình duyệt (`document.hidden === true`) và nạp lại `/video.mjpg?<Date.now()>` ngay khi hiển thị trở lại để luôn bắt vào khung hình thời gian thực mới nhất

**Checkpoint**: User Story 1 + User Story 2 hoàn chỉnh — hệ thống sẵn sàng chạy trên máy chủ từ xa không trễ dồn, không nghẽn proxy và tiết kiệm tối đa CPU khi không có người xem.

---

## Phase 5: User Story 3 - Phản hồi soi gương tức thời khi nhân viên quay đăng ký khuôn mặt đa góc (Priority: P2)

**Goal**: Luồng xem trước sạch `/video_raw.mjpg` ở màn hình Đăng ký có hướng dẫn đạt tốc độ mượt `≥ 20–24 FPS`, dùng chung 1 bản nén `raw_hub` cho mọi trình duyệt đang mở tab Đăng ký, giữ camera không rơi vào chế độ đọc chậm `idle` khi đang soi gương và đóng kết nối tức thì khi rời tab.

**Independent Test**: Chuyển sang tab Đăng ký ở chế độ quay từ camera (kể cả khi cổng vừa ở Chế độ chờ `sleeping`), kiểm tra luồng `#enrollVideo` phản hồi mượt `~24 FPS`, `live_hub.viewer_count == 0`, `raw_hub.viewer_count == 1`, và khi chuyển sang tab Nhân viên thì cả hai về `0`.

### Tests for User Story 3 ⚠️

- [ ] T014 [P] [US3] Bổ sung kiểm thử cho kênh `raw_hub` và endpoint `GET /video_raw.mjpg` trong `tests/test_stream_latency.py`: xác minh nhiều người xem `/video_raw.mjpg` dùng chung 1 bản nén sạch không dính hộp tracker, trả đủ `STREAM_HEADERS`, tự động bật `camera.preview_active = True` khi `raw_hub.viewer_count > 0` (ngay cả lúc `pipeline.sleeping == True`) và tắt về `False` ngay khi client ngắt kết nối

### Implementation for User Story 3

- [ ] T015 [US3] Mở rộng `StreamComposer` trong `app/stream.py` và `Pipeline._sleep()` / `_wake()` trong `app/pipeline.py` để xuất bản khung hình sạch đã resize (`stream_width`) vào `raw_hub` khi `raw_hub.viewer_count > 0`, đồng bộ cờ `camera.preview_active = (raw_hub.viewer_count > 0)` sao cho `Camera` luôn giải mã tốc độ thực khi có nhân viên đang soi gương ở màn hình Đăng ký
- [ ] T016 [US3] Viết lại endpoint `GET /video_raw.mjpg` trong `app/main.py` sử dụng `ctx.stream.raw_hub` kèm `STREAM_HEADERS` (thay thế vòng lặp `next_jpeg` nén riêng từng client) và hợp nhất `updateEnrollPreview()` vào `syncVideoStreams()` trong `app/static/app.js` để đảm bảo không bao giờ mở trùng cả `/video.mjpg` lẫn `/video_raw.mjpg` trên cùng một trình duyệt

**Checkpoint**: User Story 3 hoàn tất — trải nghiệm soi gương đăng ký 7 góc mượt mà tức thì và không rò rỉ kết nối thừa.

---

## Phase 6: User Story 4 - Giám sát thông số luồng phát và tự động khôi phục khi nguồn hình gián đoạn (Priority: P3)

**Goal**: Hiển thị minh bạch tốc độ khung hình xuất thực tế (`stream_fps`) cùng số người đang xem (`stream_viewers`) trong API trạng thái / giao diện trực tiếp, đồng thời tự động khôi phục luồng hình trong vòng `≤ 2 giây` khi đổi nguồn hoặc camera có tín hiệu trở lại.

**Independent Test**: Gọi `GET /api/status` và quan sát thanh trạng thái `#statusLine` trên trình duyệt thấy rõ `stream <fps> FPS`; mô phỏng ngắt và nối lại nguồn camera (hoặc đổi nguồn qua `POST /api/source`) và xác nhận `#video` tự làm mới hiển thị hình ảnh mới trong vòng `≤ 2 giây` không cần F5.

### Tests for User Story 4 ⚠️

- [ ] T017 [P] [US4] Bổ sung kiểm thử hợp đồng `GET /api/status` trong `tests/test_api_contracts.py` và kiểm thử đổi nguồn camera nóng (`set_camera`) trong `tests/test_stream_latency.py`: xác nhận `status()` trả đầy đủ `stream_fps` (`float`), `stream_target_fps` (`float`), `stream_viewers` (`int`) và `StreamComposer` chuyển sang đọc nguồn `Camera` mới ngay lập tức mà không treo người xem đang kết nối

### Implementation for User Story 4

- [ ] T018 [US4] Mở rộng `Pipeline.status()` trong `app/pipeline.py` (liên kết tham chiếu `stream_composer`) để bổ sung 3 trường hợp đồng mới: `"stream_fps": round(composer.fps_out, 1)`, `"stream_target_fps": round(composer.target_fps, 1)` và `"stream_viewers": composer.total_viewers`, đồng thời đảm bảo `StreamComposer.set_camera(cam)` đặt lại bộ đếm `last_fid` ngay khi đổi nguồn ở `app/main.py`
- [ ] T019 [US4] Cập nhật `showStatus(s)` và cơ chế watchdog khôi phục luồng hình trong `app/static/app.js`: hiển thị `stream ${s.stream_fps} FPS` trên `#statusLine` và trong HUD khi đang xem, theo dõi chuyển tiếp trạng thái nguồn từ mất kết nối / hình đen (`!prevLive -> live`) hoặc kẹt luồng khi camera đang sống để tự động gọi `reloadVideo()` khôi phục khung hình mới nhất trong vòng `≤ 2 giây`

**Checkpoint**: Toàn bộ 4 User Stories (US1 → US4) hoàn tất độc lập và đầy đủ tính năng giám sát vận hành máy chủ.

---

## Phase 7: Polish & Cross-Cutting Concerns (Công cụ Benchmark, Tài liệu & Nghiệm thu Tổng thể)

**Purpose**: Bổ sung chế độ đo kiểm định lượng tốc độ luồng phát trên CPU, cập nhật tài liệu triển khai máy chủ / Reverse Proxy và xác minh 0 hồi quy trên toàn bộ hệ thống.

- [ ] T020 [P] Mở rộng `scripts/benchmark.py` hỗ trợ tùy chọn `--stream-bench` (cho phép chạy độc lập không bắt buộc truyền file video bằng khung hình tổng hợp hoặc chạy trên video thật) để đo và in định lượng thời gian `resize + ROI draw_overlay` (ms), thời gian `JPEG encode` (ms), kích thước gói JPEG trung bình (KB) và tốc độ `stream_fps` đạt được khi chạy song song với chu kỳ suy luận AI
- [ ] T021 [P] Cập nhật hướng dẫn cấu hình tham số luồng stream độ trễ thấp và mẫu cấu hình Nginx Reverse Proxy (`proxy_buffering off`, WebSocket `/ws`) phục vụ triển khai máy chủ trong `README.md` và `docs/HUONG_DAN_KY_THUAT.html`
- [ ] T022 Chạy toàn bộ bộ kiểm thử tự động `.venv/bin/pytest -v` cùng công cụ `.venv/bin/python scripts/benchmark.py --stream-bench` để xác minh đạt trọn vẹn các ngưỡng `SC-001` → `SC-005` theo `specs/002-low-latency-stream/quickstart.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1: Setup (`T001` – `T002`)**: Không có phụ thuộc — có thể thực hiện song song ngay lập tức.
- **Phase 2: Foundational (`T003` – `T005`)**: Phụ thuộc Phase 1 — **CHẶN (BLOCKS)** toàn bộ các User Stories cho tới khi `Camera.read_latest()` và `FrameHub` hoàn tất.
- **Phase 3: User Story 1 (`T006` – `T009`, P1 🎯 MVP)**: Bắt đầu ngay sau Phase 2; giải quyết trực tiếp bài toán mượt hình 24 FPS và vẽ nhãn tiếng Việt siêu nhẹ.
- **Phase 4: User Story 2 (`T010` – `T013`, P1)**: Phụ thuộc `StreamComposer` của US1 (`T008`–`T009`) để hoàn thiện cơ chế phân phối async nhiều người xem, chống trễ dồn mạng yếu và tự ngắt `0% CPU` khi không ai xem.
- **Phase 5: User Story 3 (`T014` – `T016`, P2)**: Có thể triển khai ngay sau khi `FrameHub` (Phase 2) và `StreamComposer` cơ bản (`T008`) sẵn sàng; tích hợp cùng `syncVideoStreams()` ở `app/static/app.js`.
- **Phase 6: User Story 4 (`T017` – `T019`, P3)**: Tích hợp số liệu từ `StreamComposer` / `FrameHub` vào `/api/status` và hoàn thiện tự khôi phục luồng hình trên `app/static/app.js`.
- **Phase 7: Polish (`T020` – `T022`)**: Chạy sau khi hoàn tất các User Stories để đo kiểm định lượng và nghiệm thu toàn cục.

### User Story Dependency Graph

```text
Phase 1 (Setup: T001, T002)
   │
   ▼
Phase 2 (Foundational: T003 Camera Ingest, T004 FrameHub & Entities, T005 Base Tests)
   │
   ▼
Phase 3: US1 [P1 - MVP] (T006 Tests -> T007 ROI Sprite Cache + T008 StreamComposer + T009 Pipeline Decouple)
   │
   ├───────────────────────────────────────────┐
   ▼                                           ▼
Phase 4: US2 [P1 - Server Zero-Drift]      Phase 5: US3 [P2 - Enroll Raw Mirror]
(T010 Tests -> T011-T013 Async Broadcast)  (T014 Tests -> T015-T016 Shared Raw Stream)
   │                                           │
   └───────────────────┬───────────────────────┘
                       ▼
         Phase 6: US4 [P3 - Stream Telemetry & Auto-Recovery]
         (T017 Tests -> T018 Status Metrics + T019 UI Watchdog)
                       │
                       ▼
         Phase 7: Polish & Measurable Gates (T020 Benchmark, T021 Docs, T022 Full Pytest)
```

### Parallel Opportunities

- **Trong Phase 1**: `T001` (`app/config.py`) và `T002` (`config.yaml`) chạy song song `[P]`.
- **Trong Phase 2**: `T003` (`app/camera.py`) và `T004` (`app/stream.py`) tác động trên 2 file độc lập nên chạy song song `[P]`.
- **Trong Phase 3 (US1)**: `T006` (`tests/test_stream_latency.py`) và `T007` (`app/draw.py` ROI Sprite Cache) hoàn toàn độc lập với `Pipeline`, có thể thực hiện song song `[P]` trước khi ghép vào `T008`–`T009`.
- **Trong Phase 4 (US2)**: `T013` (`app/static/app.js` quản lý vòng đời tab) có thể viết song song `[P]` với `T011`–`T012` ở tầng Python backend.
- **Trong Phase 7 (Polish)**: `T020` (`scripts/benchmark.py`) và `T021` (`README.md`, `docs/HUONG_DAN_KY_THUAT.html`) chạy song song `[P]` trước cổng kiểm chứng cuối `T022`.

---

## Parallel Example: User Story 1 & Foundational

```bash
# Chạy song song hạ tầng thu nhận Camera và thực thể Stream (Phase 2):
Task T003: "Nâng cấp app/camera.py giảm đệm RTSP & thêm read_latest()"
Task T004: "Khởi tạo OverlayBox, OverlaySnapshot, FrameHub trong app/stream.py"

# Chạy song song kiểm thử US1 và tối ưu vẽ chữ tiếng Việt ROI (Phase 3):
Task T006: "Viết kiểm thử ROI Sprite Cache & Non-blocking StreamComposer trong tests/test_stream_latency.py"
Task T007: "Cài đặt LabelSpriteCache cắt dán vùng ROI trong app/draw.py"
```

---

## Implementation Strategy

### MVP First (Phase 1 + Phase 2 + Phase 3 [US1] + Phase 4 [US2])

Vì cả **US1** (hết lag so với ứng dụng camera gốc khi đang nhận diện) và **US2** (chạy trên server không trễ dồn, không tốn CPU thừa khi ẩn tab) đều mang độ ưu tiên **P1** gắn liền với yêu cầu cốt lõi của người dùng:
1. Hoàn tất **Phase 1 & Phase 2** (`T001`–`T005`): Chuẩn bị cấu hình 24 FPS, RTSP low-delay và `FrameHub`.
2. Hoàn tất **Phase 3 — US1** (`T006`–`T009`): Đạt ngay tốc độ vẽ chữ tiếng Việt `~0.25 ms` và tách rời luồng xuất hình `24 FPS` khỏi luồng suy luận ONNX.
3. Hoàn tất **Phase 4 — US2** (`T010`–`T013`): Đưa endpoint `/video.mjpg` lên cơ chế async broadcast không trễ dồn + ngắt kết nối khi ẩn tab.
4. **Dừng kiểm chứng MVP**: Mở trình duyệt đối chiếu trực tiếp với ứng dụng camera gốc và kiểm tra tải CPU khi đóng/mở nhiều tab.

### Incremental Delivery (Bổ sung US3, US4 & Benchmark)

5. Bổ sung **Phase 5 — US3** (`T014`–`T016`): Đồng bộ hóa luồng `/video_raw.mjpg` cho màn hình Đăng ký 7 góc.
6. Bổ sung **Phase 6 — US4** (`T017`–`T019`): Hiển thị chỉ số `stream FPS` trực quan và tự khôi phục luồng sau sự cố mạng/nguồn.
7. Hoàn tất **Phase 7** (`T020`–`T022`): Chạy benchmark định lượng và xác nhận xanh 100% toàn bộ `pytest`.

---

## Notes

- Mọi tác vụ `[P]` đều thao tác trên các tập tin tách biệt, không xung đột ghi.
- Giữ nguyên 100% hành vi nhận diện góc nghiêng ±90°, 4 lớp chống giả mạo và định dạng clip lịch sử đã hoàn thiện ở nhánh `001-profile-90-antispoof`.
- Tuân thủ nguyên tắc viết code tối giản, rõ ràng của dự án: chỉ chú thích ở những bất biến đồng bộ luồng (như lý do dùng snapshot bất biến hoặc giới hạn ngoại suy `≤ 120 ms`).
