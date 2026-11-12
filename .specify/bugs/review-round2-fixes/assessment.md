# Bug Assessment: Khắc phục các lỗi chống giả mạo, hậu kiểm đa góc và vòng đời backfill vòng 2 trên nhánh 90°

- **Slug**: review-round2-fixes
- **Created**: 2026-11-12
- **Source**: pasted text (Hợp nhất kết quả `/codereview` và bản đối chiếu phản biện 10 mục trên nhánh `001-profile-90-antispoof` tại `HEAD a6591a3`)
- **Verdict**: valid
- **Severity**: critical

## Report (verbatim or summarized)

Tổng hợp và khử trùng lặp từ hai nguồn đánh giá trên `HEAD a6591a3` (sau 10 commit sửa lỗi vòng 1 từ `b4cf497`):

1. **Nhóm P0 — Biên an ninh Chống giả mạo & Toàn vẹn phân tầng (`liveness.py`, `context_detector.py`, `tracker.py`)**:
   - **#R2-1 (`app/liveness.py:196-222, 290-291`, `app/tracker.py:103-106`, `app/pipeline.py:424`)**: Lệch pha giữa ngăn chốt danh tính (`track.matched_group`) và nhánh chống giả mạo trong `Liveness.evaluate_track`:
     - Chiều 1: Kẻ gian đưa ảnh giả chính diện trong 2–3 frame (`min_good_frames=2 <= frames < min_frames=4`, điểm `MiniFASNet` thấp `0.12 < spoof_below=0.35` nên chốt `matched_group="frontal"`, `verification_tier="verified"`), sau đó xoay/di chuyển sang góc nghiêng `> 40°` trong ≥ 4–6 frame → `evaluate_track` rơi xuống Nhánh B (`context_parallax`), bỏ qua các mẫu `track.live=[0.12, 0.12]` và phát sự kiện `checkin` với `verification_tier="verified"`, `liveness=0.12`.
     - Chiều 2: Track chốt danh tính ở `matched_group="left_profile"` (`profile_verified`), sau đó xoay về `frontal` đủ 4 frame (`len(track.live) >= 4`) nhưng cosine `frontal` nằm trong `[threshold - VERIFY_SLACK, threshold)` nên `_identify` chưa chấp nhận nâng cấp sang `frontal`; tuy nhiên Nhánh A (`app/liveness.py:213-214`) vẫn ép `track.verification_tier = "verified"`, làm sai lệch phân tầng hậu kiểm HR.
   - **#R2-2 (`app/context_detector.py:144-156`, `app/liveness.py:179-183, 198-216`)**: Bỏ sót spoof màn hình khi kẻ tấn công cầm điện thoại/tablet che ngay trước mặt mình và khi YOLOX phát hiện màn hình chập chờn:
     - Mặt hiển thị trên màn hình nằm đúng vùng đầu–vai của người cầm nên `_anatomical_head_match` trả `True` (`coupled_person` được gán). Vì `_face_overlap_ratio(coupled_person, m_box)` chia phần giao cho diện tích toàn thân người cầm (`~0.25 < 0.75`), biến `body_outside_medium` luôn `True` khiến `screen_detected` bị vô hiệu hóa hoàn toàn (đặc biệt nguy hiểm ở góc `> 40°` nơi `MiniFASNet` không chạy).
     - Khi YOLOX phát hiện màn hình chập chờn `1–2/5` frame (`1 <= screen_hits < screen_min_hits`), Nhánh A vẫn gán cứng `medium_clean=True` và duyệt `"live"` ngay thay vì giữ `"checking"`.

2. **Nhóm P1 — Hậu kiểm danh tính Tracker & Thị sai 3DDFA (`pipeline.py`, `pose3d.py`, `liveness.py`)**:
   - **#R2-3 (`app/pipeline.py:454-484`)**: Ba lỗi liên hoàn trong `_reverify_current_bucket` và `_identify` khi người đi qua xoay đầu giữa `frontal` và `profile`:
     - **(a) Cắt nhầm track hợp lệ (phá vỡ Best-shot)**: Nhân viên đã được nhận diện ở `frontal` (liveness đang `checking`), khi quay đầu sang góc nghiêng sâu có cosine thấp hơn `profile_threshold - VERIFY_SLACK` với mẫu của chính họ thì bị gọi `_on_track_end` + `tracker.split`, làm đứt chuỗi tích lũy `live` / `pose_history` và mất hoặc trễ check-in.
     - **(b) Nhảy danh tính qua lại (ping-pong)**: Khi `t.matched_group == "frontal"` (nhân viên A), ở frame `profile` vector vừa đủ `>= profile_threshold - VERIFY_SLACK` với A nhưng lại khớp `accepted` với nhân viên B trong `_identify` → gọi `t.reset_identity()` và `t.event_done = False` mà vẫn giữ nguyên `t.buckets["frontal"]` của A; sang frame kế tiếp `select_best_bucket` chọn lại `frontal` của A và bắn lặp sự kiện / lời chào `greet` liên tục mỗi 2 frame.
     - **(c) Bỏ qua kiểm tra mở khi `emp_sim is None`**: Nhân viên cũ 5 góc (chưa có mẫu `profile`) đang ở `checking`, bị ghép nhầm sang hành trình góc nghiêng của nhân viên B (có mẫu `profile`); vì `emp_sim is None` nên `return t` sớm, bỏ qua `_identify`, dẫn đến ghi check-in nhầm cho nhân viên cũ.
   - **#R2-4 (`app/pose3d.py:208, 271-284`, `app/liveness.py:267-269`)**: `PoseEstimator3D` tái dựng 68 điểm mốc bằng phép chiếu trực giao cục bộ (`R_mat @ verts + offset`) trên từng crop độc lập. Nếu người thật đi bộ ngang qua camera nhưng giữ đầu gần như không đổi góc tương đối so với ống kính (`Δyaw < 1.0°`), hai lưới điểm 2D giữa frame đầu và cuối chỉ lệch phép tịnh tiến + co giãn phẳng (`parallax_2d < 0.0043`, `score < 0.3575`), khiến `evaluate_track` gọi `_mark_spoof("planar_no_parallax")` khóa spoof vĩnh viễn cho cả track.

3. **Nhóm P1/P2 — Vòng đời dữ liệu Enrollment, Backfill chạy nền & Vệ sinh cấu hình (`enrollment.py`, `main.py`, `guided.py`, `config.py`)**:
   - **#R2-5 (`app/enrollment.py:349-445`, `app/main.py:81-96`)**: Vòng đời `backfill_legacy_embeddings`:
     - Nhân viên backfill thất bại vẫn có `n_emb == 0`, nên mỗi lần khởi động lại server đều chạy lại `detect`/`pose`/`embed` dưới `engine_lock` và gọi `_record_backfill_warning` chèn thêm dòng stub (`crops: []`) vào bảng `enrollments` không giới hạn, che khuất báo cáo gốc (`ORDER BY id DESC LIMIT 1`) trên UI.
     - Tác vụ chạy nền dùng snapshot `missing_emps` cũ chụp lúc khởi động nên có thể chèn trùng embedding `source='backfill'` hoặc văng `FOREIGN KEY constraint failed` nếu HR đăng ký lại/xóa nhân viên trong lúc backfill đang chạy; đồng thời `backfill_task.cancel()` lúc shutdown không dừng được worker thread đồng bộ đang giữ `ctx.db` và `ctx.engine`.
   - **#R2-6 (`app/enrollment.py:259-286`)**: `store_enrollment(..., supplement_only=True)` xóa embedding góc nghiêng cũ trong DB nhưng không xóa file `.jpg` tương ứng trên đĩa và đặt tên theo `len(glob("*.jpg"))`, để lại ảnh mồ côi làm bẩn các lần `backfill_legacy_embeddings` về sau; ngoài ra còn cập nhật trực tiếp `employees.name` của nhân viên đang `active` không qua duyệt HR.
   - **#R2-7 (`app/main.py:369-383`, `app/config.py:43-68`, `app/context_detector.py:137-143`, `app/pose3d.py:246`)**: Các điểm hoàn thiện phòng vệ & vệ sinh code:
     - `enroll_guided_start` truy vấn `load_employee_frontal_embeddings` hai lần và chưa bọc `GuidedSession(...)` bằng `except EnrollError` (khe hở TOCTOU hẹp có thể trả `HTTP 500` thay vì `409`).
     - Giá trị mặc định `cross_pose_min_cosine` trong code (`0.22` tại `enrollment.py`, `guided.py`) lệch với `config.yaml` (`0.20`) và chưa khai báo tập trung cùng `screen_face_overlap`, `screen_window`, `screen_min_hits` trong `app/config.py:_apply_defaults`; cờ `medium_nearby` tính nhưng chưa được nối vào cổng quyết định của `Liveness`; cần cảnh báo rõ khi bật `liveness.parallax.enabled=True` mà thiếu model `3DDFA_V2` (mục #8 trong bản đối chiếu đã được phản biện không phải lỗi fallback theo từng frame khi có ONNX, chỉ bổ sung log cảnh báo khởi tạo).

## Symptom

1. Ảnh giả 2D xuất hiện thoáng qua 2–3 frame chính diện rồi xoay nghiêng có thể vượt qua chống giả mạo và được ghi `checkin` hạng `verified` với `liveness=0.12`, hoặc kẻ gian cầm điện thoại/tablet che đúng vị trí mặt mình không bị gắn cờ `screen_detected` ở góc nghiêng.
2. Nhân viên hợp lệ đã khớp chính diện khi quay đầu đi qua cổng có thể bị cắt track làm trễ/mất check-in hoặc bị nhảy danh tính qua lại liên tục (`NVA ↔ None`) gây bắn lặp lời chào `greet`; ngược lại nhân viên cũ chưa có mẫu nghiêng có thể bị giữ nguyên danh tính khi track ghép nhầm sang đồng nghiệp khác ở góc nghiêng, và người thật giữ góc mặt cố định không xoay tương đối với camera có nguy cơ bị khóa `planar_no_parallax`.
3. Thư mục `data/enroll/<code>/` tích lũy ảnh `.jpg` mồ côi sau nhiều lần bổ sung góc 90°, bảng `enrollments` phình thêm dòng cảnh báo rỗng mỗi lần restart server với nhân viên ảnh lỗi, và luồng backfill nền có khe hở tranh chấp dữ liệu khi HR thao tác đồng thời hoặc khi tắt server.

## Reproduction

1. **Tái hiện #R2-1**: Khởi tạo `Pipeline` với `Liveness` thật (`min_good_frames=2`, `min_frames=4`, `real_prob=0.12`), đẩy 2 frame chính diện của nhân viên `NVA` tiếp nối bởi 6 frame góc nghiêng `-78°` có quỹ đạo 3D → `db.list_events()` ghi nhận `('checkin', 'NVA', 'frontal', 'verified', 0.12, 'context_parallax')`.
2. **Tái hiện #R2-2**: Gọi `evaluate_face_context` với `person_box=[150, 90, 370, 470]`, `phone_box=[185, 95, 325, 255]` (điện thoại che mặt người cầm) và `face_box=[215, 115, 295, 215]` → `_face_overlap_ratio(person_box, phone_box) = 0.268 < 0.75` nên trả `spoof_reason=None` thay vì `"screen_detected"`.
3. **Tái hiện #R2-3 (ping-pong & legacy swap)**:
   - Cho `NVA` check-in bằng 2 frame `frontal`, sau đó truyền 7 frame `left_profile` có cosine `>= 0.41` với `NVA` nhưng khớp `accepted` với `NVB` → trạng thái track dao động `[('NVA', True), (None, False), ('NVA', True), ...]` và phát lặp gói WebSocket `greet`.
   - Cho nhân viên `NVC` (chỉ có mẫu `frontal`) xuất hiện 2 frame khi liveness đang `checking`, rồi truyền 4 frame `left_profile` của `NVB` → `_reverify_current_bucket` trả sớm do `emp_sim is None` và phát sự kiện `checkin` mang mã `NVC`.
4. **Tái hiện #R2-4**: Dựng chuỗi 6 frame từ mesh `models/tddfa_u_base.bin` qua phép chiếu trực giao cục bộ `R_mat @ verts + offset` dịch ngang 100 px với `Δyaw = 0.0°` → `planar_parallax_score` trả `0.0 < 0.3575` → `evaluate_track` khóa chết `spoof="planar_no_parallax"`.
5. **Tái hiện #R2-5 & #R2-6**: Tạo nhân viên có 1 ảnh trắng trong `data/enroll/NV_ERR/` và gọi `backfill_legacy_embeddings(ctx)` 2 lần liên tiếp → bảng `enrollments` sinh 2 dòng stub `crops: []` che mất báo cáo cũ; gọi `store_enrollment(..., supplement_only=True)` 2 lần trên `NV01` → thư mục vẫn giữ file `01_nghieng-sau-trai.jpg` đã bị xóa khỏi bảng `embeddings`.

## Suspected Code Paths

- `app/liveness.py:172-295` — Logic cổng `screen_hits`, điều kiện chuyển từ Nhánh A sang Nhánh B khi `track.matched_group == "frontal"`, gán `verification_tier = "verified"` không kiểm tra `matched_group`, và khóa vĩnh viễn `planar_no_parallax`.
- `app/context_detector.py:137-156` — Công thức `body_outside_medium` dùng `_face_overlap_ratio(coupled_person, m_box)` bị đảo mẫu số diện tích khi thiết bị nhỏ nằm gọn trong khung thân người cầm.
- `app/tracker.py:102-106` — `Track.live_mean` luôn ưu tiên `np.mean(self.live)` kể cả khi quyết định liveness đến từ `liveness_score` của Nhánh B.
- `app/pipeline.py:354-360, 420-485` — `_reverify_current_bucket` cắt nhầm track đã chốt Best-shot `frontal`, bỏ qua so khớp mở khi `emp_sim is None`, và `_identify` gọi `reset_identity()` gây dao động ping-pong khi vẫn giữ `buckets["frontal"]`.
- `app/pose3d.py:173-210, 242-247, 271-285` — Tương tác giữa phép chiếu trực giao cục bộ của `3DDFA_V2` và ngưỡng sai số homography 2D trong `planar_parallax_score`.
- `app/enrollment.py:183-188, 255-287, 320-451` — Xóa thiếu file `.jpg` cũ và ghi đè tên nhân viên `active` khi `supplement_only=True`; lặp vô hạn cảnh báo backfill mỗi lần khởi động và thiếu kiểm tra trạng thái mới nhất trước khi ghi DB.
- `app/main.py:81-96, 369-384`, `app/guided.py:140-156`, `app/config.py:26-68` — Quản lý vòng đời dừng thread backfill bằng `threading.Event`, bắt `EnrollError` tránh trả `500`, và chuẩn hóa các khóa cấu hình mặc định.

## Root Cause Hypothesis

Sau vòng sửa lỗi đầu tiên (`b4cf497..a6591a3`), từng cơ chế đơn lẻ (xác minh bổ sung, Euler Y-X-Z, trung vị Moiré, đối chiếu chéo hệ quy chiếu `frontal`) đã hoạt động đúng trong phạm vi cục bộ của nó, nhưng xuất hiện sự **lệch bất biến trạng thái giữa các module ghép nối**: (1) `Pipeline` chọn danh tính theo quy tắc Best-shot (`matched_group`), trong khi `Liveness` lại tự chọn Nhánh A/B theo độ dài `track.live` và `buckets[profile]` độc lập với `matched_group`; (2) `_reverify_current_bucket` trộn lẫn hai mục tiêu khác nhau (bảo vệ Best-shot cho cùng một người vs tách track khi vướng người thứ hai); (3) điều kiện loại trừ TV nền trong `context_detector` dùng tỷ lệ diện tích trên thân người thay vì trên thiết bị; và (4) `supplement_only` cùng `backfill_legacy_embeddings` chưa đồng bộ 1-1 giữa trạng thái trên đĩa (`*.jpg`), bảng `embeddings` và lịch sử `enrollments`. Độ tin cậy chẩn đoán: **high**.

## Proposed Remediation

**Preferred**: Chia làm 4 cụm sửa đổi nhỏ, có tính nguyên tử và bảo toàn 100% tương thích ngược với 36 bài kiểm thử hiện hữu:

1. **Cụm 1 — Khóa chặt bất biến `matched_group` ↔ `Liveness` & Sửa phát hiện màn hình che mặt (`app/context_detector.py`, `app/liveness.py`, `app/tracker.py`)**:
   - Trong `evaluate_face_context`: Phân biệt rõ **thiết bị cầm tay che mặt** (hộp thiết bị nằm phần lớn trong hộp thân người `_face_overlap_ratio(m_box, coupled_person) >= 0.55` hoặc `m_label == "cell phone"` bao trọn mặt) với **màn hình TV/biển hiệu lớn phía sau lưng** (`m_label == "tv"` trải rộng vượt ra ngoài thân người và diện tích lớn gấp nhiều lần hộp mặt). Khi hộp mặt nằm `>= screen_face_overlap` bên trong điện thoại/tablet/laptop/sách cầm trước người, luôn trả `spoof_reason = "screen_detected"`.
   - Trong `Liveness.evaluate_track`:
     - Nếu trong cửa sổ `recent_scr` có `1 <= screen_hits < self.screen_min_hits` (màn hình chập chờn) hoặc frame hiện tại có `medium_nearby=True` kèm điểm FAS trung gian, giữ trạng thái `"checking"` thay vì cấp `"live"` sớm.
     - Nếu `track.matched_group == "frontal"`: bắt buộc phải đánh giá qua Nhánh A (`len(track.live) >= self.min_frames`), tuyệt đối **không** rơi xuống Nhánh B (nếu chưa đủ `min_frames` thì giữ `"checking"`, và nếu `len(track.live) >= 2` mà trung bình `< spoof_below * 0.8` thì đánh spoof sớm).
     - Ở Nhánh A, chỉ gán `track.verification_tier = "verified"` khi `track.matched_group == "frontal"`; nếu `matched_group != "frontal"` thì giữ `"profile_verified"`. Cập nhật `Track.live_mean` trả `self.liveness_score` làm điểm chính thức do `Liveness` đã tổng hợp đúng theo nhánh.

2. **Cụm 2 — Tái cấu trúc `_reverify_current_bucket` chống cắt nhầm & chống ping-pong (`app/pipeline.py`)**:
   - Khi track đã có `employee_id`:
     - Nếu `pose_group == t.matched_group` (đang ở chính ngăn đã dùng để chốt danh tính): kiểm tra cả độ khớp với nhân viên hiện tại (`emp_sim < eff_thr - VERIFY_SLACK`) lẫn so khớp gallery mở — nếu trượt hoặc khớp sang người khác thì tách track (`_split_mismatched_bucket`).
     - Nếu `pose_group != t.matched_group` (ví dụ đã chốt `frontal`, nay người đó xoay sang `left_profile`, hoặc ngược lại):
       - Trường hợp `pose_group == "frontal"` (đang từ `profile_verified` quay về chính diện): nếu `gallery.match` chấp nhận **cùng nhân viên** (`m.accepted and m.employee_id == t.employee_id`) thì nâng hạng lên `matched_group = "frontal"`, `verification_tier = "verified"`.
       - Trường hợp tổng quát ở ngăn phụ: **chỉ tách sang track mới (`tracker.split`)** khi ngăn hiện tại khớp rõ với **một nhân viên khác** (`m.accepted and m.employee_id != t.employee_id`) hoặc khi nhân viên hiện tại có mẫu ở ngăn đó mà `emp_sim` tụt cực sâu đồng thời `t.event_done` chưa chốt và ngăn chính cũng không còn giữ được; **không bao giờ** gọi `t.reset_identity()` tại chỗ trên một track đang giữ `buckets["frontal"]` của người cũ (triệt tiêu hoàn toàn vòng lặp ping-pong) và **không cắt track** chỉ vì nhân viên hợp lệ xoay đầu sang góc nghiêng có mẫu yếu hơn.

3. **Cụm 3 — Bảo vệ người thật đi thẳng không xoay đầu ở cổng Parallax (`app/pose3d.py`, `app/liveness.py`, `config.yaml`)**:
   - Tính thêm biên độ biến thiên góc nhìn `yaw_span = max(yaws) - min(yaws)` trên cửa sổ `pose_history` trong Nhánh B:
     - Khi `par_score >= self.par_min_depth`: đạt thị sai 3D (`par_ok = True`).
     - Khi `par_score < self.par_min_depth * 0.65`: chỉ kết luận cứng `_mark_spoof("planar_no_parallax")` nếu đã có biến thiên góc nhìn đủ lớn (`yaw_span >= 4.0°` mà vẫn phẳng cứng 2D) **hoặc** có tín hiệu nghi ngờ vật chứa (`any(h.get("medium_nearby") for h in recent_ctx)` / Moiré cao); ngược lại nếu người đi tịnh tiến giữ nguyên góc đầu (`yaw_span < 4.0°`) thì trả `"checking"` chờ thêm biến thiên góc tự nhiên khi tiến gần cổng thay vì khóa spoof vĩnh viễn.
   - Ghi cảnh báo rõ khi `PoseEstimator3D.session is None` trong khi `liveness.parallax.enabled=True`.

4. **Cụm 4 — Làm sạch vòng đời `supplement_only`, `backfill_legacy_embeddings`, API & Config (`app/enrollment.py`, `app/main.py`, `app/guided.py`, `app/config.py`)**:
   - **`supplement_only`**: Trước khi `clear_embeddings(..., pose_groups=updated_groups)`, truy vấn `crop_path` của các dòng sắp xóa để xóa file `.jpg` tương ứng trên đĩa; đặt chỉ số file mới theo `max_existing_prefix + 1`; không tự ý đổi `employees.name` khi nhân viên đang `status='active'`.
   - **`backfill_legacy_embeddings` & `lifespan`**:
     - Bỏ qua nhân viên nếu `report` mới nhất trong `enrollments` đã ghi nhận thất bại backfill cho cùng `engine.model_version` (tránh chạy lặp mỗi lần restart và tránh phình bảng `enrollments`); khi ghi cảnh báo backfill lần đầu, kế thừa `crops`/`bins` từ báo cáo gần nhất (nếu có) thay vì ghi đè bằng mảng rỗng.
     - Truyền `stop_event: threading.Event` từ `lifespan` vào `backfill_legacy_embeddings` (set trước khi đóng pipeline/DB lúc shutdown) và kiểm tra lại nhân viên còn tồn tại + vẫn `n_emb == 0` ngay trước khi chèn embedding vào DB.
   - **API & Config**: Trong `enroll_guided_start`, kiểm tra/nạp `verify_refs` một lần và bọc `GuidedSession(...)` bằng `try ... except EnrollError as e: raise HTTPException(409, str(e))`; đưa đầy đủ `cross_pose_min_cosine` (`0.20`), `screen_face_overlap` (`0.80`), `screen_window` (`5`), `screen_min_hits` (`3`) vào `app/config.py:_apply_defaults` và thống nhất một hằng ` MAX_OUTLIER_RATIO = 0.35`.

**Alternatives**:
- Có thể khóa tuần tự toàn bộ `enroll_video` / `employee_delete` với `backfill_legacy_embeddings` bằng một `enroll_lock` toàn cục, nhưng dùng `stop_event` kết hợp kiểm tra nguyên tử trong `db.lock` trước khi ghi nhẹ nhàng hơn và không làm treo HTTP request của HR khi server vừa khởi động.

**Files likely to change**:
- `app/config.py`
- `app/context_detector.py`
- `app/liveness.py`
- `app/tracker.py`
- `app/pose3d.py`
- `app/pipeline.py`
- `app/enrollment.py`
- `app/guided.py`
- `app/main.py`
- `tests/test_liveness_profile.py`
- `tests/test_tracker_buckets.py`
- `tests/test_pose3d.py`
- `tests/test_guided.py`
- `tests/test_api_contracts.py`

**Tests to add or update**:
- `tests/test_liveness_profile.py`:
  1. Kẻ gian cầm điện thoại che ngay trước mặt mình (có `person_box` của chính người cầm) → bắt đúng `screen_detected`, trong khi TV treo tường phía sau vẫn không báo giả.
  2. Màn hình phát hiện chập chờn `1–2/5` frame → giữ `"checking"`, không duyệt `"live"` sớm.
  3. Track có 2 frame `frontal` điểm FAS thấp (`0.12`) chuyển sang 6 frame `profile` → không được lọt `checkin` hạng `verified`; và track `left_profile` không bị nâng nhầm thành `verified` khi `frontal` chưa được `_identify` chấp nhận.
- `tests/test_tracker_buckets.py`:
  4. Nhân viên hợp lệ đã nhận diện ở `frontal` quay đầu sang góc nghiêng có điểm khớp yếu với chính họ → không bị cắt đứt track Best-shot.
  5. Chặn hiện tượng dao động danh tính ping-pong (`NVA ↔ None`) và chặn ghép nhầm sang `NVB` khi nhân viên cũ `NVC` chưa có mẫu `profile` (`emp_sim is None`).
- `tests/test_pose3d.py`:
  6. Chuỗi landmark trực giao 3DDFA dịch ngang khi người đi thẳng chưa xoay đầu (`Δyaw < 1°`, không có vật chứa nghi ngờ) → trả `"checking"`, không khóa chết `planar_no_parallax`.
- `tests/test_guided.py` & `tests/test_api_contracts.py`:
  7. `supplement_only` chạy lặp nhiều lần xóa sạch ảnh `.jpg` góc nghiêng cũ trên đĩa và giữ nguyên tên nhân viên đang `active`.
  8. `backfill_legacy_embeddings` không chạy lặp/phình bảng `enrollments` ở lần khởi động thứ 2, bảo toàn `crops` báo cáo cũ, tôn trọng `stop_event` khi shutdown và không ghi trùng nếu nhân viên vừa được đăng ký mới; `enroll_guided_start` chuyển `EnrollError` thành `HTTP 409`.

## Risks & Considerations

- **Cân bằng Bảo mật vs Trải nghiệm đi qua cổng**: Siết `matched_group == "frontal"` đòi đủ `min_frames = 4` mẫu `MiniFASNet` có nghĩa là nếu một người chỉ lướt mặt thẳng đúng 2 frame rồi quay hẳn 85° đi tiếp (tổng cộng có 2 frame thẳng + 5 frame nghiêng), hệ thống cần cho phép hạ bậc chủ động về chốt trên ngăn `profile` (`profile_verified`) nếu ngăn `profile` đủ chuẩn **và** các frame `frontal` trước đó không có dấu hiệu giả mạo (`min(track.live) >= spoof_below`), thay vì kẹt vĩnh viễn ở `"checking"`. Cần xử lý rõ nhánh chuyển tiếp này trong `Pipeline` / `Liveness`.
- **Hiệu chỉnh thực địa**: Các ngưỡng `min_depth_score` và `cross_pose_min_cosine` vẫn nên được kiểm chứng lại bằng `scripts/benchmark.py --profile-eval` trên camera lắp thực tế tại cổng.

## Open Questions

- Không còn câu hỏi chặn thực thi — toàn bộ phạm vi và mã định danh `slug=review-round2-fixes` đã được xác nhận qua bước Clarify, sẵn sàng chuyển sang `/speckit-bug-fix slug=review-round2-fixes`.
