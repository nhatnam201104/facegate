"""Tai model cho FaceGate demo.

    python scripts/download_models.py           # YuNet + SFace + MiniFASNet (chong gia mao)
    python scripts/download_models.py --onnx    # them SCRFD + ArcFace (backend onnx, benchmark)
"""
import argparse
import sys
import urllib.request
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"

OPENCV_MODELS = {
    # OpenCV Zoo - Apache-2.0
    "face_detection_yunet_2023mar.onnx":
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "face_recognition_sface_2021dec.onnx":
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
}

LIVENESS_MODELS = {
    # MiniFASNet (Minivision Silent-Face-Anti-Spoofing, Apache-2.0), ban ONNX tu yakhyo/face-anti-spoofing
    "MiniFASNetV2.onnx": "https://github.com/yakhyo/face-anti-spoofing/releases/download/weights/MiniFASNetV2.onnx",
    "MiniFASNetV1SE.onnx": "https://github.com/yakhyo/face-anti-spoofing/releases/download/weights/MiniFASNetV1SE.onnx",
}

ONNX_MODELS = {
    # Weights InsightFace: chi dung cho nghien cuu / benchmark, KHONG dung thuong mai
    "scrfd_500m_kps.onnx": "https://github.com/yakhyo/uniface/releases/download/weights/scrfd_500m_kps.onnx",
    "w600k_mbf.onnx": "https://github.com/yakhyo/uniface/releases/download/weights/w600k_mbf.onnx",
}


def download(name: str, url: str) -> bool:
    dst = MODELS_DIR / name
    if dst.exists() and dst.stat().st_size > 100_000:
        print(f"[ok]   {name} da co")
        return True
    print(f"[tai]  {name} ...", end=" ", flush=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "facegate-demo"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
    except Exception as e:  # noqa: BLE001
        print(f"LOI: {e}")
        return False
    if len(data) < 100_000:  # file con tro Git LFS hoac trang loi
        print("LOI: file tai ve qua nho (co the la con tro Git LFS)")
        return False
    dst.write_bytes(data)
    print(f"{len(data) / 1e6:.1f} MB")
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--onnx", action="store_true", help="tai them model cho backend onnx")
    args = ap.parse_args()
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    todo = {**OPENCV_MODELS, **LIVENESS_MODELS}
    if args.onnx:
        todo.update(ONNX_MODELS)
    ok = all(download(n, u) for n, u in todo.items())
    if not ok:
        print("\nMot so model tai that bai. Tai thu cong bang trinh duyet tu cac link tren")
        print(f"va dat vao thu muc: {MODELS_DIR}")
        sys.exit(1)
    print("\nXong.")


if __name__ == "__main__":
    main()
