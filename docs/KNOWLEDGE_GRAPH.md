# Knowledge graph FaceGate (sinh bởi /understand)

PoC chạy CPU trên Windows: nhận diện khuôn mặt nhân viên tại cổng để chấm công (check-in, phút đi muộn), có chống giả mạo, chế độ ngủ, clip lịch sử và adapter HRM.

- Số node: 106, số edge: 114, số lớp: 5, số bước tour: 12
- File đầy đủ để nạp vào dashboard: `docs/knowledge-graph.json`

## Các lớp kiến trúc

### Giao diện web
Trang web điều khiển và xem trực tiếp: 6 tab, vẽ vùng cửa, bảng công, lịch sử clip.

- `app/static/index.html` — Trang HTML đơn của dashboard FaceGate, chứa các tab camera, chấm công, nhân viên, lịch sử và nạp các script.
- `app/static/app.js` — Logic frontend chính: WebSocket nhận sự kiện nhận diện, hiển thị camera feed, lời chào, đăng ký và quản lý nhân viên, tải sự kiện.
- `app/static/attendance.js` — Tải và hiển thị bảng chấm công theo khoảng thời gian chọn.
- `app/static/history.js` — Xem lịch sử nhận diện và phát lại clip video của từng sự kiện.
- `app/static/style.css` — Stylesheet cho giao diện dashboard FaceGate.
- `app/static/zone.js` — Vẽ và chỉnh vùng nhận diện (zone) trên khung video bằng canvas.

### API và trạng thái dùng chung
FastAPI: REST, WebSocket, MJPEG; state chung giữa các router; API bảng công và lịch sử.

- `app/main.py` — Điểm vào FastAPI: lifespan khởi tạo pipeline/DB/camera, phục vụ video MJPEG, WebSocket, API đăng ký, nhân viên, sự kiện, metrics và CSV.
- `app/state.py` — Trạng thái dùng chung giữa các router: Broadcaster đẩy tin nhắn tới client WebSocket từ thread bất kỳ và đối tượng ctx khởi tạo trong lifespan.
- `app/records.py` — Router FastAPI cung cấp API bảng công, xuất CSV, danh sách người và lịch sử xuất hiện kèm tải zip clip video.
- `app/config.py` — Nạp cấu hình từ file YAML và giải quyết các đường dẫn dữ liệu/mô hình cho toàn ứng dụng.
- `app/__init__.py` — File rỗng đánh dấu thư mục app là Python package.

### Xử lý nhận diện
Luồng camera → detect → tracker → quality → embedding → so khớp → chống giả mạo → vẽ khung.

- `app/camera.py` — Bọc nguồn camera/RTSP/file video trong một thread đọc nền, kèm tiện ích kiểm tra khung hình đen và kiểm tra nguồn có đọc được không; hỗ trợ ghi video.
- `app/pipeline.py` — Pipeline xử lý thời gian thực: đọc camera, phát hiện/theo dõi/nhận diện, vùng chuyển động, chống giả mạo, ghi sự kiện chấm công và phát video.
- `app/tracker.py` — Tracker IoU gán ID cho khuôn mặt giữa các frame, giữ embedding và danh tính tích lũy theo track.
- `app/engines.py` — Bao bọc các engine nhận diện khuôn mặt (OpenCV YuNet/SFace và ONNX) với lớp Face, căn chỉnh khuôn mặt và factory create_engine.
- `app/quality.py` — Đánh giá chất lượng khuôn mặt (kích thước, độ nét, tư thế) để quyết định có dùng nhận diện/đăng ký hay không.
- `app/gallery.py` — Gallery embedding trong RAM và so khớp open-set bằng cosine với ngưỡng và margin top-2.
- `app/liveness.py` — Chống giả mạo thụ động bằng hai model MiniFASNet ONNX (V2, V1SE), trung bình softmax để tính xác suất người thật.
- `app/enrollment.py` — Xử lý đăng ký nhân viên từ video: đọc frame, phát hiện, lọc chất lượng, chia theo tư thế và tạo embedding.
- `app/draw.py` — Vẽ overlay lên khung hình (bbox, nhãn tên không dấu/có dấu bằng font, trạng thái) để stream video.

### Nghiệp vụ và lưu trữ
Luật chấm công (check-in, đi muộn), SQLite, clip video, đẩy sang HRM.

- `app/attendance.py` — Logic chấm công theo check-in: mỗi nhân viên một lần/ngày, tính số phút đi muộn, vắng, lời chào và bảng công theo ca làm việc.
- `app/db.py` — Lớp lưu trữ SQLite (demo) cho nhân viên, embedding, sự kiện chấm công, lần xuất hiện và outbox HRM, có migrate nhẹ.
- `app/clips.py` — Ghi clip video cho mỗi lần khuôn mặt xuất hiện, có pre-roll/post-roll, mã hóa trong thread riêng qua hàng đợi và dọn clip cũ định kỳ.
- `app/hrm.py` — Adapter đẩy sự kiện chấm công sang HRM (mock JSONL hoặc ERPNext) với outbox và retry trong worker nền.

### Cấu hình, cài đặt và tài liệu
config.yaml, script cài đặt/chạy, công cụ tải model và benchmark, tài liệu.

- `config.yaml` — Cấu hình trung tâm của demo: camera, pipeline nhận diện, ngưỡng chất lượng, liveness, chấm công, clip, chế độ chờ, đăng ký khuôn mặt, HRM adapter và server.
- `requirements.txt` — Danh sách phụ thuộc Python: OpenCV, numpy, FastAPI, uvicorn, onnxruntime, pyyaml, pillow, python-multipart.
- `.gitattributes` — Cấu hình Git chuẩn hóa line ending (LF mặc định, CRLF cho .bat) và đánh dấu .onnx là binary.
- `run.bat` — Script khởi động: kiểm tra .venv, mở trình duyệt tại 127.0.0.1:8090 và chạy app.main.
- `setup.bat` — Script cài đặt: tạo .venv, cài requirements.txt và tải model bằng scripts/download_models.py.
- `README.md` — Tài liệu tổng quan FaceGate PoC: mục tiêu, trạng thái hiện tại, luồng nghiệp vụ, hướng dẫn xem demo trong 5 phút, rủi ro cần PM quyết định và bước tiếp theo.
- `scripts/download_models.py` — Script tải model YuNet, SFace, MiniFASNet (và tùy chọn SCRFD/ArcFace) vào thư mục models.
- `scripts/benchmark.py` — Script benchmark tốc độ phát hiện và nhúng của engine trên video.
- `docs/GIOI_THIEU_DU_AN.html` — Trang HTML giới thiệu dự án FaceGate: mục tiêu, kiến trúc và cách hoạt động.
- `models/.gitkeep` — File giữ chỗ để Git theo dõi thư mục models (chứa model ONNX tải về).

## Tour tham khảo

1. **Tổng quan dự án** — FaceGate là PoC chấm công bằng khuôn mặt chạy CPU trên Windows. README mô tả luồng xử lý từ camera tới giao diện web.
2. **Cài đặt và khởi chạy** — setup.bat tạo môi trường và tải model; run.bat khởi động server tại cổng 8090.
3. **Cấu hình trung tâm** — config.yaml chứa ngưỡng nhận diện, liveness, ca làm, chế độ ngủ, clip; app/config.py nạp file này.
4. **Điểm vào của ứng dụng** — app/main.py dựng FastAPI, khai báo các endpoint, MJPEG/WebSocket và khởi tạo các thành phần.
5. **Nguồn video** — camera.py đọc webcam/file/RTSP trong thread riêng, có watchdog khung đen và chế độ idle.
6. **Vòng xử lý nhận diện** — pipeline.py là trung tâm: detect, track, kiểm tra chất lượng, embedding, định danh, sinh sự kiện, chế độ ngủ.
7. **Bám mặt và so khớp** — tracker.py gán khung giữa các lần detect (IoU + vận tốc), gallery.py so khớp open-set bằng ngưỡng và margin.
8. **Chống giả mạo** — liveness.py chạy MiniFASNet để chặn ảnh in hoặc ảnh trên màn hình điện thoại.
9. **Chấm công** — attendance.py tính check-in đầu tiên mỗi ngày và số phút đi muộn; records.py mở API bảng công và lịch sử.
10. **Lưu trữ và tích hợp** — db.py (SQLite), clips.py (clip mỗi lần xuất hiện), hrm.py (mock/ERPNext với outbox + retry).
11. **Giao diện web** — index.html và các file JS dựng 6 tab: Trực tiếp, Chấm công, Lịch sử, Đăng ký, Nhân viên, Sự kiện.
12. **Tài liệu giới thiệu** — Tài liệu HTML nêu bài toán đã giải, so sánh với đề xuất ban đầu và giới hạn của PoC.
