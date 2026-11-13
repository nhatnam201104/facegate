// Ket noi WebRTC WHEP (MediaMTX) + lop phu HTML5 <canvas> dong bo theo requestVideoFrameCallback (H8, P1)
// Tu dong fallback sang luong MJPEG khi chay demo webcam cuc bo chua bat MediaMTX.
(() => {
  const ROLE_COLORS = {
    ok: "#3cc85a",
    checking: "#ffa500",
    alert: "#e63c3c",
    low_quality: "#aaaaaa"
  };

  class WhepCanvasOverlay {
    constructor() {
      this.pc = null;
      this.whepActive = false;
      this.overlayBuf = [];
      this.lastRecvMs = 0;
      this.offsetMs = 0;
      this.rafId = null;
      this.videoEl = document.getElementById("webrtcVideo");
      this.canvasEl = document.getElementById("overlayCanvas");
      this.mjpegEl = document.getElementById("video");
    }

    pushOverlay(packet) {
      const now = performance.now();
      this.lastRecvMs = now;
      this.overlayBuf.push({ recvMs: now, objects: packet.objects || [], mode: packet.mode || "" });
      if (this.overlayBuf.length > 12) this.overlayBuf.shift();
    }

    setOffsetMs(ms) {
      this.offsetMs = Number(ms || 0);
    }

    async startWhep(whepUrl) {
      if (!this.videoEl || !whepUrl || this.whepActive) return false;
      try {
        const pc = new RTCPeerConnection();
        pc.addTransceiver("video", { direction: "recvonly" });
        pc.ontrack = ev => {
          if (ev.streams && ev.streams[0]) {
            this.videoEl.srcObject = ev.streams[0];
            this.videoEl.hidden = false;
            if (this.mjpegEl) {
              this.mjpegEl.removeAttribute("src");
              this.mjpegEl.hidden = true;
            }
            this.whepActive = true;
            this._startRenderLoop();
          }
        };
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);
        const resp = await fetch(whepUrl, {
          method: "POST",
          headers: { "Content-Type": "application/sdp" },
          body: offer.sdp
        });
        if (!resp.ok) {
          pc.close();
          return false;
        }
        const answerSdp = await resp.text();
        await pc.setRemoteDescription({ type: "answer", sdp: answerSdp });
        this.pc = pc;
        return true;
      } catch (_) {
        return false;
      }
    }

    stop() {
      if (this.pc) {
        try { this.pc.close(); } catch (_) {}
        this.pc = null;
      }
      this.whepActive = false;
      if (this.videoEl) {
        this.videoEl.srcObject = null;
        this.videoEl.hidden = true;
      }
      if (this.mjpegEl) this.mjpegEl.hidden = false;
      if (this.canvasEl) {
        const g = this.canvasEl.getContext("2d");
        if (g) g.clearRect(0, 0, this.canvasEl.width, this.canvasEl.height);
      }
    }

    _pickSnapshot(targetMs) {
      if (!this.overlayBuf.length) return null;
      if (performance.now() - this.lastRecvMs > 400) return null; // H8: an hop sau 400ms mat tin
      let best = this.overlayBuf[this.overlayBuf.length - 1];
      let bestDiff = Math.abs(targetMs - best.recvMs);
      for (const item of this.overlayBuf) {
        const d = Math.abs(targetMs - item.recvMs);
        if (d < bestDiff) { best = item; bestDiff = d; }
      }
      return { snap: best, dtMs: Math.max(-120, Math.min(120, targetMs - best.recvMs)) };
    }

    _drawFrame(targetMs) {
      if (!this.canvasEl) return;
      const host = (!this.videoEl?.hidden && this.videoEl?.videoWidth) ? this.videoEl : this.mjpegEl;
      if (!host) return;
      const rect = host.getBoundingClientRect();
      if (rect.width < 10 || rect.height < 10) return;
      const dpr = window.devicePixelRatio || 1;
      const cw = Math.round(rect.width * dpr), ch = Math.round(rect.height * dpr);
      if (this.canvasEl.width !== cw || this.canvasEl.height !== ch) {
        this.canvasEl.width = cw;
        this.canvasEl.height = ch;
      }
      const ctx2d = this.canvasEl.getContext("2d");
      ctx2d.clearRect(0, 0, cw, ch);
      const picked = this._pickSnapshot(targetMs);
      if (!picked) return;

      const { snap, dtMs } = picked;
      const dtSec = dtMs / 1000.0;
      ctx2d.lineWidth = Math.max(2, Math.round(2 * dpr));
      ctx2d.font = `600 ${Math.round(14 * dpr)}px system-ui, -apple-system, "Segoe UI", sans-serif`;

      for (const o of snap.objects) {
        const bn = o.bbox_norm || [0, 0, 0, 0];
        const vn = o.vel_norm || [0, 0];
        const x1 = Math.max(0, Math.min(1, bn[0] + vn[0] * dtSec)) * cw;
        const y1 = Math.max(0, Math.min(1, bn[1] + vn[1] * dtSec)) * ch;
        const x2 = Math.max(0, Math.min(1, bn[2] + vn[0] * dtSec)) * cw;
        const y2 = Math.max(0, Math.min(1, bn[3] + vn[1] * dtSec)) * ch;
        const col = ROLE_COLORS[o.color_role] || ROLE_COLORS.checking;

        ctx2d.strokeStyle = col;
        ctx2d.strokeRect(x1, y1, Math.max(4, x2 - x1), Math.max(4, y2 - y1));

        if (o.label) {
          const pad = 6 * dpr, lh = 22 * dpr;
          const tw = ctx2d.measureText(o.label).width;
          const ty = Math.max(0, y1 - lh - 2 * dpr);
          ctx2d.fillStyle = col;
          ctx2d.fillRect(x1, ty, tw + pad * 2, lh);
          ctx2d.fillStyle = "#ffffff";
          ctx2d.fillText(o.label, x1 + pad, ty + 15.5 * dpr);
        }
      }
    }

    _startRenderLoop() {
      if (this.videoEl && "requestVideoFrameCallback" in HTMLVideoElement.prototype) {
        const step = (now, meta) => {
          if (!this.whepActive) return;
          const baseMs = (meta && meta.receiveTime) ? meta.receiveTime : now;
          this._drawFrame(baseMs + this.offsetMs);
          this.videoEl.requestVideoFrameCallback(step);
        };
        this.videoEl.requestVideoFrameCallback(step);
      } else {
        const loop = now => {
          if (!this.whepActive && !this.overlayBuf.length) return;
          this._drawFrame(now + this.offsetMs);
          this.rafId = requestAnimationFrame(loop);
        };
        this.rafId = requestAnimationFrame(loop);
      }
    }
  }

  window.fgWhepOverlay = new WhepCanvasOverlay();
})();
