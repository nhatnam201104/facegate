from __future__ import annotations

import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from server.vision.audio_prompt import LookAtCameraPrompter, TapoChimeNotifier, build_chime_notifier, create_look_prompter
from server.config import load_config


class _FakeTapoClient:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list[tuple] = []

    def startManualAlarm(self):
        if self.fail:
            raise ConnectionError("tapo_unreachable")
        self.calls.append(("startManualAlarm",))

    def stopManualAlarm(self):
        self.calls.append(("stopManualAlarm",))

    def playAlarm(self, dur: int, alarm_type: int, volume: str):
        if self.fail:
            raise ConnectionError("tapo_unreachable")
        self.calls.append(("playAlarm", dur, alarm_type, volume))

    def testUsrDefAudio(self, audio_id: int, enabled: bool, force: int = 1):
        if self.fail:
            raise ConnectionError("tapo_unreachable")
        self.calls.append(("testUsrDefAudio", audio_id, enabled))


def test_prompts_once_after_profile_wait_and_alerts_on_spoof():
    triggered: list[str] = []
    p = LookAtCameraPrompter(
        notifier=triggered.append,
        frontal_max_yaw=40.0,
        profile_wait_sec=1.0,
        cooldown_sec=5.0,
        spoof_chime=True,
        spoof_cooldown_sec=3.0,
    )
    # Goc nghieng 65 do nhung moi 0.6s (< 1.0s) -> chua keu
    assert p.update(1, 10.0, yaw_deg=65.0, live_status="checking") is None
    assert p.update(1, 10.6, yaw_deg=65.0, live_status="checking") is None
    assert triggered == []

    # Du 1.1s nghieng -> keu chuong nhac nhin thang dung 1 lan cho track 1
    assert p.update(1, 11.1, yaw_deg=65.0, live_status="checking") == "prompt"
    assert triggered == ["prompt"]
    assert p.update(1, 12.5, yaw_deg=65.0, live_status="checking") is None

    # Phat hien gia mao tren track 2 -> keu coi canh bao spoof ngay lap tuc (doc lap voi cooldown prompt)
    assert p.update(2, 12.8, yaw_deg=5.0, live_status="spoof", spoof=True) == "spoof"
    assert triggered == ["prompt", "spoof"]
    assert p.update(2, 13.2, yaw_deg=5.0, live_status="spoof", spoof=True) is None


def test_turning_frontal_resets_wait_timer_and_cooldown_across_tracks():
    triggered: list[str] = []
    p = LookAtCameraPrompter(notifier=triggered.append, frontal_max_yaw=40.0, profile_wait_sec=1.0, cooldown_sec=6.0)
    p.update(1, 0.0, yaw_deg=60.0, live_status="checking")
    p.update(1, 0.7, yaw_deg=12.0, live_status="checking")  # quay thang -> reset
    assert p.update(1, 1.4, yaw_deg=60.0, live_status="checking") is None
    assert p.update(1, 2.5, yaw_deg=60.0, live_status="checking") == "prompt"

    # Track moi xuat hien trong luc dang cooldown 6s -> khong keu don dap
    p.update(2, 3.0, yaw_deg=-70.0, live_status="checking")
    assert p.update(2, 4.2, yaw_deg=-70.0, live_status="checking") is None
    p.forget(1)


def test_notifier_manual_alarm_custom_audio_and_web_fallback():
    fake = _FakeTapoClient()
    events: list[dict] = []
    notifier = TapoChimeNotifier(
        host="192.168.50.10",
        user="admin",
        password="secret",
        mode="manual_alarm",
        duration_sec=0.2,
        client_factory=lambda *_: fake,
        on_event=events.append,
    )
    assert notifier.trigger(kind="chime", duration_sec=0.2, blocking=False) is True
    deadline = time.monotonic() + 2.0
    while notifier.status()["play_count"] < 1 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert fake.calls == [("startManualAlarm",), ("stopManualAlarm",)]
    assert events and events[0]["hardware_sent"] is True
    notifier.close()

    # Custom audio mode
    fake_custom = _FakeTapoClient()
    n_custom = TapoChimeNotifier(
        host="192.168.50.10",
        password="secret",
        mode="custom_audio",
        duration_sec=0.2,
        custom_audio_id=2,
        client_factory=lambda *_: fake_custom,
    )
    res_c = n_custom.trigger(kind="prompt", duration_sec=0.2, blocking=True)
    assert res_c["hardware_sent"] is True
    assert fake_custom.calls == [("testUsrDefAudio", 2, True), ("testUsrDefAudio", 2, False)]
    n_custom.close()

    # Fallback Web Audio khi chua cau hinh IP camera
    fb_events: list[dict] = []
    n_web = TapoChimeNotifier(host="", password="", on_event=fb_events.append)
    res_w = n_web.trigger(kind="chime", blocking=True)
    assert res_w["ok"] is True
    assert res_w["hardware_sent"] is False
    assert res_w["fallback"] == "web_chime"
    assert fb_events and fb_events[0]["hardware_sent"] is False


def test_notifier_drops_concurrent_requests_and_backs_off_on_connection_error():
    gate = threading.Event()

    class _SlowClient(_FakeTapoClient):
        def startManualAlarm(self):
            super().startManualAlarm()
            gate.wait(timeout=1.0)

    slow = _SlowClient()
    n = TapoChimeNotifier(
        host="192.168.50.10",
        password="secret",
        mode="manual_alarm",
        duration_sec=0.2,
        client_factory=lambda *_: slow,
    )
    assert n.trigger(kind="chime", blocking=False) is True
    time.sleep(0.03)
    # Dang keu -> bo qua lenh chong cheo
    assert n.trigger(kind="chime", blocking=False) is False
    gate.set()
    time.sleep(0.3)
    n.close()

    # Khi camera mat ket noi -> khong vang exception, tu dong backoff va phat fallback Web
    broken = TapoChimeNotifier(
        host="192.168.50.99",
        password="secret",
        mode="manual_alarm",
        backoff_sec=30.0,
        client_factory=lambda *_: _FakeTapoClient(fail=True),
    )
    r1 = broken.trigger(kind="spoof", blocking=True)
    assert r1["ok"] is True
    assert r1["hardware_sent"] is False
    assert "tapo_unreachable" in r1.get("error", "")
    r2 = broken.trigger(kind="spoof", blocking=True)
    assert r2["hardware_sent"] is False
    assert "error" not in r2  # lan 2 dang trong thoi gian backoff, khong goi mang lai


def test_camera_chime_api_endpoint_and_factory(tmp_path: Path, monkeypatch):
    cfg = load_config()
    cfg["audio_prompt"]["enabled"] = False
    assert create_look_prompter(cfg) is None

    cfg["audio_prompt"]["enabled"] = True
    cfg["audio_prompt"]["host"] = ""
    cfg["audio_prompt"]["password"] = ""
    # Khong co host/pass va khong truyen on_event -> bo qua phan cung
    assert create_look_prompter(cfg, on_event=None) is None
    # Co on_event (Web broadcast) -> van tao prompter de demo tren Web
    assert create_look_prompter(cfg, on_event=lambda _: None) is not None

    monkeypatch.setenv("FACEGATE_ISOLATED_WORKER", "0")
    import server.main as main_mod

    with TestClient(main_mod.app) as client:
        st = client.get("/api/status")
        assert st.status_code == 200
        assert "camera_chime" in st.json()

        resp = client.post("/api/camera/chime", json={"kind": "chime", "duration_sec": 0.3})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["fallback"] == "web_chime"

        bad = client.post("/api/camera/chime", json={"kind": "invalid_kind"})
        assert bad.status_code == 400


def test_extract_rtsp_with_at_sign_in_password_and_hot_reload_from_config(tmp_path: Path, monkeypatch):
    from server.vision.audio_prompt import _extract_host_creds_from_rtsp

    host, user, pwd = _extract_host_creds_from_rtsp("rtsp://namnhat:P@ss@word@192.168.1.24:554/stream1")
    assert host == "192.168.1.24"
    assert user == "namnhat"
    assert pwd == "P@ss@word"

    cfg = load_config()
    cfg["camera"]["source"] = "rtsp://namnhat:P@ss@word@192.168.1.24:554/stream1"
    cfg["audio_prompt"]["host"] = ""
    cfg["audio_prompt"]["password"] = ""
    n = build_chime_notifier(cfg)
    assert n.hardware_configured is True
    assert n.host == "192.168.1.24"
    assert n.user == "namnhat"
    assert n.password == "P@ss@word"


def test_notifier_falls_back_to_setManualAlarm_on_tpap_40210():
    class _TpapC310Client(_FakeTapoClient):
        def startManualAlarm(self):
            raise RuntimeError('Error: METHOD_DO_NOT_EXIST, Response: {"method": "do", "result": {}, "error_code": -40210}')

        def executeFunction(self, method: str, params: dict):
            self.calls.append((method, params))
            return {}

    fake = _TpapC310Client()
    n = TapoChimeNotifier(
        host="192.168.1.24",
        user="admin",
        cloud_password="secret",
        mode="manual_alarm",
        duration_sec=0.2,
        client_factory=lambda *_: fake,
    )
    res1 = n.trigger(kind="chime", duration_sec=0.2, blocking=True)
    assert res1["hardware_sent"] is True
    assert fake.calls == [("setManualAlarm", {"action": "start"}), ("setManualAlarm", {"action": "stop"})]
    # Lan 2 ghi nho _prefer_tpap_manual_alarm, goi thang setManualAlarm
    res2 = n.trigger(kind="chime", duration_sec=0.2, blocking=True)
    assert res2["hardware_sent"] is True
    assert len(fake.calls) == 4


def test_talk_8800_alaw_bell_synthesis_and_dispatch():
    from server.vision.audio_prompt import synth_tapo_bell_alaw

    alaw_chime = synth_tapo_bell_alaw("chime", duration_sec=0.8)
    alaw_spoof = synth_tapo_bell_alaw("spoof", duration_sec=1.0)
    assert len(alaw_chime) == 8000 * 0.8 + 8000 * 0.1
    assert len(alaw_spoof) == 8000 * 1.0 + 8000 * 0.1
    assert len(set(alaw_chime)) > 10  # khong phai im lang tuyet doi

    sent: list[tuple] = []
    n = TapoChimeNotifier(
        host="192.168.1.24",
        cloud_password="secret",
        mode="talk_8800",
        duration_sec=0.8,
        talk_sender=lambda host, pwd, kind, duration_sec, stop_evt: sent.append((host, pwd, kind, duration_sec)),
    )
    res = n.trigger(kind="chime", blocking=True)
    assert res["hardware_sent"] is True
    assert res["mode"] == "talk_8800"
    assert sent == [("192.168.1.24", "secret", "chime", 0.8)]


def test_vietnamese_prompt_wav_alaw_conversion_and_web_serving(monkeypatch):
    from server.vision.audio_prompt import DEFAULT_PROMPT_WAV, DEFAULT_PROMPT_WEB_URL, load_wav_as_alaw

    assert DEFAULT_PROMPT_WAV.is_file()
    alaw = load_wav_as_alaw(DEFAULT_PROMPT_WAV)
    # 3.31s o 8kHz + 120ms khoang lang cuoi -> > 20,000 bytes va khong phai im lang tuyet doi
    assert len(alaw) > 20000
    assert len(set(alaw)) > 30

    events: list[dict] = []
    sent_args: list[dict] = []

    def _fake_talk(host, pwd, kind="chime", duration_sec=0.85, stop_evt=None, prompt_wav=None):
        sent_args.append({"host": host, "kind": kind, "prompt_wav": prompt_wav})

    n = TapoChimeNotifier(
        host="192.168.1.24",
        cloud_password="secret",
        mode="talk_8800",
        talk_sender=_fake_talk,
        on_event=events.append,
    )
    res = n.trigger(kind="prompt", blocking=True)
    assert res["hardware_sent"] is True
    assert res["audio_url"] == DEFAULT_PROMPT_WEB_URL
    assert sent_args == [{"host": "192.168.1.24", "kind": "prompt", "prompt_wav": DEFAULT_PROMPT_WAV}]
    assert events and events[0]["chime_kind"] == "prompt"
    assert events[0]["audio_url"] == DEFAULT_PROMPT_WEB_URL

    monkeypatch.setenv("FACEGATE_ISOLATED_WORKER", "0")
    import server.main as main_mod

    with TestClient(main_mod.app) as client:
        r_prompt = client.post("/api/camera/chime", json={"kind": "prompt"})
        assert r_prompt.status_code == 200
        assert r_prompt.json()["audio_url"] == DEFAULT_PROMPT_WEB_URL

        r_wav = client.get(DEFAULT_PROMPT_WEB_URL)
        assert r_wav.status_code == 200
        assert r_wav.content[:4] == b"RIFF"
