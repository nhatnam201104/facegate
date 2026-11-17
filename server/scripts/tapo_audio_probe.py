"""CLI kiem tra & phat thu tieng chuong tren loa camera TP-Link Tapo (C310 / C200 / C320WS).

Vi du chay:
  # 1. Kiem tra ket noi + danh sach kieu chuong camera ho tro va keu thu 1 giay:
  TAPO_HOST=192.168.50.10 TAPO_PASSWORD='mat_khau_tapo' .venv/bin/python scripts/tapo_audio_probe.py --probe

  # 2. Phat thu chuong manual_alarm hoac play_alarm:
  .venv/bin/python scripts/tapo_audio_probe.py --host 192.168.50.10 --password 'mat_khau' --duration 1.5
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from server.vision.audio_prompt import build_chime_notifier  # noqa: E402
from server.config import load_config  # noqa: E402


def probe_tapo_speaker(host: str, user: str, password: str, cloud_password: str = "") -> dict:
    from pytapo import Tapo

    cam = Tapo(
        host,
        user or "admin",
        cloud_password or password,
        cloudPassword=cloud_password,
        printWarnInformation=False,
    )
    info: dict = {"host": host, "basic_info": cam.basicInfo}
    for label, fn_name in (
        ("alarm_info", "getAlarm"),
        ("supported_alarm_types", "getSupportAlarmTypeList"),
        ("audio_config", "getAudioConfig"),
    ):
        fn = getattr(cam, fn_name, None)
        if fn is not None:
            try:
                info[label] = fn()
            except Exception as e:  # noqa: BLE001
                info[label] = f"unsupported_or_err: {e}"
    return info


def main() -> None:
    ap = argparse.ArgumentParser(description="Phat thu tieng chuong / kiem tra loa tren camera TP-Link Tapo")
    ap.add_argument("--host", default="", help="IP camera Tapo (mac dinh doc tu config.yaml hoac TAPO_HOST / CAM_IP)")
    ap.add_argument("--user", default="", help="Tai khoan camera (mac dinh: admin)")
    ap.add_argument("--password", default="", help="Mat khau Camera Account hoac TP-Link Cloud")
    ap.add_argument("--cloud-password", default="", help="Mat khau TP-Link Cloud (neu firmware bat SPAKE2+/KLAP)")
    ap.add_argument("--mode", choices=["talk_8800", "manual_alarm", "play_alarm", "custom_audio"], default="",
                    help="Che do phat chuong (mac dinh theo config.yaml)")
    ap.add_argument("--kind", choices=["chime", "prompt", "spoof"], default="chime")
    ap.add_argument("--duration", type=float, default=1.2, help="Thoi luong keu chuong (giay)")
    ap.add_argument("--probe", action="store_true", help="Doc thong so loa & kieu chuong camera ho tro truoc khi keu")
    args = ap.parse_args()

    cfg = load_config()
    ac = cfg.setdefault("audio_prompt", {})
    if args.host:
        ac["host"] = args.host
    if args.user:
        ac["user"] = args.user
    if args.password:
        ac["password"] = args.password
    if args.cloud_password:
        ac["cloud_password"] = args.cloud_password
    if args.mode:
        ac["mode"] = args.mode

    notifier = build_chime_notifier(cfg)
    if args.probe and notifier.hardware_configured:
        info = probe_tapo_speaker(notifier.host, notifier.user, notifier.password, notifier.cloud_password)
        print("=== THONG SO LOA CAMERA TAPO ===")
        print(json.dumps(info, indent=2, ensure_ascii=False))

    res = notifier.trigger(kind=args.kind, duration_sec=args.duration, blocking=True)
    print(json.dumps(res, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
