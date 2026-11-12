# Configuration Contract (`config.yaml` Additions for Low-Latency Server Streaming)

**Feature**: `002-low-latency-stream`
**Date**: 2026-11-12

Các khóa cấu hình mới và giá trị mặc định cập nhật trong `config.yaml` và `app/config.py` (hoàn toàn tương thích ngược — nếu file `config.yaml` hiện có chưa khai báo các khóa mới, `_apply_defaults()` sẽ tự động điền giá trị chuẩn):

```yaml
camera:
  # 0 (hoac /dev/video0 tren Linux) = webcam | duong dan file video | RTSP substream:
  # rtsp://user:pass@192.168.1.64:554/Streaming/Channels/102
  source: 0
  name: gate-1
  loop_file: true
  rtsp_low_latency: true     # [MỚI] Tu dong bat co FFmpeg nobuffer + low_delay cho camera RTSP tren server
  grab_drain: true           # [MỚI] Xa frame cu trong bo dem thiet bi de luon giu frame thoi gian thuc moi nhat

pipeline:
  backend: edgeface
  detect_fps: 5              # So lan phan tich AI moi giay (chay tren luong doc lap, khong chan stream)
  det_score: 0.55
  max_faces: 3
  stream_fps: 24             # [CẬP NHẬT: 15 -> 24] Toc do khung hinh muc tieu gui len trinh duyet (tiem can camera goc)
  stream_width: 960          # Chieu rong khung hinh hien thi (320..1920); tren WAN co the ha xuong 768 hoac 640
  stream_jpeg_quality: 72    # [MỚI] Chat luong nen JPEG (40..95); 72 giu ro dau tieng Viet va giam ~28% bang thong
  interpolate_boxes: true    # [MỚI] Ngoai suy muot vi tri khung mat ngan han (<=120ms) giua cac nhip detect
```

---

## Bảng Quy tắc Chuẩn hóa & Ràng buộc (`app/config.py`)

| Khóa cấu hình | Kiểu | Mặc định | Miền hợp lệ (Clamp) | Tác động khi triển khai lên Server |
|---|---|---|---|---|
| `camera.rtsp_low_latency` | `bool` | `true` | `true` \| `false` | Gắn `rtsp_transport;tcp\|fflags;nobuffer\|flags;low_delay\|max_delay;250000` cho FFmpeg RTSP nếu môi trường chưa đặt sẵn `OPENCV_FFMPEG_CAPTURE_OPTIONS`. |
| `camera.grab_drain` | `bool` | `true` | `true` \| `false` | Giữ độ tươi đầu vào tối đa cho webcam/RTSP khi luồng đọc chạy nhanh. |
| `pipeline.stream_fps` | `float` | `24.0` | `1.0 .. 60.0` | Điều chỉnh độ mượt hiển thị; có thể đặt `25–30` cho LAN nội bộ hoặc `15–20` cho server kết nối qua VPN băng thông hẹp. |
| `pipeline.stream_width` | `int` | `960` | `320 .. 1920` | Tự động giữ tỷ lệ khung hình gốc (không phóng to nếu ảnh gốc nhỏ hơn `stream_width`). |
| `pipeline.stream_jpeg_quality` | `int` | `72` | `40 .. 95` | Cân bằng trực tiếp giữa băng thông mạng/tải nén CPU và độ sắc nét hình ảnh. |
| `pipeline.interpolate_boxes` | `bool` | `true` | `true` \| `false` | Giúp khung xanh/cam bám sát khuôn mặt người đang đi nhanh ở 24 FPS trong khoảng giữa 2 lần detect 5 FPS. |
