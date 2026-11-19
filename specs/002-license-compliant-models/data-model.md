# Data Model: 002-license-compliant-models

## ModelArtifact (mục trong `server/models/manifest.json`)

| Trường | Kiểu | Bắt buộc | Ràng buộc |
|---|---|---|---|
| `file` | string | ✓ | Tên file trong `server/models/`, duy nhất |
| `role` | enum | ✓ | `face_detect` · `face_embed` · `pose` · `liveness` · `context_detect` |
| `version` | string | ✓ | Dùng để ghép thành `model_version` của engine |
| `source_url` | string (URL) | ✓ | Ưu tiên release chính thức của tác giả |
| `upstream_repo` | string (URL) | ✓ | Repo gốc của tác giả (khác `source_url` nếu tải qua bản chuyển đổi) |
| `sha256` | string | ✓ | 64 ký tự hex, **đầy đủ**; `download_models.py` từ chối khi lệch |
| `min_bytes` | int | ✓ | > 0; phát hiện con trỏ Git LFS |
| `license_code` | SPDX id | ✓ | VD `Apache-2.0`, `MIT` |
| `license_weights` | SPDX id | ✓ | Giấy phép **của file trọng số**; phải ∈ allow-list |
| `license_file` | path | ✓ | Bản sao LICENSE lưu ở `third_party/licenses/` |
| `training_data` | string[] | ✓ | Tên bộ dữ liệu huấn luyện (khai báo trung thực) |
| `training_data_risk` | enum | ✓ | `none` · `residual_accepted` · `blocking` |
| `commercial_use` | enum | ✓ | `allowed` · `forbidden` · `unknown` |
| `legal_ack` | object \| null | khi `training_data_risk=residual_accepted` | `{by, date, ref}`: người ký và văn bản chấp nhận của Pháp chế |
| `notice` | string | — | Dòng ghi công đưa vào `NOTICE` |

**Allow-list `license_weights`**: `MIT`, `Apache-2.0`, `BSD-2-Clause`, `BSD-3-Clause`, `ISC`, `Zlib`, `CC0-1.0`, `CC-BY-4.0`.

**Quy tắc kiểm tra (license gate)**:
1. Mọi file `*.onnx`, `*.bin`, `*.task`, `*.pth`, `*.pt` trong `server/models/` phải có mục manifest. Ngoại lệ duy nhất là `.gitkeep`.
2. `commercial_use == "allowed"` **và** `license_weights` ∈ allow-list.
3. `training_data_risk == "blocking"` thì fail; `residual_accepted` thì bắt buộc có `legal_ack`.
4. Không file nào khớp `tddfa_*`, `*bfm*`, `*3ddfa*`, `*edgeface*`, `*w600k*`, `*scrfd*` tồn tại trong repo (`git ls-files`) hoặc `server/models/`.
5. `sha256` thực tế của file trên đĩa (nếu có) phải khớp manifest.

## ThirdPartyComponent (dòng trong `THIRD_PARTY_LICENSES.md`)

Gồm các thành phần không phải model: thư viện Python, MediaMTX, FFmpeg (gọi qua subprocess), pytapo (tuỳ chọn), MediaPipe WASM (tuỳ chọn), file âm thanh.
Trường: `name`, `version`, `license` (SPDX), `usage` (linked/subprocess/asset/optional), `url`, `notes`.

## Thay đổi dữ liệu runtime

### `embeddings.model_version`
- Giá trị cũ: `yunet2023mar+edgeface_xs_gamma_06_v41`.
- Giá trị mới: `yunet2023mar+sface2021dec` (do `OpenCVEngine.model_version` sinh ra).
- **Không đổi schema**. Migration chỉ chèn thêm các hàng mới cho `model_version` mới; hàng cũ giữ lại cho tới khi Pháp chế xác nhận rồi mới xoá (nằm ngoài `retention_days`).

### Trạng thái migration re-embed (mỗi nhân viên)

```
pending ──re-embed từ crop_path──▶ migrated
   │                                  
   └── crop thiếu/hỏng/giải mã lỗi ──▶ needs_reenroll (HR mời đăng ký lại)
```

Kết quả ghi vào `audit_logs` (action=`gallery_migrate`, employee_id, from_version, to_version, n_vectors, status).

### `PoseResult` (giữ nguyên interface, thêm 1 trường)

| Trường | Thay đổi |
|---|---|
| `yaw, pitch, roll` | Tính bằng `solvePnP` trên 5 điểm + mô hình 3D tự định nghĩa, làm mượt EMA theo giây |
| `kps5` | = kps của YuNet (không còn sinh từ 68 điểm 3DDFA) |
| `landmarks3d` | Xấp xỉ (giữ shape `(68,3)` để không vỡ code tiêu thụ) |
| `visibility` | `cos(|yaw|)`, ≤ 0.2 khi |yaw| > 60° |
| **`pose_reliable`** (mới, `bool`, mặc định `True`) | `False` khi |yaw| > `pose.max_reliable_yaw` (mặc định 60°) hoặc lỗi reprojection của PnP vượt ngưỡng |
