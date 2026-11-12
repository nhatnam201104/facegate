# Feature Specification: Nhận diện khuôn mặt góc nghiêng đến 90° kèm xác thực chống giả mạo đa lớp

**Feature Branch**: `001-profile-90-antispoof`

**Created**: 2026-11-11

**Status**: Approved for Planning

**Input**: User description: "lên kế hoạch cải tiến góc nhận diện lên đến 90 độ, đảm bảo được tính xác thực chống giả mạo"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Nhận diện nhân viên đi ngang qua camera ở góc nghiêng tới 90° (Priority: P1)

Nhân viên bước qua cổng hoặc hành lang nơi chỉ lắp 1 camera cố định. Thay vì phải dừng lại xoay thẳng mặt vào ống kính, nhân viên đi tự nhiên với góc mặt nghiêng sâu từ 40° đến 90° (nhìn ngang sườn mặt trái hoặc phải) hoặc chỉ liếc nhẹ về phía camera trong tích tắc. Hệ thống theo dõi xuyên suốt hành trình, ưu tiên khung hình góc tốt nhất nếu có, hoặc khớp trực tiếp mẫu sườn mặt cùng phía để ghi nhận check-in chính xác mà không nhận nhầm người lạ.

**Why this priority**: Giải quyết trực tiếp bài toán cốt lõi mở rộng dải góc nhận diện từ ±35° hiện tại lên ±90° trên 1 camera duy nhất mà vẫn giữ nguyên tính chính xác Open-Set.

**Independent Test**: Cho nhân viên đã đăng ký đủ mẫu đa góc đi ngang qua vùng nhìn camera ở góc mặt 75°–90° (cả chiều trái sang phải và phải sang trái) cùng người lạ chưa đăng ký; kiểm tra nhân viên được ghi nhận check-in đúng mã và người lạ bị xếp loại `Unknown`.

**Acceptance Scenarios**:

1. **Given** nhân viên đã đăng ký đầy đủ mẫu chính diện và góc nghiêng hai bên và đã được HR duyệt, **When** nhân viên đi ngang qua camera với góc mặt duy trì trong khoảng 60°–90° trong tối thiểu 1 giây, **Then** hệ thống nhận diện đúng nhân viên và ghi nhận check-in hợp lệ (gắn mức độ tin cậy `profile_verified`).
2. **Given** nhân viên đi ngang qua ở góc 80°–90° nhưng trong hành trình có ít nhất 2 khung hình quay mặt về `|yaw| ≤ 40°` (trong vùng chính diện trước ngưỡng trễ hysteresis `45°`), **When** hệ thống tổng hợp hành trình di chuyển của người đó, **Then** hệ thống tự động ưu tiên nhóm khung hình góc `|yaw| ≤ 40°` (*Best-shot selection*) để chốt danh tính với độ tin cậy cao nhất (`verified`).
3. **Given** một người chưa đăng ký đi ngang qua camera ở góc nghiêng 75°–90°, **When** hệ thống so khớp với danh sách nhân viên hiện có, **Then** hệ thống trả về `Unknown`, không đoán tên và không tạo bản ghi chấm công.

---

### User Story 2 - Chống giả mạo đa lớp ở cả góc chính diện lẫn góc nghiêng 90° (Priority: P1)

Khi kẻ gian tìm cách qua mặt hệ thống bằng ảnh in, ảnh/video phát trên điện thoại hoặc máy tính bảng (dù là ảnh chụp thẳng hay video quay góc nghiêng 90° của đồng nghiệp), hệ thống tự động kích hoạt cơ chế kiểm chứng phù hợp theo góc nhìn để phát hiện hành vi giả mạo, từ chối chấm công và lưu lại cảnh báo kèm clip bằng chứng.

**Why this priority**: Ở góc 75°–90°, mô hình chống giả mạo ảnh tĩnh chính diện không còn đáng tin cậy; nếu không có cơ chế chống giả mạo thay thế không phụ thuộc góc mặt, việc mở khóa góc 90° sẽ tạo lỗ hổng an ninh nghiêm trọng cho dữ liệu chấm công tính lương.

**Independent Test**: Đưa điện thoại/máy tính bảng phát ảnh hoặc video góc nghiêng 85° của nhân viên đã duyệt đi ngang qua camera, đồng thời cho nhân viên thật đi qua ở cùng góc 85°; kiểm tra trường hợp dùng điện thoại bị chặn và gắn nhãn `spoof`, còn người thật không bị chặn nhầm.

**Acceptance Scenarios**:

1. **Given** một người cầm điện thoại, máy tính bảng hoặc ảnh in hiển thị khuôn mặt góc nghiêng 60°–90° của nhân viên đi qua camera, **When** hệ thống phát hiện vật chứa giả mạo quanh khuôn mặt hoặc hai khuôn mặt không nhất quán trên cùng một cơ thể hoặc thiếu thị sai chuyển động 3D tự nhiên, **Then** hệ thống chặn check-in, phát cảnh báo giả mạo và lưu sự kiện `spoof` kèm clip.
2. **Given** nhân viên thật đi bộ tự nhiên ngang qua camera ở góc 75°–90° không cầm thiết bị che mặt, **When** hệ thống định tuyến sang kiểm tra ngữ cảnh cơ thể và thị sai 3D theo chuỗi thời gian, **Then** hệ thống không báo giả mạo nhầm do suy giảm điểm từ bộ kiểm tra chính diện.

---

### User Story 3 - Đăng ký khuôn mặt có hướng dẫn mở rộng thêm góc nghiêng sâu và tương thích dữ liệu cũ (Priority: P2)

Nhân viên thực hiện đăng ký khuôn mặt qua luồng hướng dẫn trực quan trên màn hình (hoặc tải video quay đa góc) bao gồm cả tư thế xoay sườn mặt trái và phải tới vùng 70°–85° mà không bị bộ lọc loại nhầm là ngoại lai. Các nhân viên cũ đã đăng ký 5 góc trước đây vẫn tiếp tục chấm công bình thường ở góc ≤ 45° và được hiển thị trạng thái gợi ý cập nhật thêm mẫu nghiêng sâu khi cần.

**Why this priority**: Cung cấp dữ liệu mẫu cùng phía nghiêng (*Same-pose reference*) giúp tăng mạnh độ chính xác nhận diện ở góc 90° đồng thời không làm gián đoạn vận hành của danh sách nhân viên sẵn có.

**Independent Test**: Thực hiện phiên đăng ký hướng dẫn mới từ thẳng đến nghiêng sâu trái/phải cho nhân viên mới, kiểm tra các mẫu góc nghiêng không bị loại bỏ nhầm và nhân viên cũ chỉ có mẫu 5 góc vẫn chấm công bình thường ở góc thẳng.

**Acceptance Scenarios**:

1. **Given** nhân viên bắt đầu phiên đăng ký có hướng dẫn mở rộng (7 bước chữ thập + nghiêng sâu trái/phải), **When** nhân viên lần lượt giữ đúng từng góc theo chỉ dẫn trên màn hình, **Then** hệ thống lưu trữ tách biệt các mẫu theo đúng 3 nhóm tư thế `pose_group` (`frontal`, `left_profile`, `right_profile`) kèm nhãn ô góc chi tiết `pose_bin` mà không loại bỏ nhầm mẫu nghiêng sâu.
2. **Given** nhân viên đã đăng ký ở phiên bản 5 góc cũ (chưa có mẫu nghiêng sâu), **When** hệ thống nâng cấp lên phiên bản mới (kể cả khi chuyển sang mô hình trích đặc trưng mới), **Then** hệ thống tự động tái trích xuất đặc trưng từ ảnh mẫu lưu sẵn để nhân viên đó vẫn được nhận diện bình thường ở góc `|yaw| ≤ 40°` và giao diện quản trị hiển thị nhãn thông báo chưa có mẫu góc 90°.

---

### User Story 4 - Phân tầng mức độ tin cậy và hậu kiểm dành cho HR (Priority: P3)

Bộ phận nhân sự (HR) xem bảng chấm công, lịch sử xuất hiện và danh sách sự kiện có thể phân biệt rõ lượt check-in nào được xác thực chính diện đầy đủ (`verified`) và lượt nào được chốt hoàn toàn ở góc nghiêng sâu (`profile_verified`), kèm clip video để rà soát nhanh khi đối soát bảng công cuối kỳ.

**Why this priority**: Giảm thiểu rủi ro nghiệp vụ cho bài toán tính lương trên phần cứng 1 camera RGB không có cảm biến hồng ngoại/3D chuyên dụng.

**Independent Test**: Thực hiện 1 lượt check-in nhìn thẳng và 1 lượt check-in đi ngang góc 85°, kiểm tra trên giao diện Sự kiện / Lịch sử và file xuất CSV hiển thị đúng nhãn mức độ xác thực cùng bộ lọc rà soát nhanh cho HR.

**Acceptance Scenarios**:

1. **Given** một lượt check-in được xác nhận thuần túy từ các khung hình góc nghiêng `|yaw| > 40°` (không có đủ khung hình `frontal` đạt chuẩn), **When** sự kiện được lưu và hiển thị trên giao diện quản trị, **Then** bản ghi mang mức tin cậy `profile_verified` và cho phép HR lọc riêng để xem lại clip đính kèm.

---

### Edge Cases

- **Quay đầu qua lại liên tục từ trái sang thẳng sang phải trong cùng một lượt đi**: Hệ thống không được trộn trung bình cộng đặc trưng góc 0° với góc 85° làm hỏng vector nhận diện, cũng không được tách nhầm thành hai người khác nhau khi vector giữa hai góc chênh lệch lớn.
- **Hai người đi song song hoặc nối đuôi sát nhau ở góc nghiêng**: Bộ theo dõi đầu–cơ thể phải giữ đúng định danh từng khung xương/cơ thể, tránh gán nhầm mặt trên điện thoại hoặc mặt người đi sau vào người đi trước.
- **Nhân viên đội mũ lưỡi trai, đeo kính gọng dày hoặc tóc dài che khuất sườn mặt phía camera**: Nếu các điểm mốc 3D phía nhìn thấy bị che khuất hoặc góc đầu ngẩng/cúi/nghiêng vượt ngưỡng cho phép, hệ thống báo lý do chất lượng thấp tương ứng (`occluded`, `pitch`, `roll`) bằng tiếng Việt rõ ràng và giữ trạng thái `Unknown` thay vì đoán bừa.
- **Quãng đường xuất hiện quá ngắn (< 0,8 giây / ít hơn số khung hình tối thiểu để đo thị sai chuyển động 3D)**: Ở góc `|yaw| > 40°`, nếu không đủ chuỗi khung hình để xác minh thị sai chuyển động 3D thì hệ thống giữ ở trạng thái đang xác minh / chưa đủ điều kiện chấm công để đảm bảo an toàn chống giả mạo.
- **Ánh sáng xiên một bên làm nửa mặt nhìn thấy ở góc 90° bị chói gắt hoặc tối sẫm**: Chỉ đo độ sáng và độ nét trên vùng da mặt thực sự nhìn thấy thay vì tính cả phần nền phía trước mũi trong khung chữ nhật.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Hệ thống PHẢI ước lượng được tư thế đầu 3 chiều (`yaw`, `pitch`, `roll`) và bộ điểm mốc khuôn mặt ổn định trên toàn dải góc ngang từ **-90° đến +90°**, ngay cả khi một bên mắt và nửa khuôn mặt bị tự che khuất (*self-occlusion*).
- **FR-002**: Hệ thống PHẢI phân vùng khuôn mặt vào các dải tư thế chuẩn (`frontal`: $|\text{yaw}| \le 40^\circ$, `left_profile` / `right_profile`: $40^\circ < |\text{yaw}| \le 90^\circ$ hoặc chi tiết hơn theo bán nghiêng và nghiêng hoàn toàn) để áp dụng khung căn chỉnh hình học riêng cho từng nhóm góc, không ép kéo giãn mặt 90° vào khung hai mắt cân xứng.
- **FR-003**: Bộ bám vết hành trình (Tracker) PHẢI duy trì liên tục một định danh hành trình cho cùng một người khi họ xoay đầu từ 0° đến ±90° dựa trên sự kết hợp giữa vị trí đầu/cơ thể và đặc trưng cùng nhóm góc, đồng thời lưu tách biệt tập đặc trưng theo từng dải góc thay vì cộng gộp mọi góc vào một vector duy nhất.
- **FR-004**: Khi chốt danh tính cho một hành trình xuất hiện, hệ thống PHẢI ưu tiên lựa chọn nhóm khung hình có góc nhìn gần chính diện và chất lượng cao nhất (*Best-shot priority*); chỉ sử dụng nhánh khớp góc nghiêng sâu khi người đó không có đủ khung hình chính diện đạt chuẩn.
- **FR-005**: Bộ so khớp danh tính PHẢI hỗ trợ cấu hình ngưỡng tương đồng (`threshold`) và biên độ cách biệt (`margin`) riêng biệt cho nhánh góc nghiêng sâu (chặt hơn nhánh chính diện) để khống chế tỷ lệ nhận nhầm người lạ.
- **FR-006**: Hệ thống chống giả mạo PHẢI định tuyến thông minh theo góc nhìn: sử dụng kiểm tra kết cấu khuôn mặt trực tiếp khi có đủ khung hình $|\text{yaw}| \le 40^\circ$, và bắt buộc kích hoạt tổ hợp kiểm chứng không phụ thuộc góc mặt khi nhận diện ở góc $> 40^\circ$ gồm: (a) phát hiện vật chứa giả mạo quanh vùng đầu (điện thoại, máy tính bảng, khung ảnh/mép giấy), (b) kiểm tra tính nhất quán hình học Đầu–Cơ thể, và (c) kiểm tra thị sai chuyển động 3D theo chuỗi thời gian so với mặt phẳng cứng 2D.
- **FR-007**: Quy trình đăng ký khuôn mặt (cả quay có hướng dẫn trực tiếp lẫn tải video) PHẢI hỗ trợ thu thập thêm các góc nghiêng sâu trái/phải (~70°–85°) và kiểm tra tính nhất quán theo từng cụm góc liền kề thay vì loại bỏ nhầm mẫu nghiêng sâu do khác biệt so với vector chính diện.
- **FR-008**: Hệ thống PHẢI gắn nhãn mức độ tin cậy xác thực (`verification_tier`: `verified` cho chính diện đầy đủ, `profile_verified` cho nhận diện thuần góc nghiêng đã qua chống giả mạo ngữ cảnh/thị sai) trên sự kiện chấm công, giao diện quản trị và báo cáo xuất CSV.
- **FR-009**: Hệ thống PHẢI duy trì khả năng tương thích ngược hoàn toàn với cơ sở dữ liệu hiện tại: khi nâng cấp lược đồ hoặc chuyển sang mô hình nhận diện mới (`model_version` mới), hệ thống tự động tái trích xuất đặc trưng từ các ảnh khuôn mặt đã lưu trong `data/enroll/` để nhân viên 5 góc cũ tiếp tục được nhận diện ngay lập tức mà không cần đăng ký lại.
- **FR-010**: Toàn bộ luồng xử lý mở rộng PHẢI vận hành hoàn toàn trên CPU phổ thông trên cả Linux và Windows mà không làm gián đoạn luồng hiển thị video trực tiếp hay luồng ghi clip lịch sử.

### Key Entities

- **Employee Pose Profile (Hồ sơ mẫu đa góc của nhân viên)**: Tập hợp các mẫu đặc trưng khuôn mặt của một nhân viên được phân nhóm tường minh theo 3 nhóm tư thế `pose_group` (`frontal`, `left_profile`, `right_profile`) kèm nhãn ô góc chi tiết `pose_bin`, trạng thái độ phủ góc (đã đủ góc 90° hay mới có góc chuẩn) và phiên bản mô hình.
- **Pose-Aware Track (Hành trình xuất hiện đa tư thế)**: Đại diện cho một lượt người xuất hiện trước camera, liên kết khung đầu–cơ thể theo thời gian, chứa các ngăn tích lũy đặc trưng tách rời theo dải góc, lịch sử tọa độ điểm mốc 3D qua các khung hình và bằng chứng chống giả mạo tổng hợp.
- **Verification Evidence & Tier (Bằng chứng xác thực & Cấp độ tin cậy)**: Thông tin đính kèm mỗi sự kiện check-in hoặc cảnh báo giả mạo, ghi nhận góc nhìn dùng để chốt danh tính, kết quả của từng lớp chống giả mạo (kết cấu mặt, phát hiện vật chứa, gắn kết cơ thể, thị sai 3D) và phân hạng tin cậy (`verified` / `profile_verified` / `spoof`).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Nhân viên đã đăng ký đủ bộ mẫu đa góc đi bộ tự nhiên ngang qua camera với góc mặt trong dải **45°–90°** đạt tỷ lệ nhận diện đúng (**True Accept Rate**) tối thiểu **85%** trong điều kiện ánh sáng tiêu chuẩn tại cổng và khoảng cách thiết kế (mặt ≥ 60 px).
- **SC-002**: Tỷ lệ nhận nhầm người lạ thành nhân viên (**False Accept Rate — FAR**) trên toàn dải góc **0°–90°** duy trì **≤ 1%** trên tập kiểm thử đánh giá chéo.
- **SC-003**: Các cuộc tấn công trình chiếu phổ biến ở góc nghiêng 45°–90° (cầm điện thoại/máy tính bảng phát ảnh hoặc video quay nghiêng của nhân viên, cầm ảnh in đi ngang qua cổng) bị chặn với tỷ lệ phát hiện đúng **≥ 90%**, đồng thời tỷ lệ chặn nhầm người thật đi bộ tự nhiên (**False Spoof Rejection**) giữ ở mức **≤ 5%**.
- **SC-004**: Tốc độ xử lý trên máy CPU 4–8 nhân phổ thông duy trì nhịp phát hiện **3–5 lần/giây** với thời gian xử lý trung bình mỗi chu kỳ **< 200 ms** (p90 **< 280 ms**) cho tối đa 3 người đồng thời trong khung hình, luồng video trực tiếp giữ mượt **15 FPS**.
- **SC-005**: 100% nhân viên đã đăng ký ở phiên bản 5 góc trước nâng cấp tiếp tục chấm công thành công ở dải góc ≤ 40° ngay sau khi cập nhật mà không bắt buộc phải đăng ký lại ngay lập tức.

## Assumptions

- Camera được lắp đặt ở tầm cao hợp lý (khoảng 1,5 m – 2,0 m) hướng vào lối đi sao cho chiều cao khuôn mặt/đầu người trong vùng nhận diện đạt tối thiểu 60 điểm ảnh và toàn bộ phần đầu–vai nằm trong khung hình để phục vụ kiểm tra gắn kết Đầu–Cơ thể.
- Nhân viên đi qua vùng nhìn của camera trong thời gian tối thiểu **0,8 – 1,2 giây** (tương đương 4–6 chu kỳ phát hiện ở mức 5 FPS) để hệ thống đủ chuỗi khung hình đánh giá quỹ đạo và thị sai chuyển động 3D khi ở góc nghiêng sâu.
- Các mô hình được lựa chọn cho chế độ vận hành mặc định tuân thủ giấy phép nguồn mở cho phép sử dụng thương mại (Apache-2.0 / MIT / BSD), tương tự định hướng hiện tại của dự án.
