# FaceGate Constitution

## Core Principles

### I. Độ chính xác Mở & Không Đoán Danh tính (Precision-First Open-Set)
Mọi quyết định nhận diện chấm công bắt buộc tuân theo nguyên tắc Open-Set: thà trả về `Unknown` (yêu cầu bước lại/nhìn lại) còn hơn nhận nhầm người lạ thành nhân viên (`False Accept`). Mọi nhánh khớp danh tính (chính diện hoặc góc nghiêng) phải đồng thời vượt qua cả **ngưỡng tương đồng tối thiểu (`threshold`)** lẫn **biên độ cách biệt top-1 / top-2 (`margin`)** phù hợp với nhóm góc nhìn đó.

### II. Chống giả mạo Đa lớp Không Thỏa hiệp (Defense-in-Depth Liveness)
Không một sự kiện `checkin` nào được ghi nhận nếu chưa đi qua tầng xác thực thực thể sống. Khi một mô hình chống giả mạo đơn lẻ suy giảm hiệu lực ở góc nhìn cực đoan (như góc nghiêng > 40°), hệ thống bắt buộc định tuyến sang các tầng kiểm chứng bổ trợ (phát hiện vật chứa giả mạo, ràng buộc đầu–cơ thể, thị sai chuyển động 3D theo thời gian) và gắn nhãn mức độ tin cậy rõ ràng để hậu kiểm.

### III. Tối ưu Thời gian thực trên CPU & Tách luồng (CPU-First Real-Time)
Hệ thống phải vận hành mượt trên máy tính/server chỉ có CPU phổ thông (Linux & Windows), không bắt buộc GPU rời. Luồng đọc camera, luồng hiển thị MJPEG và luồng mã hóa video clip phải tách biệt hoàn toàn khỏi luồng suy luận AI; độ trễ xử lý trung bình mỗi chu kỳ phát hiện giữ trong ngân sách thời gian thực (< 200 ms/vòng tại 3–5 FPS detect).

### IV. Tuân thủ Bản quyền Thương mại & Bảo vệ Dữ liệu Sinh trắc học (License & Privacy)
Các mô hình mặc định cho vận hành sản xuất phải có giấy phép cho phép thương mại hóa (Apache-2.0, MIT, BSD); mọi trọng số chỉ dành cho nghiên cứu (như InsightFace non-commercial) phải tách thành tùy chọn benchmark riêng và cảnh báo rõ ràng. Dữ liệu khuôn mặt và clip lịch sử phải tuân thủ vòng đời lưu trữ có thời hạn (`retention_days`), cho phép xóa sạch dữ liệu sinh trắc học ngay lập tức khi nhân viên nghỉ việc mà vẫn bảo toàn lịch sử bảng công.

### V. Kiểm thử Định lượng Trước Triển khai (Test-First & Measurable Gates)
Mọi thay đổi lên thuật toán lọc chất lượng, bám vết (tracker), so khớp (gallery), đăng ký (enrollment) và chống giả mạo phải đi kèm kiểm thử tự động (`pytest`) cho logic cốt lõi và công cụ đo kiểm (`benchmark`) định lượng được FAR, FRR, Spoof-Pass Rate và độ trễ p50/p90.

## Ràng buộc Kỹ thuật & Kiến trúc

- **Ngăn xếp nền tảng**: Python 3.10+, FastAPI, OpenCV (`opencv-python-headless` trên Linux), ONNX Runtime (`CPUExecutionProvider`), SQLite WAL (PoC) và giao diện Web HTML/CSS/JS thuần không phụ thuộc build step phức tạp.
- **Tương thích ngược**: Nâng cấp lược đồ cơ sở dữ liệu phải tự động di trú an toàn (`_migrate`) trên dữ liệu hiện có; nhân viên đã đăng ký ở phiên bản 5 góc trước đó vẫn tiếp tục hoạt động bình thường ở dải góc chính diện trong khi chờ bổ sung mẫu góc nghiêng.

## Quy trình Phát triển & Cổng Chất lượng

- Thiết kế theo hướng Spec-Driven: đặc tả nghiệp vụ (`spec.md`) → nghiên cứu & kế hoạch kiến trúc (`research.md`, `plan.md`, `data-model.md`, `contracts/`) → phân rã tác vụ (`tasks.md`) → kiểm thử và triển khai.
- Toàn bộ bài kiểm thử hiện hữu trong `tests/` không được hồi quy khi mở rộng dải góc nhận diện.

## Governance

Hiến pháp này định hướng mọi quyết định thiết kế và đánh đổi kỹ thuật của dự án FaceGate. Bất kỳ ngoại lệ nào về giấy phép mô hình, ngân sách độ trễ CPU hoặc hạ cấp kiểm tra chống giả mạo đều phải được ghi nhận minh bạch trong bảng `Complexity Tracking` của kế hoạch triển khai và được phê duyệt trước khi đưa vào vận hành.

**Version**: 1.0.0 | **Ratified**: 2026-11-11 | **Last Amended**: 2026-11-11
