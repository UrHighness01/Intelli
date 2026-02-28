/**
 * tab-preload.js — Minimal preload injected into every browser tab BrowserView.
 *
 * Exposes window.electronAPI.addonFetch so injected addon scripts can make
 * network requests (e.g. to localhost Ollama) from the Electron main process,
 * completely bypassing the page's Content-Security-Policy.
 */
'use strict';
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  /**
   * Make an HTTP/HTTPS request from the main process, bypassing page CSP.
   * Returns a Promise<{ ok: boolean, status: number, text: string }>.
   */
  addonFetch: (url, options) => ipcRenderer.invoke('addon-fetch', url, options),
});
