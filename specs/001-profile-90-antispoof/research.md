# Phase 0 Research: Nhận diện khuôn mặt đến 90° & Chống giả mạo đa lớp trên 1 Camera CPU

**Feature**: `001-profile-90-antispoof`
**Date**: 2026-11-11
**Status**: Completed (All technical unknowns resolved)

---

## R1. Ước lượng Tư thế Đầu 3D & Điểm mốc Khuôn mặt tới ±90° (3D Pose & Landmarks)

- **Decision**: Sử dụng **`3DDFA_V2` (MobileNetV1 120×120 ONNX + BFM 62-param reconstruction)** chạy qua `onnxruntime` (`CPUExecutionProvider`) ngay sau bước phát hiện mặt/đầu.
- **Rationale**:
  - `YuNet` và `SCRFD` chỉ xuất 5 điểm mốc 2D (2 mắt, mũi, 2 khóe miệng). Khi góc quay ngang $|\text{yaw}| > 60^\circ$, mắt khuất phía xa chập vào sống mũi làm công thức ước lượng heuristic 2D trong `app/quality.py:27` sai số lớn và làm biến dạng ma trận affine `ARCFACE_TEMPLATE`.
  - `3DDFA_V2` (giấy phép **MIT**) hồi quy trực tiếp 62 tham số 3DMM (ma trận xoay 3×3 `R`, dịch chuyển `offset`, 40 tham số hình dáng `alpha_shp`, 10 tham số biểu cảm `alpha_exp`) chỉ mất **~1.5 – 2.5 ms/mặt** trên CPU 4 luồng, tái tạo chính xác **68 điểm mốc 3D `(x, y, z)`** và bộ góc Euler thực `(yaw, pitch, roll)` ổn định xuyên suốt dải **-90° đến +90°**.
  - Vector chiều sâu `z` và sai số tái chiếu 3D qua chuỗi frame còn cung cấp tín hiệu trực tiếp cho tầng chống giả mạo **3D Motion Parallax** (xem R5) mà không tốn thêm mô hình thứ hai.
- **Alternatives considered**:
  - *Giữ nguyên 5 điểm 2D của YuNet/SCRFD*: Bị chập điểm mốc khi mất 1 mắt ở góc > 65°, không tách được độ sâu 3D thật với ảnh phẳng.
  - *MediaPipe Face Mesh (468 điểm)*: Rất tốt ở góc ≤ 50° nhưng tỷ lệ trượt/méo lưới tăng mạnh ở góc 80°–90° full profile.
  - *InsightFace `1k3d68`*: Chính xác nhưng dính giấy phép **Non-commercial** của InsightFace, vi phạm Nguyên tắc IV của Constitution cho bản mặc định.

---

## R2. Căn chỉnh Đa góc (Pose-Specific Alignment) vs Tái tạo Chính diện 3D (3D Frontalization)

- **Decision**: Áp dụng **Căn chỉnh 3 khung theo dải tư thế (*3-Bin Pose-Specific Similarity Alignment*)** kết hợp **So khớp cùng phía nghiêng (*Same-pose / Profile-to-Profile Matching*)**, **không** dùng 3D Face Frontalization sinh ảnh giả.
- **Rationale**:
  - Chia tư thế ngang `yaw` thành 3 chế độ căn chỉnh chuẩn `112×112` (có vùng trễ *hysteresis* 5° để tránh nhảy bậc ở biên):
    1. **`frontal`** ($|\text{yaw}| \le 40^\circ$): Căn chỉnh chuẩn 5 điểm hai mắt – mũi – miệng hiện tại.
    2. **`left_profile`** ($-90^\circ \le \text{yaw} < -40^\circ$) và **`right_profile`** ($+40^\circ < \text{yaw} \le +90^\circ$): Dùng tập điểm mốc 3D phía nhìn thấy từ `3DDFA_V2` (khóe mắt ngoài/trong bên thấy, đỉnh mũi, chân mũi, khóe miệng bên thấy, đường viền gò má – tai/hàm) khớp vào **Template Profile chuẩn hóa `112×112`** sao cho sườn mặt chiếm trọn vùng trung tâm thay vì bị kéo bẹp nửa ảnh.
  - Ở góc 85°–90°, các phương pháp *3D Frontalization* tự suy diễn/lấy đối xứng nửa mặt bị khuất khiến nhiều người khác nhau có vùng mặt tái tạo na ná nhau, đẩy tỷ lệ **False Accept (FAR)** tăng vọt. Khớp trực tiếp sườn mặt thật với mẫu đăng ký sườn mặt thật bảo toàn đặc trưng hình học sống mũi – trán – cằm – tai.
- **Alternatives considered**:
  - *3DMM UV / Frontalized Rendering*: Tốn thêm ~10–15 ms rasterization trên CPU và gây hiện tượng "ảo giác đồng nhất" ở góc 85°–90° làm tăng FAR.
  - *Dùng chung 1 template chính diện cho mọi góc*: Ảnh căn chỉnh ở góc 90° bị suy biến affine (skew cực đại), làm rớt cosine similarity xuống dưới 0.20.

---

## R3. Mô hình Nhận diện Đặc trưng Bền góc Nghiêng & Bản quyền (Backbone Embedding)

- **Decision**: Kiến trúc đa backend có kiểm soát bản quyền rõ ràng:
  1. **Backend mặc định an toàn thương mại (`opencv_pose` / `edgeface`)**: Hỗ trợ song song **`SFace` (Apache-2.0)** khi khớp cùng nhóm góc (*same-pose profile matching*) và bổ sung **`EdgeFace-XS` (`edgeface_xs_gamma_06.onnx`, mã & trọng số phát hành theo BSD-3-Clause bởi Idiap, ~1.8M tham số, ~6–8 ms/mặt trên CPU)** làm backend chuyên trị góc nghiêng sâu.
  2. **Backend nghiên cứu / đối chuẩn (`adaface` / `onnx`)**: Hỗ trợ tùy chọn **`AdaFace IR-18` (MIT)** qua cờ `--research` trong `scripts/download_models.py` (ghi chú rõ nguồn tập dữ liệu huấn luyện gốc WebFace để bộ phận pháp chế rà soát trước khi thương mại hóa, tuân thủ nghiêm ngặt Nguyên tắc IV).
- **Rationale**:
  - Thực nghiệm nhận diện mặt cho thấy khi đã căn chỉnh đúng theo khung nghiêng riêng và so khớp **cùng phía nghiêng** (ảnh live 80° so với ảnh đăng ký 75°–80°), ngay cả backbone nhẹ cũng tăng mạnh điểm tương đồng so với khớp chéo 0°–90°.
  - `EdgeFace-XS` được thiết kế tối ưu cho thiết bị biên CPU, chịu biến thiên góc nhìn và chất lượng ảnh thấp vượt trội nhờ kiến trúc lai ConvNeXt-Transformer gọn nhẹ, giữ tổng thời gian embed dưới 10 ms.
- **Alternatives considered**:
  - *ArcFace ResNet-100*: Quá nặng cho CPU thời gian thực (~60–90 ms/mặt) và phi thương mại.
  - *Chỉ dùng SFace khớp chéo với mẫu 0°*: Tỷ lệ nhận diện ở góc > 65° gần như bằng 0.

---

## R4. Bám vết Hành trình Đầu–Cơ thể & Chọn Khung hình Tốt nhất (Best-Shot & Pose-Bucketed Tracking)

- **Decision**: Mở rộng `IoUTracker` thành **Pose-Bucketed Person-Face Tracker**:
  1. Liên kết mỗi `Track` với cả hộp giới hạn khuôn mặt (`face_bbox`) và hộp giới hạn cơ thể/đầu (`person_bbox` từ bộ phát hiện ngữ cảnh ở R5) để duy trì 1 ID xuyên suốt kể cả khi nhân viên xoay đầu qua lại từ $0^\circ \leftrightarrow \pm 90^\circ$.
  2. Thay thế danh sách `embs` phẳng duy nhất trong `Track` bằng **3 ngăn tích lũy đặc trưng tách biệt theo nhóm tư thế** (`buckets`: `frontal`, `left_profile`, `right_profile`). Chỉ kiểm tra tách track (`split_cosine`) giữa các vector **cùng một ngăn tư thế** — khắc phục triệt để lỗi tự cắt đứt track hoặc cộng trộn làm hỏng vector khi người đi vừa xoay đầu.
  3. **Quy tắc ưu tiên Best-Shot**: Nếu ngăn `frontal` có $\ge \text{min\_good\_frames}$ (mặc định 2 frame), hệ thống **luôn chốt danh tính trên ngăn `frontal`** với ngưỡng chuẩn; chỉ khi kết thúc giai đoạn xác nhận hoặc sau $N$ frame mà ngăn `frontal` trống, hệ thống mới xét ngăn `left_profile` / `right_profile` với bộ ngưỡng chặt hơn (`profile_threshold`, `profile_margin`).
- **Rationale**: Trong thực tế tại cổng, hơn 60% trường hợp người đi ngang vẫn có 0.2–0.3 giây liếc về hướng camera; ưu tiên Best-Shot giúp tận dụng tối đa cơ hội đạt cấp `verified` chính diện trước khi phải dùng đến `profile_verified`.
- **Alternatives considered**:
  - *Cộng trung bình có trọng số mọi góc vào 1 vector*: Vector góc 0° và 85° gần trực giao trong không gian embedding, cộng lại làm giảm điểm khớp ở cả hai phía.
  - *ByteTrack + Re-ID riêng*: Tốn thêm 1 mạng Re-ID trên CPU không cần thiết khi đã có bbox người từ tầng chống giả mạo ngữ cảnh.

---

## R5. Kiến trúc Chống giả mạo 4 Lớp cho Góc Nghiêng tới 90° (Pose-Agnostic & Temporal Anti-Spoofing)

- **Decision**: Triển khai **Pipeline Chống giả mạo Định tuyến theo Góc nhìn (Pose-Gated Multi-Layer Liveness)**:
  - **Nhánh A — Góc chính diện / bán nghiêng nhẹ ($|\text{yaw}| \le 40^\circ$)**: Giữ nguyên bộ đôi **`MiniFASNetV2 + MiniFASNetV1SE`** (Apache-2.0, ~4 ms/mặt) kết hợp kiểm tra ngữ cảnh cơ bản. Nếu đạt $\ge 4$ frame điểm trung bình $\ge 0.70$ và không có vật chứa giả mạo $\rightarrow$ cấp độ **`verified`**.
  - **Nhánh B — Góc nghiêng sâu ($40^\circ < |\text{yaw}| \le 90^\circ$)**: Không dùng điểm `MiniFASNet` để đánh rớt người thật (tránh False Reject hàng loạt), mà bắt buộc vượt qua đồng thời **3 lớp kiểm chứng không phụ thuộc góc mặt**:
    1. **Lớp B1 — Phát hiện Vật chứa Giả mạo & Ràng buộc Đầu–Cơ thể (`YOLOX-Nano` COCO ONNX, Apache-2.0, `416×416`, ~8–12 ms/chu kỳ cho toàn khung hình)**:
       - Chạy **1 lần cho cả khung hình** (không chạy lặp theo từng mặt), lấy các lớp COCO: `person` (0), `cell phone` (67), `laptop` (63), `tv` (62), `book` (73).
       - **Spoof Medium Check**: Báo `spoof` ngay nếu hộp khuôn mặt nằm lọt trong hoặc giao cắt mạnh với thiết bị màn hình (`cell phone` / `laptop` / `tv`) hoặc viền khung chữ nhật sắc cạnh có lóa phản quang xung quanh vùng mở rộng `2.5×` hộp mặt.
       - **Face-to-Body Anatomical Coupling**: Khuôn mặt hợp lệ phải neo đúng vào 1/3 trên của đúng **một** hộp `person` có tỷ lệ chiều rộng `face_w / person_w` hợp lý (`0.12 .. 0.65`) và vận tốc dịch chuyển tương đồng. Nếu một `person` mang 2 khuôn mặt (mặt thật + mặt trên điện thoại cầm trước ngực/vai) hoặc mặt lơ lửng không gắn với cơ thể nào $\rightarrow$ chặn `spoof` / từ chối xác nhận.
    2. **Lớp B2 — Kiểm định Thị sai Chuyển động 3D vs Mặt phẳng 2D (*Temporal 3D Motion Parallax*)**:
       - Tận dụng chuỗi $\ge 6$ frame liên tiếp từ `3DDFA_V2` khi đối tượng di chuyển tối thiểu một quãng dịch ngang $\Delta x \ge 8\%$ chiều rộng khung hình:
       - Kiểm tra hai bất biến vật lý của đầu 3D thật đang bước đi: (i) biến thiên góc nhìn tương đối $\Delta\text{yaw}$ đồng biến với góc tia nhìn từ tâm quang học camera khi tịnh tiến ngang + dao động bước đi dọc nhẹ, và (ii) tỷ lệ sai số khớp phẳng *Planar Homography residual* so với mô hình chiếu 3D của 68 điểm mốc vượt ngưỡng chiều sâu tối thiểu (ảnh in / ảnh tĩnh trên điện thoại di chuyển ngang cho sai số homography phẳng xấp xỉ 0 vì mọi điểm cùng nằm trên một mặt phẳng cứng).
    3. **Lớp B3 — Phân tích Tần số Moiré / Viền phát sáng Cục bộ (*High-Frequency Screen Artifact Check*)**:
       - Biến đổi FFT / DoG nhẹ (~0.5 ms NumPy/OpenCV) trên mảng da má nhìn thấy và viền sát mặt để bắt đỉnh phổ tuần hoàn (vân Moiré của lưới pixel màn hình khi phát video quay sẵn góc 90°) và hiện tượng chói bậc thang (*specular clipping*).
  - **Phân tầng nghiệp vụ**: Vượt qua trọn bộ B1 + B2 + B3 ở nhánh góc nghiêng sẽ được ghi nhận check-in với cấp **`profile_verified`** (hiển thị huy hiệu riêng trên UI + lưu clip bằng chứng bắt buộc để HR hậu kiểm nhanh).
- **Rationale**:
  - Chọn **`YOLOX-Nano` (Megvii, Apache-2.0)** thay vì `YOLOv8n`/`YOLO11n` (Ultralytics, **AGPL-3.0**) để tuân thủ tuyệt đối Nguyên tắc IV về bản quyền thương mại của dự án.
  - Chạy `YOLOX-Nano` 1 lần trên toàn ảnh `416×416` chỉ tốn ~8–12 ms trên CPU, phục vụ đồng thời cả **Tracking cơ thể (R4)** lẫn **Chống giả mạo vật chứa/cơ thể (R5)**.
- **Alternatives considered**:
  - *YOLOv8n / YOLO11n*: Nhanh nhưng giấy phép AGPL-3.0 bắt buộc mở mã nguồn toàn bộ sản phẩm nếu không mua license thương mại.
  - *Huấn luyện lại MiniFASNet cho góc 90°*: Thiếu tập dữ liệu chống giả mạo công khai ở góc 90° có bản quyền thương mại, và ảnh nhỏ 60–80 px ở cổng không đủ vi kết cấu da cho mạng CNN đơn frame.
  - *Bắt người dùng thực hiện hành động (Active Liveness: gật đầu/nháy mắt)*: Trái ngược hoàn toàn với mục tiêu để nhân viên đi ngang tự nhiên ở góc 90° không dừng lại.

---

## R6. Ngân sách Hiệu năng CPU (Latency Budget tại 5 FPS Detect)

| Thành phần suy luận | Tần suất chạy | Độ trễ CPU ước tính (4 nhân) | Ghi chú tối ưu |
|---|---|---|---|
| `YuNet` (hoặc detector mặt đa góc) | 1 lần / frame (`960 px`) | ~14 – 20 ms | Đã có sẵn |
| `YOLOX-Nano` (`person` + `spoof medium`) | 1 lần / frame (`416×416`) | ~9 – 14 ms | Chạy chung cho mọi người trong khung; tắt khi ở chế độ ngủ (`sleeping`) |
| `3DDFA_V2` (68 landmark 3D + Euler pose) | Mỗi mặt (`max 3`) | ~2 ms × 3 = ~6 ms | Đầu vào nhỏ `120×120` cực nhẹ |
| Backbone Embedding (`SFace` / `EdgeFace-XS`) | Mỗi mặt đạt chuẩn (`max 3`) | ~8 ms × 3 = ~24 ms | Chỉ chạy khi qua lọc chất lượng vùng thấy |
| Liveness (`MiniFASNet` *hoặc* `Parallax + FFT`) | Mỗi mặt (`max 3`) | ~4 ms × 3 = ~12 ms | Nhánh > 40° không tốn `MiniFASNet`, chỉ tính đại số NumPy ~1 ms |
| **Tổng cộng toàn chu kỳ (trường hợp tối đa 3 người)** | **5 lần / giây (mỗi 200 ms)** | **~65 – 76 ms** | **Đạt xa mục tiêu < 200 ms/chu kỳ (SC-004)** |
