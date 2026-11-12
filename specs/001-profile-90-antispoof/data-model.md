# Phase 1 Data Model: Nhận diện Khuôn mặt 90° & Chống giả mạo Đa lớp

**Feature**: `001-profile-90-antispoof`
**Date**: 2026-11-11
**Storage Engine**: SQLite (WAL mode) + In-memory Pose-Bucketed Gallery & Tracker State

---

## 1. Phân loại Nhóm Góc & Hạt nhân Tư thế (Pose Taxonomy)

Hệ thống tách biệt **nhóm căn chỉnh/so khớp (`pose_group`)** và **ô góc chi tiết khi đăng ký (`pose_bin`)**:

| `pose_group` | Dải góc ngang `yaw` (đã hiệu chuẩn) | Các `pose_bin` thành phần | Khung căn chỉnh `112×112` | Chế độ chống giả mạo áp dụng |
|---|---|---|---|---|
| `frontal` | $-40^\circ \le \text{yaw} \le +40^\circ$ *(trễ nhả $\pm 45^\circ$)* | `thang-giua`, `thang-ngang`, `thang-cui`, `trai-giua`, `phai-giua` | `TEMPLATE_FRONTAL` (5 điểm 2 mắt – mũi – miệng đối xứng) | `MiniFASNet` (V2+V1SE) + kiểm tra vật chứa cơ bản |
| `left_profile` | $-90^\circ \le \text{yaw} < -40^\circ$ *(người quay sang phải của họ)* | `nghieng-sau-trai` *(~ -70°..-85°)*, `ban-nghieng-trai` *(~ -45°..-65°)* | `TEMPLATE_LEFT_PROFILE` (điểm mốc 3D sườn mặt phải nhìn thấy) | `SpoofMedium` + `BodyCoupling` + `Parallax3D` + `MoireFFT` |
| `right_profile` | $+40^\circ < \text{yaw} \le +90^\circ$ *(người quay sang trái của họ)* | `nghieng-sau-phai` *(~ +70°..+85°)*, `ban-nghieng-phai` *(~ +45°..+65°)* | `TEMPLATE_RIGHT_PROFILE` (điểm mốc 3D sườn mặt trái nhìn thấy) | `SpoofMedium` + `BodyCoupling` + `Parallax3D` + `MoireFFT` |

---

## 2. Lược đồ Cơ sở Dữ liệu SQLite & Di trú Tự động (`app/db.py`)

Toàn bộ nâng cấp được thực hiện tự động qua `DB._migrate()` bằng `ALTER TABLE ... ADD COLUMN` không phá vỡ dữ liệu hiện hữu (`data/facegate.db`).

### 2.1. Bảng `embeddings` (Mở rộng cột nhóm tư thế)

| Cột | Kiểu | Bắt buộc | Mặc định / Di trú | Mô tả |
|---|---|---|---|---|
| `id` | `INTEGER PK` | Có | Auto | Khóa chính |
| `employee_id` | `INTEGER FK` | Có | `ON DELETE CASCADE` | Tham chiếu `employees(id)` |
| `vec` | `BLOB` | Có | — | Vector L2-normalized `float32` |
| `dim` | `INTEGER` | Có | — | Số chiều (`128` cho SFace, `512` cho EdgeFace/AdaFace) |
| `model_version` | `TEXT` | Có | — | Phiên bản model + chuẩn template (vd: `edgeface_xs_v1+3ddfa_v2`) |
| `quality` | `REAL` | Không | — | Điểm chất lượng vùng mặt nhìn thấy (`0.0 .. 1.0`) |
| `pose_bin` | `TEXT` | Không | — | Ô góc cụ thể (5 ô cũ + `nghieng-sau-trai`, `nghieng-sau-phai`) |
| **`pose_group`** *(Mới)* | `TEXT` | Có | `'frontal'` | Một trong: `'frontal'` \| `'left_profile'` \| `'right_profile'` |
| **`yaw`** *(Mới)* | `REAL` | Không | `NULL` | Góc quay ngang ước lượng từ 3DMM tại thời điểm lấy mẫu (độ) |
| **`pitch`** *(Mới)* | `REAL` | Không | `NULL` | Góc ngẩng/cúi ước lượng từ 3DMM (độ) |
| `source` | `TEXT` | Có | `'enroll'` | `'enroll'` \| `'enroll_supplement'` |
| `crop_path` | `TEXT` | Không | — | Đường dẫn tương đối ảnh mặt đã cắt |
| `created_at` | `REAL` | Có | — | Unix timestamp |

- **Quy tắc di trú (`_migrate`)**: Các bản ghi cũ có `pose_group IS NULL` được tự động điền `pose_group = 'frontal'` vì toàn bộ 5 bước đăng ký cũ nằm trong $|\text{yaw}| \le 45^\circ$.
- **Chỉ mục mới**: `CREATE INDEX IF NOT EXISTS ix_emb_model_pose ON embeddings(model_version, pose_group);`

### 2.2. Bảng `events` (Mở rộng bằng chứng góc nhìn & phân tầng xác thực)

| Cột mới | Kiểu | Giá trị hợp lệ | Mô tả |
|---|---|---|---|
| **`pose_group`** | `TEXT` | `'frontal'` \| `'left_profile'` \| `'right_profile'` | Nhóm góc nhìn đã dùng để chốt danh tính (hoặc lúc bắt giả mạo) |
| **`yaw`** | `REAL` | `-95.0 .. +95.0` | Góc `yaw` trung bình của ngăn dùng để chốt sự kiện |
| **`verification_tier`** | `TEXT` | `'verified'` \| `'profile_verified'` \| `'rejected_spoof'` \| `'unverified'` | Phân tầng tin cậy cho HR đối soát lương |
| **`liveness_mode`** | `TEXT` | `'texture_fas'` \| `'context_parallax'` \| `'hybrid'` \| `'disabled'` | Phương thức chống giả mạo đã thực thi |
| **`liveness_detail`** | `TEXT` | Chuỗi JSON gọn | Chi tiết từng lớp: `{"fas":0.84,"medium_clean":true,"body_coupled":true,"parallax_ok":true,"moire_ok":true,"reason":null}` |

- **Quy tắc di trú (`_migrate`)**: Các sự kiện `checkin` cũ được cập nhật `verification_tier = 'verified'`, `pose_group = 'frontal'`, `liveness_mode = 'texture_fas'`.

### 2.3. Bảng `appearances` (Lịch sử xuất hiện kèm clip)

| Cột mới | Kiểu | Mô tả |
|---|---|---|
| **`pose_group`** | `TEXT` | Nhóm góc chính của lần xuất hiện (`frontal` / `left_profile` / `right_profile`) |
| **`min_abs_yaw`** | `REAL` | Góc gần chính diện nhất đạt được trong cả hành trình (chứng minh Best-shot) |
| **`verification_tier`** | `TEXT` | Đồng bộ cấp độ xác thực từ sự kiện liên kết để lọc nhanh trên tab Lịch sử |
| **`spoof_reason`** | `TEXT` | Mã nguyên nhân cụ thể nếu `kind = 'spoof'`: `'screen_detected'` \| `'multi_face_single_body'` \| `'unattached_face'` \| `'planar_no_parallax'` \| `'moire_pattern'` \| `'texture_spoof'` |

---

## 3. Thực thể Bộ nhớ Đệm Runtime (In-Memory Entities)

### 3.1. `PoseGallery` (Nâng cấp `app/gallery.py`)
Thay vì một ma trận phẳng duy nhất, `Gallery` tổ chức thành từ điển 3 ma trận con theo `pose_group`:
- `mats: dict[str, np.ndarray]` — khóa `'frontal'`, `'left_profile'`, `'right_profile'`.
- `owners: dict[str, np.ndarray]` — mảng `employee_id` tương ứng từng hàng.
- `coverage: dict[int, set[str]]` — tập các `pose_group` mà mỗi nhân viên đã đăng ký (phục vụ gắn cờ *"Thiếu mẫu góc 90°"* trên tab Nhân viên).
- **Quy tắc so khớp (`match_pose`)**:
  - Truy vấn vector của ngăn `g` chỉ so khớp với `mats[g]` (không bao giờ so vector `left_profile` với `frontal` hoặc `right_profile`).
  - Với `g == 'frontal'`: áp dụng `(thresholds.frontal, margins.frontal)`.
  - Với `g in ('left_profile', 'right_profile')`: áp dụng `(thresholds.profile, margins.profile)` chặt hơn (mặc định cao hơn chính diện `+0.06` ở threshold và `+0.02` ở margin).

### 3.2. `PoseTrack` (Nâng cấp `Track` trong `app/tracker.py`)
Mỗi hành trình người đi qua cổng lưu trữ:
- `id: int`, `bbox: np.ndarray` (hộp mặt hiện tại), `person_bbox: np.ndarray | None` (hộp cơ thể liên kết).
- `buckets: dict[str, list[tuple[np.ndarray, float]]]` — 3 danh sách `(embedding, quality_weight)` tách rời theo `'frontal'`, `'left_profile'`, `'right_profile'`.
- `pose_history: deque[PoseSample]` — cửa sổ tối đa 24 mẫu gần nhất gồm `(ts, yaw, pitch, roll, face_center_xy, landmarks_3d_68, homography_err)` phục vụ kiểm định thị sai 3D.
- `context_flags: deque[ContextSample]` — lịch sử kiểm tra vật chứa (`medium_hit`, `body_coupled`, `dual_face_on_body`, `moire_score`).
- `fas_scores: list[float]` — chỉ nhận giá trị từ `MiniFASNet` ở các frame có $|\text{yaw}| \le 40^\circ$.
- `verification_tier: str | None` — `'verified'` hoặc `'profile_verified'`.

---

## 4. Máy Trạng thái Xác thực Hành trình (Track Verification State Machine)

```text
[NEW TRACK]
    │
    ├─► Phát hiện vật chứa (điện thoại/iPad) HOẶC 2 mặt trên 1 thân người (≥ 2 frame)
    │       └──► [SPOOF_LOCKED] (phát cảnh báo đỏ + ghi event kind='spoof' + lưu clip bằng chứng)
    │
    ├─► Có ≥ min_good_frames ở ngăn 'frontal' (|yaw| ≤ 40°):  [ƯU TIÊN BEST-SHOT]
    │       ├── Khớp Gallery['frontal'] đạt (thr_frontal, margin_frontal)
    │       └── MiniFASNet mean ≥ live_thr (≥ 4 frame) & Context sạch
    │               └──► [CONFIRMED: tier = 'verified'] ──► Ghi Check-in + đẩy HRM
    │
    └─► Không đủ frame 'frontal', nhưng có ≥ min_profile_frames ở 'left_profile' / 'right_profile':
            ├── Đủ hành trình tối thiểu (≥ 6 frame, dịch chuyển ngang Δx ≥ 8% khung hình)
            ├── Khớp Gallery[profile_side] đạt (thr_profile, margin_profile)
            └── Đạt cả 3 cổng: Medium/Body sạch + 3D Motion Parallax hợp lệ + Không có vân Moiré
                    └──► [CONFIRMED: tier = 'profile_verified'] ──► Ghi Check-in (kèm cờ hậu kiểm) + đẩy HRM
```

---

## 5. Quy tắc Kiểm tra Tính hợp lệ (Validation Rules)

1. **Tách ngăn không trộn lẫn**: `agg_embedding(pose_group)` chỉ chuẩn hóa L2 tổng có trọng số của đúng `buckets[pose_group]`. Kiểm tra tách track (`split_cosine`) chỉ so sánh frame mới với `agg_embedding` của **cùng `pose_group`** khi ngăn đó đã có $\ge 2$ mẫu.
2. **Nâng hạng tự động trong cùng lượt đi**: Nếu một track vừa được nhận diện tạm ở mức `profile_verified` (nhưng chưa phát sự kiện vì đang chờ đi qua vạch `direction: line`) mà ngay sau đó người đó quay mặt thẳng đạt chuẩn `verified`, trạng thái của track tự động nâng cấp lên `verified`.
3. **Không lọt sự kiện khi lướt quá nhanh ở góc 90°**: Nếu đối tượng ở góc $> 40^\circ$ xuất hiện ít hơn `parallax_min_frames` (mặc định 6 frame) hoặc đứng bất động hoàn toàn không tạo ra quỹ đạo dịch chuyển ($\Delta x < \text{min\_travel\_ratio}$), trạng thái liveness giữ ở `pending` (khung màu cam *"Đang xác minh chuyển động 3D..."*), tuyệt đối không cấp `profile_verified`.
4. **Tự động Backfill Embedding khi nâng cấp `model_version` (`FR-009`, `SC-005`)**: Khi khởi động server (`lifespan`), nếu phát hiện nhân viên `active` hoặc `pending` chưa có bản ghi nào khớp `engine.model_version` hiện tại nhưng vẫn còn thư mục ảnh gốc `data/enroll/<code>/*.jpg`, hàm `backfill_legacy_embeddings(ctx)` tự động chạy `PoseEstimator3D` + căn chỉnh theo nhóm góc + trích xuất vector mới và lưu vào bảng `embeddings` (`source = 'backfill'`) trước khi nạp `PoseGallery`. Nhờ đó 100% nhân viên cũ hoạt động trơn tru ngay cả khi chuyển từ `opencv` (128-d) sang `edgeface` (512-d).
