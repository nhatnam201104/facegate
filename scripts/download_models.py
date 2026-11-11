"""Tai model cho FaceGate demo.

    python scripts/download_models.py              # YuNet + SFace + EdgeFace-XS + 3DDFA_V2 + YOLOX-Nano + MiniFASNet
    python scripts/download_models.py --onnx       # them SCRFD + ArcFace (backend onnx, benchmark)
    python scripts/download_models.py --research   # them AdaFace-IR18 (doi chuan nghien cuu)
"""
import argparse
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

POSE_AND_PROFILE_MODELS = {
    # 3DDFA_V2 (MIT) - 68 diem moc 3D + goc Euler (yaw, pitch, roll) toi +-90 do
    "3ddfa_v2_mb1_120x120.onnx": (
        "https://huggingface.co/Stable-Human/3ddfa_v2/resolve/main/mb1_120x120.onnx",
        5_000_000,
    ),
    "tddfa_u_base.bin": (
        "https://huggingface.co/litert-community/3DDFA-V2-LiteRT/resolve/main/tddfa_u_base.bin",
        800,
    ),
    "tddfa_w_shp_base.bin": (
        "https://huggingface.co/litert-community/3DDFA-V2-LiteRT/resolve/main/tddfa_w_shp_base.bin",
        30_000,
    ),
    "tddfa_w_exp_base.bin": (
        "https://huggingface.co/litert-community/3DDFA-V2-LiteRT/resolve/main/tddfa_w_exp_base.bin",
        8_000,
    ),
    "tddfa_param_mean.bin": (
        "https://huggingface.co/litert-community/3DDFA-V2-LiteRT/resolve/main/tddfa_param_mean.bin",
        200,
    ),
    "tddfa_param_std.bin": (
        "https://huggingface.co/litert-community/3DDFA-V2-LiteRT/resolve/main/tddfa_param_std.bin",
        200,
    ),
    # EdgeFace-XS gamma=0.6 (Idiap, BSD-3-Clause) - nhan dien ben goc nghieng nhe cho CPU
    "edgeface_xs_gamma_06.onnx": (
        "https://huggingface.co/yakhyo/uniface-weights/resolve/main/edgeface_xs_gamma_06.onnx",
        5_000_000,
    ),
}

LIVENESS_MODELS = {
    # MiniFASNet (Minivision Silent-Face-Anti-Spoofing, Apache-2.0) - nhanh chinh dien <=40 do
    "MiniFASNetV2.onnx": (
        "https://github.com/yakhyo/face-anti-spoofing/releases/download/weights/MiniFASNetV2.onnx",
        100_000,
    ),
    "MiniFASNetV1SE.onnx": (
        "https://github.com/yakhyo/face-anti-spoofing/releases/download/weights/MiniFASNetV1SE.onnx",
        100_000,
    ),
    # YOLOX-Nano COCO (Megvii, Apache-2.0) - phat hien person + dien thoai/tablet/man hinh cho nhanh >40 do
    "yolox_nano_coco.onnx": (
        "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.onnx",
        1_000_000,
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

RESEARCH_MODELS = {
    # AdaFace IR-18 (MIT code, WebFace dataset - danh cho nghien cuu / doi chuan)
    "adaface_ir_18.onnx": (
        "https://huggingface.co/yakhyo/uniface-weights/resolve/main/adaface_ir_18.onnx",
        20_000_000,
    ),
}


def download(name: str, spec: tuple[str, int] | str) -> bool:
    url, min_bytes = spec if isinstance(spec, tuple) else (spec, 100_000)
    dst = MODELS_DIR / name
    if dst.exists() and dst.stat().st_size >= min_bytes:
        print(f"[ok]   {name} da co")
        return True
    print(f"[tai]  {name} ...", end=" ", flush=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "facegate-demo"})
        with urllib.request.urlopen(req, timeout=180) as r:
            data = r.read()
    except Exception as e:  # noqa: BLE001
        print(f"LOI: {e}")
        return False
    if len(data) < min_bytes:
        print(f"LOI: file tai ve qua nho ({len(data)} bytes < {min_bytes}, co the la con tro Git LFS)")
        return False
    dst.write_bytes(data)
    unit = f"{len(data) / 1e6:.1f} MB" if len(data) >= 100_000 else f"{len(data) / 1e3:.1f} KB"
    print(unit)
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--onnx", action="store_true", help="tai them model InsightFace cho backend onnx")
    ap.add_argument("--research", action="store_true", help="tai them model AdaFace-IR18 cho doi chuan nghien cuu")
    args = ap.parse_args()
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    todo = {**OPENCV_MODELS, **POSE_AND_PROFILE_MODELS, **LIVENESS_MODELS}
    if args.onnx:
        todo.update(ONNX_MODELS)
    if args.research:
        todo.update(RESEARCH_MODELS)
    ok = all(download(n, spec) for n, spec in todo.items())
    if not ok:
        print("\nMot so model tai that bai. Tai thu cong bang trinh duyet tu cac link tren")
        print(f"va dat vao thu muc: {MODELS_DIR}")
        sys.exit(1)
    print("\nXong.")


if __name__ == "__main__":
    main()
