"""Trang thai dung chung giua cac router (khoi tao trong lifespan cua app.main)."""
from __future__ import annotations

import asyncio


class Broadcaster:
    def __init__(self):
        self.loop: asyncio.AbstractEventLoop | None = None
        self.clients: set[asyncio.Queue] = set()

    def __call__(self, msg: dict) -> None:  # goi tu thread bat ky
        if self.loop is None:
            return
        for q in list(self.clients):
            self.loop.call_soon_threadsafe(q.put_nowait, msg)


class Ctx:
    pass


ctx = Ctx()
bc = Broadcaster()
