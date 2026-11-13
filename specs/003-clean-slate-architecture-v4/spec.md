# Feature Specification: Tái Kiến trúc Toàn diện FaceGate v4 (3 Mặt phẳng Độc lập, Chuẩn hóa cho Tapo C310 & Sẵn sàng Mở rộng Đa cổng / Phase 2 SKU)

**Feature Branch**: `003-clean-slate-architecture-v4`  
**Created**: 2026-11-13  
**Status**: Approved via Dual-Review Convergence (`docs/PHAN_BIEN_KIEN_TRUC.md` — 35/35 Issues Resolved)  
**Input**: Kết quả hội tụ 6 vòng phản biện kiến trúc cho câu hỏi *"Nếu xây dựng lại hệ thống từ đầu để đáp ứng toàn bộ yêu cầu dự án hiện tại trên phần cứng thực tế TP-Link Tapo C310 và sẵn sàng vận hành máy chủ"*

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Xem trực tiếp độ trễ cực thấp & Ghi clip bằng chứng gốc không tốn CPU nén lại trên phần cứng Tapo C310 (Priority: P1)

Người vận hành và nhân viên tại cổng quan sát luồng video trực tiếp từ camera thực tế **TP-Link Tapo C310** (`2304×1296 @ 15 fps`, H.264, cắm dây LAN RJ45) qua trình duyệt web với độ trễ đầu-cuối dưới 300 ms, tốc độ khung hình đạt ≥ 90% tốc độ gốc của nguồn (13–15 fps), khung bao và nhãn tên tiếng Việt sắc nét vẽ trên `<canvas>` bám sát khuôn mặt (sai lệch p90 ≤ 2 frame). Toàn bộ quá trình xem trực tiếp của 1–5 màn hình đồng thời và việc trích xuất clip lịch sử (có sẵn ≥ 2 giây pre-roll) không giải mã hay mã hóa lại video trên CPU máy chủ, đồng thời loại bỏ hoàn toàn track âm thanh để bảo vệ quyền riêng tư.

**Why this priority**: Giải quyết dứt điểm nút thắt kiến trúc lớn nhất hiện tại (giải mã rồi nén kép MJPEG + VP8 trên CPU, burn-in làm hỏng khung hình gốc và lỗi tương thích codec âm thanh `pcm_alaw` của Tapo C310).

**Independent Test**: Đặt màn hình chạy đồng hồ mili-giây trước Tapo C310 (đã cắm RJ45, bật `Day Mode`, lọc bỏ audio), mở đồng thời 5 tab trình duyệt xem WebRTC + `<canvas>`, kiểm tra độ lệch khung bao p90 ≤ 2 frame, mức tăng CPU < 10 điểm %, và kiểm tra file clip lịch sử xuất ra không chứa stream audio, mở xem lại đúng lớp phủ có chữ ký HMAC-SHA256.

**Acceptance Scenarios**:

1. **Given** camera Tapo C310 đang phát `/stream1` (3MP@15fps) qua dây LAN RJ45 vào `MediaMTX` (đã tách bỏ track âm thanh `pcm_alaw`), **When** từ 1 đến 5 trình duyệt mở tab Trực tiếp cùng lúc, **Then** luồng hình đạt 13–15 fps ổn định với độ trễ p90 < 300 ms và mức tăng CPU máy chủ < 10 điểm %.
2. **Given** một nhân viên hoặc người lạ đi qua vùng cửa, **When** hành trình kết thúc, **Then** hệ thống trích xuất đoạn MP4 H.264 gốc sạch từ bộ đệm `tmpfs` (có pre-roll `2.0 s` cộng tối đa `1 GOP` của camera), lưu kèm file sidecar `.meta.json` ký `HMAC-SHA256` (0 byte âm thanh) và cho phép HR xuất bản MP4 đóng dấu burn-in theo yêu cầu khi đối soát lương.

---

### User Story 2 - Nhận diện Open-Set liên tục tới ±90°, Chống tráo người đồng phục & Cô lập lỗi Process trên dòng 3MP 15fps (Priority: P1)

Nhân viên bước qua cổng trong khung giờ vào/tan ca (hoặc ngoài giờ cao điểm) dưới ánh sáng chuẩn (`Day Mode`). Bộ suy luận AI chạy trong một tiến trình độc lập (`Perception Process`), khai thác mật độ điểm ảnh gốc 3MP của `/stream1` bằng cắt vùng Đầu–Vai 1:1 cùng khung hình, tích lũy bằng chứng theo hàm điểm liên tục (giữ nguyên rào cứng `threshold + margin` tăng dần theo góc nhìn), tự động cách ly danh tính khi hai công nhân mặc đồng phục đi giao cắt nhau (`Crossing Quarantine`), và không làm sập API/Bảng công ngay cả khi camera mất tín hiệu hoặc tiến trình AI gặp sự cố.

**Why this priority**: Bảo đảm độ chính xác tính lương theo Nguyên tắc I & II của Constitution trên đúng giới hạn vật lý của Tapo C310 (`15 fps`, ống kính rộng `3.89 mm`, không chỉnh được GOP/màn trập).

**Independent Test**: Chạy bộ công cụ Replay ngoại tuyến trên tập dữ liệu thực địa `K0` (gồm kịch bản đi thẳng, đi ngang, 2 người mặc đồng phục đi chéo, người đứng sát bị cắt vai ở đáy khung hình, và ≥ 20 lượt mỗi loại công cụ giả mạo theo chuẩn ISO/IEC 30107-3), đối chiếu trực tiếp FAR, FRR, APCER/BPCER và kiểm thử kill nóng tiến trình `Perception`.

**Acceptance Scenarios**:

1. **Given** hai nhân viên mặc đồng phục giống nhau đi lướt chéo làm hộp theo dõi giao cắt (`IoU > 0.25`), **When** hai người tách nhau ra, **Then** hệ thống loại bỏ các frame lúc giao cắt, gắn cờ `quarantine` cấm kế thừa danh tính tự động và bắt buộc vượt lại đầy đủ ngưỡng chấp nhận Open-Set (`threshold + margin`) trước khi ghi nhận check-in.
2. **Given** nhân viên đứng gần camera (1.5 m) khiến phần vai/thân bị cắt ở sát cạnh dưới ảnh (`face_y2 >= 0.80 * H`), **When** hệ thống kiểm tra ngữ cảnh cơ thể, **Then** hệ thống nhận diện đây là cắt khung hợp lệ (*viewport truncation*) và không phạt nhầm lỗi `unattached_face`.
3. **Given** hệ thống đang trong khung giờ ca làm việc (`Active Shift Window`), **When** nhân viên đầu tiên bước vào cổng, **Then** bộ giải mã `/stream1` đã sẵn sàng nóng (`0 ms` trễ chờ keyframe GOP), cắt vùng Đầu–Vai 1:1 trực tiếp trên cùng frame `/stream1` để giữ nguyên kích thước mặt `80–110 px` ở cự ly `2.0–2.5 m`.
4. **Given** tiến trình `Perception` bị dừng đột ngột hoặc cáp mạng camera bị rút lúc 07:55 sáng, **When** quản trị viên/nhân viên thao tác trên hệ thống, **Then** Web API, Bảng công và HRM Outbox vẫn hoạt động bình thường, hệ thống phát cảnh báo heartbeat tức thì và mở kênh chấm công dự phòng có kiểm toán.

---

### User Story 3 - Sổ cái Chấm công Bất biến (Append-Only Ledger), Hậu kiểm Ngoại lệ & Bảo vệ Clip Khiếu nại (Priority: P2)

Bộ phận HR chỉ cần rà soát một tỷ lệ nhỏ các lượt chấm công ngoại lệ (`Audit-by-Exception`: ca sát biên ngưỡng, ca gần thời điểm cảnh báo giả mạo, cộng 5% mẫu ngẫu nhiên cuối tuần). Khi phát hiện sai sót hoặc cần nhập bù ca lúc mất điện/hỏng camera, HR thực hiện thu hồi (`revoke`) hoặc tạo thủ công (`manual`) ngay trên giao diện; hệ thống lưu vết kiểm toán bất biến, đồng bộ lệnh hủy/bù tương ứng sang ERPNext mà không tạo bản ghi trùng, và khóa bảo vệ (`legal_hold`) clip bằng chứng liên quan để không bị xóa tự động khi ổ cứng đầy hoặc quá 30 ngày.

**Why this priority**: Hoàn thiện chu trình nghiệp vụ tính lương thực tế, đóng lỗ hổng một chiều của `app/hrm.py` hiện tại và tuân thủ pháp chế dữ liệu sinh trắc học.

**Independent Test**: Tạo 1 sự kiện check-in đẩy sang HRM, đánh dấu khiếu nại bật `legal_hold`, giả lập ngưỡng ổ cứng trống `< 2 GB` để kích hoạt dọn clip tự động, sau đó thực hiện lệnh `revoke` bởi HR; kiểm tra clip có `legal_hold` vẫn còn nguyên, bảng công cập nhật đúng trạng thái và chứng từ tương ứng trên HRM/ERPNext được hủy idempotent.

**Acceptance Scenarios**:

1. **Given** một sự kiện check-in đã gửi thành công sang HRM (`remote_doc_id` đã lưu), **When** HR thực hiện thu hồi kèm lý do xác thực, **Then** sổ cái ghi thêm bản ghi `checkin_revoked` (không xóa lịch sử gốc), Outbox đồng bộ hủy chứng từ trên ERPNext theo khóa idempotent và bảng công tính toán lại tức thời.
2. **Given** ổ cứng máy chủ còn trống dưới `2 GB` hoặc quét định kỳ `retention_days`, **When** bộ dọn dẹp cuốn chiếu hoạt động, **Then** mọi clip đang mang cờ `legal_hold = 1` (khiếu nại, mẫu audit chưa duyệt, cảnh báo `spoof` chưa xử lý) được giữ nguyên tuyệt đối và hệ thống phát cảnh báo dung lượng cho quản trị viên; ngay khi HR đóng hồ sơ thì hạ `legal_hold = 0` để quay về chu kỳ xóa chuẩn.

---

### User Story 4 - Đăng ký Khuôn mặt Phản hồi Gương Tức thời (Client WASM) & Lấy Mẫu Chuẩn Miền Camera Cổng (Priority: P2)

Nhân viên thực hiện đăng ký mới hoặc đăng ký bổ sung (`supplement_only`) với giao diện soi gương mượt mà nhờ mô hình xác định góc mặt chạy trực tiếp trên trình duyệt (MediaPipe WASM, Apache-2.0, 0 ms trễ mạng), trong khi các khung hình gốc dùng để trích xuất vector lưu vào cơ sở dữ liệu bắt buộc được chụp từ chính luồng gốc 3MP của camera cổng (hoặc camera chuẩn cùng model cảm biến tại bàn HR) và chỉ có hiệu lực sau khi HR phê duyệt. Hệ thống tắt hoàn toàn mọi cơ chế tự động thêm mẫu trong lúc vận hành cổng để chống đầu độc dữ liệu.

**Why this priority**: Kết hợp trải nghiệm mượt mà của xử lý phía client với độ chuẩn xác cùng miền quang học (*domain match*) và an toàn chống giả mạo lúc đăng ký.

**Independent Test**: Thực hiện phiên đăng ký 7 bước, kiểm tra giao diện phản hồi góc đầu tức thời mà không khóa luồng nhận diện cổng (`0 ms` giữ lock trên Perception cổng), xác nhận ảnh mẫu lưu trong `data/enroll/` là crop từ luồng camera chuẩn và trạng thái nhân viên giữ ở `pending` cho tới khi HR bấm Duyệt.

**Acceptance Scenarios**:

1. **Given** nhân viên đang thực hiện đăng ký khuôn mặt ở phòng HR hoặc trước cổng, **When** đồng nghiệp khác đồng thời đi qua cổng chấm công, **Then** tiến trình đăng ký chạy trên worker/session độc lập, không gây trễ hay chặn bất kỳ chu kỳ suy luận nào của cổng chấm công.

---

### Edge Cases

- **Camera Tapo C310 tự bật đèn hồng ngoại chuyển sang ảnh đen trắng khi sẫm trời**: Hệ thống quy định khóa cứng `Video Mode = Day Mode` trên cấu hình camera kết hợp đèn chiếu sáng cổng (≥ 250–300 lux); nếu môi trường bắt buộc chạy thiếu sáng có IR thì phải vượt bài đo FRR/APCER/BPCER riêng ở `K0` trước khi cho phép vận hành.
- **Nhòe chuyển động (Motion Blur) khi bước ngang nhanh ở tốc độ tối đa 15 fps của C310**: Chiếu sáng thuận ≥ 250–300 lux ép auto-exposure rút ngắn tốc độ màn trập, đồng thời đo phân phối Laplacian blur ở `K0` để làm dữ liệu quyết định bố cục cổng (`H2`) hoặc nâng cấp camera doanh nghiệp 25–30 fps (`C'`).
- **Xung đột số luồng RTSP và rò rỉ dữ liệu ra Cloud trên camera dân dụng**: C310 chỉ hỗ trợ tối đa `2 stream1 + 2 stream2`; `MediaMTX` là điểm kéo RTSP duy nhất, cấm mở xem trực tiếp trên App Tapo khi vận hành, không gắn thẻ `microSD`, cô lập camera trong VLAN chặn Internet (sau khi cấu hình xong) kèm rule DNAT `UDP 123` về dịch vụ NTP `chrony` trên Server FaceGate.
- **Mở rộng sang Phase 2 (Nhận diện hàng hóa/SKU và Zone sản xuất)**: Toàn bộ Media Plane, giao thức WebSocket `<canvas>` và định dạng sidecar clip dùng chung thực thể tổng quát `TrackedObject` (`kind: "person" | "face" | "sku"`, `camera_id`, `ts_server_ns`), không phải sửa lại hạ tầng khi thêm camera kho xưởng.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001 (Dữ liệu Thực địa & Replay Trước Tiên)**: Mọi thay đổi lên lõi quyết định, bám vết hoặc runtime suy luận PHẢI được đánh giá định lượng trên bộ dữ liệu thực địa `K0` (có đơn đồng ý sinh trắc học `H11`, chạy baseline tuần tự tái lập `H14`, lưu kèm Observation JSONL và ảnh crop gốc để chạy Replay ngoại tuyến).
- **FR-002 (Cổng Quyết định Chiến lược `K0.5`)**: Trước khi triển khai nhánh nhận diện đi ngang phức tạp, hệ thống PHẢI cung cấp báo cáo số liệu thực nghiệm `K0` (trên cả 2 góc lắp đi thẳng vs. đi ngang của Tapo C310) để PM chốt 4 quyết định: bố cục cổng (`H2`), lựa chọn phần cứng `A / B / C / C'` (`H6`, `H31`), quy chế bằng chứng clip (`H9`) và chính sách dữ liệu thử nghiệm (`H11`).
- **FR-003 (Media Plane Độc lập & Tách Âm thanh)**: Hệ thống PHẢI dùng `MediaMTX` (MIT) làm điểm kết nối RTSP duy nhất tới camera (`H20`), lọc bỏ hoàn toàn track âm thanh `pcm_alaw` ngay đầu vào bằng `-map 0:v:0 -c:v copy -an` có giám sát tự khởi động lại (`H28`), phát trực tiếp qua WebRTC (WHEP) và ghi vòng đệm ngắn hạn `fMP4` lên `tmpfs` RAM (`recordDeleteAfter: 10m`) để trích xuất clip gốc không mã hóa lại (`P1`, `H24`).
- **FR-004 (Đồng bộ Lớp phủ `<canvas>` Client-Side)**: Giao diện web HTML/CSS/JS thuần PHẢI vẽ hộp theo dõi và nhãn tiếng Việt trên thẻ `<canvas>` đè lên `<video>` WebRTC, đồng bộ khung hình thông qua `requestVideoFrameCallback` (`metadata.receiveTime`) kết hợp hằng số hiệu chuẩn `overlay_offset_ms` và nội suy vận tốc giới hạn `≤ 120 ms` (`H8`).
- **FR-005 (Clip Bằng chứng Ký số & Xuất Burn-in Theo Yêu cầu)**: Mỗi lượt xuất hiện PHẢI lưu file MP4 H.264 gốc sạch kèm file sidecar `.meta.json` có chữ ký `HMAC-SHA256` (gồm mã băm `sha256` của file MP4) và cung cấp API xuất bản MP4 đã vẽ đè lớp phủ bằng FFmpeg khi HR tải đối soát khiếu nại (`P2`, `H9`).
- **FR-006 (Cô lập Tiến trình Suy luận & Two-Stage ROI cùng Khung 3MP)**: Bộ suy luận AI PHẢI chạy trong một `Perception Process` tách biệt hoàn toàn với Web API và Enrollment (`M1`, `P6`). Khi thức, hệ thống thu nhỏ chính khung `/stream1` (`2304×1296`) về `640×360` để tìm vùng người, sau đó cắt vùng Đầu–Vai tỷ lệ 1:1 ngay trên cùng khung `/stream1` đó đưa vào `YuNet` + `3DDFA_V2` + `EdgeFace-XS` (`H33`, `H35`).
- **FR-007 (Lịch Thức Theo Ca & Ngủ Ngoài Giờ)**: Hệ thống PHẢI hỗ trợ cấu hình các khung giờ cao điểm chấm công giữ giải mã `/stream1` nóng liên tục (`0 ms` chờ GOP), và chỉ chuyển sang chế độ ngủ canh chuyển động trên `/stream2` vào ngoài giờ cao điểm (`H19`, `H34`).
- **FR-008 (Tích lũy Điểm Liên tục Tuân thủ Nguyên tắc I Constitution)**: Máy trạng thái quyết định PHẢI thay thế các ngưỡng cắt góc rời rạc bằng hàm điểm logistic liên tục $\text{LLR} = a\cdot s + b + c\cdot|\text{yaw}| + d\cdot q$ có cổng khử tương quan chuỗi frame (`Novelty Gate`: $\Delta t \ge 250\text{ ms}$ hoặc $|\Delta\text{yaw}| \ge 8^\circ$), đồng thời giữ rào cứng bắt buộc $\text{LLR}(\text{top1}) \ge \tau$ **và** $\text{LLR}(\text{top1}) - \text{LLR}(\text{top2}) \ge \mu(\text{yaw})$ (`H4`, `H25`).
- **FR-009 (Crossing Quarantine & Viewport-Aware Context)**: Khi hai hộp theo dõi giao cắt nhau, hệ thống PHẢI loại bỏ các mẫu trong lúc giao cắt, gắn cờ `quarantine` cấm chốt check-in và bắt vượt lại đầy đủ quy tắc chấp nhận Open-Set sau khi tách nhau (`H3`). Khi hộp mặt chạm sát đáy khung hình (`y2 >= 0.80 * H`), hệ thống PHẢI tự tắt lỗi `unattached_face`.
- **FR-010 (Sổ cái Chấm công Append-Only & Đồng bộ HRM Hai chiều)**: Dữ liệu chấm công PHẢI lưu theo mô hình sự kiện bất biến (`checkin_created`, `checkin_revoked`, `checkin_manual`), hỗ trợ Audit-by-Exception (`H7`), gắn `idempotency_key`, lưu `remote_doc_id` và đồng bộ cả tạo mới lẫn hủy chứng từ sang ERPNext (`H22`).
- **FR-011 (Chống Đầu độc Mẫu & Fail-safe Vận hành)**: Hệ thống PHẢI tắt hoàn toàn tự động thêm mẫu online (`H17`), lấy mọi dấu thời gian nghiệp vụ theo đồng hồ NTP của Server (`ts_server_ns`, `H16`), duy trì heartbeat giám sát camera/ffmpeg/worker, mở kênh chấm công dự phòng khi sự cố và bảo vệ tuyệt đối các clip có cờ `legal_hold = 1` khỏi mọi tác vụ dọn ổ cứng (`H15`).
- **FR-012 (Bảo mật & Mở rộng Đa cổng / Phase 2)**: Trước khi mở truy cập mạng nội bộ (`0.0.0.0`), hệ thống PHẢI bật xác thực đăng nhập/RBAC, mã hóa dữ liệu sinh trắc học ở trạng thái nghỉ, ghi audit log truy cập clip và chuẩn hóa schema `TrackedObject` chờ sẵn cho Phase 2 SKU (`H21`, `K5`).

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001 (Độ chính xác nhận diện & Chống giả mạo có Khoảng tin cậy)**: Trên bộ Replay `K0`, cận trên khoảng tin cậy 95% của **FAR** không vượt quá baseline hiện tại (mục tiêu **≤ 1%** mức sự kiện và **≤ 0.1%** trên cặp ghép chéo embedding `H12`), **FRR** và thời gian chốt danh tính cải thiện hơn baseline; báo cáo đầy đủ **APCER** (từng loại PAI ≥ 20 lượt) và **BPCER** theo chuẩn **ISO/IEC 30107-3** (`H13`).
- **SC-002 (Hiệu năng Stream & Đồng bộ Overlay trên Tapo C310)**: Luồng xem trực tiếp WebRTC đạt **≥ 90% tốc độ khung hình nguồn** (`13–15 fps` trên Tapo C310), độ trễ hiển thị LAN **p90 < 300 ms**, độ lệch vị trí khung bao trên `<canvas>` **p90 ≤ 2 frame** (kiểm chứng bằng đồng hồ mili-giây `H8`), và 5 người xem đồng thời làm tăng tải CPU máy chủ **< 10 điểm phần trăm**.
- **SC-003 (Tính toàn vẹn Clip & Quyền riêng tư)**: 100% clip sự kiện xuất ra có pre-roll nằm trong dải **`[2.0 s, 2.0 s + 1 GOP]`**, chứa **0 track âm thanh**, xác thực khớp chữ ký `HMAC-SHA256` với sidecar `.meta.json`, và tiêu tốn **< 1% CPU** cho việc ghi vòng đệm hàng ngày.
- **SC-004 (Chống tráo người mặc đồng phục)**: Tỷ lệ gán nhầm danh tính (**ID-Switch False Check-in**) trong kịch bản 2 người mặc đồng phục giống nhau đi giao cắt trên tập `K0` giảm về **0 ca chốt sai** nhờ cơ chế `Crossing Quarantine` (`H3`).
- **SC-005 (Ngân sách CPU 10 người đồng thời)**: Tổng thời gian xử lý mỗi chu kỳ suy luận (tính cả giải mã `/stream1` 3MP@15fps + cắt ROI + suy luận AI) duy trì **< 200 ms** trung bình trên CPU phổ thông cho tối đa **10 khuôn mặt đồng thời** (`K4`).
- **SC-006 (Khả năng chịu lỗi & Đối soát hai chiều)**: Khi ngắt cáp camera hoặc buộc dừng tiến trình `Perception`, Web API và bảng công duy trì phản hồi 100%, phát cảnh báo trong vòng **≤ 5 giây**, không xóa bất kỳ clip `legal_hold = 1` nào khi đĩa `< 2 GB` và đồng bộ chính xác 100% thao tác thu hồi/nhập bù công sang HRM.
