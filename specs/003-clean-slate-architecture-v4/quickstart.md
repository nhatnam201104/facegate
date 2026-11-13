# Quickstart & Field Validation Guide: Kiến trúc FaceGate v4 trên TP-Link Tapo C310

**Feature**: `003-clean-slate-architecture-v4`  
**Date**: 2026-11-13

Tài liệu này quy định trình tự chuẩn bị phần cứng **TP-Link Tapo C310** bắt buộc trước khi bấm máy pha `K0`, cách chạy bộ công cụ đo Baseline / Replay ngoại tuyến, và kịch bản nghiệm thu từng cổng kỹ thuật `K1`–`K5`.

---

## 1. Quy trình Chuẩn bị Phần cứng Tapo C310 (Thứ tự Bắt buộc — `H27`..`H30`, `Mục 7.1`)

> **CẢNH BÁO QUAN TRỌNG**: Phải hoàn tất Bước 1.1 trên App Tapo khi camera còn kết nối mạng thường **TRƯỚC KHI** đưa camera vào VLAN cô lập ở Bước 1.3 (sau khi chặn Internet, App Tapo có thể không đổi được cấu hình nâng cao nữa).

1. **Bước 1.1 — Cấu hình một lần trên Tapo App**:
   - Cắm nguồn điện có dây và cắm cáp mạng **Ethernet RJ45** (`10/100 Mbps`) trực tiếp vào cổng LAN của Tapo C310 (tuyệt đối không chạy qua Wi-Fi 2.4 GHz).
   - **Không lắp thẻ nhớ `microSD`** vào camera, **không kích hoạt Tapo Care** (tránh khóa RTSP và tránh lưu dữ liệu sinh trắc học không mã hóa ngoài trời).
   - Tạo tài khoản Camera riêng cho RTSP (`Device Settings > Advanced Settings > Camera Account`).
   - Chuyển **`Video & Display > Video Mode`** từ `Auto` sang **`Day Mode`** (tắt hẳn đèn IR 850 nm, giữ cứng chế độ màu RGB).
   - Tắt tự động cập nhật firmware (`Auto Firmware Update = Off`) sau khi xác nhận luồng RTSP chạy ổn định.
2. **Bước 1.2 — Ánh sáng & Vị trí Lắp Thử nghiệm (`H26`, `H32`)**:
   - Bố trí đèn LED trắng chiếu thuận về lối đi sao cho độ rọi ngang tầm mặt đạt **≥ 250–300 lux** (giúp giữ màu ở `Day Mode` và ép auto-exposure của C310 tăng tốc độ màn trập chống nhòe ở `15 fps`).
   - Đánh dấu mốc khoảng cách trên sàn ở `1.5 m`, `2.0 m`, `2.5 m` và `3.0 m` tính từ ống kính để kiểm tra bề ngang mặt ≥ `80 px` trên luồng 3MP.
3. **Bước 1.3 — Bật NTP Nội bộ & Cô lập VLAN Camera (`H16`, `H29`)**:
   - Trên máy chủ Linux chạy FaceGate, bật `chrony` cho phép dải mạng VLAN camera đồng bộ giờ (`allow <subnet-vlan-camera>`).
   - Trên Router/Firewall, đưa cổng LAN của C310 vào VLAN riêng: chỉ cho phép giao tiếp với IP máy chủ FaceGate, chặn toàn bộ chiều đi ra Internet, và thêm rule **DNAT cổng `UDP 123`** về IP máy chủ FaceGate (xử lý hành vi gọi cứng NTP public của firmware Tapo).
   - **Đóng hẳn App Tapo trên điện thoại** trong suốt thời gian vận hành để trả toàn bộ suất luồng RTSP (`2+2`) cho `MediaMTX`.

---

## 2. Đo Kiểm Tiền đề Pha `K0` (GOP, Không Âm thanh, Baseline & Giao thức ISO/IEC 30107-3)

### 2.1. Đo khoảng cách Keyframe (GOP) thực tế & Xác nhận lọc bỏ âm thanh `pcm_alaw`
```bash
# 1. Đo khoảng cách giữa các I-frame (GOP) thực tế của Tapo C310 trên /stream1
ffprobe -rtsp_transport tcp -select_streams v:0 -show_frames -show_entries frame=pict_type,pts_time \
  "rtsp://$CAM_USER:$CAM_PASS@$CAM_IP:554/stream1" | grep -A 1 "pict_type=I" | head -n 30

# 2. Kiểm tra luồng qua MediaMTX chỉ còn đúng 1 video stream H.264 và 0 audio stream
ffprobe -hide_banner rtsp://127.0.0.1:8554/gate1_main
```
- **Kỳ vọng**: `gate1_main` chỉ hiển thị `Stream #0:0: Video: h264, yuv420p, 2304x1296, 15 fps`, **không có bất kỳ dòng `Audio: pcm_alaw` nào**.

### 2.2. Thu thập Tập Đánh giá Thực địa `K0` & Chạy Replay
1. Ký đơn đồng ý thử nghiệm (`H11`) với nhóm tình nguyện viên.
2. Quay cả **2 bố cục ứng viên** (`đi ngang tự nhiên` vs. `hướng mặt nhìn thẳng ±40°`) kèm bộ tấn công giả mạo theo chuẩn **ISO/IEC 30107-3** (`≥ 20 lượt` cho mỗi loại: ảnh in giấy, ảnh trên điện thoại, video phát lại trên điện thoại, màn hình lớn).
3. Chạy đánh giá tuần tự không rớt frame để xuất bảng Baseline (FAR ghép chéo + cận trên khoảng tin cậy, FRR, APCER/BPCER từng PAI, phân phối Laplacian blur, CPU decode 3MP) trình họp cổng **`K0.5`** với PM.

---

## 3. Nghiệm thu Các Cổng Kỹ thuật `K1`–`K5`

1. **Kiểm chứng Đồng bộ `<canvas>` bằng Đồng hồ Mili-giây (`K1` — `H8`)**:
   - Đặt màn hình hiển thị bộ đếm mili-giây chạy liên tục và di chuyển ngang trước ống kính C310; chụp ảnh màn hình trình duyệt chứa đồng thời đồng hồ gốc và khung `<canvas>` để hiệu chỉnh `overlay_offset_ms`, xác nhận sai lệch vị trí khung bao **p90 ≤ 2 frame** và tốc độ đạt **13–15 fps**.
2. **Kiểm chứng Cô lập Lỗi, Khóa `legal_hold` & Sổ cái HRM Hai chiều (`K2`)**:
   - Chạy bộ kiểm thử tự động `.venv/bin/pytest -v`.
   - Gửi `kill -9` tới `Perception Process` khi hệ thống đang mở: xác nhận trang Bảng công không lỗi, `/api/health` chuyển trạng thái cảnh báo trong `≤ 5 giây`, tiến trình AI tự khởi động lại, các clip có `legal_hold = 1` không bị xóa khi giả lập ngưỡng đĩa `< 2 GB`, và lệnh `POST /api/attendance/revoke` đồng bộ hủy chính xác chứng từ trên HRM.
3. **Kiểm chứng Crossing Quarantine & Tải 10 Người Đồng thời (`K3`–`K4`)**:
   - Chạy bộ Replay `K0` trên các đoạn 2 người mặc đồng phục đi chéo: xác nhận **0 ca chốt nhầm tên** và chu kỳ xử lý (gồm decode 3MP + cắt ROI 1:1 + suy luận) giữ **< 200 ms** ở tải 10 khuôn mặt.
