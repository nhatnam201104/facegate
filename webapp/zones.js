// ---------- tab Khu vuc xuong (Polygon Zones & Time-Based Presence Monitoring - GAP-04)
const ZONE_STATUS = {active: "Đang giám sát", disabled: "Đã tắt"};
const HEX_COLOR_RE = /^#[0-9a-fA-F]{6}$/;
const safeHex = (c, fb = "#3cc85a") => (typeof c === "string" && HEX_COLOR_RE.test(c.trim())) ? c.trim() : fb;
let editPoly = [];
let cachedZones = [];

function zonePreviewRect() {
  const img = $("#zonePreview"), wrap = $("#zoneDrawWrap");
  if (!img || !wrap) return null;
  const wb = wrap.getBoundingClientRect();
  if (wb.width < 10 || wb.height < 10) return null;
  const nw = img.naturalWidth, nh = img.naturalHeight;
  if (!nw || !nh) return {x: 0, y: 0, w: wb.width, h: wb.height};
  const ib = img.getBoundingClientRect();
  const k = Math.min(ib.width / nw, ib.height / nh);
  const w = nw * k, h = nh * k;
  return {
    x: ib.left - wb.left + (ib.width - w) / 2,
    y: ib.top - wb.top + (ib.height - h) / 2,
    w, h,
  };
}

function updateZonePreview() {
  const img = $("#zonePreview");
  if (!img) return;
  const show = isActive("zones");
  if (show && !img.getAttribute("src")) reloadVideo(img, "/video_raw.mjpg");
  if (!show && img.getAttribute("src")) img.removeAttribute("src");
  if (show) setTimeout(renderZoneEditSvg, 150);
}
window.updateZonePreview = updateZonePreview;

function renderZoneEditSvg() {
  const svg = $("#zoneEditSvg"), r = zonePreviewRect();
  if (!svg || !r) return;
  Object.assign(svg.style, {left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px`});
  svg.setAttribute("viewBox", `0 0 ${r.w} ${r.h}`);

  const bgPolys = cachedZones.filter(z => z.status === "active" && z.polygon?.length >= 3).map(z => {
    const pts = z.polygon.map(([x, y]) => `${x * r.w},${y * r.h}`).join(" ");
    const col = safeHex(z.color, "#50c8ff");
    const tx = z.polygon[0][0] * r.w + 6;
    const ty = Math.max(14, z.polygon[0][1] * r.h - 4);
    return `<polygon points="${pts}" style="fill:${col}22;stroke:${col};stroke-width:1.5"/>` +
      `<text x="${tx}" y="${ty}" fill="${col}" font-size="11" font-weight="600">${esc(z.code)}</text>`;
  }).join("");

  const curPts = editPoly.map(([x, y]) => `${x * r.w},${y * r.h}`).join(" ");
  const curShape = editPoly.length >= 3
    ? `<polygon points="${curPts}"/>`
    : (editPoly.length ? `<polyline points="${curPts}"/>` : "");
  const curDots = editPoly.map(([x, y], i) =>
    `<circle cx="${x * r.w}" cy="${y * r.h}" r="${i === 0 ? 6 : 4.5}"/>`).join("");

  svg.innerHTML = bgPolys + curShape + curDots;
  const cnt = $("#zonePolyCount");
  if (cnt) cnt.textContent = `Đã chấm: ${editPoly.length} điểm`;
}

function zoneFilterQuery() {
  const f = $("#zoneFilterForm");
  return new URLSearchParams({
    date_from: f.date_from.value,
    date_to: f.date_to.value,
    zone_code: f.zone_code.value,
  }).toString();
}

async function loadZoneItems() {
  const rows = await (await fetch("/api/zones")).json();
  cachedZones = rows;
  const sel = $("#zoneCodeFilter");
  if (sel) {
    const cur = sel.value;
    sel.innerHTML = `<option value="">Tất cả</option>` + rows.map(z =>
      `<option value="${esc(z.code)}">${esc(z.code)} · ${esc(z.name)}</option>`).join("");
    sel.value = cur;
  }
  const body = $("#zoneItemsBody");
  if (body) {
    body.innerHTML = rows.length ? rows.map(z => {
      const alertHtml = z.alert
        ? `<span class="bad">⚠ Trống ${z.empty_sec}s</span>`
        : `<span class="pill ${z.status}">${ZONE_STATUS[z.status] || z.status}</span>`;
      return `<tr>
        <td><b style="color:${safeHex(z.color)}">${esc(z.code)}</b></td>
        <td>${esc(z.name)}</td>
        <td><b>${z.present_count ?? 0}</b>/${z.min_persons} người${z.max_dwell_sec ? ` <small class="muted">(${z.max_dwell_sec}s)</small>` : ""}</td>
        <td><small class="muted">vào ${z.enter_confirm_sec}s · lưu ≥${z.min_dwell_sec}s · trễ ${z.exit_grace_sec}s</small></td>
        <td>${alertHtml}</td>
        <td><div class="acts">
          <button type="button" onclick="zoneFillForm(${z.id})">Sửa</button>
          ${z.status !== "active"
            ? `<button type="button" onclick="zoneAct(${z.id},'activate')">Bật</button>`
            : `<button type="button" onclick="zoneAct(${z.id},'disable')">Tắt</button>`}
          <button type="button" onclick="zoneDel(${z.id},'${esc(z.code)}')">Xóa</button>
        </div></td>
      </tr>`;
    }).join("") : `<tr><td colspan="6" class="muted">Chưa có khu vực nào. Chấm ≥ 3 đỉnh trên khung camera bên trái để tạo.</td></tr>`;
  }
  renderZoneEditSvg();
}

function zoneFillForm(id) {
  const z = cachedZones.find(x => x.id === id);
  const f = $("#zoneForm");
  if (!z || !f) return;
  f.code.value = z.code;
  f.name.value = z.name;
  f.color.value = z.color || "#3cc85a";
  f.enter_confirm_sec.value = z.enter_confirm_sec;
  f.min_dwell_sec.value = z.min_dwell_sec;
  f.exit_grace_sec.value = z.exit_grace_sec;
  f.min_persons.value = z.min_persons;
  f.alert_empty_sec.value = z.alert_empty_sec;
  editPoly = (z.polygon || []).map(p => [...p]);
  renderZoneEditSvg();
}

async function zoneAct(id, action) {
  await fetch(`/api/zones/${id}/${action}`, {method: "POST"});
  loadZoneItems();
}
async function zoneDel(id, code) {
  if (!confirm(`Xóa khu vực ${code} và lịch sử phiên hiện diện của vùng này?`)) return;
  await fetch(`/api/zones/${id}`, {method: "DELETE"});
  loadZoneItems();
  loadZoneAnalytics();
}
window.zoneFillForm = zoneFillForm;
window.zoneAct = zoneAct;
window.zoneDel = zoneDel;

async function loadZoneAnalytics() {
  const q = zoneFilterQuery();
  $("#zoneCsv").href = `/api/zones/sessions.csv?${q}`;
  const [sum, sess] = await Promise.all([
    fetch(`/api/zones/summary?${q}`).then(r => r.json()),
    fetch(`/api/zones/sessions?${q}&limit=100`).then(r => r.json()),
  ]);

  $("#zoneMetrics").innerHTML = [
    ["Người đang trong vùng", sum.live_present_total ?? 0],
    ["Tổng phiên đã đóng", sum.total_sessions ?? 0],
    ["Tổng giây hiện diện", sum.total_dwell_sec ?? 0],
    ["Cảnh báo trống vị trí", sum.live_alerts ?? 0],
  ].map(([k, v]) => `<div class="metric"><b>${v}</b><span>${k}</span></div>`).join("");

  $("#zoneSumBody").innerHTML = sum.summary?.length ? sum.summary.map(s => `<tr>
    <td><b>${esc(s.zone_code)}</b></td>
    <td>${esc(s.zone_name)}</td>
    <td>${s.sessions}</td>
    <td><b>${s.total_dwell_sec}s</b></td>
    <td>${s.avg_dwell_sec}s</td>
    <td>${s.last_ts ? fmtTime(s.last_ts) : "—"}</td>
  </tr>`).join("") : `<tr><td colspan="6" class="muted">Chưa có phiên hiện diện hoàn tất trong khoảng ngày này.</td></tr>`;

  $("#zoneSessBody").innerHTML = sess.length ? sess.map(s => `<tr>
    <td><b>${esc(s.zone_code)}</b> · ${esc(s.zone_name)}</td>
    <td>#${s.person_track_id}</td>
    <td>${fmtTime(s.enter_ts)}</td>
    <td>${s.exit_ts ? fmtTime(s.exit_ts) : `<span class="muted">Đang đứng…</span>`}</td>
    <td>${s.dwell_sec != null ? `<b>${s.dwell_sec}s</b>` : "—"}</td>
    <td><span class="pill ${s.status === "closed" ? "active" : "pending"}">${s.status === "closed" ? "Hoàn tất" : "Đang mở"}</span></td>
  </tr>`).join("") : `<tr><td colspan="6" class="muted">Chưa có phiên hiện diện nào.</td></tr>`;
}

(() => {
  const form = $("#zoneForm");
  if (!form) return;

  const svg = $("#zoneEditSvg");
  svg.onclick = ev => {
    const r = svg.getBoundingClientRect();
    if (r.width < 10 || r.height < 10) return;
    const x = Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width));
    const y = Math.min(1, Math.max(0, (ev.clientY - r.top) / r.height));
    if (editPoly.length < 32) {
      editPoly.push([+x.toFixed(4), +y.toFixed(4)]);
      renderZoneEditSvg();
    }
  };
  $("#zonePolyUndo").onclick = () => { editPoly.pop(); renderZoneEditSvg(); };
  $("#zonePolyClear").onclick = () => { editPoly = []; renderZoneEditSvg(); };
  const imgPrev = $("#zonePreview");
  if (imgPrev) imgPrev.addEventListener("load", () => { if (isActive("zones")) renderZoneEditSvg(); });
  addEventListener("resize", () => { if (isActive("zones")) renderZoneEditSvg(); });

  form.onsubmit = async ev => {
    ev.preventDefault();
    const msg = $("#zoneFormMsg"), btn = $("#zoneSaveBtn");
    if (editPoly.length < 3) {
      msg.className = "msg err";
      msg.textContent = "Hãy chấm tối thiểu 3 điểm trên khung camera để tạo vùng đa giác";
      return;
    }
    btn.disabled = true;
    msg.className = "msg";
    msg.textContent = "Đang lưu khu vực…";
    try {
      const payload = {
        code: form.code.value.trim(),
        name: form.name.value.trim(),
        color: form.color.value || "#3cc85a",
        polygon: editPoly,
        enter_confirm_sec: Number(form.enter_confirm_sec.value || 1.0),
        min_dwell_sec: Number(form.min_dwell_sec.value || 3.0),
        exit_grace_sec: Number(form.exit_grace_sec.value || 2.0),
        min_persons: Number(form.min_persons.value || 1),
        alert_empty_sec: Number(form.alert_empty_sec.value || 0.0),
      };
      const r = await fetch("/api/zones", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || r.statusText);
      msg.className = "msg ok";
      msg.textContent = `Đã lưu khu vực ${payload.code}`;
      editPoly = [];
      loadZoneItems();
      loadZoneAnalytics();
    } catch (e) {
      msg.className = "msg err";
      msg.textContent = e.message;
    } finally {
      btn.disabled = false;
    }
  };

  const zf = $("#zoneFilterForm");
  zf.date_from.value = zf.date_to.value = isoDay(new Date());
  zf.querySelectorAll("[data-zone-range]").forEach(b => b.onclick = () => {
    const now = new Date(), from = new Date(now);
    if (b.dataset.zoneRange === "week") from.setDate(now.getDate() - ((now.getDay() + 6) % 7));
    zf.date_from.value = isoDay(from);
    zf.date_to.value = isoDay(now);
    loadZoneAnalytics();
  });
  zf.onsubmit = ev => { ev.preventDefault(); loadZoneAnalytics(); };
  zf.zone_code.onchange = loadZoneAnalytics;

  let zTimer = null;
  tabHooks.zones = () => { loadZoneItems(); loadZoneAnalytics(); updateZonePreview(); };
  tabHooks.zonesItems = loadZoneItems;
  tabHooks.zonesLive = () => {
    clearTimeout(zTimer);
    zTimer = setTimeout(() => { loadZoneItems(); loadZoneAnalytics(); }, 400);
  };
})();
