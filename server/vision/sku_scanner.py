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
    method: str                          # qr | barcode | zxing | orb_ransac | sift_ransac | unknown_code
    score: float                         # 1.0 cho QR/Barcode, inlier_ratio co trong so cho RANSAC
    inliers: int
    bbox: tuple[float, float, float, float]
    polygon: list[tuple[float, float]] = field(default_factory=list)
    code_type: str | None = None

    @property
    def accepted(self) -> bool:
        return self.sku_id is not None and bool(self.code)

    @property
    def experimental(self) -> bool:
        return self.method.endswith("_ransac")

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
                "experimental": self.experimental,
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
                bcs = list(it.get("barcodes") or [])
                bc_val = (it.get("barcode") or "").strip()
                if bc_val and bc_val not in bcs:
                    bcs.insert(0, bc_val)
                for b in bcs:
                    b_s = str(b).strip()
                    if b_s:
                        self.by_barcode[b_s] = it
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
        self.max_instances = max(1, int(scfg.get("max_instances", 4)))

        try:
            import zxingcpp as _zxing  # type: ignore[import-not-found]
            self._zxing = _zxing
        except ImportError:
            self._zxing = None

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
        barcodes: list[str] | tuple[str, ...] | None = None,
        uom: str = "pcs",
        label: str | None = None,
        save_dir: Path | None = None,
        replace_templates: bool = False,
    ) -> dict:
        """Dang ky hoac cap nhat 1 SKU kem anh mau phang (khong bat buoc anh neu chi dung QR/Barcode)."""
        code_c = validate_sku_code(code)
        bc_list = db.parse_barcodes(barcode=barcode, barcodes=barcodes)
        sku_id = db.upsert_sku_item(
            code=code_c, name=name, barcodes=bc_list, uom=uom, status="active",
        )
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
        return {
            "sku_id": sku_id,
            "code": code_c,
            "barcode": bc_list[0] if bc_list else None,
            "barcodes": bc_list,
            "template": tpl_report,
        }

    def scan_codes_only(
        self,
        frame: np.ndarray,
        roi_norm: tuple[float, float, float, float] | None = None,
    ) -> list[SkuDetection]:
        """Quet nhanh rieng QR/Barcode tren 1 khung hinh (khong chay ORB/SIFT) de tu dong dien form dang ky."""
        if frame is None or frame.size == 0:
            return []
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
        sh, sw = sub.shape[:2]
        scale = min(1.0, float(self.scan_max_width) / max(sw, 1))
        work = cv2.resize(sub, (max(1, int(round(sw * scale))), max(1, int(round(sh * scale))))) if scale < 1.0 else sub
        with self._lock:
            return self._scan_codes(work, offset_xy=offset, orig_scale=scale)

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

        # 0. Uu tien zxing-cpp (Apache-2.0) neu co cai dat tren may trien khai: nhay voi QR/DataMatrix/1D nghieng/mo
        # Khi zxing-cpp chay thanh cong khong loi thi tra ve ngay (ke ca khi khung trong) de khong lang phi CPU cho 4 luot OpenCV fallback.
        if self._zxing is not None:
            try:
                zx_hits = self._zxing.read_barcodes(img)
                for r in zx_hits or []:
                    txt = getattr(r, "text", "") or ""
                    pos = getattr(r, "position", None)
                    fmt_name = str(getattr(getattr(r, "format", None), "name", None) or getattr(r, "format", "BARCODE"))
                    if txt and pos is not None:
                        pts_zx = np.array([
                            [pos.top_left.x, pos.top_left.y],
                            [pos.top_right.x, pos.top_right.y],
                            [pos.bottom_right.x, pos.bottom_right.y],
                            [pos.bottom_left.x, pos.bottom_left.y],
                        ], dtype=np.float32)
                        m_kind = "qr" if "QR" in fmt_name.upper() else "barcode"
                        _add_decoded(txt, pts_zx, method=m_kind, code_type=fmt_name)
                return found
            except Exception:  # noqa: BLE001
                pass

        # 1. QR Code OpenCV (thu Aruco detector truoc, fallback sang QRCodeDetector chuan)
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
        max_candidates: int = 12,
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
            remaining = [m[0] for m in matches if len(m) == 2 and m[0].distance < self.ratio_thresh * m[1].distance]
            if len(remaining) < self.min_inliers:
                continue

            tw, th = float(tpl["width"]), float(tpl["height"])
            corners = np.array([[[0.0, 0.0]], [[tw, 0.0]], [[tw, th]], [[0.0, th]]], dtype=np.float32)

            # Sequential Multi-Instance RANSAC (Experimental): sau moi lan tim thay 1 hop hop le,
            # loai cac diem anh nam trong tu giac hop do de tim tiep cac hop cung ma SKU nam canh nhau.
            found_for_tpl = 0
            for _ in range(self.max_instances):
                if len(remaining) < self.min_inliers:
                    break
                src_pts = tpl["kps_xy"][[m.queryIdx for m in remaining]].reshape(-1, 1, 2)
                dst_pts = pts_f[[m.trainIdx for m in remaining]].reshape(-1, 1, 2)
                H, mask = cv2.findHomography(src_pts, dst_pts, self._ransac_flag, self.reproj_thresh)
                if H is None or mask is None:
                    break
                mask_bool = mask.ravel().astype(bool)
                inliers = int(mask_bool.sum())
                inlier_ratio = inliers / max(1, len(remaining))
                if inliers < self.min_inliers:
                    break
                # Hop dau tien cua 1 mau: giu nguyen nguong chuan (min_inlier_ratio) de khong tang nhan nham;
                # chi cho phep thay bang bang chung rat manh (>= 2 * min_inliers). Tu hop thu 2 tro di (cac diem
                # cua hop truoc da bi loai) moi noi sang 1.5 * min_inliers vi mau so len(remaining) da thay doi.
                strong = 2.0 if found_for_tpl == 0 else 1.5
                if inlier_ratio < self.min_inlier_ratio and inliers < int(round(self.min_inliers * strong)):
                    break

                proj = cv2.perspectiveTransform(corners, H).reshape(4, 2)
                if not is_valid_projected_quad(proj, gray.shape[:2]):
                    # Bo tap inliers gia vua gay suy bien de tranh lap vo han
                    remaining = [m for m, keep_m in zip(remaining, ~mask_bool) if keep_m]
                    continue

                quad_full = proj / orig_scale + np.array([ox, oy], dtype=np.float32)
                bbox = _quad_to_bbox(quad_full, w_full, h_full)
                eff_ratio = max(inlier_ratio, min(1.0, inliers / max(float(self.min_inliers) * 2.0, 1.0)))
                conf = min(1.0, (inliers / max(float(self.min_inliers) * 2.0, 1.0)) * 0.6 + eff_ratio * 0.4)
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
                found_for_tpl += 1

                poly_cnt = proj.astype(np.float32).reshape(-1, 1, 2)
                next_rem = []
                for idx_m, m in enumerate(remaining):
                    if mask_bool[idx_m]:
                        continue
                    pt = (float(pts_f[m.trainIdx, 0]), float(pts_f[m.trainIdx, 1]))
                    if cv2.pointPolygonTest(poly_cnt, pt, True) >= -4.0:
                        continue
                    next_rem.append(m)
                remaining = next_rem

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


@dataclass
class _SkuTrack:
    track_id: int
    key: str                     # sku:{code} | unk:{barcode}
    sku_id: int | None
    code: str | None
    name: str | None
    barcode: str | None
    uom: str
    method: str
    score: float
    inliers: int
    bbox: np.ndarray
    first_seen: float
    last_seen: float
    hits: int = 1
    committed: bool = False
    side: int | None = None
    direction: str = "IN"        # IN (Nhap kho) | OUT (Xuat kho)

    @property
    def center(self) -> tuple[float, float]:
        return (float(self.bbox[0] + self.bbox[2]) * 0.5, float(self.bbox[1] + self.bbox[3]) * 0.5)


def _line_side(pt: tuple[float, float], line_norm: list[list[float]], frame_shape: tuple[int, int]) -> int:
    h, w = max(1, int(frame_shape[0])), max(1, int(frame_shape[1]))
    (ax, ay), (bx, by) = (line_norm[0][0] * w, line_norm[0][1] * h), (line_norm[1][0] * w, line_norm[1][1] * h)
    cx, cy = pt
    cross = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    return 1 if cross >= 0.0 else -1


def _crossing_direction(prev_side: int | None, cur_side: int, line_norm: list[list[float]], in_when: str) -> str | None:
    if prev_side is None or prev_side == cur_side:
        return None
    dx = float(line_norm[1][0] - line_norm[0][0])
    dy = float(line_norm[1][1] - line_norm[0][1])
    iw = (in_when or "down").lower()
    # Voi line nam ngang tu trai -> phai (dx >= 0): cross tang (-1 -> 1) la di xuong (down)
    # Voi line thang dung tu tren -> duoi (dy > 0): cross tang (-1 -> 1) la sang trai (left)
    if iw in ("down", "up"):
        positive_is_down = dx >= 0.0
        going_down = (cur_side > prev_side) if positive_is_down else (cur_side < prev_side)
        is_in = going_down if iw == "down" else (not going_down)
    else:
        positive_is_left = dy >= 0.0
        going_right = (cur_side < prev_side) if positive_is_left else (cur_side > prev_side)
        is_in = going_right if iw == "right" else (not going_right)
    return "IN" if is_in else "OUT"


DEFAULT_HORIZONTAL_LINE = [[0.0, 0.55], [1.0, 0.55]]
DEFAULT_VERTICAL_LINE = [[0.5, 0.0], [0.5, 1.0]]


def _default_line_for_in_when(in_when: str) -> list[list[float]]:
    if (in_when or "down").lower() in ("right", "left"):
        return [list(p) for p in DEFAULT_VERTICAL_LINE]
    return [list(p) for p in DEFAULT_HORIZONTAL_LINE]


def _line_matches_in_when(line_norm: list[list[float]], in_when: str) -> bool:
    dx = abs(float(line_norm[1][0]) - float(line_norm[0][0]))
    dy = abs(float(line_norm[1][1]) - float(line_norm[0][1]))
    if (in_when or "down").lower() in ("right", "left"):
        return dy > dx
    return dx >= dy


class SkuTrackCounter:
    """Bam vet kien hang qua cac frame, ho tro 2 che do dem:
    - count_mode == 'hold': kien hang giu on dinh >= min_hold_sec -> +1 theo hold_direction ('IN' | 'OUT').
    - count_mode == 'line': kien hang cat qua vach line_norm -> +1 kem phan chieu IN (Nhap) / OUT (Xuat).
    """

    def __init__(
        self,
        min_hold_sec: float = 0.4,
        cooldown_sec: float = 4.0,
        max_age: float = 1.2,
        iou_thr: float = 0.25,
        count_mode: str = "hold",
        line_norm: list[list[float]] | None = None,
        in_when: str = "down",
        hold_direction: str = "IN",
    ):
        self.min_hold_sec = max(0.0, float(min_hold_sec))
        self.cooldown_sec = max(0.0, float(cooldown_sec))
        self.max_age = max(0.1, float(max_age))
        self.iou_thr = float(iou_thr)
        self.count_mode = count_mode if count_mode in ("hold", "line") else "hold"
        self.in_when = in_when if in_when in ("down", "up", "right", "left") else "down"
        hd = str(hold_direction or "IN").strip().upper()
        self.hold_direction = hd if hd in ("IN", "OUT") else "IN"
        self.line_norm = (
            [[float(line_norm[0][0]), float(line_norm[0][1])],
             [float(line_norm[1][0]), float(line_norm[1][1])]]
            if (line_norm and len(line_norm) == 2)
            else _default_line_for_in_when(self.in_when)
        )
        self.tracks: dict[int, _SkuTrack] = {}
        self._next_id = 1
        self._last_commit_at: dict[str, float] = {}
        self._recent_box_commits: list[tuple[str, tuple[float, float, float, float], float]] = []
        self._lock = threading.RLock()

    def reset(self) -> None:
        with self._lock:
            self.tracks.clear()
            self._last_commit_at.clear()
            self._recent_box_commits.clear()

    def configure(
        self,
        count_mode: str | None = None,
        line_norm: list[list[float]] | None = None,
        in_when: str | None = None,
        min_hold_sec: float | None = None,
        cooldown_sec: float | None = None,
        hold_direction: str | None = None,
    ) -> None:
        with self._lock:
            if count_mode in ("hold", "line") and count_mode != self.count_mode:
                self.count_mode = count_mode
                self.reset()
            if hold_direction is not None:
                hd = str(hold_direction).strip().upper()
                if hd in ("IN", "OUT") and hd != self.hold_direction:
                    self.hold_direction = hd
                    if self.count_mode == "hold":
                        self.reset()
            if line_norm is not None and len(line_norm) == 2:
                self.line_norm = [[float(line_norm[0][0]), float(line_norm[0][1])],
                                  [float(line_norm[1][0]), float(line_norm[1][1])]]
                for tr in self.tracks.values():
                    tr.side = None
            if in_when in ("down", "up", "right", "left"):
                self.in_when = in_when
                # Neu nguoi dung doi chieu ngang <-> doc ma khong truyen toa do line rieng -> tu xoay vach cho khop
                if line_norm is None and not _line_matches_in_when(self.line_norm, in_when):
                    self.line_norm = _default_line_for_in_when(in_when)
                    for tr in self.tracks.values():
                        tr.side = None
            if min_hold_sec is not None:
                self.min_hold_sec = max(0.0, float(min_hold_sec))
            if cooldown_sec is not None:
                self.cooldown_sec = max(0.0, float(cooldown_sec))

    def update(
        self,
        detections: list[SkuDetection],
        now: float | None = None,
        frame_shape: tuple[int, int] = (720, 1280),
    ) -> tuple[list[_SkuTrack], list[_SkuTrack]]:
        with self._lock:
            return self._update_locked(detections, now=now, frame_shape=frame_shape)

    def _update_locked(
        self,
        detections: list[SkuDetection],
        now: float | None = None,
        frame_shape: tuple[int, int] = (720, 1280),
    ) -> tuple[list[_SkuTrack], list[_SkuTrack]]:
        now = float(now if now is not None else time.time())
        self._recent_box_commits = [
            it for it in self._recent_box_commits if (now - it[2]) < self.cooldown_sec
        ]
        used_t, used_d = set(), set()
        fh, fw = max(1, int(frame_shape[0])), max(1, int(frame_shape[1]))
        diag = float(np.hypot(fw, fh))

        # 1. Noi detection vao track dang ton tai (uu tien cung key + IoU >= thr, du phong dich chuyen bang tai)
        pairs = []
        for tid, tr in self.tracks.items():
            tb = (float(tr.bbox[0]), float(tr.bbox[1]), float(tr.bbox[2]), float(tr.bbox[3]))
            tcx, tcy = tr.center
            for di, d in enumerate(detections):
                key = f"sku:{d.code}" if d.accepted else f"unk:{d.barcode or '?'}"
                if key != tr.key:
                    continue
                ov = _box_iou(tb, d.bbox)
                if ov >= self.iou_thr:
                    pairs.append((1.0 + ov, tid, di))
                elif self.count_mode == "line":
                    dcx = (d.bbox[0] + d.bbox[2]) * 0.5
                    dcy = (d.bbox[1] + d.bbox[3]) * 0.5
                    dist_norm = float(np.hypot(dcx - tcx, dcy - tcy)) / max(1.0, diag)
                    if dist_norm <= 0.28:
                        pairs.append((0.5 - dist_norm, tid, di))

        for _, tid, di in sorted(pairs, reverse=True):
            if tid in used_t or di in used_d:
                continue
            tr, d = self.tracks[tid], detections[di]
            tr.bbox = np.array(d.bbox, dtype=np.float32)
            tr.last_seen = now
            tr.hits += 1
            tr.score = max(tr.score, float(d.score))
            tr.inliers = max(tr.inliers, int(d.inliers))
            tr.method = d.method
            used_t.add(tid)
            used_d.add(di)

        for di, d in enumerate(detections):
            if di in used_d:
                continue
            key = f"sku:{d.code}" if d.accepted else f"unk:{d.barcode or '?'}"
            tr = _SkuTrack(
                track_id=self._next_id,
                key=key,
                sku_id=d.sku_id,
                code=d.code,
                name=d.name,
                barcode=d.barcode,
                uom=d.uom,
                method=d.method,
                score=float(d.score),
                inliers=int(d.inliers),
                bbox=np.array(d.bbox, dtype=np.float32),
                first_seen=now,
                last_seen=now,
                direction=self.hold_direction,
            )
            tr.side = _line_side(tr.center, self.line_norm, frame_shape)
            self._next_id += 1
            self.tracks[tr.track_id] = tr

        expired = [tr for tr in self.tracks.values() if now - tr.last_seen > self.max_age]
        for tr in expired:
            del self.tracks[tr.track_id]

        newly_committed: list[_SkuTrack] = []
        active_same_key_count: dict[str, int] = {}
        for tr in self.tracks.values():
            if now - tr.last_seen <= 0.25:
                active_same_key_count[tr.key] = active_same_key_count.get(tr.key, 0) + 1

        for tr in self.tracks.values():
            if self.count_mode == "line":
                cur_side = _line_side(tr.center, self.line_norm, frame_shape)
                crossed = _crossing_direction(tr.side, cur_side, self.line_norm, self.in_when)
                tr.side = cur_side
                if crossed is not None:
                    tr.direction = crossed
                if tr.committed or crossed is None:
                    continue
                tr.committed = True
                self._last_commit_at[tr.key] = now
                newly_committed.append(tr)
                continue

            if tr.committed:
                continue

            # count_mode == "hold": giu dung chieu hold_direction (IN/OUT) da chon, khong bi cat vach ngam ghi de
            tr.direction = self.hold_direction
            held = (now - tr.first_seen) >= self.min_hold_sec
            if not held:
                continue
            tb = (float(tr.bbox[0]), float(tr.bbox[1]), float(tr.bbox[2]), float(tr.bbox[3]))
            # Neu co >= 2 hop cung ma dang hien dien dong thoi o cac vi tri khac nhau (Multi-Instance):
            # chong trung theo o vi tri (IoU > 0.35), con neu chi 1 hop don le thi giu cooldown theo key nhu cu.
            if active_same_key_count.get(tr.key, 1) > 1:
                dup_box = any(
                    k == tr.key and _box_iou(tb, prev_b) > 0.35
                    for k, prev_b, _ in self._recent_box_commits
                )
                if dup_box:
                    tr.committed = True
                    continue
            else:
                last_t = self._last_commit_at.get(tr.key, -1e18)
                if (now - last_t) < self.cooldown_sec:
                    tr.committed = True
                    continue
            tr.committed = True
            self._last_commit_at[tr.key] = now
            self._recent_box_commits.append((tr.key, tb, now))
            newly_committed.append(tr)

        return list(self.tracks.values()), newly_committed
