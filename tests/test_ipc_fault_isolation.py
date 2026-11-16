from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from app.ipc import IPCServer


def test_killing_child_worker_process_never_crashes_control_plane(tmp_path: Path):
    """Mo phong Perception Process chay doc lap gui heartbeat qua IPC Socket roi bi SIGKILL dot ngot (M1, H15)."""
    sock = tmp_path / "perception.sock"
    messages: list[dict] = []

    async def _scenario():
        srv = IPCServer(sock_path=sock, port=0, on_message=lambda m: messages.append(m))
        await srv.start()
        code = "\n".join([
            "import asyncio, sys",
            "from pathlib import Path",
            f"sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r})",
            "from app.ipc import IPCClient",
            "async def run():",
            f"    c = IPCClient(sock_path=Path({str(sock)!r}), port={srv.bound_port})",
            "    await c.connect()",
            "    await c.send({'type': 'heartbeat', 'camera_id': 'gate-1', 'stream1_connected': True})",
            "    await asyncio.sleep(30)",
            "asyncio.run(run())",
        ])
        proc = subprocess.Popen([sys.executable, "-c", code])
        try:
            for _ in range(50):
                if messages:
                    break
                await asyncio.sleep(0.05)
            assert len(messages) == 1 and messages[0]["type"] == "heartbeat"

            # Kill nong tien trinh con bang SIGKILL (giong segfault / OOM trong C++ ONNX/FFmpeg)
            if os.name == "nt":
                proc.kill()
            else:
                os.kill(proc.pid, signal.SIGKILL)
            proc.wait(timeout=3.0)

            # Control Plane IPCServer van tiep tuc song khoe va san sang nhan worker moi!
            await asyncio.sleep(0.1)
            sent_after_kill = await srv.send_command({"cmd": "reload_gallery", "version": 2})
            assert sent_after_kill == 0
        finally:
            if proc.poll() is None:
                proc.kill()
            await srv.close()

    asyncio.run(_scenario())
