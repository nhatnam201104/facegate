# Quickstart & Validation Guide: Tối ưu Độ trễ và Hiệu năng Luồng Stream

**Feature**: `002-low-latency-stream`
**Date**: 2026-11-12

Tài liệu này hướng dẫn cách chạy kiểm thử tự động, đo kiểm định lượng tốc độ dựng/phân phối khung hình trên CPU và kịch bản nghiệm thu thực tế trên trình duyệt & môi trường mô phỏng máy chủ cho tính năng `002-low-latency-stream`.

---

## 1. Điều kiện tiên quyết

- Đã cài đặt môi trường ảo `.venv` của dự án (`./setup.sh` trên Linux hoặc `setup.bat` trên Windows) — **không cần cài thêm bất kỳ thư viện bên thứ ba hay tải thêm mô hình mới nào**.
- Cấu hình mặc định trong `config.yaml` (chi tiết tại [contracts/config-schema.md](contracts/config-schema.md)) tự động kích hoạt chế độ tách luồng `24 FPS`, nén JPEG chất lượng `72` và cờ RTSP độ trễ thấp.

---

## 2. Chạy Bộ Kiểm thử Tự động (`pytest`)

```bash
.venv/bin/pytest -v
```

Các nhóm bài kiểm thử cần vượt qua 100%:
1. **`tests/test_stream_latency.py` *(Mới)***:
   - **Tách luồng không chặn (*Non-blocking Render under Slow AI*)**: Giả lập chu kỳ suy luận `_process` tốn `220 ms`; chứng minh luồng `StreamComposer` vẫn xuất đều đặn `≥ 20 FPS` trong suốt thời gian suy luận mà không bị khựng hình (`FR-001`, `SC-002`).
   - **Chống trễ dồn (*Latest-Frame Drop for Slow Client*)**: Giả lập 1 người xem mạng chậm (mỗi `250 ms` mới đọc 1 frame) song song với 1 người xem mạng nhanh; chứng minh người xem mạng nhanh vẫn nhận đủ tốc độ gốc và người xem mạng chậm luôn nhảy thẳng tới `seq` mới nhất ngay ở lần đọc kế tiếp (`FR-004`, `SC-003`).
   - **Zero-Viewer Idle & Shared Encoding**: Chứng minh khi `viewer_count == 0`, hàm nén JPEG stream không được gọi (`0` lần encode); khi có `5` người xem đồng thời, mỗi frame nguồn chỉ được nén JPEG đúng `1` lần (`FR-005`, `FR-006`, `SC-004`).
   - **ROI Label Sprite Cache & Cắt biên an toàn**: Chứng minh `draw_overlay()` với nhãn tiếng Việt có dấu trả kết quả chính xác, không tràn biên khi hộp mặt nằm sát rìa khung hình và đạt tốc độ trung bình `< 1.0 ms/frame` ở độ phân giải `960×540` (`FR-003`).
   - **HTTP Headers chống đệm Proxy**: Kiểm tra `/video.mjpg` và `/video_raw.mjpg` trả đúng `X-Accel-Buffering: no` và `Cache-Control: no-store...` theo [contracts/api-and-stream.md](contracts/api-and-stream.md).
2. **Toàn bộ bộ kiểm thử hiện hữu** (`test_camera.py`, `test_pose3d.py`, `test_tracker_buckets.py`, `test_liveness_profile.py`, `test_guided.py`, `test_api_contracts.py`): Đảm bảo 0 hồi quy nghiệp vụ nhận diện ±90° và chống giả mạo (`FR-010`, `SC-005`).

---

## 3. Đo Kiểm Hiệu năng Stream & CPU (`benchmark.py`)

Chạy công cụ benchmark kèm chế độ đo luồng hiển thị:

```bash
.venv/bin/python scripts/benchmark.py --stream-bench
```

**Kỳ vọng đầu ra**:
- Thời gian `draw_overlay + JPEG encode (960x540)` trung bình **< 3.5 ms/frame** trên CPU phổ thông.
- Tốc độ phát `stream_fps` duy trì **~24 FPS** ổn định ngay cả khi chu kỳ nhận diện đa góc chạy song song ở `5 FPS`.

---

## 4. Kịch bản Nghiệm thu Thực tế (LAN & Mô phỏng Lên Server)

1. **Khởi chạy ứng dụng**:
   ```bash
   ./run.sh
   ```
   Truy cập `http://127.0.0.1:8090` (hoặc `http://<IP-Server>:8090` từ máy khác trong mạng LAN).

2. **Bước 1 — Đối chiếu độ trễ với ứng dụng Camera gốc (US1)**:
   - Mở đồng thời ứng dụng xem camera gốc (hoặc đặt điện thoại bấm giờ mili-giây trước ống kính) và tab **Trực tiếp** của FaceGate.
   - Bước qua lại và vẫy tay khi có 1–3 người trong khung hình: kiểm tra thanh trạng thái báo `stream ~24 FPS`, chuyển động mượt không khựng theo nhịp detect, chênh lệch thời gian giữa hai màn hình **≤ 300 ms**.

3. **Bước 2 — Kiểm tra vòng đời đóng/mở kết nối khi chuyển Tab (US2 & US3)**:
   - Chuyển từ tab **Trực tiếp** sang tab **Chấm công** hoặc **Nhân viên**, sau đó gọi `curl -s http://127.0.0.1:8090/api/status`: kiểm tra `stream_viewers` giảm về `0` và `stream_fps` về `0.0` (server ngưng nén JPEG).
   - Chuyển sang tab **Đăng ký** (chế độ quay từ camera): kiểm tra luồng soi gương mượt `~24 FPS` và `stream_viewers == 1` (chỉ mở `/video_raw.mjpg`, không chạy ngầm `/video.mjpg`).

4. **Bước 3 — Mô phỏng mạng Server chập chờn không bị trễ dồn (US2)**:
   - Mở Chrome DevTools $\rightarrow$ tab **Network** $\rightarrow$ chọn chế độ giới hạn băng thông **Fast 3G / Slow 4G (1.5–3 Mbps)** trong 15 giây khi đang có người di chuyển trước camera, sau đó chuyển lại **No throttling**.
   - Xác nhận hình ảnh lập tức hiển thị đúng vị trí hiện tại của người trước ống kính trong vòng **< 2 giây**, không bị phát trễ lại các bước đi của 15 giây trước.

5. **Bước 4 — Cấu hình Reverse Proxy khi đưa lên Linux Server (Nginx mẫu)**:
   - Nhờ header `X-Accel-Buffering: no` đã gắn sẵn từ ứng dụng, Nginx tự động tắt đệm cho `/video.mjpg` và `/video_raw.mjpg`; chỉ cần cấu hình chuẩn cho WebSocket `/ws` và timeout dài cho kết nối stream trực tiếp.
