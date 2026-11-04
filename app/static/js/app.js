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

  // Checkboxes that save on change: <input class="js-autosubmit"> inside its own form.
  document.addEventListener("change", (e) => {
    if (e.target.classList.contains("js-autosubmit")) e.target.form.requestSubmit();
  });

  // Copy to clipboard: <button class="js-copy" data-copy="text">. navigator.clipboard needs HTTPS or
  // localhost; the textarea fallback covers plain-HTTP access on the local network.
  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    if (!ok) throw new Error("copy failed");
  }

  document.addEventListener("click", async (e) => {
    const btn = e.target.closest(".js-copy");
    if (!btn) return;
    // Local message on the candidate page; shared screen-reader region (base.html) in table rows.
    const status = btn.parentElement.querySelector(".js-copy-status") || document.getElementById("copy-status");
    const icon = btn.querySelector("i");
    try {
      await copyText(btn.dataset.copy);
      icon.className = "bi bi-clipboard-check";
      btn.classList.replace("btn-outline-primary", "btn-outline-success");
      if (status) status.textContent = "Copié dans le presse-papier.";
    } catch (err) {
      if (status) status.textContent = "Copie impossible : sélectionnez le texte manuellement.";
    }
    setTimeout(() => {
      icon.className = "bi bi-clipboard";
      btn.classList.replace("btn-outline-success", "btn-outline-primary");
      if (status) status.textContent = "";
    }, 2500);
  });

  // Side menu. Desktop: the header button collapses it to an icon rail, remembered in a cookie that
  // base.html reads (no flash on load). Mobile / tablet: a drawer above the bottom navigation.
  const desktop = window.matchMedia("(min-width: 992px)");

  document.addEventListener("click", (e) => {
    const sidebar = document.querySelector(".sidebar");
    if (!sidebar) return;
    const toggle = e.target.closest(".js-sidebar-toggle");
    const isOpen = sidebar.classList.contains("open");
    if (toggle && desktop.matches) setCollapsed(!document.body.classList.contains("sidebar-collapsed"));
    else if (toggle) setDrawer(sidebar, !isOpen); // header button or "Plus" opens / closes the drawer
    else if (isOpen && !e.target.closest(".sidebar")) setDrawer(sidebar, false); // tap outside closes it
  });

  function setDrawer(sidebar, open) {
    sidebar.classList.toggle("open", open);
    document.querySelectorAll(".js-sidebar-toggle").forEach((b) => b.setAttribute("aria-expanded", String(open)));
  }

  function setCollapsed(collapsed) {
    document.body.classList.toggle("sidebar-collapsed", collapsed);
    document.cookie = "sidebar=" + (collapsed ? "collapsed" : "expanded") + "; path=/; max-age=31536000; SameSite=Lax";
    syncRail();
  }

  // Rail: labels are visually hidden, so each icon gets a tooltip (Bootstrap, appended to body: not clipped).
  function syncRail() {
    const rail = desktop.matches && document.body.classList.contains("sidebar-collapsed");
    document.querySelectorAll(".js-sidebar-toggle.sidebar-toggle").forEach((b) => {
      if (desktop.matches) b.setAttribute("aria-expanded", String(!rail));
    });
    if (!window.bootstrap) return;
    document.querySelectorAll(".sidebar .nav-link").forEach((link) => {
      const label = link.querySelector(".nav-label");
      if (!label) return;
      const tip = bootstrap.Tooltip.getOrCreateInstance(link, { title: label.textContent.trim(), placement: "right",
                                                               trigger: "hover focus", container: "body" });
      if (rail) tip.enable();
      else { tip.hide(); tip.disable(); }
    });
  }
  desktop.addEventListener("change", syncRail);

  document.addEventListener("keydown", (e) => {
    const sidebar = document.querySelector(".sidebar.open");
    if (e.key === "Escape" && sidebar) setDrawer(sidebar, false);
  });

  // <select data-autosubmit>: submit its form on change (no inline handlers: blocked by the CSP).
  document.addEventListener("change", (e) => {
    if (e.target.matches("[data-autosubmit]")) e.target.form.submit();
  });

  // "Apply to all duplicates" select on the import preview.
  document.addEventListener("change", (e) => {
    if (e.target.matches(".js-apply-all") && e.target.value) {
      document.querySelectorAll(".js-dup-action").forEach((s) => { s.value = e.target.value; });
    }
  });

  // Student photo zoom: <a data-zoom href="photo"> opens it full size in a <dialog>; click or Esc closes.
  document.addEventListener("click", (e) => {
    const link = e.target.closest("a[data-zoom]");
    if (!link) return;
    e.preventDefault();
    const dialog = document.createElement("dialog");
    dialog.className = "photo-zoom";
    const img = document.createElement("img");
    img.src = link.href;
    img.alt = (link.querySelector("img") || {}).alt || "Photo";
    const close = document.createElement("button");
    close.type = "button";
    close.className = "btn-close btn-close-white";
    close.setAttribute("aria-label", "Fermer");
    dialog.append(close, img);
    dialog.addEventListener("click", () => dialog.close());
    dialog.addEventListener("close", () => dialog.remove());
    document.body.append(dialog);
    dialog.showModal();
  });

  // Print pages (PDF export): <button data-print>, <body data-autoprint> opens the dialog once loaded.
  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-print]")) window.print();
  });
  window.addEventListener("load", () => {
    if (document.body.hasAttribute("data-autoprint")) window.print();
  });

  // Tabs (Bootstrap data API) follow the URL hash, so a link or a redirect can open one: a redirect without a
  // fragment keeps the request's, so a form posted to action="…#admission" comes back on that tab.
  function initTabs() {
    if (!window.bootstrap) return;
    const open = (hash) => {
      const trigger = Array.from(document.querySelectorAll('[data-bs-toggle="tab"]')).find((t) => t.dataset.bsTarget === hash);
      if (trigger) bootstrap.Tab.getOrCreateInstance(trigger).show();
      return Boolean(trigger);
    };
    const pane = location.hash && open(location.hash) && document.getElementById(location.hash.slice(1));
    // Back from a form on this tab: the browser scrolls to the pane, so its result message moves there too.
    if (pane) pane.prepend(...document.querySelectorAll(".main > .alert"));
    window.addEventListener("hashchange", () => open(location.hash));
    document.addEventListener("shown.bs.tab", (e) => {
      const hash = e.target.dataset.bsTarget;
      if (hash && hash !== location.hash) history.replaceState(null, "", hash);
    });
  }

  // Defense sheet: live points while a jury member rates. Mirrors grid_points / DefenseScore.section
  // (models/defense.py): points = weight x cote / 4, N/A and unrated left out; the server recomputes on save.
  // A cote 1 or 4 needs an observed fact (same rule as the server, checked before submitting).
  function initScoring() {
    const fmt = (n) => String(Math.round(n * 10) / 10);
    document.querySelectorAll("form[data-scoring]").forEach((form) => {
      const update = () => {
        const sections = {};
        form.querySelectorAll("[data-criterion]").forEach((row) => {
          const weight = Number(row.dataset.weight);
          const checked = row.querySelector("input[type=radio]:checked");
          const rating = checked ? checked.value : "";
          const points = ["1", "2", "3", "4"].includes(rating) ? weight * Number(rating) / 4 : null;
          const out = row.querySelector("[data-points]");
          if (out) out.textContent = points === null ? (rating === "na" ? "Sans objet" : "—") : fmt(points) + " pts";
          const fact = row.querySelector("textarea");
          const needsFact = rating === "1" || rating === "4";
          if (fact) fact.required = needsFact;
          row.classList.toggle("needs-fact", Boolean(needsFact && fact && !fact.value.trim()));
          const s = (sections[row.dataset.section] ||= { points: 0, weight: 0 });
          if (points !== null) { s.points += points; s.weight += weight; }
        });
        const total = { points: 0, weight: 0 };
        Object.entries(sections).forEach(([key, s]) => {
          form.querySelectorAll("[data-subtotal]").forEach((out) => {
            if (out.dataset.subtotal === key) out.textContent = fmt(s.points) + " / " + s.weight;
          });
          total.points += s.points;
          total.weight += s.weight;
        });
        form.querySelectorAll("[data-total]").forEach((out) => {
          out.textContent = total.weight ? fmt(total.points) + " / " + total.weight + " pts" : "—";
        });
      };
      form.addEventListener("change", update);
      form.addEventListener("input", update);
      update();
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    initTimers(document);
    setInterval(tick, 1000);
    initPolling();
    initStartForm();
    syncRail();
    initTabs();
    initScoring();
  });
})();
