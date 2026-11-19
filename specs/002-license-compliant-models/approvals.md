# Phê duyệt: 002-license-compliant-models

Các mục dưới đây là **cổng chặn** của tasks.md. Chỉ đổi `PENDING` thành `APPROVED` hoặc `REJECTED` khi đã có văn bản ký.

| Mục | Nội dung cần duyệt | Người duyệt | Chặn task | Trạng thái | Người ký | Ngày | Mã văn bản |
|---|---|---|---|---|---|---|---|
| **C** | Sửa Hiến pháp lên v1.2.0 theo [constitution-v1.2.0-proposed.md](constitution-v1.2.0-proposed.md) | PM + Pháp chế | T012 trở đi | **APPROVED** (áp dụng sau khi chốt LA, xem ghi chú) | Chủ dự án | 2026-11-18 | Xác nhận trực tiếp trong phiên làm việc; **cần bổ sung số văn bản** |
| **L1** | Cho phép dùng EdgeFace-XS / 3DDFA_V2 / BFM để đo baseline nội bộ | Pháp chế | T001 bước 2–5 | **REJECTED**: "xoá hoàn toàn, tránh rủi ro pháp lý không đáng có" | Chủ dự án | 2026-11-18 | Xác nhận trực tiếp; **cần bổ sung số văn bản** |
| **S6** | Giá trị SC-006 | PM | T026 | **APPROVED**: dùng giá trị mặc định (chế độ tuyệt đối, vì L1 bị từ chối, xem spec.md SC-006) | Chủ dự án | 2026-11-18 | Xác nhận trực tiếp |
| **LA** | Chấp nhận rủi ro tồn dư về dữ liệu huấn luyện cho 5 model trong `server/models/manifest.json` | Pháp chế | T036 | **APPROVED CÓ ĐIỀU KIỆN** (sửa lại sau lần đầu REJECTED): chỉ chấp nhận **theo từng model** có trọng số permissive từ nhà phát hành lớn (OpenCV Zoo, Megvii, Minivision); **Pháp chế ký riêng từng model** bằng cách điền `legal_ack` thật vào manifest. Model nào chưa ký vẫn là PENDING và `license_gate.py --strict` sẽ fail | Chủ dự án | 2026-11-18 | Xác nhận trực tiếp; chữ ký từng model: **chờ Pháp chế** |

## Đã thực hiện theo L1 (2026-11-18)

- Xoá khỏi đĩa: `server/models/edgeface_xs_gamma_06.onnx` và `server/models/3ddfa_v2_mb1_120x120.onnx`.
- `git rm` 5 file `server/models/tddfa_*.bin` khỏi HEAD của nhánh `002-license-compliant-models`.
- **Quyết định 2026-11-18: không xoá thêm.** Chủ dự án tự xử lý: lịch sử git trên GitHub (T038, force-push) và các bản sao ngoài repo (`~/code/facegate/server/models/`, `~/code/msoftware-ai-camera-attendance (Copy)/server/models/`). Các bản sao này vẫn còn EdgeFace/3DDFA/BFM.
- Hệ quả: không còn baseline EdgeFace. Nghiệm thu dùng **ngưỡng tuyệt đối** (spec.md SC-002, SC-006).

## Lịch sử: hệ quả khi LA từng là REJECTED (đã được thay bằng "chấp nhận theo từng model")

Cả 5 model còn lại đều có trọng số mang giấy phép permissive, nhưng được huấn luyện trên dữ liệu không cấp quyền thương mại hoặc không công bố:

| Model | Vai trò | Dữ liệu huấn luyện |
|---|---|---|
| YuNet | Phát hiện mặt | WIDER FACE (chỉ cho nghiên cứu) |
| SFace | Nhận diện | CASIA-WebFace + MS1M (nghiên cứu; MS1M đã bị Microsoft rút) |
| YOLOX-Nano | Ngữ cảnh người / thiết bị | COCO (ảnh Flickr với nhiều loại giấy phép CC) |
| MiniFASNet ×2 | Chống giả mạo | Không công bố |

Nếu LA là REJECTED tuyệt đối thì không model công khai nào dùng được. Chủ dự án đã chọn **chấp nhận theo từng model**, nên plan "SFace + PnP" vẫn còn hiệu lực.
