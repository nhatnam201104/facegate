---

description: "Task list: chuyển model/công nghệ phi thương mại sang giấy phép MIT/Apache/BSD"
---

# Tasks: Chuyển toàn bộ model/công nghệ sang giấy phép thương mại hóa được

**Input**: Design documents from `/specs/002-license-compliant-models/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Có. plan.md yêu cầu `test_license_gate.py`, `test_pose_pnp.py`, `test_migrate_gallery.py`; Hiến pháp yêu cầu pytest 100% xanh.

**Organization**: Nhóm theo user story trong spec.md (US1 P1, US2 P1, US3 P2).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Chạy song song được (khác file, không phụ thuộc task chưa xong)
- **[Story]**: US1 / US2 / US3

## Path Conventions

Backend `server/`, frontend `webapp/`, test `server/tests/`, script `server/scripts/` (theo AGENTS.md).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Chụp baseline trước khi gỡ EdgeFace, tạo thư mục hồ sơ giấy phép

- [X] T033 **(CỔNG CHẶN, làm trước mọi task sửa code)** Sửa Hiến pháp `.specify/memory/constitution.md` lên **v1.2.0**:
  - Nguyên tắc III: thay danh sách model bằng `YuNet`, `SFace`, `YOLOX-Nano`, `MiniFASNet`, ước lượng pose PnP 5 điểm, QR/Barcode, `ORB/SIFT + RANSAC`.
  - Thêm câu: "Mọi model phải có mục trong `server/models/manifest.json` với `commercial_use: allowed`, giấy phép trọng số thuộc allow-list và qua `license_gate.py --strict` trước khi phát hành".
  - Cập nhật `Last Amended: <ngày>`; ghi chú cuối file "Sửa đổi này cần PM + Pháp chế phê duyệt bằng văn bản (Governance)".
  - Lưu số văn bản phê duyệt vào `specs/002-license-compliant-models/approvals.md` (người duyệt, ngày, mã văn bản).
  - Trong cùng `approvals.md`, tạo 3 mục cần ký, mỗi mục gồm trạng thái `PENDING | APPROVED | REJECTED`, người ký, ngày, mã văn bản:
    - **C**: Hiến pháp v1.2.0.
    - **L1**: Pháp chế xác nhận việc tiếp tục dùng EdgeFace / 3DDFA_V2 / BFM **chỉ để đo baseline nội bộ** trong T001 và T027, giữ bản sao ở `data/_baseline_models_noncommercial/` tối đa **[30] ngày** hoặc tới T037 (tuỳ mốc nào đến trước), không phân phối, không chạy cho khách.
    - **S6**: PM chốt các giá trị `[ ]` của SC-006 trong `spec.md`.
  - **Không bắt đầu T012 trở đi khi chưa có phê duyệt.** T001–T009 (đo baseline, tạo hồ sơ, guard) không đổi hành vi runtime nên được làm trong lúc chờ.
  - *Hoàn thành 2026-11-18*: C = APPROVED, đã áp dụng v1.2.0 vào `.specify/memory/constitution.md` (kèm yêu cầu Pháp chế ký **theo từng model**, theo quyết định LA). L1 = REJECTED, S6 = mặc định (tuyệt đối), LA = chấp nhận theo từng model. Còn thiếu số văn bản, xem `approvals.md`.
- [ ] T001 Đo baseline với backend `edgeface` hiện tại (**phải xong trước T014**, sau đó không còn model EdgeFace để đo).
  ⚠ **L1 = REJECTED (2026-11-18)** nên chỉ làm **bước 1** (giữ video gốc) và ghi `baseline.json` = `{"backend":"none","reason":"L1 rejected","compare_mode":"absolute"}`. Bước 2–6 **bị huỷ**; các file EdgeFace/3DDFA/BFM đã bị xoá.
  *Tiến độ 2026-11-18*: bước 1 **BỊ CHẶN**. `data/` không có footage K0 nào (không có `data/k0_raw/`, `data/k0_bundle*`; `data/sources/` và `data/uploads/` rỗng), và mục L1 đang PENDING. Công cụ của bước 4 (T041) đã sẵn sàng.
  **Điều kiện tiên quyết pháp lý**: mục **L1** trong `approvals.md` (T033) phải là `APPROVED` trước bước 2. Nếu `REJECTED`:
  - Chỉ làm bước 1 (giữ video gốc). **Bỏ** bước 2–5.
  - Ghi `baseline.json` = `{"backend":"none","reason":"L1 rejected","compare_mode":"absolute"}`.
  - T026 và T027 khi đó chỉ dùng các ngưỡng tuyệt đối của SC-006 / SC-003. Riêng SC-002 ("FAR ≤ baseline") thay bằng `event_far.false_checkins == 0` và `event_far.far_ci95_upper ≤ [0.01]`.

  Các bước:
  1. **Giữ video gốc K0**: chép toàn bộ video/RTSP dump dùng cho K0 vào `data/k0_raw/` (ngoài git). **Không** dùng cờ `--delete-raw` của `record_k0_bundle.py`. Ghi danh sách file và SHA-256 vào `specs/002-license-compliant-models/k0_raw_manifest.txt`. Phải có ít nhất một video của **camera có thông số thấp nhất** dự kiến dùng (Hiến pháp III); nếu chưa có thì quay bổ sung trước.
  2. Ghi bundle bằng EdgeFace: `python server/scripts/k0_field_runner.py --source data/k0_raw/ --out data/k0_bundle_edgeface --non-interactive`, hoặc `record_k0_bundle.py <video> --out data/k0_bundle_edgeface ...` cho từng video, giữ nguyên `--subject/--label/--consent-ref` như lần quay gốc.
  3. Chạy `python server/scripts/replay_eval.py data/k0_bundle_edgeface --out specs/002-license-compliant-models/baseline_replay.json` với ngưỡng đang dùng.
  4. **Sau khi T041 xong**, đo full pipeline EdgeFace: `python server/scripts/benchmark.py data/k0_raw/<video camera yếu nhất> --full-pipeline --backend edgeface --min-faces 1 --out specs/002-license-compliant-models/baseline_full_pipeline.json`. Chạy thêm với `--ep openvino` nếu khả dụng. **Không** dùng `--ten-faces-bench` làm baseline vì nó không phải full pipeline.
  5. Lưới an toàn: chép `server/models/edgeface_xs_gamma_06.onnx` và `server/models/3ddfa_v2_mb1_120x120.onnx` cùng 5 file `tddfa_*.bin` vào `data/_baseline_models_noncommercial/` (ngoài git; `data/` đã bị ignore), kèm file `README.txt` ghi "CHỈ dùng nội bộ để đo baseline feature 002; XOÁ sau T027; cấm phân phối". Thêm vào T037 bước xoá thư mục này.
  6. Ghi `specs/002-license-compliant-models/baseline.json` dạng `{"backend":"edgeface","model_version":"yunet2023mar+edgeface_xs_gamma_06_v41","bundle_dir":"data/k0_bundle_edgeface","raw_video":"data/k0_raw/","k4_full_pipeline_ms":{"mean":..,"p90":..,"source":"baseline_full_pipeline.json"},"far":..,"frr":..,"frr_by_pose":{..},"thresholds":{"frontal":0.40,"profile":0.46},"measured_at":"<ISO>"}`.

  Nếu chưa có footage K0 có consent thì DỪNG và báo PM, vì SC-002 sẽ không đối chiếu được.
- [X] T002 [P] Tạo thư mục `third_party/licenses/` và `third_party/provenance/` (mỗi thư mục có `.gitkeep`).
- [X] T003 [P] Tải văn bản LICENSE gốc vào `third_party/licenses/`: `opencv_zoo-LICENSE` (Apache-2.0, repo opencv/opencv_zoo), `opencv_zoo-yunet-LICENSE` (MIT, `models/face_detection_yunet/LICENSE`), `opencv_zoo-sface-LICENSE` (Apache-2.0, `models/face_recognition_sface/LICENSE`), `YOLOX-LICENSE` (Apache-2.0, Megvii-BaseDetection/YOLOX), `Silent-Face-Anti-Spoofing-LICENSE` (minivision-ai/Silent-Face-Anti-Spoofing), `yakhyo-face-anti-spoofing-LICENSE` (repo chuyển đổi ONNX). Nếu LICENSE của Minivision hoặc yakhyo **không** phải Apache/MIT/BSD thì DỪNG và báo Pháp chế; MiniFASNet khi đó chuyển sang `commercial_use: "unknown"`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Manifest + guard dùng chung. Mọi user story đều phụ thuộc phase này.

**⚠️ CRITICAL**: Chưa xong phase này thì không bắt đầu user story.

- [X] T004 Tạo `server/models/manifest.json` theo `specs/002-license-compliant-models/contracts/model-manifest.schema.json`, gồm `"schema_version": 1`, `"allowed_weight_licenses": ["MIT","Apache-2.0","BSD-2-Clause","BSD-3-Clause","ISC","Zlib","CC0-1.0","CC-BY-4.0"]` và 5 mục `models`:
  - `face_detection_yunet_2023mar.onnx`: role `face_detect`, license_weights `MIT`, training_data `["WIDER FACE"]`, training_data_risk `residual_accepted`
  - `face_recognition_sface_2021dec.onnx`: role `face_embed`, version `sface2021dec`, license_weights `Apache-2.0`, training_data `["CASIA-WebFace","MS-Celeb-1M (cleaned)"]`, `residual_accepted`
  - `yolox_nano_coco.onnx`: role `context_detect`, `Apache-2.0`, `["COCO 2017"]`, `residual_accepted`
  - `MiniFASNetV2.onnx` và `MiniFASNetV1SE.onnx`: role `liveness`, `Apache-2.0`, training_data theo README Minivision, `residual_accepted`

  `source_url` lấy từ `server/scripts/download_models.py` hiện tại. `sha256` = **64 ký tự hex đầy đủ**, tính bằng `sha256sum` trên file hiện có trong `server/models/`. `min_bytes` giữ như script cũ. `legal_ack` tạm là `{"by":"PENDING","date":"1970-01-01","ref":"PENDING"}` (gate sẽ coi `PENDING` là chưa ký, xem T006).
- [X] T005 Tạo `server/vision/license_guard.py`, gồm:
  - `load_manifest(models_dir: Path) -> dict`: đọc `manifest.json`, kiểm field bắt buộc theo data-model.md, không dùng thư viện jsonschema
  - `class LicenseError(RuntimeError)`
  - `check_artifact(manifest, file_name, models_dir, verify_sha=True) -> dict`: raise `LicenseError` với đúng 3 thông điệp trong `contracts/runtime-license-guard.md` §2 (`"LICENSE: <file> không có trong manifest"`, `"LICENSE: <file> không được phép thương mại"`, `"LICENSE: <file> sai checksum"`). Quy tắc: `commercial_use == "allowed"` **và** `license_weights` ∈ `allowed_weight_licenses`. Kết quả SHA-256 cache theo `(path, mtime, size)` để khởi động nhanh.
  - `require(cfg, file_name)`: gọi `check_artifact`; nếu `cfg["license"]["research_mode"]` là true thì chỉ `log.warning` thay vì raise.
  - `summary(manifest) -> list[dict]`: trả `[{file, license_weights, commercial_use}]` cho `/api/status`.
  - **Fail-closed** (spec §Edge Cases):
    - `manifest.json` không tồn tại thì `LicenseError("LICENSE: thiếu server/models/manifest.json")`.
    - JSON hỏng hoặc thiếu field bắt buộc thì `LicenseError("LICENSE: manifest.json không hợp lệ: <chi tiết>")`.
    - `schema_version != 1` thì `LicenseError`.
    - Không có nhánh "bỏ qua khi lỗi". Chỉ `research_mode=true` mới hạ lỗi xuống `log.warning`; riêng trường hợp thiếu hoặc hỏng manifest vẫn **raise** kể cả khi `research_mode=true`.
  - Manifest được nạp **trong từng process** gọi `require` (process chính và worker `worker_perception.py` đều tự kiểm), không truyền kết quả kiểm tra qua IPC.
- [X] T006 Tạo `server/scripts/license_gate.py` (exit 0 nếu pass, 1 nếu fail) áp dụng 5 quy tắc ở data-model.md §ModelArtifact:
  1. Mọi file `*.onnx|*.bin|*.task|*.pth|*.pt` trong `server/models/` phải có mục manifest, trừ `.gitkeep`.
  2. `commercial_use == "allowed"` và license ∈ allow-list.
  3. `training_data_risk == "blocking"` thì fail; `residual_accepted` thì bắt buộc có `legal_ack`, và `legal_ack.by == "PENDING"` bị in **WARN** (không fail, để CI chạy được trong lúc chờ ký; thêm cờ `--strict` để biến WARN thành fail khi nghiệm thu).
  4. `git ls-files` và `server/models/` không có file khớp regex `(?i)(tddfa_|bfm|3ddfa|edgeface|w600k|scrfd)`.
  5. SHA-256 khớp.

  In bảng `file | license_weights | commercial_use | status` và dùng lại `server/vision/license_guard.py`.
- [X] T007 [P] Tạo `server/tests/test_license_gate.py` (dùng `tmp_path`, không cần model thật):
  - (a) manifest hợp lệ + file giả có SHA đúng thì pass
  - (b) file `rogue.onnx` không có trong manifest thì fail và nêu tên
  - (c) `commercial_use: "forbidden"` thì `LicenseError`
  - (d) SHA lệch thì `LicenseError`
  - (e) file tên `tddfa_u_base.bin` thì fail quy tắc 4
  - (f) `research_mode: true` thì không raise
  - (g) `--strict` + `legal_ack.by == "PENDING"` thì fail
  - (h) không có `manifest.json` thì `LicenseError` chứa "thiếu", kể cả khi `research_mode: true`
  - (i) `manifest.json` là JSON hỏng (`"{"`) thì `LicenseError` chứa "không hợp lệ"
  - (j) `schema_version: 2` thì `LicenseError`
- [X] T008 Viết lại `server/scripts/download_models.py`:
  - Bỏ các dict hard-code `OPENCV_MODELS`, `V4_COMMERCIAL_MODELS`, `LIVENESS_MODELS`, `ONNX_MODELS`, `TDDFA_PARAM_FILES` và cờ `--onnx`.
  - Đọc danh sách model từ `server/models/manifest.json` qua `license_guard.load_manifest`.
  - Kiểm SHA-256 **đầy đủ**: file tải về lệch thì xoá và báo `[loi]`. File đã có mà lệch thì báo lỗi, không ghi đè.
  - Giữ `--verify-only`, sửa docstring cho đúng (bỏ EdgeFace/3DDFA/SCRFD/ArcFace).
- [X] T041 [P] Thêm công cụ đo **full pipeline** vào `server/scripts/benchmark.py` (Hiến pháp III; phải xong **trước bước 4 của T001**, khi EdgeFace và 3DDFA còn trên đĩa). Công cụ phải chạy được với **cả** code hiện tại (backend `edgeface` + 3DDFA) **và** code sau US1 (backend `opencv` + PnP), không phụ thuộc vào class bị xoá:
  - Thêm các cờ `--full-pipeline`, `--backend <tên>` (ghi đè `cfg["pipeline"]["backend"]`; tạo engine qua `create_engine(cfg)`, không import trực tiếp `EdgeFaceEngine`), `--min-faces N` (chỉ thống kê frame có ≥ N mặt), `--max-frames` (mặc định 300) và `--out <json>`.
  - Mỗi chu kỳ đo theo đúng thứ tự `server/vision/worker_perception.py` (dòng ~200–272): decode (`cap.read`) → `detect_faces_two_stage_3mp` → context YOLOX-Nano (`server/vision/context_detector.py`) → `pose.estimate` → `assess` → `align` → `embed_batch` → liveness MiniFASNet (`server/vision/liveness.py`, cho mặt đạt chất lượng). `PoseEstimator3D(cfg["_paths"]["models"])` được tạo bằng chữ ký tương thích cả trước lẫn sau T013.
  - Xuất `{"backend","model_version","ep","video","n_frames","cycle_ms":{"mean","p90"},"per_stage_ms":{decode,detect,context,pose,quality,align,embed,liveness},"faces_per_frame":{"mean","max"},"simulated":bool}`.
  - Nếu video không có frame đạt `--min-faces` thì cờ `--simulate-faces N` ghép lặp crop mặt thật lên frame để đủ N mặt, và đặt `"simulated": true`.
  - Sửa dòng 105 (`for b in ("edgeface", "opencv")`) để chạy theo `--backend` khi có cờ này.
  - Thêm test `server/tests/test_benchmark_full_pipeline.py`: video tổng hợp 5 frame (`cv2.VideoWriter` vào `tmp_path`) + engine/pose/liveness stub qua `monkeypatch`, kiểm tra JSON đủ khoá và `per_stage_ms` có đủ 8 bước.
- [X] T009 Thêm khối mặc định vào `server/config.py`: `license.research_mode=False`, `pose.engine="pnp5"`, `pose.max_reliable_yaw=60.0`, `pose.ema_tau_sec=0.3`. Thêm khối tương ứng vào `config.yaml` có comment tiếng Việt (theo `contracts/runtime-license-guard.md` §1).

**Checkpoint**: `python server/scripts/license_gate.py` chạy được và **FAIL** đúng kỳ vọng, vì `edgeface_xs_gamma_06.onnx`, `3ddfa_v2_mb1_120x120.onnx` và `tddfa_*.bin` còn tồn tại. `pytest server/tests/test_license_gate.py` xanh.

---

## Phase 3: User Story 1 - Bản build chỉ chứa thành phần thương mại hóa được (Priority: P1) 🎯 MVP

**Goal**: Cấu hình mặc định không nạp EdgeFace, 3DDFA_V2, BFM hay InsightFace; server từ chối backend phi thương mại.

**Independent Test**: `python server/scripts/license_gate.py` exit 0; `git ls-files | grep -Ei 'tddfa|bfm|3ddfa|edgeface|w600k|scrfd'` rỗng; đặt `pipeline.backend: edgeface` thì server thoát với lỗi LICENSE (quickstart kịch bản 1–2).

### Tests for User Story 1

- [X] T010 [P] [US1] Tạo `server/tests/test_pose_pnp.py`: sinh 5 điểm 2D bằng cách chiếu (`cv2.projectPoints`) mô hình 3D 5 điểm của T013 với yaw ∈ {-60,-30,0,30,60}, pitch ∈ {-20,0,20}. Assert `PoseEstimator3D(models_dir=None).estimate(img, face)` cho |yaw_est − yaw| ≤ 8° và |pitch_est − pitch| ≤ 10°. Assert `pose_group` đúng theo `classify_pose_group` (frontal_max 40). Assert `kps5` bằng `face.kps`. Assert `landmarks3d.shape == (68,3)`.
- [X] T011 [P] [US1] Thêm test vào `server/tests/test_license_gate.py`: `create_engine({"pipeline":{"backend":"edgeface",...}})` và `"onnx"` raise `LicenseError` với thông điệp chứa `"đã bị gỡ"`; `"opencv"` với manifest hợp lệ thì không raise (dùng `monkeypatch` thay `cv2.FaceDetectorYN.create` / `cv2.FaceRecognizerSF.create` bằng stub).

### Implementation for User Story 1

- [X] T012 [US1] Sửa `server/vision/engines.py`:
  - Xoá class `EdgeFaceEngine`, class `OnnxEngine` và hàm `_resolve_ort_providers` nếu không còn ai dùng (giữ lại nếu T020 cần ORT batch).
  - `create_engine`: `"opencv"` → `OpenCVEngine`; `"edgeface"` hoặc `"onnx"` → raise `license_guard.LicenseError(f"LICENSE: backend '{backend}' đã bị gỡ do giấy phép phi thương mại; dùng 'opencv' (xem specs/002-license-compliant-models)")`.
  - `OpenCVEngine.__init__` gọi `license_guard.require(cfg, ...)` cho YuNet và SFace. Đổi chữ ký thành nhận `cfg` hoặc truyền thêm tham số `cfg`.
  - Sửa docstring đầu file và comment "YuNet (Apache-2.0)" thành "YuNet (MIT)".
  - `align_by_pose` bỏ chữ "3DDFA_V2" trong docstring.
- [X] T013 [US1] Viết lại `server/vision/pose3d.py`:
  - Xoá toàn bộ nhánh 3DDFA (nạp ONNX, các file `tddfa_*.bin`, `_parse_roi_box`, `_crop_roi`, `_kps5_from_68`, `_orthogonalize_rows`, `self._r_ref`).
  - Thêm hằng `FACE5_3D` (mm, hệ trục x phải, y xuống, z vào camera), giá trị nhân trắc trung bình **tự định nghĩa** và ghi nguồn bằng comment "giá trị tự ước lượng, không phái sinh BFM": mắt trái-ảnh (-32, -35, -25), mắt phải-ảnh (32, -35, -25), đầu mũi (0, 0, 0), khoé miệng trái-ảnh (-24, 32, -22), khoé miệng phải-ảnh (24, 32, -22).
  - `PoseEstimator3D.__init__(self, models_dir=None, frontal_max_yaw=40.0, hysteresis=5.0, max_reliable_yaw=60.0, ema_tau_sec=0.3)`: giữ chữ ký cũ tương thích, `models_dir` bị bỏ qua.
  - `estimate`: camera matrix gần đúng `f = max(w, h)`, tâm ảnh; gọi `cv2.solvePnP(FACE5_3D, face.kps, K, None, flags=cv2.SOLVEPNP_EPNP)` rồi refine `SOLVEPNP_ITERATIVE` với `useExtrinsicGuess=True`; Rodrigues → `euler_from_relative_rotation` (giữ hàm này, chỉnh dấu để yaw dương = cùng chiều với quy ước hiện tại của `_fallback_from_5pt`). Nếu `solvePnP` thất bại hoặc lỗi reprojection > 0.25 × khoảng cách hai mắt thì dùng `_fallback_from_5pt`.
  - `landmarks3d` lấy từ `_fallback_from_5pt` (giữ shape `(68,3)`).
  - Sửa docstring đầu file: bỏ "3DDFA_V2 ONNX (MIT)".
- [X] T014 [US1] Xoá 5 file BFM khỏi git và đĩa: `git rm server/models/tddfa_param_mean.bin server/models/tddfa_param_std.bin server/models/tddfa_u_base.bin server/models/tddfa_w_exp_base.bin server/models/tddfa_w_shp_base.bin`. Xoá `server/models/3ddfa_v2_mb1_120x120.onnx` và `server/models/edgeface_xs_gamma_06.onnx` khỏi đĩa (chúng không được track, nằm trong `.gitignore`). Thêm `server/models/tddfa_*`, `*edgeface*`, `*3ddfa*`, `*w600k*`, `*scrfd*` vào `.gitignore` để chặn commit lại.
- [X] T015 [US1] Sửa `server/config.py` và `config.yaml`:
  - `pl.setdefault("backend", "opencv")`.
  - Xoá `thrs.setdefault("edgeface", ...)`, `thrs.setdefault("onnx", ...)` và khối `thresholds.edgeface` / `thresholds.onnx` trong `config.yaml`; thêm `thresholds.opencv` tạm `{"frontal": <giá trị cũ của opencv nếu có, nếu không thì 0.40>, "profile": 0.46}` (T021 sẽ hiệu chỉnh).
  - Sửa comment dòng 18–20 của `config.yaml`: chỉ còn `opencv : YuNet (MIT) + SFace (Apache-2.0)`.
- [X] T016 [US1] Cập nhật các nơi gọi trong `server/main.py`, `server/vision/worker_perception.py`, `server/scripts/benchmark.py`, `server/scripts/record_k0_bundle.py`:
  - `PoseEstimator3D(...)` truyền `max_reliable_yaw` / `ema_tau_sec` từ `cfg["pose"]`.
  - `benchmark.py` dòng 105: đổi `("edgeface", "opencv")` thành `("opencv",)`.
  - Gọi `license_guard.require` cho `MiniFASNetV2.onnx`, `MiniFASNetV1SE.onnx` trong `server/vision/liveness.py` và `yolox_nano_coco.onnx` trong `server/vision/context_detector.py` trước khi tạo `InferenceSession`.
  - Khi `LicenseError` xảy ra lúc khởi động thì `server/main.py` log lỗi và `sys.exit(2)`.
- [X] T017 [US1] Bổ sung `/api/status` trong `server/vision/pipeline.py` (hàm trả dict quanh dòng 690) và nhánh worker tương ứng: thêm `"pose_engine": "pnp5"`, `"license_ok": bool` (False nếu `research_mode`) và `"license_models": license_guard.summary(manifest)`, đúng `contracts/runtime-license-guard.md` §3.
- [X] T018 [US1] Cập nhật test đang tham chiếu EdgeFace:
  - `server/tests/test_accuracy_v4_1.py`: import và dòng 39–42 dùng `OpenCVEngine` + `face_recognition_sface_2021dec.onnx`; các stub `name = "edgeface"` / `model_version = "edgeface_v41_bf"` đổi thành `"opencv"` / `"opencv_v41_bf"`.
  - `server/tests/test_api_v4_integration.py` dòng 50: `"backend": "opencv"`.
  - `server/tests/test_guided.py` dòng 234, 271, 276: đổi chuỗi `edgeface_test_*` thành `opencv_test_*`.
  - `server/tests/test_ledger_and_hold.py` dòng 145, 151: `"edgeface_v4"` → `"opencv_v4"`.

  Chạy `pytest server/tests -q` cho tới khi xanh.

**Checkpoint**: Quickstart kịch bản 1 và 2 pass; `license_gate.py` exit 0 (WARN `PENDING` là chấp nhận được ở bước này).

---

## Phase 4: User Story 2 - Chấm công giữ chất lượng sau khi đổi model (Priority: P1)

**Goal**: Gallery chuyển sang SFace mà không cần đăng ký lại thủ công; FAR không tăng; khung |yaw| > 60° không được chốt danh tính.

**Independent Test**: Quickstart kịch bản 3, 4, 5; so sánh với `baseline.json` (T001).

### Tests for User Story 2

- [X] T019 [P] [US2] Tạo `server/tests/test_migrate_gallery.py`, dùng DB SQLite tạm + engine stub (`embed_aligned` trả vector cố định theo hash ảnh, `detect` trả 1 face):
  - (a) 2 nhân viên active có crop hợp lệ thì status `migrated`, `load_gallery(new_version)` trả đủ vector, `pose_group`/`yaw`/`pitch`/`crop_path` sao chép từ hàng cũ
  - (b) crop thiếu file thì `needs_reenroll`, không có hàng mới
  - (c) `--dry-run` thì DB không đổi
  - (d) chạy 2 lần thì idempotent (không nhân đôi hàng)
  - (e) `audit_logs` có bản ghi action `gallery_migrate`
- [X] T020 [P] [US2] Thêm test vào `server/tests/test_pose_pnp.py`: yaw 75° thì `pose_reliable is False` và `visibility <= 0.3`; yaw 30° thì `pose_reliable is True`. Hai khung liên tiếp cách 0.1 s với yaw 0° rồi 20° thì EMA cho yaw nằm giữa 0 và 20 (lọc theo **giây**, không theo số frame).

### Implementation for User Story 2

- [X] T021 [US2] Thêm trường `pose_reliable: bool = True` vào dataclass `PoseResult` trong `server/vision/pose3d.py`. Đặt `False` khi `abs(yaw) > max_reliable_yaw` hoặc reprojection lỗi (T013). `visibility = max(0.2, cos(radians(min(85, |yaw|))))`. Thêm EMA theo thời gian: `alpha = 1 - exp(-dt/ema_tau_sec)`, trạng thái lưu theo `track_id`. Thêm tham số tuỳ chọn `track_id: int | None = None, ts: float | None = None` vào `estimate`; khi không truyền thì không làm mượt.
- [X] T022 [US2] Trong `server/vision/pipeline.py` (khoảng dòng 356–416) và `server/vision/worker_perception.py` (khoảng dòng 207–272): truyền `track_id=t.id, ts=<timestamp frame>` vào `pose.estimate`. Nếu `pose.pose_reliable is False` thì **không** gọi `all_employee_similarities` / không cộng dồn bằng chứng nhận diện cho khung đó, đặt `t.last_reason = "pose_unreliable"` để overlay hiển thị nhắc nhìn camera (Hiến pháp II: Unknown thay vì đoán).
- [X] T023 [US2] Sửa `server/enrollment/guided.py`:
  - Hai step dòng 49–50 đổi chữ "~80°" thành "~55°".
  - `DEFAULTS["profile_yaw_min"] = 45.0`, `DEFAULTS["profile_yaw_max"] = 60.0`.
  - Thông báo dòng 94 đổi "(~80°)" thành "(~55°)".

  Cập nhật các test trong `server/tests/test_guided.py` đang assert giá trị 65/88/80 nếu có.
- [X] T024 [US2] Tạo `server/scripts/migrate_gallery.py`, đúng CLI ở `contracts/runtime-license-guard.md` §4: `--from <ver> --to auto [--dry-run]`, `--to auto` = `create_engine(cfg).model_version`.
  - Với mỗi employee status ∈ (`active`, `pending`) có hàng `embeddings` ở `--from`: đọc ảnh `data/<crop_path>`. Nếu DB có `cipher` thì giải mã qua `BiometricCipher` khi file ở dạng mã hoá; chỉ giải mã trong RAM, không ghi file tạm.
  - Ảnh 112×112 thì coi là đã căn chỉnh, gọi `engine.embed_aligned`. Ngược lại `engine.detect` → lấy mặt lớn nhất → `engine.align` → `embed_aligned`.
  - `db.add_embedding(emp_id, vec, new_version, quality_cũ, pose_bin_cũ, crop_path_cũ, source="migrate", pose_group=..., yaw=..., pitch=...)`.
  - Ghi `audit_logs` (action=`gallery_migrate`, employee_id, from_version, to_version, n_vectors, status ∈ {`migrated`, `needs_reenroll`}).
  - Bỏ qua nhân viên đã có hàng ở `new_version` (idempotent). **Không xoá** hàng cũ.
  - In tổng kết `migrated=X needs_reenroll=Y`. Exit 1 nếu Y > 0, để HR biết phải xử lý.
- [X] T025 [US2] Hiển thị `needs_reenroll` cho HR: trong `server/main.py` endpoint danh sách nhân viên (dùng `db.list_employees`, dòng ~690), thêm cờ `needs_reenroll = (n_emb == 0)` ở `model_version` hiện tại. Trong `webapp/app.js`, hiển thị nhãn "Cần đăng ký lại (đổi model)" cạnh nhân viên đó.
- [X] T039 [P] [US2] Bổ sung tham số ngưỡng theo pose cho `server/scripts/replay_eval.py` (hiện chỉ có `--threshold` đơn ở dòng 609):
  - Thêm `--threshold-frontal` và `--threshold-profile`; mặc định bằng `--threshold` để giữ tương thích ngược. Áp dụng theo `pose_group` của từng observation trong hàm mô phỏng `all_employee_similarities` (khoảng dòng 125–146) và trong `pairwise_impostor_far` (tách impostor pair theo pose_group của probe).
  - Thêm `--sweep "0.30:0.60:0.01"`: chạy lại đánh giá cho mỗi cặp (frontal, profile) trên lưới, xuất bảng Markdown `frontal | profile | FAR | FRR | FRR_frontal | FRR_profile` vào `--report-md`, và JSON vào `--out`.
  - Bổ sung chỉ số `checkin_latency_s` vào kết quả: với mỗi session bonafide được check-in, tính số giây từ `ts` của observation đầu tiên có mặt người đó đến `ts` của khung ra quyết định check-in. Xuất `{"mean","p50","p90","n"}`; session bị bỏ sót không tính vào latency (đã nằm trong `event_frr`). Cột `latency_p90` cũng xuất hiện trong bảng `--sweep`.
  - Thêm cột `event_frr`, `frr_yaw_le40`, `frr_yaw_40_60`, `false_checkins`, `id_switch_false_checkins` vào bảng `--sweep` để T026 lọc theo SC-006.
  - Thêm test vào `server/tests/test_k0_collection_toolkit.py`: observation giả có 2 pose_group, kiểm tra ngưỡng riêng được áp dụng đúng nhóm; session giả có `ts` 0.0 → 1.5 s, kiểm tra `checkin_latency_s.p90 == 1.5`.
- [ ] T040 [US2] Ghi lại bundle K0 bằng SFace từ **cùng video gốc** của T001 (sau T016, trước T026): `python server/scripts/k0_field_runner.py --source data/k0_raw/ --out data/k0_bundle_opencv --non-interactive` (hoặc `record_k0_bundle.py` từng video với đúng tham số như T001). Lý do: `replay_eval.py` đọc embedding đã tính sẵn trong `observations.jsonl` (`record_k0_bundle.py` dòng 244–284), nên bundle EdgeFace không đánh giá được SFace. Kiểm tra `data/k0_bundle_opencv/**/meta` có `model_version == "yunet2023mar+sface2021dec"` và số session bằng bundle EdgeFace.
- [ ] T026 [US2] Hiệu chỉnh ngưỡng (cần T039, T040): chạy `python server/scripts/replay_eval.py data/k0_bundle_opencv --sweep 0.30:0.60:0.01 --out specs/002-license-compliant-models/threshold_sweep.json --report-md specs/002-license-compliant-models/threshold_sweep.md`. Chọn cặp (frontal, profile) theo thứ tự lọc:
  1. **Loại** mọi cặp có `false_checkins > 0` hoặc `id_switch_false_checkins > 0`, hoặc `far_ci95_upper > 0.01` (SC-002 chế độ tuyệt đối; Hiến pháp II, cứng).
  2. Trong các cặp còn lại, giữ cặp thoả **toàn bộ SC-006** (`event_frr`, `frr_yaw_le40`, `frr_yaw_40_60`, `latency_p90`) với giá trị PM đã chốt ở mục S6 của `approvals.md`.
  3. Chọn cặp có `event_frr` nhỏ nhất; nếu bằng nhau thì chọn cặp có `latency_p90` nhỏ hơn.

  ⚠ *Bổ sung 2026-11-18 (phát hiện khi làm T039)*: ngưỡng cos chỉ đổi các chỉ số **cấp khung** (`embedding_far`, `frr_yaw_*`). `event_frr`, `false_checkins` và `latency_p90` do **bộ tích luỹ LLR** quyết định (`decision.llr_weights`, `tau_threshold`, hiện là giá trị hiệu chỉnh từ thời EdgeFace). Vì vậy phải chạy thêm `python server/scripts/replay_eval.py data/k0_bundle_opencv --calibrate-llr --out specs/002-license-compliant-models/llr_calibration.json`, đưa `llr_weights`/`tau_threshold` đã hiệu chỉnh vào `config.yaml`, rồi chạy lại replay để kiểm SC-006. Ngưỡng cos chọn ở bước 1–3 vẫn dùng cho `thresholds.opencv` (đường `gallery.match` khi không có bộ ước lượng pose).
  Nếu bước 1 không còn cặp nào thì DỪNG, chuyển "phương án B". Nếu bước 1 còn cặp nhưng bước 2 rỗng thì chuyển sang **T042** (cổng MediaPipe). Ghi bảng lọc và cặp được chọn vào `threshold_sweep.md`. Ghi ngưỡng đã chọn vào `config.yaml` và `server/config.py` (`thresholds.opencv`).
- [ ] T042 [US2] **Cổng quyết định ước lượng pose** (sau T026; research.md R2). Đọc `threshold_sweep.md` và `replay_opencv.json`:
  - **Nhánh A**: đạt SC-006 với PnP 5 điểm. Ghi "Giữ `pose.engine: pnp5`, không cần MediaPipe" vào `results.md`, rồi kết thúc task.
  - **Nhánh B**: trượt SC-006 **chỉ** ở `frr_yaw_40_60` hoặc `latency_p90`, còn `frr_yaw_le40` đạt (lỗi nằm ở pose hoặc điểm mốc góc nghiêng, không phải ở SFace). Triển khai `pose.engine: mediapipe`:
    - (a) Thêm `mediapipe` (Apache-2.0) vào `requirements.txt` dưới dạng tuỳ chọn có comment, kèm dòng tương ứng trong `THIRD_PARTY_LICENSES.md`.
    - (b) Thêm mục `face_landmarker.task` vào `server/models/manifest.json` (role `pose`, `license_weights: "Apache-2.0"`, nguồn là storage.googleapis.com/mediapipe-models chính thức, SHA-256 đầy đủ, `training_data_risk` theo model card), tải qua `download_models.py`.
    - (c) Tạo class `MediaPipePoseEstimator` trong `server/vision/pose3d.py`, cùng interface `estimate()` / `PoseResult`. yaw/pitch/roll lấy từ `facial_transformation_matrixes` (giống `estimatePoseFromMatrix` trong `webapp/guided_wasm.js`). `kps5` lấy từ landmark (mắt: trung bình vòng mắt; mũi: 1; khoé miệng: 61, 291). Nâng `max_reliable_yaw` lên **[75]**.
    - (d) Factory chọn engine theo `cfg["pose"]["engine"]`.
    - (e) Thêm test vào `server/tests/test_pose_pnp.py`, dùng `pytest.importorskip("mediapipe")`.
    - (f) Ghi lại bundle (T040), chạy lại T026, rồi đo T027 với `--backend opencv` và `pose.engine=mediapipe`. Chỉ giữ MediaPipe nếu đạt **cả** SC-006 lẫn SC-003 (≤ 200 ms/chu kỳ); nếu không thì sang phương án B.
  - **Nhánh C**: trượt `frr_yaw_le40` hoặc `event_frr` (vấn đề nằm ở chính SFace). Đổi pose không giúp được, nên DỪNG và trình PM phương án B (plan.md).
- [ ] T027 [US2] Đo **full pipeline** SFace + PnP bằng công cụ của T041 (Hiến pháp III; cấm dùng benchmark một bước đơn lẻ làm đại diện):
  - Chạy `python server/scripts/benchmark.py data/k0_raw/<cùng video camera yếu nhất như T001> --full-pipeline --backend opencv --min-faces 1 --out specs/002-license-compliant-models/full_pipeline_opencv.json`, với cùng `--max-frames`, cùng máy và cùng `--ep` như baseline.
  - Chạy thêm với `--min-faces 10` (hoặc `--simulate-faces 10` nếu baseline cũng mô phỏng) để kiểm SC-003 ≤ 200 ms/chu kỳ.
  - Không còn baseline EdgeFace (L1 = REJECTED), nên chỉ so với ngưỡng tuyệt đối SC-003 (≤ 200 ms/chu kỳ ở 10 mặt); ghi phân rã từng bước vào `results.md`.
  - Nếu > 200 ms/chu kỳ thì triển khai `OpenCVEngine.embed_batch` bằng onnxruntime trên cùng file SFace (input N×3×112×112, chuẩn hoá giống `cv2.FaceRecognizerSF`; **xác minh cosine ≥ 0.999 so với `recognizer.feature`** trước khi dùng), hỗ trợ `execution_provider` openvino, rồi đo lại.
  - Ghi kết quả vào `specs/002-license-compliant-models/results.md`. Nếu vẫn trượt thì DỪNG và báo PM điểm quyết định "phương án B" (plan.md).
- [X] T028 [US2] Chạy migration thật trên `data/facegate.db`: chạy `--dry-run` trước, **sao lưu `data/facegate.db`** sang `data/facegate.db.bak-002`, rồi chạy thật. Ghi số liệu `migrated` / `needs_reenroll` vào `specs/002-license-compliant-models/results.md`.

**Checkpoint**: Quickstart kịch bản 3–5 pass; `results.md` có FAR mới ≤ baseline và K4 ≤ 200 ms.

---

## Phase 5: User Story 3 - Tài nguyên phụ trợ sạch giấy phép (Priority: P2)

**Goal**: Loa mặc định không dùng API không chính thức; WAV có hồ sơ nguồn gốc; có NOTICE và danh sách giấy phép bên thứ ba.

**Independent Test**: Quickstart kịch bản 6.

- [X] T029 [P] [US3] Đổi mặc định loa: `server/config.py` dòng 127 `ap_cfg.setdefault("backend", "web")`; `config.yaml` dòng 157 `backend: web` kèm comment `# tapo: API TP-Link không chính thức (pytapo) - chỉ bật khi khách chấp nhận rủi ro ToS`. Trong `server/vision/audio_prompt.py` (khoảng dòng 741), khi `backend == "tapo"` thì `log.warning("audio_prompt.backend=tapo dùng API không chính thức của TP-Link (pytapo); kiểm tra điều khoản với khách hàng")` một lần lúc khởi tạo. Cập nhật test trong `server/tests/test_audio_prompt.py` nếu có assert mặc định `tapo`.
- [X] T030 [P] [US3] Tạo `third_party/provenance/look_at_camera.md` cho `webapp/audio/look_at_camera.wav` (tác giả dự án tự tạo, xác nhận 2026-11-18), gồm:
  - Thông số file: WAV PCM 16-bit mono 16 kHz, SHA-256 tính bằng `sha256sum`
  - Tác giả: chủ dự án
  - Cách tạo: **để trống cho tác giả điền**: tự thu âm, hoặc TTS (ghi tên công cụ, giọng, chạy cục bộ hay qua dịch vụ online)
  - Tuyên bố "quyền sở hữu thuộc công ty"
  - Ghi chú: nếu dùng TTS online thì Pháp chế kiểm tra điều khoản sử dụng đầu ra
- [X] T031 [P] [US3] Tạo `THIRD_PARTY_LICENSES.md` ở thư mục gốc, bảng `name | version | license (SPDX) | usage | url | notes` (data-model.md §ThirdPartyComponent), gồm:
  - Thư viện Python: opencv-python(-headless) Apache-2.0, numpy BSD-3-Clause, fastapi MIT, uvicorn BSD-3-Clause, python-multipart Apache-2.0, pyyaml MIT, onnxruntime MIT, pillow MIT-CMU, cryptography Apache-2.0 OR BSD-3-Clause
  - Tuỳ chọn: psycopg LGPL-3.0 (usage: optional, dynamic import), pytapo MIT (optional, ghi chú ToS)
  - Công cụ ngoài: MediaMTX MIT (subprocess/binary riêng), FFmpeg LGPL-2.1+/GPL tuỳ bản build (subprocess; ghi chú "nếu đóng gói kèm binary GPL thì phải cung cấp mã nguồn")
  - MediaPipe Tasks Vision Apache-2.0 (optional, chỉ khi vendor vào `/static/vendor/`)
  - 5 model trong manifest (trỏ tới `server/models/manifest.json`)
  - Asset: `look_at_camera.wav` (own work, trỏ tới provenance)
- [X] T032 [P] [US3] Tạo `NOTICE` ở thư mục gốc, liệt kê dòng `notice` của từng model trong manifest (OpenCV Zoo YuNet/SFace, YOLOX © Megvii, Silent-Face-Anti-Spoofing © Minivision) kèm câu "Licensed under the Apache License, Version 2.0 / MIT, see third_party/licenses/". Điền trường `notice` tương ứng trong `server/models/manifest.json`.

**Checkpoint**: Quickstart kịch bản 6 pass.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T034 [P] Sửa mô tả giấy phép và năng lực trong `README.md` (dòng 29–33: `YuNet` + `SFace`, pose PnP, góc nghiêng ~±60°), `docs/HUONG_DAN_KY_THUAT.html`, `docs/GIOI_THIEU_DU_AN.html` và `docs/TAI_LIEU_TRIEN_KHAI_ERP.md` (nếu có nhắc EdgeFace/3DDFA/InsightFace/±90°). Dùng `grep -rniE 'edgeface|3ddfa|insightface|scrfd|arcface|±90|90°|80°' README.md docs/` để tìm hết.
- [X] T035 [P] Sửa comment/chuỗi còn sót trong `server/` và `webapp/`: `grep -rniE 'edgeface|3ddfa|tddfa|insightface|scrfd|w600k|arcface' server webapp setup.sh setup.bat` chỉ được phép còn khớp ở hằng `ARCFACE_TEMPLATE` (template căn chỉnh 5 điểm là số liệu hình học, không phải trọng số) và trong `license_gate.py` (regex chặn).
- [ ] T036 Chạy `python server/scripts/license_gate.py --strict` sau khi Pháp chế ký `legal_ack` cho 5 model (điền `by`/`date`/`ref` thật vào `server/models/manifest.json`); mong đợi exit 0.
- [ ] T037 Chạy toàn bộ `specs/002-license-compliant-models/quickstart.md` kịch bản 1–7 và `pytest server/tests -q`; ghi kết quả SC-001…SC-005 vào `specs/002-license-compliant-models/results.md`. Sau khi có kết quả, **xoá** `data/_baseline_models_noncommercial/` (T001 bước 5) và ghi dòng xác nhận đã xoá vào `results.md`.
- [ ] T038 (**CẦN CHỦ REPO PHÊ DUYỆT, thao tác phá huỷ**) Nếu Pháp chế yêu cầu xoá BFM khỏi lịch sử: `git filter-repo --path-glob 'server/models/tddfa_*.bin' --invert-paths`, force-push lên `origin` (`msoftware-net/msoftware-ai-camera-attendance`), thông báo mọi thành viên clone lại. Không làm nếu chưa có phê duyệt bằng văn bản.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: T033 (Hiến pháp v1.2.0 + phê duyệt) là **cổng chặn** cho T012 trở đi. T001 bước 1–3 làm ngay; **bước 4–6 cần T041** (Phase 2). Toàn bộ T001 phải xong **trước** T012/T014 (sau đó không đo baseline EdgeFace được nữa) và phải giữ video gốc cho T040.
- **ID không theo thứ tự thực thi**: T033 được chuyển lên Phase 1; T039, T040, T041, T042 được thêm sau các lần phân tích 2026-11-18.
- **Cổng phê duyệt** (`approvals.md`): mục C chặn T012 trở đi; mục L1 chặn T001 bước 2–5; mục S6 chặn T026. Khi thực hiện, làm theo **thứ tự xuất hiện trong file** và các ràng buộc ở đây, không theo số ID.
- **Foundational (Phase 2)**: phụ thuộc T003 (LICENSE) → T004 (manifest) → T005 (guard) → T006/T007/T008. T009 độc lập. **T041 độc lập, nên làm đầu tiên** vì nó chặn T001 bước 4 (sau đó chặn T012/T014).
- **US1 (Phase 3)**: phụ thuộc Phase 2. T012, T013 → T014 → T015 → T016 → T017 → T018.
- **US2 (Phase 4)**: phụ thuộc US1 (engine `opencv` mặc định, pose mới). T021 → T022; T024 → T028; T022 + T039 + T040 + S6 → T026 → T042 → (nhánh B: lặp lại T040, T026, T027); T022 → T027. T039 song song được với T021–T025.
- **US3 (Phase 5)**: chỉ phụ thuộc Phase 2 (T032 cần manifest). **Có thể chạy song song với US1/US2**.
- **Polish (Phase 6)**: T034–T035 có thể làm sớm; T036–T037 cần mọi phase xong; T038 tách riêng.

### User Story Dependencies

```
Setup(T033 gate, T001-T003) ⇄ Foundational(T041 → T001 bước 4-6; T004-T009) ─┬─► US1 (T010-T018) ─► US2 (T019-T028, T039, T040) ─┐
                                                       └─► US3 (T029-T032) ──────────────────────────────┴─► Polish (T034-T038)
```

### Parallel Opportunities

- Phase 1: T002 ∥ T003
- Phase 2: T007 (test) ∥ T008 sau khi T005 xong
- US1: T010 ∥ T011 (test); T012 ∥ T013 (khác file)
- US2: T019 ∥ T020 ∥ T039; T023 ∥ T024 ∥ T025
- US3: T029 ∥ T030 ∥ T031 ∥ T032 (toàn bộ)
- Polish: T034 ∥ T035

## Parallel Example: User Story 3

```bash
Task: "T029 Đổi mặc định audio_prompt.backend sang web trong server/config.py, config.yaml"
Task: "T030 Tạo third_party/provenance/look_at_camera.md"
Task: "T031 Tạo THIRD_PARTY_LICENSES.md"
Task: "T032 Tạo NOTICE"
```

## Parallel Example: User Story 1

```bash
Task: "T012 Gỡ EdgeFaceEngine/OnnxEngine trong server/vision/engines.py"
Task: "T013 Viết lại pose3d.py bằng solvePnP 5 điểm"
```

## Implementation Strategy

### MVP First (User Story 1)

1. T033 (gửi duyệt Hiến pháp) ∥ T001 bước 1–3 (giữ video gốc, bundle EdgeFace, replay) ∥ T041 (công cụ full pipeline) → T001 bước 4–6 (baseline full pipeline) → phần còn lại của Phase 2 → chờ phê duyệt → Phase 3.
2. **DỪNG và kiểm chứng**: license gate exit 0, pytest xanh. Bản build lúc này đã **hợp pháp**, nhưng embedding cũ chưa migrate nên mọi người là `Unknown`. **Không deploy** lên camera thật trước khi xong US2.

### Incremental Delivery

1. US1: hợp pháp (chỉ dùng nội bộ / demo).
2. US2: đủ chất lượng để deploy production (migration + ngưỡng + K4).
3. US3: hoàn thiện hồ sơ giao khách (song song được).
4. Polish: Hiến pháp, tài liệu, chữ ký Pháp chế (`--strict`), rồi phát hành.

### Điểm quyết định

Sau T026 → T042 → T027: nếu có false check-in, hoặc trượt SC-006 ở dải chính diện (nhánh C của T042), hoặc MediaPipe cũng không cứu được dải 40–60° trong ngân sách K4, thì dừng và trình PM **phương án B** (mua giấy phép thương mại EdgeFace từ Idiap; pose PnP và manifest giữ nguyên).

## Notes

- Mọi thông điệp log/lỗi mới viết tiếng Việt, theo phong cách code hiện có.
- Không xoá hàng `embeddings` cũ trong feature này; việc dọn dẹp chờ Pháp chế xác nhận.
- Commit sau mỗi task hoặc nhóm logic; tuân `.github/PULL_REQUEST_TEMPLATE.md` khi mở PR (Release Note bắt buộc, vì feature này đổi config, model và schema dữ liệu runtime).
