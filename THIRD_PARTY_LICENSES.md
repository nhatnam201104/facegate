# Thành phần bên thứ ba

Danh sách các thành phần không do dự án viết, kèm giấy phép. Model AI được quản lý chi tiết (nguồn, SHA-256, dữ liệu huấn luyện, chữ ký Pháp chế) trong [`server/models/manifest.json`](server/models/manifest.json) và kiểm tra bằng `python server/scripts/license_gate.py --strict`.

Cột **usage**: `linked` = import trong tiến trình Python · `subprocess` = chạy chương trình riêng · `asset` = file đi kèm · `optional` = chỉ khi bật.

## Model AI (trọng số)

| name | version | license (SPDX) | usage | url | notes |
|---|---|---|---|---|---|
| YuNet face detector | 2023mar | MIT | asset | https://github.com/opencv/opencv_zoo | Dữ liệu huấn luyện: WIDER FACE (nghiên cứu), rủi ro tồn dư chờ Pháp chế ký |
| SFace face recognizer | 2021dec | Apache-2.0 | asset | https://github.com/opencv/opencv_zoo | CASIA-WebFace + MS1M (nghiên cứu), rủi ro tồn dư chờ Pháp chế ký |
| YOLOX-Nano | 0.1.1rc0 | Apache-2.0 | asset | https://github.com/Megvii-BaseDetection/YOLOX | COCO 2017 |
| MiniFASNetV2, MiniFASNetV1SE | n/a | Apache-2.0 | asset | https://github.com/minivision-ai/Silent-Face-Anti-Spoofing | ONNX do https://github.com/yakhyo/face-anti-spoofing chuyển đổi (Apache-2.0) |

Bản sao văn bản giấy phép: `third_party/licenses/`.

## Thư viện Python (requirements.txt)

| name | version (môi trường dev 2026-11-18) | license (SPDX) | usage | url | notes |
|---|---|---|---|---|---|
| opencv-python / opencv-python-headless | 4.14.0 | Apache-2.0 | linked | https://github.com/opencv/opencv-python | Wheel có kèm FFmpeg (LGPL-2.1+), liên kết động |
| numpy | 2.5.5 | BSD-3-Clause | linked | https://numpy.org | |
| fastapi | 0.163.1 | MIT | linked | https://github.com/fastapi/fastapi | Kéo theo starlette (BSD-3-Clause), pydantic (MIT) |
| uvicorn[standard] | 0.54.0 | BSD-3-Clause | linked | https://github.com/encode/uvicorn | Kéo theo uvloop (MIT/Apache-2.0), httptools (MIT), watchfiles (MIT), websockets (BSD-3-Clause), python-dotenv (BSD-3-Clause) |
| python-multipart | 0.0.34 | Apache-2.0 | linked | https://github.com/Kludex/python-multipart | |
| PyYAML | 6.0.3 | MIT | linked | https://github.com/yaml/pyyaml | |
| onnxruntime | 1.32.0 | MIT | linked | https://github.com/microsoft/onnxruntime | |
| pillow | 13.0.0 | MIT-CMU | linked | https://github.com/python-pillow/Pillow | |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | linked | https://github.com/pyca/cryptography | |
| psycopg[binary] | 3.x | LGPL-3.0-only | optional, linked | https://github.com/psycopg/psycopg | Chỉ khi dùng PostgreSQL; liên kết động, không sửa mã nguồn |
| pytapo | 3.x | MIT | optional, linked | https://github.com/JurajNyiri/pytapo | `audio_prompt.backend: tapo`. API TP-Link **không chính thức**: kiểm tra điều khoản sử dụng (ToS) với khách |

## Chương trình chạy riêng

| name | license (SPDX) | usage | url | notes |
|---|---|---|---|---|
| MediaMTX | MIT | subprocess | https://github.com/bluenviron/mediamtx | Binary riêng, cấu hình `deploy/mediamtx.yml` |
| FFmpeg | LGPL-2.1-or-later hoặc GPL-2.0-or-later (tuỳ bản build) | subprocess | https://ffmpeg.org | Gọi qua dòng lệnh. **Nếu đóng gói kèm một bản build GPL trong bộ cài thì phải cung cấp mã nguồn tương ứng** |

## Frontend (tuỳ chọn)

| name | license (SPDX) | usage | url | notes |
|---|---|---|---|---|
| MediaPipe Tasks Vision (Face Landmarker WASM + `face_landmarker.task`) | Apache-2.0 | optional, asset | https://github.com/google-ai-edge/mediapipe | Hiện **chưa** đặt trong repo; khi đặt vào `webapp/static/vendor/` phải kèm LICENSE và NOTICE |

## Tài nguyên

| name | license | usage | notes |
|---|---|---|---|
| `webapp/audio/look_at_camera.wav` | Sở hữu của công ty (do dự án tự tạo) | asset | Hồ sơ: [`third_party/provenance/look_at_camera.md`](third_party/provenance/look_at_camera.md) |

## Đã loại bỏ (feature 002, không được dùng lại)

EdgeFace-XS (CC BY-NC-SA 4.0), 3DDFA_V2 + tham số Basel Face Model (phi thương mại), InsightFace SCRFD/ArcFace `w600k_mbf` (phi thương mại).
