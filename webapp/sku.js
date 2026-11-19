// ---------- tab Hang hoa / SKU: dang ky mau (chup camera / upload / barcode), bang san luong & lich su
const SKU_STATUS = {active: "Đang quét", disabled: "Đã tắt"};

function updateSkuPreview() {
  const img = $("#skuPreview");
  if (!img) return;
  const srcMode = document.querySelector("#skuForm input[name=source]:checked")?.value || "camera";
  const show = isActive("sku") && srcMode === "camera";
  if (show && !img.getAttribute("src")) reloadVideo(img, "/video_raw.mjpg");
  if (!show && img.getAttribute("src")) img.removeAttribute("src");
}
window.updateSkuPreview = updateSkuPreview;

function applySkuSourceMode() {
  const srcMode = document.querySelector("#skuForm input[name=source]:checked")?.value || "camera";
  $("#skuCamRow").hidden = srcMode !== "camera";
  $("#skuFileRow").hidden = srcMode !== "upload";
  $("#skuSaveBtn").textContent = srcMode === "camera"
    ? "Lưu & chụp mẫu"
    : srcMode === "upload" ? "Lưu & tải ảnh mẫu" : "Lưu mã SKU / Barcode";
  updateSkuPreview();
}

function skuFilterQuery() {
  const f = $("#skuFilterForm");
  return new URLSearchParams({
    date_from: f.date_from.value,
    date_to: f.date_to.value,
    code: f.code.value,
  }).toString();
}

async function loadSkuItems() {
  const rows = await (await fetch("/api/sku/items")).json();
  const sel = $("#skuCodeFilter");
  if (sel) {
    const cur = sel.value;
    sel.innerHTML = `<option value="">Tất cả</option>` + rows.map(r =>
      `<option value="${esc(r.code)}">${esc(r.code)} · ${esc(r.name)}</option>`).join("");
    sel.value = cur;
  }
  const body = $("#skuItemsBody");
  if (!body) return;
  body.innerHTML = rows.length ? rows.map(s => `<tr>
    <td>${s.thumb ? `<img src="${file(s.thumb)}" data-zoom alt="">` : ""}</td>
    <td><b>${esc(s.code)}</b></td>
    <td>${esc(s.name)} <span class="muted">(${esc(s.uom)})</span></td>
    <td>${s.barcode ? `<code>${esc(s.barcode)}</code>` : `<span class="muted">—</span>`}</td>
    <td>${s.n_templates} mẫu · ${s.total_kps} kp</td>
    <td><span class="pill ${s.status}">${SKU_STATUS[s.status] || s.status}</span></td>
    <td><div class="acts">
      ${s.status !== "active" ? `<button type="button" onclick="skuAct(${s.id},'activate')">Bật</button>` : `<button type="button" onclick="skuAct(${s.id},'disable')">Tắt</button>`}
      <button type="button" onclick="skuDel(${s.id},'${esc(s.code)}')">Xóa</button>
    </div></td>
  </tr>`).join("") : `<tr><td colspan="7" class="muted">Chưa có sản phẩm nào. Thêm mẫu ở form bên trái.</td></tr>`;
}

async function skuAct(id, action) {
  await fetch(`/api/sku/items/${id}/${action}`, {method: "POST"});
  loadSkuItems();
}
async function skuDel(id, code) {
  if (!confirm(`Xóa SKU ${code} và ảnh mẫu khỏi danh mục nhận diện?`)) return;
  await fetch(`/api/sku/items/${id}`, {method: "DELETE"});
  loadSkuItems();
}
window.skuAct = skuAct;
window.skuDel = skuDel;

async function loadSkuAnalytics() {
  const q = skuFilterQuery();
  $("#skuCsv").href = `/api/sku/events.csv?${q}`;
  const [sum, evs] = await Promise.all([
    fetch(`/api/sku/summary?${q}`).then(r => r.json()),
    fetch(`/api/sku/events?${q}&limit=100`).then(r => r.json()),
  ]);
  $("#skuMetrics").innerHTML = [
    ["Nhập kho (IN)", sum.in_qty ?? sum.total_qty ?? 0],
    ["Xuất kho (OUT)", sum.out_qty ?? 0],
    ["Tổng lượt quét", sum.total_events ?? 0],
    ["Mã lạ / Chưa ĐK", sum.unknown_count ?? 0],
  ].map(([k, v]) => `<div class="metric"><b>${v}</b><span>${k}</span></div>`).join("");

  $("#skuSumBody").innerHTML = sum.summary?.length ? sum.summary.map(s => `<tr>
    <td><b>${esc(s.code)}</b></td>
    <td>${esc(s.name)}</td>
    <td>${s.count}</td>
    <td>${s.in_qty ?? s.qty}</td>
    <td>${s.out_qty ?? 0}</td>
    <td><b>${s.qty}</b></td>
    <td>${esc(s.uom)}</td>
    <td>${Object.entries(s.methods || {}).map(([m, n]) => `<span class="tag">${esc(m)}${m.endsWith("_ransac") ? " (Exp)" : ""}:${n}</span>`).join(" ")}</td>
    <td>${fmtTime(s.last_ts)}</td>
  </tr>`).join("") : `<tr><td colspan="9" class="muted">Chưa có dữ liệu đếm hàng hóa trong khoảng ngày này.</td></tr>`;

  $("#skuEvBody").innerHTML = evs.length ? evs.map(e => {
    const dir = (e.direction || "IN") === "OUT" ? "OUT" : "IN";
    const mName = String(e.method || "");
    const expBadge = mName.endsWith("_ransac") ? ` <small class="muted">(Exp)</small>` : "";
    return `<tr>
      <td>${e.snapshot ? `<img src="${file(e.snapshot)}" data-zoom alt="">` : ""}</td>
      <td>${fmtTime(e.ts)}</td>
      <td><span class="tag ${dir === "OUT" ? "out" : "in"}">${dir}</span></td>
      <td>${e.code ? `<b>${esc(e.code)}</b> · ${esc(e.name)}` : `<span class="bad">${esc(e.name || "Unknown SKU")}</span>`}</td>
      <td>${e.barcode ? `<code>${esc(e.barcode)}</code>` : `<span class="muted">—</span>`}</td>
      <td><span class="tag">${esc(mName)}</span>${expBadge}</td>
      <td>${Number(e.score || 0).toFixed(2)}${e.inliers ? ` <span class="muted">(${e.inliers} inliers)</span>` : ""}</td>
      <td>${e.qty ?? 1} ${esc(e.uom || "pcs")}${e.stock_batch_id ? ` <small class="muted">#${e.stock_batch_id}</small>` : ""}</td>
    </tr>`;
  }).join("") : `<tr><td colspan="8" class="muted">Chưa có sự kiện quét nào.</td></tr>`;
}

async function loadSkuConfigAndBatches() {
  try {
    const [cfg, batches] = await Promise.all([
      fetch("/api/sku/config").then(r => r.json()),
      fetch("/api/sku/erp/batches?limit=20").then(r => r.json()),
    ]);
    const cmSel = $("#skuCountMode");
    const iwSel = $("#skuInWhen");
    const iwWrap = $("#skuInWhenWrap");
    if (cmSel && cfg.count_mode) {
      cmSel.value = cfg.count_mode;
      if (iwWrap) iwWrap.hidden = cfg.count_mode !== "line";
    }
    if (iwSel && cfg.in_when) iwSel.value = cfg.in_when;
    const whInp = $("#skuWarehouse");
    if (whInp && !whInp.dataset.touched && cfg.default_warehouse) {
      whInp.value = cfg.default_warehouse;
    }
    renderSkuBatches(batches);
  } catch (_) {}
}

function renderSkuBatches(batches) {
  const body = $("#skuBatchBody");
  if (!body) return;
  body.innerHTML = batches?.length ? batches.map(b => {
    const itemsTxt = (b.items || []).map(it => `${esc(it.erp_item_code)}: <b>${it.qty}</b> ${esc(it.uom)}`).join(", ");
    const stCls = b.sync_status === "sent" ? "active" : (b.sync_status === "failed" ? "disabled" : "pending");
    const docTxt = b.remote_doc_id ? ` · <code>${esc(b.remote_doc_id)}</code>` : "";
    const errTxt = b.last_error ? ` <span class="bad">(${esc(b.last_error)})</span>` : "";
    const retryBtn = b.sync_status !== "sent"
      ? ` <button type="button" class="btn" onclick="skuRetryBatch(${Number(b.id)})">Thử lại</button>` : "";
    const nextTxt = b.sync_status === "failed" && b.next_retry_at
      ? ` <small class="muted">thử lại tự động lúc ${fmtHM(b.next_retry_at)} · lần ${b.attempts}</small>` : "";
    return `<tr>
      <td><b>#${b.id}</b></td>
      <td>${fmtTime(b.created_at)}</td>
      <td><span class="tag">${esc(b.direction)}</span> ${esc(b.stock_entry_type)}</td>
      <td>${esc(b.warehouse || "")}</td>
      <td>${itemsTxt || "—"}</td>
      <td><b>${b.total_qty}</b></td>
      <td><span class="pill ${stCls}">${esc(b.sync_status)}</span>${docTxt}${errTxt}${nextTxt}${retryBtn}</td>
    </tr>`;
  }).join("") : `<tr><td colspan="7" class="muted">Chưa có lô Draft Stock Entry nào được tạo.</td></tr>`;
}

async function skuRetryBatch(id) {
  const msg = $("#skuErpMsg");
  try {
    const r = await fetch(`/api/sku/erp/batches/${id}/retry`, {method: "POST"});
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.statusText);
    if (msg) {
      msg.className = d.sync_status === "sent" ? "msg ok" : "msg warn";
      msg.textContent = `Lô #${id}: ${d.sync_status}${d.remote_doc_id ? " → " + d.remote_doc_id : ""}`;
    }
  } catch (e) {
    if (msg) { msg.className = "msg err"; msg.textContent = e.message; }
  }
  loadSkuConfigAndBatches();
}
window.skuRetryBatch = skuRetryBatch;

(() => {
  const form = $("#skuForm");
  if (!form) return;
  form.querySelectorAll("input[name=source]").forEach(r => r.onchange = applySkuSourceMode);
  applySkuSourceMode();

  form.onsubmit = async ev => {
    ev.preventDefault();
    const fd = new FormData(form);
    const srcMode = fd.get("source");
    const msg = $("#skuMsg"), btn = $("#skuSaveBtn");
    if (srcMode === "upload" && !form.file.files.length) {
      msg.className = "msg err"; msg.textContent = "Chọn ảnh mẫu bao bì hoặc chuyển sang chụp từ camera";
      return;
    }
    if (srcMode !== "upload") fd.delete("file");
    if (!form.replace_templates.checked) fd.set("replace_templates", "false");
    else fd.set("replace_templates", "true");

    btn.disabled = true; msg.className = "msg"; msg.textContent = "Đang xử lý mẫu SKU…";
    try {
      const r = await fetch("/api/sku/items", {method: "POST", body: fd});
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || r.statusText);
      const tpl = data.template;
      const warnTxt = tpl?.warnings?.length ? ` · ⚠ ${tpl.warnings[0]}` : "";
      const kpsTxt = tpl ? ` (${tpl.num_kps} keypoints · nét ${tpl.sharpness})` : "";
      msg.className = tpl?.warnings?.length ? "msg warn" : "msg ok";
      msg.textContent = `Đã lưu ${data.code}${kpsTxt}${warnTxt}`;
      loadSkuItems();
    } catch (e) {
      msg.className = "msg err"; msg.textContent = e.message;
    } finally {
      btn.disabled = false;
    }
  };

  const ff = $("#skuFilterForm");
  ff.date_from.value = ff.date_to.value = isoDay(new Date());
  ff.querySelectorAll("[data-sku-range]").forEach(b => b.onclick = () => {
    const now = new Date(), from = new Date(now);
    if (b.dataset.skuRange === "week") from.setDate(now.getDate() - ((now.getDay() + 6) % 7));
    ff.date_from.value = isoDay(from);
    ff.date_to.value = isoDay(now);
    loadSkuAnalytics();
  });
  ff.onsubmit = ev => { ev.preventDefault(); loadSkuAnalytics(); };
  ff.code.onchange = loadSkuAnalytics;

  const cmSel = $("#skuCountMode");
  const iwSel = $("#skuInWhen");
  const erpMsg = $("#skuErpMsg");
  const whInp = $("#skuWarehouse");
  if (whInp) whInp.oninput = () => { whInp.dataset.touched = "1"; };

  async function saveSkuCountingCfg() {
    if (!cmSel) return;
    const modeVal = cmSel.value;
    if ($("#skuInWhenWrap")) $("#skuInWhenWrap").hidden = modeVal !== "line";
    try {
      const r = await fetch("/api/sku/config", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({count_mode: modeVal, in_when: iwSel?.value || "down"}),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || r.statusText);
      if (erpMsg) { erpMsg.className = "msg ok"; erpMsg.textContent = `Đã áp dụng kiểu đếm: ${data.count_mode.toUpperCase()}`; }
    } catch (e) {
      if (erpMsg) { erpMsg.className = "msg err"; erpMsg.textContent = e.message; }
    }
  }
  if (cmSel) cmSel.onchange = saveSkuCountingCfg;
  if (iwSel) iwSel.onchange = saveSkuCountingCfg;

  if ($("#skuSyncItemsBtn")) {
    $("#skuSyncItemsBtn").onclick = async () => {
      const btn = $("#skuSyncItemsBtn");
      btn.disabled = true;
      if (erpMsg) { erpMsg.className = "msg"; erpMsg.textContent = "Đang kéo danh mục Item từ ERP…"; }
      try {
        const r = await fetch("/api/sku/erp/sync-items", {method: "POST"});
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || r.statusText);
        if (erpMsg) {
          erpMsg.className = "msg ok";
          erpMsg.textContent = `Đã đồng bộ ${d.synced} Item từ ERP (${d.adapter}) · khóa ${d.disabled} mã ngừng dùng`;
        }
        loadSkuItems();
      } catch (e) {
        if (erpMsg) { erpMsg.className = "msg err"; erpMsg.textContent = e.message; }
      } finally {
        btn.disabled = false;
      }
    };
  }

  async function pushBatch(direction, btn) {
    btn.disabled = true;
    if (erpMsg) { erpMsg.className = "msg"; erpMsg.textContent = `Đang tạo Draft Stock Entry (${direction})…`; }
    try {
      const r = await fetch("/api/sku/erp/push-batch", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          date_from: ff.date_from.value,
          date_to: ff.date_to.value,
          direction,
          warehouse: whInp?.value || "",
          include_experimental: Boolean($("#skuIncExp")?.checked),
        }),
      });
      const b = await r.json();
      if (!r.ok) throw new Error(b.detail || r.statusText);
      if (erpMsg) {
        erpMsg.className = b.sync_status === "sent" ? "msg ok" : "msg warn";
        erpMsg.textContent = `Đã tạo lô #${b.id} (${b.total_qty} ĐVT) → ${b.remote_doc_id || b.sync_status}`;
      }
      loadSkuConfigAndBatches();
      loadSkuAnalytics();
    } catch (e) {
      if (erpMsg) { erpMsg.className = "msg err"; erpMsg.textContent = e.message; }
    } finally {
      btn.disabled = false;
    }
  }
  if ($("#skuPushInBtn")) $("#skuPushInBtn").onclick = () => pushBatch("IN", $("#skuPushInBtn"));
  if ($("#skuPushOutBtn")) $("#skuPushOutBtn").onclick = () => pushBatch("OUT", $("#skuPushOutBtn"));

  let liveTimer = null;
  tabHooks.sku = () => { loadSkuItems(); loadSkuConfigAndBatches(); loadSkuAnalytics(); updateSkuPreview(); };
  tabHooks.skuItems = loadSkuItems;
  tabHooks.skuBatches = loadSkuConfigAndBatches;
  tabHooks.skuLive = () => { clearTimeout(liveTimer); liveTimer = setTimeout(loadSkuAnalytics, 500); };
})();
