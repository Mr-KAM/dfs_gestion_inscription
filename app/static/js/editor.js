// Rich text with TOAST UI Editor (vendor/toastui, MIT). Content is stored as Markdown.
// Editor: <textarea id="x" name="x"> + <div class="js-editor" data-input="x" data-upload="/campus/media" data-csrf="…">.
//   Without JS the plain textarea stays usable. Pasted / inserted images are uploaded, not inlined as base64.
// Viewer: <div class="js-md">markdown</div> (escaped text) -> rendered HTML, sanitized by the editor (DOMPurify).
(() => {
  "use strict";
  const Editor = window.toastui && window.toastui.Editor;
  if (!Editor) return;
  const common = { usageStatistics: false, language: "fr-FR" }; // no Google Analytics ping

  async function upload(el, blob, done) {
    const body = new FormData();
    body.append("file", blob, blob.name || "image.png");
    try {
      const res = await fetch(el.dataset.upload, { method: "POST", body, headers: { "X-CSRFToken": el.dataset.csrf } });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Image refusée.");
      done(data.url, blob.name || "image");
    } catch (e) {
      window.alert(e.message || "Envoi de l'image impossible.");
    }
  }

  document.querySelectorAll(".js-editor").forEach((el) => {
    const input = document.getElementById(el.dataset.input);
    if (!input) return;
    input.hidden = true;
    input.required = false; // the editor owns the value now
    const editor = new Editor({
      ...common,
      el,
      height: el.dataset.height || "380px",
      initialEditType: "wysiwyg",
      previewStyle: "tab",
      initialValue: input.value,
      placeholder: input.placeholder || "",
      hooks: { addImageBlobHook: (blob, done) => { upload(el, blob, done); } },
    });
    input.form.addEventListener("submit", () => { input.value = editor.getMarkdown(); });
  });

  document.querySelectorAll(".js-md").forEach((el) => {
    const text = el.textContent;
    el.textContent = "";
    el.classList.remove("md-fallback");
    Editor.factory({ ...common, el, viewer: true, initialValue: text });
  });
})();
