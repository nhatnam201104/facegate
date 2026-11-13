# Hướng dẫn Hạ tầng Hiện trường: Cấu hình TP-Link Tapo C310, Cô lập VLAN & Đồng bộ NTP (`chrony`)

Tài liệu triển khai cho Pha `K0` & `K1` theo Kế hoạch Kiến trúc v4 (`H16`, `H20`, `H27`–`H30`, `H32`).

---

## 1. Trình tự Thực hiện Bắt buộc (Tuyệt đối Không Đảo Ngược)

> **CẢNH BÁO**: Sau khi đưa camera Tapo C310 vào VLAN chặn Internet ở Bước 3, ứng dụng Tapo trên điện thoại có thể không thay đổi được các thiết lập nâng cao nữa. Bắt buộc hoàn tất Bước 1 khi camera còn mạng thường.

### Bước 1 — Cấu hình Một Lần trên Tapo App
1. Cắm nguồn DC 9V đi kèm và kết nối cáp mạng **Ethernet RJ45 (`10/100 Mbps`)** trực tiếp vào camera (cấm vận hành qua Wi-Fi 2.4 GHz tại cổng chấm công — `H30`).
2. **Không gắn thẻ nhớ `microSD`** vào khe cắm trên thân camera và **tắt hoàn toàn dịch vụ Tapo Care** (`H29`).
3. Vào `Device Settings > Advanced Settings > Camera Account`: tạo tài khoản `CAM_USER` / `CAM_PASS` dành riêng cho luồng RTSP nội bộ.
4. Vào `Live View` (hoặc `Device Settings > Video & Display > Video Mode`): chuyển từ `Auto` sang **`Day Mode`** để tắt vĩnh viễn đèn hồng ngoại 850 nm và giữ cứng chế độ màu RGB (`H27`).
5. Vào `Device Settings > Firmware Update`: tắt `Auto Update` để khóa phiên bản firmware đã kiểm định ổn định RTSP (`H30`).
6. **Thoát hẳn ứng dụng Tapo** và không mở xem trực tiếp trên điện thoại trong lúc hệ thống chạy để nhường trọn vẹn giới hạn `2 stream1 + 2 stream2` cho `MediaMTX` (`H20`).

### Bước 2 — Bố trí Quang học & Chiếu sáng Khu vực Cổng (`H26`, `H32`)
- Đặt vùng nhận diện cách ống kính **`1.5 m – 2.5 m`** (với cảm biến `1/2.7"` và tiêu cự `3.89 mm`, bề ngang khuôn mặt trên `/stream1` `2304×1296` đạt `80–130 px`).
- Lắp đèn LED trắng chiếu thuận về phía người bước vào sao cho độ rọi ngang tầm mặt đạt **≥ 250–300 lux**, giúp giữ màu sạch ở `Day Mode` và ép auto-exposure của C310 rút ngắn tốc độ màn trập (`≥ 1/125 s`), chống nhòe chuyển động ở tốc độ `15 fps`.

### Bước 3 — Cấu hình Dịch vụ NTP Nội bộ (`chrony`) trên Server FaceGate (`H16`, `H29`)
Thêm quyền cho dải VLAN camera vào `/etc/chrony/chrony.conf` trên máy chủ Linux chạy FaceGate:

```conf
# Đồng bộ với máy chủ giờ chuẩn phía trên (nếu máy chủ có mạng) và cấp giờ cho VLAN camera
allow 192.168.50.0/24
local stratum 10
```

Khởi động lại dịch vụ:
```bash
sudo systemctl restart chrony
```

### Bước 4 — Cô lập VLAN Camera & Chuyển hướng NTP Hardcode (`DNAT UDP 123`)
Firmware dòng Tapo phớt lờ DHCP Option 42 và gọi cứng tới máy chủ NTP mặc định (như `time.windows.com`). Trên Router/Firewall quản lý VLAN camera (`192.168.50.0/24`, IP Server FaceGate trên VLAN là `192.168.50.1`):

```bash
# 1. Chuyển hướng mọi gói tin NTP (UDP 123) từ camera về thẳng chrony trên Server FaceGate
iptables -t nat -A PREROUTING -i vlan50 -p udp --dport 123 -j DNAT --to-destination 192.168.50.1:123

# 2. Chỉ cho phép camera giao tiếp với Server FaceGate, chặn toàn bộ đường ra WAN/Cloud
iptables -A FORWARD -i vlan50 -d 192.168.50.1 -j ACCEPT
iptables -A FORWARD -i vlan50 -j DROP
```

> **Quy trình bảo trì**: Khi cần đổi cấu hình qua Tapo App, quản trị viên tạm mở rule WAN cho IP camera trong 5 phút, cấu hình xong lập tức đóng lại và kiểm tra `ffprobe` trên `MediaMTX`.
