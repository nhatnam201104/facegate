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
        <span class="muted">${KIND_VI[a.kind]} · score ${Number(a.score ?? 0).toFixed(2)}${a.liveness != null ? ` · thật ${Number(a.liveness).toFixed(2)}` : ""}</span>
        ${a.clip_ok ? `<a class="btn" href="${file(a.clip)}" download>Tải clip</a>` : ""}
      </div>
    </article>`).join("") : `<p class="muted">Không có lần xuất hiện nào trong khoảng đã chọn.</p>`;
}

function playClip(i) {
  const a = histRows[i];
  $("#playerTitle").innerHTML = histTitle(a);
  $("#playerSub").textContent = ` · ${new Date(a.start_ts * 1000).toLocaleString("vi-VN")} · ${fmtDur(a.duration)}`;
  $("#playerDl").href = file(a.clip);
  $("#playerVideo").src = file(a.clip);
  $("#player").hidden = false;
}
function closePlayer() { $("#playerVideo").pause(); $("#playerVideo").removeAttribute("src"); $("#player").hidden = true; }
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
