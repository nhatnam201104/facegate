# Research: Chuyển sang model/công nghệ thương mại hóa được

**Feature**: 002-license-compliant-models | **Date**: 2026-11-18

## Nguyên tắc phân loại

Một model chỉ "thương mại hóa được" khi **cả ba lớp** đều sạch hoặc rủi ro đã được chấp nhận:

1. **Giấy phép code** (kiến trúc, script).
2. **Giấy phép trọng số**: chính điều khoản nhà phát hành gắn với file weights. Lớp này quyết định.
3. **Dữ liệu huấn luyện**: hầu như mọi model khuôn mặt công khai đều huấn luyện trên dữ liệu "research only". Nếu nhà phát hành cấp trọng số theo Apache/MIT thì ta xếp vào **rủi ro tồn dư cần Pháp chế ký chấp nhận**, không phải vi phạm trực tiếp.

Kết luận thẳng: **không tồn tại model nhận diện khuôn mặt công khai nào sạch tuyệt đối cả ba lớp**. Muốn sạch hoàn toàn chỉ có hai đường: mua giấy phép thương mại, hoặc tự huấn luyện trên dữ liệu có đồng ý.

---

## R1 — Nhận diện khuôn mặt (thay EdgeFace-XS)

**Decision**: Đặt **SFace** (`face_recognition_sface_2021dec.onnx`, OpenCV Zoo, Apache-2.0) làm backend mặc định. Engine `opencv` đã tích hợp sẵn tại `server/vision/engines.py` (`OpenCVEngine`).

**Rationale**:
- Trọng số được OpenCV Zoo phát hành theo Apache-2.0, cho phép thương mại và phân phối lại (kèm NOTICE).
- Đã tích hợp và benchmark trong repo (`benchmark.py` chạy cả `edgeface` lẫn `opencv`), nên thay đổi nhỏ nhất.
- Căn chỉnh 112×112 theo cùng template ArcFace, nên crop đã lưu dùng lại được để re-embed.

**Rủi ro tồn dư**: SFace huấn luyện trên CASIA-WebFace/MS1M. Hai bộ này là dữ liệu nghiên cứu, MS1M đã bị Microsoft rút năm 2019. Pháp chế cần ký chấp nhận.

**Cần đo**: SFace (~38 MB) chậm hơn EdgeFace-XS. Phải chạy lại benchmark K4 (≤ 200 ms / 10 mặt) và replay K0 (FAR không tăng). Nếu vượt ngân sách CPU thì bật `embed_batch` thật bằng onnxruntime (SFace là ONNX, chạy qua ORT được) thay vì `cv2.FaceRecognizerSF` từng mặt.

**Alternatives considered**:

| Phương án | Giấy phép trọng số | Loại vì |
|---|---|---|
| Mua giấy phép thương mại EdgeFace từ Idiap | Thương mại (trả phí) | Giữ được ±90° nhưng không phải "MIT/tương đương"; **giữ làm phương án B** nếu SFace trượt KPI |
| InsightFace commercial license | Thương mại (trả phí) | Như trên, chi phí cao hơn |
| AdaFace / MagFace / ElasticFace | MIT code; trọng số huấn luyện trên MS1M/WebFace4M, nhà phát hành không cấp quyền thương mại rõ ràng | Rủi ro ngang hoặc cao hơn SFace, chưa tích hợp |
| Tự huấn luyện trên dữ liệu tổng hợp (DigiFace-1M, …) | DigiFace-1M chỉ cho nghiên cứu | Không sạch hơn, tốn GPU (vi phạm ràng buộc "0đ GPU" của Hiến pháp) |
| Tự huấn luyện trên dữ liệu nhân viên có consent | Sạch | Không đủ dữ liệu ở quy mô PoC; xem xét dài hạn |

## R2 — Ước lượng tư thế đầu (thay 3DDFA_V2 + BFM)

**Decision**: Thay `PoseEstimator3D` bằng **`cv2.solvePnP` trên 5 điểm mốc của YuNet** với **mô hình 3D 5 điểm tự định nghĩa** (giá trị nhân trắc trung bình, tự viết tay, không trích từ BFM). Thêm làm mượt theo thời gian (EMA trên yaw/pitch, tính theo giây, tuân Hiến pháp I). Hàm `_fallback_from_5pt` hiện có là điểm khởi đầu.

**Rationale**:
- Không cần model hay dữ liệu bên thứ ba, nên sạch tuyệt đối về giấy phép; chỉ dùng OpenCV (Apache-2.0).
- Giữ nguyên interface `PoseResult(yaw, pitch, roll, pose_group, landmarks3d, kps5, visibility)`, nên pipeline, guided enrollment và tracker không phải đổi.
- Rẻ hơn 3DDFA (bỏ một lượt suy luận ONNX cho mỗi khuôn mặt), có lợi cho ngân sách K4.

**Giới hạn**: Điểm mốc YuNet kém tin cậy khi |yaw| > ~60°. Xử lý:
- `visibility` giảm dần theo |yaw|; khi > 60° gắn cờ `pose_reliable=False`. Pipeline không chốt danh tính từ khung đó (Hiến pháp II: Unknown thay vì đoán).
- Guided enrollment: giảm 2 góc nghiêng ~80° xuống ~55–60°.
- `landmarks3d` (68 điểm) chỉ còn là xấp xỉ; rà các chỗ tiêu thụ (overlay `draw.py`, `quality.py`).

**Alternatives considered**:

| Phương án | Giấy phép | Ghi chú |
|---|---|---|
| **MediaPipe Face Landmarker** (`mediapipe` pip + `face_landmarker.task`) | Apache-2.0 code + model card Apache-2.0, dữ liệu nội bộ Google | **Phương án nâng cấp có điều kiện**: kích hoạt bởi cổng T042 khi trượt SC-006 chỉ ở dải 40–60° hoặc ở latency, còn dải chính diện đạt. Tốn thêm CPU, phải benchmark. Web client đã dùng bản WASM |
| 6DRepNet / HopeNet / WHENet | Code MIT/Apache, trọng số huấn luyện trên 300W-LP/BIWI (nghiên cứu) | Cùng vấn đề với 3DDFA, loại |
| Mua giấy phép BFM (Unitectra) | Thương mại | Vẫn còn trọng số 3DDFA huấn luyện trên 300W-LP, loại |

## R3 — InsightFace SCRFD + ArcFace

**Decision**: **Xoá hẳn** `OnnxEngine`, `ONNX_MODELS`, cờ `--onnx`, ngưỡng `thresholds.onnx`, và nhánh `onnx` trong `create_engine`.

**Rationale**: Không có giá trị sản phẩm (chỉ để benchmark), giữ lại thì tạo đường bật nhầm. Ai cần benchmark nghiên cứu có thể dùng branch/tag cũ.

**Alternatives**: Giữ sau cờ `research_mode`. Bị loại vì vẫn phân phối code tải weights phi thương mại cho khách.

## R4 — Model giữ nguyên (cần làm hồ sơ)

| Model | Giấy phép trọng số | Hành động |
|---|---|---|
| YuNet `face_detection_yunet_2023mar` | MIT (OpenCV Zoo) | Sửa comment đang ghi "Apache-2.0", ghim SHA-256 |
| YOLOX-Nano | Apache-2.0 (Megvii, release chính thức) | Ghim SHA-256, thêm NOTICE |
| MiniFASNet V2 / V1SE | Apache-2.0 (Minivision); ONNX do bên thứ ba chuyển đổi (`yakhyo/face-anti-spoofing`) | Kiểm tra LICENSE của repo gốc và repo chuyển đổi, lưu bản sao LICENSE vào `third_party/licenses/`, ghim SHA-256. Dài hạn: tự chuyển đổi ONNX từ `.pth` chính thức để loại bỏ khâu trung gian |
| MediaPipe Face Landmarker (web, tùy chọn) | Apache-2.0 | Nếu vendor vào `/static/vendor/` thì kèm LICENSE + NOTICE |

## R5 — Loa nhắc (pytapo)

**Decision**: Đổi mặc định `audio_prompt.backend` thành `web` trong `config.yaml` và `server/config.py`. Backend `tapo` vẫn giữ nhưng chỉ bật thủ công, có cảnh báo trong log và tài liệu rằng đây là API không chính thức.

**Alternatives**: Xoá hẳn tapo. Bị loại vì một số khách đã có loa Tapo; rủi ro thuộc về ToS chứ không phải bản quyền phần mềm.

## R6 — File âm thanh `look_at_camera.wav`

**Decision (cập nhật 2026-11-18)**: Tác giả dự án xác nhận **tự tạo** file này, nên **giữ nguyên**. Chỉ cần lập hồ sơ nguồn gốc ở `third_party/provenance/look_at_camera.md`, gồm: tác giả, ngày tạo, cách tạo (tự thu âm hay công cụ TTS nào, giọng nào) và tuyên bố quyền sở hữu thuộc công ty.

**Lưu ý**: Nếu file được tạo bằng TTS của bên thứ ba thì ghi rõ tên dịch vụ, vì một số dịch vụ (ví dụ Google Translate TTS, edge-tts) có điều khoản hạn chế dùng đầu ra cho mục đích thương mại. Nếu tự thu âm hoặc dùng TTS chạy cục bộ có giấy phép cho phép thì không còn rủi ro.

**Alternatives** (chỉ khi hồ sơ cho thấy có vấn đề): thu âm lại bằng giọng nội bộ.

## R7 — Lịch sử git chứa file BFM

**Decision**: Xoá 5 file `tddfa_*.bin` khỏi working tree trong feature này. **Việc viết lại lịch sử** (`git filter-repo` + force-push lên `msoftware-net/...`) là thao tác phá huỷ, **cần chủ repo phê duyệt riêng** và được tách thành một task độc lập.

**Rationale**: Repo đã push lên GitHub, bất kỳ ai clone được đều lấy được bản sao. Nếu repo private và chưa từng chia sẻ ra ngoài thì có thể chấp nhận chỉ xoá ở HEAD, kèm ghi chú của Pháp chế.

## R8 — Cơ chế ngăn tái phát

**Decision**:
- `server/models/manifest.json` là nguồn sự thật duy nhất (schema ở `contracts/model-manifest.schema.json`).
- `download_models.py` đọc URL và SHA-256 từ manifest, không còn hard-code.
- `server/scripts/license_gate.py` + một test pytest (`test_license_gate.py`) fail khi gặp model lạ, model `commercial_use != allowed`, hoặc file `tddfa_*.bin` / `*bfm*` trong repo.
- Khi khởi động, `create_engine` / `PoseEstimator` kiểm tra manifest (FR-006).
