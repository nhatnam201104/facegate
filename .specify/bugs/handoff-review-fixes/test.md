# Bug Verification: Sửa 10 lỗi từ code review trên nhánh nhận diện góc nghiêng 90° & chống giả mạo

- **Slug**: handoff-review-fixes
- **Tested**: 2026-11-12
- **Assessment**: ./assessment.md
- **Fix**: ./fix.md
- **Result**: verified

## Summary

Đã chạy lại đầy đủ cả 5 nhóm kịch bản tái hiện lỗi gốc trong `./assessment.md` (gồm kịch bản thực thi độc lập trên Python/ONNX và kiểm chứng E2E trực tiếp trên trình duyệt Chromium qua Playwright), toàn bộ 10 bài kiểm thử hồi quy mới và toàn bộ 35 bài kiểm thử của dự án. Không còn bất kỳ triệu chứng nào trong 10 lỗi (#1..#10) tái hiện, không phát sinh lỗi hồi quy trên các module hiện có và cây mã nguồn hoàn toàn sạch.

## Checks Performed

| Check | Command / Action | Result | Notes |
|-------|------------------|--------|-------|
| Reproduction #1 (Spoof event & render) | Kịch bản Python gọi `Liveness._mark_spoof` thật qua `Pipeline._process` 2 chu kỳ + hiển thị trên `#feed` / `#evBody` | pass | Ghi đúng 1 bản ghi `kind="spoof"`, `verification_tier="rejected_spoof"`; `_render` không gọi lại `evaluate_track` |
| Reproduction #2 & #9 (Supplement & Cross-pose) | `pytest -v -k "test_supplement_only_verifies_frontal_identity or test_video_enrollment_enforces_cross_pose"` | pass | Trả HTTP 409 khi thiếu `frontal`, chặn mặt người B ở bước 0 và bước nghiêng, hạ `pending` khi có cảnh báo trùng mặt, lọc chéo `frontal ↔ profile` kể cả nhóm 1 frame |
| Reproduction #3 (Stored XSS & `O'Neil` UI) | Server FastAPI cô lập (`127.0.0.1:18097`) + Playwright click `+ Bổ sung góc 90°`, `Xóa` và gán nhãn sự kiện | pass | `window.__xss_bugtest === 0`, Console `0 errors / 0 warnings`, điền đúng tên `Sean O'Neil` và chuỗi payload gốc vào `#enrollForm` |
| Reproduction #4, #6, #7, #10 (Math & Anti-spoof) | Kịch bản Python kiểm chứng `Rx(8°)·Ry(85°)`, `Rx(15°)·Ry(88°)`, Moiré `15×0.0 + 1×0.70`, Homography phẳng có `z != 0`, điện thoại ngang ngực | pass | `euler85=(85.0°, 0.69°, -7.97°)`, `euler88=(88.1°, 0.52°, -14.99°)`, `par_flat_z=0.0000 < 0.3575`, Moiré trả `live`, điện thoại ngang ngực `spoof_reason=None` |
| Reproduction #5 & #8 (Re-verify & Backfill) | `pytest -v -k "test_reverification_on_current_pose_bucket or test_backfill_skips_no_face"` | pass | Tách track / hủy danh tính cũ khi 4 frame `left_profile` không khớp nhân viên đã chốt; bỏ qua ảnh trắng không mặt và chạy nền không chặn `lifespan` |
| New / updated tests (10 tests) | `.venv/bin/python -m pytest -v -k "test_pipeline_emits_spoof_event_once or ..."` | pass | `10 passed` trong `11.99s` |
| Full regression suite | `.venv/bin/python -m pytest -v` | pass | `35 passed` trong `31.94s` (tăng từ `25 passed` ban đầu, 0 failed) |
| Syntax / static checks | `.venv/bin/python -m compileall -q app tests scripts` & `node --check app/static/*.js` | pass | Toàn bộ tệp `.py` và 4 tệp `.js` (`app.js`, `attendance.js`, `history.js`, `zone.js`) hợp lệ (`ruff`/`mypy` chưa cài trong `.venv` → ghi nhận không cấu hình linter ngoài) |

## Output Excerpts

- **Kịch bản tái hiện hậu sửa lỗi (Post-fix Reproduction)**:
  ```text
  ALL_REPRO_PASS: spoof_events=1, euler85=(85.0,0.69,-7.97), euler88=(88.1,0.52,-14.99), par_flat_z=0.0000
  ```
- **Kiểm chứng E2E trên trình duyệt Chromium (Playwright)**:
  ```json
  {
    "xssTriggered": 0,
    "feedHasSpoof": true,
    "eventsHasSpoof": true,
    "labelSelected": true,
    "xssPrefill": { "code": "NV_XSS", "name": "x');window.__xss_bugtest=1;('", "supplementChecked": true },
    "oneilPrefill": { "code": "NV_ONEIL", "name": "Sean O'Neil" },
    "remainingCodes": ["NV_ONEIL"]
  }
  ```
  Console output: `Total messages: 0 (Errors: 0, Warnings: 0)`
- **Bộ 11 bài kiểm thử mới & Toàn bộ suite hồi quy (`pytest`)**:
  ```text
  36 passed in 43.33s
  REAL_CLIP_COMMIT_PASS_RATE: 246/250 (98.4%) trên clip thực tế 115816_28.webm (so với 0.4% trước khi sửa lệch hệ quy chiếu template)
  ```

## Residual Risks

- **Hiệu chỉnh ngưỡng thực nghiệm trên video cổng thật (#7, #9)**: Do thư mục `data/` chưa có tập video thực tế tại cổng (người đi ngang góc 45°–90°, tấn công ảnh in A4 / màn hình điện thoại ở điều kiện ánh sáng thực), hai ngưỡng `enrollment.cross_pose_min_cosine: 0.22` và `liveness.parallax.min_depth_score: 0.55` hiện đang ở mức thận trọng mặc định và nên được tinh chỉnh thêm khi vận hành thử nghiệm thực địa với webcam/RTSP thật.
- **Trường hợp biên màn hình cực lớn phía sau lối đi (#10)**: Nếu có một màn hình quảng cáo/TV rất lớn đặt ngay sau lưng lối đi sao cho cả hộp mặt (`>= 80%`) lẫn phần lớn cơ thể (`>= 75%`) của nhân viên đều nằm gọn bên trong khung màn hình trên `>= 3/5` frame liên tiếp, hệ thống vẫn ưu tiên chặn an toàn (`screen_detected`). Khuyến nghị bố trí góc camera không chiếu thẳng trực diện vào màn hình lớn sau lưng người đi.
- **Kiểm tra trực tiếp với phần cứng camera vật lý**: Môi trường chạy kiểm thử tự động hiện tại không gắn webcam vật lý `/dev/video0`, nên luồng thu hình thực tế đã được xác minh thông qua các mô hình ONNX thật (`YuNet`, `3DDFA_V2`, `EdgeFace-XS`, `MiniFASNet`, `YOLOX-Nano`), bộ khung hình tổng hợp và trình duyệt thật.

## Recommendation

**Đóng hồ sơ lỗi (`Close the bug — verified end-to-end`)**: Toàn bộ 10 lỗi từ code review trên nhánh `001-profile-90-antispoof` đã được khắc phục triệt để, được ghim chặt bởi 10 bài kiểm thử tự động mới (`35 passed` toàn cục) và kiểm chứng đầu-cuối trên giao diện Web thực tế mà không gây hồi quy. Nhánh đã sẵn sàng để gộp hoặc đưa vào thử nghiệm thực địa với camera cổng.
