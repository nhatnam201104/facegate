# FaceGate – Demo PoC camera nhận diện nhân viên tại cổng

Bản demo chạy **cục bộ trên Windows, chỉ dùng CPU**, theo các phương án kỹ thuật đã đề xuất:

```
Webcam / file video / RTSP ──► Reader thread (giữ frame mới nhất)
        ► Detect 3–5 FPS (YuNet hoặc SCRFD) ► IoU tracker ► Quality filter
        ► Align + Embedding (SFace hoặc ArcFace) ► Gom embedding theo track
        ► So khớp open-set (ngưỡng + margin top-2, xác minh lại liên tục)
        ► Chống giả mạo MiniFASNet (ảnh in / màn hình điện thoại) ► Rule chấm công (check-in/out, dedup)
        ► SQLite + snapshot + clip mỗi lần xuất hiện ► HRM adapter (mock | ERPNext) qua outbox + retry
        ► FastAPI: MJPEG video + WebSocket + REST  ►  Giao diện web
```

## 1. Yêu cầu
- Windows 10/11, **Python 3.10 trở lên** (khi cài nhớ tick *Add Python to PATH*).
- Webcam laptop, hoặc một file video quay ở cổng, hoặc camera IP có RTSP.

## 2. Cài đặt (một lần)
Clone repo (hoặc giải nén), mở thư mục, **double-click `setup.bat`**. Script sẽ:
1. tạo môi trường ảo `.venv`;
2. cài thư viện trong `requirements.txt`;
3. tải model YuNet + SFace (OpenCV Zoo) và MiniFASNet chống giả mạo vào thư mục `models/`.

> Model (`models/*.onnx`) và dữ liệu chạy (`data/`) không nằm trong git: model do `setup.bat` tải về, `data/` tự tạo khi chạy lần đầu.

Muốn benchmark thêm backend ONNX (SCRFD + ArcFace), mở terminal trong thư mục và chạy:
```bat
.venv\Scripts\python scripts\download_models.py --onnx
```
> Nếu tải model lỗi: mở các link trong `scripts/download_models.py` bằng trình duyệt, tải về và đặt vào `models/`.

## 3. Chạy
**Double-click `run.bat`**. Trình duyệt sẽ mở http://127.0.0.1:8090.

**Đổi nguồn nhận diện ngay trên web:** ở tab Trực tiếp, chọn Webcam / File video / Camera RTSP rồi bấm **Đổi nguồn**. Khi chọn file video, webcam được tắt hẳn. Video tải lên lưu ở `data/sources/` (chỉ giữ file đang dùng). Nguồn đổi trên web chỉ có hiệu lực tới khi tắt app; mỗi lần mở lại `run.bat` sẽ dùng `camera.source` trong `config.yaml`.

Thay đổi ngưỡng, backend hay nguồn mặc định thì sửa `config.yaml`, sau đó tắt cửa sổ đang chạy và mở lại `run.bat`.

| Muốn | Sửa trong `config.yaml` |
|---|---|
| Dùng webcam khác | `camera.source: 1` |
| Chạy trên footage đã quay | `camera.source: data/footage.mp4` |
| Camera Hikvision | `camera.source: rtsp://user:pass@IP:554/Streaming/Channels/102` (102 = substream) |
| Đổi backend | `pipeline.backend: opencv` hoặc `onnx` |
| Chế độ chờ khi không có người | `power.sleep`, `power.idle_seconds` (mặc định 30 giây); tăng `power.motion_threshold` nếu ánh sáng nhấp nháy làm app tự bật |
| Chỉ check-in khi đi qua đường ảo theo chiều vào | `events.direction: line`, chỉnh `events.line` và `events.in_when` |
| Đẩy sang ERPNext | `hrm.adapter: erpnext` và điền `url`, `api_key`, `api_secret` |

## 4. Kịch bản demo
1. **Đăng ký**: tab *Đăng ký* → nhập mã và họ tên → tải video 6–10 giây (quay bằng điện thoại: nhìn thẳng, xoay trái/phải, ngẩng/cúi nhẹ) **hoặc** chọn *Quay từ camera cổng*. Hệ thống tự lọc frame, chia theo góc nhìn, bỏ frame trùng, chọn 5–15 frame và báo cáo. Video gốc bị xóa sau khi xử lý.
2. **Duyệt**: tab *Nhân viên* → bấm **Duyệt**. Chỉ nhân viên đã duyệt mới được nhận diện. Hệ thống sẽ cảnh báo nếu khuôn mặt giống một nhân viên khác đã có.
3. **Trực tiếp** (màn hình máy chấm công): đứng trước camera ~1 giây. Lần đầu trong ngày sẽ hiện thẻ *Xin chào… · Check-in HH:MM · Đúng giờ / Đi muộn N phút* kèm âm báo; các lần sau trong ngày chỉ hiện *Đã chấm công hôm nay, check-in lúc …* (không ghi thêm). Cột bên phải: số người có mặt, đi muộn, giả mạo hôm nay. Màu khung:
   - **xanh lá**: đã nhận diện và đã xác minh người thật (nhãn có `thật 0.xx`);
   - **cam**: đang xác minh danh tính hoặc đang kiểm tra người thật;
   - **đỏ**: Unknown, hoặc **GIẢ MẠO** (ảnh/màn hình) — không chấm công, ghi sự kiện cảnh báo;
   - **xám**: chất lượng thấp, kèm lý do (mờ, mặt nhỏ, quay ngang…).
4. **Chấm công**: mỗi nhân viên chỉ lưu **giờ check-in đầu tiên trong ngày** và **số phút đi muộn** (tính từ giờ vào ca, trong khoảng `grace_minutes` coi như đúng giờ). Trạng thái *Đúng giờ / Đi muộn / Vắng / Chưa đến*, tổng hợp theo nhân viên (số ngày, số lần muộn, tổng phút muộn, vắng), nút *Xuất CSV*. Giờ vào ca cấu hình ở mục `attendance` trong `config.yaml`.
5. **Lịch sử**: mỗi lần một khuôn mặt xuất hiện trước camera được lưu kèm **clip video** (có khung nhận diện). Lọc theo người, loại (nhân viên / người lạ / giả mạo) và khoảng ngày; xem clip, tải từng clip hoặc *Tải tất cả clip (ZIP)* kèm `danh_sach.csv`. Từ bảng công bấm *Xem clip* để mở đúng người, đúng ngày.
6. **Sự kiện & đánh giá**: gán nhãn *Đúng / Nhầm người / Là NV / Người lạ* (và *Đúng giả mạo / Người thật* cho cảnh báo giả mạo). Hệ thống tính **false accept, false reject, unknown rate, tỉ lệ chặn nhầm người thật, latency p50/p90**. Nút *Xuất CSV* để lấy số liệu báo cáo.
7. **Xóa nhân viên** ở tab *Nhân viên*: camera ngừng nhận diện người đó ngay, kể cả khi họ đang đứng trước camera. Dữ liệu khuôn mặt bị xóa; lịch sử chấm công và clip vẫn giữ (lọc được với nhãn *đã xóa*).
8. Ở chế độ `hrm.adapter: mock`, mỗi check-in được ghi vào `data/hrm_mock.jsonl` đúng định dạng Employee Checkin.

## 5. Benchmark backend
```bat
.venv\Scripts\python scripts\benchmark.py data\footage.mp4
```
Lệnh này in thời gian detect, thời gian embed và số mặt đạt chất lượng của từng backend trên cùng một video. Kết hợp số liệu này với nhãn ở tab *Sự kiện* để chọn model.

## 6. Cấu trúc
```
app/
  engines.py     backend nhận diện (OpenCVEngine: YuNet+SFace, OnnxEngine: SCRFD+ArcFace)
  quality.py     đánh giá kích thước, độ mờ, ánh sáng, góc mặt (heuristic)
  tracker.py     IoU tracker + trạng thái từng track
  gallery.py     gallery trong RAM, so khớp ngưỡng + margin top-2
  pipeline.py    vòng xử lý chính, rule chấm công, ghi lịch sử, chế độ chờ, vẽ khung
  camera.py      đọc webcam / file / RTSP trong thread riêng, tự kết nối lại
  liveness.py    chống giả mạo MiniFASNet (gộp V2 + V1SE)
  attendance.py  giờ vào ca, check-in mỗi ngày, đi muộn/vắng, bảng công
  clips.py       ghi clip WebM mỗi lần xuất hiện (thread riêng, pre/post-roll, tự xóa clip cũ)
  records.py     API bảng công + lịch sử + tải ZIP
  enrollment.py  đăng ký từ video: lọc → chia góc → bỏ trùng → kiểm tra trùng người
  hrm.py         adapter HRM (mock, ERPNext) + outbox/retry
  db.py          SQLite (khi lên server sẽ chuyển sang PostgreSQL + pgvector)
  main.py        FastAPI: REST, WebSocket, MJPEG, đổi nguồn video, vùng cửa
  state.py       trạng thái dùng chung giữa các router
  static/        giao diện web (app.js, attendance.js, history.js, zone.js)
scripts/         tải model, benchmark
data/            DB, snapshot, ảnh đăng ký, clips/, settings.json (tự tạo khi chạy, không push)
```

## 7. Lưu ý quan trọng
- **License model**: YuNet và SFace thuộc OpenCV Zoo (Apache-2.0). Weights SCRFD/ArcFace của InsightFace **chỉ dùng cho nghiên cứu/benchmark**; muốn dùng thương mại phải có license riêng.
- **Ngưỡng** (`recognition.thresholds`) là giá trị khởi điểm, cần hiệu chỉnh bằng footage thật. Sau khi đổi backend phải **đăng ký lại**, vì embedding được gắn `model_version`.
- **Chống giả mạo là phần mềm, chỉ giảm rủi ro**: MiniFASNet (Minivision, Apache-2.0) chặn tốt ảnh in và ảnh trên màn hình điện thoại, nhưng video/màn hình chất lượng cao hoặc mặt nạ vẫn có thể lọt. Nếu dữ liệu dùng để **tính lương**, nên dùng máy chấm công có camera IR/3D. Chỉnh `liveness.threshold` (mặc định 0.70) theo điểm `thật 0.xx` hiện trên khung: tăng để chặn chặt hơn, giảm nếu người thật bị báo giả mạo.
- **Chế độ chờ**: sau `idle_seconds` không thấy khuôn mặt, app ngừng nhận diện/vẽ khung/mã hoá video và chỉ đo chuyển động trên ảnh 160×90 (4 lần/giây); badge chuyển thành *CHẾ ĐỘ CHỜ*, hình cập nhật mỗi 2 giây. Để không bị lá cây, bóng đổ, đèn nhấp nháy đánh thức: (1) chỉ tính chuyển động trong **vùng cửa** — bấm nút *Vùng cửa* ở tab Trực tiếp, chấm các góc lối đi lên hình rồi *Lưu vùng* (lưu ở `data/settings.json`); (2) khi có chuyển động trong vùng, app tìm khuôn mặt trong `confirm_seconds` (1 giây) — thấy mặt mới bật lại, không thấy thì ngủ tiếp (rê chuột lên badge để xem số lần đã bỏ qua). Khi đang chạy, chỉ khuôn mặt mới giữ app thức. Clip vẫn có 2 giây trước khi người xuất hiện. Webcam vẫn mở (đèn vẫn sáng) để không bỏ sót người đến.
- **Clip video** lưu tại `data/clips/`, tự xóa sau `clips.retention_days` ngày (mặc định 30).
- **Dữ liệu khuôn mặt là dữ liệu sinh trắc học**: chỉ dùng với nhân viên đã đồng ý. Thư mục `data/` chứa ảnh khuôn mặt, không chia sẻ ra ngoài.
- Webcam laptop cho kết quả tốt hơn camera cổng vì mặt to và gần. Muốn đánh giá đúng thực tế, hãy quay footage tại vị trí lắp camera thật rồi chạy với `camera.source` là file đó.
