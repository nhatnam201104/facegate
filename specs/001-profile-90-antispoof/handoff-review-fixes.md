# Biên bản bàn giao: sửa 10 lỗi từ code review (branch `001-profile-90-antispoof`)

- **Ngày lập:** 2026-11-12
- **Branch:** `001-profile-90-antispoof` (HEAD `b4cf497 base feature`), so sánh với `main`
- **Người nhận:** agent lập trình tiếp theo
- **Nguồn:** kết quả `/code-review` trên diff của branch so với `main`

## 0. Bối cảnh và quy tắc làm việc

FaceGate là hệ thống chấm công bằng khuôn mặt (FastAPI + OpenCV, Python, venv ở `.venv/`). Branch này thêm nhận diện góc nghiêng tới 90° và chống giả mạo theo ngữ cảnh (context, parallax, moire). Spec, plan và tasks của tính năng nằm cùng thư mục với file này (`spec.md`, `plan.md`, `tasks.md`); đọc `spec.md` trước khi đổi hành vi.

**Trạng thái ban đầu:** `.venv/bin/python -m pytest -q` cho kết quả **25 passed** (khoảng 20 giây). Sau mỗi mục sửa, test phải vẫn xanh.

**Quy tắc:**
1. Mỗi mục sửa phải kèm ít nhất 1 test hồi quy mô tả đúng kịch bản lỗi bên dưới. Test phải fail trước khi sửa và pass sau khi sửa. Đặt test vào file có sẵn cùng chủ đề trong `tests/` nếu phù hợp.
2. Commit riêng cho từng mục (hoặc từng nhóm mục liên quan), message ghi rõ số mục, ví dụ `fix(#1): ghi sự kiện spoof vào bảng events`.
3. Không đổi ngưỡng trong `config.yaml` trừ khi mục đó yêu cầu. Ngưỡng mới thì thêm thành key config có giá trị mặc định, không hard-code.
4. Comment và chuỗi hiển thị dùng tiếng Việt, theo phong cách hiện có của file.
5. Các điểm đánh dấu **[CẦN HỎI NGƯỜI DÙNG]** là quyết định sản phẩm: làm theo mặc định được đề xuất, rồi ghi lại trong báo cáo cuối để người dùng xác nhận.

## 1. Thứ tự ưu tiên

| Ưu tiên | Mục | Loại | Ước lượng |
|---|---|---|---|
| P0 | #1 Spoof không được ghi lại | Logic | Nhỏ |
| P0 | #2 Mạo danh qua đăng ký bổ sung góc nghiêng | Bảo mật | Vừa |
| P0 | #3 Stored XSS ở tab Nhân viên | Bảo mật | Nhỏ |
| P0 | #9 Không kiểm tra cùng một người giữa các nhóm góc | Bảo mật | Vừa |
| P1 | #6 Sai công thức tách pitch/roll | Chính xác | Nhỏ |
| P1 | #4 Moire bỏ frame điểm 0 | Từ chối nhầm | Nhỏ |
| P1 | #10 Đồ vật gần mặt gây spoof vĩnh viễn | Từ chối nhầm | Vừa |
| P1 | #5 Xác minh lại chỉ dùng bucket chính diện | Nhận nhầm | Vừa |
| P2 | #8 Backfill nhúng ảnh chưa căn chỉnh | Chất lượng dữ liệu | Nhỏ |
| P2 | #7 Điểm độ sâu không có giá trị chống giả mạo | Thiết kế | Vừa |

Nên làm theo thứ tự: #1 → #3 → #6 → #4 → #2 + #9 (cùng chủ đề, làm chung) → #5 → #10 → #8 → #7.

---

## 2. Chi tiết từng mục

### #1. Sự kiện spoof không bao giờ được ghi vào bảng events (P0)

**Vị trí:** [app/pipeline.py:359](../../app/pipeline.py#L359), [app/liveness.py:267](../../app/liveness.py#L267) (`_mark_spoof`), [app/pipeline.py:459](../../app/pipeline.py#L459) (`_live_state`)

**Nguyên nhân:** `Liveness.evaluate_track` gọi `_mark_spoof`, hàm này đặt `track.spoof = True` rồi mới trả về `"spoof"`. Pipeline kiểm tra `state == "spoof" and not t.spoof`, nên điều kiện luôn sai. Kết quả: `_emit(kind="spoof")`, `verification_tier="rejected_spoof"` và log cảnh báo không bao giờ chạy. `_render` cũng gọi `_live_state` nên có thể đặt `t.spoof` từ ngoài luồng phát hiện.

**Hướng sửa:**
- Thêm cờ `spoof_emitted: bool = False` vào `Track` ([app/tracker.py](../../app/tracker.py)).
- Trong pipeline, thay điều kiện bằng `if t.spoof and not t.spoof_emitted:` (đặt sau lời gọi `_live_state`), rồi đặt `t.spoof_emitted = True`, `verification_tier`, log và `_emit(kind="spoof")` như hiện tại.
- Kiểm tra `_render`: nếu nó gọi `_live_state` chỉ để vẽ, chuyển sang đọc trạng thái đã cache trên track (ví dụ `t.live_state` do pipeline ghi) để không thay đổi track từ luồng render.

**Tiêu chí nghiệm thu:**
- Test: giả lập `liveness.evaluate_track` trả về spoof (gọi `_mark_spoof` thật), chạy 1 bước pipeline, khẳng định `_emit` được gọi đúng 1 lần với `kind="spoof"` và `t.verification_tier == "rejected_spoof"`.
- Gọi bước pipeline lần 2 cho cùng track: `_emit` không bị gọi lại.

### #2. Phiên `supplement_only` ghi đè embedding góc nghiêng của nhân viên mà không xác minh danh tính (P0, bảo mật)

**Vị trí:** [app/enrollment.py:208](../../app/enrollment.py#L208), [app/guided.py:126-140](../../app/guided.py#L126-L140), [app/guided.py:262-314](../../app/guided.py#L262-L314), [app/main.py:337](../../app/main.py#L337)

**Nguyên nhân:** Với `supplement_only=True`, phiên chỉ có 2 bước góc nghiêng (`PROFILE_STEPS`). Bước 0 chính diện bị bỏ nên `front_emb` là None, và `_commit` chỉ kiểm tra danh tính với bước frontal. `store_enrollment` xoá vector trái/phải cũ, chèn vector mới và giữ `status='active'`. Ai cũng có thể gọi `POST /api/enroll/guided/start` với mã của nhân viên đang active và chèn mặt của mình vào.

**Hướng sửa (mặc định):**
1. Phiên `supplement_only` bắt đầu bằng **1 bước nhìn thẳng để xác minh** (không lưu embedding của bước này). Embedding bước này phải khớp với gallery frontal đã lưu của đúng nhân viên đó, cosine ≥ ngưỡng nhận diện frontal hiện hành (`ctx.threshold` hoặc ngưỡng frontal theo backend trong `recognition.thresholds`). Nếu không khớp: phiên fail với thông báo rõ ràng.
2. Nếu nhân viên chưa có embedding frontal cho `model_version` hiện tại: từ chối `supplement_only` (HTTP 409) và yêu cầu đăng ký đầy đủ.
3. Lấy embedding bước xác minh làm `front_emb` cho các bước góc nghiêng, rồi áp dụng kiểm tra chéo góc của mục #9.
4. Trong `store_enrollment`, khi `supplement_only` và `warnings` không rỗng (ví dụ trùng nhân viên khác), chuyển `status='pending'` để HR duyệt lại.

**[CẦN HỎI NGƯỜI DÙNG]:** Có muốn **mọi** lần bổ sung góc nghiêng đều phải HR duyệt lại (đặt `pending`) không? Mặc định ở trên chỉ đặt `pending` khi có cảnh báo.

**Tiêu chí nghiệm thu:**
- Test: nhân viên A active, mở phiên `supplement_only` với mặt người B ở bước xác minh → phiên fail, embedding góc nghiêng của A không đổi, status vẫn `active`.
- Test: mặt A ở bước xác minh → embedding góc nghiêng được thay, status đúng theo quy tắc ở trên.
- Test: nhân viên không có embedding frontal → API trả 409.
- `tests/test_guided.py` hiện có vẫn pass (cập nhật số bước nếu test đang giả định 2 bước).

### #3. Stored XSS và lỗi cú pháp JS ở tab Nhân viên (P0, bảo mật)

**Vị trí:** [app/static/app.js:344](../../app/static/app.js#L344) (và dòng 346 `empDel`, hàm `jumpSupplement` ở dòng 320)

**Nguyên nhân:** `esc()` escape HTML, nhưng giá trị được đặt trong chuỗi JS nháy đơn bên trong thuộc tính `onclick`. Trình duyệt giải mã entity (`&#39;` → `'`) trước khi chạy handler, nên tên như `x');fetch(...);('` chạy được JS tuỳ ý, và tên `O'Neil` gây SyntaxError.

**Hướng sửa:**
- Bỏ toàn bộ `onclick="..."` chứa dữ liệu người dùng trong bảng nhân viên. Dùng `data-*` (giá trị đi qua `esc()`) kèm class hành động, ví dụ `<button class="js-supplement" data-code="${esc(e.code)}" data-name="${esc(e.name)}">`.
- Gắn 1 listener uỷ quyền (event delegation) trên `<tbody>` để đọc `btn.dataset.code` / `btn.dataset.name` rồi gọi `jumpSupplement`, `empDel`, `empAct`.
- Rà toàn bộ `app.js` (`grep -n "onclick=" app/static/app.js`) tìm các chỗ khác nội suy dữ liệu người dùng vào handler inline, sửa theo cùng cách.
- Tuỳ chọn: phía server, `_check` trong `app/main.py` giới hạn độ dài tên và loại ký tự điều khiển.

**Tiêu chí nghiệm thu:**
- Thủ công hoặc bằng Playwright: tạo nhân viên tên `O'Neil` và tên chứa payload ở trên, mở tab Nhân viên, bấm "+ Bổ sung góc 90°" và "Xóa". Không có lỗi console, không có request lạ, form bổ sung điền đúng tên gốc.

### #4. Moire bỏ các frame có điểm 0 trước khi lấy trung bình (P1)

**Vị trí:** [app/liveness.py:136](../../app/liveness.py#L136), [app/liveness.py:175-180](../../app/liveness.py#L175-L180), [app/liveness.py:244](../../app/liveness.py#L244)

**Nguyên nhân:** `0.0` đang mang 2 nghĩa: "không tính" (moire tắt hoặc frame không đạt chất lượng, dòng 136) và "sạch". Dòng 175 lọc `> 0`, nên `moire_mean` chỉ là trung bình các giá trị ngoại lai. Một frame nhiễu 0.7 là đủ để người thật bị spoof vĩnh viễn ở nhánh B.

**Hướng sửa:**
- Dòng 136: trả về `None` khi không tính moire, thay vì `0.0`.
- Dòng 175: `moire_vals = [h["moire_score"] for h in hist if h["moire_score"] is not None]` (giữ cả giá trị 0 thật).
- Dùng trung vị (median) của 6 giá trị gần nhất thay cho trung bình để bền với 1 frame lệch, và yêu cầu tối thiểu số mẫu (ví dụ `len(moire_vals) >= 4`) trước khi kết luận `moire_ok=False` ở dòng 244. Khi chưa đủ mẫu thì trả `"checking"`, không trả spoof.
- Rà các chỗ khác đọc `moire_score` (log, `liveness_detail`, API) để chịu được `None`.

**Tiêu chí nghiệm thu:**
- Test: 15 frame điểm 0.0 + 1 frame 0.7 → không spoof.
- Test: 6 frame liên tiếp ≥ 0.8 → spoof `moire_pattern` như cũ.
- `tests/test_liveness_profile.py` vẫn pass.

### #5. Xác minh lại danh tính chỉ dùng bucket chính diện cũ (P1)

**Vị trí:** [app/tracker.py:147](../../app/tracker.py#L147) (`select_best_bucket`), [app/tracker.py:119](../../app/tracker.py#L119) (`should_split`), [app/tracker.py:196-209](../../app/tracker.py#L196-L209) (`_jump`), `_identify` trong [app/pipeline.py](../../app/pipeline.py)

**Nguyên nhân:** Khi bucket frontal có từ 2 frame trở lên, `select_best_bucket` luôn trả về frontal. Nếu track bị ghép nhầm sang người B (do nhánh ghép person-box IoU ≥ 0.45 trong `_jump`, hoặc do hoán đổi), embedding của B rơi vào bucket góc nghiêng đang trống. `should_split` cần ≥2 phần tử cùng bucket nên không kích hoạt, và `_identify` vẫn so vector frontal cũ của A.

**Hướng sửa:**
- Giữ quy tắc best-shot cho **lần nhận diện đầu**. Sau khi track đã có `employee_id`, mỗi khi bucket của `pose_group` hiện tại đạt ngưỡng tối thiểu (`min_good` cho frontal, `min_profile` cho profile), thì xác minh bucket đó với gallery **cùng nhóm góc** của nhân viên đã nhận.
- Nếu điểm khớp dưới ngưỡng của nhóm góc đó: tách track (`tracker.split`) hoặc huỷ danh tính, không giữ check-in cho A.
- Nếu nhân viên chưa có gallery cho nhóm góc đó: không xác minh được, ghi log debug và giữ nguyên (không tự ý tách).
- Cân nhắc chặt nhánh `d * 0.5` trong `_jump`: chỉ áp dụng khi kích thước mặt khớp và khoảng cách vẫn trong `JUMP_MAX`.

**Tiêu chí nghiệm thu:**
- Test trong `tests/test_tracker_buckets.py` hoặc test pipeline: track xác nhận A bằng 3 frame frontal, sau đó thêm ≥ `min_profile` frame profile mang embedding của B (khớp gallery profile của B, không khớp của A) → track bị tách hoặc mất danh tính A.
- Track của A với frame profile của chính A → giữ nguyên danh tính.

### #6. Công thức tách pitch/roll sai khi yaw lớn (P1)

**Vị trí:** [app/pose3d.py:80-97](../../app/pose3d.py#L80-L97) (`euler_from_relative_rotation`)

**Nguyên nhân:** Code tính `pitch = atan2(R21, R22)` và `roll = atan2(-R10, R00)`. Khi yaw gần 90°, `R22` và `R00` tiến về 0, nên chỉ vài độ nghiêng camera cũng làm pitch/roll bùng lên, và frame bị loại vì `max_pitch=35` / `max_roll=35`. Docstring (`hypot(R00, R10)`) cũng không khớp với code.

**Đã kiểm chứng bằng `.venv`** (camera nghiêng t độ, mặt quay y độ, `R = Rx(t)·Ry(y)`):

| t, y | Hiện tại (yaw, pitch, roll) | Phân rã YXZ (yaw, pitch, roll) |
|---|---|---|
| 8°, 85° | 85.0, **58.2**, **-57.8** | 85.0, 0.7, 8.0 |
| 15°, 88° | 88.1, **82.6**, **-82.3** | 88.1, 0.5, 15.0 |
| 15°, 70° | 70.6, **38.1**, -35.4 | 70.6, 5.1, 14.1 |

Với `R = Ry(y)·Rx(t)`, hai cách cho kết quả như nhau.

**Hướng sửa:** dùng phân rã Euler chuẩn thứ tự Y-X-Z (R = Ry·Rx·Rz):
```python
yaw   = degrees(atan2(R[0,2], R[2,2]))
pitch = degrees(asin(clip(-R[1,2], -1, 1)))
roll  = degrees(atan2(R[1,0], R[1,1]))
```
Giữ phần kẹp yaw về ±95° như hiện tại. Sửa docstring cho đúng. Chú ý dấu của pitch và roll phải khớp quy ước code hiện tại đang dùng (ngẩng/cúi, các gợi ý trong `guided.py`). Kiểm tra bằng các test có sẵn trong `tests/test_pose3d.py` và đảo dấu nếu cần.

**Tiêu chí nghiệm thu:**
- Test tham số hoá: t ∈ {0, 8, 15}, y ∈ {0, 45, 70, 85, 88}, cả `Rx(t)·Ry(y)` và `Ry(y)·Rx(t)` → `|yaw - y| ≤ 1.5` và `max(|pitch|, |roll|) ≤ t + 1`.
- `tests/test_pose3d.py` hiện có vẫn pass.

### #7. Thành phần độ sâu trong điểm parallax không có giá trị chống giả mạo (P2, thiết kế)

**Vị trí:** [app/pose3d.py:161-200](../../app/pose3d.py#L161-L200) (`planar_parallax_score`), nhánh dự phòng 5 điểm có độ sâu cố định (-15/5/22) trong cùng file

**Nguyên nhân:** `depth_spans` lấy từ toạ độ z của landmark do 3DDFA tái dựng. Mô hình morphable luôn sinh độ sâu cho mọi khuôn mặt, kể cả ảnh in, nên `s_dep` gần như luôn bằng 1 và cộng sẵn 0.35 vào điểm. Ảnh phẳng chỉ cần thêm 0.65·s_par từ dao động landmark là vượt `min_depth_score=0.55`. Nhánh 5 điểm luôn qua `depth_ratio > 0.02`.

**Hướng sửa:**
- Bỏ trọng số của `depth_ratio` khỏi điểm cuối. Điểm chỉ dựa trên phần dư homography 2D (`parallax_2d`), vì đây mới là tín hiệu phân biệt mặt phẳng với vật 3D.
- Có thể giữ `depth_ratio` làm điều kiện sanity (loại landmark hỏng) nhưng không được cộng điểm.
- Với nhánh 5 điểm (không có z thật): trả về `0.0` cho phần độ sâu, hoặc đánh dấu "không đủ dữ liệu parallax" để nhánh B trả `"checking"` thay vì `"live"`.
- Hiệu chỉnh lại `min_depth_score` và ngưỡng spoof nếu thang điểm thay đổi. Ghi rõ giá trị mới trong `config.yaml` kèm comment.

**[CẦN HỎI NGƯỜI DÙNG]:** Có dữ liệu video thật / ảnh in / màn hình nào để hiệu chỉnh ngưỡng không? Nếu không có, chỉ sửa công thức, giữ ngưỡng, và ghi trong báo cáo rằng ngưỡng chưa được hiệu chỉnh lại.

**Tiêu chí nghiệm thu:**
- Test: chuỗi landmark tạo bằng một phép homography phẳng áp lên một bộ điểm 3D bất kỳ (z khác 0) → điểm < ngưỡng spoof.
- Test: chuỗi landmark từ một vật 3D xoay quanh trục dọc (chiếu phối cảnh) → điểm ≥ `min_depth_score`.

### #8. Backfill nhúng ảnh chưa căn chỉnh khi không phát hiện được mặt (P2)

**Vị trí:** [app/enrollment.py:266-320](../../app/enrollment.py#L266-L320) (`backfill_legacy_embeddings`), lời gọi tại [app/main.py:62](../../app/main.py#L62)

**Nguyên nhân:** Khi `engine.detect` không thấy mặt, code embed bản `cv2.resize` của crop margin 25% chưa căn chỉnh, gán quality 0.75 và lưu vào gallery. Các vector rác này làm lệch `Gallery.match`. Hàm còn chạy đồng bộ trong `lifespan`, chặn server khởi động.

**Hướng sửa:**
- Không phát hiện được mặt → **bỏ qua ảnh đó** (ghi log cảnh báo và đếm số ảnh bị bỏ), không embed.
- Với ảnh có mặt: chạy kiểm tra chất lượng như luồng đăng ký (`quality`), dùng điểm chất lượng thật thay vì hằng 0.75.
- Sau khi backfill một nhân viên: áp dụng kiểm tra nhất quán (outlier trong nhóm + kiểm tra chéo nhóm của mục #9). Nếu không đạt, không lưu và đánh dấu nhân viên cần đăng ký lại (warning trong báo cáo).
- Chuyển backfill sang chạy nền (thread hoặc `asyncio.to_thread` + task) để server khởi động ngay. Trong lúc backfill chưa xong, gallery dùng dữ liệu hiện có, rồi reload khi backfill xong.

**Tiêu chí nghiệm thu:**
- Test: thư mục enroll có 1 ảnh có mặt + 1 ảnh trắng → chỉ 1 embedding được thêm.
- Test (hoặc kiểm tra thủ công): `lifespan` trả quyền trước khi backfill hoàn tất.

### #9. Không còn kiểm tra cùng một người giữa các nhóm góc (P0, bảo mật)

**Vị trí:** [app/enrollment.py:118-138](../../app/enrollment.py#L118-L138) (đăng ký bằng video), [app/guided.py:307-316](../../app/guided.py#L307-L316) (`_commit` của phiên có hướng dẫn)

**Nguyên nhân:** Kiểm tra outlier chỉ chạy trong từng `pose_group`. Nhóm chỉ có 1 frame được giữ tự động với `sim_center=1.0`. Không có kiểm tra giữa các nhóm, nên frame góc nghiêng của người B (trong video, hoặc bị ghép vào) được lưu làm embedding của A. Phiên có hướng dẫn cũng bỏ qua `min_identity_cos` ở các bước góc nghiêng.

**Hướng sửa:**
- Thêm key config `enrollment.cross_pose_min_cosine` (ngưỡng cosine giữa tâm frontal và từng frame profile).
- **Hiệu chỉnh ngưỡng từ dữ liệu có sẵn**: viết script nhỏ trong scratchpad (không commit) đọc DB, tính cosine frontal↔profile của **cùng một người** và của **người khác nhau** với backend hiện tại (edgeface). Chọn ngưỡng tách hai phân bố, ưu tiên ít chấp nhận nhầm. Ghi phân bố và ngưỡng chọn vào báo cáo. Nếu DB chưa có đủ nhân viên có cả 2 nhóm góc, dùng giá trị thận trọng và ghi rõ là tạm thời.
- Đăng ký video: nếu có frame profile thì bắt buộc phải có frame frontal. Tính tâm frontal (sau lọc outlier), loại các frame profile có cosine với tâm frontal < `cross_pose_min_cosine`. Nếu tỉ lệ bị loại vượt ngưỡng (dùng lại quy tắc 35%) → `EnrollError` "nhiều người trong video".
- Bỏ quy tắc nhóm 1 frame được tự động giữ với `sim_center=1.0`: một frame đơn vẫn phải qua kiểm tra chéo nhóm (với nhóm frontal thì so với tâm của toàn bộ frame frontal).
- Phiên có hướng dẫn: trong `_commit`, với bước `pose_group` khác `frontal` và `front_emb` có giá trị → yêu cầu `emb @ front_emb >= cross_pose_min_cosine`, trả lỗi tiếng Việt tương tự bước frontal.

**Tiêu chí nghiệm thu:**
- Test (mock engine trả embedding định trước): frame frontal của A + frame profile của B → bị loại / `EnrollError`.
- Test: frame frontal + profile của cùng A → được giữ.
- Test phiên có hướng dẫn: bước profile với embedding của B → `_commit` trả lỗi.

### #10. Đồ vật bình thường gần mặt gây spoof vĩnh viễn (P1)

**Vị trí:** [app/context_detector.py:100-112](../../app/context_detector.py#L100-L112) (`evaluate_face_context`), [app/liveness.py:166-171](../../app/liveness.py#L166-L171)

**Nguyên nhân:** Bất kỳ box `phone`/`laptop`/`tv`/`book` nào nằm ≥35% trong vùng 2.5× quanh mặt, hoặc chồng ≥20% lên mặt, đều trả `screen_detected`. Chỉ cần 2 frame là `_mark_spoof`, và `track.spoof` giữ True suốt track. Người thật cầm điện thoại ngang ngực, ôm sách, hoặc đứng trước TV treo tường sẽ không thể chấm công.

**Hướng sửa:**
- Chỉ coi là "mặt nằm trong màn hình" khi **hộp mặt nằm phần lớn bên trong box thiết bị** (ví dụ `_face_overlap_ratio(face_box, m_box) >= 0.8`), và mặt **không** gắn với một thân người nằm ngoài thiết bị (dùng kết quả body-coupling ở phần 2 của hàm).
- Bỏ điều kiện "thiết bị chiếm ≥35% vùng mở rộng" làm căn cứ spoof. Nếu muốn giữ, hạ xuống thành tín hiệu phụ (`medium_nearby=True`) không tự gây spoof.
- Trong `evaluate_track`, yêu cầu `screen_hits` đạt tỉ lệ đáng kể trên các frame gần nhất (ví dụ ≥3 trong 5 frame gần nhất) thay vì tổng tích luỹ ≥2.
- Cân nhắc cho phép gỡ trạng thái spoof do `screen_detected` nếu N frame sau đó sạch. **[CẦN HỎI NGƯỜI DÙNG]**; mặc định là không gỡ (giữ hành vi an toàn), chỉ làm hẹp điều kiện phát hiện.

**Tiêu chí nghiệm thu:**
- Test: mặt có thân người, box điện thoại nằm dưới cằm (chồng mặt < 20%, nằm trong vùng mở rộng) → `spoof_reason is None`.
- Test: mặt nằm trọn trong box điện thoại, không có thân người phù hợp → `screen_detected`.
- Test: box TV lớn phía sau chứa cả mặt, nhưng mặt gắn với thân người nằm ngoài/trước TV → không spoof. (Trường hợp mặt nằm trọn trong TV là ca khó; ghi chú giới hạn này trong báo cáo nếu không xử lý được.)

---

## 3. Kiểm tra cuối cùng

1. `.venv/bin/python -m pytest -q`: tất cả pass, số test tăng so với 25 ban đầu.
2. Chạy app (`./run.sh`) và kiểm tra thủ công:
   - Tab Nhân viên với tên chứa `'` (mục #3).
   - Giơ ảnh/màn hình điện thoại trước camera → có dòng `spoof` trong bảng events và live feed (mục #1).
   - Đi qua camera khi cầm điện thoại ngang ngực → không bị spoof (mục #10).
3. `git diff main --stat` chỉ gồm các file liên quan.

## 4. Báo cáo bàn giao lại

Khi xong, báo cáo cho người dùng (tiếng Việt):
- Danh sách mục đã sửa, commit tương ứng, test mới thêm.
- Các mục chưa sửa hoặc sửa một phần, kèm lý do.
- Các quyết định đã chọn theo mặc định ở các điểm **[CẦN HỎI NGƯỜI DÙNG]** (#2, #7, #10).
- Ngưỡng mới (`cross_pose_min_cosine`, ngưỡng parallax nếu có) và cách đã hiệu chỉnh.
- Kết quả `pytest` cuối cùng (dán nguyên dòng tóm tắt).
