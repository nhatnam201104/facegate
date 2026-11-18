# Đề xuất sửa Hiến pháp: v1.1.0 → v1.2.0

**Trạng thái**: CHỜ DUYỆT (mục C trong [approvals.md](approvals.md)). Theo Governance, sửa đổi cần **PM + Pháp chế phê duyệt bằng văn bản**. Chỉ áp dụng vào `.specify/memory/constitution.md` sau khi mục C là `APPROVED`.

**Lý do**: Nguyên tắc III v1.1.0 liệt kê `3DDFA_V2` và `EdgeFace-XS` là "giấy phép thương mại hóa được", nhưng thực tế:
- EdgeFace-XS: trọng số CC BY-NC-SA 4.0 (Idiap), tức phi thương mại.
- 3DDFA_V2: code MIT, nhưng tham số `tddfa_*.bin` phái sinh từ Basel Face Model (giấy phép phi thương mại, cấm phân phối lại) và trọng số huấn luyện trên 300W-LP (chỉ dành cho nghiên cứu).

Như vậy Hiến pháp đang tự mâu thuẫn với chính yêu cầu giấy phép của nó.

## Thay đổi 1: Nguyên tắc III, gạch đầu dòng thứ nhất

**Hiện tại (v1.1.0, dòng 13):**

> - Toàn bộ mô hình và thuật toán (`YuNet`, `3DDFA_V2`, `EdgeFace-XS`, `YOLOX-Nano`, `MiniFASNet`, QR/Barcode `zxing-cpp`/OpenCV, `ORB/SIFT + RANSAC`) phải dùng giấy phép thương mại hóa được (MIT / Apache-2.0 / BSD) và vận hành trên máy chủ Linux CPU hiện có (hỗ trợ `OpenVINOExecutionProvider` / iGPU khi khả dụng), không yêu cầu mua GPU rời trong giai đoạn PoC.

**Đề xuất (v1.2.0):**

> - Toàn bộ mô hình và thuật toán (`YuNet`, `SFace`, ước lượng tư thế `solvePnP` 5 điểm, `YOLOX-Nano`, `MiniFASNet`, QR/Barcode `zxing-cpp`/OpenCV, `ORB/SIFT + RANSAC`) phải dùng giấy phép thương mại hóa được (MIT / Apache-2.0 / BSD) **cho cả code lẫn trọng số** và vận hành trên máy chủ Linux CPU hiện có (hỗ trợ `OpenVINOExecutionProvider` / iGPU khi khả dụng), không yêu cầu mua GPU rời trong giai đoạn PoC.
> - **Mọi file model phải có mục trong `server/models/manifest.json`** với `commercial_use: allowed`, giấy phép trọng số thuộc allow-list, SHA-256 đầy đủ và (nếu dữ liệu huấn luyện chỉ dành cho nghiên cứu) `legal_ack` của Pháp chế; bản phát hành phải qua `python server/scripts/license_gate.py --strict`. Cấm dùng dữ liệu phái sinh từ Basel Face Model và trọng số phát hành theo giấy phép phi thương mại (`*-NC-*`, "research only").

## Thay đổi 2: Dòng phiên bản

**Hiện tại:** `**Version**: 1.1.0 | **Ratified**: 2026-11-17 | **Last Amended**: 2026-11-18`

**Đề xuất:** `**Version**: 1.2.0 | **Ratified**: 2026-11-17 | **Last Amended**: <ngày phê duyệt C>`

Thêm dòng cuối file:

> *v1.2.0: thay EdgeFace-XS/3DDFA_V2 bằng SFace/PnP 5 điểm và bắt buộc manifest giấy phép (feature 002). Phê duyệt: <mã văn bản mục C trong specs/002-license-compliant-models/approvals.md>.*

## Không thay đổi

Nguyên tắc I, II, IV, V, Ràng buộc phần cứng, Quality Gates, Governance.
