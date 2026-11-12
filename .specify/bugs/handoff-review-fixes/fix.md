# Bug Fix: Sửa 10 lỗi từ code review trên nhánh nhận diện góc nghiêng 90° & chống giả mạo

- **Slug**: handoff-review-fixes
- **Fixed**: 2026-11-12
- **Assessment**: ./assessment.md
- **Status**: applied

## Summary

Đã khắc phục đầy đủ cả 10 lỗi (4 lỗi P0 bảo mật/logic, 4 lỗi P1 độ chính xác/từ chối nhầm/nhận nhầm, 2 lỗi P2 chất lượng dữ liệu/thiết kế) theo biên bản bàn giao `specs/001-profile-90-antispoof/handoff-review-fixes.md`, bổ sung 10 bài kiểm thử hồi quy tương ứng (tăng tổng số bài kiểm thử từ `25 passed` lên `35 passed`) và tách thành 8 commit độc lập theo từng mục/nhóm mục trên nhánh `001-profile-90-antispoof`.

## Changes

| File | Change | Notes |
|------|--------|-------|
| `app/tracker.py` | modified | Thêm `spoof_emitted: bool = False` và `live_state: str = "checking"` vào `Track`; giới hạn nhánh `person_bbox` trong `IoUTracker._jump` (`d <= JUMP_MAX`) (#1, #5) |
| `app/pipeline.py` | modified | Ghi sự kiện `spoof` qua `if t.spoof and not t.spoof_emitted:`, đọc `t.live_state` trong `_render`, bổ sung `_reverify_current_bucket` hậu kiểm ngăn góc nhìn hiện tại (#1, #5) |
| `app/gallery.py` | modified | Thêm `Gallery.employee_similarity(employee_id, emb, pose_group)` hỗ trợ hậu kiểm danh tính cùng nhóm góc (#5) |
| `app/static/app.js` | modified | Xóa toàn bộ `onclick` nội suy dữ liệu người dùng ở `#empBody` và `#evBody`, chuyển sang `data-*` kèm event delegation (#3) |
| `app/static/attendance.js` | modified | Chuyển nút `Xem clip` sang `data-code` / `data-date` kèm event delegation trên `#attBody` (#3) |
| `app/main.py` | modified | Kiểm tra độ dài (≤120 ký tự) và chặn ký tự điều khiển trong `_check`; trả HTTP 409 khi `supplement_only` thiếu mẫu `frontal`; chạy `backfill_legacy_embeddings` trong task nền ở `lifespan` (#2, #3, #8) |
| `app/pose3d.py` | modified | Đổi `euler_from_relative_rotation` sang phân rã Euler Y-X-Z; bỏ trọng số `depth_ratio` khỏi `planar_parallax_score`, thêm `has_valid_parallax_landmarks` và đặt `z=0` cho nhánh 5 điểm dự phòng (#6, #7) |
| `app/liveness.py` | modified | Trả `None` khi không đo Moiré, giữ `0.0` thật, lấy `median` 6 mẫu gần nhất với tối thiểu 4 mẫu; cửa sổ trượt `>= 3/5` frame cho `screen_detected`; giữ `checking` khi thiếu lưới 68 điểm 3D thật (#4, #7, #10) |
| `app/context_detector.py` | modified | Kiểm tra gắn kết cơ thể trước, chỉ báo `screen_detected` khi hộp mặt lọt `>= 80%` trong thiết bị và không gắn với thân người nằm ngoài thiết bị; hạ vùng mở rộng thành cờ phụ `medium_nearby` (#10) |
| `app/guided.py` | modified | Thêm bước 0 `VERIFY_FRONT_STEP` (`verify_only=True`) xác minh danh tính chính diện cho `supplement_only`; kiểm tra `cross_pose_min_cosine` ở `_commit` cho các bước góc nghiêng (#2, #9) |
| `app/enrollment.py` | modified | Bổ sung `filter_consistent_candidates` kiểm tra nhất quán trong nhóm + chéo nhóm với tâm `frontal`; hạ `status='pending'` khi `supplement_only` có cảnh báo; chuẩn hóa `backfill_legacy_embeddings` bỏ ảnh không thấy mặt và chấm điểm chất lượng thật (#2, #8, #9) |
| `config.yaml` | modified | Thêm khóa cấu hình mặc định `enrollment.cross_pose_min_cosine: 0.22`, `liveness.context.screen_face_overlap: 0.80`, `screen_window: 5`, `screen_min_hits: 3`, và cập nhật chú thích `min_depth_score` (#7, #9, #10) |
| `tests/test_liveness_profile.py` | added tests | Thêm kiểm thử hồi quy cho mục #1, #4 và #10 |
| `tests/test_api_contracts.py` | added test | Thêm kiểm thử hồi quy chống inline `onclick` XSS và kiểm tra `_check` cho mục #3 |
| `tests/test_pose3d.py` | added tests | Thêm kiểm thử tham số hóa góc Euler Y-X-Z (#6) và homography phẳng có `z != 0` + nhánh 5 điểm dự phòng (#7) |
| `tests/test_guided.py` | added tests | Thêm kiểm thử hồi quy cho mục #2, #9 và #8 |
| `tests/test_tracker_buckets.py` | added test | Thêm kiểm thử hồi quy hậu kiểm ngăn góc nhìn hiện tại cho mục #5 |

##Commits Created

1. `8f5fdab` — `fix(#1): ghi su kien spoof vao bang events va tach luong render`
2. `86a0eaa` — `fix(#3): loai bo inline onclick tranh Stored XSS o tab Nhan vien`
3. `503d505` — `fix(#6): tach pitch va roll theo phan ra Euler Y-X-Z khi yaw lon`
4. `86679fb` — `fix(#4): giu mau diem 0 that va lay trung vi cho Moire`
5. `24f5933` — `fix(#2,#9): xac minh danh tinh supplement_only va kiem tra cheo nhom goc`
6. `ca0d011` — `fix(#5): xac minh lai danh tinh theo bucket goc hien tai va siet _jump`
7. `400f537` — `fix(#10): siet dieu kien phat hien man hinh tranh spoof vinh vien do vat gan mat`
8. `0c06862` — `fix(#8): chuan hoa backfill_legacy_embeddings va chay nen trong lifespan`
9. `ca11b42` — `fix(#7): loai bo trong so do sau gia khoi diem thi sai planar_parallax_score`
10. `a6591a3` — `fix(#9): doi chieu cheo goc trong cung he quy chieu frontal tranh ket buoc 7/7`

## Diff Highlights (optional)

- **Ghi nhận sự kiện giả mạo & không đột biến từ luồng render (`app/pipeline.py`)**:
  ```python
  state = self._live_state(t, frame.shape)
  t.live_state = state
  if state == "spoof":
      t.spoof = True
  if t.spoof and not t.spoof_emitted:
      t.spoof_emitted = True
      t.verification_tier = "rejected_spoof"
      self._emit(t, None, kind="spoof")
  ```
- **Phân rã Euler Y-X-Z ổn định khi `|yaw|` sát 90° (`app/pose3d.py`)**:
  ```python
  yaw = math.degrees(math.atan2(float(R_rel[0, 2]), float(R_rel[2, 2])))
  sin_p = max(-1.0, min(1.0, -float(R_rel[1, 2])))
  pitch = math.degrees(math.asin(sin_p))
  roll = math.degrees(math.atan2(-float(R_rel[1, 0]), float(R_rel[1, 1]))) if abs(sin_p) < 0.9999 else 0.0
  ```
- **Điểm thị sai thuần phần dư homography 2D (`app/pose3d.py`)**:
  ```python
  parallax_2d = float(np.max(rel_residuals))
  s_par = min(1.0, parallax_2d / 0.012)
  return float(round(s_par, 4))
  ```

## Tests Added or Updated

- `tests/test_liveness_profile.py::test_pipeline_emits_spoof_event_once_and_render_does_not_mutate_track` — ghim chặt việc ghi đúng 1 sự kiện `spoof` khi `Liveness._mark_spoof` chạy thật và đảm bảo `_render` không gọi lại `evaluate_track` (#1).
- `tests/test_api_contracts.py::test_employee_tab_no_inline_onclick_xss_and_name_validation` — ghim chặt không còn biểu thức nội suy `${...}` trong `onclick="..."` trên `app.js`/`attendance.js` và kiểm tra `_check` (#3).
- `tests/test_pose3d.py::test_euler_pitch_roll_stability_under_camera_tilt_and_large_yaw` — kiểm thử tham số hóa `tilt ∈ {0, 8, 15}`, `yaw ∈ ±{0, 45, 70, 85, 88}` cho cả `Rx·Ry` và `Ry·Rx` (#6).
- `tests/test_liveness_profile.py::test_moire_keeps_true_zeros_and_uses_median_against_single_outlier` — kiểm chứng 15 frame `0.0` + 1 frame `0.70` không bị báo spoof, dưới 4 mẫu giữ `checking`, và 6 frame `>= 0.80` bắt đúng `moire_pattern` (#4).
- `tests/test_guided.py::test_supplement_only_verifies_frontal_identity_and_409_when_missing` — kiểm chứng HTTP 409 khi thiếu mẫu frontal, chặn mặt người B ở bước 0 hoặc bước nghiêng, giữ `active` khi sạch và hạ `pending` khi có cảnh báo trùng mặt (#2, #9).
- `tests/test_guided.py::test_video_enrollment_enforces_cross_pose_consistency` — kiểm chứng lọc chéo `frontal ↔ profile` (kể cả nhóm 1 frame) và ném `EnrollError` khi tỉ lệ ngoại lai vượt 35% (#9).
- `tests/test_tracker_buckets.py::test_reverification_on_current_pose_bucket_splits_when_profile_mismatches` — kiểm chứng tách track / hủy danh tính cũ khi chuyển sang góc nghiêng không khớp nhân viên đã chốt (#5).
- `tests/test_liveness_profile.py::test_normal_objects_near_face_or_tv_behind_person_do_not_trigger_permanent_spoof` — kiểm chứng điện thoại ngang ngực và TV treo tường phía sau người thật không gây spoof, trong khi màn hình bao trọn mặt không gắn cơ thể với `>= 3/5` frame bị chặn (#10).
- `tests/test_guided.py::test_backfill_skips_no_face_validates_consistency_and_runs_in_background` — kiểm chứng bỏ qua ảnh trắng không mặt, chấm điểm chất lượng thật, từ chối nhân viên có mẫu không nhất quán và `lifespan` không bị chặn (#8).
- `tests/test_pose3d.py::test_planar_homography_with_nonzero_3ddfa_z_still_scores_below_spoof_threshold` — kiểm chứng phép biến đổi homography phẳng dù có `z != 0` từ 3DDFA vẫn dưới ngưỡng spoof và nhánh 5 điểm dự phòng trả `checking` (#7).

## Local Verification

- Commands run: `.venv/bin/python -m pytest -q` → `35 passed in 32.78s`
- Commands run: `node --check app/static/app.js && node --check app/static/attendance.js` → exit code `0`
- Manual / Playwright E2E checks: Khởi chạy FastAPI trên DB cô lập với nhân viên `Sean O'Neil` và `x');window.__xss_fired=1;('`, mở tab Nhân viên trên trình duyệt Chromium, bấm `+ Bổ sung góc 90°` và `Xóa` → `xssFired = 0`, console `0 errors / 0 warnings`, ô họ tên ở form bổ sung điền nguyên vẹn chuỗi gốc.

## Deviations from Assessment

Không có sai lệch so với hợp đồng trong `./assessment.md`. Toàn bộ các quyết định sản phẩm (`#2`, `#7`, `#9`, `#10`) đã được người dùng xác nhận tường minh trước khi triển khai:
- **#2**: Giữ `status='active'` cho phiên `supplement_only` đã xác minh khớp mặt chính diện và không có cảnh báo; chỉ hạ về `pending` khi có `warnings`.
- **#7 & #9**: Do thư mục `data/` hiện chưa có dữ liệu mẫu thực tế, ngưỡng `enrollment.cross_pose_min_cosine` được đặt tạm thời ở mức thận trọng `0.22` (cho vector EdgeFace căn chỉnh khác template giữa `frontal` và `profile` ~80°), và `liveness.parallax.min_depth_score` giữ ở `0.55` trên thang điểm phần dư homography 2D mới (chưa hiệu chỉnh lại trên tập video thực địa).
- **#10**: Không tự gỡ cờ `spoof` trong cùng một track sau khi đã đạt ngưỡng `>= 3/5` frame `screen_detected`; giới hạn ca khó (mặt nằm trọn trong màn hình TV khổng lồ và cơ thể người đứng trước cũng nằm gọn >75% bên trong khung TV đó) được ghi nhận rõ để lưu ý khi lắp đặt góc camera cổng.

## Follow-ups

- Thu thập tập video thực tế tại cổng (nhân viên đi ngang 45°–90°, cầm điện thoại ngang ngực, tấn công bằng ảnh in A4 và màn hình điện thoại/tablet) để đo phân bố thực nghiệm và tinh chỉnh hai ngưỡng `enrollment.cross_pose_min_cosine` cùng `liveness.parallax.min_depth_score`.
- Chạy bước xác nhận độc lập của SpecKit bằng lệnh `/speckit-bug-test slug=handoff-review-fixes`.
