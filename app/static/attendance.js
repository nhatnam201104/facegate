// ---------- tab Cham cong: gio check-in moi ngay + so phut di muon
const ATT_STATUS = {
  ok: ["Đúng giờ", "ok"], late: ["Đi muộn", "warn"], absent: ["Vắng", "bad"], not_arrived: ["Chưa đến", "warn"],
};

function setRange(form, kind) {
  const now = new Date(), from = new Date(now);
  if (kind === "week") from.setDate(now.getDate() - ((now.getDay() + 6) % 7));
  if (kind === "month") from.setDate(1);
  form.date_from.value = isoDay(from);
  form.date_to.value = isoDay(now);
}

function attQuery() {
  const f = $("#attForm");
  return new URLSearchParams({date_from: f.date_from.value, date_to: f.date_to.value, code: f.code.value}).toString();
}

async function loadAttendance() {
  const q = attQuery();
  $("#attCsv").href = `/api/attendance.csv?${q}`;
  const r = await fetch(`/api/attendance?${q}`);
  const data = await r.json();
  if (!r.ok) {
    $("#attBody").innerHTML = `<tr><td colspan="7" class="bad">${esc(data.detail || r.statusText)}</td></tr>`;
    $("#attSum").innerHTML = "";
    return;
  }
  const sh = data.shift;
  $("#attShift").textContent = `Giờ vào ca ${sh.start}, trễ cho phép ${sh.grace_minutes} phút (sửa trong config.yaml)`;
  $("#attSum").innerHTML = data.summary.length ? data.summary.map(s => `<tr>
      <td>${esc(s.code)}</td><td><b>${esc(s.name)}</b></td><td>${s.days}</td><td>${s.on_time}</td>
      <td class="${s.late ? "warn" : ""}">${s.late}</td>
      <td class="${s.late_min ? "warn" : ""}">${s.late_min ? `${s.late_min} phút` : "—"}</td>
      <td class="${s.absent ? "bad" : ""}">${s.absent}</td></tr>`).join("")
    : `<tr><td colspan="7" class="muted">Không có dữ liệu.</td></tr>`;
  $("#attBody").innerHTML = data.rows.length ? data.rows.map(r => {
    const [label, cls] = ATT_STATUS[r.status] || [r.status, ""];
    const day = new Date(r.date + "T00:00:00").toLocaleDateString("vi-VN", {weekday: "short", day: "2-digit", month: "2-digit"});
    const noShow = !r.check_in;
    const hist = noShow ? "" : `<button class="btn" onclick="openHistory('${esc(r.code)}','${r.date}')">Xem clip</button>`;
    return `<tr class="${noShow ? "absent" : ""}"><td>${day}</td><td>${esc(r.code)}</td><td>${esc(r.name)}</td>
      <td><b>${fmtHM(r.check_in)}</b></td><td class="${r.late_min ? "warn" : ""}">${r.late_min ? `${r.late_min} phút` : "—"}</td>
      <td><span class="pill ${cls}">${label}</span></td><td>${hist}</td></tr>`;
  }).join("") : `<tr><td colspan="7" class="muted">Không có dữ liệu chấm công trong khoảng này.</td></tr>`;
}

(() => {
  const f = $("#attForm");
  setRange(f, "today");
  f.querySelectorAll("[data-range]").forEach(b => b.onclick = () => { setRange(f, b.dataset.range); loadAttendance(); });
  f.onsubmit = ev => { ev.preventDefault(); loadAttendance(); };
  f.code.onchange = loadAttendance;
  tabHooks.attendance = loadAttendance;
})();
