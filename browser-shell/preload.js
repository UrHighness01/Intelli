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
  reorderTab:    (dragId, targetId) => ipcRenderer.invoke('reorder-tab', dragId, targetId),
  duplicateTab:  (id)            => ipcRenderer.invoke('duplicate-tab', id),
  /** Swap left/right sides of the active split pair (drag-to-swap in tab bar). */
  swapSplitSides: (tabId) => ipcRenderer.invoke('swap-split-sides', tabId),
  /** Show native OS context menu for a tab (renders above BrowserViews). */
  showTabCtx:    (tabId, tabUrl, groups) => ipcRenderer.invoke('show-tab-ctx', { tabId, tabUrl, groups: groups || [] }),
  /** Show native OS context menu for a tab group chip (renders above BrowserViews). */
  showGroupCtx:     (groupId) => ipcRenderer.invoke('show-group-ctx', { groupId }),
  /** IPC from main: an action was selected in the group chip context menu. */
  onGroupCtxAction: (cb) => ipcRenderer.on('group-ctx-action', (_, data) => cb(data)),

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
  /** Save a tab-group as a single group-bookmark (colored dot in bar). */
  bookmarkAddGroup: (data)          => ipcRenderer.invoke('bookmarks-add-group', data),
  /** Show / hide the bookmarks bar (persisted to settings). */
  setBookmarksBarVisible: (v)      => ipcRenderer.invoke('set-bookmarks-bar-visible', v),
  /** IPC from main: full bookmark list changed — re-render bar. */
  onBookmarksChanged:     (cb) => ipcRenderer.on('bookmarks-changed',  (_, bm) => cb(bm)),
  /** IPC from main: bookmarks bar visibility changed. */
  onBookmarksBarState:    (cb) => ipcRenderer.on('bookmarks-bar-state', (_, v)  => cb(v)),

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

  // ── Chrome Extensions ─────────────────────────────────────────────────────
  /** Returns [{id, name, version, path, enabled, liveLoaded}] */
  extList:         ()              => ipcRenderer.invoke('ext-list'),
  /** Opens folder picker, loads unpacked extension. Resolves {ok, id?, name?, reason?} */
  extLoadUnpacked: ()              => ipcRenderer.invoke('ext-load-unpacked'),
  /** Opens CRX file picker, extracts + loads. Resolves {ok, id?, name?, reason?} */
  extLoadCrx:      ()              => ipcRenderer.invoke('ext-load-crx'),
  /** Removes extension by id from session + index. */
  extRemove:       (id)            => ipcRenderer.invoke('ext-remove', id),
  /** Enable/disable extension. `enabled` is boolean. */
  extToggle:       (id, enabled)   => ipcRenderer.invoke('ext-toggle', { extId: id, enabled }),
  /** Rename the display name of an extension (persisted to index). */
  extRename:       (id, name)      => ipcRenderer.invoke('ext-rename', { extId: id, name }),

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
  onMaximizeChange: (cb)  => ipcRenderer.on('win-maximize-changed', (_, v) => cb(v)),  /** Called when the user picks "Mettre en onglet inactif" from the tab context menu.
   *  cb receives { id, url, title, favicon } */
  onGroupTab: (cb) => ipcRenderer.on('group-tab', (_, d) => cb(d)),
  /** Called when the user picks a chrome group action from the native context menu.
   *  cb receives { action, tabId, groupId? } */
  onTabGroupAction: (cb) => ipcRenderer.on('tab-group-action', (_, d) => cb(d)),
  /** Show a native context menu listing inactive tabs. `tabs` is the _groupedTabs array. */
  showInactiveTabsMenu: (tabs) => ipcRenderer.invoke('show-inactive-tabs-menu', tabs),
  /** Show the custom popup listing inactive tabs with hover previews. */
  showInactiveTabsPopup: (payload) => ipcRenderer.invoke('show-inactive-tabs-popup', payload),
  /** Returns the stored page screenshot (data:URL) for a tab, or null. */
  getTabPreview: (tabId) => ipcRenderer.invoke('get-tab-preview', tabId),
  getSettings:  ()    => ipcRenderer.invoke('get-settings'),
  saveSettings: (s)   => ipcRenderer.invoke('save-settings', s),
  /** Show the floating hover preview window for a tab. */
  showTabPreview: (data) => ipcRenderer.invoke('show-tab-preview', data),
  /** Hide/close the floating hover preview window. */
  hideTabPreview: ()     => ipcRenderer.send('hide-tab-preview'),
  /** Called when the user clicks a tab in the inactive-tabs native menu. cb receives the tab id. */
  onRestoreInactiveTab: (cb)   => ipcRenderer.on('restore-inactive-tab', (_, id) => cb(id)),
  /** Called when the user clicks ✕ Retirer next to a tab. cb receives the tab id. */
  onRemoveInactiveTab:  (cb)   => ipcRenderer.on('remove-inactive-tab',  (_, id) => cb(id)),
  /** Called when the user clicks 🗑 Clear. */
  onClearInactiveTabs:   (cb) => ipcRenderer.on('clear-inactive-tabs',        (_) => cb()),
  /** Called when the user clicks ↩ Tout restaurer. */
  onRestoreAllInactiveTabs: (cb) => ipcRenderer.on('restore-all-inactive-tabs', (_) => cb()),
  // ── Events from main → renderer ──────────────────────────────────────────
  onTabTitleUpdated:   (cb) => ipcRenderer.on('tab-title-updated',   (_, d) => cb(d)),
  onTabFaviconUpdated: (cb) => ipcRenderer.on('tab-favicon-updated', (_, d) => cb(d)),
  onUrlChanged:        (cb) => ipcRenderer.on('url-changed',         (_, d) => cb(d)),
  onNavState:          (cb) => ipcRenderer.on('nav-state',           (_, d) => cb(d)),
  /** Called whenever tabs are created, closed, or switched. cb receives the full tab array. */
  onTabsUpdated:       (cb) => ipcRenderer.on('tabs-updated',        (_, tabs) => cb(tabs)),
  /** Close the split-view mode. */
  closeSplit:          ()   => ipcRenderer.send('close-split'),
  /** Called when split mode changes. cb receives { splitTabId } or null. */
  onSplitChanged:      (cb) => ipcRenderer.on('split-changed',       (_, d) => cb(d)),
  /** Called when a tab’s audio mute state changes. cb receives { id, muted }. */
  onTabMuted:          (cb) => ipcRenderer.on('tab-muted',           (_, d) => cb(d)),

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
