# Mẫu Đơn Đồng ý Tham gia Thu thập Dữ liệu Thử nghiệm Hệ thống Nhận diện Khuôn mặt (Pha `K0`)

**Dự án**: FaceGate — Hệ thống Chấm công Khuôn mặt Nội bộ  
**Mã tài liệu**: `FG-CONSENT-K0` (Tuân thủ yêu cầu `H11`, `H28`, `H29` và Nguyên tắc IV của Constitution)

---

## 1. Mục đích và Phạm vi Thu thập

1. Dữ liệu hình ảnh khuôn mặt và bước đi tại khu vực cổng thử nghiệm chỉ được sử dụng **duy nhất cho mục đích đo kiểm độ chính xác nhận diện (FAR/FRR) và khả năng chống giả mạo (APCER/BPCER)** của hệ thống chấm công nội bộ trong Pha `K0`.
2. **Cam kết Không Ghi âm (`H28`)**: Luồng thu nhận đã được cấu hình kỹ thuật tách bỏ hoàn toàn kênh âm thanh (`-an` tại tầng ingest); hệ thống **tuyệt đối không thu, không lưu trữ và không xử lý giọng nói hay âm thanh** tại khu vực cổng.
3. **Cam kết Lưu trữ Nội bộ Cách ly (`H29`)**: Camera thử nghiệm được kết nối qua mạng nội bộ cô lập (VLAN riêng, tắt hoàn toàn dịch vụ đám mây Tapo Care và không gắn thẻ nhớ ngoài trời). Dữ liệu không được truyền ra ngoài máy chủ nội bộ của công ty và không được đưa lên kho mã nguồn Git.

## 2. Thời hạn Lưu trữ và Quyền của Người Tham gia

1. **Thời hạn tự động xóa**:
   - Video quay thô phục vụ trích xuất gói đánh giá `K0` sẽ được **xóa vĩnh viễn ngay sau khi trích xuất xong gói kiểm thử chuẩn (tối đa không quá 07 ngày làm việc)**.
   - Gói dữ liệu kiểm thử ngoại tuyến (`observations.jsonl` và ảnh cắt khuôn mặt mã hóa) chỉ lưu giữ tối đa **30 ngày** để hoàn tất nghiệm thu các pha `K1`–`K4`, sau đó tự động hủy toàn bộ.
   - *(Điều khoản tùy chọn nếu Công ty phê duyệt Phương án phần cứng A tại cổng `K0.5`)*: Nếu chuyển sang sử dụng đầu đọc chấm công chuyên dụng dùng giao thức ISAPI, người lao động sẽ được thông báo riêng trước khi lưu ảnh mẫu đăng ký chính thức để đồng bộ xuống thiết bị đầu cuối.
2. **Quyền rút lại sự đồng ý**: Người tham gia có quyền yêu cầu bộ phận Nhân sự / Kỹ thuật xóa ngay lập tức toàn bộ hình ảnh và vector đặc trưng của mình khỏi tập thử nghiệm bất kỳ lúc nào mà không chịu bất kỳ ảnh hưởng nào về quyền lợi lao động; công ty luôn duy trì kênh chấm công thay thế (Thẻ/QR/Xác nhận lễ tân).

---

## 3. Xác nhận Đồng ý

- **Họ và tên nhân viên tình nguyện**: ........................................................................
- **Mã nhân viên / Bộ phận**: ....................................................................................
- **Xác nhận**: `[ ]` Tôi đã đọc hiểu rõ mục đích, cam kết không thu âm, thời hạn xóa dữ liệu nêu trên và tự nguyện đồng ý tham gia phiên ghi hình thử nghiệm Pha `K0`.

*Ngày ...... tháng ...... năm 2026*

| Người tham gia ký tên | Đại diện Nhân sự / Pháp chế | Phụ trách Kỹ thuật |
|---|---|---|
| *(Ký, ghi rõ họ tên)* | *(Ký, ghi rõ họ tên)* | *(Ký, ghi rõ họ tên)* |
