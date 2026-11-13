# Interface Contract: Media Plane (`MediaMTX`), IPC Suy luận & Đồng bộ `<canvas>`

**Feature**: `003-clean-slate-architecture-v4`  
**Date**: 2026-11-13

---

## 1. Hợp đồng Cấu hình `MediaMTX` cho Tapo C310 (`mediamtx.yml` — `P1`, `H20`, `H24`, `H28`)

`MediaMTX` đóng vai trò là **Single RTSP Puller** duy nhất giao tiếp với camera vật lý, loại bỏ track âm thanh `pcm_alaw` ngay từ vòng ngoài và quản lý vòng đệm `tmpfs` RAM:

```yaml
rtspAddress: 127.0.0.1:8554
webrtcAddress: :8889
playback: yes
playbackAddress: 127.0.0.1:9996

paths:
  gate1_main:
    # Kéo đúng 1 kết nối /stream1 (2304x1296@15fps) và loại bỏ hoàn toàn audio pcm_alaw (-an)
    runOnInit: >-
      ffmpeg -hide_banner -loglevel warning -rtsp_transport tcp
      -i rtsp://${CAM_USER}:${CAM_PASS}@${CAM_IP}:554/stream1
      -map 0:v:0 -c:v copy -an -f rtsp rtsp://127.0.0.1:8554/gate1_main
    runOnInitRestart: yes
    # Vòng đệm fMP4 trên RAM tmpfs (${FACEGATE_RING_DIR}: mặc định /dev/shm/facegate_ring trên Linux, %TEMP%/facegate_ring trên Windows dev) tự hủy sau 10 phút (~300 MB/cam)
    record: yes
    recordPath: ${FACEGATE_RING_DIR}/%path/%Y-%m-%d_%H-%M-%S-%f
    recordFormat: fmp4
    recordPartDuration: 500ms
    recordSegmentDuration: 1s
    recordDeleteAfter: 10m

  gate1_sub:
    # Kéo đúng 1 kết nối /stream2 (640x360@15fps) chỉ phục vụ canh ngủ ngoài giờ cao điểm
    runOnInit: >-
      ffmpeg -hide_banner -loglevel warning -rtsp_transport tcp
      -i rtsp://${CAM_USER}:${CAM_PASS}@${CAM_IP}:554/stream2
      -map 0:v:0 -c:v copy -an -f rtsp rtsp://127.0.0.1:8554/gate1_sub
    runOnInitRestart: yes
    record: no
```

### 1.1. Luồng Xem Trực tiếp Trình duyệt (WebRTC WHEP)
- **Endpoint**: `POST http://<server-host>:8889/gate1_main/whep` (hoặc reverse-proxy qua `/media/gate1_main/whep` trên cùng cổng ứng dụng).
- **Đặc tính**: Phát trực tiếp H.264 passthrough (0% CPU encode lại, 0 audio track), tốc độ khung hình trên Tapo C310 đạt **13–15 fps** (`≥ 90%` nguồn).

### 1.2. Luồng Cắt Clip Quá khứ Không Mã hóa Lại (Playback API — `P2`, `H24`)
- Khi một hành trình (`Appearance`) kết thúc tại `end_ts_ns` (bắt đầu tại `start_ts_ns`), `Control Plane` gọi nội bộ:
  `GET http://127.0.0.1:9996/get?path=gate1_main&start=<ISO8601(start - pre_seconds)>&duration=<duration + pre + post>`
- Lưu luồng byte MP4 trả về vào `data/clips/YYYYMMDD/<id>.mp4`, tính `sha256` và ghi tệp `.meta.json` ký `HMAC-SHA256`.

---

## 2. Hợp đồng IPC Giữa `Perception Process` và `Control Plane` (`M1`, `P6`, `H15`)

- **Kênh truyền**: Unix Domain Socket (`data/run/perception.sock` trên Linux) hoặc Localhost TCP (`127.0.0.1:18091` trên Windows), định dạng **NDJSON (Newline-Delimited JSON)**.
- **3 loại bản tin chiều `Perception → Control Plane`**:
  1. **`frame_overlay`** (tần suất 5–8 Hz khi đang thức):
     ```json
     {
       "type": "frame_overlay",
       "camera_id": "gate-1",
       "seq": 10482,
       "ts_server_ns": 1794565200123456789,
       "infer_ms": 48.2,
       "decode_ms": 11.4,
       "mode": "active_shift",
       "objects": [
         {
           "track_id": 19,
           "kind": "face",
           "bbox_norm": [0.412, 0.215, 0.456, 0.308],
           "vel_norm": [0.085, 0.002],
           "state": "LIVE_CONFIRMED",
           "color_role": "ok",
           "label": "NV002 · Nguyễn Văn B",
           "verification_tier": "verified",
           "meta": {"yaw": -14.2, "llr_top1": 3.85, "llr_margin": 2.41, "quarantine": false}
         }
       ]
     }
     ```
  2. **`track_event`** (khi chốt `checkin`, `spoof`, hoặc đóng `appearance` để cắt clip).
  3. **`heartbeat`** (mỗi `2.0 s` — `H15`, `H30`):
     ```json
     {
       "type": "heartbeat",
       "camera_id": "gate-1",
       "ts_server_ns": 1794565202000000000,
       "stream1_connected": true,
       "stream2_connected": true,
       "fps_in": 14.9,
       "fps_detect": 6.2,
       "ffmpeg_ingest_alive": true,
       "sleeping": false
     }
     ```
- **Bản tin chiều `Control Plane → Perception`**:
  - `{"cmd": "reload_gallery", "version": 12}` (khi HR duyệt hoặc xóa nhân viên — có hiệu lực tức thì mà không khóa `engine_lock`).
  - `{"cmd": "update_zone", "motion_zone": [[0.1, 0.2], ...]}`.

---

## 3. Hợp đồng WebSocket Đồng bộ `<canvas>` Trình duyệt (`/ws` — `H8`)

1. **Bản tin `overlay` đẩy xuống trình duyệt**: Chuyển tiếp trực tiếp `frame_overlay` kèm `ws_send_mono_ms`.
2. **Thuật toán khớp thời gian chuẩn trên Client (`app/static/app.js`)**:
   - Lắng nghe `videoEl.requestVideoFrameCallback((now, metadata) => ...)`.
   - Với mỗi khung hình video vừa giải mã tại `metadata.receiveTime` (đơn vị ms trên trục `performance.now()`), tính thời điểm mục tiêu của metadata:
     `target_meta_ms = metadata.receiveTime + overlay_offset_ms` (trong đó `overlay_offset_ms` mặc định `0`, chỉnh được trong cấu hình sau phép đo đồng hồ mili-giây ở `K1`).
   - Tìm bản tin `overlay` gần nhất trong bộ đệm vòng 12 bản tin; nếu `|dt| <= 120 ms` thì nội suy tọa độ:
     `x1_draw = (bbox_norm[0] + vel_norm[0] * (dt / 1000)) * canvas.width`.
   - Nếu `now - last_overlay_recv_ms > 400 ms`: tự động ẩn hộp bao để không bao giờ treo khung ma khi mất kết nối AI.

---

## 4. Hợp đồng API Xuất Bằng chứng MP4 Đóng dấu Theo Yêu cầu (`P2`, `H9`)

- **Endpoint**: `POST /api/appearances/{appearance_id}/export-burnin` (Yêu cầu quyền `hr` hoặc `admin`, ghi vào `audit_logs`).
- **Hành vi**:
  1. Kiểm tra chữ ký `HMAC-SHA256` trong `.meta.json` và mã băm `clip_sha256` của file `.mp4` gốc sạch.
  2. Chạy tiến trình nền nhẹ kết xuất bộ lọc vẽ hộp + nhãn tiếng Việt + dấu thời gian server theo `timeline` bằng FFmpeg sang file tạm `burnin_<id>.mp4` và trả về `FileResponse` để đính kèm hồ sơ giải quyết khiếu nại lương.
