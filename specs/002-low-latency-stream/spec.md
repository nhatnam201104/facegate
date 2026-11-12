# Feature Specification: Tối ưu độ trễ và hiệu năng luồng hiển thị video trực tiếp cho triển khai máy chủ

**Feature Branch**: `002-low-latency-stream`

**Created**: 2026-11-12

**Status**: Ready for Planning

**Input**: User description: "hiện tại luồng stream trên màn hình đang khá lag so với ứng dụng thật của camera, tối ưu luồng stream để sau này lên sever không bị delay quá nhiều"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Xem hình ảnh trực tiếp mượt mà, bám sát thời gian thực ngay cả khi đang nhận diện nhiều người (Priority: P1)

Nhân viên bước qua cổng hoặc người vận hành quan sát màn hình Trực tiếp nhìn thấy chuyển động hình ảnh trôi chảy, phản hồi tức thời gần tương đương ứng dụng xem gốc của camera. Ngay cả khi có từ 1 đến 3 người cùng đi vào vùng cửa khiến hệ thống phải liên tục phân tích khuôn mặt đa góc và kiểm tra chống giả mạo, hình ảnh trên màn hình vẫn duy trì độ mượt ổn định, không bị khựng, giật cục hay đứng hình theo từng nhịp phân tích; khung bao và nhãn tên nhân viên bám theo chuyển động người đi một cách tự nhiên.

**Why this priority**: Giải quyết trực tiếp vấn đề cốt lõi người dùng đang gặp phải hàng ngày — hiện tượng hình ảnh trên màn hình bị giật và chậm hơn rõ rệt so với ứng dụng gốc của camera, gây cảm giác nặng nề tại màn hình chào ở cổng cũng như giao diện giám sát.

**Independent Test**: Mở song song ứng dụng xem gốc của camera và màn hình Trực tiếp của hệ thống trên cùng mạng nội bộ; thực hiện động tác bước đi nhanh và vẫy tay trước ống kính trong cả hai trạng thái (không có người và có 1–3 người đang được nhận diện), đo độ chênh lệch thời gian hiển thị giữa hai màn hình và kiểm tra tốc độ khung hình thực tế.

**Acceptance Scenarios**:

1. **Given** nguồn camera đang hoạt động ổn định, **When** người đi qua trước ống kính và đối chiếu trực quan với ứng dụng gốc của camera trên cùng mạng nội bộ, **Then** luồng hình trên màn hình hệ thống đạt tốc độ tối thiểu 20–25 hình/giây (hoặc đạt tối thiểu 90% tốc độ gốc nếu nguồn camera phát thấp hơn) với độ lệch thời gian so với ứng dụng gốc không quá 300 mili-giây.
2. **Given** có từ 1 đến 3 người đồng thời xuất hiện trong khung hình khiến bộ phận nhận diện và chống giả mạo hoạt động liên tục, **When** quan sát luồng hình Trực tiếp trên màn hình, **Then** video nền vẫn phát mượt liên tục mà không bị đứng hình chờ chu kỳ phân tích kết thúc, biên độ giảm tốc độ khung hình hiển thị không quá 15% so với lúc khung hình trống.
3. **Given** hệ thống cập nhật trạng thái nhận diện ở tần suất 3–5 lần/giây trong khi luồng hiển thị chạy ở tốc độ cao hơn, **When** ghép khung đánh dấu và nhãn tiếng Việt lên từng khung hình mới, **Then** nhãn thông tin vẫn rõ nét, dễ đọc và không gây hiện tượng nhấp nháy hay nghẽn tốc độ xuất hình.

---

### User Story 2 - Vận hành trên máy chủ từ xa không bị trễ dồn theo thời gian hoặc quá tải khi nhiều người cùng xem (Priority: P1)

Khi hệ thống được đưa lên máy chủ tập trung (thu nhận luồng camera qua mạng và truyền hình ảnh tới trình duyệt của lễ tân, bảo vệ hoặc nhân sự qua mạng văn phòng), màn hình xem trực tiếp luôn ưu tiên hiển thị khoảnh khắc hiện tại mới nhất. Dù hệ thống chạy xuyên suốt nhiều ngày hay đường truyền mạng của một người xem tạm thời chập chờn, hệ thống không tích tụ các khung hình cũ trong hàng đợi dẫn tới cảnh "người đã đi qua cửa từ vài giây trước nhưng trên màn hình bây giờ mới thấy bước vào", đồng thời nhiều người cùng mở xem không làm nhân bội tải xử lý trên máy chủ.

**Why this priority**: Đáp ứng mục tiêu trọng tâm thứ hai để hệ thống sẵn sàng vận hành trên máy chủ thực tế, loại bỏ rủi ro trễ dồn (*latency drift*) và nghẽn tài nguyên khi chuyển từ môi trường thử nghiệm cá nhân sang môi trường mạng máy chủ.

**Independent Test**: Chạy hệ thống trên môi trường mạng có giới hạn băng thông và độ trễ mô phỏng máy chủ từ xa, mở đồng thời 5 cửa sổ trình duyệt xem trực tiếp trong 60 phút và chủ động bóp băng thông tạm thời của 1 cửa sổ trong 10 giây; kiểm tra cửa sổ bị nghẽn lập tức nhảy về hình ảnh hiện tại ngay khi mạng thông suốt và mức tiêu thụ tài nguyên máy chủ vẫn ổn định.

**Acceptance Scenarios**:

1. **Given** kết nối mạng từ máy chủ tới trình duyệt của một người xem bị chậm tạm thời làm tốc độ nhận dữ liệu thấp hơn tốc độ phát của máy chủ, **When** đường truyền khôi phục bình thường, **Then** người xem đó lập tức nhìn thấy khung hình thời gian thực mới nhất thay vì phải xem lại hàng loạt khung hình cũ bị ứ đọng trước đó, và sự cố mạng của người xem này không làm chậm những người xem khác.
2. **Given** có từ 2 đến 5 màn hình giám sát cùng mở xem luồng Trực tiếp của một cổng camera, **When** máy chủ phát luồng hình ảnh, **Then** hệ thống chuẩn bị và nén mỗi khung hình hiển thị một lần duy nhất để phân phối chung cho tất cả người xem, giữ mức tăng tải vi xử lý máy chủ không quá 10 điểm phần trăm so với khi chỉ có 1 người xem.
3. **Given** hệ thống đang chạy trực chấm công 24/7 trên máy chủ nhưng không có ai mở màn hình Trực tiếp hay màn hình Đăng ký (hoặc người dùng đã chuyển sang xem các tab bảng công, danh sách nhân viên), **When** không tồn tại người xem luồng hình nào đang hoạt động, **Then** máy chủ tự động ngưng công đoạn dựng và nén hình phục vụ trình duyệt để tiết kiệm tài nguyên mà vẫn duy trì đầy đủ việc nhận diện chấm công và lưu clip lịch sử khi có người đi qua.

---

### User Story 3 - Phản hồi soi gương tức thời khi nhân viên quay đăng ký khuôn mặt đa góc (Priority: P2)

Nhân viên đứng trước camera thực hiện đăng ký khuôn mặt có hướng dẫn 7 bước (bao gồm nhìn thẳng, ngẩng/cúi và xoay sườn mặt trái/phải tới ~80°). Hình ảnh xem trước dạng soi gương phản hồi tức thì theo từng cử động đầu của nhân viên, giúp họ dừng lại đúng góc yêu cầu ngay khi mũi tên chỉ dẫn báo đạt mà không bị quay quá đà do hình ảnh trên màn hình đi trễ hơn chuyển động thật.

**Why this priority**: Trải nghiệm đăng ký khuôn mặt góc nghiêng phụ thuộc trực tiếp vào phản hồi thị giác thời gian thực; giảm độ trễ luồng xem trước giúp rút ngắn thời gian đăng ký và tăng chất lượng mẫu thu được.

**Independent Test**: Mở tab Đăng ký ở chế độ quay từ camera, thực hiện xoay đầu liên tục qua các bước hướng dẫn và kiểm tra luồng hình soi gương phản hồi mượt mà, đồng thời kiểm tra kết nối luồng hình tự ngắt ngay khi chuyển sang tab khác.

**Acceptance Scenarios**:

1. **Given** nhân viên bắt đầu xem trước camera tại màn hình Đăng ký có hướng dẫn, **When** nhân viên xoay đầu theo các hướng trên màn hình, **Then** luồng xem trước sạch (không vướng khung theo dõi của màn hình chấm công) đạt tốc độ tối thiểu 20 hình/giây với độ trễ phản hồi dưới 300 mili-giây.
2. **Given** nhân viên hoàn tất đăng ký hoặc chuyển sang bất kỳ tab quản trị nào khác, **When** khu vực xem trước camera không còn hiển thị trên màn hình, **Then** phiên truyền hình ảnh xem trước được đóng ngay lập tức để không duy trì luồng truyền dư thừa từ máy chủ.

---

### User Story 4 - Giám sát thông số luồng phát và tự động khôi phục khi nguồn hình gián đoạn (Priority: P3)

Người quản trị vận hành máy chủ nhìn vào thanh trạng thái biết được ngay tốc độ khung hình thực tế đang phát ra màn hình bên cạnh tốc độ thu từ camera và tốc độ nhận diện, từ đó phân biệt nhanh hiện tượng chậm hình (nếu có) đến từ camera đầu vào, tải máy chủ hay mạng người xem. Khi camera mạng đổi nguồn hoặc rớt kết nối ngắn hạn, màn hình tự động khôi phục luồng hình mới nhất ngay khi có tín hiệu lại mà không cần tải lại trang.

**Why this priority**: Giúp quá trình vận hành và chẩn đoán chất lượng luồng truyền trên máy chủ từ xa minh bạch, giảm thao tác can thiệp thủ công tại các màn hình đặt cố định ở cổng.

**Independent Test**: Quan sát chỉ số tốc độ phát hình trên giao diện Trực tiếp, thực hiện đổi nguồn camera hoặc ngắt kết nối nguồn video trong vài giây rồi khôi phục lại, xác nhận màn hình tự động hiển thị tiếp hình ảnh mới trong vòng 2 giây.

**Acceptance Scenarios**:

1. **Given** màn hình Trực tiếp đang kết nối tới máy chủ, **When** hệ thống cập nhật thông tin trạng thái định kỳ, **Then** giao diện hiển thị rõ tốc độ khung hình xuất thực tế của luồng hiển thị cùng với tốc độ camera đầu vào và tốc độ phân tích nhận diện.
2. **Given** nguồn camera bị ngắt tín hiệu tạm thời hoặc quản trị viên đổi nguồn camera ngay trên giao diện, **When** nguồn hình sẵn sàng trở lại, **Then** màn hình xem trực tiếp tự động làm mới luồng phát và hiển thị khung hình hiện tại trong vòng tối đa 2 giây mà không cần người dùng tải lại trang web.

---

### Edge Cases

- **Đường truyền từ máy chủ tới trình duyệt yếu hơn tốc độ phát hình (ví dụ xem từ mạng văn phòng khác hoặc Wi-Fi yếu)**: Hệ thống bắt buộc bỏ qua các khung hình trung gian chưa kịp gửi và luôn lấy khung hình mới nhất tại thời điểm đường truyền rảnh, chấp nhận giảm độ mượt tạm thời thay vì để hình ảnh bị trễ sau thực tế hàng chục giây.
- **Trình duyệt chuyển sang tab khác hoặc thu nhỏ cửa sổ trong thời gian dài rồi mở lại**: Khi cửa sổ hoặc tab xem trực tiếp bị ẩn đi, trình duyệt tạm dừng tiêu thụ băng thông luồng hình; ngay khi người dùng bật hiển thị trở lại, luồng hình kết nối thẳng vào khoảnh khắc hiện tại mà không phát bù hay tua nhanh các khung hình cũ trong lúc ẩn.
- **Nguồn camera an ninh có độ phân giải gốc rất cao (Full HD 1080p hoặc 2K/4K)**: Hệ thống thu phóng về kích thước hiển thị chuẩn và áp dụng mức nén hình ảnh cấu hình được trước khi truyền qua mạng, tránh đẩy các khung hình dung lượng quá lớn gây nghẽn băng thông máy chủ.
- **Hệ thống chuyển giữa Chế độ chờ (tiết kiệm điện) và Chế độ thức khi có người bước vào vùng cửa**: Trong Chế độ chờ, màn hình vẫn cập nhật hình ảnh định kỳ tiết kiệm tài nguyên; ngay khoảnh khắc phát hiện người bước vào vùng cửa, cả camera đầu vào lẫn luồng phát trực tiếp lập tức chuyển lên tốc độ thời gian thực mà không làm trễ lượt nhận diện đầu tiên.
- **Ghi clip bằng chứng lịch sử song song với tối ưu luồng xem**: Ngay cả khi không có người dùng nào đang mở trình duyệt xem trực tiếp, nếu có người đi qua cổng thì hệ thống vẫn đảm bảo ghi lại đầy đủ clip lịch sử xuất hiện (gồm cả đoạn hình trước và sau khi xuất hiện) kèm khung thông tin nhận diện phục vụ hậu kiểm.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Hệ thống PHẢI tách rời hoàn toàn chu kỳ thu nhận và xuất hình ảnh hiển thị khỏi chu kỳ suy luận nhận diện khuôn mặt và chống giả mạo, đảm bảo việc phân tích một khung hình nặng không bao giờ chặn hoặc làm khựng nhịp phát hình ảnh trực tiếp ra màn hình.
- **FR-002**: Ở tầng thu nhận tín hiệu từ mọi nguồn hình trực tiếp (camera gắn máy và camera mạng), hệ thống PHẢI cấu hình bộ đệm đầu vào ở mức tối thiểu và liên tục xả bỏ các khung hình cũ để luôn giữ duy nhất khung hình mới nhất vừa thu được.
- **FR-003**: Hệ thống PHẢI lưu trữ trạng thái lớp phủ nhận diện mới nhất (vị trí khuôn mặt, màu sắc trạng thái, nhãn tên/cảnh báo tiếng Việt, vạch hướng đi và chỉ số vận hành) một cách độc lập để bộ phận xuất hình có thể ghép nhanh lên các khung hình video mới nhất với chi phí xử lý tối thiểu.
- **FR-004**: Luồng truyền hình ảnh tới từng người xem PHẢI hoạt động theo cơ chế "chỉ phát khung hình mới nhất, hủy bỏ khung hình trễ": không duy trì hàng đợi nhiều khung hình chưa gửi trên từng kết nối mạng để triệt tiêu hoàn toàn hiện tượng trễ dồn tích lũy theo thời gian.
- **FR-005**: Hệ thống PHẢI chuẩn bị và nén mỗi khung hình hiển thị trực tiếp một lần duy nhất rồi chia sẻ cho toàn bộ người xem đang mở tại cùng thời điểm, đồng thời áp dụng cơ chế tương tự cho luồng xem trước sạch của màn hình Đăng ký.
- **FR-006**: Hệ thống PHẢI theo dõi số lượng người xem đang kết nối thực tế; khi không có bất kỳ trình duyệt nào đang mở luồng xem và không có phiên ghi clip lịch sử nào đang cần khung hình đã gắn nhãn, hệ thống tự động tạm ngưng bước dựng và nén ảnh phục vụ trình duyệt để giải phóng tài nguyên vi xử lý cho máy chủ.
- **FR-007**: Giao diện trình duyệt PHẢI tự động ngắt luồng tải hình ảnh khi người dùng rời khỏi màn hình có video (chuyển sang các tab bảng công, lịch sử, nhân viên, sự kiện hoặc ẩn tab trình duyệt) và tự động kết nối lại ngay lập tức vào khung hình hiện tại khi người dùng quay lại màn hình xem video.
- **FR-008**: Hệ thống PHẢI cho phép cấu hình tường minh các tham số luồng hiển thị phục vụ triển khai máy chủ (tốc độ khung hình phát mục tiêu, chiều rộng khung hình hiển thị và chất lượng nén hình ảnh) với bộ giá trị mặc định cân bằng tốt giữa độ mượt, độ nét chữ tiếng Việt và băng thông mạng.
- **FR-009**: Hệ thống PHẢI đo lường và cung cấp thông số tốc độ luồng phát thực tế (số khung hình xuất mỗi giây) trong dữ liệu trạng thái vận hành để hiển thị trực quan cho người quản trị theo dõi.
- **FR-010**: Mọi thay đổi tối ưu luồng hiển thị PHẢI giữ nguyên tính chính xác nhận diện mở rộng tới ±90°, toàn bộ quy tắc chống giả mạo đa lớp, cơ chế vùng cửa đánh thức Chế độ chờ, chất lượng ảnh chụp sự kiện và tính toàn vẹn của clip lịch sử xuất hiện.

### Key Entities

- **Latest Camera Frame (Khung hình Camera Mới nhất)**: Đại diện cho bức hình thời gian thực vừa thu nhận từ nguồn camera kèm mã thứ tự và dấu thời gian gốc, luôn ghi đè khung hình trước đó để triệt tiêu độ trễ đầu vào.
- **Overlay State Snapshot (Bản chụp Trạng thái Lớp phủ)**: Tập dữ liệu gọn nhẹ chứa danh sách hộp khuôn mặt đang theo dõi, nhãn trạng thái tiếng Việt, màu sắc và thông số vận hành từ lần phân tích gần nhất, dùng để ghép nhanh lên luồng video tốc độ cao mà không gây tranh chấp khóa với bộ nhận diện.
- **Shared Broadcast Frame (Khung hình Phát Dùng chung)**: Khung hình hiển thị đã được thu phóng, gắn lớp phủ và nén hoàn chỉnh cho một nhịp phát, được chia sẻ cho tất cả người xem trực tiếp đang hoạt động nhằm bảo toàn tài nguyên máy chủ.
- **Active Viewer Subscription (Phiên Đăng ký Xem Trực tiếp)**: Trạng thái kết nối của một trình duyệt đang mở xem luồng hình, dùng để kiểm soát việc bật/tắt bộ dựng hình theo nhu cầu thực tế và đảm bảo mỗi người xem luôn nhận bản hình mới nhất phù hợp với tốc độ mạng của họ.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Trên mạng nội bộ tiêu chuẩn (LAN), tổng độ trễ hiển thị từ lúc chuyển động diễn ra trước ống kính tới lúc xuất hiện trên màn hình hệ thống đạt **≤ 250 mili-giây** ở mức trung vị (p50) và **≤ 400 mili-giây** ở phân vị thứ 95 (p95); độ chênh lệch so với ứng dụng xem gốc của cùng camera đạt **≤ 300 mili-giây**.
- **SC-002**: Tốc độ hiển thị thực tế trên màn hình Trực tiếp và màn hình Đăng ký đạt tối thiểu **20–25 hình/giây** (hoặc **≥ 90%** tốc độ khung hình gốc của camera nếu camera phát dưới 25 hình/giây), và không bị sụt giảm quá **15%** khi có đồng thời **3 người** đi qua vùng nhận diện so với khi khung hình trống.
- **SC-003**: Sau **60 phút** vận hành liên tục hoặc sau khi đường truyền của người xem bị giới hạn băng thông tạm thời (mô phỏng truy cập máy chủ từ xa ở mức 2–5 Mbps), độ trễ luồng xem **không tăng tích lũy theo thời gian** và tự khôi phục về trạng thái thời gian thực (**≤ 500 mili-giây** cộng độ trễ mạng một chiều) trong vòng **≤ 2 giây** sau khi băng thông ổn định lại.
- **SC-004**: Khi tăng từ **1 người xem lên 5 người xem đồng thời** trên cùng một cổng camera, mức sử dụng vi xử lý của máy chủ tăng thêm không quá **10 điểm phần trăm**; khi có **0 người xem** đang mở màn hình video và khung hình không có người xuất hiện, chi phí dựng/nén khung hình phục vụ trình duyệt giảm về **0%**.
- **SC-005**: 100% chức năng nghiệp vụ hiện hữu không bị hồi quy: tốc độ phân tích nhận diện duy trì **3–5 chu kỳ/giây** với thời gian xử lý trung bình mỗi chu kỳ **< 200 ms** trên máy chủ vi xử lý phổ thông, các chỉ số nhận diện/chống giả mạo giữ nguyên và toàn bộ bài kiểm thử tự động hiện có đều đạt.

## Assumptions

- Hệ thống chỉ yêu cầu truyền tải hình ảnh trực tiếp và dữ liệu trạng thái/sự kiện chấm công theo thời gian thực; không yêu cầu thu hay truyền tải âm thanh từ micro của camera (âm báo chấm công tiếp tục được phát trực tiếp trên trình duyệt khi nhận sự kiện).
- Trong mô hình triển khai lên máy chủ cho mỗi cổng chấm công, số lượng màn hình mở xem trực tiếp đồng thời thông thường nằm trong khoảng **1 đến 5 người xem** (màn hình đặt tại cổng cộng với trình duyệt của bộ phận nhân sự/an ninh), kết nối qua mạng nội bộ doanh nghiệp hoặc mạng riêng ảo văn phòng có băng thông tối thiểu **2 Mbps** cho mỗi người xem.
- Đối với camera mạng (IP Camera) khi đưa lên máy chủ, quản trị viên có thể cấu hình luồng phụ (*substream*, ví dụ độ phân giải 720p ở 20–25 hình/giây) hoặc luồng chính tùy hạ tầng mạng; hệ thống chịu trách nhiệm triệt tiêu độ trễ thuộc phạm vi phần mềm thu nhận, xử lý và phân phối hình ảnh kể từ lúc gói tin khung hình tới máy chủ.
- Khi đường truyền mạng giữa máy chủ và trình duyệt người xem suy giảm nghiêm trọng, việc giữ cho hình ảnh trên màn hình luôn phản ánh đúng thời điểm hiện tại được ưu tiên cao hơn việc giữ trọn mọi khung hình trung gian (riêng clip bằng chứng lưu trong mục Lịch sử trên máy chủ vẫn được ghi độc lập, không bị ảnh hưởng bởi mạng của người xem).
