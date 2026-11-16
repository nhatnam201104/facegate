// ---------- tab Lich su: moi lan xuat hien truoc camera + clip video, loc va tai ve
const KIND_VI = {employee: "Nhân viên", unknown: "Người lạ", spoof: "Giả mạo"};
const fmtDur = s => s >= 60 ? `${Math.floor(s / 60)}p${String(Math.round(s % 60)).padStart(2, "0")}s` : `${Math.round(s)}s`;
let histRows = [];

function histQuery() {
  const f = $("#histForm");
  return new URLSearchParams({date_from: f.date_from.value, date_to: f.date_to.value,
                              code: f.code.value, kind: f.kind.value}).toString();
}

function histTitle(a) {
  if (a.kind === "employee") return `${esc(a.name)} <span class="muted">${esc(a.code)}</span>`;
  if (a.kind === "spoof") return `⚠ Giả mạo${a.code ? ` <span class="muted">giống ${esc(a.name)}</span>` : ""}`;
  return "Người lạ";
}

async function loadHistory() {
  const r = await fetch(`/api/appearances?${histQuery()}`);
  const data = await r.json();
  const grid = $("#histGrid");
  if (!r.ok) { grid.innerHTML = `<p class="bad">${esc(data.detail || r.statusText)}</p>`; return; }
  histRows = data;
  const clips = data.filter(a => a.clip_ok).length;
  $("#histCount").textContent = `${data.length} lần xuất hiện · ${clips} clip`;
  $("#histZip").disabled = !clips;
  grid.innerHTML = data.length ? data.map((a, i) => `
    <article class="appear k-${a.kind}">
      <button class="thumb" ${a.clip_ok ? `onclick="playClip(${i})" aria-label="Xem clip"` : "disabled"}>
        ${a.snapshot ? `<img src="${file(a.snapshot)}" alt="" loading="lazy">` : ""}
        <span class="play">${a.clip_ok ? "▶" : a.clip_ready ? "" : "Đang ghi…"}</span>
        <span class="dur">${fmtDur(a.duration)}</span>
      </button>
      <div class="meta">
        <b>${histTitle(a)}</b>
        <span>${new Date(a.start_ts * 1000).toLocaleString("vi-VN")}</span>
        <span class="muted">${KIND_VI[a.kind]} · score ${Number(a.score ?? 0).toFixed(2)}${a.liveness != null ? ` · thật ${Number(a.liveness).toFixed(2)}` : ""}${a.legal_hold ? " · 🔒 Legal Hold" : ""}</span>
        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:4px">
          ${a.clip_ok ? `<a class="btn" href="${file(a.clip)}" download>Tải gốc</a>` : ""}
          <button type="button" class="btn" onclick="toggleHold(${a.id}, ${Boolean(a.legal_hold)})">${a.legal_hold ? "Gỡ khóa" : "🔒 Giữ bằng chứng"}</button>
        </div>
      </div>
    </article>`).join("") : `<p class="muted">Không có lần xuất hiện nào trong khoảng đã chọn.</p>`;
}

let activeSidecarTimeline = [];
let playerRaf = null;

async function toggleHold(appId, currentlyHeld) {
  let reason = "";
  if (!currentlyHeld) {
    reason = prompt("Nhập lý do khóa bảo vệ clip khỏi tự động xóa (legal_hold):") || "";
    if (!reason.trim()) return;
  }
  const r = await fetch(`/api/appearances/${appId}/legal-hold`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({hold: !currentlyHeld, reason})
  });
  if (!r.ok) { const d = await r.json(); alert(d.detail || "Không đổi được trạng thái khóa"); return; }
  loadHistory();
}

function _renderPlayerSidecarOverlay() {
  const vid = $("#playerVideo"), cvs = $("#playerCanvas");
  if (!vid || !cvs || $("#player").hidden) return;
  const rect = vid.getBoundingClientRect();
  if (rect.width > 10 && rect.height > 10) {
    cvs.width = rect.width; cvs.height = rect.height;
    const g = cvs.getContext("2d");
    g.clearRect(0, 0, cvs.width, cvs.height);
    const curMs = vid.currentTime * 1000.0;
    let active = [];
    for (const item of activeSidecarTimeline) {
      const dt = curMs - (item.offset_ms || 0);
      if (dt >= -120 && dt <= 350) active = item.objects || [];
    }
    g.lineWidth = 2; g.font = "600 13px system-ui, sans-serif";
    for (const o of active) {
      const bn = o.bbox_norm || [0, 0, 0, 0];
      const x1 = bn[0] * cvs.width, y1 = bn[1] * cvs.height;
      const w = (bn[2] - bn[0]) * cvs.width, h = (bn[3] - bn[1]) * cvs.height;
      const col = o.color_role === "ok" ? "#3cc85a" : o.color_role === "alert" ? "#e63c3c" : "#ffa500";
      g.strokeStyle = col; g.strokeRect(x1, y1, w, h);
      if (o.label) {
        const tw = g.measureText(o.label).width;
        g.fillStyle = col; g.fillRect(x1, Math.max(0, y1 - 22), tw + 10, 20);
        g.fillStyle = "#fff"; g.fillText(o.label, x1 + 5, Math.max(14, y1 - 7));
      }
    }
  }
  playerRaf = requestAnimationFrame(_renderPlayerSidecarOverlay);
}

async function playClip(i) {
  const a = histRows[i];
  $("#playerTitle").innerHTML = histTitle(a);
  $("#playerSub").textContent = ` · ${new Date(a.start_ts * 1000).toLocaleString("vi-VN")} · ${fmtDur(a.duration)}`;
  $("#playerDl").href = file(a.clip);
  const burnBtn = $("#playerBurninBtn");
  if (burnBtn) {
    burnBtn.hidden = !a.sidecar_ok;
    burnBtn.onclick = async () => {
      burnBtn.disabled = true;
      try {
        const r = await fetch(`/api/appearances/${a.id}/export-burnin`, {method: "POST"});
        if (!r.ok) { const d = await r.json(); alert(d.detail || "Lỗi kết xuất"); return; }
        const blob = await r.blob();
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url; link.download = `bang_chung_${a.code || a.kind}_${a.id}.mp4`;
        link.click();
        URL.revokeObjectURL(url);
      } finally { burnBtn.disabled = false; }
    };
  }
  activeSidecarTimeline = [];
  if (a.sidecar) {
    try {
      const meta = await (await fetch(file(a.sidecar))).json();
      activeSidecarTimeline = meta.timeline || [];
    } catch (_) {}
  }
  $("#playerVideo").src = file(a.clip);
  $("#player").hidden = false;
  cancelAnimationFrame(playerRaf);
  playerRaf = requestAnimationFrame(_renderPlayerSidecarOverlay);
}
function closePlayer() {
  cancelAnimationFrame(playerRaf);
  activeSidecarTimeline = [];
  $("#playerVideo").pause(); $("#playerVideo").removeAttribute("src"); $("#player").hidden = true;
}
$("#playerClose").onclick = closePlayer;
$("#player").onclick = e => { if (e.target.id === "player") closePlayer(); };
document.addEventListener("keydown", e => { if (e.key === "Escape" && !$("#player").hidden) closePlayer(); });

// mo tu bang cong: loc dung nguoi + ngay
function openHistory(code, day) {
  const f = $("#histForm");
  f.date_from.value = f.date_to.value = day;
  f.code.value = code; f.kind.value = "";
  document.querySelector('nav button[data-tab="history"]').click();
}

(() => {
  const f = $("#histForm");
  f.date_from.value = f.date_to.value = isoDay(new Date());
  f.onsubmit = ev => { ev.preventDefault(); loadHistory(); };
  f.code.onchange = f.kind.onchange = loadHistory;
  $("#histZip").onclick = () => { location.href = `/api/appearances.zip?${histQuery()}`; };
  let t = null;
  tabHooks.history = loadHistory;
  tabHooks.historyLive = () => { clearTimeout(t); t = setTimeout(loadHistory, 800); };  // gom nhieu cap nhat
})();
