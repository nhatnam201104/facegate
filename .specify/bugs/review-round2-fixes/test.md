# Bug Verification: Khắc phục các lỗi chống giả mạo, hậu kiểm đa góc và vòng đời backfill vòng 2 trên nhánh 90°

- **Slug**: review-round2-fixes
- **Tested**: 2026-11-12
- **Assessment**: ./assessment.md
- **Fix**: ./fix.md
- **Result**: verified

## Summary

Đã thực thi đầy đủ toàn bộ 5 kịch bản tái hiện lỗi gốc từ `./assessment.md` (bao gồm cả kịch bản chuyển tiếp 2 frame thẳng sạch sang 6 frame nghiêng và kịch bản đua đồng thời giữa thread `backfill_legacy_embeddings` với thao tác đăng ký lại của HR), chạy riêng 6 bài kiểm thử hồi quy mới và chạy toàn bộ 42 bài kiểm thử của dự án. Không còn bất kỳ triệu chứng nào trong 7 cụm lỗi (`#R2-1` đến `#R2-7`) còn tái hiện, không phát sinh lỗi hồi quy trên các module sẵn có và mã nguồn không bị chỉnh sửa trong quá trình kiểm định.

## Checks Performed

| Check | Command / Action | Result | Notes |
|-------|------------------|--------|-------|
| Reproduction #R2-1 (Branch alignment & Tier integrity) | Kịch bản E2E `Pipeline` + `Liveness` thật: (a) 2 frame ảnh giả `FAS=0.12` + 6 frame nghiêng; (b) 2 frame người thật `FAS=0.88` + 6 frame nghiêng; (c) khớp `left_profile` + 4 frame `frontal` yếu | pass | (a) Bị chặn sớm `texture_spoof` (`rejected_spoof`), `0` check-in; (b) Hạ bậc mượt sang `profile_verified` check-in không bị kẹt `checking`; (c) Giữ nguyên `profile_verified` |
| Reproduction #R2-2 (Phone over face & Flickering screen) | Kịch bản `evaluate_face_context` (điện thoại che mặt người cầm vs TV treo tường phía sau) + cửa sổ `screen_hits` chập chờn | pass | `phone_over_face = "screen_detected"`, `wall_tv = None`; màn hình chập chờn giữ `"checking"` không duyệt `"live"` sớm |
| Reproduction #R2-3 & #R2-4 (Re-verify Best-shot, Ping-pong & 3DDFA Walk) | Kiểm thử `Pipeline._reverify_current_bucket` và lưới trực giao `tddfa_u_base.bin` dịch ngang với `Δyaw = 0.4°` | pass | Giữ Best-shot `frontal` khi xoay đầu, `0` gói `greet` lặp do ping-pong, tách sang `NVB` khi `NVC` đang `checking`, và `Δyaw = 0.4°` giữ `"checking"` |
| Reproduction #R2-5, #R2-6, #R2-7 (Concurrency Race, Orphan Crops, Config & 409) | Thread `backfill_legacy_embeddings` bị chặn giữa chừng trong lúc HR chạy `store_enrollment` + lặp `supplement_only` 2 lần + `enroll_guided_start` TOCTOU | pass | `sources == ["enroll"]` (không chèn trùng `backfill`), xóa sạch ảnh `.jpg` cũ trên đĩa, giữ tên nhân viên `active`, không phình `enrollments` khi restart, trả đúng `HTTP 409` |
| New / updated tests (6 tests) | `.venv/bin/python -m pytest -v -k "test_phone_held_over_own_face or ..."` | pass | `6 passed` trong `0.64s` |
| Full regression suite (42 tests) | `.venv/bin/python -m pytest -q` | pass | `42 passed` trong `42.76s` (`0 failed`) |
| Lint / syntax check | `.venv/bin/python -m compileall -q app tests scripts` & `node --check app/static/*.js` | pass | `STATIC_SYNTAX_OK` cho toàn bộ tệp Python & JavaScript (`ruff`/`mypy` không cài trong `.venv`) |

## Output Excerpts

- **Kịch bản tái hiện hậu sửa lỗi & Đua đa luồng (Post-fix E2E & Concurrency Race)**:
  ```text
  Track 1: nghi gia mao (ly do=texture_spoof, live=0.12, giong NVA) -> khong cham cong
  ALL_ROUND2_REPRO_VERIFIED: {"repro_r2_1_spoof_blocked": "rejected_spoof", "repro_r2_1_brief_frontal_to_profile_checkin": "profile_verified", "repro_r2_2": {"phone_over_face": "screen_detected", "wall_tv": null}, "repro_r2_5_concurrent_enroll_sources": ["enroll"]}
  ```
- **Chạy riêng 6 bài kiểm thử hồi quy mới (`pytest -k`)**:
  ```text
  tests/test_api_contracts.py::test_config_defaults_sync_and_guided_start_catches_enroll_error PASSED [ 16%]
  tests/test_guided.py::test_supplement_cleans_old_profile_crops_keeps_active_name_and_backfill_idempotent_with_stop_event PASSED [ 33%]
  tests/test_liveness_profile.py::test_phone_held_over_own_face_detected_and_intermittent_screen_holds_checking PASSED [ 50%]
  tests/test_liveness_profile.py::test_matched_group_and_liveness_branch_alignment_blocks_frontal_spoof_leak_and_wrong_tier_upgrade PASSED [ 66%]
  tests/test_pose3d.py::test_pure_translation_under_3ddfa_orthographic_projection_stays_checking_not_permanent_spoof PASSED [ 83%]
  tests/test_tracker_buckets.py::test_reverify_preserves_best_shot_prevents_pingpong_and_splits_legacy_impostor PASSED [100%]
  6 passed, 36 deselected in 0.64s
  ```
- **Toàn bộ bộ kiểm thử dự án & Kiểm tra cú pháp tĩnh**:
  ```text
  ..........................................                               [100%]
  42 passed in 42.76s
  STATIC_SYNTAX_OK
  ```

## Residual Risks

- **Hiệu chỉnh ngưỡng thực địa trên camera cổng**: Hai ngưỡng `liveness.parallax.min_depth_score: 0.55` và `enrollment.cross_pose_min_cosine: 0.20` đã được xác minh trên mô hình ONNX thật (`3DDFA_V2`, `EdgeFace-XS`, `YOLOX-Nano`, `MiniFASNet`) và các quỹ đạo 3D, nhưng vẫn nên chạy `python scripts/benchmark.py <footage.mp4> --profile-eval` trên video thu thực tế tại vị trí lắp camera cổng trước khi vận hành chính thức.
- **Môi trường không gắn webcam vật lý**: Phiên kiểm định chạy trong môi trường máy chủ không cắm `/dev/video0` trực tiếp, do đó luồng thu hình thực tế được kiểm chứng thông qua pipeline xử lý khung hình và các mô hình ONNX thật.

## Recommendation

**Đóng hồ sơ lỗi (`Close the bug — verified end-to-end`)**: Toàn bộ 7 cụm lỗi hợp nhất ở vòng 2 (`review-round2-fixes`) đã được chứng minh biến mất hoàn toàn qua cả kịch bản tái hiện độc lập (bao gồm kiểm chứng đua đa luồng), 6 bài kiểm thử hồi quy mới và toàn bộ 42/42 bài kiểm thử của dự án mà không gây ra bất kỳ hồi quy nào. Bản sửa lỗi đủ điều kiện để commit lên nhánh `001-profile-90-antispoof`.
