# Quickstart & Validation Guide: Nhận diện 90° & Chống giả mạo Đa lớp

**Feature**: `001-profile-90-antispoof`
**Date**: 2026-11-11

Tài liệu này mô tả quy trình cài đặt mô hình mới, chạy bộ kiểm thử tự động và kịch bản nghiệm thu thực tế cho tính năng nhận diện góc nghiêng tới 90° kèm chống giả mạo trên 1 camera.

---

## 1. Điều kiện tiên quyết & Cài đặt mô hình

- Môi trường Linux (`./setup.sh`) hoặc Windows (`setup.bat`) với Python 3.10+ và `.venv` đã sẵn sàng.
- Tải trọn bộ mô hình ONNX (bao gồm các mô hình mới `3ddfa_v2_mb1_120x120.onnx`, `edgeface_xs_gamma_06.onnx`, `yolox_nano_coco.onnx` cùng bộ 4 mô hình hiện tại):

```bash
# Trên Linux
./setup.sh

# Hoặc tải trực tiếp bằng script Python
.venv/bin/python scripts/download_models.py
```

*(Tùy chọn đối chuẩn nghiên cứu với AdaFace/InsightFace: `.venv/bin/python scripts/download_models.py --onnx --research`)*

---

## 2. Chạy Kiểm thử Tự động (Unit & Integration Tests)

```bash
.venv/bin/pytest -v
```

Các nhóm bài kiểm thử bắt buộc phải vượt qua:
1. `tests/test_pose3d.py`: Kiểm tra ước lượng `yaw`, `pitch`, `roll` từ `3DDFA_V2` và phân loại đúng 3 nhóm `frontal` / `left_profile` / `right_profile` (bao gồm dải trễ hysteresis).
2. `tests/test_tracker_buckets.py`: Xác minh khi một người xoay đầu liên tục từ `0° → -80° → +80°`, tracker giữ nguyên **1 ID duy nhất**, không trộn lẫn vector giữa 3 ngăn và ưu tiên chọn ngăn `frontal` (*Best-shot*) khi có đủ 2 frame.
3. `tests/test_liveness_profile.py`:
   - Ngăn chặn trường hợp có `cell phone` / tablet quanh mặt hoặc 2 mặt trên 1 khung cơ thể (`spoof`).
   - Ngăn chặn quỹ đạo phẳng 2D không có thị sai 3D (`planar_no_parallax`).
   - Cho phép hành trình 3D hợp lệ ở góc 80°–85° đi qua với nhãn `profile_verified`.
4. `tests/test_guided.py` & `tests/test_camera.py`: Đảm bảo 7 bước đăng ký hướng dẫn (và chế độ 5 bước tương thích cũ) hoạt động chính xác, không loại nhầm mẫu góc 80° là outlier.

---

## 3. Đo kiểm Hiệu năng & Độ chính xác trên Video Thật (`benchmark.py`)

Quay 3 đoạn video ngắn tại vị trí lắp camera cổng thực tế (1–2 phút/video):
1. `data/eval_walk_profile.mp4`: Nhân viên đã đăng ký + người lạ đi ngang tự nhiên qua cổng ở góc 60°–90°.
2. `data/eval_spoof_phone.mp4`: Cầm điện thoại/iPad mở ảnh và video quay góc nghiêng 85° của nhân viên đi ngang qua cổng.

Chạy công cụ đo:
```bash
.venv/bin/python scripts/benchmark.py data/eval_walk_profile.mp4 --profile-eval
```
**Kỳ vọng đầu ra**:
- Chu kỳ xử lý trung bình **< 200 ms** trên CPU (p90 **< 280 ms**).
- Báo cáo tách riêng tỷ lệ phát hiện, phân bố góc `yaw` và độ trễ của từng mô hình (`YuNet`, `YOLOX-Nano`, `3DDFA_V2`, `EdgeFace-XS`, `Liveness`).

---

## 4. Kịch bản Nghiệm thu End-to-End trên Giao diện Web

1. Khởi chạy hệ thống:
   ```bash
   ./run.sh
   ```
   Mở `http://127.0.0.1:8090`.
2. **Bước 1 — Đăng ký 7 góc**:
   - Vào tab **Đăng ký**, nhập mã `NV901` và họ tên, bấm **Bắt đầu quay**.
   - Thực hiện 5 góc chuẩn + 2 bước mới (*"Quay hẳn mặt sang phải ~80°"* và *"Quay hẳn mặt sang trái ~80°"*).
   - Sang tab **Nhân viên**, kiểm tra cột **Embedding** hiện huy hiệu **`Đủ góc 90°`** và bấm **Duyệt**.
3. **Bước 2 — Kiểm tra Best-shot & Profile Check-in**:
   - Đi ngang qua camera (không dừng lại nhìn ống kính) giữ góc mặt ~80°–85° trong ~1.2 giây:
     - Màn hình Trực tiếp hiện khung **Xanh lá**, thẻ chào nhân viên kèm nhãn **`Góc nghiêng · Đã lưu clip`**, bản ghi trong tab **Sự kiện** mang `verification_tier = profile_verified` (theo hợp đồng tại [api-and-websocket.md](contracts/api-and-websocket.md)).
4. **Bước 3 — Kiểm tra Chống giả mạo góc 90°**:
   - Mở ảnh hoặc video quay góc 85° của `NV901` trên điện thoại, cầm đi ngang qua camera:
     - Khung chuyển sang **Đỏ** với cảnh báo cụ thể (*"GIẢ MẠO (màn hình / ảnh phẳng) - không chấm công"*), sự kiện `spoof` được ghi lại kèm clip bằng chứng trong tab **Lịch sử**.
