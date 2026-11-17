"""Giao thuc IPC NDJSON bat dong bo & thuc the TrackedObject tong quat (ho tro ca Phase 2 SKU)."""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

VALID_KINDS = {"face", "person", "sku"}
VALID_STATES = {
    "SEEN",
    "COLLECTING",
    "QUARANTINED",
    "IDENTIFIED",
    "LIVE_CONFIRMED",
    "COMMITTED",
    "REJECTED_SPOOF",
    "UNKNOWN",
}
VALID_COLOR_ROLES = {"ok", "checking", "alert", "low_quality"}
VALID_TIERS = {"verified", "profile_verified", "rejected_spoof", "unverified", None}


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


@dataclass
class TrackedObject:
    camera_id: str
    ts_server_ns: int
    track_id: int
    kind: str
    bbox_norm: tuple[float, float, float, float]
    vel_norm: tuple[float, float] = (0.0, 0.0)
    state: str = "SEEN"
    color_role: str = "checking"
    label: str = ""
    verification_tier: str | None = None
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.camera_id:
            raise ValueError("camera_id khong duoc rong")
        self.ts_server_ns = int(self.ts_server_ns)
        self.track_id = int(self.track_id)
        if self.kind not in VALID_KINDS:
            raise ValueError(f"kind khong hop le: {self.kind}")
        if self.state not in VALID_STATES:
            raise ValueError(f"state khong hop le: {self.state}")
        if self.color_role not in VALID_COLOR_ROLES:
            raise ValueError(f"color_role khong hop le: {self.color_role}")
        if self.verification_tier not in VALID_TIERS:
            raise ValueError(f"verification_tier khong hop le: {self.verification_tier}")
        if len(self.bbox_norm) != 4:
            raise ValueError("bbox_norm phai co 4 phan tu [x1, y1, x2, y2]")
        x1, y1, x2, y2 = (_clamp01(v) for v in self.bbox_norm)
        self.bbox_norm = (round(x1, 5), round(y1, 5), round(max(x1, x2), 5), round(max(y1, y2), 5))
        self.vel_norm = (round(float(self.vel_norm[0]), 5), round(float(self.vel_norm[1]), 5))

    def to_dict(self) -> dict:
        return {
            "camera_id": self.camera_id,
            "ts_server_ns": self.ts_server_ns,
            "track_id": self.track_id,
            "kind": self.kind,
            "bbox_norm": list(self.bbox_norm),
            "vel_norm": list(self.vel_norm),
            "state": self.state,
            "color_role": self.color_role,
            "label": self.label,
            "verification_tier": self.verification_tier,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TrackedObject":
        return cls(
            camera_id=str(d["camera_id"]),
            ts_server_ns=int(d.get("ts_server_ns") or time.time_ns()),
            track_id=int(d["track_id"]),
            kind=str(d["kind"]),
            bbox_norm=tuple(d["bbox_norm"]),  # type: ignore[arg-type]
            vel_norm=tuple(d.get("vel_norm", (0.0, 0.0))),  # type: ignore[arg-type]
            state=str(d.get("state", "SEEN")),
            color_role=str(d.get("color_role", "checking")),
            label=str(d.get("label", "")),
            verification_tier=d.get("verification_tier"),
            meta=dict(d.get("meta") or {}),
        )


def encode_ndjson(payload: dict) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def decode_ndjson(line: bytes | str) -> dict:
    raw = line.decode("utf-8") if isinstance(line, (bytes, bytearray)) else line
    return json.loads(raw.strip())


class IPCServer:
    """May chu IPC nhan luong NDJSON tu Perception Process va gui lenh dieu khien nguoc lai."""

    def __init__(self, sock_path: Path | None = None, host: str = "127.0.0.1", port: int = 18091,
                 on_message: Callable[[dict], None] | None = None):
        self.sock_path = sock_path
        self.host = host
        self.port = port
        self.on_message = on_message
        self._server: asyncio.AbstractServer | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self.bound_port: int = port

    @property
    def use_unix(self) -> bool:
        return os.name != "nt" and self.sock_path is not None and hasattr(asyncio, "start_unix_server")

    async def start(self) -> None:
        if self.use_unix and self.sock_path is not None:
            self.sock_path.parent.mkdir(parents=True, exist_ok=True)
            self.sock_path.unlink(missing_ok=True)
            self._server = await asyncio.start_unix_server(self._handle_client, path=str(self.sock_path))
        else:
            self._server = await asyncio.start_server(self._handle_client, host=self.host, port=self.port)
            if self._server.sockets:
                self.bound_port = int(self._server.sockets[0].getsockname()[1])

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        try:
            while not reader.at_eof():
                line = await reader.readline()
                if not line:
                    break
                msg = decode_ndjson(line)
                if self.on_message is not None:
                    res = self.on_message(msg)
                    if asyncio.iscoroutine(res):
                        await res
        except (ConnectionError, json.JSONDecodeError, asyncio.CancelledError):
            pass
        finally:
            self._writers.discard(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    async def send_command(self, cmd: dict) -> int:
        data = encode_ndjson(cmd)
        sent = 0
        for w in list(self._writers):
            try:
                w.write(data)
                await w.drain()
                sent += 1
            except ConnectionError:
                self._writers.discard(w)
        return sent

    async def close(self) -> None:
        for w in list(self._writers):
            w.close()
        self._writers.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self.use_unix and self.sock_path is not None:
            self.sock_path.unlink(missing_ok=True)


class IPCClient:
    """Client ket noi tu Perception Process ve Control Plane."""

    def __init__(self, sock_path: Path | None = None, host: str = "127.0.0.1", port: int = 18091):
        self.sock_path = sock_path
        self.host = host
        self.port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    @property
    def use_unix(self) -> bool:
        return os.name != "nt" and self.sock_path is not None and hasattr(asyncio, "open_unix_connection")

    async def connect(self) -> None:
        if self.use_unix and self.sock_path is not None:
            self._reader, self._writer = await asyncio.open_unix_connection(path=str(self.sock_path))
        else:
            self._reader, self._writer = await asyncio.open_connection(host=self.host, port=self.port)

    async def send(self, payload: dict) -> None:
        if self._writer is None:
            await self.connect()
        assert self._writer is not None
        self._writer.write(encode_ndjson(payload))
        await self._writer.drain()

    async def recv(self) -> dict | None:
        if self._reader is None:
            return None
        line = await self._reader.readline()
        if not line:
            return None
        return decode_ndjson(line)

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            self._writer = None
            self._reader = None
