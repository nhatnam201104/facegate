// ---------- tab Hang hoa / SKU: dang ky mau (chup camera / upload / barcode), bang san luong & lich su
const SKU_STATUS = {active: "Đang quét", disabled: "Đã tắt"};
const SKU_PAGE_SIZE = 25;
let skuPage = 0;
let skuTotal = 0;
let skuPageCache = new Map();
let skuSelectedCode = "";

function renderBarcodesCell(s) {
  const bcs = Array.isArray(s.barcodes) && s.barcodes.length
    ? s.barcodes
    : (s.barcode ? [s.barcode] : []);
  if (!bcs.length) return `<span class="muted">—</span>`;
  return `<div class="sku-bc-list">${bcs.map(b => `<code>${esc(b)}</code>`).join("")}</div>`;
}

function fillSkuForm(item, msgText = "") {
  const form = $("#skuForm");
  if (!form || !item) return;
  form.code.value = item.code || "";
  form.name.value = item.name || "";
  const bcs = Array.isArray(item.barcodes) && item.barcodes.length
    ? item.barcodes
    : (item.barcode ? [item.barcode] : []);
  form.barcode.value = bcs.join(", ");
  form.uom.value = item.uom || "pcs";
  skuSelectedCode = item.code || "";
  document.querySelectorAll("#skuItemsBody tr.sku-row").forEach(tr => {
    tr.classList.toggle("selected", tr.dataset.code === skuSelectedCode);
  });
  const msg = $("#skuMsg");
  if (msg && msgText) {
    msg.className = "msg ok";
    msg.textContent = msgText;
  }
}

function skuPickRow(id) {
  const item = skuPageCache.get(Number(id));
  if (!item) return;
  fillSkuForm(item, `Đã chọn ${item.code} — bấm Lưu để cập nhật hoặc chụp bổ sung ảnh mẫu.`);
}
window.skuPickRow = skuPickRow;

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
    code: (f.code.value || "").trim(),
  }).toString();
}

async function refreshSkuFilterSuggestions(query = "") {
  const dl = $("#skuCodeOptions");
  if (!dl) return;
  try {
    const p = new URLSearchParams({limit: "200"});
    if (query.trim()) p.set("q", query.trim());
    const rows = await (await fetch(`/api/sku/items?${p}`)).json();
    const list = Array.isArray(rows) ? rows : (rows.items || []);
    dl.innerHTML = list.map(r =>
      `<option value="${esc(r.code)}">${esc(r.code)} · ${esc(r.name)}</option>`).join("");
  } catch (_) {}
}

async function loadSkuItems(resetPage = false) {
  if (resetPage) skuPage = 0;
  const qVal = ($("#skuSearchInput")?.value || "").trim();
  const stVal = $("#skuStatusFilter")?.value || "";
  const params = new URLSearchParams({
    paged: "true",
    limit: String(SKU_PAGE_SIZE),
    offset: String(skuPage * SKU_PAGE_SIZE),
  });
  if (qVal) params.set("q", qVal);
  if (stVal) params.set("status", stVal);

  const data = await (await fetch(`/api/sku/items?${params}`)).json();
  const rows = Array.isArray(data) ? data : (data.items || []);
  skuTotal = Array.isArray(data) ? rows.length : Number(data.total || 0);
  const maxPage = Math.max(0, Math.ceil(skuTotal / SKU_PAGE_SIZE) - 1);
  if (skuPage > maxPage && skuTotal > 0) {
    skuPage = maxPage;
    return loadSkuItems(false);
  }
  skuPageCache = new Map(rows.map(r => [Number(r.id), r]));

  const totalLbl = $("#skuTotalLbl");
  if (totalLbl) {
    totalLbl.textContent = `(${skuTotal} mã${qVal || stVal ? " khớp bộ lọc" : ""}) — click 1 dòng để đổ vào form`;
  }
  const pageInfo = $("#skuPageInfo");
  if (pageInfo) {
    const totalPages = Math.max(1, Math.ceil(skuTotal / SKU_PAGE_SIZE));
    pageInfo.textContent = `Trang ${skuPage + 1} / ${totalPages} (${skuTotal} SKU)`;
  }
  if ($("#skuPrevPage")) $("#skuPrevPage").disabled = skuPage <= 0;
  if ($("#skuNextPage")) $("#skuNextPage").disabled = (skuPage + 1) * SKU_PAGE_SIZE >= skuTotal;

  const body = $("#skuItemsBody");
  if (!body) return;
  body.innerHTML = rows.length ? rows.map(s => `<tr class="sku-row${s.code === skuSelectedCode ? " selected" : ""}" data-code="${esc(s.code)}" onclick="skuPickRow(${Number(s.id)})">
    <td>${s.thumb ? `<img src="${file(s.thumb)}" data-zoom alt="" onclick="event.stopPropagation()">` : ""}</td>
    <td><b>${esc(s.code)}</b></td>
    <td>${esc(s.name)} <span class="muted">(${esc(s.uom)})</span></td>
    <td>${renderBarcodesCell(s)}</td>
    <td>${s.n_templates} mẫu · ${s.total_kps} kp</td>
    <td><span class="pill ${s.status}">${SKU_STATUS[s.status] || s.status}</span></td>
    <td onclick="event.stopPropagation()"><div class="acts">
      <button type="button" onclick="skuPickRow(${Number(s.id)})">Chọn</button>
      ${s.status !== "active" ? `<button type="button" onclick="skuAct(${s.id},'activate')">Bật</button>` : `<button type="button" onclick="skuAct(${s.id},'disable')">Tắt</button>`}
      <button type="button" onclick="skuDel(${s.id},'${esc(s.code)}')">Xóa</button>
    </div></td>
  </tr>`).join("") : `<tr><td colspan="7" class="muted">${qVal || stVal ? "Không tìm thấy SKU khớp bộ lọc." : "Chưa có sản phẩm nào. Kéo từ ERP, nhập CSV hoặc thêm ở form bên trái."}</td></tr>`;
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
    const hdSel = $("#skuHoldDir");
    const hdWrap = $("#skuHoldDirWrap");
    const iwSel = $("#skuInWhen");
    const iwWrap = $("#skuInWhenWrap");
    if (cmSel && cfg.count_mode) {
      cmSel.value = cfg.count_mode;
      if (iwWrap) iwWrap.hidden = cfg.count_mode !== "line";
      if (hdWrap) hdWrap.hidden = cfg.count_mode !== "hold";
    }
    if (hdSel && cfg.hold_direction) hdSel.value = cfg.hold_direction;
    if (iwSel && cfg.in_when) iwSel.value = cfg.in_when;
    const whInp = $("#skuWarehouse");
    if (whInp && !whInp.dataset.touched && cfg.default_warehouse) {
      whInp.value = cfg.default_warehouse;
    }
    const zxBadge = $("#skuZxingBadge");
    if (zxBadge) {
      const autoTxt = cfg.erp_adapter === "erpnext" && cfg.erp_sync_interval_sec > 0
        ? ` · ERP tự đồng bộ mỗi ${Math.round(cfg.erp_sync_interval_sec / 60)}p`
        : "";
      zxBadge.textContent = (cfg.has_zxing ? "zxing-cpp 1D/2D đang bật" : "OpenCV fallback") + autoTxt;
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
  let codeSuggestTimer = null;
  ff.code.oninput = () => {
    clearTimeout(codeSuggestTimer);
    codeSuggestTimer = setTimeout(() => refreshSkuFilterSuggestions(ff.code.value), 220);
  };
  ff.code.onchange = loadSkuAnalytics;

  const cmSel = $("#skuCountMode");
  const hdSel = $("#skuHoldDir");
  const iwSel = $("#skuInWhen");
  const erpMsg = $("#skuErpMsg");
  const whInp = $("#skuWarehouse");
  if (whInp) whInp.oninput = () => { whInp.dataset.touched = "1"; };

  async function saveSkuCountingCfg() {
    if (!cmSel) return;
    const modeVal = cmSel.value;
    if ($("#skuInWhenWrap")) $("#skuInWhenWrap").hidden = modeVal !== "line";
    if ($("#skuHoldDirWrap")) $("#skuHoldDirWrap").hidden = modeVal !== "hold";
    try {
      const r = await fetch("/api/sku/config", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          count_mode: modeVal,
          hold_direction: hdSel?.value || "IN",
          in_when: iwSel?.value || "down",
        }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || r.statusText);
      const detail = data.count_mode === "hold"
        ? `GIỮ ỔN ĐỊNH · ${data.hold_direction === "OUT" ? "Xuất kho (OUT)" : "Nhập kho (IN)"}`
        : `QUA VẠCH · Chiều Nhập: ${data.in_when.toUpperCase()}`;
      if (erpMsg) { erpMsg.className = "msg ok"; erpMsg.textContent = `Đã áp dụng: ${detail}`; }
    } catch (e) {
      if (erpMsg) { erpMsg.className = "msg err"; erpMsg.textContent = e.message; }
    }
  }
  if (cmSel) cmSel.onchange = saveSkuCountingCfg;
  if (hdSel) hdSel.onchange = saveSkuCountingCfg;
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

  if ($("#skuClearFormBtn")) {
    $("#skuClearFormBtn").onclick = () => {
      form.reset();
      form.uom.value = "pcs";
      skuSelectedCode = "";
      document.querySelectorAll("#skuItemsBody tr.sku-row.selected").forEach(tr => tr.classList.remove("selected"));
      applySkuSourceMode();
      const msg = $("#skuMsg");
      if (msg) { msg.className = "msg"; msg.textContent = ""; }
    };
  }

  if ($("#skuScanCodeBtn")) {
    $("#skuScanCodeBtn").onclick = async () => {
      const btn = $("#skuScanCodeBtn"), msg = $("#skuMsg");
      btn.disabled = true;
      msg.className = "msg"; msg.textContent = "Đang quét QR/Barcode từ camera…";
      try {
        const r = await fetch("/api/sku/scan-code", {method: "POST"});
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || r.statusText);
        if (!d.found || !d.codes?.length) {
          msg.className = "msg warn";
          msg.textContent = "Chưa thấy mã QR/Barcode nào trong khung hình. Đưa tem vào giữa camera rồi bấm lại.";
          return;
        }
        const first = d.codes[0];
        if (first.matched_sku) {
          fillSkuForm(first.matched_sku, `Khớp SKU sẵn có: ${first.matched_sku.code} (${first.barcode})`);
        } else {
          const cur = form.barcode.value.split(/[,;\n|]+/).map(s => s.trim()).filter(Boolean);
          if (first.barcode && !cur.includes(first.barcode)) cur.push(first.barcode);
          form.barcode.value = cur.join(", ");
          if (!form.code.value.trim() && first.barcode) {
            const safe = first.barcode.replace(/[^A-Za-z0-9_.\-]/g, "-").replace(/^-+|-+$/g, "").slice(0, 64);
            if (safe) form.code.value = safe;
          }
          msg.className = "msg ok";
          msg.textContent = `Đã đọc ${first.code_type || first.method}: ${first.barcode} (chưa có trong danh mục — điền tên rồi bấm Lưu).`;
        }
      } catch (e) {
        msg.className = "msg err"; msg.textContent = e.message;
      } finally {
        btn.disabled = false;
      }
    };
  }

  if ($("#skuCsvImportBtn")) {
    $("#skuCsvImportBtn").onclick = async () => {
      const inp = $("#skuCsvFile"), btn = $("#skuCsvImportBtn"), msg = $("#skuImportMsg");
      if (!inp?.files?.length) {
        msg.className = "msg err"; msg.textContent = "Chọn file CSV trước khi nhập.";
        return;
      }
      const fd = new FormData();
      fd.append("file", inp.files[0]);
      btn.disabled = true;
      msg.className = "msg"; msg.textContent = "Đang nhập danh mục CSV…";
      try {
        const r = await fetch("/api/sku/items/import", {method: "POST", body: fd});
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || r.statusText);
        const errHint = d.errors?.length ? ` · Lỗi đầu: ${d.errors[0]}` : "";
        msg.className = d.skipped ? "msg warn" : "msg ok";
        msg.textContent = `Đã nhập ${d.imported}/${d.total_rows} SKU (bỏ qua ${d.skipped})${errHint}`;
        inp.value = "";
        loadSkuItems(true);
      } catch (e) {
        msg.className = "msg err"; msg.textContent = e.message;
      } finally {
        btn.disabled = false;
      }
    };
  }

  let searchTimer = null;
  if ($("#skuSearchInput")) {
    $("#skuSearchInput").oninput = () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => loadSkuItems(true), 220);
    };
  }
  if ($("#skuStatusFilter")) $("#skuStatusFilter").onchange = () => loadSkuItems(true);
  if ($("#skuPrevPage")) $("#skuPrevPage").onclick = () => { if (skuPage > 0) { skuPage -= 1; loadSkuItems(false); } };
  if ($("#skuNextPage")) $("#skuNextPage").onclick = () => { if ((skuPage + 1) * SKU_PAGE_SIZE < skuTotal) { skuPage += 1; loadSkuItems(false); } };

  let liveTimer = null;
  tabHooks.sku = () => { loadSkuItems(); refreshSkuFilterSuggestions(); loadSkuConfigAndBatches(); loadSkuAnalytics(); updateSkuPreview(); };
  tabHooks.skuItems = () => { loadSkuItems(false); refreshSkuFilterSuggestions(ff.code.value); };
  tabHooks.skuBatches = loadSkuConfigAndBatches;
  tabHooks.skuLive = () => { clearTimeout(liveTimer); liveTimer = setTimeout(loadSkuAnalytics, 500); };
})();
