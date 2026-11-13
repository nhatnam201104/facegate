# Phase 0 Research: Tái Kiến trúc Toàn diện FaceGate v4 (Chuẩn hóa cho Tapo C310 & Vận hành Máy chủ)

**Feature**: `003-clean-slate-architecture-v4`  
**Date**: 2026-11-13  
**Source Evidence**: Biên bản phản biện kiến trúc 6 vòng `docs/PHAN_BIEN_KIEN_TRUC.md` (`H1`–`H35`), mã nguồn hiện hữu `app/` và thông số chính thức TP-Link Tapo C310 V1/V2.20  
**Status**: Completed (0 Open Technical Issues)

---

## R1. Tách Mặt phẳng Truyền thông (Media Plane) & Xử lý Đặc thù RTSP của Tapo C310 (`P1`, `P2`, `H20`, `H24`, `H28`)

- **Decision**: Sử dụng **`MediaMTX` (giấy phép MIT)** làm **Single RTSP Puller** duy nhất kết nối vào camera **TP-Link Tapo C310** qua tiến trình ingest lọc bỏ âm thanh (`ffmpeg -rtsp_transport tcp -i ... -map 0:v:0 -c:v copy -an` có `runOnInitRestart: yes`), phân phối luồng xem trực tiếp qua **WebRTC (WHEP)** kết hợp vẽ lớp phủ trên HTML5 `<canvas>` ở trình duyệt, và ghi vòng đệm phân mảnh `fMP4` 1 giây lên **`tmpfs` (RAM, `recordDeleteAfter: 10m`)** để trích xuất clip gốc bằng MediaMTX Playback Server API.
- **Rationale**:
  1. **Triệt tiêu mã hóa kép trên CPU**: Kiến trúc hiện tại (`app/main.py:133-186`, `app/draw.py:66-78`, `app/clips.py:165-175`) giải mã H.264 rồi chuyển BGR↔RGB vẽ chữ bằng Pillow, nén lại thành MJPEG cho từng nhịp xem và nén lại lần nữa sang VP8 `.webm` bằng CPU. Với `MediaMTX` passthrough H.264, chi phí mã hóa lại trên CPU giảm về **~0%**, 5 màn hình cùng mở xem tăng **< 2–5% CPU**, và giữ nguyên được khung hình gốc sạch chưa bị vẽ đè (kèm API xuất MP4 đóng dấu FFmpeg theo yêu cầu khi đối soát khiếu nại lương — `H9`).
  2. **Bảo vệ giới hạn phần cứng 2+2 luồng của Tapo C310 (`H20`)**: Tapo C310 giới hạn tối đa 2 kết nối `/stream1` và 2 kết nối `/stream2` (App Tapo đang mở cũng chiếm suất). Đặt `MediaMTX` làm đầu mối kéo duy nhất (1 luồng chính + 1 luồng phụ) ngăn chặn hoàn toàn lỗi camera từ chối kết nối hoặc rớt khung hình.
  3. **Khắc phục lỗi hỏng file `fMP4` do codec âm thanh `pcm_alaw` & Bảo vệ quyền riêng tư (`H24`, `H28`)**: RTSP của dòng Tapo phát kèm track âm thanh `G.711 A-law 8000 Hz` (`pcm_alaw` — xem `AlexxIT/go2rtc#1954`). Định dạng `fMP4` và WebRTC không hỗ trợ đóng gói trực tiếp G.711, gây lỗi sai thời lượng file `.mp4` trên `MediaMTX` (`bluenviron/mediamtx#4902`) và làm phát sinh thu âm cá nhân ngoài phạm vi đồng ý sinh trắc học. Lọc `-map 0:v:0 -c:v copy -an` ngay từ cửa ngõ ingest tốn `~0.2%` CPU nhưng bảo đảm **0 byte âm thanh** đi vào RAM, đĩa hay trình duyệt.
- **Alternatives considered**:
  - *Giữ MJPEG trong FastAPI (`specs/002`)*: Tốt cho bản vá nhanh không cài thêm binary, nhưng băng thông mạng cao gấp 8–12 lần H.264 và vẫn tốn CPU nén lại video clip bằng `cv2.VideoWriter` khi chạy trên dòng 3MP của C310.
  - *Dùng `go2rtc` đơn thuần*: `go2rtc` rất mạnh về relay WebRTC nhưng không có sẵn Playback Server API để trích xuất ngược một đoạn MP4 trong quá khứ (pre-roll 2 giây) từ RAM như `MediaMTX` (`H24`).

---

## R2. Chiến lược Khai thác Cảm biến 3MP 15fps của Tapo C310: Lịch Thức Theo Ca & Two-Stage ROI Cùng Khung (`M5`, `H26`, `H27`, `H32`–`H35`)

- **Decision**:
  1. **Nhận diện trên `/stream1` (`2304×1296`), canh ngủ ngoài giờ trên `/stream2` (`640×360`)**: Khi đang thức, thu nhỏ chính khung `/stream1` vừa giải mã về `640×360` (`< 1 ms` CPU) để chạy `YOLOX-Nano`, sau đó **cắt vùng Đầu–Vai tỷ lệ 1:1 (`~640×640 px`) ngay trên cùng khung `/stream1` đó** đưa vào `YuNet` → `3DDFA_V2` → `EdgeFace-XS` (bỏ giới hạn bóp khung cứng `det_width = 960` tại `app/pipeline.py:104,278`).
  2. **Lịch thức theo ca (`Shift-Scheduled Active Window` — `H34`)**: Trong các khung giờ cao điểm chấm công (vd. `06:30–09:30` và `16:30–18:30`), giữ bộ giải mã `/stream1` chạy liên tục; chỉ bật ngủ theo nhu cầu ngoài giờ cao điểm với vùng đánh thức mở rộng trên `/stream2`.
  3. **Chuẩn hóa quang học & ánh sáng hiện trường (`H26`, `H27`, `H30`, `H32`)**: Bắt buộc cắm dây LAN **RJ45**, không gắn thẻ `microSD`, chiếu sáng thuận khu vực cổng **≥ 250–300 lux** và khóa cứng **`Video Mode = Day Mode`** trên cấu hình C310 trước khi cô lập vào VLAN (có DNAT `UDP 123` về dịch vụ `chrony` trên Server FaceGate).
- **Rationale**:
  - Với cảm biến `1/2.7"` và tiêu cự `f = 3.89 mm` (`HFOV ≈ 83°–88°`), ở cự ly cổng `2.0–2.5 m`, bề ngang khuôn mặt trên `/stream2` chỉ đạt `~22–28 px` (rớt hoàn toàn ngưỡng `min_face: 60`), trong khi trên `/stream1` đạt **`80–100 px`**.
  - Cắt ROI trên **cùng một khung `/stream1`** (`H35`) triệt tiêu 100% độ lệch pha thời gian giữa hai phiên RTSP (lệch `100 ms` ở tốc độ đi bộ `1.3 m/s` làm trôi hộp cắt tới `~80 px` trên ảnh 3MP).
  - Vì C310 không cho chỉnh `GOP = 1 s` (GOP thực tế có thể dài 2–3 giây), nếu tắt giải mã `/stream1` trong giờ vào ca thì lúc có người tới bộ giải mã phải chờ I-frame tiếp theo khiến nhân viên đi lọt qua vùng nhận diện (`H34`). Lịch thức theo ca giải quyết triệt để độ trễ đánh thức vào giờ cao điểm mà vẫn tiết kiệm điện/CPU suốt ngày đêm còn lại.
  - Khóa `Day Mode` + đèn LED ≥ 250–300 lux vừa ngăn camera nhảy sang hồng ngoại đen trắng vào sáng sớm/mùa đông (`H27`), vừa ép auto-exposure của C310 rút ngắn thời gian phơi sáng để giảm nhòe chuyển động ở tốc độ `15 fps` (`H32`).
- **Alternatives considered**:
  - *Dùng `/stream2` để tìm người rồi chiếu tọa độ sang `/stream1` lúc đang thức*: Bị loại ở Vòng 5 (`H35`) do lệch thời gian giữa hai phiên RTSP gây cắt hụt đầu khi bước nhanh.
  - *Quét trực tiếp `YuNet` trên toàn khung `2304×1296`*: Tốn gấp ~5.7 lần phép tính tích chập so với cắt vùng Đầu–Vai `640×640`, làm vỡ ngân sách `< 200 ms` khi có nhiều người.

---

## R3. Cô lập Tiến trình Suy luận (Fault-Isolated Perception Process) & Runtime CPU (`M1`, `M2`, `P6`)

- **Decision**: Tách vòng lặp nhận diện thành **1 tiến trình hệ điều hành độc lập (`Perception Process`)** giao tiếp với `FastAPI Control Plane` qua Unix Domain Socket / Local TCP JSON Lines; giữ độ chính xác `FP32/FP16` trên `onnxruntime` (`CPUExecutionProvider` ở `K2`, thử nghiệm `OpenVINOExecutionProvider` ở `K4`, tuyệt đối không lượng tử hóa INT8 cho `EdgeFace-XS` và `3DDFA_V2`).
- **Rationale**:
  - **Cô lập sự cố (Fault Isolation)**: Nếu driver giải mã video hoặc thư viện C++ gặp lỗi segfault/treo luồng lúc 07:55 sáng, tiến trình Web API, Bảng công, kênh chấm công dự phòng và `HRMWorker` vẫn sống nguyên vẹn, đồng thời supervisor tự khởi động lại `Perception Process` trong vài giây.
  - **Xóa bỏ tranh chấp khóa `ctx.engine_lock` (`P6`)**: Luồng đăng ký khuôn mặt và backfill chạy trên session riêng của tiến trình API/Worker phụ, không bao giờ chặn chu kỳ nhận diện tại cổng.
- **Alternatives considered**:
  - *Multiprocessing nhiều worker nhỏ qua `shared_memory` ngay từ đầu*: Phức tạp hóa quản lý vòng đời bộ nhớ khi chưa có số đo chứng minh 1 process bị nghẽn; để dành làm tối ưu tùy chọn sau khi đo ở `K4`.

---

## R4. Bám vết Chống Bẫy Đồng phục & Cắt Khung Hình: Crossing Quarantine + Viewport-Aware Context (`P4`, `H3`)

- **Decision**: Giữ bám vết cấp 1 trên hộp Mặt/Đầu có dự đoán vận tốc, dùng hộp người `YOLOX-Nano` làm cầu nối tạm thời khi xoay mặt > 75°, **loại bỏ hoàn toàn Body/Clothing Re-ID**, và áp đặt hai quy tắc bảo vệ cứng:
  1. **Crossing Quarantine (`H3`)**: Khi 2 track giao cắt (`IoU > 0.25` hộp mặt hoặc `IoU > 0.40` hộp người), loại bỏ toàn bộ mẫu thu trong pha giao cắt khỏi ngăn tích lũy của cả hai track, gắn cờ `quarantine` cấm phát `checkin`, và sau khi tách nhau bắt buộc danh tính phải vượt lại đầy đủ quy tắc chấp nhận Open-Set như một track mới.
  2. **Viewport-Aware Body Check**: Nếu cạnh dưới hộp mặt nằm sát đáy khung hình (`face_y2 >= 0.80 * H`) hoặc chồng lên hộp người đứng trước ở tiền cảnh, hệ thống coi là cắt khung hợp lệ (*viewport truncation*) và tắt cờ phạt `unattached_face`.
- **Rationale**:
  - Trong nhà máy/kho vận, nhân viên mặc **đồng phục và đội mũ bảo hộ giống hệt nhau** khiến mọi mô hình so khớp quần áo (`OSNet`) dễ tráo đổi ID khi đi chéo qua nhau.
  - Khi lắp camera ở cự ly `1.5–2.0 m` hoặc vướng bàn bảo vệ/cổng xoay, phần thân dưới bị cắt khỏi khung hình; quy tắc Viewport-Aware sửa triệt để nguy cơ phạt nhầm người thật thành `unattached_face` trong `app/context_detector.py:166`.
- **Alternatives considered**:
  - *Strict Top-Down Full-Body Skeleton*: Thất bại hoàn toàn khi người đi sau chỉ nhô đầu sau lưng người đi trước hoặc đứng gần bị cắt vai.

---

## R5. Máy Trạng thái Tích lũy Điểm Liên tục Tuân thủ Nguyên tắc I Constitution (`P5`, `H4`, `H25`)

- **Decision**: Thay thế rừng ngưỡng rời rạc (`40° ± 5°`) bằng máy trạng thái tường minh (`SEEN → COLLECTING → IDENTIFIED → LIVE_CONFIRMED → COMMITTED / REJECTED / UNKNOWN`) sử dụng điểm Log-Likelihood Ratio liên tục 4 tham số:
  $$\text{LLR}(s, \text{yaw}, q) = a \cdot s + b + c \cdot |\text{yaw}| + d \cdot q \quad (a > 0)$$
  kết hợp cổng khử tương quan chuỗi frame (**Novelty Gate**: chỉ cộng dồn mẫu mới khi $\Delta t \ge 250\text{ ms}$ hoặc $|\Delta\text{yaw}| \ge 8^\circ$), và giữ **rào cứng kép bắt buộc** ở bước chốt danh tính:
  $$\text{LLR}_{\text{agg}}(\text{top1}) \ge \tau \quad \text{VÀ} \quad \text{LLR}_{\text{agg}}(\text{top1}) - \text{LLR}_{\text{agg}}(\text{top2}) \ge \mu_0 + \mu_1 \cdot \frac{|\overline{\text{yaw}}|}{90^\circ}$$
- **Rationale**:
  - Tuân thủ 100% **Nguyên tắc I của Constitution** (*bắt buộc vượt đồng thời cả `threshold` lẫn `margin` top-1/top-2 phù hợp với góc nhìn*), vì biên độ cách biệt $\mu(\text{yaw})$ tự động siết chặt mượt mà khi góc nghiêng tăng mà không sinh lỗi nhảy ngăn ở vùng biên 40°.
  - Chỉ có 4 tham số nên hiệu chỉnh ổn định ngay cả trên tập dữ liệu nhỏ của `K0` thông qua hàng nghìn cặp cùng người / khác người ghép chéo (`H12`).
- **Alternatives considered**:
  - *SPRT độc lập ngây thơ trên mọi frame liên tiếp*: Phóng đại độ tự tin sai lệch khi một người đứng yên trước ống kính (`M7`).
  - *Platt Scaling tách cứng 2 miền `< 40°` và `> 40°`*: Tái tạo lại chính vách ngăn rời rạc 40° muốn loại bỏ (`H25`).

---

## R6. Đồng bộ Lớp phủ `<canvas>` với Luồng Video WebRTC (`H8`)

- **Decision**: Sử dụng API chuẩn của trình duyệt **`HTMLVideoElement.requestVideoFrameCallback((now, metadata) => ...)`** dựa trên `metadata.receiveTime` theo từng khung hình kết hợp hằng số bù **`overlay_offset_ms`** (được hiệu chuẩn thực nghiệm ở `K1` bằng bài test quay màn hình đồng hồ mili-giây) và ngoại suy tuyến tính vận tốc `(vx, vy)` kẹp biên `≤ 120 ms` (tự ẩn hộp nếu quá `400 ms` không có gói tin mới).
- **Rationale**:
  - Khi `MediaMTX` đóng gói lại RTSP sang WebRTC (SRTP), `rtpTimestamp` được sinh theo phiên PeerConnection riêng nên không khớp trực tiếp với timestamp bên Python; mặt khác `jitterBufferDelay / jitterBufferEmittedCount` trong `getStats()` là trung bình cộng dồn từ đầu phiên chứ không phải độ trễ tức thời của frame hiện tại. Phép đo đồng hồ mili-giây hiệu chuẩn thẳng toàn bộ chuỗi `MediaMTX → Perception → WebSocket` so với `MediaMTX → WebRTC`, đảm bảo sai lệch thực tế **p90 ≤ 2 frame**.
- **Alternatives considered**:
  - *Khớp trực tiếp `rtpTimestamp`*: Không khả thi do tái đóng gói SRTP và giới hạn của bộ đọc FFmpeg/OpenCV.

---

## R7. Sổ cái Chấm công Bất biến, Hậu kiểm Ngoại lệ & Bảo vệ Bằng chứng `legal_hold` (`H7`, `H15`, `H17`, `H22`)

- **Decision**:
  1. Chuyển lưu trữ chấm công sang mô hình **Append-Only Event Ledger** (`checkin_created`, `checkin_revoked`, `checkin_manual`), mỗi bản ghi mang `idempotency_key` duy nhất và lưu `remote_doc_id` trả về từ ERPNext để hỗ trợ thu hồi/xóa chứng từ hai chiều.
  2. Áp dụng **Audit-by-Exception (`H7`)** cho các lượt `profile_verified`: tự động ghi nhận công, chỉ gắn cờ cần HR rà soát đối với các ca sát biên xác suất, ca nằm trong cửa sổ 5 phút quanh một cảnh báo `spoof`, cộng **5% mẫu ngẫu nhiên** cuối tuần.
  3. Bổ sung cờ **`legal_hold`** cho clip gắn với khiếu nại, mẫu audit chưa duyệt hoặc `spoof` chưa xử lý: không bao giờ tự xóa khi dọn đĩa `< 2 GB` hay quá `retention_days`; ngay khi HR đóng hồ sơ thì hạ `legal_hold = 0` để tuân thủ vòng đời sinh trắc học của Nguyên tắc IV Constitution. Cấm tuyệt đối tự động thêm mẫu online (`H17`).
- **Rationale**: Đóng kín lỗ hổng một chiều trong `app/hrm.py:38-50` hiện tại (không sửa được khi nhận nhầm và dễ trùng lặp khi timeout mạng), đồng thời giảm > 90% gánh nặng duyệt tay cho HR.
- **Alternatives considered**:
  - *Bắt HR duyệt tay 100% lượt `profile_verified`*: Gây quá tải vận hành hàng ngày khi quy mô nhân sự tăng (`H7`).

---

## R8. Chiến lược Đo kiểm Định lượng `K0` & Bốn Phương án Phần cứng tại Cổng Quyết định `K0.5` (`H1`, `H2`, `H5`, `H6`, `H11`–`H14`, `H23`, `H31`)

- **Decision**: Đặt pha **`K0` (Đo thực địa trên Tapo C310 thật ở cả 2 góc lắp: đi thẳng vs. đi ngang)** và cổng họp **`K0.5` với PM** làm điều kiện tiên quyết trước khi viết lại nhánh nhận diện sâu, trình PM lựa chọn minh bạch giữa **4 phương án phần cứng**:
  - **Phương án A (Hybrid Terminal)**: Mua thiết bị đầu cuối có sẵn cặp camera RGB+IR chống giả mạo phần cứng (vd. dòng MinMoe ISAPI; lưu ý chi phí ẩn: buộc người dừng nhìn máy và phải lưu ảnh mặt gốc để đồng bộ ISAPI) + FaceGate làm Middleware trung tâm & mở rộng Phase 2 SKU/Zone.
  - **Phương án B (Two-Factor C310)**: Giữ Tapo C310 + Yếu tố xác thực thứ 2 (Thẻ từ / QR động).
  - **Phương án C (Pure C310 Optimized)**: Giữ thuần Tapo C310 (dây RJ45, `Day Mode` + đèn ≥ 250–300 lux, chạy Audit-by-Exception `H7`).
  - **Phương án C' (Upgrade Enterprise IP Camera — `H31`)**: Đổi sang Camera IP doanh nghiệp phổ thông ống kính `4–6 mm`, `25–30 fps`, khóa được tốc độ màn trập, chỉnh được `GOP = 1 s` & tắt audio từ nguồn (tận dụng lại C310 cho giám sát chung).
- **Rationale**: Phá vỡ vòng lặp phụ thuộc giữa vị trí lắp và thiết kế phần mềm (`H23`), đo lường trung thực bằng giao thức **ISO/IEC 30107-3** (`APCER` từng loại PAI ≥ 20 lượt, `BPCER`, và cận trên khoảng tin cậy `FAR` ghép chéo `H12`), giúp PM ra quyết định đầu tư dựa trên số liệu thật thay vì cảm tính.
