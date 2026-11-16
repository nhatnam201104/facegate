# FaceGate – Hệ thống chấm công khuôn mặt tại cổng (Kiến trúc v4)

**FaceGate** là giải pháp nhận diện khuôn mặt chấm công tại cổng phục vụ phần HRM của dự án *camera nhận diện nhân viên và hàng hóa*. Camera nhận diện nhân viên theo thời gian thực (hỗ trợ đa góc tới **±90°** và tối đa **10 khuôn mặt đồng thời**), kiểm tra người thật chống giả mạo, ghi nhận check-in qua sổ cái bất biến và đồng bộ hai chiều sang HRM (**ERPNext** / mock).

Hệ thống chạy hoàn toàn trên **CPU phổ thông** (không cần GPU riêng), hỗ trợ triển khai trên **Linux Server** (Ubuntu/Debian — ưu tiên vận hành thực tế với `MediaMTX`, `tmpfs` và `chrony` NTP) lẫn **Windows 10/11** (phát triển và chạy demo). Nguồn video hỗ trợ **TP-Link Tapo C310 / Camera IP (RTSP qua MediaMTX)**, webcam (`/dev/video0` hoặc chỉ số `0`) hoặc file video quay sẵn.

> Cập nhật: 16/11/2026 · Phiên bản kiến trúc: **v4 (Clean-Slate 3-Plane Architecture)** · Bộ test tự động: **46/46 pass (`pytest`)**, đang chờ đo kiểm thực địa pha `K0` trên phần cứng Tapo C310 trước khi vận hành chính thức.

---

## Đọc gì trước (khoảng 15–20 phút)

| Thứ tự | Tài liệu | Nội dung | Thời gian |
|---|---|---|---|
| 1 | `README.md` này | Tổng quan, trạng thái tính năng, kiến trúc v4, chạy nhanh & các quyết định cho PM | 5 phút |
| 2 | [docs/GIOI_THIEU_DU_AN.html](docs/GIOI_THIEU_DU_AN.html) | Giới thiệu dự án, kiến trúc 3 mặt phẳng, công nghệ hiện tại, so sánh với đề xuất ban đầu & lộ trình K0–K5 | 10 phút |
| 3 | [docs/HUONG_DAN_KY_THUAT.html](docs/HUONG_DAN_KY_THUAT.html) | Hướng dẫn cài đặt Linux/Windows, MediaMTX, cấu hình v4, benchmark, replay `K0` & cấu trúc mã nguồn | 5 phút |
| (tham khảo) | [docs/BAO_CAO_K0_5_PM.md](docs/BAO_CAO_K0_5_PM.md) | Khung báo cáo số liệu thực địa `K0` và 4 quyết định cần PM/Pháp chế chốt tại cổng `K0.5` | |
| (tham khảo) | [deploy/vlan-ntp-chrony.md](deploy/vlan-ntp-chrony.md) | Quy trình chuẩn bị phần cứng Tapo C310, cô lập VLAN và đồng bộ NTP `chrony` | |

> Các file `.html` nên mở bằng trình duyệt (double-click sau khi tải repo) để xem đầy đủ định dạng và mục lục.

---

## Trạng thái hiện tại

| Hạng mục | Trạng thái |
|---|---|
| Nhận diện đa góc tới **±90°** (`YuNet` + `3DDFA_V2` + `EdgeFace-XS`), batch tối đa **10 khuôn mặt** trên CPU | ✅ Đã triển khai |
| Báo `"Unknown"` khi không đạt rào cứng kép (`LLR >= tau_threshold` và `margin >= μ(yaw)`), không đoán tên | ✅ Đã triển khai |
| Chống tráo tên khi đi giao cắt (`Crossing Quarantine`) & ngữ cảnh cơ thể (`YOLOX-Nano` Viewport-Aware) | ✅ Đã triển khai |
| Đăng ký khuôn mặt có hướng dẫn tới **7 góc** (5 góc chữ thập + 2 góc nghiêng ~80°), tùy chọn WASM soi gương `0 ms`, HR duyệt | ✅ Đã triển khai |
| Chống giả mạo thụ động (`MiniFASNet V2 + V1SE`) kết hợp phát hiện màn hình/điện thoại (`YOLOX-Nano`) | ✅ Đã triển khai (giảm rủi ro phần mềm) |
| Media Plane (`MediaMTX` Single RTSP Puller, lọc bỏ âm thanh `-an`, WebRTC WHEP + `<canvas>`, vòng đệm `tmpfs` fMP4) | ✅ Đã triển khai |
| Clip bằng chứng MP4 H.264 gốc sạch + sidecar `.meta.json` ký `HMAC-SHA256`, xuất MP4 burn-in FFmpeg & khóa `legal_hold` | ✅ Đã triển khai |
| Cô lập tiến trình AI (`worker_perception.py`), Fail-safe `/api/health`, lịch thức theo ca & chế độ chờ tiết kiệm CPU | ✅ Đã triển khai |
| Sổ cái chấm công bất biến (`attendance_ledger`), hậu kiểm ngoại lệ 5%, nhập bù dự phòng & đồng bộ ERPNext hai chiều | ✅ Đã triển khai (ERPNext cần thử nghiệm với server thật) |
| Bảo mật vận hành: mã hóa sinh trắc học `AES-256-GCM`, phân quyền RBAC 3 vai trò (`viewer`/`hr`/`admin`), `audit_logs` & PostgreSQL đa cổng | ✅ Đã triển khai |
| Số liệu thực địa pha `K0` trên **TP-Link Tapo C310** (FAR/FRR, APCER/BPCER, độ trễ `<canvas>`, tải 10 mặt thực tế) | 🟡 Đã có sẵn công cụ đo (`record_k0_bundle.py`, `replay_eval.py`, `benchmark.py`), cần quay footage hiện trường |
| Nhận diện hàng hóa / SKU & zone sản xuất (Phase 2) | ⬜ Chưa làm (đã chuẩn hóa schema `TrackedObject` chờ sẵn) |

---

## Kiến trúc & Công nghệ hiện tại (v4)

Hệ thống tách khỏi mô hình đơn luồng ban đầu thành **3 Mặt phẳng Độc lập**:

1. **Media Plane (`deploy/mediamtx.yml`, `app/media_clips.py`, `app/static/whep_canvas.js`)**
   - **`MediaMTX` (MIT)** làm **Single RTSP Puller** duy nhất nối camera (tránh quá tải giới hạn `2+2` luồng của Tapo C310).
   - Ingest qua `ffmpeg -map 0:v:0 -c:v copy -an` để loại bỏ hoàn toàn kênh âm thanh `pcm_alaw` và không mã hóa lại video.
   - Phát trực tiếp **WebRTC (WHEP)** kèm lớp phủ HTML5 `<canvas>` đồng bộ bằng `requestVideoFrameCallback` (`receiveTime + overlay_offset_ms`); tự động fallback sang MJPEG (`/video.mjpg`) khi chạy demo webcam cục bộ không bật MediaMTX.
   - Ghi vòng đệm `fMP4` 1 giây lên RAM `tmpfs` (`/dev/shm/facegate_ring`, tự xóa sau 10 phút) để trích xuất clip MP4 H.264 gốc sạch kèm tệp `.meta.json` ký `HMAC-SHA256`; hỗ trợ kết xuất MP4 burn-in qua FFmpeg theo yêu cầu khi khiếu nại.
2. **Perception Process (`app/worker_perception.py`, `app/ipc.py`, `app/decision.py`, `app/tracker.py`)**
   - Có thể chạy trong tiến trình riêng cô lập lỗi (`FACEGATE_ISOLATED_WORKER=1`), giao tiếp NDJSON qua Unix Domain Socket / TCP theo schema `TrackedObject` (dùng chung cho cả Phase 2 SKU).
   - Áp dụng **Lịch thức theo ca** (`power.active_windows`) giữ nóng giải mã `/stream1` (`0 ms` chờ keyframe GOP) trong giờ cao điểm và chuyển chế độ chờ canh chuyển động vùng cửa ngoài giờ.
   - **Two-Stage ROI 1:1**: Thu nhỏ khung `/stream1` (`2304×1296`) về `640px` dùng **`YOLOX-Nano`** (Apache-2.0) tìm vùng người, sau đó cắt vùng Đầu–Vai tỷ lệ **1:1 ngay trên khung 3MP gốc** đưa vào **`YuNet`** (Apache-2.0) → **`3DDFA_V2`** (MIT, ước lượng góc 3D ±90°) → **`EdgeFace-XS`** (MIT, batch tới 10 mặt trên `CPUExecutionProvider` hoặc tùy chọn `OpenVINOExecutionProvider`) → **`MiniFASNet V2 + V1SE`** (Apache-2.0).
   - **Lõi quyết định liên tục**: Tích lũy điểm `LLR` 4 tham số có **Novelty Gate** ($\Delta t \ge 250\text{ ms}$ hoặc $|\Delta\text{yaw}| \ge 8^\circ$), giữ nguyên rào cứng kép (`tau_threshold` và `margin μ(yaw)` siết chặt theo góc nghiêng), `Crossing Quarantine` chống tráo tên khi đi giao cắt và `Viewport-Aware Context` miễn phạt cắt vai ở đáy khung hình. Cấm tuyệt đối `auto_enroll_online`.
3. **Control & Business Plane (`app/main.py`, `app/ledger.py`, `app/records.py`, `app/security.py`, `app/hrm.py`, `app/db.py`)**
   - **Sổ cái bất biến `attendance_ledger`** (`checkin_created`, `checkin_revoked`, `checkin_manual`) có `idempotency_key`, **Audit-by-Exception 5%** (hậu kiểm ca `profile_verified` / sát biên / gần cảnh báo spoof) và đồng bộ tạo/hủy chứng từ hai chiều sang **ERPNext** (`Employee Checkin`).
   - **Fail-safe & Legal Hold**: Endpoint `/api/health` phát hiện mất camera / worker / ffmpeg / đầy đĩa trong `≤ 5 giây`, tự động bật banner nhắc dùng kênh **Chấm công Dự phòng / Nhập bù**; bộ dọn đĩa bảo vệ tuyệt đối clip có `legal_hold = 1`.
   - **Bảo mật & Mở rộng**: Lưu trữ **SQLite WAL** (mặc định 1 cổng) hoặc **PostgreSQL** (`storage.database_url` cho nhiều cổng), mã hóa embedding và ảnh mẫu ở trạng thái nghỉ bằng **`AES-256-GCM`**, phân quyền **RBAC 3 vai trò** (`viewer`, `hr`, `admin`) và nhật ký `audit_logs`.

> Toàn bộ mô hình vận hành mặc định (`YuNet`, `EdgeFace-XS`, `3DDFA_V2`, `YOLOX-Nano`, `MiniFASNet`, `MediaPipe Face Landmarker`) đều dùng giấy phép thương mại hóa được (**Apache-2.0 / MIT**). Backend `onnx` (`SCRFD + ArcFace` của InsightFace) chỉ giữ lại để nghiên cứu/benchmark.

---

## Luồng nghiệp vụ

```
Nhân viên làm theo hướng dẫn 7 góc (5 chữ thập + 2 nghiêng ~80°, có tùy chọn bổ sung)
   ─►  HR bấm "Duyệt"  ─►  Nhân viên đi qua cổng  ─►  Two-Stage ROI + EdgeFace-XS + MiniFASNet
   ─►  Tích lũy LLR + Crossing Quarantine  ─►  Ghi sổ cái (checkin_created)  ─►  Đẩy HRM / ERPNext
```

- Mỗi nhân viên có **một check-in hiệu lực mỗi ngày** (nếu HR thu hồi qua `checkin_revoked`, có thể nhập bù `checkin_manual`). Các lần đi qua sau chỉ hiện *"Đã chấm công hôm nay, check-in lúc …"*.
- Ca mặc định: `08:00 – 17:00`, trễ cho phép `5 phút`, cấu hình trong `config.yaml`.
- Khi phát hiện giả mạo (ảnh/màn hình/MiniFASNet), hệ thống không chấm công, ghi cảnh báo và tự động gắn `legal_hold = 1` cho clip bằng chứng.
- Khi xóa nhân viên, dữ liệu sinh trắc học bị xóa và camera ngừng nhận diện ngay (kể cả ở tiến trình worker qua lệnh `reload_gallery`), trong khi lịch sử chấm công và clip vẫn giữ để đối soát.

Giao diện web gồm 6 tab: **Trực tiếp** (WebRTC `<canvas>` / MJPEG + vẽ Vùng cửa), **Chấm công** (bảng công, nhập bù dự phòng, hàng chờ hậu kiểm 5%, thu hồi công), **Lịch sử** (clip MP4/WebM, lớp phủ sidecar, xuất MP4 đóng dấu, khóa `legal_hold`, tải ZIP), **Đăng ký**, **Nhân viên**, và **Sự kiện & đánh giá** (gán nhãn tính FAR/FRR/latency).

---

## Chạy nhanh trong 5 phút

Yêu cầu: **Linux** (Ubuntu/Debian…) hoặc **Windows 10/11**, Python **3.10+** (hoặc `uv`), có webcam, file video hoặc camera RTSP.

### Trên Linux
```bash
chmod +x setup.sh run.sh
./setup.sh      # tạo .venv, cài thư viện (opencv-python-headless, cryptography, ...) và tải bộ model v4
./run.sh        # khởi động tại http://127.0.0.1:8090 (dùng NO_BROWSER=1 ./run.sh nếu chạy server headless)
```

Chạy đầy đủ **chế độ máy chủ v4** (MediaMTX + Tiến trình AI cô lập + Bảo mật):
```bash
# 1. Chạy MediaMTX + vòng đệm tmpfs (cần cài binary mediamtx và ffmpeg)
CAM_USER=admin CAM_PASS=matkhau CAM_IP=192.168.50.10 ./scripts/run_mediamtx.sh

# 2. Đặt biến môi trường bảo mật và bật worker suy luận độc lập
export FACEGATE_BIOMETRIC_KEY="chuoi-bi-mat-32-byte-hoac-passphrase"
export FACEGATE_HMAC_SECRET="chuoi-bi-mat-ky-sidecar-clip"
export FACEGATE_AUTH_SECRET="chuoi-bi-mat-rbac-token"   # bắt buộc nếu bật security.auth_enabled: true
FACEGATE_ISOLATED_WORKER=1 ./run.sh
```
> Nếu dùng webcam USB trên Linux bị từ chối quyền `/dev/video0`, thêm user vào nhóm `video`: `sudo usermod -aG video $USER` rồi đăng nhập lại. Khi mở cho LAN (`server.host: 0.0.0.0`), hãy bật `security.auth_enabled: true` và cấu hình các khóa môi trường trên.

### Trên Windows
1. Double-click **`setup.bat`** (tạo `.venv`, cài thư viện và tải bộ model v4 vào `models/`).
2. Double-click **`run.bat`** (mở http://127.0.0.1:8090).

### Thao tác nhanh trên giao diện web
1. Tab **Đăng ký**: nhập mã và họ tên, chọn *Quay từ camera cổng* rồi xoay mặt theo hướng dẫn (hoặc tải video ngắn).
2. Tab **Nhân viên**: bấm **Duyệt**.
3. Tab **Trực tiếp**: đứng trước camera. Màn hình hiện thẻ chào kèm trạng thái đúng giờ/đi muộn; dùng nút **Vùng cửa** nếu muốn khoanh vùng đánh thức khi ở chế độ chờ.
4. Thử đưa ảnh trên điện thoại trước camera: khung chuyển đỏ báo **GIẢ MẠO** và lưu clip kèm cờ `legal_hold`.
5. Xem bảng công, hàng chờ hậu kiểm 5% hoặc nhập bù dự phòng ở tab **Chấm công**; xem lại clip & xuất MP4 đóng dấu ở tab **Lịch sử**.

Chi tiết kịch bản demo, benchmark (`--ten-faces-bench`), thu thập gói `K0` và vận hành xem tại [docs/HUONG_DAN_KY_THUAT.html](docs/HUONG_DAN_KY_THUAT.html).

---

## Rủi ro chính và việc cần PM / Pháp chế quyết định (Cổng `K0.5`)

| # | Vấn đề | Cần quyết định hoặc hỗ trợ |
|---|---|---|
| 1 | **Chưa có số liệu thực địa trên TP-Link Tapo C310.** Các bộ kiểm thử đơn vị/tích hợp đã pass (`46 passed`), nhưng số liệu thực tế về độ mờ ở `15 fps`, FAR ghép chéo và APCER/BPCER cần đo trên footage tại cổng. | Thực hiện pha `K0` theo `deploy/vlan-ntp-chrony.md`, chạy `scripts/record_k0_bundle.py` + `scripts/replay_eval.py` và điền `docs/BAO_CAO_K0_5_PM.md`. |
| 2 | **Lựa chọn bố cục cổng & phần cứng (`A / B / C / C'`).** Đi ngang tự do (±90°) ở 15 fps dễ nhòe chuyển động hơn dẫn hướng nhìn thẳng (±40°). | PM chọn giữ thuần Tapo C310 (kèm đèn ≥ 250–300 lux + hậu kiểm 5%), thêm thẻ/QR, nâng cấp IP camera doanh nghiệp 25–30 fps, hoặc dùng đầu đọc IR/3D chuyên dụng nếu tính lương. |
| 3 | **Chống giả mạo phần mềm chỉ giảm rủi ro.** Video chất lượng cao hoặc màn hình lớn vẫn có thể vượt qua phân tích RGB đơn thuần. | Nếu dùng trực tiếp để tính lương, cân nhắc thiết bị IR/3D hoặc xác thực hai yếu tố; đồng thời bật bộ tấn công ISO/IEC 30107-3 ở `K0`. |
| 4 | **Pháp chế dữ liệu sinh trắc học & bằng chứng đối soát lương.** | Pháp chế duyệt mẫu đơn đồng ý (`deploy/consent-template-k0.md`), chính sách không thu âm (`-an`), hạn lưu clip 30 ngày và giá trị pháp lý nội bộ của clip MP4 gốc + sidecar `HMAC-SHA256`. |
| 5 | **Thử nghiệm ERPNext thật & hạ tầng mạng.** Adapter ERPNext hai chiều (tạo/hủy `Employee Checkin`) đã viết nhưng chưa nối server ERP thật. | Bố trí môi trường ERPNext staging và Linux server đặt tại văn phòng/LAN nội bộ cùng VLAN cô lập cho camera. |

---

## Cấu trúc dự án & tài liệu

```
README.md                                    ← tổng quan hệ thống, công nghệ & hướng dẫn nhanh v4
config.yaml                                  ← cấu hình trung tâm (camera, pipeline, LLR, tracking, power, security, hrm, storage)
setup.sh / run.sh / setup.bat / run.bat      ← script cài đặt & khởi chạy trên Linux và Windows
app/                                         ← mã nguồn backend (FastAPI, worker_perception, MediaMTX clips, LLR, ledger, security)
app/static/                                  ← giao diện web thuần HTML/CSS/JS (WebRTC WHEP <canvas>, WASM mirror guide, 6 tab)
deploy/
  ├── mediamtx.yml                           ← cấu hình MediaMTX Single Puller, lọc -an & vòng đệm tmpfs fMP4
  ├── vlan-ntp-chrony.md                     ← trình tự lắp đặt Tapo C310 (RJ45, Day Mode, VLAN + chrony NTP)
  └── consent-template-k0.md                 ← mẫu đơn đồng ý sinh trắc học pha K0
scripts/
  ├── download_models.py                     ← tải & kiểm tra SHA-256 bộ model thương mại v4 (+ --onnx để benchmark)
  ├── benchmark.py                           ← đo tốc độ decode 3MP, ROI, Laplacian blur & batch 10 khuôn mặt (--ten-faces-bench)
  ├── record_k0_bundle.py                    ← thu thập gói dữ liệu thực địa K0 (manifest + observations.jsonl + crops)
  ├── replay_eval.py                         ← đánh giá ngoại tuyến FAR (Wilson CI 95%), APCER/BPCER ISO/IEC 30107-3
  └── run_mediamtx.sh / run_mediamtx.bat     ← khởi chạy MediaMTX kèm vòng đệm tmpfs
tests/                                       ← 10 file test pytest (46 test cases: IPC, LLR, ledger, quarantine, API v4...)
docs/
  ├── GIOI_THIEU_DU_AN.html                  ← giới thiệu dự án, so sánh phương án cũ vs v4, lộ trình K0–K5
  ├── HUONG_DAN_KY_THUAT.html                ← hướng dẫn cài đặt, cấu hình, kịch bản demo, benchmark & cấu trúc code
  ├── BAO_CAO_K0_5_PM.md                     ← bảng đối chiếu số liệu K0 & 4 quyết định chốt tại K0.5
  └── PHAN_BIEN_KIEN_TRUC.md                 ← biên bản 6 vòng phản biện kiến trúc đóng 35/35 lỗ hổng
specs/003-clean-slate-architecture-v4/       ← hồ sơ thiết kế chi tiết v4 (spec, plan, research, data-model, contracts, tasks)
```
