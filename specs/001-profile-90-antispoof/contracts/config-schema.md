# Configuration Contract (`config.yaml` Additions)

**Feature**: `001-profile-90-antispoof`
**Date**: 2026-11-11

Cấu trúc các khóa mới/cập nhật trong `config.yaml` (giữ giá trị mặc định an toàn nếu file cấu hình cũ chưa khai báo các khóa mới):

```yaml
pipeline:
  # opencv   : YuNet + 3DDFA_V2 + SFace (Apache-2.0 + MIT)
  # edgeface : YuNet + 3DDFA_V2 + EdgeFace-XS (Apache-2.0 + MIT + BSD-3, toi uu goc 90 do tren CPU)
  # adaface  : SCRFD/YuNet + 3DDFA_V2 + AdaFace-IR18 (chi dung nghien cuu / benchmark)
  backend: edgeface
  detect_fps: 5
  det_score: 0.55            # ha nhe tu 0.60 xuong 0.55 de khong sot mat o goc nghieng 80-90 do
  max_faces: 3
  stream_fps: 15
  stream_width: 960

quality:
  min_face: 60
  min_blur: 35               # do net chi tinh tren vung da mat nhin thay (visible ROI)
  min_brightness: 45
  max_brightness: 225
  max_yaw: 90                # mo rong tu 35 len 90 do (ket hop uoc luong 3DMM)
  max_pitch: 35              # chan goc nhin tu dinh dau qua doc
  max_roll: 35

recognition:
  frontal_max_yaw: 40        # <= 40 do: ngan 'frontal'; > 40 do: ngan 'left_profile' / 'right_profile'
  pose_hysteresis: 5         # do tre o bien 40..45 do de khong nhay ngan lien tuc
  thresholds:
    opencv:
      frontal: 0.42
      profile: 0.48          # nguong chat hon cho nhanh goc nghieng 40-90 do
    edgeface:
      frontal: 0.40
      profile: 0.46
    adaface:
      frontal: 0.40
      profile: 0.46
  margin: 0.06
  profile_margin: 0.08       # bien do top1 - top2 chat hon cho nhanh profile
  min_good_frames: 2         # cho ngan frontal (Best-shot uu tien)
  min_profile_frames: 4      # can nhieu frame hon khi chi co goc nghieng sau
  max_track_embeddings: 10   # toi da moi ngan tu the
  track_max_age: 2.0
  split_cosine: 0.25         # chi so sanh trong cung mot pose_group

liveness:
  enabled: true
  # --- Nhanh A: Chinh dien / ban nghieng nhe (|yaw| <= 40 do) ---
  threshold: 0.70            # MiniFASNet V2+V1SE
  spoof_below: 0.35
  min_frames: 4
  window: 8
  # --- Nhanh B: Da lop khong phu thuoc goc mat (bat buoc cho 40 < |yaw| <= 90 do, ho tro them cho nhanh A) ---
  context:
    enabled: true
    person_conf: 0.45        # nguong tin cay phat hien co the nguoi (YOLOX-Nano)
    medium_conf: 0.35        # nguong tin cay phat hien dien thoai / tablet / man hinh / khung anh
    require_body_coupling: true
    face_body_ratio: [0.12, 0.65]
  parallax:
    enabled: true
    min_frames: 6            # toi thieu ~1.0-1.2s di chuyen o 5 FPS detect
    min_travel_ratio: 0.08   # quang duong dich chuyen ngang toi thieu = 8% chieu rong khung hinh
    min_depth_score: 0.55    # vuot sai so chieu phang 2D (planar homography residual)
  moire:
    enabled: true
    max_screen_score: 0.65   # chan van luoi pixel man hinh phat video quay san

enrollment:
  sample_fps: 10
  min_frames: 5
  max_frames: 21             # tang tu 15 len 21 de chua them mau nghieng sau 2 ben
  per_bin: 3
  dedup_cosine: 0.95
  outlier_min_cosine: 0.42   # chi kiem tra nhat quan trong cung pose_group (khong loai nham mau 80 do)
  keep_video: false
  guided:
    enable_profile_90: true  # 7 buoc (5 buoc goc + 2 buoc nghieng suon ~75-85 do)
    yaw_enter: 18
    yaw_center: 9
    profile_yaw_min: 65      # goc xoay suon toi thieu cho buoc 6 & 7
    profile_yaw_max: 88
    pitch_enter: 10
    pitch_center: 8
    hold_seconds: 0.7
    timeout_seconds: 300
```

### Quy tắc Tương thích Ngược Cấu hình (`app/config.py`)
Nếu `recognition.thresholds.<backend>` trong file cấu hình cũ đang là một số thực đơn (ví dụ `opencv: 0.42` thay vì dict `{frontal, profile}`), hàm nạp cấu hình tự động ánh xạ thành `{"frontal": val, "profile": round(val + 0.06, 2)}` để không làm gãy các file `config.yaml` hiện hữu.
