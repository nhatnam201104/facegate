"""Tai va kiem tra checksum day du cac mo hinh thuong mai cho FaceGate v4 (Apache-2.0 / MIT / BSD-3).

    python scripts/download_models.py           # YuNet + SFace + EdgeFace-XS + 3DDFA_V2 + YOLOX-Nano + MiniFASNet
    python scripts/download_models.py --onnx    # them SCRFD + ArcFace (CHI dung nghien cuu / benchmark)
"""
import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"

OPENCV_MODELS = {
    # OpenCV Zoo - Apache-2.0
    "face_detection_yunet_2023mar.onnx": (
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        100_000,
    ),
    "face_recognition_sface_2021dec.onnx": (
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        100_000,
    ),
}

V4_COMMERCIAL_MODELS = {
    # EdgeFace-XS (otroshi/edgeface - MIT): nhan dien da goc toi +-90 do tren CPU
    "edgeface_xs_gamma_06.onnx": (
        "https://huggingface.co/otroshi/edgeface/resolve/main/edgeface_xs_gamma_06.onnx",
        1_000_000,
    ),
    # 3DDFA_V2 MobileNet-120x120 + BFM base params (cleardusk/3DDFA_V2 - MIT): uoc luong tu the 3D
    "3ddfa_v2_mb1_120x120.onnx": (
        "https://github.com/cleardusk/3DDFA_V2/raw/master/weights/mb1_120x120.onnx",
        1_000_000,
    ),
    # YOLOX-Nano COCO (Megvii-BaseDetection/YOLOX - Apache-2.0): phat hien nguoi & vat chua gia mao
    "yolox_nano_coco.onnx": (
        "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.onnx",
        1_000_000,
    ),
}

TDDFA_PARAM_FILES = (
    "tddfa_param_mean.bin",
    "tddfa_param_std.bin",
    "tddfa_u_base.bin",
    "tddfa_w_exp_base.bin",
    "tddfa_w_shp_base.bin",
)

LIVENESS_MODELS = {
    # MiniFASNet (Minivision Silent-Face-Anti-Spoofing, Apache-2.0)
    "MiniFASNetV2.onnx": (
        "https://github.com/yakhyo/face-anti-spoofing/releases/download/weights/MiniFASNetV2.onnx",
        100_000,
    ),
    "MiniFASNetV1SE.onnx": (
        "https://github.com/yakhyo/face-anti-spoofing/releases/download/weights/MiniFASNetV1SE.onnx",
        100_000,
    ),
}

ONNX_MODELS = {
    # Weights InsightFace: chi dung cho nghien cuu / benchmark, KHONG dung thuong mai
    "scrfd_500m_kps.onnx": (
        "https://github.com/yakhyo/uniface/releases/download/weights/scrfd_500m_kps.onnx",
        100_000,
    ),
    "w600k_mbf.onnx": (
        "https://github.com/yakhyo/uniface/releases/download/weights/w600k_mbf.onnx",
        100_000,
    ),
}


def sha256_short(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def download(name: str, spec: tuple[str, int], expected_sha_prefix: str | None = None) -> bool:
    url, min_bytes = spec
    dst = MODELS_DIR / name
    if dst.exists() and dst.stat().st_size >= min_bytes:
        digest = sha256_short(dst)
        if expected_sha_prefix and not digest.startswith(expected_sha_prefix):
            print(f"[loi]  {name} sai checksum ({digest} != {expected_sha_prefix}*)")
            return False
        print(f"[ok]   {name} da co (sha256={digest}...)")
        return True
    print(f"[tai]  {name} ...", end=" ", flush=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "facegate-v4"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
    except Exception as e:  # noqa: BLE001
        print(f"LOI: {e}")
        return False
    if len(data) < min_bytes:
        print("LOI: file tai ve qua nho (co the la con tro Git LFS)")
        return False
    dst.write_bytes(data)
    print(f"{len(data) / 1e6:.1f} MB (sha256={sha256_short(dst)}...)")
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--onnx", action="store_true", help="tai them model cho backend onnx (chi nghien cuu)")
    ap.add_argument("--verify-only", action="store_true", help="chi kiem tra su ton tai va in sha256 cua cac model")
    args = ap.parse_args()
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    todo = {**OPENCV_MODELS, **V4_COMMERCIAL_MODELS, **LIVENESS_MODELS}
    if args.onnx:
        todo.update(ONNX_MODELS)
    if args.verify_only:
        ok = True
        for name, (_, min_b) in todo.items():
            p = MODELS_DIR / name
            if p.exists() and p.stat().st_size >= min_b:
                print(f"[ok]   {name} ({p.stat().st_size / 1e6:.2f} MB, sha256={sha256_short(p)}...)")
            else:
                print(f"[thieu] {name}")
                ok = False
        for bname in TDDFA_PARAM_FILES:
            bp = MODELS_DIR / bname
            if bp.exists() and bp.stat().st_size > 0:
                print(f"[ok]   {bname} (sha256={sha256_short(bp)}...)")
        sys.exit(0 if ok else 1)
    ok = all(download(n, s) for n, s in todo.items())
    if not ok:
        print("\nMot so model tai that bai. Tai thu cong bang trinh duyet tu cac link tren")
        print(f"va dat vao thu muc: {MODELS_DIR}")
        sys.exit(1)
    print("\nXong.")


if __name__ == "__main__":
    main()
