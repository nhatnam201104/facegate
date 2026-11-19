"""Kiem thu Phase 2A: Multi-Instance SKU, dem vach IN/OUT, Draft Stock Entry ERP & Zone Engine Time-Based."""
from __future__ import annotations

import json
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from server.config import load_config
from server.store.db import DB
from server.vision.sku_scanner import SkuDetection, SkuGallery, SkuScanner, SkuTrackCounter


def _make_cfg(tmp_path: Path) -> dict:
    cfg_file = tmp_path / "cfg.yaml"
    cfg_file.write_text(
        f"storage:\n  data_dir: '{(tmp_path / 'data').as_posix()}'\nsku:\n  feature_type: orb\n  min_blur: 10.0\n",
        encoding="utf-8",
    )
    return load_config(cfg_file)


def _textured_card(seed: int, title: str, size=(260, 200)) -> np.ndarray:
    w, h = size
    rng = np.random.default_rng(seed)
    card = np.full((h, w, 3), 235, dtype=np.uint8)
    cv2.rectangle(card, (6, 6), (w - 7, h - 7), (30, 50, 120), 3)
    cv2.rectangle(card, (14, 14), (w - 15, 60), (40, 90, 180), -1)
    cv2.putText(card, title, (22, 46), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
    for _ in range(40):
        p1 = (int(rng.integers(18, w - 18)), int(rng.integers(70, h - 18)))
        p2 = (int(rng.integers(18, w - 18)), int(rng.integers(70, h - 18)))
        col = tuple(int(c) for c in rng.integers(20, 190, size=3))
        if rng.random() < 0.5:
            cv2.line(card, p1, p2, col, 2, cv2.LINE_AA)
        else:
            cv2.circle(card, p1, int(rng.integers(5, 16)), col, 2, cv2.LINE_AA)
    return card


def _det(code="SKU-01", bbox=(100.0, 100.0, 200.0, 200.0), method="qr") -> SkuDetection:
    return SkuDetection(sku_id=1, code=code, name="Hộp", barcode="893", uom="box",
                        method=method, score=1.0, inliers=0, bbox=bbox)


def test_multi_instance_ransac_finds_two_identical_boxes(tmp_path: Path):
    cfg = _make_cfg(tmp_path)
    db = DB(cfg["_paths"]["db"])
    gal = SkuGallery(db, feature_type="orb")
    scanner = SkuScanner(cfg, gal)
    card = _textured_card(7, "MSW TEA")
    scanner.register_template(db, "SKU-TEA-01", "Trà", img=card, save_dir=cfg["_paths"]["data"])

    scene = np.full((480, 800, 3), 200, dtype=np.uint8)
    scene[120:320, 60:320] = card
    scene[140:340, 460:720] = card
    res = scanner.scan_frame(scene)
    hits = [d for d in res["detections"] if d.code == "SKU-TEA-01"]
    assert len(hits) >= 2, f"Chi tim thay {len(hits)} hop"
    assert all(d.experimental for d in hits)
    xs = sorted((d.bbox[0] + d.bbox[2]) / 2 for d in hits[:2])
    assert xs[0] < 400 < xs[1]


def test_line_count_mode_records_in_and_out_direction():
    cnt = SkuTrackCounter(count_mode="line", line_norm=[[0.0, 0.5], [1.0, 0.5]], in_when="down",
                          max_age=1.0, cooldown_sec=4.0)
    shape = (400, 600)
    # Kien 1 di tu tren xuong qua vach -> IN
    _, c0 = cnt.update([_det(bbox=(250, 100, 330, 160))], now=1.0, frame_shape=shape)
    _, c1 = cnt.update([_det(bbox=(250, 150, 330, 210))], now=1.2, frame_shape=shape)
    _, c2 = cnt.update([_det(bbox=(250, 220, 330, 280))], now=1.4, frame_shape=shape)
    assert c0 == [] and c1 == []
    assert len(c2) == 1 and c2[0].direction == "IN"
    # Khong dem lai khi tiep tuc nam duoi vach
    _, c3 = cnt.update([_det(bbox=(250, 240, 330, 300))], now=1.6, frame_shape=shape)
    assert c3 == []

    # Kien 2 di tu duoi len -> OUT (cung ma, khong bi cooldown chan trong mode line)
    cnt.update([], now=3.0, frame_shape=shape)
    _, d0 = cnt.update([_det(bbox=(250, 300, 330, 360))], now=3.1, frame_shape=shape)
    _, d1 = cnt.update([_det(bbox=(250, 220, 330, 280))], now=3.3, frame_shape=shape)
    _, d2 = cnt.update([_det(bbox=(250, 120, 330, 180))], now=3.5, frame_shape=shape)
    assert d0 == [] and d1 == []
    assert len(d2) == 1 and d2[0].direction == "OUT"


def test_hold_mode_counts_two_simultaneous_same_sku_boxes():
    cnt = SkuTrackCounter(min_hold_sec=0.3, cooldown_sec=4.0, max_age=1.0)
    a, b = _det(bbox=(10, 10, 110, 110)), _det(bbox=(300, 10, 400, 110))
    cnt.update([a, b], now=1.0)
    _, c = cnt.update([a, b], now=1.4)
    assert len(c) == 2
    _, c2 = cnt.update([a, b], now=1.8)
    assert c2 == []


def test_summary_splits_in_out_and_erp_batch_excludes_experimental(tmp_path: Path):
    from server.attendance.erp_sku import MockSkuERP, build_stock_batch_from_events

    cfg = _make_cfg(tmp_path)
    db = DB(cfg["_paths"]["db"])
    sid = db.upsert_sku_item("SKU-A", "Sản phẩm A", uom="box", erp_item_code="ITEM-A")
    now = time.time()
    for _ in range(3):
        db.add_sku_event(ts=now, camera="c", sku_id=sid, code="SKU-A", name="A", method="qr", direction="IN", uom="box")
    db.add_sku_event(ts=now, camera="c", sku_id=sid, code="SKU-A", name="A", method="orb_ransac", direction="IN", uom="box")
    db.add_sku_event(ts=now, camera="c", sku_id=sid, code="SKU-A", name="A", method="barcode", direction="OUT", uom="box")

    s = db.summarize_sku_events(t0=now - 10, t1=now + 10)
    assert s["in_qty"] == 4.0 and s["out_qty"] == 1.0

    batch = build_stock_batch_from_events(db, t0=now - 10, t1=now + 10, date_from="d", date_to="d",
                                          direction="IN", warehouse="WH-1")
    assert batch["total_qty"] == 3.0
    assert batch["items"][0]["erp_item_code"] == "ITEM-A"
    assert batch["stock_entry_type"] == "Material Receipt"

    # Goi lai cung du lieu -> khong tao lo trung (cac event da gan stock_batch_id)
    with pytest.raises(ValueError):
        build_stock_batch_from_events(db, t0=now - 10, t1=now + 10, date_from="d", date_to="d",
                                      direction="IN", warehouse="WH-1")

    ok, doc = MockSkuERP(cfg).push_draft_stock_entry(batch)
    assert ok and doc.startswith("MOCK-STE-")
    rec = json.loads((cfg["_paths"]["data"] / "erp_stock_mock.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert rec["docstatus"] == 0 and rec["total_qty"] == 3.0


def test_erp_worker_circuit_breaker_and_item_sync(tmp_path: Path):
    from server.attendance.erp_sku import SkuERPWorker

    cfg = _make_cfg(tmp_path)
    db = DB(cfg["_paths"]["db"])
    gal = SkuGallery(db, feature_type="orb")
    worker = SkuERPWorker(cfg, db, sku_gallery=gal)
    try:
        res = worker.sync_items_from_erp()
        assert res["synced"] >= 2
        assert db.get_sku_by_code("ITEM-TEA-001")["erp_item_code"] == "ITEM-TEA-001"

        class _Failing:
            def push_draft_stock_entry(self, batch):
                return False, "HTTP 503"

        worker.adapter = _Failing()
        for i in range(4):
            db.create_sku_stock_batch(idempotency_key=f"k{i}", actor="t", direction="IN",
                                      stock_entry_type="Material Receipt", warehouse="W",
                                      date_from="d", date_to="d",
                                      items=[{"erp_item_code": "X", "qty": 1, "uom": "pcs"}], event_ids=[])
        worker.sync_once()
        assert worker._circuit_open_until > time.time()
        assert worker.sync_once() == 0  # mach dang mo -> khong goi ERP
    finally:
        worker.stop()


def test_zone_engine_time_based_session_and_empty_alert(tmp_path: Path):
    from server.vision.zone_engine import ZoneEngine

    cfg = _make_cfg(tmp_path)
    db = DB(cfg["_paths"]["db"])
    db.upsert_zone("ZONE-A", "Bàn 1", [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]],
                   enter_confirm_sec=1.0, min_dwell_sec=2.0, exit_grace_sec=1.0, alert_empty_sec=3.0)
    eng = ZoneEngine(cfg, db)
    eng.alert_cooldown_sec = 100.0
    shape = (400, 800)
    person = np.array([100, 100, 200, 300], dtype=np.float32)

    # Vung trong tu dau -> sau 3s phat canh bao
    eng.update([], shape, now=0.0)
    r = eng.update([], shape, now=3.2)
    assert len(r["alerts"]) == 1 and r["zone_states"][0]["alert"]

    # Nguoi buoc vao: chua du 1s -> chua xac nhan
    t = 10.0
    r = eng.update([person], shape, now=t)
    assert r["zone_states"][0]["present_count"] == 0
    for k in range(1, 9):  # dung trong vung 4 giay (fps bat ky)
        r = eng.update([person], shape, now=t + k * 0.5)
    assert r["zone_states"][0]["present_count"] == 1
    assert not r["zone_states"][0]["alert"]

    # Roi vung > exit_grace -> dong phien, dwell ~ 4s
    eng.update([], shape, now=t + 4.5)
    r = eng.update([], shape, now=t + 5.6)
    assert len(r["closed_sessions"]) == 1
    assert 3.5 <= r["closed_sessions"][0]["dwell_sec"] <= 4.5
    sess = db.list_zone_sessions()
    assert len(sess) == 1 and sess[0]["status"] == "closed"


def test_concurrent_batch_build_and_sync_never_duplicates(tmp_path: Path):
    """F1: 2 luong cung tao lo + cung goi sync_once -> chi 1 lo, ERP chi nhan 1 lan POST."""
    import threading

    from server.attendance.erp_sku import SkuERPWorker, build_stock_batch_from_events

    cfg = _make_cfg(tmp_path)
    db = DB(cfg["_paths"]["db"])
    sid = db.upsert_sku_item("SKU-C", "C", erp_item_code="ITEM-C")
    now = time.time()
    for _ in range(20):
        db.add_sku_event(ts=now, camera="c", sku_id=sid, code="SKU-C", name="C", method="qr", direction="IN")

    results, errors = [], []

    def _build():
        try:
            results.append(build_stock_batch_from_events(db, t0=now - 10, t1=now + 10, date_from="d",
                                                         date_to="d", direction="IN", warehouse="W"))
        except ValueError as e:
            errors.append(str(e))

    threads = [threading.Thread(target=_build) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    batches = db.list_sku_stock_batches()
    assert len(batches) == 1 and batches[0]["total_qty"] == 20.0
    assert len(db.q("SELECT id FROM sku_events WHERE stock_batch_id IS NULL")) == 0

    worker = SkuERPWorker(cfg, db)
    worker.stop()
    # Luong nen vua khoi tao co the da day lo qua mock truoc khi stop -> dat lai de kiem tra rieng sync_once dong thoi
    db.x("UPDATE sku_stock_batches SET sync_status='pending', attempts=0, remote_doc_id=NULL")
    calls = []
    gate = threading.Event()

    class _SlowAdapter:
        def push_draft_stock_entry(self, batch):
            calls.append(batch["id"])
            gate.wait(0.3)
            return True, f"STE-{batch['id']}"

    worker.adapter = _SlowAdapter()
    ts = [threading.Thread(target=worker.sync_once) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert calls == [batches[0]["id"]], f"ERP bi goi {len(calls)} lan"


def test_sku_counter_configure_is_thread_safe_during_update():
    """F2: doi count_mode/line lien tuc tu luong HTTP trong khi pipeline update -> khong RuntimeError."""
    import threading

    cnt = SkuTrackCounter(min_hold_sec=0.1, max_age=5.0)
    stop = threading.Event()
    errs = []

    def _pipeline():
        t = 0.0
        while not stop.is_set():
            dets = [_det(bbox=(i * 40.0, 10.0, i * 40.0 + 30.0, 40.0), code=f"S{i}") for i in range(30)]
            try:
                cnt.update(dets, now=t, frame_shape=(400, 1400))
            except Exception as e:  # noqa: BLE001
                errs.append(repr(e))
            t += 0.05

    th = threading.Thread(target=_pipeline)
    th.start()
    for i in range(300):
        cnt.configure(count_mode="line" if i % 2 else "hold", line_norm=[[0, 0.3 + (i % 5) * 0.1], [1, 0.5]])
    stop.set()
    th.join()
    assert errs == []


def test_zone_api_rejects_bad_color_and_non_finite_seconds(tmp_path: Path):
    """F3: color khong phai #RRGGBB hoac giay inf/nan -> HTTP 400; DB khong luu chuoi doc hai."""
    from types import SimpleNamespace

    from fastapi import HTTPException

    from server.api import zones as zones_api
    from server.api.state import ctx
    from server.vision.zone_engine import ZoneEngine

    cfg = _make_cfg(tmp_path)
    db = DB(cfg["_paths"]["db"])
    old = {k: getattr(ctx, k, None) for k in ("cfg", "db", "zone_engine", "pipeline")}
    try:
        ctx.cfg, ctx.db = cfg, db
        ctx.zone_engine = ZoneEngine(cfg, db)
        ctx.pipeline = SimpleNamespace(status=lambda: {})
        req = SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"), cookies={})
        poly = [[0.1, 0.1], [0.6, 0.1], [0.6, 0.9]]
        base = dict(code="Z-1", name="Ban", polygon=poly, enter_confirm_sec=1.0, min_dwell_sec=3.0,
                    exit_grace_sec=2.0, alert_empty_sec=0.0, min_persons=1, color="#3cc85a")
        for bad in ('red"/><script>alert(1)</script>', "#12345", "url(x)", "#gggggg"):
            with pytest.raises(HTTPException) as e:
                zones_api.create_or_update_zone(req, **{**base, "color": bad})
            assert e.value.status_code == 400
        for k in ("enter_confirm_sec", "min_dwell_sec", "exit_grace_sec", "alert_empty_sec"):
            for v in (float("inf"), float("nan")):
                with pytest.raises(HTTPException):
                    zones_api.create_or_update_zone(req, **{**base, k: v})
        assert db.list_zones() == []
        zones_api.create_or_update_zone(req, **{**base, "color": "#3CC85A"})
        assert db.list_zones()[0]["color"] == "#3cc85a"
        with pytest.raises(ValueError):
            db.upsert_zone("Z-2", "x", poly, color="javascript:1")
    finally:
        for k, v in old.items():
            if v is None and hasattr(ctx, k):
                delattr(ctx, k)
            elif v is not None:
                setattr(ctx, k, v)


def test_worker_receives_current_mode_on_reconnect_and_cli(tmp_path: Path, monkeypatch):
    """F5: IPCServer goi on_connect khi worker ket noi lai; CLI --mode ghi de mode cua config."""
    import asyncio
    import sys

    from server.api.ipc import IPCClient, IPCServer
    from server.vision import worker_perception as wp

    async def _scenario():
        pushed = []
        srv = None

        async def _on_connect():
            pushed.append("connected")
            await srv.send_command({"cmd": "set_mode", "mode": "zone"})

        srv = IPCServer(sock_path=None, port=0, on_connect=_on_connect)
        await srv.start()
        cli = IPCClient(sock_path=None, port=srv.bound_port)
        await cli.connect()
        msg = await asyncio.wait_for(cli.recv(), timeout=3.0)
        await cli.close()
        await srv.close()
        return pushed, msg

    pushed, msg = asyncio.run(_scenario())
    assert pushed == ["connected"] and msg == {"cmd": "set_mode", "mode": "zone"}

    captured = {}

    async def _fake_loop(cfg, sock_path, port):
        captured["mode"] = cfg["pipeline"]["mode"]

    monkeypatch.setattr(wp, "run_worker_loop", _fake_loop)
    monkeypatch.setattr(sys, "argv", ["worker", "--port", "1", "--mode", "sku"])
    wp.main()
    assert captured["mode"] == "sku"


def test_zone_engine_ignores_brief_pass_through(tmp_path: Path):
    from server.vision.zone_engine import ZoneEngine

    cfg = _make_cfg(tmp_path)
    db = DB(cfg["_paths"]["db"])
    db.upsert_zone("ZONE-B", "Lối đi", [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
                   enter_confirm_sec=1.0, min_dwell_sec=3.0, exit_grace_sec=1.0)
    eng = ZoneEngine(cfg, db)
    shape = (400, 800)
    person = np.array([100, 100, 200, 300], dtype=np.float32)
    eng.update([person], shape, now=0.0)
    eng.update([person], shape, now=0.5)
    eng.update([], shape, now=1.0)
    eng.update([], shape, now=3.0)
    assert db.list_zone_sessions() == []


def test_erp_sync_preserves_local_barcode_disabled_and_paginates(tmp_path: Path):
    """#1: keo Item tu ERP giu barcode & trang thai disabled o may, dong thoi phan trang & keo Item Barcode."""
    from server.attendance.erp_sku import ERPNextSkuAdapter, SkuERPWorker

    cfg = _make_cfg(tmp_path)
    db = DB(cfg["_paths"]["db"])
    gal = SkuGallery(db, feature_type="orb")
    db.upsert_sku_item("ITEM-A", "Tên đặt ở máy", barcode="8930001", uom="box")
    db.set_sku_status(db.get_sku_by_code("ITEM-A")["id"], "disabled")
    db.upsert_sku_item("ITEM-LINKED", "Cũ", barcode="8930002", uom="pcs", erp_item_code="ITEM-LINKED")

    worker = SkuERPWorker(cfg, db, sku_gallery=gal)
    worker.stop()

    class _ErpNoBarcode:
        def fetch_items(self):
            return [
                {"item_code": "ITEM-A", "item_name": "Tên ERP", "stock_uom": "Nos", "barcode": None, "disabled": 0},
                {"item_code": "ITEM-LINKED", "item_name": "Mới ERP", "stock_uom": "Kg", "barcode": None, "disabled": 0},
                {"item_code": "ITEM-NEW", "item_name": "Mặt hàng mới", "stock_uom": "box", "barcode": "8939999", "disabled": 0},
            ]

    worker.adapter = _ErpNoBarcode()
    res = worker.sync_items_from_erp()
    assert res["synced"] == 3
    row_a = db.get_sku_by_code("ITEM-A")
    assert row_a["barcode"] == "8930001" and row_a["status"] == "disabled" and row_a["name"] == "Tên đặt ở máy"
    row_l = db.get_sku_by_code("ITEM-LINKED")
    assert row_l["barcode"] == "8930002" and row_l["name"] == "Mới ERP" and row_l["uom"] == "Kg"
    assert gal.lookup_code_string("8939999")["code"] == "ITEM-NEW"

    # Kiem tra phan trang > 200 Item va gop bang Item Barcode
    adapter = ERPNextSkuAdapter(cfg)
    calls = []

    def _fake_list(doctype, fields):
        calls.append(doctype)
        if doctype == "Item":
            return [{"name": f"I-{i}", "item_code": f"I-{i}", "item_name": f"N-{i}", "stock_uom": "pcs", "disabled": 0}
                    for i in range(250)]
        return [{"parent": "I-249", "barcode": "BC-249"}]

    adapter._list_resource = _fake_list
    fetched = adapter.fetch_items()
    assert len(fetched) == 250 and fetched[-1]["barcode"] == "BC-249" and "Item Barcode" in calls


def test_erp_failed_batch_uses_backoff_and_manual_retry(tmp_path: Path):
    """#2: lo failed khong bi ket vinh vien sau 5 lan; den han backoff hoac bam Thu lai la day tiep duoc."""
    import asyncio
    from types import SimpleNamespace

    from server.api import sku as sku_api
    from server.api.state import ctx
    from server.attendance.erp_sku import SkuERPWorker, build_stock_batch_from_events

    cfg = _make_cfg(tmp_path)
    db = DB(cfg["_paths"]["db"])
    sid = db.upsert_sku_item("SKU-R", "Hàng R", erp_item_code="ITEM-R")
    now = time.time()
    db.add_sku_event(ts=now, camera="c", sku_id=sid, code="SKU-R", name="R", method="qr", direction="IN")
    batch = build_stock_batch_from_events(db, t0=now - 10, t1=now + 10, date_from="d", date_to="d",
                                          direction="IN", warehouse="W")

    worker = SkuERPWorker(cfg, db)
    worker.stop()
    db.x("UPDATE sku_stock_batches SET sync_status='pending', attempts=0, remote_doc_id=NULL")

    class _Down:
        def push_draft_stock_entry(self, b):
            return False, "HTTP 503"

    worker.adapter = _Down()
    for _ in range(7):
        worker._circuit_open_until = 0.0
        db.x("UPDATE sku_stock_batches SET next_retry_at=NULL WHERE id=?", (batch["id"],))
        worker.sync_once()
    row = db.q("SELECT sync_status, attempts, next_retry_at FROM sku_stock_batches WHERE id=?", (batch["id"],))[0]
    assert row["sync_status"] == "failed" and row["attempts"] == 7
    assert row["next_retry_at"] is not None and row["next_retry_at"] > time.time()
    # Chua den han -> chua day; het han -> tu day lai duoc
    assert db.pending_sku_stock_batches(now=time.time()) == []
    assert len(db.pending_sku_stock_batches(now=float(row["next_retry_at"]) + 1.0)) == 1

    class _Up:
        def push_draft_stock_entry(self, b):
            return True, "STE-RECOVERED"

    worker.adapter = _Up()
    old = {k: getattr(ctx, k, None) for k in ("cfg", "db", "sku_erp")}
    try:
        ctx.cfg, ctx.db, ctx.sku_erp = cfg, db, worker
        req = SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"), cookies={})
        resp = asyncio.run(sku_api.retry_sku_erp_batch(batch["id"], req))
        assert resp["sync_status"] == "sent" and resp["remote_doc_id"] == "STE-RECOVERED"
    finally:
        for k, v in old.items():
            if v is None and hasattr(ctx, k):
                delattr(ctx, k)
            elif v is not None:
                setattr(ctx, k, v)


def test_zone_mode_requires_person_detector_and_blocks_false_empty_alerts(tmp_path: Path):
    """#3: thieu bo phat hien nguoi (zone_det=None) -> tu choi chuyen sang mode=zone (409) & khong canh bao gia."""
    import asyncio
    from types import SimpleNamespace

    from fastapi import HTTPException

    from server.api import sku as sku_api
    from server.api.state import ctx
    from server.vision.pipeline import Pipeline
    from server.vision.zone_engine import ZoneEngine

    cfg = _make_cfg(tmp_path)
    cfg["pipeline"]["mode"] = "zone"
    db = DB(cfg["_paths"]["db"])
    db.upsert_zone("Z-G", "Bàn", [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]], alert_empty_sec=1.0)
    ze = ZoneEngine(cfg, db)
    emitted = []
    frame = np.full((240, 320, 3), 128, dtype=np.uint8)
    cam = SimpleNamespace(connected=True, fps_in=15.0, idle=False,
                          describe=lambda: {"kind": "webcam", "label": "Mock", "connected": True, "black": False},
                          read=lambda: (1, frame))
    pl = Pipeline(cfg, db, SimpleNamespace(name="opencv", model_version="t"),
                  SimpleNamespace(version=1, size=0, has=lambda _: False), cam,
                  SimpleNamespace(kind="mock", notify=lambda: None), emitted.append,
                  zone_engine=ze, zone_detector=None, context_detector=None)
    assert pl.mode == "attendance" and pl.status()["zone_ready"] is False
    pl._process_zone(frame, now=10.0)
    pl._process_zone(frame, now=12.0)
    assert [m for m in emitted if m.get("type") == "zone_alert"] == []

    old = {k: getattr(ctx, k, None) for k in ("cfg", "db", "pipeline")}
    try:
        ctx.cfg, ctx.db, ctx.pipeline = cfg, db, pl
        req = SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"), cookies={})
        with pytest.raises(HTTPException) as exc:
            asyncio.run(sku_api.set_mode(req, mode="zone"))
        assert exc.value.status_code == 409
    finally:
        for k, v in old.items():
            if v is None and hasattr(ctx, k):
                delattr(ctx, k)
            elif v is not None:
                setattr(ctx, k, v)


def test_ransac_keeps_strict_ratio_on_first_hit_against_noise(tmp_path: Path):
    """#4: anh nen nhieu khong dat min_inlier_ratio khong duoc nhan nham lam hop SKU dau tien."""
    cfg = _make_cfg(tmp_path)
    db = DB(cfg["_paths"]["db"])
    gal = SkuGallery(db, feature_type="orb")
    scanner = SkuScanner(cfg, gal)
    card = _textured_card(7, "MSW TEA")
    scanner.register_template(db, "SKU-TEA-01", "Trà", img=card, save_dir=cfg["_paths"]["data"])

    noise_scene = _textured_card(99, "KHAC HAN", size=(800, 480))
    res = scanner.scan_frame(noise_scene)
    assert [d for d in res["detections"] if d.code == "SKU-TEA-01"] == []
