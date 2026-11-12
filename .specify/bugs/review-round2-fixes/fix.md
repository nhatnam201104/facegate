# Bug Fix: Khắc phục các lỗi chống giả mạo, hậu kiểm đa góc và vòng đời backfill vòng 2 trên nhánh 90°

- **Slug**: review-round2-fixes
- **Fixed**: 2026-11-12
- **Assessment**: ./assessment.md
- **Status**: applied

## Summary

Đã triển khai trọn vẹn cả 4 cụm khắc phục (`#R2-1` đến `#R2-7`) theo hợp đồng tại `./assessment.md`: khóa chặt bất biến giữa ngăn nhận diện (`matched_group`) và nhánh chống giả mạo (`Liveness`), bắt chặt hành vi cầm điện thoại/tablet che trước mặt mình, bảo vệ Best-shot `frontal` và triệt tiêu dao động ping-pong trong `_reverify_current_bucket`, bảo vệ người thật đi thẳng chưa đổi góc đầu dưới phép chiếu trực giao `3DDFA_V2`, dọn sạch ảnh `.jpg` cũ khi `supplement_only`, và làm cho `backfill_legacy_embeddings` chạy nền bất biến (idempotent), an toàn đồng thời kèm `stop_event` khi tắt server. Toàn bộ bộ kiểm thử tăng từ `36 passed` lên **`42 passed`** (`0 failed`).

## Changes

| File | Change | Notes |
|------|--------|-------|
| `app/context_detector.py` | modified | Sửa điều kiện miễn trừ màn hình nền thành `background_wall_tv` (`m_label == "tv"`, cơ thể vượt ngoài TV và TV vượt ngoài bề ngang thân người), bắt chặt điện thoại/tablet cầm che ngay trước mặt mình (#R2-2) |
| `app/liveness.py` | modified | Giữ `"checking"` khi màn hình chập chờn (`screen_suspect`); chặn sớm khi `>= 2` mẫu `MiniFASNet < spoof_below`; khóa chặt định tuyến theo `matched_group` và không nâng nhầm `profile` lên `verified`; kiểm tra `yaw_span` và `medium_nearby` trước khi khóa `planar_no_parallax` (#R2-1, #R2-2, #R2-4) |
| `app/tracker.py` | modified | Ưu tiên `liveness_score` đã tổng hợp theo nhánh trong `Track.live_mean` để không rò điểm nháp của nhánh khác (#R2-1) |
| `app/pose3d.py` | modified | Làm rõ log cảnh báo khi thiếu bộ tệp `3DDFA_V2` rằng nhánh 5 điểm dự phòng không đủ lưới 68 điểm cho parallax góc `> 40°` (#R2-4, Mục 8) |
| `app/pipeline.py` | modified | Tái cấu trúc `_reverify_current_bucket` + `_split_mismatched_bucket`: giữ nguyên Best-shot `frontal` khi xoay đầu sang góc nghiêng yếu, tách track dứt điểm khi khớp sang đồng nghiệp khác (kể cả lúc `emp_sim is None` trước khi check-in) và loại bỏ vòng lặp ping-pong `greet` (#R2-3) |
| `app/enrollment.py` | modified | Xóa file `.jpg` cũ của nhóm góc được thay thế khi `supplement_only=True`, đánh chỉ số `max_prefix + 1`, giữ nguyên tên nhân viên đang `active`; bổ sung `backfill_failed_model` chống lặp backfill mỗi lần restart, kế thừa `crops` cũ, kiểm tra nguyên tử trong `db.lock` và hỗ trợ `stop_event` (#R2-5, #R2-6, #R2-7) |
| `app/guided.py` | modified | Cho phép truyền sẵn `verify_refs` vào `GuidedSession.__init__` để tránh truy vấn DB hai lần và dùng chung `DEFAULT_CROSS_POSE_COS = 0.20` (#R2-7) |
| `app/main.py` | modified | Điều phối dừng `backfill_legacy_embeddings` bằng `threading.Event` khi thoát `lifespan`; nạp `verify_refs` một lần và bọc `GuidedSession(...)` bằng `except EnrollError -> HTTP 409` (#R2-5, #R2-7) |
| `app/config.py` | modified | Đồng bộ đầy đủ `cross_pose_min_cosine` (`0.20`), `screen_face_overlap` (`0.80`), `screen_window` (`5`), `screen_min_hits` (`3`) vào `_apply_defaults` (#R2-7) |
| `tests/test_liveness_profile.py` | added tests | Thêm 2 bài kiểm thử hồi quy cho điện thoại che mặt + màn hình chập chờn và căn chỉnh nhánh `matched_group ↔ Liveness` (#R2-1, #R2-2) |
| `tests/test_tracker_buckets.py` | added test | Thêm bài kiểm thử hồi quy bảo vệ Best-shot khi xoay đầu, chống ping-pong `greet` và tách đồng nghiệp khác khi `emp_sim is None` (#R2-3) |
| `tests/test_pose3d.py` | added test | Thêm bài kiểm thử hồi quy người đi thẳng `Δyaw < 1°` dưới phép chiếu trực giao `3DDFA_V2` giữ trạng thái `"checking"` (#R2-4) |
| `tests/test_guided.py` | added test | Thêm bài kiểm thử hồi quy dọn ảnh `.jpg` cũ khi `supplement_only`, giữ tên nhân viên `active`, chống phình bảng `enrollments` khi backfill lỗi và tôn trọng `stop_event` (#R2-5, #R2-6) |
| `tests/test_api_contracts.py` | added test | Thêm bài kiểm thử hồi quy đồng bộ `_apply_defaults` và dịch `EnrollError` sang `HTTP 409` trong `enroll_guided_start` (#R2-7) |

## Diff Highlights (optional)

- **Phân biệt điện thoại/tablet cầm che mặt với TV treo tường phía sau (`app/context_detector.py`)**:
  ```python
  background_wall_tv = (
      coupled_person is not None
      and m_label == "tv"
      and _face_overlap_ratio(coupled_person, m_box) < 0.75
      and _face_overlap_ratio(m_box, coupled_person) < 0.70
  )
  if not background_wall_tv:
      return {..., "spoof_reason": "screen_detected", "medium_label": m_label}
  ```
- **Khóa chặt định tuyến chống giả mạo theo `matched_group` & bảo vệ chuyển động thẳng (`app/liveness.py`)**:
  ```python
  if len(track.live) >= 2 and float(np.mean(track.live)) < self.spoof_below:
      return self._mark_spoof(track, "texture_spoof", ...)
  ...
  if is_frontal_target:
      return "checking"
  ...
  if flat_z or yaw_span >= 3.0 or medium_near or len(track.pose_history) >= self.par_min_frames + 4:
      return self._mark_spoof(track, "planar_no_parallax", par_score, "context_parallax", ...)
  return "checking"
  ```
- **Hậu kiểm ngăn hiện tại không cắt nhầm Best-shot & không dao động ping-pong (`app/pipeline.py`)**:
  ```python
  if m.accepted and m.employee_id != t.employee_id:
      if emp_sim is not None or not t.event_done or m.score >= 0.95:
          return self._split_mismatched_bucket(t, f, now, pose_group, vec, pose)
      return t
  ```

## Tests Added or Updated

- `tests/test_liveness_profile.py::test_phone_held_over_own_face_detected_and_intermittent_screen_holds_checking` — xác nhận điện thoại che ngay trước mặt người cầm bị bắt `screen_detected` và màn hình chập chờn giữ `"checking"` (#R2-2).
- `tests/test_liveness_profile.py::test_matched_group_and_liveness_branch_alignment_blocks_frontal_spoof_leak_and_wrong_tier_upgrade` — xác nhận 2 frame ảnh giả chính diện (`FAS=0.12`) tiếp nối bởi 6 frame nghiêng bị chặn không lọt `checkin`, và track `left_profile` không bị nâng nhầm thành `verified` (#R2-1).
- `tests/test_tracker_buckets.py::test_reverify_preserves_best_shot_prevents_pingpong_and_splits_legacy_impostor` — xác nhận không cắt nhầm Best-shot khi xoay đầu, không lặp gói `greet` do ping-pong và tách đúng sang `NVB` khi nhân viên cũ `NVC` đang `checking` chưa có mẫu `profile` (#R2-3).
- `tests/test_pose3d.py::test_pure_translation_under_3ddfa_orthographic_projection_stays_checking_not_permanent_spoof` — xác nhận người thật đi ngang giữ nguyên góc đầu (`Δyaw = 0.4°`) dưới lưới trực giao `3DDFA_V2` giữ `"checking"`, không khóa chết `planar_no_parallax` (#R2-4).
- `tests/test_guided.py::test_supplement_cleans_old_profile_crops_keeps_active_name_and_backfill_idempotent_with_stop_event` — xác nhận xóa sạch file `.jpg` cũ trên đĩa khi chạy lại `supplement_only`, giữ tên nhân viên đang `active`, không phình bảng `enrollments` mỗi lần restart và dừng ngay theo `stop_event` (#R2-5, #R2-6).
- `tests/test_api_contracts.py::test_config_defaults_sync_and_guided_start_catches_enroll_error` — xác nhận `_apply_defaults` nạp đủ khóa mới và `enroll_guided_start` dịch `EnrollError` thành `HTTP 409` (#R2-7).

## Local Verification

- Commands run: `.venv/bin/python -m pytest -v` → `42 passed in 42.66s`
- Manual checks: Đối chiếu pha Red (`6 failed`) trước khi sửa code và pha Green (`42 passed, 0 failed`) sau khi áp dụng 4 cụm sửa đổi trên cùng bộ test.

## Deviations from Assessment

Không có sai lệch so với hợp đồng `./assessment.md`. Ở nhánh `emp_sim is None` của `_reverify_current_bucket` (`app/pipeline.py:498`), bổ sung điều kiện bảo vệ `not t.event_done or m.score >= 0.95` để vừa chặn triệt để việc ghép nhầm sang đồng nghiệp khác trước khi chấm công vừa giữ ổn định cho nhân viên 5 góc cũ sau khi đã hoàn tất check-in hợp lệ.

## Follow-ups

- Chạy `/speckit-bug-test slug=review-round2-fixes` để thực hiện kiểm tra xác nhận độc lập và sinh báo cáo nghiệm thu `test.md` / `test.html`.
- Khi lắp đặt trên camera cổng thực tế, chạy `python scripts/benchmark.py <footage.mp4> --profile-eval` để tinh chỉnh lần cuối `min_depth_score` và `cross_pose_min_cosine` theo điều kiện ánh sáng thực địa.
