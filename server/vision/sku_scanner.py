"""Nhan dien Hang hoa / SKU tren CPU: Uu tien QR/Barcode -> Fallback ORB/SIFT + RANSAC (0 can train)."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from ..api.ipc import TrackedObject
from ..store.db import validate_sku_code

log = logging.getLogger("sku_scanner")

VALID_FEATURES = ("orb", "sift")


@dataclass
class SkuDetection:
    sku_id: int | None
    code: str | None
    name: str | None
    barcode: str | None
    uom: str
    method: str                          # qr | barcode | orb_ransac | sift_ransac | unknown_code
    score: float                         # 1.0 cho QR/Barcode, inlier_ratio co trong so cho RANSAC
    inliers: int
    bbox: tuple[float, float, float, float]
    polygon: list[tuple[float, float]] = field(default_factory=list)
    code_type: str | None = None

    @property
    def accepted(self) -> bool:
        return self.sku_id is not None and bool(self.code)

    def to_tracked_object(
        self,
        camera_id: str,
        frame_shape: tuple[int, int],
        track_id: int = 1,
        ts_server_ns: int | None = None,
        state: str | None = None,
    ) -> TrackedObject:
        h, w = max(1, int(frame_shape[0])), max(1, int(frame_shape[1]))
        x1, y1, x2, y2 = self.bbox
        st = state or ("IDENTIFIED" if self.accepted else "UNKNOWN")
        role = "ok" if self.accepted else "alert"
        lbl = f"{self.code} · {self.name}" if self.accepted else (
            f"Mã lạ ({self.barcode})" if self.barcode else "Unknown SKU"
        )
        return TrackedObject(
            camera_id=camera_id,
            ts_server_ns=ts_server_ns if ts_server_ns is not None else time.time_ns(),
            track_id=int(track_id),
            kind="sku",
            bbox_norm=(x1 / w, y1 / h, x2 / w, y2 / h),
            state=st,
            color_role=role,
            label=lbl,
            verification_tier="verified" if self.accepted else "unverified",
            meta={
                "sku_id": self.sku_id,
                "code": self.code,
                "barcode": self.barcode,
                "uom": self.uom,
                "method": self.method,
                "score": round(float(self.score), 4),
                "inliers": int(self.inliers),
                "code_type": self.code_type,
            },
        )


def _create_feature_extractor(feature_type: str, orb_nfeatures: int = 800, sift_nfeatures: int = 600):
    ft = feature_type.lower().strip()
    if ft == "orb":
        return cv2.ORB_create(nfeatures=int(orb_nfeatures), scaleFactor=1.2, nlevels=8)
    if ft == "sift":
        return cv2.SIFT_create(nfeatures=int(sift_nfeatures))
    raise ValueError(f"feature_type khong hop le: {feature_type} (chon 'orb' hoac 'sift')")


def _to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def compute_sharpness(img: np.ndarray) -> float:
    gray = _to_gray(img)
    h, w = gray.shape[:2]
    if max(h, w) > 320:
        s = 320.0 / max(h, w)
        gray = cv2.resize(gray, (max(1, int(w * s)), max(1, int(h * s))))
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def extract_template_features(
    img: np.ndarray,
    feature_type: str = "orb",
    orb_nfeatures: int = 800,
    sift_nfeatures: int = 600,
    max_side: int = 640,
    min_kps_warn: int = 30,
) -> dict:
    """Trich keypoints + descriptors cho anh mau phang va danh gia do phong phu hoa van."""
    if img is None or img.size == 0:
        raise ValueError("Anh mau rong")
    h0, w0 = img.shape[:2]
    scale = min(1.0, float(max_side) / max(h0, w0))
    work = cv2.resize(img, (max(1, int(round(w0 * scale))), max(1, int(round(h0 * scale))))) if scale < 1.0 else img
    gray = _to_gray(work)
    h, w = gray.shape[:2]
    det = _create_feature_extractor(feature_type, orb_nfeatures=orb_nfeatures, sift_nfeatures=sift_nfeatures)
    kps, desc = det.detectAndCompute(gray, None)
    if not kps or desc is None or len(kps) == 0:
        pts = np.zeros((0, 2), dtype=np.float32)
        cols = 32 if feature_type.lower() == "orb" else 128
        dt = np.uint8 if feature_type.lower() == "orb" else np.float32
        desc_arr = np.zeros((0, cols), dtype=dt)
    else:
        pts = np.array([kp.pt for kp in kps], dtype=np.float32)
        desc_arr = desc

    n_kps = int(pts.shape[0])
    warnings: list[str] = []
    if n_kps < min_kps_warn:
        warnings.append(
            f"Mẫu bao bì ít hoa văn/chi tiết ({n_kps} < {min_kps_warn} keypoints). "
            "Nên dán QR/Barcode hoặc cân nhắc train detector nếu khớp thực tế không đạt."
        )
    return {
        "feature_type": feature_type.lower(),
        "width": w,
        "height": h,
        "num_kps": n_kps,
        "kps_xy": pts,
        "descriptors": desc_arr,
        "resized_img": work,
        "sharpness": round(compute_sharpness(work), 2),
        "texture_ok": n_kps >= min_kps_warn,
        "warnings": warnings,
    }


def _polygon_area(quad: np.ndarray) -> float:
    return float(abs(cv2.contourArea(quad.reshape(-1, 1, 2).astype(np.float32))))


def is_valid_projected_quad(
    quad: np.ndarray,
    frame_shape: tuple[int, int],
    min_area_ratio: float = 0.004,
    max_area_ratio: float = 0.95,
    max_edge_ratio: float = 6.0,
) -> bool:
    """Rao cung hinh hoc cho tu giac chieu tu Homography: phai loi, khong suy bien, dien tich hop ly."""
    if quad is None or quad.shape != (4, 2) or not np.all(np.isfinite(quad)):
        return False
    cnt = quad.reshape(4, 1, 2).astype(np.float32)
    if not cv2.isContourConvex(cnt):
        return False
    h, w = float(frame_shape[0]), float(frame_shape[1])
    frame_area = max(1.0, h * w)
    area = _polygon_area(quad)
    if not (min_area_ratio * frame_area <= area <= max_area_ratio * frame_area):
        return False
    edges = [float(np.linalg.norm(quad[(i + 1) % 4] - quad[i])) for i in range(4)]
    if min(edges) < 12.0:
        return False
    if max(edges) / max(1e-6, min(edges)) > max_edge_ratio:
        return False
    # Tam tu giac phai nam trong khung hinh (cho phep mot phan goc hoi tran vien)
    cx, cy = float(quad[:, 0].mean()), float(quad[:, 1].mean())
    return -0.05 * w <= cx <= 1.05 * w and -0.05 * h <= cy <= 1.05 * h


def _quad_to_bbox(quad: np.ndarray, w: int, h: int) -> tuple[float, float, float, float]:
    x1 = float(np.clip(np.min(quad[:, 0]), 0.0, w))
    y1 = float(np.clip(np.min(quad[:, 1]), 0.0, h))
    x2 = float(np.clip(np.max(quad[:, 0]), x1, w))
    y2 = float(np.clip(np.max(quad[:, 1]), y1, h))
    return (round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2))


def _box_iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    ua = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1]) + max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1]) - inter
    return float(inter / ua) if ua > 0 else 0.0


class SkuGallery:
    """Bo nho RAM luu danh muc ma vach va tap mau ORB/SIFT da nap tu SQLite/PostgreSQL."""

    def __init__(self, db, feature_type: str = "orb"):
        self.db = db
        self.feature_type = feature_type.lower()
        self.lock = threading.Lock()
        self.items_by_id: dict[int, dict] = {}
        self.by_code: dict[str, dict] = {}
        self.by_barcode: dict[str, dict] = {}
        self.templates: list[dict] = []
        self.version = 0
        self.reload()

    def reload(self) -> None:
        items, templates = self.db.load_sku_gallery(feature_type=self.feature_type)
        with self.lock:
            self.items_by_id = {int(it["id"]): it for it in items}
            self.by_code = {str(it["code"]): it for it in items}
            self.by_barcode = {}
            for it in items:
                bc_val = (it.get("barcode") or "").strip()
                if bc_val:
                    self.by_barcode[bc_val] = it
            for it in items:
                # Cho phep ma QR chua chinh sku_code nhung khong bao gio ghi de barcode khai bao tuong minh
                self.by_barcode.setdefault(str(it["code"]), it)
            self.templates = [t for t in templates if int(t["num_kps"]) >= 4]
            self.version += 1

    @property
    def size(self) -> int:
        with self.lock:
            return len(self.items_by_id)

    @property
    def template_count(self) -> int:
        with self.lock:
            return len(self.templates)

    def lookup_code_string(self, raw_text: str) -> dict | None:
        txt = (raw_text or "").strip()
        if not txt:
            return None
        with self.lock:
            return dict(self.by_barcode[txt]) if txt in self.by_barcode else None

    def snapshot_templates(self) -> list[dict]:
        with self.lock:
            return list(self.templates)


class SkuScanner:
    """Pipeline quet Hang hoa 2 tang tren CPU:
    1. Tang 1: QR Code + 1D Barcode (nhanh, chinh xac tuyet doi).
    2. Tang 2: ORB/SIFT KNN Lowe ratio + Homography RANSAC voi rao cung tu giac loi.
    """

    def __init__(self, cfg: dict, gallery: SkuGallery):
        self.cfg = cfg
        self.gallery = gallery
        scfg = cfg.get("sku") or {}
        self.feature_type = str(scfg.get("feature_type", gallery.feature_type)).lower()
        self.orb_nfeatures = int(scfg.get("orb_nfeatures", 800))
        self.sift_nfeatures = int(scfg.get("sift_nfeatures", 600))
        self.min_template_kps = int(scfg.get("min_template_kps", 30))
        self.min_inliers = int(scfg.get("min_inliers", 12))
        self.min_inlier_ratio = float(scfg.get("min_inlier_ratio", 0.25))
        self.ratio_thresh = float(scfg.get("ratio_thresh", 0.75))
        self.reproj_thresh = float(scfg.get("ransac_reproj_thresh", 4.5))
        self.min_blur = float(scfg.get("min_blur", 20.0))
        self.scan_max_width = int(scfg.get("scan_max_width", 960))

        self._detector = _create_feature_extractor(
            self.feature_type,
            orb_nfeatures=self.orb_nfeatures,
            sift_nfeatures=self.sift_nfeatures,
        )
        norm = cv2.NORM_HAMMING if self.feature_type == "orb" else cv2.NORM_L2
        self._matcher = cv2.BFMatcher(norm, crossCheck=False)
        self._qr = cv2.QRCodeDetectorAruco() if hasattr(cv2, "QRCodeDetectorAruco") else cv2.QRCodeDetector()
        self._qr_fallback = cv2.QRCodeDetector()
        self._barcode = (
            cv2.barcode.BarcodeDetector()
            if hasattr(cv2, "barcode") and hasattr(cv2.barcode, "BarcodeDetector")
            else None
        )
        self._ransac_flag = getattr(cv2, "USAC_MAGSAC", cv2.RANSAC)
        self._lock = threading.Lock()

    def register_template(
        self,
        db,
        code: str,
        name: str,
        img: np.ndarray | None = None,
        barcode: str | None = None,
        uom: str = "pcs",
        label: str | None = None,
        save_dir: Path | None = None,
        replace_templates: bool = False,
    ) -> dict:
        """Dang ky hoac cap nhat 1 SKU kem anh mau phang (khong bat buoc anh neu chi dung QR/Barcode)."""
        code_c = validate_sku_code(code)
        sku_id = db.upsert_sku_item(code=code_c, name=name, barcode=barcode, uom=uom, status="active")
        if replace_templates:
            db.clear_sku_templates(sku_id, feature_type=self.feature_type)

        tpl_report = None
        if img is not None:
            feat = extract_template_features(
                img,
                feature_type=self.feature_type,
                orb_nfeatures=self.orb_nfeatures,
                sift_nfeatures=self.sift_nfeatures,
                min_kps_warn=self.min_template_kps,
            )
            rel_path = None
            if save_dir is not None:
                sku_root = (Path(save_dir) / "sku").resolve()
                sku_folder = (sku_root / code_c).resolve()
                if sku_root not in sku_folder.parents:
                    raise ValueError("Đường dẫn lưu mẫu SKU không hợp lệ")
                sku_folder.mkdir(parents=True, exist_ok=True)
                fname = f"{int(time.time() * 1000)}_{self.feature_type}.jpg"
                out_file = sku_folder / fname
                cv2.imwrite(str(out_file), feat["resized_img"])
                rel_path = f"sku/{code_c}/{fname}"

            tpl_id = None
            if feat["num_kps"] >= 4:
                tpl_id = db.add_sku_template(
                    sku_id=sku_id,
                    feature_type=self.feature_type,
                    width=feat["width"],
                    height=feat["height"],
                    kps_xy=feat["kps_xy"],
                    descriptors=feat["descriptors"],
                    image_path=rel_path,
                    label=label,
                )
            tpl_report = {
                "template_id": tpl_id,
                "feature_type": self.feature_type,
                "num_kps": feat["num_kps"],
                "sharpness": feat["sharpness"],
                "texture_ok": feat["texture_ok"],
                "warnings": feat["warnings"],
                "image_path": rel_path,
            }

        self.gallery.reload()
        return {"sku_id": sku_id, "code": code_c, "barcode": barcode, "template": tpl_report}

    def _scan_codes(self, img: np.ndarray, offset_xy: tuple[int, int], orig_scale: float) -> list[SkuDetection]:
        ox, oy = offset_xy
        h_full, w_full = int(round(img.shape[0] / orig_scale)) + oy, int(round(img.shape[1] / orig_scale)) + ox
        found: list[SkuDetection] = []
        seen_keys: set[tuple[str, int, int]] = set()

        def _add_decoded(text: str, pts: np.ndarray, method: str, code_type: str):
            txt = (text or "").strip()
            if not txt or pts is None:
                return
            quad = pts.reshape(-1, 2).astype(np.float32) / orig_scale + np.array([ox, oy], dtype=np.float32)
            bbox = _quad_to_bbox(quad, w_full, h_full)
            key = (txt, int(bbox[0] // 24), int(bbox[1] // 24))
            if key in seen_keys:
                return
            seen_keys.add(key)
            item = self.gallery.lookup_code_string(txt)
            poly = [(round(float(p[0]), 2), round(float(p[1]), 2)) for p in quad]
            if item is not None:
                found.append(SkuDetection(
                    sku_id=int(item["id"]),
                    code=str(item["code"]),
                    name=str(item["name"]),
                    barcode=txt,
                    uom=str(item.get("uom") or "pcs"),
                    method=method,
                    score=1.0,
                    inliers=0,
                    bbox=bbox,
                    polygon=poly,
                    code_type=code_type,
                ))
            else:
                found.append(SkuDetection(
                    sku_id=None,
                    code=None,
                    name=None,
                    barcode=txt,
                    uom="pcs",
                    method="unknown_code",
                    score=1.0,
                    inliers=0,
                    bbox=bbox,
                    polygon=poly,
                    code_type=code_type,
                ))

        # 1. QR Code (thu Aruco detector truoc, fallback sang QRCodeDetector chuan)
        for det in (self._qr, self._qr_fallback):
            try:
                ok, texts, points, _ = det.detectAndDecodeMulti(img)
                if ok and texts is not None and points is not None:
                    for txt, pts in zip(texts, points):
                        if txt:
                            _add_decoded(txt, pts, method="qr", code_type="QR_CODE")
                if found:
                    break
            except cv2.error:
                continue

        # 2. 1D Barcode (EAN-13, EAN-8, UPC, Code128...)
        if self._barcode is not None:
            for candidate in (img, cv2.GaussianBlur(img, (3, 3), 0.6)):
                try:
                    res = self._barcode.detectAndDecodeWithType(candidate)
                    ok_b = bool(res[0])
                    if ok_b and len(res) >= 4:
                        texts_b, types_b, points_b = res[1], res[2], res[3]
                        for txt, btype, pts in zip(texts_b, types_b, points_b):
                            if txt:
                                _add_decoded(txt, pts, method="barcode", code_type=str(btype or "BARCODE"))
                        if any(d.method == "barcode" for d in found):
                            break
                except cv2.error:
                    continue
        return found

    def _match_templates_ransac(
        self,
        gray: np.ndarray,
        offset_xy: tuple[int, int],
        orig_scale: float,
        full_shape: tuple[int, int],
        max_candidates: int = 3,
    ) -> list[SkuDetection]:
        templates = self.gallery.snapshot_templates()
        if not templates:
            return []
        kps_f, desc_f = self._detector.detectAndCompute(gray, None)
        if not kps_f or desc_f is None or len(kps_f) < self.min_inliers:
            return []
        pts_f = np.array([kp.pt for kp in kps_f], dtype=np.float32)

        ox, oy = offset_xy
        h_full, w_full = full_shape[:2]
        raw_hits: list[SkuDetection] = []

        for tpl in templates:
            desc_t = tpl["descriptors"]
            if desc_t.shape[0] < 4 or desc_t.shape[1] != desc_f.shape[1] or desc_t.dtype != desc_f.dtype:
                continue
            matches = self._matcher.knnMatch(desc_t, desc_f, k=2)
            good = [m[0] for m in matches if len(m) == 2 and m[0].distance < self.ratio_thresh * m[1].distance]
            if len(good) < self.min_inliers:
                continue

            src_pts = tpl["kps_xy"][[m.queryIdx for m in good]].reshape(-1, 1, 2)
            dst_pts = pts_f[[m.trainIdx for m in good]].reshape(-1, 1, 2)
            H, mask = cv2.findHomography(src_pts, dst_pts, self._ransac_flag, self.reproj_thresh)
            if H is None or mask is None:
                continue
            inliers = int(mask.ravel().sum())
            inlier_ratio = inliers / max(1, len(good))
            if inliers < self.min_inliers or inlier_ratio < self.min_inlier_ratio:
                continue

            tw, th = float(tpl["width"]), float(tpl["height"])
            corners = np.array([[[0.0, 0.0]], [[tw, 0.0]], [[tw, th]], [[0.0, th]]], dtype=np.float32)
            proj = cv2.perspectiveTransform(corners, H).reshape(4, 2)
            if not is_valid_projected_quad(proj, gray.shape[:2]):
                continue

            quad_full = proj / orig_scale + np.array([ox, oy], dtype=np.float32)
            bbox = _quad_to_bbox(quad_full, w_full, h_full)
            conf = min(1.0, (inliers / max(float(self.min_inliers) * 2.0, 1.0)) * 0.6 + inlier_ratio * 0.4)
            raw_hits.append(SkuDetection(
                sku_id=int(tpl["sku_id"]),
                code=str(tpl["code"]),
                name=str(tpl["name"]),
                barcode=tpl.get("barcode"),
                uom=str(tpl.get("uom") or "pcs"),
                method=f"{self.feature_type}_ransac",
                score=round(float(conf), 4),
                inliers=inliers,
                bbox=bbox,
                polygon=[(round(float(p[0]), 2), round(float(p[1]), 2)) for p in quad_full],
                code_type=None,
            ))

        # Sap xep theo so inliers & score giam dan, loai bo khung chong lap (NMS theo IoU > 0.5)
        raw_hits.sort(key=lambda d: (d.inliers, d.score), reverse=True)
        kept: list[SkuDetection] = []
        for cand in raw_hits:
            if any(_box_iou(cand.bbox, prev.bbox) > 0.50 for prev in kept):
                continue
            kept.append(cand)
            if len(kept) >= max_candidates:
                break
        return kept

    def scan_frame(
        self,
        frame: np.ndarray,
        roi_norm: tuple[float, float, float, float] | None = None,
        prefer_code_short_circuit: bool = True,
    ) -> dict:
        """Quet 1 khung hinh (hoac vung ROI chuan hoa 0..1).
        Tra ve dict: {detections, sharpness, skipped_blur, elapsed_ms}.
        """
        t0 = time.perf_counter()
        if frame is None or frame.size == 0:
            return {"detections": [], "sharpness": 0.0, "skipped_blur": False, "elapsed_ms": 0.0}

        h0, w0 = frame.shape[:2]
        if roi_norm is not None:
            rx1 = int(np.clip(round(roi_norm[0] * w0), 0, w0 - 1))
            ry1 = int(np.clip(round(roi_norm[1] * h0), 0, h0 - 1))
            rx2 = int(np.clip(round(roi_norm[2] * w0), rx1 + 1, w0))
            ry2 = int(np.clip(round(roi_norm[3] * h0), ry1 + 1, h0))
            sub = frame[ry1:ry2, rx1:rx2]
            offset = (rx1, ry1)
        else:
            sub = frame
            offset = (0, 0)

        sharp = compute_sharpness(sub)
        if sharp < self.min_blur:
            return {
                "detections": [],
                "sharpness": round(sharp, 2),
                "skipped_blur": True,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 2),
            }

        sh, sw = sub.shape[:2]
        scale = min(1.0, float(self.scan_max_width) / max(sw, 1))
        work = cv2.resize(sub, (max(1, int(round(sw * scale))), max(1, int(round(sh * scale))))) if scale < 1.0 else sub

        with self._lock:
            code_hits = self._scan_codes(work, offset_xy=offset, orig_scale=scale)
            if code_hits and prefer_code_short_circuit and any(d.accepted for d in code_hits):
                return {
                    "detections": code_hits,
                    "sharpness": round(sharp, 2),
                    "skipped_blur": False,
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 2),
                }

            gray = _to_gray(work)
            feat_hits = self._match_templates_ransac(
                gray, offset_xy=offset, orig_scale=scale, full_shape=(h0, w0),
            )

        # Gop ket qua QR/Barcode (ke ca unknown_code) va ORB/SIFT RANSAC khong trung khung
        merged = list(code_hits)
        for fh in feat_hits:
            if not any(_box_iou(fh.bbox, ch.bbox) > 0.50 and ch.accepted for ch in merged):
                merged.append(fh)

        return {
            "detections": merged,
            "sharpness": round(sharp, 2),
            "skipped_blur": False,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 2),
        }
