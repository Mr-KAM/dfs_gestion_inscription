// Timers display server-computed remaining seconds; the server stays the source of truth.
// Elements: <span class="js-timer" data-remaining="3540" data-status="running">.
(function () {
  "use strict";

  function fmt(s) {
    s = Math.max(0, Math.floor(s));
    const p = (n) => String(n).padStart(2, "0");
    return p(Math.floor(s / 3600)) + ":" + p(Math.floor((s % 3600) / 60)) + ":" + p(s % 60);
  }

  function initTimers(root) {
    root.querySelectorAll(".js-timer").forEach((el) => {
      el.dataset.loadedAt = String(performance.now());
    });
    tick();
  }

  function tick() {
    document.querySelectorAll(".js-timer").forEach((el) => {
      const base = Number(el.dataset.remaining);
      const elapsed = el.dataset.status === "running" ? (performance.now() - Number(el.dataset.loadedAt)) / 1000 : 0;
      const left = base - elapsed;
      el.textContent = fmt(left);
      const expired = left <= 0 && (el.dataset.status === "running" || el.dataset.status === "paused");
      el.classList.toggle("is-expired", expired);
      el.classList.toggle("is-paused", el.dataset.status === "paused");
      const wrap = el.closest(".js-timer-wrap");
      if (wrap) wrap.classList.toggle("is-expired-wrap", expired);
    });
  }

  // Polling: <div data-poll="/url?partial=1" data-poll-interval="5000"> swaps its innerHTML.
  function initPolling() {
    document.querySelectorAll("[data-poll]").forEach((el) => {
      const every = Number(el.dataset.pollInterval || 5000);
      setInterval(async () => {
        if (document.hidden) return;
        try {
          const res = await fetch(el.dataset.poll, { headers: { "X-Requested-With": "fetch" }, cache: "no-store" });
          if (res.redirected || res.status === 401) { window.location.reload(); return; }
          if (!res.ok) return;
          el.innerHTML = await res.text();
          initTimers(el);
        } catch (e) { /* network blip: keep last state, retry next tick */ }
      }, every);
    });
  }

  // Start-test form: reload free workstations for the chosen room, gate the submit button.
  function initStartForm() {
    const form = document.getElementById("start-test-form");
    if (!form) return;
    const room = form.querySelector("[name=room_id]");
    const ws = form.querySelector("[name=workstation_id]");
    const btn = form.querySelector("button[type=submit]");
    const hint = form.querySelector(".js-ws-hint");
    const update = () => { btn.disabled = !(room.value && ws.value); };
    async function loadFree() {
      ws.innerHTML = '<option value="">—</option>';
      if (!room.value) { update(); return; }
      const res = await fetch(form.dataset.freeUrl.replace("/0/", "/" + room.value + "/"), { cache: "no-store" });
      const list = res.ok ? await res.json() : [];
      list.forEach((w) => ws.add(new Option(w.name, w.id)));
      hint.textContent = list.length ? list.length + " ordinateur(s) libre(s)" : "Aucun ordinateur disponible dans cette salle.";
      update();
    }
    room.addEventListener("change", loadFree);
    ws.addEventListener("change", update);
    loadFree();
  }

  // Confirmation for destructive buttons: <form data-confirm="Message">.
  document.addEventListener("submit", (e) => {
    const msg = (e.submitter && e.submitter.dataset.confirm) || e.target.dataset.confirm;
    if (msg && !window.confirm(msg)) e.preventDefault();
  });

  // Mobile sidebar toggle.
  document.addEventListener("click", (e) => {
    if (e.target.closest(".js-sidebar-toggle")) document.querySelector(".sidebar")?.classList.toggle("open");
  });

  // "Apply to all duplicates" select on the import preview.
  document.addEventListener("change", (e) => {
    if (e.target.matches(".js-apply-all") && e.target.value) {
      document.querySelectorAll(".js-dup-action").forEach((s) => { s.value = e.target.value; });
    }
  });

  document.addEventListener("DOMContentLoaded", () => {
    initTimers(document);
    setInterval(tick, 1000);
    initPolling();
    initStartForm();
  });
})();
