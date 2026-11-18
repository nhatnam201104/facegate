"""Kiem thu loi Nhan dien Hang hoa / SKU (Phase 2): DB, QR/Barcode, ORB/SIFT + RANSAC, chong bao nham & toc do CPU."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from server.config import load_config
from server.store.db import DB
from server.vision.sku_scanner import (
    SkuGallery,
    SkuScanner,
    extract_template_features,
    is_valid_projected_quad,
)


def _make_textured_package(seed: int, title: str, size: tuple[int, int] = (280, 220)) -> np.ndarray:
    """Tao anh bao bi co logo, khung chu va hoa van da dang goc canh de test ORB/SIFT."""
    w, h = size
    rng = np.random.default_rng(seed)
    card = np.full((h, w, 3), 235, dtype=np.uint8)
    cv2.rectangle(card, (8, 8), (w - 9, h - 9), (30, 50, 120), 3)
    cv2.rectangle(card, (18, 18), (w - 19, 68), (40, 90, 180), -1)
    cv2.putText(card, title, (26, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (255, 255, 255), 2, cv2.LINE_AA)

    for _ in range(32):
        pt1 = (int(rng.integers(22, w - 22)), int(rng.integers(78, h - 22)))
        pt2 = (int(rng.integers(22, w - 22)), int(rng.integers(78, h - 22)))
        color = tuple(int(c) for c in rng.integers(20, 190, size=3))
        if rng.random() < 0.5:
            cv2.line(card, pt1, pt2, color, int(rng.integers(1, 3)), cv2.LINE_AA)
        else:
            r = int(rng.integers(6, 20))
            cv2.circle(card, pt1, r, color, int(rng.integers(1, 3)), cv2.LINE_AA)
    for idx in range(6):
        x0 = 24 + idx * 38
        cv2.drawMarker(card, (x0, h - 34), (20, 20, 20), cv2.MARKER_STAR, 18, 2)
    return card


def _make_ean13_image(digits12: str) -> tuple[str, np.ndarray]:
    """Sinh ma vach 1D EAN-13 chuan (13 chu so) de test BarcodeDetector."""
    L = {
        "0": "0001101", "1": "0011001", "2": "0010011", "3": "0111101", "4": "0100011",
        "5": "0110001", "6": "0101111", "7": "0111011", "8": "0110111", "9": "0001011",
    }
    R = {d: "".join("1" if c == "0" else "0" for c in L[d]) for d in L}
    G = {d: R[d][::-1] for d in L}
    PARITY = {
        "0": "LLLLLL", "1": "LLGLGG", "2": "LLGGLG", "3": "LLGGGL", "4": "LGLLGG",
        "5": "LGGLLG", "6": "LGGGLL", "7": "LGLGLG", "8": "LGLGGL", "9": "LGGLGL",
    }
    s = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits12))
    ean13 = digits12 + str((10 - s % 10) % 10)
    par = PARITY[ean13[0]]
    left = "".join((L[d] if p == "L" else G[d]) for d, p in zip(ean13[1:7], par))
    right = "".join(R[d] for d in ean13[7:13])
    bits = "101" + left + "01010" + right + "101"
    img = np.full((260, 600, 3), 245, dtype=np.uint8)
    cv2.rectangle(img, (70, 35), (530, 225), (255, 255, 255), -1)
    for i, b in enumerate(bits):
        if b == "1":
            img[55:205, 110 + i * 4:110 + (i + 1) * 4] = 0
    return ean13, img


def _make_cfg(tmp_path: Path, feature_type: str = "orb") -> dict:
    data_dir = (tmp_path / f"data_{feature_type}").as_posix()
    cfg_file = tmp_path / f"cfg_{feature_type}.yaml"
    cfg_file.write_text(
        f"storage:\n  data_dir: '{data_dir}'\nsku:\n  feature_type: {feature_type}\n  min_blur: 10.0\n",
        encoding="utf-8",
    )
    return load_config(cfg_file)


def test_sku_db_crud_and_event_persistence(tmp_path: Path):
    db = DB(tmp_path / "sku_test.db")
    sku_id = db.upsert_sku_item("SKU-CF-01", "Cà phê rang xay 500g", barcode="8930001112225", uom="bag")
    assert db.get_sku_by_code("SKU-CF-01")["id"] == sku_id
    assert db.get_sku_by_barcode("8930001112225")["name"] == "Cà phê rang xay 500g"

    kps = np.array([[10.0, 20.0], [30.0, 40.0], [50.0, 60.0], [70.0, 80.0]], dtype=np.float32)
    desc = np.arange(4 * 32, dtype=np.uint8).reshape(4, 32)
    db.add_sku_template(sku_id, "orb", 200, 150, kps, desc, image_path="sku/SKU-CF-01/tpl.jpg", label="front")

    items = db.list_sku_items()
    assert len(items) == 1
    assert items[0]["n_templates"] == 1 and items[0]["total_kps"] == 4

    loaded_items, loaded_tpls = db.load_sku_gallery(feature_type="orb")
    assert len(loaded_items) == 1 and len(loaded_tpls) == 1
    assert np.allclose(loaded_tpls[0]["kps_xy"], kps)
    assert np.array_equal(loaded_tpls[0]["descriptors"], desc)

    ev_id = db.add_sku_event(
        camera="dock-1", sku_id=sku_id, code="SKU-CF-01", name="Cà phê rang xay 500g",
        barcode="8930001112225", method="barcode", score=1.0, inliers=0, direction="IN",
        uom="bag", meta={"lane": 1},
    )
    evs = db.list_sku_events(code="SKU-CF-01")
    assert len(evs) == 1 and evs[0]["id"] == ev_id

    removed_imgs = db.delete_sku_item(sku_id)
    assert removed_imgs == ["sku/SKU-CF-01/tpl.jpg"]
    assert db.list_sku_items() == []


def test_low_texture_template_warns_user():
    blank_box = np.full((220, 280, 3), 210, dtype=np.uint8)
    feat = extract_template_features(blank_box, feature_type="orb", min_kps_warn=30)
    assert feat["texture_ok"] is False
    assert feat["num_kps"] < 30
    assert any("ít hoa văn" in w for w in feat["warnings"])


def test_tier1_qr_and_ean13_barcode_and_tracked_object(tmp_path: Path):
    cfg = _make_cfg(tmp_path, "orb")
    db = DB(cfg["_paths"]["db"])
    gal = SkuGallery(db, feature_type="orb")
    scanner = SkuScanner(cfg, gal)

    ean13, bar_scene = _make_ean13_image("893456313816")
    scanner.register_template(db, code="SKU-QR-100", name="Thùng linh kiện A", barcode="QR-PAYLOAD-100", uom="box")
    scanner.register_template(db, code="SKU-BAR-200", name="Hộp sữa hạt", barcode=ean13, uom="box")

    # 1. Quet QR Code
    qr_raw = cv2.QRCodeEncoder_create().encode("QR-PAYLOAD-100")
    qr_bgr = cv2.cvtColor(cv2.resize(qr_raw, (200, 200), interpolation=cv2.INTER_NEAREST), cv2.COLOR_GRAY2BGR)
    scene_qr = np.full((420, 640, 3), 220, dtype=np.uint8)
    scene_qr[110:310, 220:420] = qr_bgr

    res_qr = scanner.scan_frame(scene_qr)
    assert not res_qr["skipped_blur"]
    assert len(res_qr["detections"]) == 1
    det_qr = res_qr["detections"][0]
    assert det_qr.accepted and det_qr.code == "SKU-QR-100" and det_qr.method == "qr"

    tobj = det_qr.to_tracked_object("dock-1", scene_qr.shape[:2], track_id=5)
    assert tobj.kind == "sku" and tobj.state == "IDENTIFIED" and tobj.meta["method"] == "qr"

    # 2. Quet EAN-13 1D Barcode
    res_bar = scanner.scan_frame(bar_scene)
    assert any(d.accepted and d.code == "SKU-BAR-200" and d.method == "barcode" for d in res_bar["detections"])

    # 3. Ma QR la chua dang ky -> bao unknown_code ro rang, khong doan bua
    qr_unk = cv2.QRCodeEncoder_create().encode("UNREGISTERED-999")
    scene_unk = np.full((420, 640, 3), 220, dtype=np.uint8)
    scene_unk[100:300, 200:400] = cv2.cvtColor(
        cv2.resize(qr_unk, (200, 200), interpolation=cv2.INTER_NEAREST), cv2.COLOR_GRAY2BGR,
    )
    res_unk = scanner.scan_frame(scene_unk)
    assert len(res_unk["detections"]) == 1
    assert not res_unk["detections"][0].accepted and res_unk["detections"][0].method == "unknown_code"


@pytest.mark.parametrize("feature_type", ["orb", "sift"])
def test_tier2_orb_and_sift_ransac_under_perspective_and_noise_rejection(tmp_path: Path, feature_type: str):
    cfg = _make_cfg(tmp_path, feature_type)
    db = DB(cfg["_paths"]["db"])
    gal = SkuGallery(db, feature_type=feature_type)
    scanner = SkuScanner(cfg, gal)

    pkg_a = _make_textured_package(seed=101, title="MSW TEA GOLD")
    pkg_b = _make_textured_package(seed=202, title="MSW COFFEE ROAST")

    rep_a = scanner.register_template(db, "SKU-TEA-01", "Trà Oolong MSW", img=pkg_a, save_dir=cfg["_paths"]["data"])
    rep_b = scanner.register_template(db, "SKU-COF-02", "Cà phê MSW", img=pkg_b, save_dir=cfg["_paths"]["data"])
    assert rep_a["template"]["texture_ok"] and rep_b["template"]["texture_ok"]

    # Tao khung canh ban dong goi co nhieu nen va dat goi hang A bi xoay + nghieng phoi canh
    rng = np.random.default_rng(999)
    bg = rng.integers(90, 160, size=(480, 640, 3), dtype=np.uint8)
    bg = cv2.GaussianBlur(bg, (3, 3), 0.8)

    th, tw = pkg_a.shape[:2]
    src_corners = np.array([[0, 0], [tw, 0], [tw, th], [0, th]], dtype=np.float32)
    dst_corners = np.array([[185, 95], [445, 125], [420, 335], [160, 305]], dtype=np.float32)
    M = cv2.getPerspectiveTransform(src_corners, dst_corners)
    warped = cv2.warpPerspective(pkg_a, M, (640, 480))
    mask = cv2.warpPerspective(np.full((th, tw), 255, dtype=np.uint8), M, (640, 480)) > 0
    scene = bg.copy()
    scene[mask] = warped[mask]

    res = scanner.scan_frame(scene)
    assert res["elapsed_ms"] < 500.0
    accepted = [d for d in res["detections"] if d.accepted]
    assert len(accepted) == 1
    hit = accepted[0]
    assert hit.code == "SKU-TEA-01"
    assert hit.method == f"{feature_type}_ransac"
    assert hit.inliers >= scanner.min_inliers
    # Kiem tra khung bbox khop sat vung dat san pham
    x1, y1, x2, y2 = hit.bbox
    assert 130 <= x1 <= 200 and 70 <= y1 <= 130 and 410 <= x2 <= 475 and 300 <= y2 <= 365

    # Chong bao nham tren khung nen nhieu khong chua san pham
    res_noise = scanner.scan_frame(bg)
    assert [d for d in res_noise["detections"] if d.accepted] == []


def test_projected_quad_geometric_gate_rejects_self_intersecting_and_tiny():
    # Tu giac tu cat (bowtie / non-convex) phai bi loai
    bowtie = np.array([[100, 100], [300, 300], [300, 100], [100, 300]], dtype=np.float32)
    assert not is_valid_projected_quad(bowtie, (480, 640))

    # Tu giac qua nho (< 0.4% khung hinh) phai bi loai
    tiny = np.array([[50, 50], [60, 50], [60, 60], [50, 60]], dtype=np.float32)
    assert not is_valid_projected_quad(tiny, (480, 640))

    # Tu giac loi binh thuong -> hop le
    good = np.array([[120, 90], [360, 100], [350, 290], [110, 280]], dtype=np.float32)
    assert is_valid_projected_quad(good, (480, 640))


def test_security_path_traversal_duplicate_barcode_and_sql_whitelist(tmp_path: Path):
    cfg = _make_cfg(tmp_path, "orb")
    db = DB(cfg["_paths"]["db"])
    gal = SkuGallery(db, feature_type="orb")
    scanner = SkuScanner(cfg, gal)
    pkg = _make_textured_package(seed=7, title="SAFE PKG")

    # 1. Chan ma SKU chua ky tu thoat thu muc hoac rong
    for bad_code in ("../escape", "..\\escape", "SKU/01", "", ".."):
        with pytest.raises(ValueError, match="Mã SKU"):
            scanner.register_template(db, bad_code, "Bad", img=pkg, save_dir=cfg["_paths"]["data"])
    assert not (cfg["_paths"]["data"].parent / "escape").exists()

    with pytest.raises(ValueError, match="Tên SKU"):
        db.upsert_sku_item("SKU-VALID-1", "   ")

    # 2. Chan trung barcode giua 2 SKU khac nhau nhung cho phep cap nhat chinh SKU do
    scanner.register_template(db, "SKU-A", "Sản phẩm A", barcode="8930009998887")
    scanner.register_template(db, "SKU-A", "Sản phẩm A (đổi tên)", barcode="8930009998887")
    with pytest.raises(ValueError, match="đã thuộc về SKU 'SKU-A'"):
        scanner.register_template(db, "SKU-B", "Sản phẩm B", barcode="8930009998887")

    # Uu tien explicit barcode khong phu thuoc thu tu dang ky trong SkuGallery
    scanner.register_template(db, "BC-SHARED-CODE", "Sản phẩm trùng mã", barcode=None)
    scanner.register_template(db, "SKU-OWNER", "Sản phẩm gắn mã vạch tường minh", barcode="BC-SHARED-CODE")
    hit = gal.lookup_code_string("BC-SHARED-CODE")
    assert hit is not None and hit["code"] == "SKU-OWNER"

    # 3. Whitelist cot SQL trong add_sku_event
    with pytest.raises(ValueError, match="Cột sku_events không hợp lệ"):
        db.add_sku_event(code="SKU-A", method="qr", **{"qty); DROP TABLE sku_items;--": 1})


def test_download_models_enforces_sha256_integrity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import hashlib
    import io
    import urllib.request
    from server.scripts import download_models as dm

    payload = b"x" * 120_000
    good_sha = hashlib.sha256(payload).hexdigest()

    def _fake_urlopen(req, timeout=120):
        return io.BytesIO(payload)

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    # Checksum sai -> tu choi va KHONG ghi file xuong dia
    ok_bad = dm.download(
        "dummy.onnx",
        ("https://example.com/dummy.onnx", 100_000),
        expected_sha_prefix="0000000000000000",
        models_dir=tmp_path,
    )
    assert not ok_bad
    assert not (tmp_path / "dummy.onnx").exists()

    # Checksum dung -> ghi file va verify lai thanh cong
    ok_good = dm.download(
        "dummy.onnx",
        ("https://example.com/dummy.onnx", 100_000),
        expected_sha_prefix=good_sha,
        models_dir=tmp_path,
    )
    assert ok_good and (tmp_path / "dummy.onnx").is_file()
    assert not dm.download(
        "dummy.onnx",
        ("https://example.com/dummy.onnx", 100_000),
        expected_sha_prefix="ffffffffffffffff",
        models_dir=tmp_path,
    )

