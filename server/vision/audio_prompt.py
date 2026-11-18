"""Phat chuong / am bao tren loa camera TP-Link Tapo (port 8800 Talk Stream + pytapo) kem du phong Web Audio khi demo.

Ho tro cac che do am tren Tapo C310 / C200 / C320WS:
  - "talk_8800"    : Phat truc tiep tieng chuong Dinh-Dong / coi canh bao G.711 A-law (MPEG-TS) qua kenh loa port 8800 (mac dinh, em tai, khong nhay den trang)
  - "manual_alarm" : Goi startManualAlarm / setManualAlarm tren port 443
  - "play_alarm"   : playAlarm(duration, alarm_type, volume)
  - "custom_audio" : testUsrDefAudio(id=custom_audio_id, enabled=True) -> sleep -> stop

Moi lenh mang toi camera chay tren luong nen (background daemon thread), khong bao gio chan luong AI
hoac lam crash he thong neu camera mat ket noi / sai mat khau (tu dong backoff).
"""
from __future__ import annotations

import hashlib
import inspect
import logging
import math
import os
import re
import socket
import struct
import threading
import time
import wave
from pathlib import Path
from typing import Callable

log = logging.getLogger("audio_prompt")
_TAPO_WARNED = False


def _warn_tapo_once() -> None:
    """Feature 002 (FR-009): pytapo goi API TP-Link KHONG chinh thuc -> canh bao 1 lan khi bat backend tapo."""
    global _TAPO_WARNED
    if not _TAPO_WARNED:
        _TAPO_WARNED = True
        log.warning("audio_prompt.backend=tapo dùng API không chính thức của TP-Link (pytapo); "
                    "kiểm tra điều khoản sử dụng với khách hàng trước khi triển khai")

DEFAULT_PROMPT_WAV = Path(__file__).resolve().parent.parent.parent / "webapp" / "audio" / "look_at_camera.wav"
DEFAULT_PROMPT_WEB_URL = "/static/audio/look_at_camera.wav"


def _crc32_mpeg(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for b in data:
        crc ^= b << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if (crc & 0x80000000) else (crc << 1) & 0xFFFFFFFF
    return crc


def _ts_section_packet(pid: int, payload: bytes, cc: int = 0) -> bytes:
    body = b"\x00" + payload
    pkt = bytearray([0x47, 0x40 | ((pid >> 8) & 0x1F), pid & 0xFF, 0x10 | (cc & 0x0F)]) + body
    pkt += b"\xFF" * (188 - len(pkt))
    return bytes(pkt[:188])


def _build_tapo_audio_pat_pmt(audio_pid: int = 0x45, stream_type: int = 0x90) -> bytes:
    pat_sec = bytes([0x00, 0xB0, 13, 0x00, 0x01, 0xC1, 0x00, 0x00, 0x00, 0x01, 0xE0, 0x12])
    pat_sec += struct.pack(">I", _crc32_mpeg(pat_sec))
    pmt_sec = bytes([0x02, 0xB0, 18, 0x00, 0x01, 0xC1, 0x00, 0x00, 0xE0, audio_pid, 0xF0, 0x00, stream_type, 0xE0, audio_pid, 0xF0, 0x00])
    pmt_sec += struct.pack(">I", _crc32_mpeg(pmt_sec))
    return _ts_section_packet(0x0000, pat_sec) + _ts_section_packet(0x0012, pmt_sec)


def _encode_alaw_sample(pcm16: int) -> int:
    mask = 0x55
    sign = 0x00
    if pcm16 < 0:
        pcm16 = -pcm16 - 1
        sign = 0x80
    if pcm16 > 32635:
        pcm16 = 32635
    if pcm16 < 256:
        val = pcm16 >> 4
    else:
        exp = 7
        for e in range(7, 0, -1):
            if pcm16 & (1 << (e + 7)):
                exp = e
                break
        mant = (pcm16 >> (exp + 3)) & 0x0F
        val = (exp << 4) | mant
    return (val ^ mask) ^ (0x80 if sign == 0 else 0x00)


def synth_tapo_bell_alaw(kind: str = "chime", duration_sec: float = 0.85, sr: int = 8000) -> bytes:
    """Tong hop tieng chuong Dinh-Dong ('chime' / 'prompt') hoac coi 2 tong ('spoof') dang G.711 A-law 8kHz mono."""
    total = max(int(sr * 0.25), int(sr * min(4.0, float(duration_sec))))
    out = bytearray(total)
    for n in range(total):
        t = n / sr
        if kind == "spoof":
            # 2-3 hoi canh bao ro rang 760Hz -> 540Hz
            slot = int(t / 0.22)
            lt = t - slot * 0.22
            if lt < 0.16:
                freq = 760.0 - (lt / 0.16) * 220.0
                env = min(1.0, lt / 0.015) * min(1.0, (0.16 - lt) / 0.02)
                s = math.sin(2.0 * math.pi * freq * lt) * env
            else:
                s = 0.0
        elif kind == "prompt":
            # Hai not nhac nhe nhang (G5 -> C6)
            split = total * 0.42 / sr
            if t < split:
                env = min(1.0, t / 0.012) * math.exp(-t * 6.5)
                s = (0.85 * math.sin(2.0 * math.pi * 783.99 * t) + 0.15 * math.sin(4.0 * math.pi * 783.99 * t)) * env
            else:
                t2 = t - split
                env = min(1.0, t2 / 0.012) * math.exp(-t2 * 5.0)
                s = (0.85 * math.sin(2.0 * math.pi * 1046.5 * t2) + 0.15 * math.sin(4.0 * math.pi * 1046.5 * t2)) * env
        else:
            # Tieng chuong cua Dinh-Dong (E5 -> C5)
            split = total * 0.42 / sr
            if t < split:
                env = min(1.0, t / 0.012) * math.exp(-t * 5.5)
                s = (0.82 * math.sin(2.0 * math.pi * 659.25 * t) + 0.18 * math.sin(4.0 * math.pi * 659.25 * t)) * env
            else:
                t2 = t - split
                env = min(1.0, t2 / 0.012) * math.exp(-t2 * 4.0)
                s = (0.82 * math.sin(2.0 * math.pi * 523.25 * t2) + 0.18 * math.sin(4.0 * math.pi * 523.25 * t2)) * env
        pcm = int(max(-32000, min(32000, s * 28000)))
        out[n] = _encode_alaw_sample(pcm)
    # Them 100ms khoang lang cuoi de camera xa het bo dem loa
    return bytes(out) + (b"\xD5" * int(sr * 0.1))


def load_wav_as_alaw(wav_path: str | Path, sr_out: int = 8000, max_sec: float = 8.0) -> bytes:
    """Doc file WAV PCM 16-bit (mono/stereo) va chuyen sang G.711 A-law 8kHz mono de phat qua Tapo port 8800."""
    p = Path(wav_path)
    if not p.is_file():
        raise FileNotFoundError(f"Khong tim thay file am thanh: {p}")
    with wave.open(str(p), "rb") as wf:
        ch = wf.getnchannels()
        sw = wf.getsampwidth()
        sr_in = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)
    if sw != 2 or sr_in <= 0 or ch <= 0:
        raise ValueError(f"Chi ho tro WAV PCM 16-bit hop le (got ch={ch}, sw={sw}, sr={sr_in})")
    total_samples = len(raw) // (2 * ch)
    if total_samples == 0:
        return b"\xD5" * int(sr_out * 0.1)
    pcm_all = struct.unpack(f"<{total_samples * ch}h", raw[:total_samples * 2 * ch])
    mono = [pcm_all[i * ch] if ch == 1 else sum(pcm_all[i * ch:(i + 1) * ch]) // ch for i in range(total_samples)]

    out_len = min(int(sr_out * max_sec), max(1, int(round(len(mono) * sr_out / sr_in))))
    out = bytearray(out_len)
    ratio = sr_in / sr_out
    for n in range(out_len):
        pos = n * ratio
        idx = int(pos)
        frac = pos - idx
        s0 = mono[idx]
        s1 = mono[min(len(mono) - 1, idx + 1)]
        sample = int(s0 + (s1 - s0) * frac)
        out[n] = _encode_alaw_sample(max(-32768, min(32767, sample)))
    return bytes(out) + (b"\xD5" * int(sr_out * 0.12))


def _ts_audio_pes_packet(audio_pid: int, alaw_160: bytes, pts_90k: int, cc: int) -> tuple[bytes, int]:
    pts_b = bytes([
        0x21 | (((pts_90k >> 30) & 0x07) << 1),
        (pts_90k >> 22) & 0xFF,
        0x01 | (((pts_90k >> 15) & 0x7F) << 1),
        (pts_90k >> 7) & 0xFF,
        0x01 | ((pts_90k & 0x7F) << 1),
    ])
    pes_len = 3 + 5 + len(alaw_160)
    pes = bytes([0x00, 0x00, 0x01, 0xC0, (pes_len >> 8) & 0xFF, pes_len & 0xFF, 0x80, 0x80, 0x05]) + pts_b + alaw_160
    pad_needed = 184 - len(pes)
    if pad_needed > 0:
        af_len = pad_needed - 1
        af = bytes([af_len]) + (b"\x00" + b"\xFF" * (af_len - 1) if af_len > 0 else b"")
        pkt = bytes([0x47, 0x40 | ((audio_pid >> 8) & 0x1F), audio_pid & 0xFF, 0x30 | (cc & 0x0F)]) + af + pes
    else:
        pkt = bytes([0x47, 0x40 | ((audio_pid >> 8) & 0x1F), audio_pid & 0xFF, 0x10 | (cc & 0x0F)]) + pes[:184]
    return pkt, (cc + 1) & 0x0F


def _play_audio_via_tapo_port8800(
    host: str,
    cloud_password: str,
    kind: str = "chime",
    duration_sec: float = 0.85,
    port: int = 8800,
    stop_evt: threading.Event | None = None,
    prompt_wav: str | Path | None = None,
) -> None:
    """Ket noi kenh Talk Two-Way Audio cua Tapo (HTTP Digest tren port 8800) va phat am thanh G.711 A-law."""
    alaw: bytes | None = None
    if kind == "prompt" and prompt_wav:
        wav_p = Path(prompt_wav)
        if wav_p.is_file():
            try:
                alaw = load_wav_as_alaw(wav_p)
            except Exception as e:  # noqa: BLE001
                log.warning("Loi doc WAV %s (%s) -> fallback chuong synth", wav_p, e)
    if alaw is None:
        alaw = synth_tapo_bell_alaw(kind=kind, duration_sec=duration_sec)
    pwd_sha = hashlib.sha256(cloud_password.encode()).hexdigest().upper()
    pwd_md5 = hashlib.md5(cloud_password.encode()).hexdigest().upper()

    def _dial_with_hash(pwd_hash: str) -> tuple[socket.socket, str]:
        s = socket.create_connection((host, port), timeout=5.0)
        req1 = (
            f"POST /stream HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Content-Type: multipart/mixed; boundary=--client-stream-boundary--\r\n"
            f"Connection: keep-alive\r\n"
            f"Content-Length: -1\r\n\r\n"
        ).encode()
        s.sendall(req1)
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        head = buf.decode(errors="ignore")
        realm = (re.search(r'realm="([^"]+)"', head) or [None, ""])[1]
        nonce = (re.search(r'nonce="([^"]+)"', head) or [None, ""])[1]
        qop = (re.search(r'qop="([^"]+)"', head) or [None, "auth"])[1]
        opq_m = re.search(r'opaque="([^"]+)"', head)
        cnonce = os.urandom(16).hex()
        nc = "00000001"
        ha1 = hashlib.md5(f"admin:{realm}:{pwd_hash}".encode()).hexdigest()
        ha2 = hashlib.md5(b"POST:/stream").hexdigest()
        resp = hashlib.md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}".encode()).hexdigest()
        auth_hdr = (
            f'Digest username="admin", realm="{realm}", nonce="{nonce}", '
            f'uri="/stream", qop={qop}, nc={nc}, cnonce="{cnonce}", response="{resp}"'
        )
        if opq_m:
            auth_hdr += f', opaque="{opq_m.group(1)}", algorithm=MD5'
        req2 = (
            f"POST /stream HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Content-Type: multipart/mixed; boundary=--client-stream-boundary--\r\n"
            f"Connection: keep-alive\r\n"
            f"Content-Length: -1\r\n"
            f"Authorization: {auth_hdr}\r\n\r\n"
        ).encode()
        s.sendall(req2)
        buf2 = b""
        while b"\r\n\r\n" not in buf2:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf2 += chunk
        return s, buf2.decode(errors="ignore")

    sock, head2 = _dial_with_hash(pwd_sha)
    if "200 OK" not in head2:
        sock.close()
        sock, head2 = _dial_with_hash(pwd_md5)
    if "200 OK" not in head2:
        sock.close()
        raise RuntimeError(f"Port 8800 tu choi ket noi loa: {head2.splitlines()[0] if head2 else 'empty'}")

    try:
        talk_req = b'{"params":{"talk":{"mode":"aec"},"method":"get"},"seq":3,"type":"request"}'
        sock.sendall(
            b"----client-stream-boundary--\r\n"
            b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(talk_req)}\r\n\r\n".encode()
            + talk_req
            + b"\r\n"
        )
        reply = sock.recv(4096).decode(errors="ignore")
        m_sid = re.search(r'"session_id"\s*:\s*"?([^\s",}]+)', reply)
        if not m_sid:
            raise RuntimeError(f"Camera khong cap session talk: {reply.strip()}")
        sid = m_sid.group(1)

        def _send_part(data: bytes) -> None:
            sock.sendall(
                b"----client-stream-boundary--\r\n"
                b"Content-Type: audio/mp2t\r\n"
                b"X-If-Encrypt: 0\r\n"
                + f"X-Session-Id: {sid}\r\n".encode()
                + f"Content-Length: {len(data)}\r\n\r\n".encode()
                + data
            )

        _send_part(_build_tapo_audio_pat_pmt(0x45, 0x90))
        cc = 0
        pts = 0
        for off in range(0, len(alaw), 160):
            if stop_evt is not None and stop_evt.is_set():
                break
            chunk = alaw[off:off + 160]
            if len(chunk) < 160:
                chunk = chunk + b"\xD5" * (160 - len(chunk))
            pkt, cc = _ts_audio_pes_packet(0x45, chunk, pts, cc)
            _send_part(pkt)
            pts += 1800  # 20ms o tan so xung 90kHz
            time.sleep(0.018)
    finally:
        try:
            sock.close()
        except Exception:  # noqa: BLE001
            pass


def _extract_host_creds_from_rtsp(url: str) -> tuple[str, str, str]:
    raw = str(url or "").strip()
    if not raw.lower().startswith(("rtsp://", "rtsps://", "http://", "https://")):
        return "", "", ""
    try:
        scheme_sep = raw.find("://")
        after_scheme = raw[scheme_sep + 3:]
        path_idx = after_scheme.find("/")
        authority = after_scheme[:path_idx] if path_idx != -1 else after_scheme
        if "@" in authority:
            creds, host_port = authority.rsplit("@", 1)
            user, _, password = creds.partition(":")
        else:
            user, password, host_port = "", "", authority
        host = host_port.split(":", 1)[0].strip("[]")
        if not host or host in ("127.0.0.1", "localhost", "0.0.0.0"):
            return "", "", ""
        return host, user, password
    except Exception:  # noqa: BLE001
        return "", "", ""


class TapoChimeNotifier:
    """Dieu khien chuong/coi tren loa camera Tapo bat dong bo (khong khoa pipeline)."""

    def __init__(
        self,
        host: str = "",
        user: str = "",
        password: str = "",
        cloud_password: str = "",
        mode: str = "talk_8800",
        duration_sec: float = 0.85,
        spoof_duration_sec: float = 1.2,
        alarm_type: int = 0,
        alarm_volume: str = "low",
        custom_audio_id: int = 1,
        backoff_sec: float = 15.0,
        prompt_wav: str | Path | None = None,
        prompt_audio_url: str = DEFAULT_PROMPT_WEB_URL,
        client_factory: Callable | None = None,
        talk_sender: Callable | None = None,
        on_event: Callable[[dict], None] | None = None,
    ):
        self.host = str(host or "").strip()
        self.user = str(user or "").strip()
        self.password = str(password or "").strip()
        self.cloud_password = str(cloud_password or "").strip()
        self.mode = str(mode or "talk_8800").strip().lower()
        self.duration_sec = max(0.2, min(10.0, float(duration_sec)))
        self.spoof_duration_sec = max(0.3, min(15.0, float(spoof_duration_sec)))
        self.alarm_type = int(alarm_type)
        self.alarm_volume = str(alarm_volume or "low")
        self.custom_audio_id = int(custom_audio_id)
        self.backoff_sec = max(1.0, float(backoff_sec))
        if prompt_wav is not None:
            pw = str(prompt_wav).strip()
            self.prompt_wav: Path | None = Path(pw) if pw else None
        else:
            self.prompt_wav = DEFAULT_PROMPT_WAV if DEFAULT_PROMPT_WAV.is_file() else None
        self.prompt_audio_url = str(prompt_audio_url or DEFAULT_PROMPT_WEB_URL).strip()
        self._client_factory = client_factory
        self._talk_sender = talk_sender or _play_audio_via_tapo_port8800
        self._on_event = on_event

        self._lock = threading.Lock()
        self._client = None
        self._playing = False
        self._closed = False
        self._stop_evt = threading.Event()
        self._backoff_until = 0.0
        self._prefer_tpap_manual_alarm = False
        self.last_error: str | None = None
        self.last_triggered_at: float | None = None
        self.play_count = 0

    @property
    def hardware_configured(self) -> bool:
        return bool(self.host and (self.password or self.cloud_password))

    def _get_client(self):
        if self._client is not None:
            return self._client
        if self._client_factory is not None:
            self._client = self._client_factory(self.host, self.user, self.password, self.cloud_password)
            return self._client
        from pytapo import Tapo
        auth_pwd = self.cloud_password or self.password
        self._client = Tapo(
            self.host,
            self.user or "admin",
            auth_pwd,
            cloudPassword=self.cloud_password,
            printWarnInformation=False,
        )
        return self._client

    def _run_manual_alarm_pulse(self, cam, dur: float) -> None:
        """Goi chuong/coi thu cong: thu startManualAlarm, neu fw TPAP moi bao -40210 thi tu dong dung setManualAlarm."""
        if not self._prefer_tpap_manual_alarm:
            try:
                cam.startManualAlarm()
                try:
                    self._stop_evt.wait(timeout=dur)
                finally:
                    try:
                        cam.stopManualAlarm()
                    except Exception:  # noqa: BLE001
                        pass
                return
            except Exception as e:
                if "-40210" not in str(e) and "METHOD_DO_NOT_EXIST" not in str(e):
                    raise
                self._prefer_tpap_manual_alarm = True

        cam.executeFunction("setManualAlarm", {"action": "start"})
        try:
            self._stop_evt.wait(timeout=dur)
        finally:
            try:
                cam.executeFunction("setManualAlarm", {"action": "stop"})
            except Exception:  # noqa: BLE001
                pass

    def _prompt_url_for(self, kind: str) -> str | None:
        if kind != "prompt" or not self.prompt_audio_url:
            return None
        if self.prompt_wav is None or self.prompt_wav.is_file():
            return self.prompt_audio_url
        return None

    def _emit_event(self, kind: str, hardware_sent: bool, detail: str = "") -> None:
        if self._on_event is None:
            return
        try:
            payload = {
                "type": "camera_chime",
                "chime_kind": kind,
                "hardware_sent": hardware_sent,
                "host": self.host or None,
                "mode": self.mode,
                "detail": detail,
                "ts": time.time(),
            }
            audio_url = self._prompt_url_for(kind)
            if audio_url:
                payload["audio_url"] = audio_url
            self._on_event(payload)
        except Exception:  # noqa: BLE001
            pass

    def _play_sync(self, kind: str, dur: float) -> dict:
        audio_url = self._prompt_url_for(kind)
        if not self.hardware_configured:
            self._emit_event(kind, hardware_sent=False, detail="web_demo_fallback")
            res = {
                "ok": True,
                "hardware_sent": False,
                "fallback": "web_chime",
                "message": (
                    "Chưa cấu hình IP/mật khẩu loa Tapo — đang phát lời nhắc trên Web"
                    if audio_url
                    else "Chưa cấu hình IP/mật khẩu loa Tapo — đang phát chuông giả lập trên Web"
                ),
            }
            if audio_url:
                res["audio_url"] = audio_url
            return res
        now = time.monotonic()
        if now < self._backoff_until:
            rem = round(self._backoff_until - now, 1)
            self._emit_event(kind, hardware_sent=False, detail=f"backoff_{rem}s")
            res = {
                "ok": True,
                "hardware_sent": False,
                "fallback": "web_chime",
                "message": f"Loa camera đang tạm chờ kết nối lại ({rem}s) — phát âm báo dự phòng trên Web",
            }
            if audio_url:
                res["audio_url"] = audio_url
            return res
        try:
            if self.mode == "talk_8800":
                pwd_for_talk = self.cloud_password or self.password
                kwargs: dict = {
                    "kind": kind,
                    "duration_sec": dur,
                    "stop_evt": self._stop_evt,
                }
                try:
                    sig = inspect.signature(self._talk_sender)
                    if "prompt_wav" in sig.parameters or any(
                        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                    ):
                        kwargs["prompt_wav"] = self.prompt_wav
                except (TypeError, ValueError):
                    pass
                self._talk_sender(self.host, pwd_for_talk, **kwargs)
            else:
                cam = self._get_client()
                if self.mode == "play_alarm" and hasattr(cam, "playAlarm"):
                    cam.playAlarm(max(1, int(round(dur))), self.alarm_type, self.alarm_volume)
                elif self.mode == "custom_audio" and hasattr(cam, "testUsrDefAudio"):
                    cam.testUsrDefAudio(self.custom_audio_id, True)
                    self._stop_evt.wait(timeout=dur)
                    cam.testUsrDefAudio(self.custom_audio_id, False)
                else:
                    self._run_manual_alarm_pulse(cam, dur)
            self.last_error = None
            self.last_triggered_at = time.time()
            self.play_count += 1
            self._emit_event(kind, hardware_sent=True, detail="tapo_speaker_ok")
            res = {
                "ok": True,
                "hardware_sent": True,
                "host": self.host,
                "mode": self.mode,
                "duration_sec": dur,
                "message": (
                    f"Đã phát lời nhắc quay mặt trên loa camera Tapo ({self.host})"
                    if audio_url
                    else f"Đã phát chuông trên loa camera Tapo ({self.host})"
                ),
            }
            if audio_url:
                res["audio_url"] = audio_url
            return res
        except Exception as e:  # noqa: BLE001
            self._client = None
            self.last_error = str(e)
            self._backoff_until = time.monotonic() + self.backoff_sec
            log.warning("Khong phat duoc chuong tren camera Tapo (%s): %s -> tam ngung %.0fs",
                        self.host, e, self.backoff_sec)
            self._emit_event(kind, hardware_sent=False, detail=f"tapo_error:{e}")
            res = {
                "ok": True,
                "hardware_sent": False,
                "fallback": "web_chime",
                "error": str(e),
                "message": f"Không gọi được loa Tapo ({self.host}: {e}) — đã phát âm báo dự phòng trên Web",
            }
            if audio_url:
                res["audio_url"] = audio_url
            return res

    def trigger(self, kind: str = "chime", duration_sec: float | None = None, blocking: bool = False) -> dict | bool:
        """Kich hoat tieng chuong ('chime' | 'prompt' | 'spoof'). Mac dinh chay nen, bo qua neu loa dang keu."""
        if self._closed:
            return {"ok": False, "hardware_sent": False, "message": "Notifier closed"} if blocking else False
        with self._lock:
            if self._playing:
                return {"ok": False, "hardware_sent": False, "busy": True, "message": "Loa đang phát chuông"} if blocking else False
            self._playing = True

        dur = float(duration_sec) if duration_sec is not None else (
            self.spoof_duration_sec if kind == "spoof" else self.duration_sec
        )

        if blocking:
            try:
                return self._play_sync(kind, dur)
            finally:
                with self._lock:
                    self._playing = False

        def _worker() -> None:
            try:
                self._play_sync(kind, dur)
            finally:
                with self._lock:
                    self._playing = False

        threading.Thread(target=_worker, daemon=True, name="tapo-chime").start()
        return True

    def notify(self, kind: str = "prompt") -> bool:
        return bool(self.trigger(kind=kind, blocking=False))

    def status(self) -> dict:
        return {
            "hardware_configured": self.hardware_configured,
            "host": self.host or None,
            "mode": self.mode,
            "duration_sec": self.duration_sec,
            "prompt_audio_url": self._prompt_url_for("prompt"),
            "playing": self._playing,
            "play_count": self.play_count,
            "last_triggered_at": self.last_triggered_at,
            "last_error": self.last_error,
        }

    def close(self) -> None:
        self._closed = True
        self._stop_evt.set()


class LookAtCameraPrompter:
    """Theo doi tung track de phat chuong:
      1. Nhac 1 lan khi nguoi dung chi quay mat nghieng (|yaw| > frontal_max_yaw) qua profile_wait_sec ma chua xong liveness.
      2. Canh bao chuong ngay khi phat hien gia mao (spoof=True) neu bat spoof_chime.
    """

    def __init__(
        self,
        notifier,
        frontal_max_yaw: float = 40.0,
        profile_wait_sec: float = 1.2,
        cooldown_sec: float = 6.0,
        spoof_chime: bool = True,
        spoof_cooldown_sec: float = 4.0,
    ):
        self.notifier = notifier
        self.frontal_max_yaw = float(frontal_max_yaw)
        self.profile_wait_sec = max(0.2, float(profile_wait_sec))
        self.cooldown_sec = max(0.5, float(cooldown_sec))
        self.spoof_chime = bool(spoof_chime)
        self.spoof_cooldown_sec = max(0.5, float(spoof_cooldown_sec))

        self._profile_since: dict[int, float] = {}
        self._prompted_tracks: set[int] = set()
        self._spoof_alerted_tracks: set[int] = set()
        self._last_prompt_ts: float = -1e9
        self._last_spoof_ts: float = -1e9

    def _trigger_notifier(self, kind: str) -> bool:
        if hasattr(self.notifier, "trigger"):
            return bool(self.notifier.trigger(kind=kind, blocking=False))
        if hasattr(self.notifier, "notify"):
            return bool(self.notifier.notify())
        if callable(self.notifier):
            self.notifier(kind)
            return True
        return False

    def update(
        self,
        track_id: int,
        now_sec: float,
        yaw_deg: float,
        live_status: str,
        spoof: bool = False,
        event_done: bool = False,
    ) -> str | None:
        tid = int(track_id)
        now = float(now_sec)

        if spoof or live_status == "spoof":
            self._profile_since.pop(tid, None)
            if self.spoof_chime and tid not in self._spoof_alerted_tracks:
                self._spoof_alerted_tracks.add(tid)
                if now - self._last_spoof_ts >= self.spoof_cooldown_sec:
                    if self._trigger_notifier("spoof"):
                        self._last_spoof_ts = now
                        return "spoof"
            return None

        if event_done or live_status == "live":
            self._profile_since.pop(tid, None)
            return None

        if abs(float(yaw_deg)) <= self.frontal_max_yaw:
            # Nguoi dung da quay nhin thang -> reset bo dem cho goc nghieng
            self._profile_since.pop(tid, None)
            return None

        if tid in self._prompted_tracks:
            return None

        t0 = self._profile_since.setdefault(tid, now)
        if now - t0 >= self.profile_wait_sec and (now - self._last_prompt_ts) >= self.cooldown_sec:
            self._prompted_tracks.add(tid)
            if self._trigger_notifier("prompt"):
                self._last_prompt_ts = now
                return "prompt"
        return None

    def forget(self, track_id: int) -> None:
        tid = int(track_id)
        self._profile_since.pop(tid, None)
        self._prompted_tracks.discard(tid)
        self._spoof_alerted_tracks.discard(tid)

    def close(self) -> None:
        if hasattr(self.notifier, "close"):
            self.notifier.close()


def build_chime_notifier(
    cfg: dict,
    on_event: Callable[[dict], None] | None = None,
    camera_source: str | int | None = None,
) -> TapoChimeNotifier:
    """Khoi tao TapoChimeNotifier tu config + bien moi truong CAM_IP / CAM_USER / CAM_PASS + nguon RTSP dang chay."""
    ac = cfg.get("audio_prompt") or {}
    cam_cfg = cfg.get("camera") or {}
    src_str = str(camera_source if camera_source is not None else cam_cfg.get("source", ""))
    rtsp_host, rtsp_user, rtsp_pass = _extract_host_creds_from_rtsp(src_str)

    host = str(ac.get("host") or os.environ.get("TAPO_HOST") or os.environ.get("CAM_IP") or rtsp_host or "").strip()
    cfg_user = str(ac.get("user") or "").strip()
    env_user = str(os.environ.get("TAPO_USER") or os.environ.get("CAM_USER") or "").strip()
    if ac.get("host") or ac.get("password"):
        user = cfg_user or env_user or rtsp_user or "admin"
    else:
        user = env_user or rtsp_user or cfg_user or "admin"
    password = str(ac.get("password") or os.environ.get("TAPO_PASSWORD") or os.environ.get("CAM_PASS") or rtsp_pass or "").strip()
    cloud_password = str(ac.get("cloud_password") or os.environ.get("TAPO_CLOUD_PASSWORD") or "").strip()
    if password == "changeme":
        password = ""

    prompt_wav_cfg = str(ac.get("prompt_wav", "") or "").strip()
    if prompt_wav_cfg:
        pw_path = Path(prompt_wav_cfg)
        if not pw_path.is_absolute():
            root_dir = (cfg.get("_paths") or {}).get("root")
            if root_dir:
                pw_path = Path(root_dir) / pw_path
    else:
        pw_path = DEFAULT_PROMPT_WAV if DEFAULT_PROMPT_WAV.is_file() else None

    return TapoChimeNotifier(
        host=host,
        user=user,
        password=password,
        cloud_password=cloud_password,
        mode=str(ac.get("mode", "manual_alarm")),
        duration_sec=float(ac.get("duration_sec", 1.0)),
        spoof_duration_sec=float(ac.get("spoof_duration_sec", 2.0)),
        alarm_type=int(ac.get("alarm_type", 0)),
        alarm_volume=str(ac.get("alarm_volume", "low")),
        custom_audio_id=int(ac.get("custom_audio_id", 1)),
        backoff_sec=float(ac.get("backoff_sec", 15.0)),
        prompt_wav=pw_path,
        on_event=on_event,
    )


def create_look_prompter(cfg: dict, on_event: Callable[[dict], None] | None = None) -> LookAtCameraPrompter | None:
    """Tao bo nhac chuong tu dong cho Pipeline / Worker khi audio_prompt.enabled=true."""
    ac = cfg.get("audio_prompt") or {}
    if not ac.get("enabled", False):
        return None
    backend = str(ac.get("backend", "web")).strip().lower()
    if backend not in ("tapo", "web"):
        log.warning("audio_prompt.backend=%s khong ho tro -> tat", backend)
        return None
    if backend == "tapo":
        _warn_tapo_once()
    notifier = build_chime_notifier(cfg, on_event=on_event)
    if backend == "tapo" and not notifier.hardware_configured and on_event is None:
        log.warning("audio_prompt.enabled=true nhung thieu host/password cua camera Tapo -> bo qua nhac phan cung")
        return None
    frontal_max = float(cfg.get("recognition", {}).get("frontal_max_yaw", 40.0))
    return LookAtCameraPrompter(
        notifier=notifier,
        frontal_max_yaw=frontal_max,
        profile_wait_sec=float(ac.get("profile_wait_sec", 1.2)),
        cooldown_sec=float(ac.get("cooldown_sec", 6.0)),
        spoof_chime=bool(ac.get("spoof_chime", True)),
        spoof_cooldown_sec=float(ac.get("spoof_cooldown_sec", 4.0)),
    )
