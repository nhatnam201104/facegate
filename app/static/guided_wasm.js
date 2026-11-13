// Huong dan goc dau soi guong tuc thoi 0ms tre mang tren trinh duyet bang MediaPipe Face Landmarker WASM cuc bo (Apache-2.0 - H10, T027)
// Luong anh mau cuoi cung van do Server chup truc tiep tu luong chuan camera cong (gate1_main) va HR duyet.
(() => {
  class ClientMirrorGuide {
    constructor() {
      this.landmarker = null;
      this.ready = false;
      this.running = false;
    }

    async tryInitLocalWasm() {
      if (this.ready) return true;
      try {
        // Nap cuc bo tu /static/vendor/mediapipe/ neu da dat goi WASM cho moi truong VLAN khong Internet
        const mod = await import("/static/vendor/vision_bundle.mjs");
        const resolver = await mod.FilesetResolver.forVisionTasks("/static/vendor/wasm");
        this.landmarker = await mod.FaceLandmarker.createFromOptions(resolver, {
          baseOptions: { modelAssetPath: "/static/vendor/face_landmarker.task" },
          runningMode: "VIDEO",
          numFaces: 2,
          outputFacialTransformationMatrixes: true
        });
        this.ready = true;
        return true;
      } catch (_) {
        // Khi chua dat file WASM cuc bo: tu dong dung che do phan hoi goc tu Server ma khong bao loi
        this.ready = false;
        return false;
      }
    }

    estimatePoseFromMatrix(m) {
      // m: ma tran 4x4 theo thu tu cot
      const r02 = m[8], r12 = m[9], r22 = m[10];
      const yaw = Math.atan2(r02, r22) * (180 / Math.PI);
      const pitch = Math.asin(Math.max(-1, Math.min(1, -r12))) * (180 / Math.PI);
      return { yaw, pitch };
    }

    attachToPreview(videoOrImgEl, onPoseHint) {
      if (!this.ready || !this.landmarker || !(videoOrImgEl instanceof HTMLVideoElement)) return;
      this.running = true;
      const tick = () => {
        if (!this.running) return;
        if (videoOrImgEl.readyState >= 2) {
          const res = this.landmarker.detectForVideo(videoOrImgEl, performance.now());
          if (res.facialTransformationMatrixes?.length === 1) {
            const pose = this.estimatePoseFromMatrix(res.facialTransformationMatrixes[0].data);
            onPoseHint?.(pose);
          }
        }
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    }

    stop() {
      this.running = false;
    }
  }

  window.fgMirrorGuide = new ClientMirrorGuide();
})();
