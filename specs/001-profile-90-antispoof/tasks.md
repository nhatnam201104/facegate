---

description: "Danh sách tác vụ triển khai cho tính năng Nhận diện khuôn mặt góc nghiêng đến 90° kèm xác thực chống giả mạo đa lớp"
---

# Tasks: Nhận diện khuôn mặt góc nghiêng đến 90° kèm xác thực chống giả mạo đa lớp

**Input**: Tài liệu thiết kế từ `/specs/001-profile-90-antispoof/` (`plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`)

**Prerequisites**: `plan.md` (bắt buộc), `spec.md` (4 User Stories: US1 [P1], US2 [P1], US3 [P2], US4 [P3]), `research.md`, `data-model.md`, `contracts/api-and-websocket.md`, `contracts/config-schema.md`

**Tests**: Có bao gồm kiểm thử tự động (`pytest`) trước từng giai đoạn theo Nguyên tắc V của `.specify/memory/constitution.md` (*Test-First & Measurable Gates*).

**Organization**: Các tác vụ được tổ chức theo từng User Story độc lập để có thể kiểm thử và bàn giao tăng dần theo từng lát cắt giá trị.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Có thể thực thi song song (khác file, không phụ thuộc tác vụ chưa xong trong cùng pha)
- **[Story]**: Nhãn User Story tương ứng (`[US1]`, `[US2]`, `[US3]`, `[US4]`) — chỉ gắn ở các pha User Story
- Mọi tác vụ đều chỉ định đường dẫn file chính xác tính từ thư mục gốc dự án

---

## Phase 1: Setup (Hạ tầng Mô hình & Cấu hình Chung)

**Purpose**: Cập nhật trình tải mô hình ONNX nguồn mở thương mại hóa được và hợp đồng cấu hình tương thích ngược trước khi xây dựng các mô-đun cốt lõi.

- [X] T001 Cập nhật danh mục tải mô hình ONNX mới (`3ddfa_v2_mb1_120x120.onnx` + BFM basis, `edgeface_xs_gamma_06.onnx`, `yolox_nano_coco.onnx` ở chế độ mặc định và cờ `--research` cho `adaface_ir_18.onnx`) trong `scripts/download_models.py`
- [X] T002 [P] Mở rộng bộ nạp cấu hình và chuẩn hóa tương thích ngược `recognition.thresholds.<backend>` (tự động ánh xạ số thực đơn sang `{"frontal": val, "profile": round(val + 0.06, 2)}`) trong `app/config.py`
- [X] T003 [P] Cập nhật các tham số mặc định cho góc 90° (`quality.max_yaw: 90`, `max_pitch: 35`, `max_roll: 35`, `recognition.frontal_max_yaw: 40`, `pose_hysteresis: 5`, `profile_margin: 0.08`, `min_profile_frames: 4` và khối `liveness.context` / `parallax` / `moire`) trong `config.yaml`

---

## Phase 2: Foundational (Nền tảng Chặn Trước Mọi User Story)

**Purpose**: Di trú cơ sở dữ liệu SQLite tự động không mất dữ liệu cũ và xây dựng mô-đun ước lượng tư thế 3D `3DDFA_V2` dùng chung cho toàn bộ nhận diện, chống giả mạo và đăng ký.

**⚠️ CRITICAL**: Không bắt đầu bất kỳ User Story nào trước khi hoàn tất Pha này.

- [X] T004 Nâng cấp lược đồ và hàm `DB._migrate()` trong `app/db.py`: thêm cột `pose_group TEXT NOT NULL DEFAULT 'frontal'` (`'frontal'` | `'left_profile'` | `'right_profile'`), `yaw REAL`, `pitch REAL` + index `ix_emb_model_pose` cho bảng `embeddings`; thêm cột `pose_group TEXT`, `yaw REAL` (`-95.0 .. +95.0`), `verification_tier TEXT` (`'verified'` | `'profile_verified'` | `'rejected_spoof'` | `'unverified'`), `liveness_mode TEXT` (`'texture_fas'` | `'context_parallax'` | `'hybrid'` | `'disabled'`), `liveness_detail TEXT` cho bảng `events`; thêm cột `pose_group TEXT`, `min_abs_yaw REAL`, `verification_tier TEXT`, `spoof_reason TEXT` cho bảng `appearances`
- [X] T005 [P] Viết bộ kiểm thử đơn vị cho ước lượng góc Euler 3D, phân nhóm tư thế có trễ hysteresis (±5° quanh biên ±40°) và phép chiếu sai số homography phẳng trong `tests/test_pose3d.py`
- [X] T006 Xây dựng mô-đun `3DDFA_V2` ONNX (`PoseEstimator3D`, `PoseResult`, phân loại `classify_pose_group` với hysteresis `frontal` `|yaw| <= 40°` nhả ở `±45°`, `left_profile` `-90° <= yaw < -40°`, `right_profile` `+40° < yaw <= +90°`, và tái tạo 68 điểm mốc 3D) trong `app/pose3d.py`
- [X] T007 [P] Nâng cấp bộ đánh giá chất lượng khuôn mặt để chỉ đo độ nét (`min_blur`) và độ sáng (`min_brightness..max_brightness`) trên vùng da mặt thực sự nhìn thấy (*visible skin ROI* theo hướng `yaw`), bổ sung kiểm tra `max_pitch: 35`, `max_roll: 35`, mã che khuất `occluded` trong `app/quality.py` và đồng bộ nhãn tiếng Việt trong `_REASON` (`app/pipeline.py`) cùng `QUALITY_HINT` (`app/guided.py`)

**Checkpoint**: Nền tảng DB và ước lượng tư thế 3D đã sẵn sàng — các User Story có thể bắt đầu triển khai.

---

## Phase 3: User Story 1 - Nhận diện nhân viên đi ngang qua camera ở góc nghiêng tới 90° (Priority: P1) 🎯 MVP

**Goal**: Nhân viên đi bộ tự nhiên ngang qua 1 camera ở góc mặt từ 0° đến ±90° được giữ liền mạch 1 hành trình, tự động ưu tiên khung hình góc ≤ 40° (*Best-shot*) nếu có, hoặc khớp trực tiếp mẫu sườn mặt cùng phía (`Profile-to-Profile`) với ngưỡng chặt hơn mà không nhận nhầm người lạ.

**Independent Test**: Cho nhân viên đã có mẫu đa góc và người lạ đi ngang qua camera ở góc 75°–90° (cả hai chiều) và trường hợp có liếc nhẹ 35° trong 2 frame; kiểm tra đúng mã nhân viên được chốt, ưu tiên `verified` khi có frame ≤ 40°, và người lạ trả về `Unknown`.

### Tests for User Story 1 ⚠️

> **NOTE: Viết các bài kiểm thử này TRƯỚC, xác nhận FAIL trước khi viết code triển khai**

- [X] T008 [P] [US1] Viết kiểm thử căn chỉnh 3 khung hình học (`TEMPLATE_FRONTAL`, `TEMPLATE_LEFT_PROFILE`, `TEMPLATE_RIGHT_PROFILE`) và trích đặc trưng `EdgeFaceEngine` trong `tests/test_pose3d.py`
- [X] T009 [P] [US1] Viết kiểm thử `PoseGallery` tách 3 ma trận con, kiểm thử `Track` tách 3 ngăn embedding không tự cắt track khi xoay đầu `0° -> -85° -> +85°` và quy tắc ưu tiên Best-shot trong `tests/test_tracker_buckets.py`

### Implementation for User Story 1

- [X] T010 [P] [US1] Cài đặt 3 khung căn chỉnh tư thế `112×112` (`align_by_pose` cho `frontal`, `left_profile`, `right_profile` dựa trên điểm mốc 3D nhìn thấy) và bổ sung lớp `EdgeFaceEngine` (`edgeface_xs_gamma_06.onnx`) cùng tùy chọn `AdaFaceEngine` trong `app/engines.py`
- [X] T011 [P] [US1] Nâng cấp `Gallery` thành ma trận con phân tách theo `pose_group` (`mats['frontal' | 'left_profile' | 'right_profile']`), áp ngưỡng riêng `(thresholds.frontal, margin)` vs `(thresholds.profile, profile_margin)` và tính `pose_coverage` từng nhân viên trong `app/gallery.py`
- [X] T012 [US1] Nâng cấp `Track` và `IoUTracker` để lưu 3 ngăn tích lũy đặc trưng riêng (`buckets`), chỉ kiểm tra `split_cosine` trong cùng một `pose_group`, hỗ trợ tham số tùy chọn `person_boxes: list | None = None` trong `update()` (để US1 vận hành độc lập khi chưa bật `ContextDetector`, và liên kết `person_bbox` dự phòng khi có dữ liệu) cùng hàm `agg_embedding(pose_group)` trong `app/tracker.py`
- [X] T013 [US1] Cập nhật luồng nhận diện và hàm `_identify` trong `app/pipeline.py` để tích hợp `PoseEstimator3D`, thực thi quy tắc ưu tiên Best-shot (`frontal >= min_good_frames` trước, sau đó xét `left_profile`/`right_profile >= min_profile_frames`), tự động nâng hạng trong cùng lượt đi và ghi `pose_group`, `yaw`, `min_abs_yaw` vào DB

**Checkpoint**: User Story 1 hoàn chỉnh và có thể kiểm thử độc lập toàn bộ luồng nhận diện 0°–90° cùng Best-shot selection.

---

## Phase 4: User Story 2 - Chống giả mạo đa lớp ở cả góc chính diện lẫn góc nghiêng 90° (Priority: P1)

**Goal**: Bảo vệ tuyệt đối luồng check-in ở góc 40°–90° (nơi `MiniFASNet` mất hiệu lực) bằng tổ hợp phát hiện vật chứa giả mạo (`cell phone`, `tablet`, khung ảnh), kiểm tra gắn kết Đầu–Cơ thể, kiểm định thị sai chuyển động 3D qua chuỗi ≥ 6 frame và quét vân Moiré màn hình, đồng thời giữ `MiniFASNet` cho góc ≤ 40°.

**Independent Test**: Đưa điện thoại/máy tính bảng phát ảnh hoặc video góc 85° của nhân viên đi ngang qua camera -> bị chặn đỏ `spoof` kèm lý do cụ thể và lưu clip; cho nhân viên thật đi bộ tự nhiên góc 85° qua camera -> được duyệt `profile_verified` không bị chặn nhầm.

### Tests for User Story 2 ⚠️

- [X] T014 [P] [US2] Viết bộ kiểm thử toàn diện cho 4 lớp chống giả mạo (phát hiện màn hình điện thoại quanh mặt, 2 khuôn mặt trên 1 cơ thể `multi_face_single_body`, mặt lơ lửng `unattached_face`, quỹ đạo ảnh phẳng 2D `planar_no_parallax`, vân màn hình `moire_pattern`, và quỹ đạo người thật 3D hợp lệ) trong `tests/test_liveness_profile.py`

### Implementation for User Story 2

- [X] T015 [P] [US2] Xây dựng mô-đun `ContextDetector` bọc `YOLOX-Nano` COCO ONNX (`416×416`) chạy 1 lần/chu kỳ phát hiện để trích xuất `person` + thiết bị màn hình (`cell phone`, `laptop`, `tv`, `book`), kiểm tra giao cắt vật chứa giả mạo quanh vùng `2.5×` hộp mặt và kiểm định tỷ lệ giải phẫu Đầu–Cơ thể `face_body_ratio` `[0.12, 0.65]` trong `app/context_detector.py`
- [X] T016 [US2] Mở rộng `app/liveness.py` thành bộ điều phối chống giả mạo định tuyến theo tư thế: giữ `MiniFASNetV2 + V1SE` cho các frame `|yaw| <= 40°`, cài đặt bộ kiểm định Thị sai Chuyển động 3D (`parallax.min_frames: 6`, `min_travel_ratio: 0.08`, `min_depth_score: 0.55` dựa trên chuỗi 68 điểm 3D và sai số homography phẳng) cùng bộ quét phổ `MoireFFT` cho nhánh `40° < |yaw| <= 90°`
- [X] T017 [US2] Tích hợp `ContextDetector` (tự động ngắt khi `sleeping` để tiết kiệm CPU) và máy trạng thái xác thực 4 lớp vào `app/pipeline.py` và `app/main.py`, xuất đúng `verification_tier` (`'verified'` | `'profile_verified'` | `'rejected_spoof'`), `liveness_mode`, JSON `liveness_detail` và mã `spoof_reason` (`'screen_detected'` | `'multi_face_single_body'` | `'unattached_face'` | `'planar_no_parallax'` | `'moire_pattern'` | `'texture_spoof'`)

**Checkpoint**: User Story 1 + User Story 2 vận hành đồng bộ — nhận diện tới 90° đã được bảo vệ đầy đủ bởi chống giả mạo đa lớp trên CPU.

---

## Phase 5: User Story 3 - Đăng ký khuôn mặt có hướng dẫn mở rộng 7 góc & Tương thích dữ liệu cũ (Priority: P2)

**Goal**: Thu thập thêm 2 góc nghiêng sâu trái/phải (`70°–85°`) trong cả luồng quay hướng dẫn trực tiếp (7 bước) lẫn tải video mà không loại bỏ nhầm mẫu nghiêng sâu là ngoại lai; cho phép nhân viên cũ quay bổ sung riêng 2 góc nghiêng và giữ nguyên khả năng chấm công ở góc ≤ 40° ngay sau nâng cấp.

**Independent Test**: Chạy phiên đăng ký hướng dẫn 7 bước cho nhân viên mới và chế độ quay bổ sung `supplement_only=true` cho nhân viên cũ 5 góc; xác nhận DB lưu đủ cả 3 `pose_group` và nhân viên cũ chưa quay bổ sung vẫn khớp bình thường ở ngăn `frontal`.

### Tests for User Story 3 ⚠️

- [X] T018 [P] [US3] Mở rộng kiểm thử tự động cho phiên đăng ký hướng dẫn 7 bước (5 góc chữ thập + `nghieng-sau-trai`, `nghieng-sau-phai` ở `±78°`), chế độ `supplement_only`, lọc outlier theo từng `pose_group` và tự động backfill tái trích xuất vector cho nhân viên 5 góc cũ khi đổi `model_version` trong `tests/test_guided.py`

### Implementation for User Story 3

- [X] T019 [P] [US3] Nâng cấp `process_enrollment`, `store_enrollment` và bổ sung hàm `backfill_legacy_embeddings(ctx)` (tự động đọc lại ảnh mẫu `data/enroll/<code>/*.jpg` để sinh vector cho `engine.model_version` mới khi nhân viên cũ chưa có embedding tương ứng) trong `app/enrollment.py`, đồng thời kiểm tra loại bỏ ngoại lai (`outlier_min_cosine: 0.42`) tách riêng trong từng `pose_group` và hỗ trợ cờ `supplement_only` giữ lại mẫu `frontal` cũ
- [X] T020 [US3] Mở rộng `STEPS`, `evaluate` và `GuidedSession` trong `app/guided.py` lên 7 bước khi `enable_profile_90: true` (thêm 2 bước nghiêng sâu `profile_yaw_min: 65` đến `profile_yaw_max: 88`, kiểm tra đồng nhất danh tính bước nghiêng qua quỹ đạo liên tục thay vì ép cosine trực tiếp với bước 0°) và trả thêm `current_pose` trong `snapshot()`
- [X] T021 [US3] Kích hoạt `backfill_legacy_embeddings(ctx)` lúc khởi động `lifespan`, cập nhật các endpoint `POST /api/enroll/guided/start` (tham số `profile_90`, `supplement_only`), `GET /api/employees` (trả thêm `pose_coverage` và cờ `ready_90`) trong `app/main.py` cùng giao diện hướng dẫn 7 góc và nút *"Bổ sung góc 90°"* trên tab Đăng ký / Nhân viên trong `app/static/index.html` và `app/static/app.js`

**Checkpoint**: Quy trình đăng ký 7 góc và tương thích ngược nhân viên cũ hoạt động trọn vẹn từ API tới giao diện Web.

---

## Phase 6: User Story 4 - Phân tầng mức độ tin cậy và hậu kiểm dành cho HR (Priority: P3)

**Goal**: Giúp HR phân biệt minh bạch giữa check-in chính diện (`verified`) và check-in thuần góc nghiêng (`profile_verified`), lọc nhanh các lượt chấm công góc nghiêng kèm clip bằng chứng để rà soát bảng công và xuất báo cáo CSV đầy đủ.

**Independent Test**: Tạo 1 sự kiện `verified`, 1 sự kiện `profile_verified` và 1 sự kiện `spoof` (`screen_detected`); kiểm tra bộ lọc theo `tier` trên API/UI tab Lịch sử & Sự kiện, thẻ thống kê `/api/metrics` và các cột mới trong file CSV tải về.

### Implementation for User Story 4

- [X] T022 [P] [US4] Mở rộng `GET /api/status`, `GET /api/events` (tham số lọc `tier`, `pose_group`), `GET /api/metrics` (`recognized_frontal`, `recognized_profile`, `spoof_by_reason`, `false_accept_rate_profile`) và `GET /api/events.csv` (thêm cột `pose_group`, `yaw`, `verification_tier`, `liveness_mode`) trong `app/main.py`
- [X] T023 [P] [US4] Mở rộng `GET /api/appearances` và `GET /api/appearances.zip` (tham số lọc `tier`), cập nhật `timesheet()` trong `app/attendance.py` và `GET /api/attendance.csv` (thêm cột `Mức xác thực`) trong `app/records.py`
- [X] T024 [US4] Cập nhật giao diện hiển thị huy hiệu phân tầng (`Chính diện` vs `Góc nghiêng · Có clip`), cập nhật từ điển `REASON` (`pitch`, `roll`, `occluded`), thông điệp lý do giả mạo tiếng Việt cụ thể và bộ lọc hậu kiểm `tier` trên các tab Trực tiếp, Chấm công, Lịch sử và Sự kiện trong `app/static/index.html`, `app/static/app.js`, `app/static/attendance.js`, `app/static/history.js` và `app/static/style.css`

**Checkpoint**: Toàn bộ 4 User Stories hoàn tất độc lập, đầy đủ công cụ quản trị và hậu kiểm cho HR.

---

## Phase 7: Polish & Cross-Cutting Concerns (Đo kiểm Hiệu năng & Tài liệu)

**Purpose**: Đo kiểm định lượng ngân sách độ trễ CPU (< 200 ms), hoàn thiện tài liệu kỹ thuật và xác minh toàn bộ kịch bản trong `quickstart.md`.

- [X] T025 [P] Nâng cấp công cụ đo kiểm `scripts/benchmark.py` hỗ trợ cờ `--profile-eval` để đo thời gian suy luận riêng của từng tầng (`YuNet`, `YOLOX-Nano`, `3DDFA_V2`, `EdgeFace-XS`/`SFace`, `Liveness`) và thống kê tỷ lệ phát hiện theo từng dải góc `yaw` (`0°–40°`, `40°–65°`, `65°–90°`)
- [X] T026 [P] Cập nhật hướng dẫn đăng ký 7 góc, giải thích cơ chế chống giả mạo 4 lớp và phân tầng hậu kiểm `profile_verified` trong `README.md` và `docs/HUONG_DAN_KY_THUAT.html`
- [X] T027 Chạy toàn bộ bộ kiểm thử `.venv/bin/pytest -v` và xác minh trọn vẹn các kịch bản nghiệm thu đầu-cuối theo `specs/001-profile-90-antispoof/quickstart.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: Không có phụ thuộc — bắt đầu ngay lập tức (`T001` – `T003`).
- **Phase 2 (Foundational)**: Phụ thuộc Phase 1 — **CHẶN** toàn bộ các User Story cho đến khi hoàn tất di trú DB (`T004`) và mô-đun `app/pose3d.py` (`T005` – `T007`).
- **Phase 3 (US1 - Nhận diện 90° & Best-shot, P1)**: Phụ thuộc Phase 2 — cung cấp lõi nhận diện đa góc MVP (`T008` – `T013`).
- **Phase 4 (US2 - Chống giả mạo đa lớp, P1)**: Phụ thuộc Phase 2 (mô-đun `T014` – `T016` có thể phát triển song song với US1; bước tích hợp pipeline `T017` nối tiếp sau `T013`).
- **Phase 5 (US3 - Đăng ký 7 góc & Tương thích cũ, P2)**: Phụ thuộc Phase 2 và các khung căn chỉnh của `T010` — có thể triển khai song song với Phase 4 (`T018` – `T021`).
- **Phase 6 (US4 - Phân tầng tin cậy & Hậu kiểm HR, P3)**: Phụ thuộc các trường sự kiện/phân tầng đã ghi ở `T013` và `T017` (`T022` – `T024`).
- **Phase 7 (Polish & Benchmark)**: Thực hiện sau khi hoàn tất các User Story mục tiêu (`T025` – `T027`).

### User Story Dependency Graph

```text
Phase 1: Setup (T001..T003)
    │
    ▼
Phase 2: Foundational — DB Migration + 3DDFA_V2 Pose3D + Visible Quality (T004..T007)
    │
    ├───────────────────────────────┬───────────────────────────────┐
    ▼                               ▼                               ▼
Phase 3: US1 (P1) 🎯 MVP       Phase 4: US2 (P1)               Phase 5: US3 (P2)
Multi-Pose & Best-Shot         4-Layer Anti-Spoofing           7-Step Guided Enrollment
(T008..T013)                   (T014..T016 song song,          (T018..T021, sau T010)
    │                           T017 tích hợp sau T013)             │
    └───────────────┬───────────────┘                               │
                    ▼                                               │
            Phase 6: US4 (P3)                                       │
            HR Verification Tiers & Clip Audit (T022..T024)         │
                    │                                               │
                    └───────────────────────┬───────────────────────┘
                                            ▼
                                Phase 7: Polish & Benchmark (T025..T027)
```

---

## Parallel Opportunities & Examples

### Ví dụ chạy song song trong Phase 2 (Foundational)
```text
Task T004: Di trú schema SQLite trong app/db.py
Task T005: Viết unit test ước lượng tư thế 3D trong tests/test_pose3d.py
Task T007: Cập nhật đánh giá chất lượng vùng da nhìn thấy trong app/quality.py
```

### Ví dụ chạy song song trong User Story 1 & User Story 2
```text
# Nhóm kiểm thử viết trước (khác file hoàn toàn):
Task T008 [US1]: Kiểm thử căn chỉnh 3 khung & EdgeFace trong tests/test_pose3d.py
Task T009 [US1]: Kiểm thử PoseGallery & Pose-Bucketed Tracker trong tests/test_tracker_buckets.py
Task T014 [US2]: Kiểm thử chống giả mạo 4 lớp trong tests/test_liveness_profile.py

# Nhóm mô-đun lõi độc lập (khác file hoàn toàn):
Task T010 [US1]: Căn chỉnh 3 khung + EdgeFaceEngine trong app/engines.py
Task T011 [US1]: Ma trận con theo pose_group trong app/gallery.py
Task T015 [US2]: ContextDetector (YOLOX-Nano) trong app/context_detector.py
```

---

## Implementation Strategy

### 1. Chốt MVP An toàn (Setup + Foundational + US1 + US2)
Vì bài toán chấm công yêu cầu **độ chính xác đi đôi với chống giả mạo** (cả **US1** và **US2** đều mang độ ưu tiên **P1**), gói phát hành khả dụng tối thiểu (Security-Complete MVP) bao gồm:
1. Hoàn tất **Phase 1 + Phase 2** (`T001` – `T007`): Có ước lượng góc 3D tới ±90° và DB mới.
2. Hoàn tất **Phase 3 (US1)** (`T008` – `T013`): Nhận diện góc nghiêng + ưu tiên Best-shot hoạt động.
3. Hoàn tất **Phase 4 (US2)** (`T014` – `T017`): Đóng chặt lỗ hổng điện thoại / ảnh phẳng ở góc 90°.
4. **Dừng kiểm chứng (STOP & VALIDATE)**: Kiểm tra bằng video quay nghiêng và điện thoại giả mạo trước khi làm UI mở rộng.

### 2. Bàn giao Tăng trưởng (Incremental Delivery)
- **Đợt 2 — Phase 5 (US3)**: Bật giao diện hướng dẫn quay 7 góc và chế độ bổ sung 2 góc nghiêng cho nhân viên cũ (`T018` – `T021`).
- **Đợt 3 — Phase 6 & 7 (US4 + Polish)**: Bàn giao bộ lọc hậu kiểm `profile_verified` trên bảng công/lịch sử cho HR và báo cáo đo kiểm `benchmark.py` (`T022` – `T027`).
