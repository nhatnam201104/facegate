# FaceGate – Demo chấm công bằng khuôn mặt tại cổng

**FaceGate** là bản demo (PoC) cho phần HRM của dự án *camera nhận diện nhân viên và hàng hóa*. Camera ở cổng nhận ra nhân viên, kiểm tra đó là người thật (không phải ảnh hay màn hình điện thoại), rồi ghi giờ check-in và số phút đi muộn. Sau đó hệ thống gửi sự kiện sang HRM.

Bản demo chạy trên **Linux** (Ubuntu/Debian…) hoặc **Windows**, chỉ dùng CPU, không cần GPU riêng. Nguồn video có thể là webcam (`/dev/video0` hoặc chỉ số `0`), file video quay sẵn hoặc camera IP (RTSP).

> Cập nhật: 04/11/2026 · Giai đoạn: PoC, **chưa dùng cho vận hành thật**

---

## Đọc gì trước (khoảng 20 phút)

| Thứ tự | Tài liệu | Nội dung | Thời gian |
|---|---|---|---|
| 1 | README này | Dự án làm gì, đã xong đến đâu, cần PM quyết định gì | 5 phút |
| 2 | [docs/GIOI_THIEU_DU_AN.html](docs/GIOI_THIEU_DU_AN.html), **mục 1, 6 và 7** | Tóm tắt, giới hạn và rủi ro, bước tiếp theo | 10 phút |
| 3 | Cùng file đó, **mục 5** | So sánh với phương án công nghệ đã đề xuất ban đầu và lý do thay đổi | 5 phút |
| (tùy chọn) | Mục 2–4 của file đó | Kiến trúc, công nghệ, cách giải từng bài toán | |
| (tùy chọn) | [docs/HUONG_DAN_KY_THUAT.md](docs/HUONG_DAN_KY_THUAT.md) | Cài đặt, cấu hình, kịch bản demo chi tiết, cấu trúc mã nguồn | |

> File `.html` cần mở bằng trình duyệt: tải repo về rồi double-click file. Trên GitHub, file này chỉ hiện mã nguồn.

---

## Trạng thái hiện tại

| Hạng mục | Trạng thái |
|---|---|
| Nhận diện nhân viên tới góc nghiêng **±90°** (Best-shot ưu tiên góc thẳng + khớp Profile-to-Profile) | ✅ Đã có |
| Báo "Unknown" khi không đủ tin cậy, không đoán tên (ngưỡng riêng cho chính diện & góc nghiêng) | ✅ Đã có |
| Đăng ký khuôn mặt quay từ camera có hướng dẫn (**7 góc** tới ±80° + bổ sung góc 90° cho nhân viên cũ), HR duyệt | ✅ Đã có |
| Chấm công: check-in đầu tiên trong ngày, số phút đi muộn, vắng, phân tầng mức xác thực, xuất CSV | ✅ Đã có |
| Chống giả mạo **4 lớp** (MiniFASNet + phát hiện màn hình/cơ thể YOLOX-Nano + thị sai 3D + vân Moiré) | ✅ Đã có (bằng phần mềm, phân tầng kèm clip đối soát) |
| Lịch sử mỗi lần xuất hiện kèm clip video, tải ZIP | ✅ Đã có (ngoài đề xuất ban đầu) |
| Chế độ chờ khi không có người, để giảm tải CPU | ✅ Đã có (ngoài đề xuất ban đầu) |
| Tích hợp HRM | 🟡 Bản giả lập đã chạy. Bản ERPNext đã viết nhưng chưa thử với ERP thật |
| Camera Hikvision qua RTSP | 🟡 Code đã hỗ trợ, chưa thử với camera thật |
| Nhận diện 10 khuôn mặt cùng lúc | 🟡 Chưa kiểm chứng (đang giới hạn 3) |
| Số liệu độ chính xác trên footage thật | 🟡 Đã có công cụ đo, chưa có số liệu |
| Nhận diện hàng hóa/SKU, khu vực sản xuất | ⬜ Chưa làm, đề xuất tách thành phase 2 |

---

## Luồng nghiệp vụ

```
Nhân viên làm theo hướng dẫn 5 góc ─►  HR bấm "Duyệt"  ─►  Nhân viên đứng trước camera ~1 giây
   ─►  Hệ thống nhận diện + kiểm tra người thật
   ─►  Ghi check-in (lần đầu trong ngày) + số phút đi muộn  ─►  Gửi sang HRM
```

- Mỗi nhân viên chỉ có **một lần check-in mỗi ngày**. Các lần đi qua sau đó không được ghi thêm.
- Ca mặc định bắt đầu lúc 08:00 và đến trễ dưới 5 phút vẫn tính đúng giờ. Cả hai giá trị đều chỉnh được.
- Khi phát hiện giả mạo, hệ thống không chấm công, ghi một cảnh báo kèm tên người bị giả mạo và lưu clip làm bằng chứng.
- Khi xóa một nhân viên, camera ngừng nhận ra người đó ngay. Lịch sử chấm công của họ vẫn được giữ.

Giao diện web có 6 tab: **Trực tiếp** (màn hình máy chấm công), **Chấm công** (bảng công), **Lịch sử** (clip), **Đăng ký**, **Nhân viên** và **Sự kiện & đánh giá** (gán nhãn đúng/sai để tính độ chính xác).

---

## Xem demo trong 5 phút

Cần máy **Linux** (Ubuntu/Debian…) hoặc **Windows 10/11** đã cài Python 3.10 trở lên (hoặc `uv`) và có webcam / file video.

### Trên Linux
```bash
chmod +x setup.sh run.sh
./setup.sh      # chỉ chạy lần đầu: tạo .venv, cài thư viện headless và tải model ONNX
./run.sh        # khởi động server tại http://127.0.0.1:8090 (NO_BROWSER=1 ./run.sh nếu chạy headless)
```
> Nếu dùng webcam USB/laptop trên Linux mà báo không mở được `/dev/video0`, hãy thêm user vào nhóm `video`: `sudo usermod -aG video $USER` rồi đăng nhập lại. Nếu chạy trên Linux server cần truy cập từ máy khác trong mạng nội bộ, đổi `server.host: 0.0.0.0` trong `config.yaml`.

### Trên Windows
1. Double-click **`setup.bat`** (chỉ chạy lần đầu, mất vài phút để cài thư viện và tải model).
2. Double-click **`run.bat`**. Trình duyệt sẽ tự mở http://127.0.0.1:8090.

### Thao tác trên giao diện web
1. Vào tab **Đăng ký**, nhập mã và tên, chọn *Quay từ camera cổng* rồi xoay mặt nhẹ theo hướng dẫn (hoặc tải video ngắn lên).
2. Vào tab **Nhân viên** và bấm **Duyệt**.
3. Quay lại tab **Trực tiếp** và đứng trước camera. Màn hình sẽ hiện thẻ *"Xin chào… · Check-in HH:MM · Đúng giờ / Đi muộn N phút"*.
4. Thử giơ ảnh của chính mình trên điện thoại trước camera. Khung sẽ chuyển sang đỏ và báo **GIẢ MẠO**.
5. Xem kết quả ở tab **Chấm công** và tab **Lịch sử**.

Ý nghĩa màu khung: **xanh lá** là đã nhận diện và là người thật · **cam** là đang xác minh · **đỏ** là người lạ hoặc giả mạo · **xám** là ảnh kém chất lượng (mờ, mặt nhỏ, quay ngang).

Kịch bản demo đầy đủ ở [docs/HUONG_DAN_KY_THUAT.md](docs/HUONG_DAN_KY_THUAT.md), mục 4.

---

## Rủi ro chính và việc cần PM quyết định

| # | Vấn đề | Cần quyết định hoặc hỗ trợ |
|---|---|---|
| 1 | **Chưa có số liệu độ chính xác thực tế.** Mọi kết quả hiện tại được đo trên webcam laptop, nơi mặt to và gần hơn nhiều so với camera ở cổng. | Bố trí quay vài phút video tại đúng vị trí sẽ lắp camera, để đo và chỉnh ngưỡng. |
| 2 | **Chống giả mạo bằng phần mềm chỉ giảm rủi ro.** Video chất lượng cao, màn hình lớn hoặc mặt nạ vẫn có thể lọt. | Nếu dữ liệu dùng để **tính lương**, nên dùng thêm máy chấm công có camera IR/3D, hoặc thẻ/QR làm yếu tố thứ hai. |
| 3 | **Khuôn mặt là dữ liệu sinh trắc học.** | Cần nhân viên đồng ý, pháp chế xác nhận và có phân quyền truy cập. Cần phương án thay thế (thẻ/QR) cho người không đồng ý. |
| 4 | **Vị trí lắp camera** ảnh hưởng lớn nhất đến độ chính xác (ngược sáng, mặt quá nhỏ khi dùng lens góc rộng). | Thử thực tế trước khi chốt vị trí. Cân nhắc lens 4 mm thay cho 2,8 mm. |
| 5 | **Chưa có đăng nhập và phân quyền.** Hiện app chỉ mở được trên chính máy đang chạy. | Phải bổ sung trước khi mở cho mạng nội bộ. |
| 6 | **Chưa có server Linux**, nên demo đang chạy trên Windows. | Cần server, hoặc một máy xử lý đặt tại văn phòng, để triển khai thử. |

## Bước tiếp theo đề xuất

1. Quay footage tại vị trí lắp camera thật, chạy đo độ chính xác, chốt ngưỡng và model.
2. Lắp thử camera Hikvision qua RTSP, đo tốc độ, độ trễ hiện tên và mức dùng CPU.
3. Thử chống giả mạo có hệ thống với ảnh in, ảnh và video trên điện thoại.
4. Kết nối ERPNext/HRM thật và bổ sung đăng nhập, phân quyền.
5. Khi có server Linux: đóng gói Docker, tăng dần số mặt đồng thời từ 3 lên 5 rồi khoảng 10.
6. Phase 2: nhận diện hàng hóa/SKU và khu vực sản xuất, dùng camera đặt riêng.

Chi tiết từng bước ở mục 7 của [docs/GIOI_THIEU_DU_AN.html](docs/GIOI_THIEU_DU_AN.html).

---

## Công nghệ (tóm tắt)

Python + FastAPI · OpenCV (YuNet phát hiện mặt, SFace nhận diện) · MiniFASNet chống giả mạo · SQLite · giao diện web HTML/JS thuần. Các model dùng mặc định có license Apache-2.0, **dùng thương mại được**. Bộ model InsightFace (SCRFD/ArcFace) trong repo chỉ dùng để so sánh vì license chỉ cho phép nghiên cứu.

## Cấu trúc tài liệu

```
README.md                      ← bạn đang ở đây (tổng quan cho PM)
docs/GIOI_THIEU_DU_AN.html     ← giới thiệu đầy đủ: kiến trúc, công nghệ, so với đề xuất, rủi ro
docs/HUONG_DAN_KY_THUAT.md     ← cài đặt, cấu hình, kịch bản demo, cấu trúc mã nguồn
config.yaml                    ← mọi thông số (ngưỡng, giờ vào ca, nguồn video…)
app/                           ← mã nguồn
```
