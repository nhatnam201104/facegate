# Báo cáo Cổng Quyết định Chiến lược `K0.5` Trình PM & Pháp chế

**Dự án**: FaceGate v4 · **Phần cứng đánh giá**: TP-Link Tapo C310 (`2304×1296 @ 15 fps`, H.264, `3.89 mm`, RJ45)  
**Cách điền số liệu**: Chạy `python scripts/replay_eval.py data/k0_bundle` sau khi hoàn tất thu thập Pha `K0` theo `deploy/vlan-ntp-chrony.md`.

---

## 1. Bảng Đối chiếu Thực nghiệm Hai Bố cục Ứng viên trên Tapo C310 (`K0`)

| Chỉ số đo kiểm | Bố cục 1: Đi ngang tự do (`side_walk`, ±90°) | Bố cục 2: Dẫn hướng nhìn thẳng (`frontal_corridor`, ±40°) | Mục tiêu Cổng |
|---|---|---|---|
| Khoảng cách nhận diện (`d`, mét) & Bề ngang mặt trung vị (`px` trên 3MP) | *(Điền từ `manifest.json`)* | *(Điền từ `manifest.json`)* | `≥ 80 px` (`1.5–2.5 m`) |
| Độ nét trung vị (`Laplacian blur` ở 15 fps, ≥ 250 lux) | *(Điền từ `replay_eval.py`)* | *(Điền từ `replay_eval.py`)* | `≥ 35` |
| Khoảng cách Keyframe (`GOP`) thực tế & Rớt luồng / giờ (RJ45) | *(Điền từ `ffprobe`)* | *(Điền từ `ffprobe`)* | `0` rớt luồng/giờ |
| Chi phí CPU giải mã `/stream1` 3MP@15fps (`decode_ms` TB / p90) | *(Điền từ `replay_eval.py`)* | *(Điền từ `replay_eval.py`)* | Tổng chu kỳ `< 200 ms` |
| **FAR** cặp ghép chéo (điểm & cận trên CI 95% Wilson — `H12`) | *(Điền từ `embedding_far`)* | *(Điền từ `embedding_far`)* | `≤ 0.1%` (mức cặp) |
| **APCER** theo PAI (Ảnh in / Ảnh ĐT / Video ĐT / Màn lớn — `H13`) | *(Điền từ `apcer_by_pai`)* | *(Điền từ `apcer_by_pai`)* | `≤ 10%` |
| **BPCER** (Tỷ lệ chặn nhầm người thật — `H13`) | *(Điền từ `bpcer`)* | *(Điền từ `bpcer`)* | `≤ 5%` |

---

## 2. Bốn Quyết định Cần PM / Pháp chế Ký Chốt Trước Khi Triển khai Nhánh Sâu

### Quyết định 1 (`H11` — Pháp chế Dữ liệu Sinh trắc học)
- `[ ]` Phê duyệt chính thức mẫu đơn đồng ý (`deploy/consent-template-k0.md`), xác nhận cam kết không thu âm (`-an`, `0` audio track) và chính sách tự động xóa clip sau `30 ngày` (trừ clip đang giữ khóa `legal_hold = 1` để giải quyết khiếu nại lương).

### Quyết định 2 (`H2`, `H26`, `H32` — Bố cục Cổng Chấm công)
- `[ ]` **Lựa chọn 2A**: Bố trí lối đi / màn hình chào hướng mặt nhân viên nhìn về phía ống kính (`±40°`, cự ly `1.5–2.5 m`) để triệt tiêu nhòe ngang ở `15 fps` và đạt độ tin cậy chống giả mạo cao nhất.
- `[ ]` **Lựa chọn 2B**: Bắt buộc giữ hành lang đi ngang tự do (`±90°`), chấp nhận phân tầng `profile_verified` kèm hậu kiểm ngoại lệ 5%.

### Quyết định 3 (`H6`, `H31` — Phương án Phần cứng & Đầu tư)
- `[ ]` **Phương án A (Hybrid Terminal)**: Trang bị đầu đọc chuyên dụng có cặp camera kép RGB+IR chống giả mạo phần cứng (lưu ý: buộc nhân viên dừng nhịp ngắn nhìn máy và phải lưu ảnh mặt gốc để đồng bộ ISAPI); FaceGate làm Middleware bảng công/ERPNext & mở rộng Phase 2 nhận diện hàng hóa/SKU.
- `[ ]` **Phương án B (Two-Factor C310)**: Giữ Tapo C310 + Yếu tố thứ 2 (Thẻ từ / Mã QR).
- `[ ]` **Phương án C (Pure Tapo C310)**: Giữ thuần Tapo C310 RGB (dây RJ45, `Day Mode` + đèn ≥ 250–300 lux, chạy hậu kiểm ngoại lệ 5% `Audit-by-Exception`).
- `[ ]` **Phương án C' (Upgrade Enterprise IP Camera)**: Nâng cấp camera cổng sang dòng doanh nghiệp phổ thông ống kính `4–6 mm`, `25–30 fps`, khóa được tốc độ màn trập & đặt cứng `GOP = 1 s`; điều chuyển chiếc Tapo C310 sang giám sát khu vực chung.

### Quyết định 4 (`H9`, `H22` — Quy chế Bằng chứng Đối soát Lương)
- `[ ]` Phê duyệt cơ chế lưu MP4 H.264 gốc sạch + tệp sidecar `.meta.json` ký `HMAC-SHA256` (kèm chức năng kết xuất file MP4 đóng dấu burn-in bằng FFmpeg khi giải quyết khiếu nại) và sổ cái thu hồi/nhập bù công hai chiều với ERPNext.

---

*Chữ ký phê duyệt PM / Pháp chế*: ................................................... · *Ngày*: ....../....../2026
