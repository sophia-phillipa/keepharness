// Preload of the main harness window only: the continuation hand-off bridge (WP5) and the interface
// language (P13). It exposes fixed calls and nothing else, never the raw ipcRenderer. Main validates the
// sender, the target and the text, and builds the URL itself.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('keepharnessDesktop', {
  handoffApps: () => ipcRenderer.invoke('keepharness:handoff-apps'),
  openHandoff: (target, text) => ipcRenderer.invoke('keepharness:handoff-open', { target, text }),
  setLanguage: (tag) => ipcRenderer.invoke('keepharness:set-language', tag),
});
