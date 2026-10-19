// Admin QR scanner: camera (getUserMedia, needs HTTPS or localhost) or a photo.
// Native BarcodeDetector when the browser has it (Chrome/Android: fast, robust), jsQR otherwise.
// The raw QR text goes into the form; decryption to a matricule is done by the server.
(() => {
  const form = document.getElementById("scan-form");
  if (!form) return;
  const input = form.querySelector("[name=token]");
  const video = document.getElementById("scan-video");
  const status = document.getElementById("scan-status");
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  const MAX_SIDE = 800; // jsQR: big frames are slow and no more readable
  let stream = null;
  let detector = null;

  async function initDetector() {
    if (!("BarcodeDetector" in window)) return;
    try {
      if ((await BarcodeDetector.getSupportedFormats()).includes("qr_code")) {
        detector = new BarcodeDetector({ formats: ["qr_code"] });
      }
    } catch (e) { detector = null; }
  }
  const ready = initDetector();

  async function read(source, width, height) {
    if (detector) {
      try {
        const codes = await detector.detect(source);
        if (codes.length) return codes[0].rawValue;
      } catch (e) { /* frame not ready: fall through to jsQR */ }
    }
    if (typeof jsQR !== "function" || !width || !height) return null;
    const scale = Math.min(1, MAX_SIDE / Math.max(width, height));
    canvas.width = Math.round(width * scale);
    canvas.height = Math.round(height * scale);
    ctx.drawImage(source, 0, 0, canvas.width, canvas.height);
    const code = jsQR(ctx.getImageData(0, 0, canvas.width, canvas.height).data, canvas.width, canvas.height,
                      { inversionAttempts: "attemptBoth" });
    return code && code.data;
  }

  function stop() {
    if (stream) stream.getTracks().forEach((t) => t.stop());
    stream = null;
    video.hidden = true;
  }

  function found(text) {
    stop();
    status.textContent = "QR code lu, décodage…";
    input.value = text;
    form.requestSubmit();
  }

  async function tick() {
    if (!stream) return;
    if (video.readyState >= 2) {
      const text = await read(video, video.videoWidth, video.videoHeight);
      if (text) return found(text);
    }
    setTimeout(tick, 120); // ~8 tries per second, leaves the phone responsive
  }

  document.getElementById("scan-camera").addEventListener("click", async () => {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      status.textContent = "Caméra indisponible : ouvrez la page en HTTPS (ou localhost), ou utilisez « Photo du QR code ».";
      return;
    }
    try {
      await ready;
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      });
      const [track] = stream.getVideoTracks();
      const caps = track.getCapabilities ? track.getCapabilities() : {};
      if (caps.focusMode && caps.focusMode.includes("continuous")) {
        track.applyConstraints({ advanced: [{ focusMode: "continuous" }] }).catch(() => {});
      }
      video.srcObject = stream;
      video.hidden = false;
      await video.play();
      status.textContent = "Visez le QR code (10 à 20 cm, bien éclairé)…";
      tick();
    } catch (e) {
      stop();
      status.textContent = "Caméra inaccessible (" + e.name + "). Autorisez-la ou utilisez « Photo du QR code ».";
    }
  });

  // FileReader -> data: URL (the CSP allows data: images, not blob:).
  document.getElementById("scan-file").addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const img = new Image();
      img.onload = async () => {
        await ready;
        const text = await read(img, img.naturalWidth, img.naturalHeight);
        if (text) found(text);
        else status.textContent = "Aucun QR code détecté sur la photo. Recadrez et réessayez.";
      };
      img.src = reader.result;
    };
    reader.readAsDataURL(file);
    e.target.value = "";
  });

  window.addEventListener("pagehide", stop);
})();
