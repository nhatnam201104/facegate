const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmtTime = ts => new Date(ts * 1000).toLocaleString("vi-VN");
const fmtHM = ts => ts ? new Date(ts * 1000).toLocaleTimeString("vi-VN", {hour: "2-digit", minute: "2-digit"}) : "—";
const isoDay = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const file = p => p ? `/files/${p}` : "";
const REASON = {
  small:"mặt nhỏ", blur:"mờ", light:"ánh sáng", yaw:"quay quá góc",
  pitch:"ngẩng/cúi quá mức", roll:"nghiêng đầu", occluded:"bị che khuất", cut:"bị cắt"
};
const SPOOF_REASON_VI = {
  screen_detected: "Phát hiện màn hình điện thoại / máy tính bảng",
  multi_face_single_body: "Hai khuôn mặt trên cùng một cơ thể",
  unattached_face: "Khuôn mặt không gắn kết với cơ thể người",
  planar_no_parallax: "Ảnh phẳng 2D thiếu thị sai chuyển động 3D",
  moire_pattern: "Phát hiện vân lưới màn hình phát video",
  texture_spoof: "Khuôn mặt từ ảnh in / màn hình"
};
const TIER_BADGE = {
  verified: ["Chính diện", "ok"],
  profile_verified: ["Góc nghiêng · Có clip", "warn"],
  rejected_spoof: ["Chặn giả mạo", "bad"],
  unverified: ["Chưa xác thực", ""]
};
const isActive = tab => $("#tab-" + tab).classList.contains("active");

// ---------- tabs (attendance.js / history.js dang ky them vao tabHooks)
const tabHooks = {employees: () => loadEmployees(), events: () => loadEvents()};
document.querySelectorAll("nav button").forEach(b => b.onclick = () => {
  document.querySelectorAll("nav button").forEach(x => x.classList.toggle("active", x === b));
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.id === "tab-" + b.dataset.tab));
  tabHooks[b.dataset.tab]?.();
  updateEnrollPreview();
});

// danh sach nguoi cho cac o loc (ke ca nhan vien da xoa con lich su)
async function loadPeople() {
  const people = await (await fetch("/api/people")).json();
  document.querySelectorAll(".peopleSel").forEach(sel => {
    const cur = sel.value;
    sel.innerHTML = `<option value="">Tất cả</option>` + people.map(p =>
      `<option value="${esc(p.code)}">${esc(p.name)} (${esc(p.code)})${p.deleted ? " – đã xóa" : ""}</option>`).join("");
    sel.value = cur;
  });
}

const reloadVideo = (img, url = "/video.mjpg") => img.src = `${url}?${Date.now()}`;
reloadVideo($("#video"));
$("#video").onerror = () => setTimeout(() => reloadVideo($("#video")), 2000);
let lastStatus = null;

// ---------- websocket
function connectWS() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onmessage = m => {
    const msg = JSON.parse(m.data);
    if (msg.type === "status") showStatus(msg.status);
    if (msg.type === "event") {
      addFeed(msg.event, true);
      if (msg.event.kind !== "unknown") showGreet(msg.event, msg.greet);
      loadToday();
      if (isActive("events")) loadEvents();
      if (isActive("attendance")) tabHooks.attendance?.();
    }
    if (msg.type === "greet") showGreet({kind: "dup", name: msg.name, code: msg.code, ts: msg.last_ts});
    if (msg.type === "appearance" && isActive("history")) tabHooks.historyLive?.();
    if (msg.type === "employees") { loadPeople(); if (isActive("employees")) loadEmployees(); }
    if (msg.type === "hrm" && isActive("events")) loadEvents();
  };
  ws.onclose = () => { $("#camDot").classList.remove("on"); $("#statusLine").textContent = "Mất kết nối server…"; setTimeout(connectWS, 2000); };
}
function showStatus(s) {
  lastStatus = s;
  const black = s.source.black, live = s.camera_connected && s.camera_fps > 0 && !black;
  $("#camDot").classList.toggle("on", s.camera_connected);
  $("#statusLine").textContent =
    `${s.backend} · ngưỡng ${s.threshold} · cam ${s.camera_connected ? s.camera_fps + " FPS" : "MẤT KẾT NỐI"} · detect ${s.detect_fps} FPS / ${s.detect_ms} ms · ${s.gallery} NV active · HRM ${s.hrm}`;
  // nguon + trang thai nhan dien
  const kindName = {webcam: "Webcam", file: "File video", rtsp: "RTSP"}[s.source.kind] || s.source.kind;
  $("#srcLabel").textContent = `${s.source.label} (${kindName})`;
  const sleeping = live && s.sleeping;
  $("#recBadge").classList.toggle("on", live && !sleeping);
  $("#recBadge").classList.toggle("sleep", sleeping);
  $("#recBadge").textContent = sleeping ? "CHẾ ĐỘ CHỜ" : live ? "ĐANG NHẬN DIỆN" : black ? "HÌNH ĐEN" : "KHÔNG CÓ HÌNH";
  $("#recBadge").title = sleeping ? `Không có người từ ${fmtHM(s.sleep_since)} — tự bật khi có người vào vùng cửa` +
    ` · đã bỏ qua ${s.wakes_rejected} lần chuyển động không phải người` : "";
  const zoned = s.motion_zone && JSON.stringify(s.motion_zone) !== JSON.stringify([[0, 0], [1, 0], [1, 1], [0, 1]]);
  $("#zoneBtn").textContent = zoned ? "Vùng cửa ✓" : "Vùng cửa";
  const ph = $("#videoPh"), hasImg = $("#video").naturalWidth > 0;
  ph.hidden = live && hasImg;
  ph.textContent = black ? `“${s.source.label}” đang trả hình đen — đang tự mở lại. Kiểm tra nắp che/phím tắt camera, ` +
      "và đóng ứng dụng khác đang dùng camera (Zoom, Teams, Camera…)."
    : !s.camera_connected ? `Không mở được nguồn “${s.source.label}” — đang thử lại…` +
      (s.source.kind === "webcam" ? " Có thể ứng dụng khác đang dùng camera (Zoom, Teams, Camera, cửa sổ FaceGate cũ…)." : "")
    : !hasImg ? "Đang tải hình…" : "Nguồn không gửi frame mới";
  if (live && !hasImg) reloadVideo($("#video"));
  // khong cho quay dang ky tu file video
  const camRadio = $("input[name=mode][value=camera]");
  camRadio.disabled = s.source.kind === "file";
  camRadio.parentElement.title = camRadio.disabled ? "Nguồn hiện tại là file video — đổi về webcam/RTSP ở tab Trực tiếp" : "";
  if (camRadio.disabled && camRadio.checked) { $("input[name=mode][value=file]").checked = true; applyEnrollMode(); }
}

// ---------- video source
$("#srcForm").kind.onchange = e => {
  const k = e.target.value;
  $("#srcValue").hidden = k === "file"; $("#srcFile").hidden = k !== "file";
  $("#srcValue").placeholder = k === "rtsp" ? "rtsp://user:pass@192.168.1.64:554/Streaming/Channels/102" : "0 hoặc /dev/video0";
  $("#srcValue").value = k === "webcam" ? "0" : "";
};
$("#srcForm").onsubmit = async ev => {
  ev.preventDefault();
  const f = ev.target, fd = new FormData(f), msg = $("#srcMsg"), btn = $("#srcBtn");
  if (fd.get("kind") === "file" && !f.file.files.length) { msg.className = "msg err"; msg.textContent = "Chọn file video"; return; }
  if (fd.get("kind") !== "file") fd.delete("file");
  btn.disabled = true; msg.className = "msg"; msg.textContent = fd.get("kind") === "file" ? "Đang tải video lên…" : "Đang chuyển nguồn…";
  try {
    const r = await fetch("/api/source", {method: "POST", body: fd});
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || r.statusText);
    msg.className = "msg ok"; msg.textContent = `Đã chuyển sang ${data.label}`;
    reloadVideo($("#video"));
  } catch (e) { msg.className = "msg err"; msg.textContent = e.message; }
  finally { btn.disabled = false; }
};
const ATT = {in: "Check-in", out: "Check-out"};
function feedTitle(e) {
  if (e.kind === "spoof") return `⚠ Giả mạo${e.code ? ` (giống ${esc(e.name)})` : ""}`;
  if (e.kind === "unknown") return "Người lạ";
  return `${esc(e.name)} (${esc(e.code)})`;
}
function addFeed(e, isNew) {
  const ul = $("#feed");
  if (ul.querySelector(".muted")) ul.innerHTML = "";
  const li = document.createElement("li");
  li.className = `${isNew ? "new " : ""}k-${e.kind || "checkin"}`;
  const tag = e.kind === "checkin"
    ? `<span class="tag ${e.verification_tier === "profile_verified" ? "prof" : e.att_type}">${e.verification_tier === "profile_verified" ? "Check-in · Nghiêng" : (ATT[e.att_type] || "Ghi nhận")}</span>`
    : "";
  const live = e.liveness != null ? ` · thật ${Number(e.liveness).toFixed(2)}` : "";
  const yawTxt = e.yaw != null ? ` · góc ${Math.round(Math.abs(e.yaw))}°` : "";
  li.innerHTML = `${e.snapshot ? `<img src="${file(e.snapshot)}" alt="">` : ""}
    <div><b>${feedTitle(e)}</b> ${tag}
    <span class="t">${fmtTime(e.ts)} · score ${Number(e.score).toFixed(2)}${yawTxt}${live}</span></div>`;
  ul.prepend(li);
  while (ul.children.length > 30) ul.lastChild.remove();
}
fetch("/api/events?limit=15").then(r => r.json()).then(evs => evs.reverse().forEach(e => addFeed(e, false)));

// ---------- man hinh chao kieu may cham cong
let greetTimer = null, audioCtx = null;
function beep(ok) {
  if (!$("#soundOn").checked) return;
  try {
    audioCtx = audioCtx || new AudioContext();
    const notes = ok ? [880, 1320] : [300, 220, 300];
    notes.forEach((f, i) => {
      const o = audioCtx.createOscillator(), g = audioCtx.createGain(), t = audioCtx.currentTime + i * 0.14;
      o.frequency.value = f; o.type = ok ? "sine" : "square";
      g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.2, t + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t + 0.13);
      o.connect(g).connect(audioCtx.destination); o.start(t); o.stop(t + 0.14);
    });
  } catch { /* trinh duyet chan am thanh truoc khi nguoi dung tuong tac */ }
}
const GREET_SUB = {on_time: () => "Đúng giờ", late: g => `Đi muộn ${g.minutes} phút`};
function showGreet(e, g) {
  const box = $("#greet");
  let cls, kind, name = e.name, sub;
  if (e.kind === "spoof") {
    const rCode = e.liveness_detail?.reason || e.spoof_reason || "";
    cls = "spoof"; kind = "Phát hiện giả mạo"; name = "Không chấm công";
    sub = SPOOF_REASON_VI[rCode] || "Khuôn mặt từ ảnh/màn hình — yêu cầu người thật đứng trước camera";
  } else if (e.kind === "dup") {
    cls = "dup"; kind = "Đã chấm công hôm nay"; sub = `Check-in lúc ${fmtHM(e.ts)}`;
  } else {
    const prof = e.verification_tier === "profile_verified";
    cls = g && g.status === "late" ? "warn" : "ok";
    kind = `Check-in · ${fmtHM(e.ts)}${prof ? " · Góc nghiêng (đã lưu clip)" : ""}`;
    sub = g ? GREET_SUB[g.status](g) : "";
    name = `Xin chào, ${e.name}`;
  }
  box.className = `greet g-${cls}`;
  $("#greetKind").textContent = kind; $("#greetName").textContent = name; $("#greetSub").textContent = sub;
  $("#greetImg").hidden = !e.snapshot; if (e.snapshot) $("#greetImg").src = file(e.snapshot);
  box.hidden = false;
  if (e.kind !== "dup") beep(e.kind !== "spoof");
  clearTimeout(greetTimer); greetTimer = setTimeout(() => box.hidden = true, e.kind === "spoof" ? 7000 : 4500);
}

async function loadToday() {
  const t = await (await fetch("/api/attendance/today")).json();
  $("#tdPresent").textContent = `${t.present}/${t.total}`;
  $("#tdLate").textContent = t.late; $("#tdSpoof").textContent = t.spoof;
  $("#tdShift").textContent = `Ca ${t.shift.start}–${t.shift.end} · trễ cho phép ${t.shift.grace_minutes}′`;
}
loadToday(); loadPeople();
setInterval(loadToday, 60000);
connectWS();

// ---------- enrollment
function applyEnrollMode() {
  const cam = $("input[name=mode]:checked").value === "camera";
  $("#fileRow").hidden = cam; $("#camRow").hidden = !cam; $("#profileOpts").hidden = !cam;
  $("#enrollBtn").textContent = cam ? "Bắt đầu quay" : "Xử lý đăng ký";
  updateEnrollPreview();
}
// chi mo luong preview khi dang o tab Dang ky + che do quay (tiet kiem ket noi)
function updateEnrollPreview() {
  const img = $("#enrollVideo");
  const show = $("#tab-enroll").classList.contains("active") && !$("#camRow").hidden;
  if (show && !img.getAttribute("src")) reloadVideo(img, "/video_raw.mjpg");  // luong sach, khong khung tracker
  if (!show && img.getAttribute("src")) img.removeAttribute("src");
}
document.querySelectorAll("input[name=mode]").forEach(r => r.onchange = applyEnrollMode);

// --- dang ky co huong dan kieu app ngan hang: 5 goc chu thap co dinh, dat dung goc moi sang buoc tiep
// luoi 3x3 bo tri theo huong tren man hinh (preview lat guong): cot trai = nguoi dung quay sang trai (h > 0)
function buildGrid(steps) {
  const box = $("#grid9");
  box.style.gridTemplateColumns = `repeat(${Math.min(steps.length, 4)}, 1fr)`;
  box.innerHTML = steps.map((st, i) => `<div data-i="${i}" title="${esc(st.text)}">${i + 1}. ${st.pose_group === "frontal" ? "Thẳng/Nhẹ" : "Nghiêng 80°"}</div>`).join("");
}
let guidedTimer = null, guidedLastStep = -1, guidedSteps = 0;
function speak(text) {
  if (!$("#soundOn").checked || !window.speechSynthesis) return;
  try { speechSynthesis.cancel(); const u = new SpeechSynthesisUtterance(text); u.lang = "vi-VN"; speechSynthesis.speak(u); } catch { /* khong co giong doc */ }
}
function renderGuided(g) {
  if (guidedSteps !== g.total) { buildGrid(g.steps); guidedSteps = g.total; }
  document.querySelectorAll("#grid9 div[data-i]").forEach(el => {
    const i = Number(el.dataset.i);
    el.classList.toggle("done", g.steps[i].done);
    el.classList.toggle("cur", g.state === "running" && i === g.step);
  });
  const running = g.state === "running";
  const yawInfo = running && g.current_pose ? ` (góc hiện tại: ${Math.round(Math.abs(g.current_pose.yaw))}°)` : "";
  $("#guideStep").textContent = running ? `${g.step + 1}/${g.total} · ${g.instruction}` : g.state === "done" ? `Đã hoàn tất ${g.total} bước` : "";
  $("#guideHint").textContent = (g.hint || "") + yawInfo;
  $("#guide .guideview").classList.toggle("good", g.ok);
  $("#guideOval").classList.toggle("ok", g.ok);
  $("#guideProg").style.strokeDasharray = `${running ? Math.round(g.hold * 100) : g.state === "done" ? 100 : 0} 100`;
  const t = running ? g.target : {};
  $("#arrL").classList.toggle("on", t.h === "left" || t.h === "left_deep");
  $("#arrR").classList.toggle("on", t.h === "right" || t.h === "right_deep");
  $("#arrU").classList.toggle("on", t.v === "up"); $("#arrD").classList.toggle("on", t.v === "down");
  if (running && g.step !== guidedLastStep) speak(g.instruction);
  guidedLastStep = g.step;
}
function endGuided(btn) {
  clearInterval(guidedTimer); guidedTimer = null;
  btn.disabled = false; $("#enrollCancel").hidden = true;
  $("#guideOval").classList.remove("ok"); $("#guideProg").style.strokeDasharray = "0 100";
  document.querySelectorAll(".arrow").forEach(a => a.classList.remove("on"));
}
async function pollGuided(code, btn) {
  const msg = $("#enrollMsg");
  try {
    const g = await (await fetch("/api/enroll/guided/status")).json();
    renderGuided(g);
    if (g.state === "done") {
      endGuided(btn); speak("Đăng ký hoàn tất");
      msg.className = "msg ok"; msg.textContent = `Đã hoàn tất ${g.total} bước đăng ký. Kiểm tra ở tab Nhân viên.`;
      showReport(code, g.report);
    } else if (g.state === "failed") {
      endGuided(btn); msg.className = "msg err"; msg.textContent = g.error || "Đăng ký thất bại";
    } else if (g.state === "cancelled") {
      endGuided(btn); msg.className = "msg"; msg.textContent = "Đã hủy.";
    }
  } catch { /* mat ket noi tam thoi: thu lai o lan poll sau */ }
}
$("#enrollCancel").onclick = () => fetch("/api/enroll/guided/cancel", {method: "POST"});
window.addEventListener("beforeunload", () => { if (guidedTimer) navigator.sendBeacon("/api/enroll/guided/cancel"); });
async function startGuided(fd, btn) {
  const msg = $("#enrollMsg");
  fd.delete("file"); fd.delete("mode");
  if (!fd.has("profile_90")) fd.set("profile_90", "false");
  if (!fd.has("supplement_only")) fd.set("supplement_only", "false");
  btn.disabled = true; msg.className = "msg"; msg.textContent = "Đứng trước camera và làm theo hướng dẫn trên màn hình";
  guidedLastStep = -1; guidedSteps = 0;
  try {
    const r = await fetch("/api/enroll/guided/start", {method: "POST", body: fd});
    const g = await r.json();
    if (!r.ok) throw new Error(g.detail || r.statusText);
    $("#enrollCancel").hidden = false;
    renderGuided(g);
    guidedTimer = setInterval(() => pollGuided(fd.get("code"), btn), 200);
  } catch (e) { msg.className = "msg err"; msg.textContent = e.message; btn.disabled = false; }
}
$("#enrollForm").onsubmit = async ev => {
  ev.preventDefault();
  const f = ev.target, fd = new FormData(f), cam = fd.get("mode") === "camera";
  const msg = $("#enrollMsg"), btn = $("#enrollBtn");
  if (cam) return startGuided(fd, btn);
  if (!f.file.files.length) { msg.className = "msg err"; msg.textContent = "Chọn file video"; return; }
  fd.delete("mode");
  btn.disabled = true; msg.className = "msg"; msg.textContent = "Đang xử lý video…";
  try {
    const r = await fetch("/api/enroll", {method: "POST", body: fd});
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || r.statusText);
    msg.className = "msg ok"; msg.textContent = "Đã xử lý. Chờ HR duyệt ở tab Nhân viên.";
    showReport(fd.get("code"), data);
  } catch (e) { msg.className = "msg err"; msg.textContent = e.message; }
  finally { btn.disabled = false; }
};
applyEnrollMode();
function showReport(code, r) {
  const rej = Object.entries(r.rejected || {}).map(([k, v]) => `${REASON[k] || k}: ${v}`).join(", ") || "không";
  $("#enrollResult").innerHTML = `<h3>Kết quả · ${esc(code)}</h3>
    <p>Đã chọn <b>${r.frames_selected}</b> frame từ ${r.frames_total} frame (đạt chất lượng: ${r.frames_good}).<br>
    <span class="muted">Không thấy mặt: ${r.no_face} · Nhiều người: ${r.multi_face} · Loại: ${rej} · Lệch nhất quán: ${r.outliers} · Cosine nhỏ nhất giữa các frame: ${r.min_pair_cosine}</span></p>
    <p class="muted">Góc nhìn: ${Object.entries(r.bins).map(([k, v]) => `${k}=${v}`).join(" · ")}</p>
    ${(r.warnings || []).map(w => `<p class="warn">⚠ ${esc(w)}</p>`).join("")}
    <div class="crops">${r.crops.map(c => `<figure><img src="${file(c)}"><figcaption>${esc(c.split("_").slice(1).join("_").replace(".jpg", ""))}</figcaption></figure>`).join("")}</div>`;
}

// ---------- employees
const STATUS = {pending: "Chờ duyệt", active: "Đang hoạt động", disabled: "Đã khóa"};
function jumpSupplement(code, name) {
  const f = $("#enrollForm");
  f.code.value = code; f.name.value = name;
  $("input[name=mode][value=camera]").checked = true;
  f.profile_90.checked = true;
  $("#supChk").checked = true;
  applyEnrollMode();
  document.querySelector('nav button[data-tab="enroll"]').click();
}
async function loadEmployees() {
  const rows = await (await fetch("/api/employees")).json();
  $("#empBody").innerHTML = rows.length ? rows.map(e => {
    const cov = e.pose_coverage || {};
    const badge90 = cov.ready_90
      ? `<span class="pill ok" title="Thẳng: ${cov.frontal} · Trái: ${cov.left_profile} · Phải: ${cov.right_profile}">Đủ góc 90°</span>`
      : `<span class="pill warn" title="Mới có góc chính diện — nên bổ sung 2 góc nghiêng sườn">Chỉ ≤40°</span>`;
    return `<tr>
    <td>${e.thumb ? `<img src="${file(e.thumb)}" data-zoom>` : ""}</td>
    <td>${esc(e.code)}</td><td>${esc(e.name)}</td>
    <td><span class="pill ${e.status}">${STATUS[e.status] || e.status}</span></td>
    <td>${e.n_emb} mẫu · ${badge90}</td>
    <td class="warn">${(e.report?.warnings || []).map(esc).join("<br>")}${e.n_emb === 0 ? "Không có embedding cho model hiện tại — cần đăng ký lại" : ""}</td>
    <td><div class="acts">
      ${e.status !== "active" ? `<button onclick="empAct(${e.id},'approve')">Duyệt</button>` : ""}
      ${!cov.ready_90 ? `<button onclick="jumpSupplement('${esc(e.code)}','${esc(e.name)}')">+ Bổ sung góc 90°</button>` : ""}
      ${e.status === "active" ? `<button onclick="empAct(${e.id},'disable')">Khóa</button>` : ""}
      <button onclick="empDel(${e.id},'${esc(e.code)}')">Xóa</button></div></td></tr>`;
  }).join("")
    : `<tr><td colspan="7" class="muted">Chưa có nhân viên. Sang tab Đăng ký.</td></tr>`;
}
async function empAct(id, a) { await fetch(`/api/employees/${id}/${a}`, {method: "POST"}); loadEmployees(); }
async function empDel(id, code) {
  if (!confirm(`Xóa nhân viên ${code} và toàn bộ dữ liệu khuôn mặt?\nCamera sẽ ngừng nhận diện ngay. Lịch sử chấm công vẫn được giữ.`)) return;
  await fetch(`/api/employees/${id}`, {method: "DELETE"}); loadEmployees();
}

// ---------- events + metrics
const LABELS_KNOWN = [["correct", "Đúng"], ["wrong", "Nhầm người"]];
const LABELS_UNK = [["unknown_employee", "Là NV"], ["unknown_stranger", "Người lạ"]];
const LABELS_SPOOF = [["spoof_attack", "Đúng giả mạo"], ["spoof_real", "Người thật"]];
const LABELS_BY_KIND = {checkin: LABELS_KNOWN, unknown: LABELS_UNK, spoof: LABELS_SPOOF};
async function loadEvents() {
  const tierSel = $("#evTierFilter");
  const tierParam = tierSel && tierSel.value ? `&tier=${encodeURIComponent(tierSel.value)}` : "";
  const [evs, m] = await Promise.all([
    fetch(`/api/events?limit=300${tierParam}`).then(r => r.json()),
    fetch("/api/metrics").then(r => r.json()),
  ]);
  const pct = v => v == null ? "—" : (v * 100).toFixed(1) + "%";
  $("#metrics").innerHTML = [
    ["Tổng sự kiện", m.events_total],
    ["Nhận diện (Thẳng / Nghiêng)", `${m.recognized} (${m.recognized_frontal ?? m.recognized} / ${m.recognized_profile ?? 0})`],
    ["Unknown", m.unknown],
    ["Giả mạo bị chặn", m.spoof],
    ["Đã gán nhãn", m.labeled],
    ["FAR (Tổng / Nghiêng)", `${pct(m.false_accept_rate)} / ${pct(m.false_accept_rate_profile)}`],
    ["False reject", pct(m.false_reject_rate)],
    ["Chặn nhầm người thật", pct(m.spoof_false_rate)],
    ["Unknown rate", pct(m.unknown_rate)],
    ["Latency p50 / p90", m.latency_ms_p50 == null ? "—" : `${Math.round(m.latency_ms_p50)} / ${Math.round(m.latency_ms_p90)} ms`],
  ].map(([k, v]) => `<div class="metric"><b>${v}</b><span>${k}</span></div>`).join("");
  $("#evBody").innerHTML = evs.length ? evs.map(e => {
    const opts = LABELS_BY_KIND[e.kind] || LABELS_UNK, known = e.kind === "checkin";
    const rCode = e.liveness_detail?.reason || "";
    const spoofSub = rCode && SPOOF_REASON_VI[rCode] ? `<br><small class="muted">${esc(SPOOF_REASON_VI[rCode])}</small>` : "";
    const who = known ? `<b>${esc(e.name)}</b> <span class="muted">${esc(e.code)}</span>`
      : e.kind === "spoof" ? `<span class="bad">⚠ Giả mạo</span>${e.code ? ` <span class="muted">giống ${esc(e.name)}</span>` : ""}${spoofSub}`
      : `<span class="bad">Unknown</span>`;
    const [tLbl, tCls] = TIER_BADGE[e.verification_tier || (known ? "verified" : "unverified")] || ["—", ""];
    const yawStr = e.yaw != null ? `${Math.round(e.yaw)}°` : "—";
    const poseCell = `<span class="pill ${tCls}">${tLbl}</span> <span class="muted">${yawStr}</span>`;
    return `<tr><td>${e.snapshot ? `<img src="${file(e.snapshot)}" data-zoom alt="">` : ""}</td>
      <td>${fmtTime(e.ts)}</td>
      <td>${who}</td>
      <td>${poseCell}</td>
      <td>${Number(e.score).toFixed(3)}${known ? ` <span class="muted">Δ${Number(e.margin).toFixed(2)}</span>` : ""}</td>
      <td>${e.liveness != null ? Number(e.liveness).toFixed(2) : "—"}</td>
      <td>${known ? Math.round(e.latency_ms) + " ms" : "—"}</td>
      <td><span class="pill ${e.hrm_status}" title="${esc(e.hrm_response)}">${e.hrm_status}</span></td>
      <td><div class="acts">${opts.map(([v, t]) => `<button class="${e.label === v ? "sel" : ""}" onclick="setLabel(${e.id},'${e.label === v ? "" : v}')">${t}</button>`).join("")}</div></td></tr>`;
  }).join("") : `<tr><td colspan="9" class="muted">Chưa có sự kiện.</td></tr>`;
}
async function setLabel(id, v) { const fd = new FormData(); fd.append("value", v); await fetch(`/api/events/${id}/label`, {method: "POST", body: fd}); loadEvents(); }
$("#reloadEvents").onclick = loadEvents;
if ($("#evTierFilter")) $("#evTierFilter").onchange = loadEvents;

// ---------- zoom
document.addEventListener("click", e => {
  if (e.target.matches("img[data-zoom], .crops img, .feed img, .greet img")) { $("#modalImg").src = e.target.src; $("#modal").hidden = false; }
  else if (e.target.closest("#modal")) $("#modal").hidden = true;
});
