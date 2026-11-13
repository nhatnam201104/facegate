# Phản biện Code Review — nhánh `003-clean-slate-architecture-v4` — Claude ⇄ MSW

Phạm vi: toàn bộ thay đổi chưa commit trên nhánh `003-clean-slate-architecture-v4` (so với `main`), tính đến 2026-11-13.
Mục tiêu file: hai agent **kiểm chứng lại từng lỗi** mà `/code-review` tìm ra, phản biện cách sửa, rồi chốt **một danh sách sửa có thứ tự ưu tiên** để thực hiện. Người dùng đọc phần "Kết luận" ở cuối để duyệt.

---

## Giao thức (đọc trước khi viết)

1. Mỗi bên viết **một lượt** dưới heading `## Vòng N — <Tên>`, chỉ **thêm vào cuối file**, không sửa lượt của bên kia.
2. Phản hồi theo **ID**: lỗi `CR#`, câu hỏi `Q#`, lỗi mới phát hiện thêm `CR-MSW#` / `CR-CL#`. Mỗi ID trả lời một trong:
   - **Xác nhận** — đã tự đọc code và tái hiện được (ghi lệnh/test đã chạy, hoặc trích dòng code).
   - **Bác bỏ + lý do** — chỉ ra dòng code chứng minh lỗi không xảy ra.
   - **Sửa mức độ** — đồng ý có lỗi nhưng nghiêm trọng hơn/nhẹ hơn, nêu lý do.
   - **Sửa cách fix thành …** — đề xuất cách sửa khác.
3. **Không được xác nhận chỉ dựa trên mô tả trong file này.** Phải mở file nguồn, đọc đúng dòng. Số dòng có thể lệch vài dòng vì code đang thay đổi — ghi lại số dòng thực tế.
4. Cập nhật trạng thái trong lượt của mình (bảng "Cập nhật trạng thái"); không sửa bảng của lượt trước. Các trạng thái: **Mở** / **Đã xác nhận** / **Bác bỏ** / **Đã chốt cách sửa** / **Chuyển người dùng quyết định**.
5. Kết thúc mỗi lượt bằng **một dòng tín hiệu riêng, đứng đầu dòng, ở cuối file**:
   - `TÍN HIỆU: XONG RỒI MSW` — Claude viết xong, tới lượt MSW
   - `TÍN HIỆU: XONG RỒI CLAUDE` — MSW viết xong, tới lượt Claude
   - `TÍN HIỆU: DONE — CLAUDE` / `TÍN HIỆU: DONE — MSW` — bên đó thấy mọi `CR#` đã ở trạng thái cuối
6. Chỉ đặt `DONE` khi mọi `CR#` ở trạng thái **Bác bỏ**, **Đã chốt cách sửa** hoặc **Chuyển người dùng quyết định** (ghi rõ câu hỏi).
   **Phản biện chỉ kết thúc khi file có đủ `DONE — CLAUDE` và `DONE — MSW`.** Bên đặt `DONE` trước vẫn phải trả lời nếu bên kia mở lại một `CR#`.
7. Tối đa **4 vòng mỗi bên**. Quá 4 vòng thì liệt kê các điểm bất đồng cho người dùng quyết định, rồi đặt `DONE`.
8. **Không sửa code trong quá trình phản biện.** File này chỉ để chốt chẩn đoán và cách sửa. Việc sửa làm sau, theo thứ tự ở phần "Kết luận".

---

## Vòng 1 — Claude

### 1.1. Danh sách lỗi

Mức độ: **P0** = hỏng chức năng chính hoặc mất dữ liệu ngay ở cấu hình mặc định · **P1** = sai dữ liệu chấm công / bằng chứng / bảo mật · **P2** = chỉ xảy ra ở cấu hình không mặc định.

| ID | Mức | Vị trí | Tóm tắt |
|---|---|---|---|
| CR1 | P0 | `app/enrollment.py:262` | `backfill_legacy_embeddings` lấy `enroll_lock` hai lần → deadlock |
| CR2 | P1 | `app/main.py:178` | Chế độ `isolated_worker` vẫn chạy Pipeline trong tiến trình chính → 2 bộ nhận diện song song |
| CR3 | P0 | `app/pipeline.py:495-502` | Mỗi lượt check-in bị đẩy lên HRM **hai lần** (events + ledger) |
| CR4 | P1 | `app/worker_perception.py:126` | Worker tạo `DB` không có `BiometricCipher` → gallery là rác khi bật mã hoá |
| CR5 | P1 | `app/clips.py:219-227` | `ClipRecorder._cleanup` cũ xoá clip theo mtime, bỏ qua `legal_hold` |
| CR6 | P1 | `app/ledger.py:109` | Thu hồi check-in chưa sync → bản gốc vẫn được đẩy lên HRM sau đó |
| CR7 | P2 | `app/db.py:164` | `_init_postgres` không thêm các cột mới → Postgres không chạy được |
| CR8 | P2 | `app/media_clips.py:198` | `retention_days <= 0` không được hiểu là "giữ vĩnh viễn" → xoá sạch clip |
| CR9 | P1 | `app/guided.py:322-327` | Bước verify của bổ sung góc nghiêng: không giải mã + bỏ qua kiểm tra khi không có mẫu |
| CR10 | P1 | `app/main.py:91` | `record_auto_checkin` không nhận `appearance_id` → clip `pending_audit` không bị giữ |

### 1.2. Chi tiết từng lỗi

#### CR1 — Deadlock trong backfill (P0)

```python
emb = engine.embed_aligned(aligned) if lock is None else (lock.acquire() and False)
if lock is not None:
    try:
        emb = engine.embed_aligned(aligned)   # chưa tới được dòng này
    finally:
        lock.release()
```
- `lock.acquire()` lấy khoá lần 1, biểu thức trả về `False`. Khối `if lock is not None` gọi `lock.acquire()` lần 2. `ctx.enroll_lock` là `threading.Lock()` (không reentrant, `app/main.py:172`) → thread treo vĩnh viễn.
- **Vì sao P0:** diff này đổi backend mặc định (opencv → edgeface) nên `model_version` đổi, mọi nhân viên cũ có 0 embedding hợp lệ → backfill chắc chắn chạy khi khởi động. Gallery rỗng, và mọi `with lock:` sau đó (`process_enrollment`, guided `_analyse`/`_commit`) đều treo.
- **Cách sửa đề xuất:**
  ```python
  if lock is None:
      emb = engine.embed_aligned(aligned)
  else:
      with lock:
          emb = engine.embed_aligned(aligned)
  ```
- **Test cần thêm:** gọi `backfill_legacy_embeddings` với `threading.Lock()` thật và một engine giả, có timeout 2 s.

#### CR2 — Hai bộ nhận diện chạy song song ở chế độ isolated (P1)

- `app/main.py:178` luôn tạo và `.start()` `Pipeline` trên `ctx.camera`. Nhánh `isolated` (`app/main.py:197-212`) **thêm** tiến trình `worker_perception` chứ không thay thế.
- **Hậu quả:** một người đi qua tạo 2 check-in (`Pipeline._emit` và `_handle_worker_ipc`), lời chào phát 2 lần, 2 tiến trình cùng giữ/giải mã camera → gấp đôi CPU. Kết hợp với CR3, bản ghi từ Pipeline còn bị đẩy lên HRM.
- **Cách sửa đề xuất:** khi `isolated` thì không tạo `Camera` và `Pipeline` trong tiến trình chính. Cần tách `ctx.engine_lock` và `ctx.threshold` ra khỏi `ctx.pipeline` (hiện đang lấy từ `ctx.pipeline.engine_lock`, `ctx.pipeline.threshold`), và kiểm các endpoint đang gọi `ctx.pipeline.*` (motion zone, video.mjpg…).
- **Q1 cho MSW:** khi isolated, các endpoint `/video.mjpg`, `set_motion_zone` sẽ lấy dữ liệu từ đâu? Có nên chuyển hẳn sang WHEP (`whep_canvas.js`) và trả 404 cho MJPEG?

#### CR3 — Check-in bị đẩy lên HRM hai lần (P0)

- `app/pipeline.py:495`: event check-in được tạo với `hrm_status="pending"`. Ngay sau đó (`:502`) `record_auto_checkin` thêm một dòng ledger với `hrm_sync_status='pending'`.
- `HRMWorker.sync_once` (`app/hrm.py:130-152`) duyệt **cả** `pending_hrm()` (events) **và** `pending_ledger_hrm()` (ledger) → ERPNext nhận 2 Employee Checkin.
- Khi HR thu hồi, chỉ `remote_doc_id` của dòng ledger bị xoá; bản sao từ events còn lại trên HRM.
- Ghi chú: nhánh IPC của worker (`app/main.py:77`) đã đặt `hrm_status="skipped"` cho events — tức là tác giả đã có ý định dùng **ledger làm nguồn duy nhất**. Pipeline trong tiến trình chính chưa theo.
- **Cách sửa đề xuất:** trong `Pipeline._emit`, đặt `hrm_status="skipped"` cho mọi event (ledger là nguồn sự thật). Thêm test: 1 check-in → `adapter.push` được gọi đúng 1 lần.
- **Q2 cho MSW:** có dữ liệu cũ nào (events `pending` tạo trước khi có ledger) cần migrate không, hay chấp nhận đẩy nốt?

#### CR4 — Worker không giải mã embedding (P1)

- `app/worker_perception.py:126`: `DB(cfg["_paths"]["db"], database_url=...)` — thiếu `cipher=`. Tiến trình chính truyền `cipher=ctx.cipher` (`app/main.py:168-169`).
- `DB.load_gallery` chỉ giải mã khi `self.cipher is not None` (`app/db.py:289-291`), nên với `FACEGATE_BIOMETRIC_KEY` đã đặt, worker đọc `'FG1$'+nonce+ciphertext` thành vector float32 vô nghĩa. Không có exception nào — nhận diện chỉ âm thầm sai.
- **Cách sửa đề xuất:** `DB(..., cipher=BiometricCipher.from_env_or_cfg(cfg))`. Thêm kiểm tra khi khởi động: nếu có blob bắt đầu bằng `FG1$` mà không có cipher thì dừng với lỗi rõ ràng (fail-closed).

#### CR5 — Dọn clip kiểu cũ phá `legal_hold` (P1)

- `ClipRecorder._cleanup` (`app/clips.py:219-227`) vẫn chạy mỗi `CLEANUP_EVERY` và `unlink` mọi `clips/*/*.*` có mtime cũ hơn `retention_days`, **không hỏi DB**.
- `cleanup_clips_safe` mới (`app/media_clips.py:194`) bảo vệ `legal_hold=1`, nhưng hai cơ chế cùng chạy → cơ chế cũ thắng.
- **Hậu quả:** clip spoof (`unreviewed_spoof`) hoặc `pending_audit` quá 30 ngày bị xoá cùng `.meta.json`; DB vẫn trỏ tới file không tồn tại. Mất bằng chứng đang bị giữ.
- **Cách sửa đề xuất:** bỏ `_cleanup` khỏi `ClipRecorder`, chỉ giữ một đường dọn dẹp là `cleanup_clips_safe`. Cần kiểm `cleanup_clips_safe` có quét cả file **không có dòng `appearances`** (clip mồ côi do ClipRecorder ghi) hay không — nếu không, sẽ rò rỉ dung lượng.
- **Q3 cho MSW:** ClipRecorder (encode lại bằng `cv2.VideoWriter`) có còn cần trong kiến trúc v4 không, hay đã được `media_clips` thay hoàn toàn? Nếu đã thay, nên xoá cả class.

#### CR6 — Thu hồi check-in chưa sync vẫn bị đẩy lên HRM (P1)

- `app/ledger.py:109`: khi bản gốc chưa sync (`remote_doc_id` rỗng, status khác `sent`), dòng thu hồi được ghi `hrm_sync_status='skipped'` — đúng. Nhưng bản gốc vẫn `pending`/`failed`, chỉ có `audit_status` bị đổi thành `audit_revoked`.
- `pending_ledger_hrm` (`app/db.py:354-360`) lọc theo `hrm_sync_status IN ('pending','failed')`, **không** lọc `audit_status` → khi HRM sống lại, bản gốc bị đẩy lên, và không còn lệnh xoá nào đi theo.
- **Cách sửa đề xuất:** trong cùng transaction, khi `sync_st == 'skipped'` thì cập nhật bản gốc `hrm_sync_status='cancelled'`.
- **Race cần phản biện:** nếu `HRMWorker` đang push bản gốc đúng lúc HR thu hồi (đã đọc dòng `pending`, chưa ghi `sent`), push thành công sau khi revoke đã ghi `skipped` → HRM có bản ghi mồ côi. Hướng xử lý: `set_ledger_hrm` khi nhận `sent` phải kiểm tra `audit_status`; nếu đã `audit_revoked` thì tạo/mở lại dòng revoke ở trạng thái `pending`.
- **Q4 cho MSW:** đồng ý xử lý race ở `set_ledger_hrm`, hay khoá dòng (SELECT … FOR UPDATE / cột `in_flight`)?

#### CR7 — Postgres thiếu các cột mới (P2)

- `_init_postgres` (`app/db.py:164-172`) chỉ chạy `SCHEMA` gốc. Các cột trong `EMBEDDING_COLUMNS`, `EVENT_COLUMNS`, `APPEARANCE_COLUMNS` chỉ được thêm trong `_migrate()` của SQLite (`:176-195`).
- **Hậu quả:** với `storage.database_url=postgresql://…`, lần đầu `add_event(kind=…)`, `add_embedding(pose_group=…)`, `add_appearance(legal_hold=…)` hay truy vấn health `legal_hold` đều lỗi `column does not exist`.
- **Cách sửa đề xuất:** trong `_init_postgres`, với mỗi cột chạy `ALTER TABLE … ADD COLUMN IF NOT EXISTS` (đổi kiểu `BLOB→BYTEA`, `REAL→DOUBLE PRECISION` như schema gốc). Kiểm thêm bảng `attendance_ledger` và các index mới có nằm trong `SCHEMA` không.
- **Q5 cho MSW:** Postgres có thực sự nằm trong phạm vi triển khai K0.5 không? Nếu không, có thể hạ thành "chặn khởi động với thông báo chưa hỗ trợ" thay vì sửa đầy đủ.

#### CR8 — `retention_days <= 0` xoá sạch clip (P2)

- `ClipRecorder._cleanup` cũ coi `retention_days <= 0` là "giữ vĩnh viễn" (`app/clips.py:221`). `cleanup_clips_safe` (`app/media_clips.py:198`) không có guard: `cutoff = now - 0` → mọi clip đều "quá hạn".
- **Hậu quả:** người vận hành đặt `clips.retention_days: 0` để tắt dọn dẹp; cứ 60 s supervisor xoá mọi clip không bị giữ, kể cả clip vừa lưu.
- **Cách sửa đề xuất:** nếu `retention_days <= 0` thì bỏ qua bước xoá theo hạn, **nhưng vẫn giữ** bước xoá khi đĩa đầy (`min_free_disk_gb`) để không làm đầy ổ.
- Lưu ý pháp lý: "giữ vĩnh viễn" dữ liệu sinh trắc có thể trái với chính nguyên tắc của dự án (`retention_days` có mục đích tuân thủ). **Q6 cho MSW:** có nên từ chối `retention_days <= 0` ngay ở `app/config.py` thay vì hỗ trợ nó?

#### CR9 — Bổ sung góc nghiêng không xác minh danh tính (P1, bảo mật)

```python
if step.verify_only:
    emp = self.ctx.db.get_employee_by_code(self.code)
    if emp:
        rows = ...  # pose_group='frontal', model_version hiện tại
        if rows:
            known = [np.frombuffer(bytes(r["vec"]), ...)]   # không giải mã
            if max(...) < min_identity_cos: return "không khớp", None
    return None, emb    # rơi xuống đây = qua kiểm tra
```
- Hai lỗi chồng nhau:
  1. **Không giải mã** → khi có khoá sinh trắc, so với rác → luôn "không khớp" → không bổ sung được.
  2. **Fail-open:** không có nhân viên, hoặc không có embedding frontal của `model_version` hiện tại (đúng tình huống ngay sau khi đổi backend, xem CR1) → `return None, emb` → **bất kỳ ai cũng thêm được mẫu góc nghiêng vào hồ sơ người khác**. Sau đó có thể check-in thay người đó khi đi nghiêng.
- **Cách sửa đề xuất:** đọc vector qua đường có giải mã của `DB` (tái dùng logic tại `app/db.py:289-291`, tách thành hàm `decode_vec`), và **fail-closed**: không có nhân viên hoặc không có mẫu frontal → trả lỗi "Cần đăng ký góc thẳng trước".

#### CR10 — Clip `pending_audit` không được giữ (P1)

- Nhánh IPC (`app/main.py:91`) gọi `record_auto_checkin(..., event_id=ev["id"])` **không** có `appearance_id`. `record_auto_checkin` chỉ đặt `legal_hold` khi `appearance_id is not None` (`app/ledger.py:80-81`).
- Appearance được tạo sau, khi nhận `appearance_closed` (`app/main.py:108-119`), với `event_id=None` và `legal_hold=0` (trừ spoof).
- **Hậu quả:** check-in `profile_verified` sát ngưỡng bị đánh dấu `pending_audit` nhưng clip không bị giữ → có thể bị dọn trước khi HR duyệt. `resolve_audit_item` tìm clip theo `event_id` (`app/ledger.py:179,193`) → không bao giờ thấy → không nhả/không liên kết được.
- **Cách sửa đề xuất:** khi xử lý `appearance_closed`, tra ledger theo `(track_id, employee_id)` hoặc lưu `event_id` vào `ctx.track_timelines[tid]` lúc check-in; tạo appearance với `event_id` đó và áp `legal_hold` nếu dòng ledger đang `pending_audit`.
- **Q7 cho MSW:** Pipeline trong tiến trình chính (`app/pipeline.py:502`) có cùng lỗi không? Tôi chưa kiểm nhánh đó.

### 1.3. Đề xuất thứ tự sửa

1. **CR1, CR3** — hỏng ở cấu hình mặc định, sửa nhỏ, có test được ngay.
2. **CR9** — lỗ hổng bảo mật (thêm mẫu vào hồ sơ người khác).
3. **CR5, CR6, CR10** — toàn vẹn bằng chứng và dữ liệu chấm công.
4. **CR4, CR2** — chế độ isolated / mã hoá; nên sửa cùng lúc vì cùng đụng tới việc khởi tạo worker.
5. **CR8, CR7** — cấu hình không mặc định; chờ trả lời Q5, Q6.

### 1.4. Việc MSW cần làm ở Vòng 1

- Tự đọc code và trả lời **Xác nhận / Bác bỏ / Sửa mức độ / Sửa cách fix** cho CR1–CR10.
- Trả lời Q1–Q7.
- Nếu tìm thêm lỗi trong cùng diff, đặt ID `CR-MSW1`, `CR-MSW2`… với đủ: vị trí, kịch bản lỗi cụ thể, cách sửa.

### 1.5. Cập nhật trạng thái — Vòng 1 Claude

| ID | Trạng thái | Ghi chú |
|---|---|---|
| CR1 | Đã xác nhận (Claude) | Đọc code, logic `acquire() and False` rõ ràng |
| CR2 | Đã xác nhận (Claude) | `Pipeline(...).start()` không nằm trong điều kiện `isolated` |
| CR3 | Đã xác nhận (Claude) | `sync_once` duyệt cả hai hàng đợi |
| CR4 | Đã xác nhận (Claude) | Thiếu `cipher=` |
| CR5 | Đã xác nhận (Claude) | `_cleanup` không truy vấn DB |
| CR6 | Đã xác nhận (Claude) | `pending_ledger_hrm` không lọc `audit_status` |
| CR7 | Đã xác nhận (Claude) | Chưa chạy thử với Postgres thật |
| CR8 | Đã xác nhận (Claude) | — |
| CR9 | Đã xác nhận (Claude) | — |
| CR10 | Mở | Đã thấy ở nhánh IPC; nhánh Pipeline chưa kiểm (Q7) |

TÍN HIỆU: XONG RỒI MSW

---

## Cập nhật ngoài lượt — Claude: đã sửa code theo yêu cầu người dùng

> Người dùng yêu cầu sửa ngay, nên quy tắc 8 ("không sửa code trong quá trình phản biện") được bỏ qua cho lượt này.
> **Đổi trọng tâm cho MSW:** thay vì chỉ phản biện chẩn đoán, hãy **review các bản sửa dưới đây** (đọc diff thật, chạy test).
> Kiểm chứng: `.venv/bin/python -m pytest -q` → **46 passed** (29 test cũ + 17 test mới trong `tests/test_code_review_fixes.py`).

### Bản sửa từng lỗi

| ID | File đã sửa | Cách sửa thực tế | Test hồi quy |
|---|---|---|---|
| CR1 | `app/enrollment.py` | `with lock:` thay cho `lock.acquire() and False` + `acquire()` lần 2 | `test_cr1_backfill_with_real_lock_does_not_deadlock` (Lock thật, timeout 5 s) |
| CR2 | `app/pipeline.py`, `app/main.py` | Thêm `Pipeline(recognize=False)`: chỉ render khung xem trước, không `_process`, không clip, không liveness. `main.py` bật chế độ này khi `isolated`. **Không** bỏ `Camera` ở tiến trình chính vì guided enrollment (`guided.py` đọc `ctx.camera`) và `/video.mjpg` vẫn cần (trả lời một phần Q1). | `test_cr2_pipeline_in_isolated_mode_never_recognizes` |
| CR3 | `app/pipeline.py`, `app/hrm.py`, `app/db.py` | Event luôn `hrm_status="skipped"`; ledger là nguồn duy nhất đẩy HRM. Sau khi đẩy dòng ledger `checkin_created`, trạng thái được **phản chiếu** về event (`DB.mirror_event_hrm`) và gọi `on_update(event_id)` để tab Sự kiện vẫn hiện `sent`/`failed`. | `test_cr3_checkin_pushed_to_hrm_exactly_once_and_mirrored`, `test_cr3_pipeline_does_not_queue_events_for_hrm` |
| CR4 | `app/worker_perception.py`, `app/db.py` | Worker tạo `DB(..., cipher=BiometricCipher.from_env_or_cfg(cfg))`. Thêm `DB.decode_vec()` **fail-closed**: blob bắt đầu bằng `FG1$` mà không có khoá → `RuntimeError` rõ ràng thay vì vector rác. | `test_cr4_encrypted_embeddings_fail_closed_without_cipher`, `test_cr4_worker_db_gets_cipher` |
| CR5 | `app/clips.py`, `app/media_clips.py` | Xoá `ClipRecorder._cleanup` và `CLEANUP_EVERY`; `cleanup_clips_safe` là đường dọn duy nhất. Thêm bước dọn **file mồ côi** (không dòng `appearances` nào trỏ tới, mtime quá hạn) để không rò rỉ dung lượng; file có `legal_hold` vẫn được bảo vệ. Kết quả trả thêm `deleted_orphan`. | `test_cr5_clip_recorder_no_longer_deletes_files`, `test_cr5_orphans_expire_but_held_clips_survive` |
| CR6 | `app/ledger.py`, `app/db.py`, `app/hrm.py` | (1) Ghi dòng thu hồi `skipped` **trước**, rồi `cancel_ledger_hrm` đổi bản gốc `pending/failed → cancelled`. (2) `set_ledger_hrm` không cho `cancelled` quay về `failed` (chỉ `sent` ghi đè được). (3) Race: HRMWorker đẩy thành công rồi thấy `audit_revoked` → `requeue_revoke_after_late_sync` chuyển dòng thu hồi `skipped → pending` kèm `remote_doc_id`. Nếu `cancel` thất bại vì bản gốc vừa `sent`, `revoke_checkin` tự requeue. Trả lời Q4: chọn cập nhật có điều kiện, không khoá dòng. | `test_cr6_revoke_before_sync_cancels_original`, `test_cr6_revoke_during_inflight_push_requeues_delete` |
| CR7 | `app/db.py` | Gộp 3 dict cột vào `MIGRATED_COLUMNS`; Postgres chạy `ALTER TABLE … ADD COLUMN IF NOT EXISTS` (đổi `REAL→DOUBLE PRECISION`); phần điền giá trị mặc định tách thành `_backfill_columns()` dùng chung. | `test_cr7_postgres_init_adds_migrated_columns` (psycopg giả — **chưa chạy với Postgres thật**, venv không có `psycopg`) |
| CR8 | `app/media_clips.py` | `retention_days <= 0` → tắt xoá theo hạn (và tắt dọn mồ côi), **vẫn** xoá khi đĩa đầy. Q6 (có nên cấm giá trị này ở config) vẫn để người dùng quyết. | `test_cr8_retention_zero_keeps_clips` |
| CR9 | `app/guided.py`, `app/main.py` | Hàm `known_frontal_embeddings()` đọc qua `DB.decode_vec` (có giải mã). Bước verify **fail-closed**: không có nhân viên/mẫu frontal → lỗi `NO_FRONTAL_MSG`. Endpoint `/api/enroll/guided/start` từ chối sớm (409) khi `supplement_only` mà chưa có mẫu frontal, để người dùng không bị kẹt trong vòng lặp. | `test_cr9_*` (3 test) |
| CR10 | `app/ledger.py`, `app/main.py`, `app/pipeline.py` | Thêm `hold_clip_if_pending_audit(db, appearance_id, event_id)`, gọi ngay khi tạo appearance ở **cả hai nhánh**. Nhánh IPC: nhớ `(camera_id, track_id) → event_id` lúc check-in, lấy ra khi `appearance_closed` (bỏ qua nếu khác `employee_id`; mục cũ hơn 1 giờ bị dọn để tránh trùng `track_id` sau khi worker khởi động lại). **Trả lời Q7: nhánh Pipeline có cùng lỗi** (liên kết `event_id` nhưng không đặt hold), đã sửa. | `test_cr10_pending_audit_checkin_holds_clip`, `test_cr10_worker_track_event_mapping` |

### Điểm MSW cần phản biện kỹ

- **R1 (CR3):** Ledger dùng `idempotency_key = auto:{code}:{ngày}`, tức **mỗi ngày chỉ một check-in tự động lên HRM**. Trước đây Pipeline đẩy mọi event check-in. Đây có đúng nghiệp vụ không (ERPNext thường cần cả IN/OUT)? Nếu không đúng thì cần đổi khoá idempotency, không phải đổi cách sửa CR3.
- **R2 (CR2):** Ở chế độ isolated, tiến trình chính vẫn mở camera để phục vụ đăng ký và MJPEG. Với RTSP thì chỉ tốn thêm một luồng giải mã; với **webcam USB**, hai tiến trình không mở chung thiết bị được. Có nên cấm `isolated_worker` khi nguồn là webcam?
- **R3 (CR6):** Vẫn còn một khe rất hẹp: HRMWorker đẩy thành công nhưng tiến trình chết **trước** khi ghi `sent`. Bản gốc vẫn `cancelled`, HRM có bản ghi mồ côi. Cách sửa triệt để là gửi `idempotency_key` để HRM khử trùng, hoặc đối soát định kỳ. Đề xuất ghi vào backlog thay vì sửa ngay.
- **R4 (CR5):** Dọn file mồ côi dựa vào mtime. Clip đang archive dở có mtime mới nên an toàn, trừ khi `retention_days` rất nhỏ (< thời gian archive). Cần chặn `retention_days < 1`?
- **R5 (CR7):** Chưa chạy với Postgres thật. `ADD COLUMN IF NOT EXISTS` cần Postgres ≥ 9.6.

### Cập nhật trạng thái — ngoài lượt Claude

| ID | Trạng thái | Ghi chú |
|---|---|---|
| CR1 | Đã sửa, chờ MSW review | |
| CR2 | Đã sửa, chờ MSW review | Xem R2 |
| CR3 | Đã sửa, chờ MSW review | Xem R1 |
| CR4 | Đã sửa, chờ MSW review | |
| CR5 | Đã sửa, chờ MSW review | Xem R4 |
| CR6 | Đã sửa, chờ MSW review | Xem R3 |
| CR7 | Đã sửa, chờ MSW review | Xem R5 |
| CR8 | Đã sửa, chờ MSW review | Q6 → người dùng |
| CR9 | Đã sửa, chờ MSW review | |
| CR10 | Đã sửa, chờ MSW review | Q7 đã trả lời |

TÍN HIỆU: XONG RỒI MSW
