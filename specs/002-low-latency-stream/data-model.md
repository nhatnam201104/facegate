# Phase 1 Data Model: Tối ưu Độ trễ và Hiệu năng Luồng Hiển thị Video Trực tiếp

**Feature**: `002-low-latency-stream`
**Date**: 2026-11-12
**Storage Impact**: Pure In-Memory Concurrency & Streaming State (Không thay đổi schema SQLite `data/facegate.db`, đảm bảo tương thích ngược 100%)

---

## 1. Tổng quan Mô hình Dữ liệu Thời gian thực (Runtime Concurrency Entities)

Tính năng tối ưu độ trễ luồng stream không yêu cầu thêm bảng cơ sở dữ liệu trên đĩa, mà tái cấu trúc **mô hình truyền dữ liệu bất biến không khóa lâu (*Immutable Snapshot & Latest-Frame Broadcast*)** giữa 4 thành phần chạy song song: `Camera` $\rightarrow$ `Pipeline` $\rightarrow$ `StreamComposer` $\rightarrow$ `FrameHub` (FastAPI Async Viewers).

```text
[Camera Thread] --(LatestCameraFrame)--> [Pipeline AI Thread (3-5 FPS)]
       |                                           |
       |                                  (OverlaySnapshot)
       v                                           v
[StreamComposer Thread (24 FPS, On-Demand)] <------+
       |
       +---> [ClipRecorder Queue (10 FPS, khi có người)]
       +---> [live FrameHub] ---> N Async HTTP Viewers (/video.mjpg)
       +---> [raw FrameHub]  ---> M Async HTTP Viewers (/video_raw.mjpg)
```

---

## 2. Chi tiết Các Thực thể Bộ nhớ Đệm (In-Memory Data Structures)

### 2.1. `LatestCameraFrame` (Mở rộng trong `app/camera.py`)

Đại diện cho khung hình mới nhất vừa thu được từ phần cứng webcam, luồng mạng RTSP hoặc file video mô phỏng.

| Trường | Kiểu dữ liệu | Bắt buộc | Quy tắc / Bất biến |
|---|---|---|---|
| `frame_id` | `int` | Có | Tăng đơn điệu (`1, 2, 3...`) mỗi khi có frame hợp lệ mới; dùng để phát hiện trùng lặp `O(1)`. |
| `frame_ts` | `float` | Có | Unix monotonic/wall timestamp ngay khoảnh khắc đọc xong từ thiết bị (`time.time()`). |
| `frame` | `np.ndarray \| None` | Không | Mảng BGR `uint8` kích thước `(H, W, 3)` mới nhất; luôn ghi đè tham chiếu cũ (không xếp hàng đợi). |
| `fps_in` | `float` | Có | Tốc độ khung hình đầu vào đo trên cửa sổ trượt ~1 giây. |
| `connected` | `bool` | Có | `True` khi nguồn video đang mở và trả frame hợp lệ. |
| `black` | `bool` | Có | `True` khi watchdog phát hiện khung hình đen liên tục quá `BLACK_SECS` (4 giây). |

- **Quy tắc chuyển trạng thái**:
  - Khi `idle == True` (hệ thống đang ở Chế độ chờ `sleeping` **và** không có ai mở `/video_raw.mjpg`), luồng `Camera` vẫn gọi `cap.grab()` liên tục để xả sạch bộ đệm driver/socket nhưng chỉ `cap.retrieve()` giải mã màu mỗi `idle_interval` (`0.25s`).
  - Ngay khi `idle == False` (có người bước vào vùng cửa hoặc người dùng mở màn hình Đăng ký), `Camera` lập tức giải mã đầy đủ mọi frame mới ở tốc độ gốc (25–30 FPS).

---

### 2.2. `OverlayBox` & `OverlaySnapshot` (Mới — `app/stream.py` & `app/pipeline.py`)

Bản chụp bất biến (`@dataclass(frozen=True)`) chứa toàn bộ thông tin đồ họa cần vẽ lên khung hình trực tiếp. Được tạo bởi luồng `Pipeline` sau mỗi vòng nhận diện và đọc bởi luồng `StreamComposer` mà **không cần giữ khóa trên `IoUTracker` hay `Engine`**.

#### `OverlayBox`
| Trường | Kiểu dữ liệu | Mô tả |
|---|---|---|
| `track_id` | `int` | Định danh hành trình (`Track.id`). |
| `bbox` | `tuple[float, float, float, float]` | Tọa độ `(x1, y1, x2, y2)` trên hệ quy chiếu khung hình gốc của `Camera`. |
| `vx` | `float` | Vận tốc dịch chuyển ngang ước lượng (pixel/giây) từ `Track` để tùy chọn ngoại suy nhẹ giữa 2 nhịp detect (giới hạn tối đa `≤ 120 ms` tránh trôi khung). |
| `vy` | `float` | Vận tốc dịch chuyển dọc ước lượng (pixel/giây). |
| `color` | `tuple[int, int, int]` | Màu BGR (`GREEN`, `ORANGE`, `RED`, `GRAY`). |
| `label` | `str` | Nhãn tiếng Việt đã định dạng sẵn (vd: `"Nguyễn Văn A · 0.62 · thật 0.91"`). |
| `updated_at` | `float` | Thời điểm cập nhật mẫu nhận diện gần nhất (`Track.last_seen`). |

#### `OverlaySnapshot`
| Trường | Kiểu dữ liệu | Mô tả |
|---|---|---|
| `version` | `int` | Số thứ tự bản chụp tăng dần mỗi khi trạng thái nhận diện/HUD thay đổi. |
| `created_at` | `float` | Thời điểm xuất bản snapshot. |
| `sleeping` | `bool` | `True` khi hệ thống đang trong Chế độ chờ tiết kiệm tài nguyên. |
| `boxes` | `tuple[OverlayBox, ...]` | Danh sách các hộp khuôn mặt còn tươi (`now - updated_at <= DRAW_FRESH`). |
| `line` | `tuple[tuple[float, float], tuple[float, float]] \| None` | Vạch qua cửa chuẩn hóa `0..1` nếu `events.direction == "line"`. |
| `motion_zone` | `tuple[tuple[float, float], ...] \| None` | Đa giác vùng cửa chuẩn hóa `0..1` (vẽ ở Chế độ chờ khi khác `FULL_ZONE`). |
| `hud` | `str` | Chuỗi thông tin vận hành góc trên bên trái (engine, detect FPS/ms, cam FPS, stream FPS, số NV). |

- **Quy tắc toàn vẹn**:
  - Đối tượng `OverlaySnapshot` là **bất biến 100%** sau khi khởi tạo. Phép gán `self._overlay = new_snapshot` trong Python là nguyên tử ở mức tham chiếu, loại bỏ hoàn toàn nguy cơ deadlock hoặc chặn luồng xuất hình.

---

### 2.3. `LabelSpriteCache` (Mới — trong `app/draw.py`)

Bộ nhớ đệm LRU cho các nhãn chữ tiếng Việt và thanh HUD đã dựng bằng Pillow nhằm loại bỏ chuyển đổi màu toàn khung hình.

| Thuộc tính | Kiểu dữ liệu | Quy tắc / Giới hạn |
|---|---|---|
| `key` | `tuple[str, tuple[int, int, int], int]` | Bộ khóa `(text, bg_color_bgr, font_size)`. |
| `value` | `np.ndarray` | Mảng ảnh nhỏ BGR `uint8` kích thước `(sprite_h, sprite_w, 3)` đã vẽ sẵn nền màu và chữ trắng chống răng cưa. |
| `maxsize` | `int` | Mặc định `256` mục (~2–3 MB RAM tối đa); tự động đẩy mục ít dùng nhất (LRU) ra ngoài khi đầy. |

- **Quy tắc cắt dán biên an toàn (*Boundary Clipping*)**:
  - Khi dán `sprite` tại tọa độ `(x1, ty)` lên khung hình đích `(H, W)`, nếu `x1 + sprite_w > W` hoặc `ty + sprite_h > H` hoặc `x1 < 0`, hàm dán tự động cắt phần giao hợp lệ (clamp) để không bao giờ gây lỗi tràn mảng `IndexError`/`ValueError` với tên nhân viên dài hoặc mặt sát mép phải màn hình.

---

### 2.4. `EncodedFrame` & `FrameHub` (Mới — trong `app/stream.py`)

Quản lý khung hình JPEG dùng chung và danh sách người xem bất đồng bộ cho một kênh stream (`live` cho `/video.mjpg` và `raw` cho `/video_raw.mjpg`).

| Trường | Kiểu dữ liệu | Mô tả |
|---|---|---|
| `name` | `str` | Tên kênh: `"live"` hoặc `"raw"`. |
| `seq` | `int` | Số thứ tự khung hình JPEG đã xuất bản (`0` khi chưa có frame). |
| `ts` | `float` | Dấu thời gian nguồn của khung hình (`frame_ts`), dùng để đo độ trễ nội bộ. |
| `jpeg` | `bytes \| None` | Mảng byte JPEG hoàn chỉnh, dùng chung (read-only) cho toàn bộ HTTP response generators. |
| `fps_out` | `float` | Tốc độ khung hình thực tế đã xuất trên kênh này (làm tròn 1 chữ số thập phân khi đưa ra `/api/status`). |
| `viewer_count` | `int` | Số lượng kết nối trình duyệt đang mở thực tế (`>= 0`). |

#### Sơ đồ chuyển trạng thái của `StreamComposer` theo `viewer_count` và trạng thái hệ thống:

| Trạng thái `Pipeline` | `live.viewer_count` | `raw.viewer_count` | Có phiên ghi `ClipRecorder` / cần pre-roll? | Hành vi của `StreamComposer` |
|---|---|---|---|---|
| `Awake` (Đang thức) | `> 0` | `0` | Bất kỳ | Dựng overlay + nén JPEG `live` ở nhịp `stream_fps` (`24 FPS`); đẩy sang `clips` ở nhịp `clips.fps` (`10 FPS`). |
| `Awake` (Đang thức) | `0` | `0` | `Có` (`clips.enabled`) | Dựng ảnh `view` tối đa ở nhịp `clips.fps` (`10 FPS`) để giữ pre-roll/ghi clip; **bỏ qua 100% bước `cv2.imencode` JPEG**. |
| `Awake` (Đang thức) | `0` | `0` | `Không` (`clips.enabled=False`) | **Ngủ chờ sự kiện (`0% CPU` render/encode)**; chỉ nhận diện ngầm. |
| `Awake` / `Enroll` | Bất kỳ | `> 0` | Bất kỳ | Dựng và nén thêm 1 bản `raw` dùng chung ở nhịp `stream_fps` cho mọi tab Đăng ký đang mở. |
| `Sleeping` (Chế độ chờ) | `> 0` | `0` | `Có` / `Không` | Cập nhật khung hình Chế độ chờ tối màu mỗi `SLEEP_PREVIEW_EVERY` (`2.0s`); thức dậy tức thì ngay khi `Pipeline` thoát ngủ. |
| `Sleeping` (Chế độ chờ) | `0` | `0` | `Có` / `Không` | **Không dựng và không nén bất kỳ JPEG nào** (`0% CPU` cho stream). |

---

## 3. Quy tắc Kiểm tra Tính Hợp lệ của Tham số Cấu hình (`Validation Rules`)

Được chuẩn hóa trong `app/config.py` (`_apply_defaults`) để mọi file `config.yaml` cũ hoặc mới đều nhận giá trị an toàn:

- `pipeline.stream_fps`: Số thực trong khoảng `[1.0, 60.0]`, mặc định **`24`** (nâng từ `15` cũ).
- `pipeline.stream_width`: Số nguyên trong khoảng `[320, 1920]`, mặc định **`960`**.
- `pipeline.stream_jpeg_quality`: Số nguyên trong khoảng `[40, 95]`, mặc định **`72`** (giảm ~25–30% kích thước gói JPEG so với mức 80–85 mà mắt thường không phân biệt được trên chuyển động thực tế, chữ tiếng Việt vẫn sắc nét).
- `camera.rtsp_low_latency`: Kiểu `bool`, mặc định **`true`** (kích hoạt bộ cờ giảm đệm FFmpeg cho nguồn RTSP khi biến môi trường `OPENCV_FFMPEG_CAPTURE_OPTIONS` chưa bị người dùng ghi đè thủ công).
- `camera.grab_drain`: Kiểu `bool`, mặc định **`true`** (luôn lấy khung mới nhất, tránh ứ đọng bộ đệm đầu vào).
