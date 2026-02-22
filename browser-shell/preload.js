'use strict';
/**
 * preload.js — runs in the chrome renderer (browser.html) with access to
 * Node/Electron APIs, but the renderer itself is isolated.
 * 
 * We expose a typed `window.electronAPI` surface via contextBridge so the
 * renderer can call main-process IPC without having nodeIntegration access.
 */
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  // ── Tabs ──────────────────────────────────────────────────────────────────
  newTab:        (url)  => ipcRenderer.invoke('new-tab', url),
  closeTab:      (id)   => ipcRenderer.invoke('close-tab', id),
  switchTab:     (id)   => ipcRenderer.invoke('switch-tab', id),
  getTabs:       ()     => ipcRenderer.invoke('get-tabs'),
  duplicateTab:  (id)            => ipcRenderer.invoke('duplicate-tab', id),
  /** Show native OS context menu for a tab (renders above BrowserViews). */
  showTabCtx:    (tabId, tabUrl) => ipcRenderer.invoke('show-tab-ctx', { tabId, tabUrl }),

  // ── Navigation ────────────────────────────────────────────────────────────
  navigate:      (url)  => ipcRenderer.invoke('navigate', url),
  goBack:        ()     => ipcRenderer.invoke('go-back'),
  goForward:     ()     => ipcRenderer.invoke('go-forward'),
  reload:        ()     => ipcRenderer.invoke('reload'),
  stop:          ()     => ipcRenderer.invoke('stop'),
  goHome:        ()     => ipcRenderer.invoke('go-home'),
  getActiveUrl:  ()     => ipcRenderer.invoke('get-active-url'),

  // ── Gateway ───────────────────────────────────────────────────────────────
  getGatewayStatus: () => ipcRenderer.invoke('get-gateway-status'),  /** Returns the auto-minted admin bearer token for this session. */
  getAdminToken:    () => ipcRenderer.invoke('get-admin-token'),
  // ── System ────────────────────────────────────────────────────────────────
  openExternal:  (url)  => ipcRenderer.invoke('open-external', url),

  // ── Tab snapshot & script injection (addon / agent capabilities) ──────────
  /** Capture the active tab's full HTML. Resolves { url, title, html } or null. */
  getTabSnapshot: ()       => ipcRenderer.invoke('get-tab-snapshot'),
  /**
   * On-demand agent snapshot: captures tab HTML + optional screenshot, posts
   * to POST /tab/preview for sanitization + consent logging, and resolves with
   * { ok, status, url, title, screenshotDataUrl, result } where `result` is
   * the gateway's sanitized preview JSON.
   */
  captureTab: () => ipcRenderer.invoke('capture-tab'),
  /** Execute arbitrary JS in the active tab BrowserView. Use with care. */
  injectScript:   (code)   => ipcRenderer.invoke('inject-script', code),
  /** Toggle DevTools for the active page (same as Ctrl+Shift+I in Chrome). */
  toggleDevTools:       ()       => ipcRenderer.invoke('toggle-devtools'),
  /** Toggle DevTools for the Intelli chrome shell window itself. */
  toggleChromeDevTools: ()       => ipcRenderer.invoke('toggle-chrome-devtools'),

  // ── Bookmarks ─────────────────────────────────────────────────────────────
  bookmarksList:   ()              => ipcRenderer.invoke('bookmarks-list'),
  bookmarksAdd:    (url, title, favicon) => ipcRenderer.invoke('bookmarks-add', { url, title, favicon }),
  bookmarksRemove: (url)           => ipcRenderer.invoke('bookmarks-remove', url),
  bookmarksHas:    (url)           => ipcRenderer.invoke('bookmarks-has', url),

  // ── History ───────────────────────────────────────────────────────────────
  historyList:   (limit) => ipcRenderer.invoke('history-list', limit),
  historyClear:  ()      => ipcRenderer.invoke('history-clear'),
  historyRemove: (url)   => ipcRenderer.invoke('history-remove', url),

  // ── Zoom ──────────────────────────────────────────────────────────────────
  zoomIn:    () => ipcRenderer.invoke('zoom-in'),
  zoomOut:   () => ipcRenderer.invoke('zoom-out'),
  zoomReset: () => ipcRenderer.invoke('zoom-reset'),
  zoomGet:   () => ipcRenderer.invoke('zoom-get'),

  // ── Downloads ─────────────────────────────────────────────────────────────
  openDownloadsFolder: () => ipcRenderer.invoke('open-downloads-folder'),

  // ── App menu (three-dot popup) ────────────────────────────────────────────
  showAppMenu: () => ipcRenderer.invoke('show-app-menu'),

  // ── Panel visibility (tells main to shrink the BrowserView) ──────────────
  setPanelVisible: (isOpen) => ipcRenderer.invoke('panel-visible', isOpen),

  // ── Events from main → renderer (panels + misc) ───────────────────────────
  onOpenPanel:           (cb) => ipcRenderer.on('open-panel',            (_, name) => cb(name)),
  onRequestBookmarkToggle:(cb) => ipcRenderer.on('request-bookmark-toggle', () => cb()),
  onDownloadDone:        (cb) => ipcRenderer.on('download-done',         (_, d)    => cb(d)),
  onZoomChanged:         (cb) => ipcRenderer.on('zoom-changed',          ()        => cb()),
  // ── Sidebar ────────────────────────────────────────────────────────────
  /** Toggle the admin-hub sidebar panel. Resolves with { open: boolean }. */
  toggleSidebar: ()     => ipcRenderer.invoke('toggle-sidebar'),

  // ── Window controls ──────────────────────────────────────────────────────────
  minimizeWindow:   ()    => ipcRenderer.invoke('win-minimize'),
  toggleMaximize:   ()    => ipcRenderer.invoke('win-maximize'),
  closeWindow:      ()    => ipcRenderer.invoke('win-close'),
  isMaximized:      ()    => ipcRenderer.invoke('win-is-maximized'),
  /** Calls cb(isMaximized: boolean) whenever the window is maximized/restored. */
  onMaximizeChange: (cb)  => ipcRenderer.on('win-maximize-changed', (_, v) => cb(v)),
  // ── Events from main → renderer ──────────────────────────────────────────
  onTabTitleUpdated:   (cb) => ipcRenderer.on('tab-title-updated',   (_, d) => cb(d)),
  onTabFaviconUpdated: (cb) => ipcRenderer.on('tab-favicon-updated', (_, d) => cb(d)),
  onUrlChanged:        (cb) => ipcRenderer.on('url-changed',         (_, d) => cb(d)),
  onNavState:          (cb) => ipcRenderer.on('nav-state',           (_, d) => cb(d)),
  /** Called whenever tabs are created, closed, or switched. cb receives the full tab array. */
  onTabsUpdated:       (cb) => ipcRenderer.on('tabs-updated',        (_, tabs) => cb(tabs)),

  // ── Auto-updater notifications ────────────────────────────────────────────
  /** Called with { version, releaseDate } when a new release is available. */
  onUpdateAvailable:   (cb) => ipcRenderer.on('update-available',  (_, info) => cb(info)),
  /** Called with no arguments once the update binary has been downloaded. */
  onUpdateDownloaded:  (cb) => ipcRenderer.on('update-downloaded', ()        => cb()),
  /** Quit and install the downloaded update immediately. */
  installUpdate:       ()   => ipcRenderer.invoke('install-update'),

  // Remove all listeners for a channel (cleanup)
  removeAllListeners: (channel) => ipcRenderer.removeAllListeners(channel),
});
