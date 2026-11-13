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
    const tierBadge = r.verification_tier && !noShow ? `<span class="pill info">${esc(r.verification_tier)}</span> ` : "";
    const hist = noShow ? "" : `<button class="btn" onclick="openHistory('${esc(r.code)}','${r.date}')">Xem clip</button>`;
    const revBtn = r.ledger_id ? ` <button class="btn" onclick="revokeEntry(${r.ledger_id},'${esc(r.code)}')">Thu hồi</button>` : "";
    return `<tr class="${noShow ? "absent" : ""}"><td>${day}</td><td>${esc(r.code)}</td><td>${esc(r.name)}</td>
      <td><b>${fmtHM(r.check_in)}</b></td><td class="${r.late_min ? "warn" : ""}">${r.late_min ? `${r.late_min} phút` : "—"}</td>
      <td><span class="pill ${cls}">${label}</span></td><td>${tierBadge}${hist}${revBtn}</td></tr>`;
  }).join("") : `<tr><td colspan="7" class="muted">Không có dữ liệu chấm công trong khoảng này.</td></tr>`;
  loadAuditQueue();
}

async function revokeEntry(ledgerId, code) {
  const reason = prompt(`Nhập lý do thu hồi lượt chấm công của ${code} (sẽ đồng bộ hủy sang HRM):`);
  if (!reason || !reason.trim()) return;
  const r = await fetch("/api/attendance/revoke", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({target_ledger_id: ledgerId, reason: reason.trim()})
  });
  if (!r.ok) { const d = await r.json(); alert(d.detail || "Lỗi thu hồi"); return; }
  loadAttendance();
  loadToday();
}

async function loadAuditQueue() {
  const body = $("#auditQueueBody");
  if (!body) return;
  const f = $("#attForm");
  const q = new URLSearchParams({date_from: f.date_from.value, date_to: f.date_to.value}).toString();
  const r = await fetch(`/api/attendance/audit-queue?${q}`);
  if (!r.ok) return;
  const items = await r.json();
  body.innerHTML = items.length ? items.map(it => `<tr>
    <td>${esc(it.work_date)}</td><td><b>${esc(it.code)}</b></td><td>${fmtHM(it.effective_ts)}</td>
    <td class="warn">${esc(it.reason || "profile_verified")}</td>
    <td><div class="acts">
      <button onclick="openHistory('${esc(it.code)}','${esc(it.work_date)}')">Clip</button>
      <button class="sel" onclick="resolveAudit(${it.id}, true)">Xác nhận</button>
      <button onclick="resolveAudit(${it.id}, false)">Thu hồi</button>
    </div></td></tr>`).join("") : `<tr><td colspan="5" class="muted">Không có ca ngoại lệ nào chờ duyệt.</td></tr>`;
}

async function resolveAudit(ledgerId, confirmOk) {
  let reason = "";
  if (!confirmOk) {
    reason = prompt("Nhập lý do thu hồi ca ngoại lệ này:") || "";
    if (!reason.trim()) return;
  }
  await fetch(`/api/attendance/audit-resolve/${ledgerId}`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({confirm: confirmOk, reason})
  });
  loadAttendance();
  loadToday();
}

(() => {
  const f = $("#attForm");
  setRange(f, "today");
  f.querySelectorAll("[data-range]").forEach(b => b.onclick = () => { setRange(f, b.dataset.range); loadAttendance(); });
  f.onsubmit = ev => { ev.preventDefault(); loadAttendance(); };
  f.code.onchange = loadAttendance;
  const mf = $("#manualAttForm");
  if (mf) {
    mf.work_date.value = isoDay(new Date());
    mf.onsubmit = async ev => {
      ev.preventDefault();
      const msg = $("#manualAttMsg");
      const payload = Object.fromEntries(new FormData(mf).entries());
      const r = await fetch("/api/attendance/manual", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload)
      });
      const d = await r.json();
      if (!r.ok) { msg.className = "msg err"; msg.textContent = d.detail || "Lỗi ghi sổ cái"; return; }
      msg.className = "msg ok"; msg.textContent = `Đã ghi nhận dự phòng cho ${payload.employee_code}`;
      mf.reason.value = "";
      loadAttendance();
      loadToday();
    };
  }
  tabHooks.attendance = loadAttendance;
})();
