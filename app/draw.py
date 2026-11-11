"""Ve khung + nhan tieng Viet len frame (Pillow), fallback bo dau neu khong co font."""
from __future__ import annotations

import os
import unicodedata

import cv2
import numpy as np

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover
    Image = None

_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\tahoma.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
    "/usr/share/fonts/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/google-noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/ttf-dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]
_font_cache: dict = {}


def _font(size: int):
    if Image is None:
        return None
    if size not in _font_cache:
        f = None
        for p in _FONT_CANDIDATES:
            if os.path.exists(p):
                try:
                    f = ImageFont.truetype(p, size)
                    break
                except OSError:
                    continue
        _font_cache[size] = f
    return _font_cache[size]


def strip_accents(s: str) -> str:
    s = s.replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


GREEN, ORANGE, RED, GRAY = (60, 200, 90), (0, 165, 255), (60, 60, 230), (170, 170, 170)


def draw_overlay(frame: np.ndarray, boxes: list[dict], hud: str, line=None) -> np.ndarray:
    """boxes: [{bbox:(x1,y1,x2,y2), color:(b,g,r), label:str}] da theo toa do frame."""
    out = frame
    for b in boxes:
        x1, y1, x2, y2 = map(int, b["bbox"])
        cv2.rectangle(out, (x1, y1), (x2, y2), b["color"], 2)
    if line is not None:
        cv2.line(out, line[0], line[1], (255, 200, 0), 2)
    font = _font(18)
    labels = [(b, b["label"]) for b in boxes if b.get("label")]
    if font is not None:
        img = Image.fromarray(cv2.cvtColor(out, cv2.COLOR_BGR2RGB))
        d = ImageDraw.Draw(img)
        for b, text in labels:
            x1, y1 = int(b["bbox"][0]), int(b["bbox"][1])
            tw = d.textlength(text, font=font)
            ty = max(0, y1 - 26)
            c = b["color"][::-1]
            d.rectangle([x1, ty, x1 + tw + 10, ty + 24], fill=c)
            d.text((x1 + 5, ty + 1), text, font=font, fill=(255, 255, 255))
        d.rectangle([0, 0, d.textlength(hud, font=_font(14)) + 12, 22], fill=(0, 0, 0))
        d.text((6, 3), hud, font=_font(14), fill=(220, 220, 220))
        out = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
    else:
        for b, text in labels:
            x1, y1 = int(b["bbox"][0]), int(b["bbox"][1])
            t = strip_accents(text)
            (tw, th), _ = cv2.getTextSize(t, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            cv2.rectangle(out, (x1, max(0, y1 - th - 10)), (x1 + tw + 8, y1), b["color"], -1)
            cv2.putText(out, t, (x1 + 4, y1 - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.putText(out, strip_accents(hud), (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (220, 220, 220), 1)
    return out
