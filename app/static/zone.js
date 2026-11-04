// ---------- ve vung loi di: chi chuyen dong trong vung moi danh thuc che do cho
let zonePts = null;   // null = khong o che do ve

// vung hien thi thuc cua anh trong <img> (object-fit: contain co the de vien den)
function videoRect() {
  const img = $("#video"), nw = img.naturalWidth, nh = img.naturalHeight;
  if (!nw) return null;
  const box = img.getBoundingClientRect(), wrap = $(".video").getBoundingClientRect();
  const k = Math.min(box.width / nw, box.height / nh), w = nw * k, h = nh * k;
  return {x: box.left - wrap.left + (box.width - w) / 2, y: box.top - wrap.top + (box.height - h) / 2, w, h};
}

function drawZone() {
  const svg = $("#zoneSvg"), r = videoRect();
  if (!r || zonePts === null) return;
  Object.assign(svg.style, {left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px`});
  svg.setAttribute("viewBox", `0 0 ${r.w} ${r.h}`);
  const pts = zonePts.map(([x, y]) => `${x * r.w},${y * r.h}`).join(" ");
  svg.innerHTML = (zonePts.length >= 3 ? `<polygon points="${pts}"/>` : zonePts.length ? `<polyline points="${pts}"/>` : "")
    + zonePts.map(([x, y], i) => `<circle cx="${x * r.w}" cy="${y * r.h}" r="${i ? 5 : 7}"/>`).join("");
  $("#zoneSave").disabled = zonePts.length < 3;
}

function zoneMsg(text, cls = "") { $("#zoneMsg").className = `msg ${cls}`; $("#zoneMsg").textContent = text; }

async function startZoneEdit() {
  const z = await (await fetch("/api/motion-zone")).json();
  zonePts = z.full ? [] : z.zone.map(p => [...p]);
  $("#zoneSvg").toggleAttribute("hidden", false);   // SVG khong co thuoc tinh .hidden nhu HTML
  $("#zoneBar").hidden = false;
  $("#greet").hidden = true;
  zoneMsg(z.full ? "Đang dùng toàn khung hình" : "");
  drawZone();
}
function stopZoneEdit() { zonePts = null; $("#zoneSvg").toggleAttribute("hidden", true); $("#zoneBar").hidden = true; }

$("#zoneBtn").onclick = () => zonePts === null ? startZoneEdit() : stopZoneEdit();
$("#zoneSvg").onclick = e => {
  const b = $("#zoneSvg").getBoundingClientRect();
  const x = Math.min(1, Math.max(0, (e.clientX - b.left) / b.width));
  const y = Math.min(1, Math.max(0, (e.clientY - b.top) / b.height));
  zonePts.push([+x.toFixed(4), +y.toFixed(4)]);
  zoneMsg(""); drawZone();
};
$("#zoneUndo").onclick = () => { zonePts.pop(); drawZone(); };
$("#zoneCancel").onclick = stopZoneEdit;
$("#zoneFull").onclick = async () => {
  await fetch("/api/motion-zone", {method: "DELETE"});
  zonePts = []; drawZone(); zoneMsg("Đã chuyển về toàn khung hình", "ok");
};
$("#zoneSave").onclick = async () => {
  const r = await fetch("/api/motion-zone", {method: "POST", headers: {"Content-Type": "application/json"},
                                               body: JSON.stringify({zone: zonePts})});
  const data = await r.json();
  if (!r.ok) { zoneMsg(data.detail || r.statusText, "err"); return; }
  stopZoneEdit();
};
addEventListener("resize", drawZone);
