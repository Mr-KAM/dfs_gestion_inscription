// Admin QR scanner: camera (getUserMedia, needs HTTPS or localhost) or a photo, read with jsQR.
// The raw QR text goes into the form; decoding to a matricule is done by the server.
(() => {
  const form = document.getElementById("scan-form");
  if (!form || typeof jsQR !== "function") return;
  const input = form.querySelector("[name=token]");
  const video = document.getElementById("scan-video");
  const status = document.getElementById("scan-status");
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  let stream = null;

  function read(source, width, height) {
    canvas.width = width;
    canvas.height = height;
    ctx.drawImage(source, 0, 0, width, height);
    const code = jsQR(ctx.getImageData(0, 0, width, height).data, width, height);
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

  function tick() {
    if (!stream) return;
    if (video.readyState >= 2) {
      const text = read(video, video.videoWidth, video.videoHeight);
      if (text) return found(text);
    }
    requestAnimationFrame(tick);
  }

  document.getElementById("scan-camera").addEventListener("click", async () => {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      status.textContent = "Caméra indisponible : ouvrez la page en HTTPS (ou localhost), ou utilisez « Photo du QR code ».";
      return;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
      video.srcObject = stream;
      video.hidden = false;
      await video.play();
      status.textContent = "Visez le QR code…";
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
      img.onload = () => {
        const scale = Math.min(1, 1600 / Math.max(img.width, img.height)); // phone photos are huge
        const text = read(img, Math.round(img.width * scale), Math.round(img.height * scale));
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
