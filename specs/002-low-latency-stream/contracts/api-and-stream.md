# Interface Contracts: Video Stream, REST Status & Browser Lifecycle

**Feature**: `002-low-latency-stream`
**Date**: 2026-11-12
**Base URL**: `http://127.0.0.1:8090` (hoặc qua Reverse Proxy trên Server triển khai thực tế)

Toàn bộ đường dẫn endpoint hiện hữu được giữ nguyên 100% để tương thích ngược với giao diện và các công cụ tích hợp sẵn có; hợp đồng dưới đây đặc tả hành vi truyền tải độ trễ thấp, bộ HTTP headers chống đệm proxy và các trường giám sát mở rộng.

---

## 1. Luồng Video Trực tiếp Đã Gắn Nhãn (`GET /video.mjpg`)

Phục vụ luồng hình ảnh thời gian thực cho tab **Trực tiếp** (bao gồm hộp khuôn mặt theo dõi, nhãn tiếng Việt, vạch cửa và thanh thông số HUD).

- **Method & Path**: `GET /video.mjpg`
- **Query Parameters**:
  - Tham số chống cache tùy chọn từ trình duyệt (ví dụ `?t=1794471813866`) — server bỏ qua giá trị nhưng cho phép trình duyệt buộc mở phiên mới khi khôi phục kết nối.
- **Response Status**: `200 OK`
- **Response Headers (BẮT BUỘC cho môi trường Server / Reverse Proxy)**:
  ```http
  Content-Type: multipart/x-mixed-replace; boundary=frame
  Cache-Control: no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0
  Pragma: no-cache
  Expires: 0
  X-Accel-Buffering: no
  ```
- **Multipart Frame Payload Format**:
  ```http
  --frame\r\n
  Content-Type: image/jpeg\r\n
  Content-Length: <byte_length>\r\n
  \r\n
  <raw_jpeg_bytes>\r\n
  ```
- **Hợp đồng Hành vi Thời gian thực (Streaming Guarantees)**:
  1. **Zero Per-Client Encoding**: Nén JPEG tối đa 1 lần cho mỗi khung hình xuất bản, dùng chung vùng nhớ `bytes` cho mọi kết nối đang mở.
  2. **Latest-Frame Drop (Không trễ dồn)**: Mỗi client chỉ giữ tham chiếu số thứ tự `last_seq` vừa gửi xong. Nếu trong lúc client bận truyền qua mạng chậm mà server đã xuất bản nhiều frame mới (`hub.seq > last_seq + 1`), client lập tức nhận frame `hub.seq` mới nhất và bỏ qua toàn bộ các frame trung gian.
  3. **Event-Loop Native**: Không cấp phát hay khóa worker trong `run_in_threadpool` khi chờ frame mới; giải phóng `viewer_count` ngay trong khối `finally` khi socket đóng.

---

## 2. Luồng Video Sạch Cho Đăng ký Hướng dẫn (`GET /video_raw.mjpg`)

Phục vụ luồng hình ảnh sạch (không có hộp tracker/HUD của cổng chấm công) dùng để hiển thị lật gương trên tab **Đăng ký**.

- **Method & Path**: `GET /video_raw.mjpg`
- **Response Headers**: Giống hệt `GET /video.mjpg` (bao gồm `X-Accel-Buffering: no` và `Cache-Control: no-store...`).
- **Hợp đồng Hành vi Thời gian thực**:
  1. Chia sẻ chung `raw_hub`: Nếu có 2 trình duyệt cùng mở màn hình Đăng ký, khung hình sạch chỉ được thu phóng và nén JPEG **1 lần duy nhất** mỗi nhịp `stream_fps`.
  2. Khi không có người xem nào kết nối tới `/video_raw.mjpg` (`raw_hub.viewer_count == 0`), server **không** tốn bất kỳ chu kỳ CPU nào để dựng hay nén luồng raw.
  3. Khi có người mở `/video_raw.mjpg`, `Camera.idle` được giữ ở trạng thái hoạt động đầy đủ để hình ảnh soi gương đạt tốc độ mượt tối đa ngay cả trước khi bấm nút *"Bắt đầu quay"*.

---

## 3. Mở rộng Hợp đồng Trạng thái Vận hành (`GET /api/status` & WebSocket `/ws`)

Bổ sung các trường đo lường hiệu năng luồng phát (giữ nguyên toàn bộ các khóa cũ từ `001-profile-90-antispoof`):

```json
{
  "backend": "edgeface",
  "model_version": "edgeface_xs_v1+3ddfa_v2",
  "max_supported_yaw": 90,
  "source": {
    "kind": "rtsp",
    "label": "rtsp://***@192.168.1.64:554/Streaming/Channels/102",
    "connected": true,
    "black": false
  },
  "camera_connected": true,
  "camera_fps": 25.0,
  "stream_fps": 23.8,
  "stream_target_fps": 24.0,
  "stream_viewers": 2,
  "detect_fps": 5.0,
  "detect_ms": 68.4,
  "gallery": 24,
  "gallery_profile_ready": 19,
  "tracks": 1,
  "threshold": 0.40,
  "profile_threshold": 0.46,
  "margin": 0.06,
  "profile_margin": 0.08,
  "liveness": true,
  "live_threshold": 0.70,
  "liveness_modes": ["texture_fas", "context_medium", "body_coupling", "parallax_3d", "moire_fft"],
  "sleeping": false,
  "sleep_since": null,
  "idle_seconds": 30.0,
  "motion_zone": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
  "wakes_rejected": 2,
  "clips": true,
  "direction": "none",
  "hrm": "mock"
}
```

### Ý nghĩa các trường mới:
| Trường | Kiểu | Mô tả |
|---|---|---|
| `stream_fps` | `float` | Tốc độ xuất khung hình thực tế của luồng hiển thị trực tiếp (`0.0` khi không có người xem hoặc nguồn ngắt kết nối). |
| `stream_target_fps` | `float` | Tốc độ khung hình mục tiêu cấu hình trong `config.yaml` (`pipeline.stream_fps`). |
| `stream_viewers` | `int` | Tổng số kết nối trình duyệt đang xem luồng video trực tiếp (`live_hub.viewer_count + raw_hub.viewer_count`). |

---

## 4. Hợp đồng Vòng đời Kết nối Trên Giao diện Web (`app/static/app.js`)

1. **Chỉ mở đúng luồng đang nhìn thấy**:
   - Khi `tab-live` đang active **và** `!document.hidden`: thẻ `#video` kết nối `/video.mjpg?<ts>`, thẻ `#enrollVideo` rỗng (`src` được gỡ bỏ).
   - Khi `tab-enroll` đang active ở chế độ quay từ camera **và** `!document.hidden`: thẻ `#enrollVideo` kết nối `/video_raw.mjpg?<ts>`, thẻ `#video` được gỡ `src` ngay lập tức.
   - Khi ở các tab `attendance`, `history`, `employees`, `events` **hoặc** khi `document.hidden === true`: cả `#video` và `#enrollVideo` đều được gỡ `src` để giải phóng băng thông mạng và đưa `stream_viewers` trên server về `0`.
2. **Hiển thị thông số trực quan**:
   - Thanh trạng thái đầu trang (`#statusLine`) hiển thị thêm `stream <stream_fps> FPS` bên cạnh `cam <camera_fps> FPS` và `detect <detect_fps> FPS / <detect_ms> ms`.
3. **Tự phục hồi không trễ dồn**:
   - Khi quay lại tab có video, khi đổi nguồn camera thành công (`POST /api/source`), hoặc khi nguồn camera chuyển từ mất tín hiệu sang có hình trở lại, giao diện tự nạp lại URL kèm dấu thời gian mới để nhận ngay khung hình hiện tại trong vòng `≤ 2 giây`.
