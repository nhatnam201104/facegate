# Implementation Plan: Chuyển toàn bộ model/công nghệ sang giấy phép thương mại hóa được

**Branch**: `002-license-compliant-models` | **Date**: 2026-11-18 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/002-license-compliant-models/spec.md`

## Summary

Gỡ mọi thành phần phi thương mại khỏi FaceGate v4 và thay bằng thành phần có giấy phép MIT/Apache/BSD:

| Hiện tại (vi phạm) | Thay bằng | Giấy phép mới |
|---|---|---|
| EdgeFace-XS (CC BY-NC-SA) | **SFace**, OpenCV Zoo (`OpenCVEngine` đã có sẵn) | Apache-2.0 |
| 3DDFA_V2 + BFM `.bin` | **`solvePnP` trên 5 điểm YuNet** + mô hình 3D tự định nghĩa; MediaPipe là tuỳ chọn | Apache-2.0 (OpenCV) / tự viết |
| InsightFace SCRFD/ArcFace | **Xoá** | — |
| `pytapo` mặc định | Backend loa `web` mặc định | — |
| WAV thiếu hồ sơ (tác giả dự án tự tạo) | Giữ nguyên, bổ sung hồ sơ nguồn gốc | Sở hữu |
| Không có hồ sơ | `manifest.json` + `license_gate.py` + `NOTICE` + `THIRD_PARTY_LICENSES.md` | — |

Chi tiết lựa chọn và phương án loại bỏ: [research.md](research.md).

## Technical Context

**Language/Version**: Python 3.11+ (backend), JS ES2020 (webapp)

**Primary Dependencies**: OpenCV ≥ 4.8 (`FaceDetectorYN`, `FaceRecognizerSF`, `solvePnP`), onnxruntime ≥ 1.17, FastAPI. **Không thêm dependency bắt buộc mới**; `mediapipe` chỉ là tuỳ chọn (R2).

**Storage**: SQLite `data/facegate.db`, bảng `embeddings` (không đổi schema, chỉ thêm hàng theo `model_version` mới)

**Testing**: pytest `server/tests/` + `replay_eval.py` (K0) + `benchmark.py` (K4)

**Target Platform**: Linux server, chỉ CPU (OpenVINO EP tuỳ chọn)

**Project Type**: web-service (FastAPI + webapp tĩnh)

**Performance Goals**: K4 ≤ 200 ms/chu kỳ cho 10 khuôn mặt, đo trọn pipeline

**Constraints**: FAR không tăng so với baseline; không hạ ngưỡng; 0đ phần cứng thêm

**Scale/Scope**: ~5 file nguồn chính, 3 script mới, 1 migration dữ liệu, tài liệu và Hiến pháp

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Nguyên tắc | Trước thiết kế | Sau thiết kế |
|---|---|---|
| **III. Giấy phép thương mại (MIT/Apache/BSD)** | ❌ **VI PHẠM**: Hiến pháp v1.1.0 tự liệt kê `3DDFA_V2`, `EdgeFace-XS` là thương mại hoá được, nhưng thực tế không phải | ✅ Sau khi thay model **và sửa đổi Hiến pháp lên v1.2.0** (bỏ 2 tên này, thêm yêu cầu manifest + license gate). Theo Governance, cần **PM + Pháp chế phê duyệt bằng văn bản** |
| III. Benchmark full-pipeline | — | ✅ SC-003 bắt buộc đo lại K4 trọn chuỗi; không lấy benchmark riêng SFace làm đại diện |
| II. Không hạ ngưỡng để đổi FAR | ⚠ SFace có phân bố điểm khác, nên phải hiệu chỉnh lại ngưỡng | ✅ Hiệu chỉnh `thresholds.opencv` bằng replay K0 với ràng buộc FAR ≤ baseline; pose > 60° thì `Unknown` |
| I. Thời gian thực theo giây | — | ✅ EMA pose dùng hằng số thời gian (giây), không theo số frame |
| IV. Sổ cái / idempotency | — | ✅ Không chạm luồng ERP; migration ghi `audit_logs` |
| V. Sinh trắc học | — | ✅ Re-embed từ crop đã mã hoá và giải mã trong RAM; không xuất crop ra ngoài; giữ `retention_days` |
| Quality gate: pytest 100% xanh | — | ✅ SC-004; cập nhật các test đang hard-code `EdgeFaceEngine` |

**Kết luận gate**: Được phép tiếp tục. Vi phạm duy nhất (III) chính là mục tiêu của feature, và được giải quyết bằng sửa đổi Hiến pháp ở Phase A.

## Project Structure

### Documentation (this feature)

```text
specs/002-license-compliant-models/
├── spec.md
├── plan.md                               # file này
├── research.md                           # Phase 0
├── data-model.md                         # Phase 1
├── quickstart.md                         # Phase 1
├── contracts/
│   ├── model-manifest.schema.json
│   └── runtime-license-guard.md
└── tasks.md                              # /speckit-tasks (chưa tạo)
```

### Source Code (repository root)

```text
NOTICE                                    # MỚI
THIRD_PARTY_LICENSES.md                   # MỚI
third_party/
├── licenses/                             # MỚI: bản sao LICENSE của từng model
└── provenance/look_at_camera.md          # MỚI
config.yaml                               # backend: opencv, pose.engine, audio web
.specify/memory/constitution.md           # v1.2.0
server/
├── config.py                             # default mới, bỏ thresholds edgeface/onnx
├── models/
│   ├── manifest.json                     # MỚI
│   └── tddfa_*.bin                       # XOÁ (5 file)
├── vision/
│   ├── engines.py                        # xoá EdgeFaceEngine, OnnxEngine; guard manifest
│   ├── pose3d.py                         # PnP 5 điểm thay 3DDFA; pose_reliable
│   ├── license_guard.py                  # MỚI: đọc/kiểm manifest (dùng chung)
│   ├── pipeline.py / worker_perception.py  # tôn trọng pose_reliable; status license
│   └── liveness.py, context_detector.py  # guard manifest
├── enrollment/guided.py                  # góc nghiêng 80° → ~55–60°
├── scripts/
│   ├── download_models.py                # đọc manifest, SHA-256 đầy đủ, bỏ --onnx
│   ├── license_gate.py                   # MỚI
│   ├── migrate_gallery.py                # MỚI
│   └── benchmark.py                      # bỏ edgeface
└── tests/
    ├── test_license_gate.py              # MỚI
    ├── test_pose_pnp.py                  # MỚI
    ├── test_migrate_gallery.py           # MỚI
    └── test_accuracy_v4_1.py             # chuyển sang OpenCVEngine
webapp/audio/look_at_camera.wav           # giữ nguyên (tự tạo), chỉ thêm provenance
README.md, docs/*.html                    # sửa mô tả giấy phép
```

**Structure Decision**: Giữ nguyên cấu trúc `server/` hiện có (theo AGENTS.md). Logic kiểm giấy phép dùng chung đặt ở `server/vision/license_guard.py` để engine, pose, liveness và script gate cùng gọi.

## Lộ trình triển khai (theo pha)

| Pha | Nội dung | Phụ thuộc | Đầu ra / tiêu chí xong |
|---|---|---|---|
| **A. Chặn ngay** (0.5 ngày) | Đo **baseline** FAR/FRR/K4 với EdgeFace hiện tại → `baseline.json`. Sửa Hiến pháp v1.2.0 (chờ ký). Ghi nhãn README "không bán thương mại cho tới khi xong 002" | — | `baseline.json`, PR Hiến pháp |
| **B. Hồ sơ & gate** (1 ngày) | `manifest.json` cho model giữ lại (YuNet, SFace, YOLOX, MiniFASNet) với SHA-256 đầy đủ; tải LICENSE vào `third_party/licenses/`; `license_guard.py`, `license_gate.py`, `test_license_gate.py`; `download_models.py` đọc manifest | A | Gate chạy được (đang **fail** vì EdgeFace/3DDFA, đúng kỳ vọng) |
| **C. Thay nhận diện** (1.5 ngày) | Đổi mặc định sang `opencv`; xoá `EdgeFaceEngine`, `OnnxEngine`, `ONNX_MODELS`; `migrate_gallery.py` + test; hiệu chỉnh `thresholds.opencv` bằng replay K0 | B | Replay: FAR ≤ baseline |
| **D. Thay pose** (2 ngày) | `pose3d.py`: PnP 5 điểm + EMA + `pose_reliable`; xoá 5 file `.bin` và nhánh 3DDFA; pipeline bỏ qua khung `pose_reliable=False`; guided enrollment giới hạn góc; `test_pose_pnp.py` | B | Gate **pass**; pytest xanh |
| **E. Phụ trợ** (0.5 ngày) | Audio mặc định `web`; hồ sơ provenance cho WAV tự tạo; `NOTICE`, `THIRD_PARTY_LICENSES.md`; sửa docs/README/comment | — (song song C/D) | Kịch bản 6 của quickstart |
| **F. Nghiệm thu** (1 ngày) | Benchmark K4, replay K0, quickstart 1–7, Pháp chế ký `legal_ack` cho SFace/YuNet/YOLOX/MiniFASNet | C, D, E | SC-001…005 đạt |
| **G. Lịch sử git** (tách riêng, **cần chủ repo duyệt**) | `git filter-repo` xoá `tddfa_*.bin` khỏi lịch sử + force-push + yêu cầu mọi người clone lại | F | Chỉ làm nếu Pháp chế yêu cầu |

**Điểm quyết định sau Pha F**: nếu SFace + PnP **không** đạt FAR/K4, hoặc PM không chấp nhận mất góc ±60–90°, thì kích hoạt **phương án B**: mua giấy phép thương mại EdgeFace (Idiap). Phương án này vẫn đòi hỏi pose sạch (Pha D giữ nguyên) và manifest (`license_weights: LicenseRef-Idiap-Commercial`, cần mở rộng allow-list bằng văn bản).

## Rủi ro chính

| Rủi ro | Ảnh hưởng | Giảm thiểu |
|---|---|---|
| SFace kém hơn EdgeFace ở góc nghiêng | FRR tăng ở camera cổng | Nhắc nhìn camera (audio), giới hạn chốt danh tính ở |yaw| ≤ 60°, gom nhiều mẫu theo track |
| SFace chậm hơn, vượt K4 | Không đạt SC-003 | Chạy SFace qua ORT batch / OpenVINO EP; giảm tần suất embed mỗi track |
| Crop cũ thiếu/hỏng | Một số nhân viên phải đăng ký lại | Trạng thái `needs_reenroll` + thông báo HR; chạy `--dry-run` trước |
| Dữ liệu huấn luyện research-only (rủi ro tồn dư) | Pháp lý (thấp, chưa có tiền lệ) | `legal_ack` bắt buộc trong manifest; theo dõi định kỳ |
| Bản sao BFM trong lịch sử git | Đã phân phối lại | Pha G, hoặc Pháp chế ghi nhận nếu repo luôn private |
| Dùng EdgeFace/3DDFA để đo baseline có thể bị coi là sử dụng thương mại (L1) | Pháp lý | Cổng L1 trong `approvals.md`: chỉ đo nội bộ, giữ bản sao tối đa [30] ngày; nếu bị từ chối thì so sánh theo ngưỡng tuyệt đối của SC-006 |
| Mất nhận diện ở góc nghiêng | FRR tăng ở dải 40–60° | Có ngưỡng chấp nhận rõ (SC-006); cổng T042 quyết định có dùng MediaPipe hay không |

## Complexity Tracking

Không có vi phạm cần biện minh: feature **giảm** độ phức tạp (bỏ 2 engine, 1 model pose, 5 file dữ liệu) và chỉ thêm 1 module guard + 3 script nhỏ.
