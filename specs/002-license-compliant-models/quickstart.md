# Quickstart: Kiểm chứng 002-license-compliant-models

## Điều kiện tiên quyết
- Đã cài `requirements.txt`; đã chạy `python server/scripts/download_models.py`.
- Có bộ replay K0 (`deploy/k0_session_plan.yaml`) và baseline FAR/FRR của EdgeFace đo **trước** khi migrate (lưu vào `specs/002-license-compliant-models/baseline.json`).

## Kịch bản 1: License gate (US1)
```bash
python server/scripts/license_gate.py          # mong đợi: exit 0, mọi dòng "allowed"
git ls-files | grep -Ei 'tddfa|bfm|3ddfa|edgeface|w600k|scrfd'   # mong đợi: rỗng
cp /dev/null server/models/rogue.onnx && python server/scripts/license_gate.py; echo $?   # mong đợi: 1, nêu "rogue.onnx"
rm server/models/rogue.onnx
```

## Kịch bản 2: Server chặn cấu hình phi thương mại (US1, FR-006)
- Đặt `pipeline.backend: edgeface`, rồi chạy `./run.sh`. Mong đợi: thoát với lỗi `LICENSE: backend 'edgeface' đã bị gỡ`.
- Trả về `opencv`, chạy lại, rồi `curl localhost:<port>/api/status`. Mong đợi: `"license_ok": true`, `"pose_engine": "pnp5"`.

## Kịch bản 3: Migration gallery (US2, SC-005)
```bash
python server/scripts/migrate_gallery.py --from yunet2023mar+edgeface_xs_gamma_06_v41 --to auto --dry-run
python server/scripts/migrate_gallery.py --from yunet2023mar+edgeface_xs_gamma_06_v41 --to auto
```
Mong đợi: số `migrated` bằng số nhân viên active; các trường hợp `needs_reenroll` xuất hiện trong UI HR; `audit_logs` có bản ghi `gallery_migrate`.

## Kịch bản 4: Chất lượng & hiệu năng (US2, SC-002/003)

Điều kiện: đã có `data/k0_raw/` (video gốc, T001), `baseline.json` + `baseline_full_pipeline.json` (T001), bundle `data/k0_bundle_opencv` (T040).

```bash
# 4a. Độ chính xác: ghi lại bundle bằng SFace từ CÙNG video gốc (bundle EdgeFace chứa embedding cũ, không dùng được)
python server/scripts/k0_field_runner.py --source data/k0_raw/ --out data/k0_bundle_opencv --non-interactive

# 4b. Quét ngưỡng theo pose, chọn cặp có FRR nhỏ nhất trong các cặp thoả FAR <= FAR baseline (T039, T026)
python server/scripts/replay_eval.py data/k0_bundle_opencv --sweep 0.30:0.60:0.01 \
  --out specs/002-license-compliant-models/threshold_sweep.json \
  --report-md specs/002-license-compliant-models/threshold_sweep.md

# 4c. Kiểm lại với ngưỡng đã chọn
python server/scripts/replay_eval.py data/k0_bundle_opencv \
  --threshold-frontal <F> --threshold-profile <P> --out specs/002-license-compliant-models/replay_opencv.json
# Mong đợi: far <= baseline.json.far

# 4d. Hiệu năng full pipeline (T041, T027): cùng video camera yếu nhất, cùng máy, cùng --ep như baseline
python server/scripts/benchmark.py data/k0_raw/<video_cam_yeu_nhat> --full-pipeline --backend opencv \
  --min-faces 1 --out specs/002-license-compliant-models/full_pipeline_opencv.json
python server/scripts/benchmark.py data/k0_raw/<video_cam_yeu_nhat> --full-pipeline --backend opencv \
  --min-faces 10 --simulate-faces 10 --out specs/002-license-compliant-models/full_pipeline_opencv_10.json
# Mong đợi: cycle_ms.mean <= 200 ở mức 10 mặt; so sánh per_stage_ms với baseline_full_pipeline.json
```

**Không** dùng `--ten-faces-bench` làm bằng chứng SC-003: nó chỉ đo embed, vi phạm Hiến pháp III.

Nếu trượt: (a) hiệu chỉnh `thresholds.opencv` (**không** được hạ để đổi FAR, theo Hiến pháp II), (b) chuyển SFace sang ORT batch / OpenVINO EP, (c) chuyển sang phương án B (mua giấy phép EdgeFace) và báo PM.

## Kịch bản 5: Pose > 60° (US2)
- Nhân viên quay đầu ~75° trước camera. Mong đợi: overlay hiển thị `Unknown` / nhắc nhìn camera; không có check-in nào được ghi từ các khung hình này.

## Kịch bản 6: Tài nguyên phụ trợ (US3)
- `grep -n "backend:" config.yaml`: audio là `web`.
- `THIRD_PARTY_LICENSES.md`, `NOTICE` và `third_party/provenance/look_at_camera.md` tồn tại.

## Kịch bản 7: Hồi quy
```bash
pytest server/tests -q                          # 100% xanh (SC-004)
```
