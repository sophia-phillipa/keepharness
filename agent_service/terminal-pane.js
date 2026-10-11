/* Workspace terminal pane (D-055 P7): one shell session per project, streamed over SSE, one-line input. */
(() => {
  "use strict";
  const MAX_LINES = 5000;
  const MIN_HEIGHT = 120;
  const KEYBOARD_STEP = 24;
  const KEYS = { ctrlC: "\x03", ctrlD: "\x04", tab: "\t", up: "\x1b[A" };
  const JSON_HEADERS = { "Content-Type": "application/json" };
  const byId = (id) => document.getElementById(id);
  const pane = byId("terminal-pane");
  const toggle = byId("terminal-toggle");
  const settingsToggle = byId("terminal-settings-toggle");
  const output = byId("terminal-output");
  const input = byId("terminal-input");
  const cwdLabel = byId("terminal-cwd");
  const notice = byId("terminal-notice");
  const handle = byId("terminal-resize-handle");
  const narrow = matchMedia("(max-width: 999px)");
  const state = {
    open: null, // null until the first setOpen, so the startup default is saved like the editor's
    project: "",
    session: "",
    source: null,
    generation: 0,
    lines: [],
    partial: "",
    carriage: false,
    masked: false,
    frame: 0,
    sending: Promise.resolve(),
  };

  function say(message) {
    notice.textContent = message;
    notice.hidden = !message;
  }

  function clearOutput() {
    state.lines = [];
    state.partial = "";
    state.carriage = false;
    state.masked = false;
    input.type = "text";
    output.textContent = "";
  }

  // Bare CR rewrites the current line; a CR at the end of a chunk waits for the next chunk's LF.
  function appendText(text) {
    if (state.carriage) {
      state.carriage = false;
      if (text.startsWith("\n")) {
        endLine();
        text = text.slice(1);
      } else {
        state.partial = "";
      }
    }
    if (text.endsWith("\r")) {
      text = text.slice(0, -1);
      state.carriage = true;
    }
    text.split(/(\r\n|\n|\r)/).forEach((part, index) => {
      if (index % 2 === 0) state.partial += part;
      else if (part === "\r") state.partial = "";
      else endLine();
    });
  }

  function endLine() {
    state.lines.push(state.partial);
    state.partial = "";
    if (state.lines.length > MAX_LINES) state.lines.splice(0, state.lines.length - MAX_LINES);
  }

  function render() {
    if (state.frame) return;
    state.frame = requestAnimationFrame(() => {
      state.frame = 0;
      const stick = output.scrollHeight - output.scrollTop - output.clientHeight < 8;
      const lines = state.partial ? [...state.lines, state.partial] : state.lines;
      output.textContent = lines.join("\n");
      if (stick) output.scrollTop = output.scrollHeight;
    });
  }

  // echo false means the shell is reading a hidden value: the input is masked and nothing typed is kept.
  function receive(event) {
    const masked = event.echo === false;
    if (masked !== state.masked) {
      state.masked = masked;
      input.type = masked ? "password" : "text";
      input.placeholder = masked ? "Hidden input" : "";
    }
    // An empty text event only carries the echo flag (see TerminalSession._publish_echo).
    if (event.text) appendText(event.text);
    render();
  }

  function detach() {
    state.generation++;
    if (state.source) state.source.close();
    state.source = null;
  }

  function connect(generation, session) {
    const source = new EventSource("/v1/terminal/sessions/" + encodeURIComponent(session) + "/stream");
    state.source = source;
    source.addEventListener("output", (event) => {
      if (generation === state.generation) receive(JSON.parse(event.data));
    });
    source.addEventListener("exit", (event) => {
      if (generation !== state.generation) return;
      detach();
      say("The shell exited with code " + JSON.parse(event.data).code + ". Reopen the terminal to start a new one.");
    });
    source.addEventListener("revoked", (event) => {
      if (generation !== state.generation) return;
      detach();
      say("Terminal access ended (" + JSON.parse(event.data).code + ").");
    });
  }

  // Starts the project's session, or reattaches to it (200), then replays its output from the stream.
  async function attach() {
    detach();
    clearOutput();
    say("");
    const project = byId("project").value;
    state.project = project;
    if (!project) return say("Choose a project to open its terminal.");
    const generation = state.generation;
    try {
      const data = await (
        await api("/v1/terminal/sessions", {
          method: "POST",
          headers: JSON_HEADERS,
          body: JSON.stringify({ project_id: project }),
        })
      ).json();
      if (generation !== state.generation) return;
      state.session = data.session_id;
      cwdLabel.textContent = data.cwd_label;
      connect(generation, data.session_id);
    } catch (error) {
      if (generation === state.generation) say(error.message);
    }
  }

  function send(data) {
    const session = state.session;
    if (!session) {
      say("Open the terminal to start a session.");
      return;
    }
    state.sending = state.sending
      .then(() =>
        api("/v1/terminal/sessions/" + encodeURIComponent(session) + "/input", {
          method: "POST",
          headers: JSON_HEADERS,
          body: JSON.stringify({ data }),
        }),
      )
      .catch((error) => say(error.message));
  }

  function setOpen(on, save = true) {
    if (on === state.open) return;
    state.open = on;
    pane.hidden = !on;
    toggle.setAttribute("aria-pressed", String(on));
    settingsToggle.checked = on;
    if (save) prefs.set("terminal_open", on);
    if (on) {
      window.runConsole?.closeForPanel();
      if (narrow.matches && !byId("editor-pane").hidden) byId("editor-toggle").click();
      attach();
    } else {
      detach();
    }
  }

  function sizePane(height, save) {
    const limit = Math.max(MIN_HEIGHT, innerHeight - 160);
    pane.style.height = Math.round(Math.min(Math.max(height, MIN_HEIGHT), limit)) + "px";
    if (save) prefs.set("terminal_height", Math.round(pane.getBoundingClientRect().height));
  }

  toggle.onclick = () => setOpen(!state.open);
  settingsToggle.onchange = () => setOpen(settingsToggle.checked);
  byId("terminal-end-session").onclick = async () => {
    const session = state.session;
    if (!session) return;
    detach();
    state.session = "";
    clearOutput();
    try {
      await api("/v1/terminal/sessions/" + encodeURIComponent(session), { method: "DELETE" });
      say("Session ended. Close and reopen the terminal to start a new one.");
    } catch (error) {
      say(error.message);
    }
  };
  byId("terminal-send-ctrl-c").onclick = () => send(KEYS.ctrlC);
  byId("terminal-send-ctrl-d").onclick = () => send(KEYS.ctrlD);
  byId("terminal-send-tab").onclick = () => send(KEYS.tab);
  byId("terminal-send-up").onclick = () => send(KEYS.up);

  input.addEventListener("keydown", (event) => {
    if (event.isComposing) return;
    const control = event.ctrlKey && !event.altKey && !event.metaKey;
    if (event.key === "Enter") {
      event.preventDefault();
      const line = input.value + "\n";
      input.value = "";
      send(line);
    } else if (control && event.key.toLowerCase() === "c" && input.selectionStart === input.selectionEnd) {
      event.preventDefault();
      send(KEYS.ctrlC);
    } else if (control && event.key.toLowerCase() === "d") {
      event.preventDefault();
      send(KEYS.ctrlD);
    } else if (event.key === "Tab" && !event.shiftKey) {
      event.preventDefault();
      send(KEYS.tab);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      send(KEYS.up);
    }
  });

  // Control+` (Command+` on Mac) toggles the pane, also while the terminal input has focus.
  document.addEventListener("keydown", (event) => {
    if (
      event.code !== "Backquote" ||
      !(event.ctrlKey || event.metaKey) ||
      event.altKey ||
      event.shiftKey ||
      foreignModalOpen()
    )
      return;
    event.preventDefault();
    setOpen(!state.open);
  });

  handle.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    event.preventDefault();
    const step = event.key === "ArrowUp" ? KEYBOARD_STEP : -KEYBOARD_STEP;
    sizePane(pane.getBoundingClientRect().height + step, true);
  });
  let drag = null;
  handle.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    drag = { y: event.clientY, height: pane.getBoundingClientRect().height };
    handle.setPointerCapture(event.pointerId);
  });
  handle.addEventListener("pointermove", (event) => {
    if (drag) sizePane(drag.height + drag.y - event.clientY, false);
  });
  const endDrag = () => {
    if (!drag) return;
    drag = null;
    sizePane(pane.getBoundingClientRect().height, true);
  };
  handle.addEventListener("pointerup", endDrag);
  handle.addEventListener("pointercancel", endDrag);

  const savedHeight = Number(prefs.get("terminal_height", 0));
  if (savedHeight) sizePane(savedHeight, false);
  // Apply the saved choice without writing it back: a missing key already reads as closed.
  setOpen(prefs.get("terminal_open", false) === true, false);

  // Called by ui.js when the active project changes (conversation switch); the pane reattaches to its session.
  window.terminalPane = {
    closeForPanel() {
      if (narrow.matches) setOpen(false);
    },
    projectChanged() {
      if (state.open && byId("project").value !== state.project) attach();
    },
  };
})();
