# Bug Assessment: Sửa 10 lỗi từ code review trên nhánh nhận diện góc nghiêng 90° & chống giả mạo

- **Slug**: handoff-review-fixes
- **Created**: 2026-11-12
- **Source**: `specs/001-profile-90-antispoof/handoff-review-fixes.md` (biên bản bàn giao `/code-review` trên diff so với `main`)
- **Verdict**: valid
- **Severity**: critical

## Report (verbatim or summarized)

Biên bản bàn giao `specs/001-profile-90-antispoof/handoff-review-fixes.md` tổng hợp 10 lỗi từ `/code-review` trên nhánh `001-profile-90-antispoof` (HEAD `b4cf497`):

- **P0 (Nghiêm trọng / Bảo mật & Logic cốt lõi)**:
  - **#1**: Sự kiện `spoof` không bao giờ được ghi vào bảng `events` vì `Liveness._mark_spoof` gán `track.spoof = True` trước khi `Pipeline._update_track` kiểm tra `state == "spoof" and not t.spoof`, đồng thời `_render` cũng gọi `_live_state` gây hiệu ứng phụ trên track.
  - **#2**: Phiên `supplement_only` chỉ chạy 2 bước `PROFILE_STEPS`, không xác minh danh tính chính diện của nhân viên đang `active`, cho phép người khác ghi đè embedding góc nghiêng.
  - **#3**: Stored XSS và lỗi cú pháp JS ở tab Nhân viên (`app/static/app.js`) và tab Chấm công (`app/static/attendance.js`) khi chèn dữ liệu nhân viên vào chuỗi nháy đơn bên trong thuộc tính `onclick`.
  - **#9**: Thiếu kiểm tra cùng một người giữa các nhóm góc (`frontal` ↔ `left_profile` / `right_profile`) trong cả đăng ký bằng video (`process_enrollment`) và phiên có hướng dẫn (`GuidedSession._commit`), cộng thêm quy tắc giữ tự động nhóm có 1 frame với `sim_center = 1.0`.
- **P1 (Độ chính xác & Từ chối/Nhận nhầm)**:
  - **#4**: `moire_vals` lọc `> 0` và trả `0.0` khi không tính Moiré khiến frame sạch (`0.0`) bị bỏ qua, chỉ cần 1 frame nhiễu cao là làm lệch trung bình gây báo spoof nhầm.
  - **#5**: Sau khi track đã chốt danh tính ở `frontal`, `select_best_bucket` luôn trả về bucket `frontal` cũ nên nếu track ghép nhầm sang người khác ở góc nghiêng thì không bao giờ xác minh lại trên bucket góc hiện tại.
  - **#6**: `euler_from_relative_rotation` tách `pitch`/`roll` bằng `atan2(R21, R22)` và `atan2(-R10, R00)` làm `pitch`/`roll` bùng lên tới 58°–82° khi `yaw` tiến gần 90° và camera hơi nghiêng.
  - **#10**: `evaluate_face_context` báo `screen_detected` cho mọi thiết bị/sách nằm ≥35% trong vùng 2.5× quanh mặt hoặc chồng ≥20% lên mặt, kết hợp `screen_hits >= 2` tích lũy làm người thật cầm điện thoại ngang ngực hoặc đứng trước TV bị khóa `spoof` vĩnh viễn.
- **P2 (Chất lượng dữ liệu & Thiết kế chống giả mạo)**:
  - **#7**: `planar_parallax_score` cộng sẵn trọng số `0.35 * s_dep` từ độ sâu tái dựng của mô hình 3DDFA morphable (vốn luôn sinh độ sâu cho cả ảnh in 2D) và nhánh dự phòng 5 điểm gán cứng `z` khác 0.
  - **#8**: `backfill_legacy_embeddings` nhúng trực tiếp ảnh crop chưa căn chỉnh khi không phát hiện được mặt với chất lượng giả `0.75`, thiếu lọc nhất quán và chạy đồng bộ chặn `lifespan` khi khởi động server.

## Symptom

Hệ thống không ghi nhận bản ghi sự kiện `spoof` vào bảng `events`, tồn tại lỗ hổng mạo danh qua luồng đăng ký bổ sung góc nghiêng hoặc ghép frame khác người giữa các nhóm góc, lỗi Stored XSS/SyntaxError khi tên nhân viên chứa dấu nháy đơn, sai số góc `pitch`/`roll` lớn ở góc nghiêng ~85°–88°, báo giả mạo nhầm khi người thật có điểm Moiré nhiễu đơn lẻ hoặc cầm điện thoại gần ngực, không hậu kiểm danh tính khi chuyển sang góc nghiêng, cộng điểm độ sâu giả cho ảnh phẳng và làm bẩn gallery khi backfill ảnh lỗi.

## Reproduction

1. **#1**: Đưa trường hợp giả mạo qua `Liveness.evaluate_track` → `_mark_spoof` đặt `track.spoof = True` → `Pipeline._update_track` kiểm tra `if state == "spoof" and not t.spoof:` luôn `False` → bảng `events` không có bản ghi `kind="spoof"`.
2. **#2 & #9**: Gọi `POST /api/enroll/guided/start` với `supplement_only=true` cho mã nhân viên đang `active` bằng khuôn mặt người khác, hoặc tải video gồm các frame thẳng của người A và 1 frame nghiêng của người B → hệ thống chấp nhận lưu mẫu nghiêng của B vào hồ sơ A.
3. **#3**: Tạo nhân viên tên `O'Neil` hoặc payload chuỗi nháy đơn, mở tab Nhân viên và bấm `+ Bổ sung góc 90°` → trình duyệt ném `SyntaxError` hoặc thực thi mã JS chèn vào `onclick`.
4. **#4, #6, #7, #10**: Truyền chuỗi 15 frame Moiré `0.0` kèm 1 frame `0.7` (báo spoof nhầm); truyền `Rx(8°) @ Ry(85°)` vào `euler_from_relative_rotation` (`pitch` vọt lên `58.2°`); áp homography phẳng lên đám mây điểm 3D có `z != 0` (vẫn được cộng sẵn `0.35`); đặt hộp điện thoại ở vùng ngực dưới cằm trong 2 frame (bị khóa `screen_detected`).
5. **#5 & #8**: Cho track nhận diện nhân viên A bằng 2 frame thẳng rồi nối nhầm sang 4 frame nghiêng của nhân viên B (vẫn giữ danh tính A); đặt ảnh trắng vào `data/enroll/<code>/` rồi chạy `backfill_legacy_embeddings` (vẫn chèn vector rác vào DB).

## Suspected Code Paths

- `app/pipeline.py:348-366, 395-443, 459-476, 603-630` — điều kiện ghi sự kiện `spoof`, gọi `_live_state` trong `_render`, và xác minh lại danh tính theo bucket góc hiện tại trong `_identify` / `_update_track`.
- `app/tracker.py:45-155, 196-210` — các trường trạng thái `spoof_emitted`, `live_state` trên `Track` và điều kiện ghép `person_bbox` trong `IoUTracker._jump`.
- `app/static/app.js:320-405` & `app/static/attendance.js:37-57` & `app/main.py:300-358` — handler `onclick` nội suy chuỗi người dùng và hàm `_check` kiểm tra dữ liệu đầu vào.
- `app/pose3d.py:81-96, 134-205` — công thức phân rã góc Euler Y-X-Z và công thức `planar_parallax_score` / nhánh dự phòng 5 điểm.
- `app/liveness.py:128-265` — xử lý `moire_score=None`, trung vị 6 mẫu gần nhất (tối thiểu 4 mẫu), cửa sổ trượt `screen_hits` ≥ 3/5 frame gần nhất và xử lý khi thiếu dữ liệu thị sai 3D thật.
- `app/enrollment.py:119-264, 266-322`, `app/guided.py:124-338`, `app/main.py:47-87, 337-358`, `config.yaml:115-133` — bước xác minh chính diện cho `supplement_only`, kiểm tra chéo nhóm góc `cross_pose_min_cosine`, chuẩn hóa `backfill_legacy_embeddings` và chạy nền trong `lifespan`.
- `app/context_detector.py:86-152` — điều kiện hộp mặt nằm phần lớn trong màn hình (`>= 0.80`) và không gắn với cơ thể hợp lệ nằm ngoài thiết bị.

## Root Cause Hypothesis

Các lỗi phát sinh từ việc mở rộng nhanh pipeline từ mô hình 1 ngăn chính diện sang kiến trúc 3 ngăn góc nhìn (`frontal`, `left_profile`, `right_profile`) và chống giả mạo 4 lớp: trạng thái đột biến trên `Track` bị ghi sớm trong `Liveness._mark_spoof` và đọc chung ở luồng `_render`; kiểm tra nhất quán danh tính bị tách rời hoàn toàn theo từng `pose_group` mà thiếu cầu nối kiểm chứng với tâm `frontal`; điều kiện hình học/thống kê ở Moiré, Context, Parallax và Euler chưa xét hết các trường hợp biên thực tế. Độ tin cậy chẩn đoán: **high**.

## Proposed Remediation

**Preferred**: Thực hiện tuần tự theo đúng lộ trình ưu tiên của biên bản bàn giao (`#1` → `#3` → `#6` → `#4` → `#2 + #9` → `#5` → `#10` → `#8` → `#7`), mỗi mục/nhóm mục đi kèm kiểm thử hồi quy chứng minh lỗi trước khi sửa và xanh sau khi sửa, lưu thành từng commit độc lập trên nhánh `001-profile-90-antispoof`:

1. **#1**: Thêm `spoof_emitted: bool = False` và `live_state: str = "checking"` vào `Track`; cập nhật `t.live_state` trong `_update_track`, kiểm tra `if t.spoof and not t.spoof_emitted:` để ghi `_emit(..., kind="spoof")` đúng 1 lần; đổi `_render` sang đọc `t.live_state` đã cache thay vì gọi `_live_state`.
2. **#3**: Loại bỏ mọi `onclick="..."` nội suy dữ liệu động trong `app/static/app.js` và `app/static/attendance.js`, chuyển sang `data-*` kết hợp event delegation trên `#empBody`, `#evBody`, `#attBody`; giới hạn độ dài tên (≤ 120 ký tự) và loại bỏ ký tự điều khiển trong `_check` (`app/main.py`).
3. **#6**: Chuyển `euler_from_relative_rotation` sang phân rã chuẩn `R = Ry(yaw) · Rx(pitch) · Rz(roll)` để tách sạch độ nghiêng camera khi `yaw` tiến tới ±90°, giữ đúng quy ước dấu `pitch`/`roll` hiện hành.
4. **#4**: Trả `None` khi không đo Moiré, lọc `is not None` để giữ nguyên điểm `0.0` thật, tính `np.median` trên tối đa 6 mẫu gần nhất và yêu cầu tối thiểu 4 mẫu hợp lệ trước khi đánh rớt `moire_ok = False` (nếu chưa đủ 4 mẫu mà nghi ngờ cao thì giữ `"checking"`).
5. **#2 & #9**: Thêm khóa cấu hình `enrollment.cross_pose_min_cosine: 0.22` (ngưỡng thận trọng tạm thời cho vector thẳng ↔ nghiêng sâu đã căn chỉnh theo template riêng, có thể cấu hình lại khi có dữ liệu thực địa). Bắt buộc nhân viên phải có sẵn mẫu `frontal` mới mở được `supplement_only` (trả HTTP 409 nếu thiếu); bước 0 của `supplement_only` là bước nhìn thẳng xác minh danh tính khớp với gallery `frontal` của chính nhân viên đó (không lưu lặp mẫu bước 0), lấy làm `front_emb` để kiểm tra chéo các bước nghiêng; trong `store_enrollment`, nếu `supplement_only` phát sinh `warnings` thì hạ `status='pending'`. Trong `process_enrollment`, nếu có frame profile thì bắt buộc có frame frontal và lọc chéo với tâm frontal, bỏ quy tắc miễn kiểm tra cho nhóm chỉ có 1 frame.
6. **#5**: Giữ nguyên `select_best_bucket` cho lần định danh đầu tiên; khi track đã có `employee_id` và bucket của `pose.pose_group` hiện tại đủ số mẫu tối thiểu (`min_good` hoặc `min_profile`), đối chiếu vector tích lũy của bucket đó với gallery cùng `pose_group` (nếu nhân viên đã có mẫu ở nhóm góc đó) — nếu trượt dưới ngưỡng hoặc khớp sang nhân viên khác thì tách track / hủy danh tính cũ. Đồng thời siết nhánh `person_bbox` trong `IoUTracker._jump` để không nhảy vượt quá `JUMP_MAX * 1.25`.
7. **#10**: Đánh giá gắn kết cơ thể trước; chỉ gán `screen_detected` khi hộp mặt nằm phần lớn trong hộp thiết bị (`_face_overlap_ratio(face_box, m_box) >= 0.80`) và khuôn mặt không gắn hợp lệ với một thân người nằm ngoài thiết bị (hạ vùng mở rộng xuống cờ phụ `medium_nearby`, không tự kích hoạt spoof). Trong `evaluate_track`, yêu cầu `>= 3` lần `screen_detected` trong 5 frame gần nhất và giữ cố định trạng thái `spoof` cho hết track khi đã xác nhận.
8. **#8**: Trong `backfill_legacy_embeddings`, bỏ qua ảnh không phát hiện được mặt (ghi log cảnh báo), chấm điểm chất lượng thật qua `assess`, kiểm tra nhất quán trong nhóm + chéo nhóm trước khi lưu vào DB; chạy backfill trong luồng nền ở `lifespan` rồi gọi `ctx.gallery.reload()` và phát thông báo cập nhật khi hoàn tất.
9. **#7**: Loại bỏ hoàn toàn trọng số cộng điểm của `depth_ratio` khỏi `planar_parallax_score` (chỉ dùng sai số homography phẳng 2D `parallax_2d`, giữ `depth_ratio` làm điều kiện kiểm tra dữ liệu 3D hợp lệ); đánh dấu landmark từ nhánh dự phòng 5 điểm không có độ sâu thật để Nhánh B giữ trạng thái `"checking"` thay vì duyệt `"live"` giả tạo.

**Files likely to change**:
- `config.yaml`
- `app/tracker.py`
- `app/pipeline.py`
- `app/pose3d.py`
- `app/liveness.py`
- `app/context_detector.py`
- `app/enrollment.py`
- `app/guided.py`
- `app/main.py`
- `app/static/app.js`
- `app/static/attendance.js`
- `tests/test_api_contracts.py`
- `tests/test_pose3d.py`
- `tests/test_liveness_profile.py`
- `tests/test_guided.py`
- `tests/test_tracker_buckets.py`

**Tests to add or update**:
- Kiểm thử hồi quy ghi sự kiện `spoof` đúng 1 lần và không đột biến track từ `_render` (#1).
- Kiểm thử an toàn DOM / API cho tên nhân viên chứa dấu nháy đơn, payload XSS và ký tự điều khiển (#3).
- Kiểm thử tham số hóa `euler_from_relative_rotation` với `t ∈ {0, 8, 15}`, `y ∈ {0, 45, 70, 85, 88}` cho cả `Rx(t)·Ry(y)` và `Ry(y)·Rx(t)` (#6).
- Kiểm thử Moiré với 15 frame `0.0` + 1 frame `0.7` (không spoof) và 6 frame liên tiếp `>= 0.8` (bắt spoof) (#4).
- Kiểm thử `supplement_only` chặn mặt người lạ ở bước xác minh, trả 409 khi thiếu mẫu frontal, hạ `pending` khi có cảnh báo trùng mặt và chặn ghép frame khác người giữa các nhóm góc trong cả video lẫn guided (#2, #9).
- Kiểm thử hậu kiểm danh tính khi chuyển sang góc nghiêng của người khác so với người đúng (#5).
- Kiểm thử ngữ cảnh cầm điện thoại dưới cằm, đứng trước TV có thân người thật so với mặt lọt trọn trong màn hình điện thoại (#10).
- Kiểm thử `backfill_legacy_embeddings` bỏ qua ảnh trắng, lọc nhất quán và không chặn `lifespan` (#8).
- Kiểm thử `planar_parallax_score` chặn phép chiếu homography phẳng áp lên đám mây điểm 3D có `z != 0` và duyệt vật thể 3D xoay phối cảnh (#7).

## Risks & Considerations

- Trong `tests/test_guided.py`, `FakeEngine.embed_aligned` đang trả các vector giả lập có tích vô hướng dương nhẹ giữa góc thẳng và góc nghiêng (`~0.149`); cần đảm bảo vector giả lập của cùng một nhân viên trong test mô phỏng hợp lý độ tương đồng chéo góc ≥ `cross_pose_min_cosine`, trong khi vector của kẻ mạo danh nhỏ hơn ngưỡng này.
- Chưa có tập dữ liệu video thực tế trong `data/`, nên các ngưỡng `cross_pose_min_cosine` và `min_depth_score` sau khi đổi công thức được giữ ở mức thận trọng và cần ghi rõ trong báo cáo để hiệu chỉnh thêm khi thu thập video thực địa.

## Resolved Decisions (User Confirmed)

- **#2 (`supplement_only`)**: Chỉ chuyển trạng thái nhân viên về `pending` khi có `warnings` (ví dụ nghi trùng nhân viên khác); nếu xác minh khớp mặt chính diện và sạch cảnh báo thì giữ nguyên `active`.
- **#7 & #9 (Hiệu chỉnh ngưỡng)**: Thư mục `data/` hiện chưa có dữ liệu mẫu thực tế → sửa công thức chuẩn, đặt ngưỡng thận trọng mặc định trong `config.yaml` và ghi chú rõ trong báo cáo.
- **#10 (`screen_detected`)**: Giữ cố định trạng thái `spoof` cho hết track sau khi đã đủ bằng chứng (`>= 3/5` frame gần nhất với hộp mặt nằm phần lớn trong thiết bị và không gắn cơ thể hợp lệ).
