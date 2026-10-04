(() => {
  "use strict";

  const STORAGE_KEY = "keepharness-tour-seen";
  const RELEASE = "0.15.0";
  // D19: five steps, each for something a new user cannot guess. The rest is discoverable in place.
  const steps = [
    { target: "composer", title: "Write and route work", text: "Write a request, type / to choose agents, skills and commands, or @@ to call one of your agents. The chips under the box attach files and Space pages and choose plugins; the pickers set access, model and effort." },
    { target: "rail-areas", title: "Space, Scheduled and Customize", text: "The rail opens Space (pages for each project), Scheduled (recurring tasks), Runs and Customize (your agents and skills). Hover or focus a rail button to read its name." },
    { target: "sidebar-state-groups", title: "Conversations by state", text: "Project chats stay inside their project; other conversations are listed under Chats, with what needs you first. A yellow dot means a conversation needs your answer; a spinner, running; a ring, queued; red, failed; blue, an unread answer." },
    { target: "run-console-tabs", title: "Live status and the Run console", text: "The status strip keeps running, queued and needs-you counts visible; select it or press Ctrl/⌘+J to open the Run console. Pipeline shows the current plan, Timeline and Logs show execution detail, and Runs lists work across the project.", reveal: "console" },
    { target: "settings-admin", title: "Settings and help", text: "Settings holds Appearance, Customize, Models, Usage, Connect a client and About. On the computer that runs KeepHarness, its System section shows providers, operations, run history and catalogs. KeepHarness was called Tail Harness before 0.15. Choose Take the tour in Settings to replay this guide." },
  ];

  let root = null;
  let index = 0;
  let previousFocus = null;
  let target = null;
  let frame = 0;
  let startTimer = 0;
  let backgroundState = [];

  function storedSeen() {
    try { return localStorage.getItem(STORAGE_KEY) === RELEASE; }
    catch (_) { return false; }
  }

  function rememberSeen() {
    try { localStorage.setItem(STORAGE_KEY, RELEASE); }
    catch (_) { /* Storage can be unavailable in restricted browsers. */ }
  }

  function visible(node) {
    if (!node || node.hidden) return false;
    const style = getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0 &&
      rect.right > 0 && rect.bottom > 0 && rect.left < innerWidth && rect.top < innerHeight;
  }

  function reveal(step) {
    if (step.reveal === "console") {
      const drawer = document.querySelector("#run-console");
      if (drawer?.hidden) document.querySelector("#run-status-toggle")?.click();
    }
  }

  function findTarget(step) {
    reveal(step);
    const node = document.querySelector(`[data-tour="${step.target}"]`);
    return visible(node) ? node : null;
  }

  function build() {
    root = document.createElement("div");
    root.id = "tour-root";
    root.innerHTML = `
      <div class="tour-guard" aria-hidden="true"></div>
      <div class="tour-spotlight" aria-hidden="true"></div>
      <div class="tour-pointer" aria-hidden="true"></div>
      <section id="tour-card" role="dialog" aria-modal="true" aria-labelledby="tour-title" aria-describedby="tour-description">
        <div id="tour-counter" aria-live="polite"></div>
        <h2 id="tour-title"></h2>
        <p id="tour-description"></p>
        <div class="tour-actions">
          <button id="tour-skip" type="button">Skip tour</button>
          <span class="tour-spacer"></span>
          <button id="tour-back" type="button">Back</button>
          <button id="tour-next" type="button">Next</button>
        </div>
      </section>`;
    document.body.append(root);
    backgroundState = [...document.body.children]
      .filter(node => node !== root && node instanceof HTMLElement)
      .map(node => ({ node, inert: node.inert, ariaHidden: node.getAttribute("aria-hidden") }));
    for (const item of backgroundState) {
      item.node.inert = true;
      item.node.setAttribute("aria-hidden", "true");
    }
    root.querySelector("#tour-skip").addEventListener("click", stop);
    root.querySelector("#tour-back").addEventListener("click", () => move(-1));
    root.querySelector("#tour-next").addEventListener("click", () => move(1));
    root.querySelector(".tour-guard").addEventListener("click", event => event.preventDefault());
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("focusin", containFocus, true);
    window.addEventListener("resize", schedulePosition);
    window.addEventListener("scroll", schedulePosition, true);
  }

  function show(nextIndex, direction) {
    let candidate = nextIndex;
    while (candidate >= 0 && candidate < steps.length) {
      const found = findTarget(steps[candidate]);
      if (found) {
        index = candidate;
        target = found;
        const step = steps[index];
        root.querySelector("#tour-counter").textContent = `${index + 1} of ${steps.length}`;
        root.querySelector("#tour-title").textContent = step.title;
        root.querySelector("#tour-description").textContent = step.text;
        root.querySelector("#tour-back").disabled = index === 0;
        root.querySelector("#tour-next").textContent = index === steps.length - 1 ? "Finish" : "Next";
        target.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "auto" });
        schedulePosition();
        root.querySelector("#tour-next").focus({ preventScroll: true });
        return true;
      }
      candidate += direction;
    }
    if (direction < 0) return show(0, 1);
    stop();
    return false;
  }

  function move(direction) {
    if (!root) return;
    show(index + direction, direction);
  }

  function schedulePosition() {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(position);
  }

  function position() {
    if (!root) return;
    if (!visible(target)) { show(index, 1); return; }
    const gap = 14;
    const edge = 12;
    const rect = { ...target.getBoundingClientRect().toJSON() };
    for (let ancestor = target.parentElement; ancestor; ancestor = ancestor.parentElement) {
      const style = getComputedStyle(ancestor), clip = ancestor.getBoundingClientRect();
      if (/(auto|scroll|hidden|clip)/.test(style.overflowY)) {
        rect.top = Math.max(rect.top, clip.top);
        rect.bottom = Math.max(rect.top, Math.min(rect.bottom, clip.bottom));
      }
      if (/(auto|scroll|hidden|clip)/.test(style.overflowX)) {
        rect.left = Math.max(rect.left, clip.left);
        rect.right = Math.max(rect.left, Math.min(rect.right, clip.right));
      }
    }
    rect.width = rect.right - rect.left;
    rect.height = rect.bottom - rect.top;
    const spotlight = root.querySelector(".tour-spotlight");
    const card = root.querySelector("#tour-card");
    const pointer = root.querySelector(".tour-pointer");
    const spotLeft = Math.max(edge, Math.min(innerWidth - edge, rect.left - 6));
    const spotTop = Math.max(edge, Math.min(innerHeight - edge, rect.top - 6));
    const spotRight = Math.max(spotLeft, Math.min(innerWidth - edge, rect.right + 6));
    const spotBottom = Math.max(spotTop, Math.min(innerHeight - edge, rect.bottom + 6));
    spotlight.style.cssText = `left:${spotLeft}px;top:${spotTop}px;width:${spotRight - spotLeft}px;height:${spotBottom - spotTop}px`;
    card.style.left = "0";
    card.style.top = "0";
    const box = card.getBoundingClientRect();
    const below = rect.bottom + gap + box.height <= innerHeight - edge;
    const above = rect.top - gap - box.height >= edge;
    const side = below ? "below" : above ? "above" : rect.right + gap + box.width <= innerWidth - edge ? "right" : "left";
    let left = rect.left + rect.width / 2 - box.width / 2;
    let top = rect.bottom + gap;
    if (side === "above") top = rect.top - gap - box.height;
    if (side === "right") { left = rect.right + gap; top = rect.top + rect.height / 2 - box.height / 2; }
    if (side === "left") { left = rect.left - gap - box.width; top = rect.top + rect.height / 2 - box.height / 2; }
    left = Math.max(edge, Math.min(innerWidth - box.width - edge, left));
    top = Math.max(edge, Math.min(innerHeight - box.height - edge, top));
    card.style.left = `${left}px`;
    card.style.top = `${top}px`;
    root.dataset.side = side;
    const focused = document.activeElement;
    if (card.contains(focused)) {
      const action = focused.getBoundingClientRect(), viewport = card.getBoundingClientRect();
      if (action.bottom > viewport.bottom) card.scrollTop += action.bottom - viewport.bottom + 8;
      else if (action.top < viewport.top) card.scrollTop -= viewport.top - action.top + 8;
    }
    const px = side === "right" ? rect.right + 4 : side === "left" ? rect.left - 12 : Math.max(12, Math.min(innerWidth - 20, rect.left + rect.width / 2 - 4));
    const py = side === "below" ? rect.bottom + 4 : side === "above" ? rect.top - 12 : Math.max(12, Math.min(innerHeight - 20, rect.top + rect.height / 2 - 4));
    pointer.style.cssText = `left:${px}px;top:${py}px`;
  }

  function focusable() {
    return [...root.querySelectorAll("button:not([disabled])")];
  }

  function consume(event) {
    event.preventDefault();
    event.stopPropagation();
  }

  function containFocus(event) {
    if (!root || root.contains(event.target)) return;
    event.stopPropagation();
    queueMicrotask(() => root?.querySelector("#tour-next")?.focus({ preventScroll: true }));
  }

  function onKey(event) {
    if (!root) return;
    if (event.key === "Escape") { consume(event); stop(); return; }
    if (event.key === "ArrowRight") { consume(event); move(1); return; }
    if (event.key === "ArrowLeft") { consume(event); move(-1); return; }
    if (event.key !== "Tab") return;
    const items = focusable();
    const first = items[0], last = items[items.length - 1];
    if (!root.contains(document.activeElement)) { consume(event); (event.shiftKey ? last : first).focus(); }
    else if (event.shiftKey && document.activeElement === first) { consume(event); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { consume(event); first.focus(); }
  }

  function start(returnFocus = null) {
    if (root) stop(false);
    const active = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    previousFocus = returnFocus || (active?.closest("dialog:not([open])") ? null : active);
    build();
    index = 0;
    show(0, 1);
  }

  function stop(markSeen = true) {
    if (!root) return;
    if (markSeen) rememberSeen();
    cancelAnimationFrame(frame);
    document.removeEventListener("keydown", onKey, true);
    document.removeEventListener("focusin", containFocus, true);
    window.removeEventListener("resize", schedulePosition);
    window.removeEventListener("scroll", schedulePosition, true);
    for (const item of backgroundState) {
      if (!item.node.isConnected) continue;
      item.node.inert = item.inert;
      if (item.ariaHidden == null) item.node.removeAttribute("aria-hidden");
      else item.node.setAttribute("aria-hidden", item.ariaHidden);
    }
    backgroundState = [];
    root.remove();
    root = null;
    target = null;
    const restore = previousFocus !== document.body && visible(previousFocus) && !previousFocus.disabled && !previousFocus.closest("dialog:not([open]), [inert]")
      ? previousFocus : document.querySelector("#prompt:not(:disabled)") || document.querySelector("#models-retry");
    restore?.focus({ preventScroll: true });
    window.syncWorkspaceModal?.();
  }

  function autoStart() {
    clearTimeout(startTimer);
    const ready = document.body.dataset.connectionReady === "true";
    if (!ready) {
      if (root) stop(false);
      return;
    }
    if (document.querySelector("dialog[open]")) {
      if (root) stop(false);
      return;
    }
    if (storedSeen() || root) return;
    startTimer = setTimeout(() => {
      if (document.body.dataset.connectionReady === "true" && !root && !storedSeen() && !document.querySelector("dialog[open]")) start();
    }, 250);
  }

  document.addEventListener("click", event => {
    const trigger = event.target.closest('[data-tour-action="start"]');
    if (trigger) {
      event.preventDefault();
      const dialog = trigger.closest("dialog[open]");
      const opener = dialog?.id ? document.querySelector(`[aria-controls="${dialog.id}"]`) : null;
      dialog?.close();
      start(opener);
    }
  });
  const readiness = new MutationObserver(autoStart);
  readiness.observe(document.body, { attributes: true, subtree: true, attributeFilter: ["data-connection-ready", "open"] });
  autoStart();
  window.keepHarnessTour = { start, stop, isActive: () => !!root, storageKey: STORAGE_KEY };
})();
