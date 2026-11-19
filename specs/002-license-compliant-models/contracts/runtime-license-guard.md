# Contract: Runtime license guard, config & CLI

## 1. Cấu hình (`config.yaml`)

| Khoá | Cũ | Mới | Ghi chú |
|---|---|---|---|
| `pipeline.backend` | `edgeface` (mặc định), `opencv`, `onnx` | `opencv` (mặc định, duy nhất) | Giá trị `edgeface`/`onnx` sẽ gây **lỗi khởi động** với thông báo trỏ tới tài liệu này |
| `pipeline.thresholds.edgeface`, `.onnx` | có | **xoá** | Thêm/hiệu chỉnh `thresholds.opencv` từ replay K0 |
| `pose.engine` (mới) | — | `pnp5` (mặc định) \| `mediapipe` (tuỳ chọn, R2) | |
| `pose.max_reliable_yaw` (mới) | — | `60.0` | Ngoài ngưỡng này thì `pose_reliable=False` |
| `audio_prompt.backend` | `tapo` | `web` | `tapo` vẫn hợp lệ nhưng in cảnh báo ToS |
| `license.research_mode` (mới) | — | `false` | Chỉ dành cho dev; khi `true` thì log WARNING mỗi phút và `/api/status` trả `license_ok=false` |

## 2. Hành vi khởi động

Mỗi process tự kiểm tra: process chính **và** worker `worker_perception.py` đều tự nạp manifest, không tin kết quả gửi qua IPC.

```
Bước 1, nạp manifest: FAIL-CLOSED, KHÔNG có ngoại lệ research_mode
    server/models/manifest.json không tồn tại      → LicenseError("LICENSE: thiếu server/models/manifest.json")
    JSON hỏng / thiếu field bắt buộc               → LicenseError("LICENSE: manifest.json không hợp lệ: <chi tiết>")
    schema_version != 1                            → LicenseError("LICENSE: manifest.json không hợp lệ: schema_version=<v>")

Bước 2, với mỗi model engine/pose/liveness/context sắp nạp:
    mục manifest thiếu            → LicenseError("LICENSE: <file> không có trong manifest")
    commercial_use != allowed
      hoặc license_weights ∉ allow-list → LicenseError("LICENSE: <file> không được phép thương mại")
    sha256 lệch                   → LicenseError("LICENSE: <file> sai checksum")
  Chỉ ở bước 2: nếu license.research_mode=true thì hạ xuống log WARNING và /api/status.license_ok=false.

Bước 3, backend đã gỡ:
    pipeline.backend ∈ {edgeface, onnx} → LicenseError("LICENSE: backend '<x>' đã bị gỡ ...")
    (KHÔNG có ngoại lệ research_mode, vì code của các backend này đã bị xoá)

Khi gặp LicenseError lúc khởi động: server/main.py log ERROR rồi sys.exit(2).
```

| Tình huống | `research_mode=false` (mặc định) | `research_mode=true` |
|---|---|---|
| Thiếu / hỏng manifest | Từ chối khởi động | **Từ chối khởi động** |
| Model không có trong manifest / không thương mại / sai SHA | Từ chối khởi động | WARNING, `license_ok=false` |
| `backend: edgeface` / `onnx` | Từ chối khởi động | **Từ chối khởi động** |
| File lạ trên đĩa nhưng không được nạp | Chạy bình thường (chỉ `license_gate.py` fail) | Như cột bên |

## 3. API `/api/status` (bổ sung trường, không phá tương thích)

```json
{
  "backend": "opencv",
  "model_version": "yunet2023mar+sface2021dec",
  "pose_engine": "pnp5",
  "license_ok": true,
  "license_models": [
    {"file": "face_recognition_sface_2021dec.onnx", "license_weights": "Apache-2.0", "commercial_use": "allowed"}
  ]
}
```

## 4. CLI

| Lệnh | Exit code | Mô tả |
|---|---|---|
| `python server/scripts/download_models.py` | 0 / 1 | Tải mọi model trong manifest, xác minh SHA-256 đầy đủ. **Bỏ cờ `--onnx`** |
| `python server/scripts/download_models.py --verify-only` | 0 / 1 | Kiểm tra tồn tại + checksum |
| `python server/scripts/license_gate.py` | 0 pass / 1 fail | Chạy các quy tắc 1–5 trong data-model.md, in bảng kết quả |
| `python server/scripts/migrate_gallery.py --from <ver> --to auto [--dry-run]` | 0 / 1 | Re-embed từ `crop_path`; in thống kê migrated / needs_reenroll |
