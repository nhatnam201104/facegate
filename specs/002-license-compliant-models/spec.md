# Feature Specification: Chuyển toàn bộ model/công nghệ sang giấy phép thương mại hóa được

**Feature Branch**: `002-license-compliant-models`

**Created**: 2026-11-18

**Status**: Draft

**Input**: User description: "lên kế hoạch chuyển các công nghệ đang bị phi thương mại sang các công nghệ không có bản quyền có giấy phép MIT hoặc tương đương"

## Bối cảnh

Đánh giá rủi ro pháp lý ngày 2026-11-18 phát hiện các thành phần sau đang dùng (hoặc có thể bật) trong FaceGate v4 nhưng **không được phép dùng thương mại**:

| ID | Thành phần | Giấy phép thực tế | Đang dùng ở đâu |
|---|---|---|---|
| R1 | EdgeFace-XS (trọng số) | CC BY-NC-SA 4.0 (Idiap) | Backend nhận diện **mặc định** `edgeface` |
| R2 | 3DDFA_V2 `mb1_120x120.onnx` + 5 file `tddfa_*.bin` (BFM) | Trọng số huấn luyện trên 300W-LP (nghiên cứu); BFM = phi thương mại, cấm phân phối lại | `PoseEstimator3D`, các `.bin` **đã commit vào git** |
| R3 | InsightFace SCRFD + ArcFace `w600k_mbf` | Phi thương mại | Backend `onnx`, cờ `--onnx` |
| R4 | `look_at_camera.wav` | **Đã xác minh 2026-11-18: do chính tác giả dự án tự tạo**, chỉ thiếu hồ sơ nguồn gốc | Loa nhắc nhìn camera |
| R5 | `pytapo` (API TP-Link không chính thức) | MIT, nhưng có thể vi phạm điều khoản dịch vụ (ToS) của TP-Link | `audio_prompt.backend: tapo` mặc định |
| R6 | Thiếu NOTICE / THIRD_PARTY, không ghim checksum model | Vi phạm điều kiện ghi công của Apache-2.0; không chứng minh được nguồn gốc | `download_models.py` |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Bản build giao khách chỉ chứa thành phần thương mại hóa được (Priority: P1)

Pháp chế/PM cần bàn giao FaceGate cho khách mà mọi trọng số model, dữ liệu và tài nguyên đi kèm đều có giấy phép MIT / Apache-2.0 / BSD (hoặc tương đương), có hồ sơ nguồn gốc kiểm chứng được.

**Why this priority**: Đây là rủi ro ĐỎ (16 điểm). Nếu chưa xử lý thì không được bán thương mại.

**Independent Test**: Chạy `python server/scripts/license_gate.py`. Lệnh trả về 0 khi không có model nào thiếu manifest hoặc có `commercial_use != allowed`, và không còn file BFM nào trong repo.

**Acceptance Scenarios**:

1. **Given** cấu hình mặc định, **When** khởi động server, **Then** không nạp EdgeFace, 3DDFA_V2, BFM hay InsightFace.
2. **Given** thư mục `server/models/` có một file `.onnx` không nằm trong manifest, **When** chạy license gate, **Then** gate fail và nêu tên file.
3. **Given** `pipeline.backend: onnx` hoặc `edgeface` trong config, **When** khởi động ở chế độ production, **Then** server từ chối khởi động và báo lỗi giấy phép.

---

### User Story 2 - Chấm công vẫn đạt chất lượng sau khi đổi model (Priority: P1)

HR cần nhận diện vẫn đúng, không tăng FAR, và nhân viên không phải đăng ký lại thủ công.

**Why this priority**: Đổi model nhận diện làm mất hiệu lực toàn bộ embedding cũ.

**Independent Test**: Chạy `replay_eval.py` trên bộ K0 với backend mới: FAR ≤ mức hiện tại, FRR được báo cáo, và benchmark K4 ≤ 200 ms cho 10 khuôn mặt.

**Acceptance Scenarios**:

1. **Given** gallery đang ở `model_version` EdgeFace, **When** chạy migration re-embed, **Then** mỗi nhân viên active có embedding mới từ ảnh crop đã lưu, không cần đứng lại trước camera.
2. **Given** nhân viên quay đầu > 60°, **When** pose ước lượng từ 5 điểm không tin cậy, **Then** hệ thống trả `Unknown` / yêu cầu nhìn camera thay vì đoán (Hiến pháp II).

---

### User Story 3 - Tài nguyên phụ trợ sạch giấy phép (Priority: P2)

File âm thanh, backend loa và tài liệu phải có nguồn gốc rõ ràng; mặc định không dựa vào API không chính thức.

**Independent Test**: `THIRD_PARTY_LICENSES.md` liệt kê đủ mọi thành phần; `audio_prompt.backend` mặc định là `web`; WAV có hồ sơ nguồn gốc.

### Edge Cases

- **Thiếu `server/models/manifest.json`**: server và worker **từ chối khởi động** (fail-closed), kể cả khi `research_mode=true`. Không có chế độ "chạy tạm không kiểm".
- **`manifest.json` hỏng JSON / thiếu field bắt buộc / sai `schema_version`**: xử lý như thiếu manifest, thông báo lỗi nêu rõ field.
- **Worker perception chạy ở process riêng**: worker tự nạp và kiểm manifest trước khi tạo engine/pose/liveness/context; không tin kết quả kiểm từ process chính.
- **File model có trong manifest nhưng không có trên đĩa**: giữ hành vi hiện tại (`FileNotFoundError` hướng dẫn chạy `download_models.py`).
- **File model trên đĩa không có trong manifest nhưng không được nạp**: server vẫn chạy (chỉ kiểm file được nạp); `license_gate.py` thì **fail** (quy tắc 1).
- **Crop đăng ký bị thiếu hoặc hỏng khi migrate**: nhân viên chuyển `needs_reenroll`, không chặn migration của người khác.
- **Bundle K0 cũ chứa embedding EdgeFace**: không dùng để đánh giá SFace; phải ghi lại bundle từ video gốc.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Backend nhận diện mặc định phải dùng trọng số có giấy phép thương mại hóa được (Apache-2.0/MIT/BSD).
- **FR-002**: Ước lượng tư thế đầu không được dùng bất kỳ dữ liệu nào phái sinh từ BFM hay trọng số huấn luyện trên 300W-LP.
- **FR-003**: Gỡ bỏ code, cấu hình và script tải của InsightFace (SCRFD/ArcFace) và EdgeFace khỏi bản build.
- **FR-004**: Mỗi model phải có mục trong `server/models/manifest.json` (nguồn, SHA-256 đầy đủ, giấy phép code, giấy phép trọng số, dữ liệu huấn luyện, trạng thái thương mại).
- **FR-005**: `download_models.py` phải xác minh SHA-256 đầy đủ và từ chối file lệch checksum.
- **FR-006**: Server từ chối khởi động nếu backend hoặc model đang nạp không có `commercial_use: allowed` (trừ khi bật cờ `research_mode` tường minh, mặc định tắt).
- **FR-007**: Có migration re-embed gallery sang `model_version` mới từ các crop đã lưu.
- **FR-008**: Có `THIRD_PARTY_LICENSES.md` + `NOTICE` ở thư mục gốc.
- **FR-009**: `audio_prompt.backend` mặc định là `web`; `tapo` chỉ bật thủ công kèm cảnh báo.
- **FR-010**: Giữ `look_at_camera.wav` (tác giả dự án tự tạo); lập hồ sơ nguồn gốc `third_party/provenance/look_at_camera.md` ghi tác giả, ngày tạo, công cụ dùng (nếu là TTS thì ghi tên công cụ/giọng để Pháp chế kiểm tra điều khoản đầu ra) và tuyên bố quyền sở hữu thuộc công ty.
- **FR-011**: Sửa mọi mô tả sai giấy phép trong code, README, `config.yaml`, `docs/*.html` và Hiến pháp.

### Key Entities

- **ModelArtifact**: Một file model/dữ liệu kèm hồ sơ giấy phép (xem data-model.md).
- **LicenseManifest**: Tập hợp ModelArtifact, nguồn sự thật cho license gate.

## Success Criteria *(mandatory)*

- **SC-001**: 0 thành phần phi thương mại trong bản build mặc định (license gate pass).
- **SC-002** *(chế độ tuyệt đối, vì L1 bị từ chối nên không có baseline EdgeFace)*: Trên replay K0 với SFace: `event_far.false_checkins == 0` **và** `event_far.far_ci95_upper ≤ 0.01`; `embedding_far` và FRR được công bố.
- **SC-003**: Benchmark full-pipeline K4 (10 khuôn mặt) ≤ 200 ms/chu kỳ trên CPU hiện có.
- **SC-004**: 100% `pytest server/tests/` xanh.
- **SC-005**: 100% nhân viên active có embedding ở `model_version` mới sau migration.
- **SC-006** *(đã chốt 2026-11-18, mục S6 trong approvals.md: dùng giá trị mặc định; chế độ **tuyệt đối** vì L1 bị từ chối)*: Trên replay K0 với ngưỡng đã chọn ở T026:

  | Chỉ số (`replay_eval.py`) | Yêu cầu | Lý do |
  |---|---|---|
  | `event_far.false_checkins` | **= 0** (cứng) | Hiến pháp II: không đánh đổi FAR |
  | `crossing_quarantine.id_switch_false_checkins` | **= 0** (cứng) | Hiến pháp II |
  | `event_frr.frr` (tỷ lệ lượt đi qua không được check-in) | **≤ 5 %** | Giá trị mặc định đã chốt |
  | `frr_by_yaw_band` dải \|yaw\| ≤ 40° | **≤ 5 %** | Dải dùng để check-in, nên không lỏng hơn `event_frr` |
  | `frr_by_yaw_band` dải 40° < \|yaw\| ≤ 60° | **≤ 15 %** | Mức mặc định +10 điểm % được quy đổi sang tuyệt đối (5 % + 10 điểm %); dải chỉ dùng gom bằng chứng sớm |
  | `frr_by_yaw_band` dải \|yaw\| > 60° | Không ràng buộc (chỉ công bố) | Theo thiết kế trả `Unknown` (`pose_reliable=False`) |
  | `checkin_latency_s.p90` (bonafide) | **≤ 3.0 s** | Quy đổi từ mặc định "+1.0 s": giả định khoảng 2 s cho một lượt đi qua camera cổng, cộng thêm 1.0 s |

  Hai giá trị 15 % và 3.0 s là **quy đổi** từ mặc định tương đối (do không còn baseline), PM có thể chỉnh. Nếu không đạt thì đi theo cổng T042 (MediaPipe), rồi đến phương án B (plan.md).

## Assumptions

- Chấp nhận rủi ro tồn dư "giấy phép trọng số permissive nhưng dữ liệu huấn luyện là bộ dữ liệu nghiên cứu" (YuNet/WIDER FACE, SFace/CASIA-WebFace+MS1M, YOLOX/COCO, MiniFASNet/không công bố) **theo từng model**: Pháp chế ký riêng từng model (`legal_ack` trong manifest). Quyết định LA ngày 2026-11-18.
- Phạm vi nhận diện góc nghiêng có thể giảm từ ±90° xuống ~±60°. PM chấp nhận đổi lấy tính hợp pháp, **với điều kiện đạt SC-006**.
- **L1 = REJECTED (2026-11-18)**: không dùng EdgeFace / 3DDFA_V2 / BFM dưới bất kỳ hình thức nào, kể cả để đo baseline; các file đã bị xoá khỏi repo. SC-002, SC-003 và SC-006 dùng ngưỡng tuyệt đối.
