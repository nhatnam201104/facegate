"""Dong bo danh muc Item & day lo kiem dem SKU thanh Draft Stock Entry sang ERPNext (erp_outbox).

Tuan thu docs/TAI_LIEU_TRIEN_KHAI_ERP.md (GAP-03 & IMP-04):
- Mac dinh chi gom cac su kien tu nguon chuan (QR / Barcode / zxing-cpp) chua gan lo (stock_batch_id IS NULL);
  cho phep tuy chon include_experimental=True khi thu kho chu dong xac nhan.
- Day sang ERPNext voi docstatus=0 (Draft Stock Entry cho thu kho duyet).
- Chong trung thuc chat: GET-before-retry kiem tra remarks chua [FG-IDEMP:<key>] truoc khi POST lai,
  kem Circuit Breaker va rate-limit an toan.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from ..store.db import SKU_CODE_RE

log = logging.getLogger("erp_sku")

STANDARD_SKU_METHODS = ("qr", "barcode", "zxing")


def _sanitize_item_code_for_local(raw_code: str) -> str | None:
    c = (raw_code or "").strip()
    if not c:
        return None
    if SKU_CODE_RE.match(c) and c not in {".", ".."}:
        return c
    cleaned = re.sub(r"[^A-Za-z0-9_.\-]", "-", c).strip("-")[:64]
    return cleaned if (cleaned and SKU_CODE_RE.match(cleaned)) else None


class MockSkuERP:
    def __init__(self, cfg: dict):
        self.data_dir = cfg["_paths"]["data"]
        self.outbox_path = self.data_dir / "erp_stock_mock.jsonl"
        self.items_seed_path = self.data_dir / "erp_items_mock.json"

    def fetch_items(self) -> list[dict]:
        if self.items_seed_path.is_file():
            try:
                data = json.loads(self.items_seed_path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
            except Exception as e:  # noqa: BLE001
                log.warning("Khong doc duoc erp_items_mock.json: %s", e)
        return [
            {"item_code": "ITEM-TEA-001", "item_name": "Trà Oolong MSW 500g", "stock_uom": "box", "barcode": "8936000000018", "disabled": 0},
            {"item_code": "ITEM-COF-002", "item_name": "Cà phê Arabica Cầu Đất 1kg", "stock_uom": "bag", "barcode": "8936000000025", "disabled": 0},
        ]

    def push_draft_stock_entry(self, batch: dict) -> tuple[bool, str]:
        rec = {
            "doctype": "Stock Entry",
            "docstatus": 0,
            "stock_entry_type": batch["stock_entry_type"],
            "direction": batch["direction"],
            "warehouse": batch.get("warehouse"),
            "idempotency_key": batch["idempotency_key"],
            "items": batch.get("items", []),
            "total_qty": batch.get("total_qty", 0.0),
            "batch_id": batch["id"],
            "ts": time.time(),
        }
        with open(self.outbox_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True, f"MOCK-STE-{batch['id']:04d}"


class ERPNextSkuAdapter:
    def __init__(self, cfg: dict):
        c = cfg.get("hrm", {}).get("erpnext", {})
        self.base_url = str(c.get("url", "http://localhost:8000")).rstrip("/")
        self.auth = f"token {c.get('api_key', '')}:{c.get('api_secret', '')}"
        self.default_warehouse = str(c.get("warehouse") or "Stores - MSW")

    def _headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        h = {"Authorization": self.auth, "Content-Type": "application/json"}
        if idempotency_key:
            h["X-Idempotency-Key"] = str(idempotency_key)
        return h

    PAGE_SIZE = 200
    MAX_PAGES = 100

    def _list_resource(self, doctype: str, fields: list[str]) -> list[dict]:
        """GET /api/resource/<doctype> co phan trang (limit_start) de danh muc > 500 Item khong bi cat."""
        rows: list[dict] = []
        q_fields = urllib.parse.quote(json.dumps(fields))
        for page in range(self.MAX_PAGES):
            url = (f"{self.base_url}/api/resource/{urllib.parse.quote(doctype)}"
                   f"?fields={q_fields}&limit_start={page * self.PAGE_SIZE}&limit_page_length={self.PAGE_SIZE}")
            req = urllib.request.Request(url, method="GET", headers=self._headers())
            with urllib.request.urlopen(req, timeout=12) as r:
                chunk = json.loads(r.read().decode("utf-8", errors="replace")).get("data") or []
            rows.extend(chunk)
            if len(chunk) < self.PAGE_SIZE:
                break
        return rows

    def fetch_items(self) -> list[dict]:
        rows = self._list_resource("Item", ["name", "item_code", "item_name", "stock_uom", "disabled"])
        # ERPNext luu barcode o bang con `Item Barcode` (parent = Item.name), khong nam tren Item
        barcodes: dict[str, str] = {}
        try:
            for b in self._list_resource("Item Barcode", ["parent", "barcode"]):
                if b.get("parent") and b.get("barcode"):
                    barcodes.setdefault(str(b["parent"]), str(b["barcode"]))
        except Exception as e:  # noqa: BLE001
            log.warning("Khong keo duoc Item Barcode (bo qua, giu barcode dang ky tai may): %s", e)
        out = []
        for it in rows:
            code = str(it.get("item_code") or it.get("name") or "").strip()
            if not code:
                continue
            out.append({
                "item_code": code,
                "item_name": str(it.get("item_name") or code),
                "stock_uom": str(it.get("stock_uom") or "Nos"),
                "barcode": barcodes.get(str(it.get("name") or code)) or it.get("barcode"),
                "disabled": int(it.get("disabled") or 0),
            })
        return out

    def _find_existing_by_idempotency(self, idempotency_key: str) -> str | None:
        """IMP-04 GET-before-retry: kiem tra phieu Draft Stock Entry da tao truoc do khi timeout HTTP."""
        marker = f"[FG-IDEMP:{idempotency_key}]"
        filters = urllib.parse.quote(json.dumps([["Stock Entry", "remarks", "like", f"%{marker}%"]]))
        fields = urllib.parse.quote(json.dumps(["name"]))
        url = f"{self.base_url}/api/resource/Stock%20Entry?filters={filters}&fields={fields}&limit_page_length=1"
        req = urllib.request.Request(url, method="GET", headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                data = (json.loads(r.read().decode("utf-8", errors="replace")).get("data") or [])
                if data and data[0].get("name"):
                    return str(data[0]["name"])
        except Exception:  # noqa: BLE001
            return None
        return None

    def push_draft_stock_entry(self, batch: dict) -> tuple[bool, str]:
        idem_key = str(batch["idempotency_key"])
        if int(batch.get("attempts") or 0) > 0:
            existing_doc = self._find_existing_by_idempotency(idem_key)
            if existing_doc:
                return True, existing_doc

        wh = str(batch.get("warehouse") or self.default_warehouse)
        is_out = str(batch.get("direction") or "IN").upper() == "OUT"
        items_payload = []
        for it in batch.get("items") or []:
            row = {
                "item_code": it["erp_item_code"],
                "qty": float(it["qty"]),
                "uom": it.get("uom") or "pcs",
            }
            if is_out:
                row["s_warehouse"] = wh
            else:
                row["t_warehouse"] = wh
            items_payload.append(row)

        payload = {
            "doctype": "Stock Entry",
            "docstatus": 0,  # Luon tao Draft cho thu kho duyet
            "stock_entry_type": batch.get("stock_entry_type") or ("Material Issue" if is_out else "Material Receipt"),
            "remarks": f"FaceGate Camera SKU Count [FG-IDEMP:{idem_key}] ({batch.get('date_from')}..{batch.get('date_to')})",
            "items": items_payload,
        }
        url = f"{self.base_url}/api/resource/Stock%20Entry"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers=self._headers(idem_key),
        )
        try:
            with urllib.request.urlopen(req, timeout=12) as r:
                raw = r.read().decode("utf-8", errors="replace")
                doc_id = str((json.loads(raw).get("data") or {}).get("name") or "")
                return True, doc_id or f"{r.status}"
        except urllib.error.HTTPError as e:
            return False, f"HTTP {e.code}: {e.read()[:200]!r}"
        except Exception as e:  # noqa: BLE001
            return False, str(e)


def build_stock_batch_from_events(
    db,
    *,
    t0: float,
    t1: float,
    date_from: str,
    date_to: str,
    direction: str = "IN",
    warehouse: str = "Stores - MSW",
    actor: str = "local:dev",
    include_experimental: bool = False,
) -> dict:
    """Gom cac su kien SKU chua day ERP trong khoang [t0, t1) thanh 1 lo Draft Stock Entry."""
    dir_c = (direction or "IN").strip().upper()
    if dir_c not in ("IN", "OUT"):
        raise ValueError("direction phải là 'IN' hoặc 'OUT'")

    with db.lock:
        sku_map = {r["code"]: r for r in db.list_sku_items()}
        rows = db.q(
            """SELECT * FROM sku_events
               WHERE ts>=? AND ts<? AND sku_id IS NOT NULL AND code IS NOT NULL AND code != ''
                     AND COALESCE(direction, 'IN')=? AND stock_batch_id IS NULL
               ORDER BY id ASC""",
            (float(t0), float(t1), dir_c),
        )
        grouped: dict[str, dict] = {}
        event_ids: list[int] = []
        skipped_exp = 0
        for r in rows:
            m = str(r.get("method") or "").lower()
            if not include_experimental and m not in STANDARD_SKU_METHODS:
                skipped_exp += 1
                continue
            code = str(r["code"])
            meta_sku = sku_map.get(code) or {}
            erp_code = (meta_sku.get("erp_item_code") or code).strip()
            g = grouped.setdefault(code, {
                "sku_code": code,
                "erp_item_code": erp_code,
                "name": r.get("name") or meta_sku.get("name") or code,
                "uom": r.get("uom") or meta_sku.get("uom") or "pcs",
                "qty": 0.0,
                "count": 0,
            })
            g["qty"] = round(g["qty"] + float(r.get("qty") or 1.0), 3)
            g["count"] += 1
            event_ids.append(int(r["id"]))

        if not event_ids:
            hint = " (có sự kiện RANSAC thử nghiệm bị bỏ qua — bật include_experimental nếu muốn gộp)" if skipped_exp else ""
            raise ValueError(f"Không có sự kiện SKU chiều {dir_c} nào mới chưa đồng bộ ERP trong khoảng ngày này{hint}")

        sig_src = f"{dir_c}|{warehouse}|{','.join(str(i) for i in event_ids)}"
        idem_key = "fg-ste-" + hashlib.sha256(sig_src.encode("utf-8")).hexdigest()[:24]
        entry_type = "Material Issue" if dir_c == "OUT" else "Material Receipt"
        items_list = sorted(grouped.values(), key=lambda x: x["sku_code"])
        return db.create_sku_stock_batch(
            idempotency_key=idem_key,
            actor=actor,
            direction=dir_c,
            stock_entry_type=entry_type,
            warehouse=warehouse,
            date_from=date_from,
            date_to=date_to,
            items=items_list,
            event_ids=event_ids,
        )


class SkuERPWorker:
    """Worker nen day cac lo sku_stock_batches sang ERPNext kem Circuit Breaker & gioi han toc do."""

    def __init__(self, cfg: dict, db, sku_gallery=None, on_update=None):
        kind = str(cfg.get("hrm", {}).get("adapter", "mock")).lower()
        self.adapter = ERPNextSkuAdapter(cfg) if kind == "erpnext" else MockSkuERP(cfg)
        self.kind = kind
        self.cfg = cfg
        self.db = db
        self.sku_gallery = sku_gallery
        self.on_update = on_update
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._sync_lock = threading.Lock()
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0
        self._thread = threading.Thread(target=self._run, daemon=True, name="sku-erp")
        self._thread.start()

    def notify(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def sync_items_from_erp(self) -> dict:
        """Keo danh muc Item tu ERPNext / Mock ve bang sku_items, tu khoa Item disabled."""
        raw_items = self.adapter.fetch_items()
        upserted = 0
        disabled_cnt = 0
        skipped = 0
        default_wh = str(self.cfg.get("hrm", {}).get("erpnext", {}).get("warehouse") or "Stores - MSW")
        for it in raw_items:
            erp_code = str(it.get("item_code") or "").strip()
            local_code = _sanitize_item_code_for_local(erp_code)
            if not local_code:
                skipped += 1
                continue
            is_dis = bool(int(it.get("disabled") or 0))
            existing = self.db.get_sku_by_code(local_code)
            erp_barcode = str(it["barcode"]).strip() if it.get("barcode") else None
            if existing is None:
                name, uom, barcode, st = (str(it.get("item_name") or local_code),
                                          str(it.get("stock_uom") or "pcs"), erp_barcode,
                                          "disabled" if is_dis else "active")
            elif existing.get("erp_item_code"):
                # Item da lien ket ERP tu truoc: ERP la nguon su that cho ten/DVT; barcode & trang thai tat tay duoc giu
                name, uom = str(it.get("item_name") or existing["name"]), str(it.get("stock_uom") or existing["uom"])
                barcode = erp_barcode or existing.get("barcode")
                st = "disabled" if is_dis else existing["status"]
            else:
                # SKU dang ky tay trung ma: chi gan erp_item_code, KHONG ghi de ten/DVT/barcode/trang thai da dat o may
                name, uom, barcode = existing["name"], existing["uom"], existing.get("barcode")
                st = "disabled" if is_dis else existing["status"]
            try:
                self.db.upsert_sku_item(
                    code=local_code,
                    name=name,
                    barcode=barcode,
                    uom=uom,
                    status=st,
                    erp_item_code=erp_code,
                    erp_warehouse=default_wh,
                )
                upserted += 1
                if is_dis:
                    disabled_cnt += 1
            except ValueError:
                skipped += 1
        if self.sku_gallery is not None:
            self.sku_gallery.reload()
        return {"synced": upserted, "disabled": disabled_cnt, "skipped": skipped, "adapter": self.kind}

    def sync_once(self) -> int:
        with self._sync_lock:
            now = time.time()
            if now < self._circuit_open_until:
                return 0
            processed = 0
            for batch in self.db.pending_sku_stock_batches():
                ok, resp = self.adapter.push_draft_stock_entry(batch)
                if ok:
                    self._consecutive_failures = 0
                    self.db.set_sku_stock_batch_status(batch["id"], "sent", remote_doc_id=resp, error=None)
                else:
                    self._consecutive_failures += 1
                    self.db.set_sku_stock_batch_status(batch["id"], "failed", error=resp)
                    log.warning("Day ERP Stock Entry lo #%s that bai: %s", batch["id"], resp)
                    if self._consecutive_failures >= 3:
                        self._circuit_open_until = time.time() + 15.0
                        log.warning("Circuit breaker mo 15s sau %d loi lien tiep", self._consecutive_failures)
                        break
                processed += 1
                if self.on_update is not None:
                    self.on_update(batch["id"], "sent" if ok else "failed", resp if ok else None)
                time.sleep(0.1)  # gioi han <= 10 req/s bao ve ERP
            return processed

    def retry_batch(self, batch_id: int) -> bool:
        """Nguoi dung bam 'Thu lai': bo cho backoff + dong circuit breaker roi day ngay lo do."""
        if not self.db.retry_sku_stock_batch(batch_id):
            return False
        self._circuit_open_until = 0.0
        self._consecutive_failures = 0
        self.sync_once()
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.sync_once()
            except Exception:  # noqa: BLE001
                log.exception("Loi vong lap SkuERPWorker")
            self._wake.wait(timeout=10.0)
            self._wake.clear()
