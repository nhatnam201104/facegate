# Kết quả đo: 002-license-compliant-models

## T028: Migration gallery trên DB thật (2026-11-18)

- Sao lưu trước khi chạy: `data/facegate.db.bak-002`.
- `--dry-run`: migrated=1, needs_reenroll=0, vectors_added=2.
- Chạy thật: `yunet2023mar+edgeface_xs_gamma_06_v41 → yunet2023mar+sface2021dec`, migrated=1 (NV002), needs_reenroll=0, vectors_added=2. Đã có audit log `gallery_migrate`.
- Gallery SFace sau migration: frontal 5 (đăng ký SFace ngày 2026-11-17), left_profile 1, right_profile 1 (tạo từ crop `05`/`06`). Không bật `--mirror-profiles`.
- Hàng EdgeFace cũ **giữ nguyên** (chưa xoá, chờ Pháp chế).

## Đo trên ảnh đăng ký NV002 (SFace + PnP 5 điểm)

| Ảnh | yaw PnP | yaw 3DDFA cũ (lưu trong DB) | cos so với ảnh chính diện |
|---|---|---|---|
| 01 trai-giua | −28.3 | −31.6 | 0.812 |
| 02 phai-giua | 7.8 | 18.1 | 0.724 |
| 03 thang-ngang | −0.7 | 0.3 | 0.751 |
| 04 thang-cui | 0.0 | 0.6 | 0.857 |
| 05 nghiêng trái | −53.6 | −62.8 | 0.478 |
| 06 nghiêng phải | 59.3 | 68.7 | 0.405 |

So sánh các cách cải thiện góc nghiêng (cos của ảnh nghiêng):

| Cách | Trái | Phải |
|---|---|---|
| Hiện tại (so ảnh chính diện) | 0.478 | 0.405 |
| Lật ảnh khi tính embedding (flip TTA) | 0.496 | 0.445 |
| Template căn chỉnh riêng cho ảnh nghiêng | 0.357 | 0.075 (loại) |
| So với ảnh mẫu cùng góc (gương của bên kia) | 0.545 | |

## Đo trên video thực tế (screencast 2026-11-18 16:28, camera RTSP Tapo, 19 khung)

- yaw PnP 42–65° ở **cả 19 khung** (người ngồi lệch so với camera; điểm mốc YuNet đúng vị trí khi kiểm tra bằng mắt).
- Độ tương đồng tốt nhất với gallery SFace: **0.48–0.80, trung bình 0.695** (ngưỡng frontal 0.42, profile 0.48). Thêm mẫu nghiêng làm trung bình đổi từ 0.695 lên 0.696.
- **Kết luận**: trong video này nhận diện danh tính **không** phải điểm yếu. Check-in bị chặn vì liveness chỉ lấy mẫu khi |yaw| ≤ 40° (`require_frontal_for_checkin`). Đây là đúng thiết kế, nhưng giao diện cần hướng dẫn người dùng (xem đề xuất lỗi 3 trong phiên làm việc).
- Lưu ý: chỉ có 1 người, chưa đo FAR. SC-002 và SC-006 vẫn cần replay K0.
