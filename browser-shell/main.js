'use strict';

// ─── Imports ─────────────────────────────────────────────────────────────────
const {
  app,
  BrowserWindow,
  BrowserView,
  ipcMain,
  Menu,
  clipboard,
  session,
  shell,
  dialog,
  nativeTheme,
} = require('electron');
const path  = require('path');
const fs    = require('fs');
const { spawn, execFile } = require('child_process');
const http  = require('http');
const os    = require('os');

// ─── Constants ────────────────────────────────────────────────────────────────
const GATEWAY_PORT      = 8080;
const GATEWAY_HOST      = '127.0.0.1';
const GATEWAY_ORIGIN    = `http://${GATEWAY_HOST}:${GATEWAY_PORT}`;
const GATEWAY_HEALTH    = `${GATEWAY_ORIGIN}/health`;
const HOME_URL          = `${GATEWAY_ORIGIN}/ui/`;
const CHROME_HEIGHT     = 88;   // px — tab bar (36) + address bar (52)
const SIDEBAR_WIDTH     = 340;  // px — admin hub sidebar panel
const PANEL_WIDTH       = 360;  // px — right-side overlay panels (bookmarks, history, etc.)
const GATEWAY_READY_MS  = 15000;
const HEALTH_POLL_MS    = 400;
const NEW_TAB_URL       = `${GATEWAY_ORIGIN}/ui/`;
const SETUP_URL         = `${GATEWAY_ORIGIN}/ui/setup.html`;

// ─── Gateway process handle ───────────────────────────────────────────────────
let gatewayProcess  = null;
let gatewayReady    = false;

// ─── Bootstrap secret & admin token ──────────────────────────────────────────
// A random secret is generated at startup and passed to the gateway via env.
// After the gateway is ready, the shell uses it to mint an admin bearer token
// which is then automatically injected into all admin UI pages.
const { randomBytes } = require('crypto');
const BOOTSTRAP_SECRET = randomBytes(24).toString('hex');
let adminToken  = null;   // populated in app.whenReady after gateway start
let needsSetup  = false;  // true on first launch when no admin account exists

// ─── Auto-updater (graceful degradation — not available in dev without publish) ─
let autoUpdater = null;
try {
  autoUpdater = require('electron-updater').autoUpdater;
  autoUpdater.autoDownload          = true;
  autoUpdater.autoInstallOnAppQuit  = true;
  autoUpdater.logger                = null;  // suppress verbose file logging
} catch { /* dev env or stripped build without electron-updater */ }

// ─── Tab state ────────────────────────────────────────────────────────────────
// tabs[id] = { view: BrowserView, id: number }
let tabs        = {};
let activeTabId = null;
let nextTabId   = 1;

// ─── User-data paths ──────────────────────────────────────────────────────────
function userDataFile(name) {
  return path.join(app.getPath('userData'), name);
}

// ─── Bookmarks ────────────────────────────────────────────────────────────────
// [{ id, title, url, favicon, addedAt }]
function loadBookmarks() {
  try { return JSON.parse(fs.readFileSync(userDataFile('bookmarks.json'), 'utf8')); }
  catch { return []; }
}
function saveBookmarks(bm) {
  fs.writeFileSync(userDataFile('bookmarks.json'), JSON.stringify(bm, null, 2));
}

// ─── History ─────────────────────────────────────────────────────────────────
// [{ title, url, visitedAt }]  — newest first, capped at 2000
const HISTORY_MAX = 2000;
function loadHistory() {
  try { return JSON.parse(fs.readFileSync(userDataFile('history.json'), 'utf8')); }
  catch { return []; }
}
function saveHistory(h) {
  fs.writeFileSync(userDataFile('history.json'), JSON.stringify(h, null, 2));
}
function pushHistory(url, title) {
  if (!url || url.startsWith('about:') || url === 'data:') return;
  let h = loadHistory();
  // Move to top if URL already exists (within last 50)
  const recent = h.slice(0, 50);
  const dupIdx = recent.findIndex(e => e.url === url);
  if (dupIdx !== -1) h.splice(dupIdx, 1);
  h.unshift({ url, title: title || url, visitedAt: new Date().toISOString() });
  if (h.length > HISTORY_MAX) h = h.slice(0, HISTORY_MAX);
  saveHistory(h);
}

// ─── Downloads (metadata only) ────────────────────────────────────────────────
// Electron handles the actual download; we just track it in the session.
function setupDownloads(ses) {
  ses.on('will-download', (_, item) => {
    item.on('done', (__, state) => {
      if (mainWin && !mainWin.isDestroyed()) {
        mainWin.webContents.send('download-done', {
          filename: item.getFilename(),
          path: item.getSavePath(),
          state,
        });
      }
    });
  });
}

/** The one BrowserWindow that forms the browser chrome. */
let mainWin = null;

// ─── Sidebar state ────────────────────────────────────────────────────────────
/** Lazily created BrowserView for the admin-hub sidebar panel. */
let sidebarView = null;
let sidebarOpen = false;

// ─── Panel state (right-side overlays in chrome renderer) ─────────────────────
// When a panel is open we must shrink the active BrowserView so the panel
// is not hidden underneath it (BrowserViews render above the chrome DOM).
let panelVisible = false;

// ─────────────────────────────────────────────────────────────────────────────
// Gateway discovery & launch
// ─────────────────────────────────────────────────────────────────────────────

function findGatewayDir() {
  // Packaged: gateway is extracted to resources/agent-gateway/
  const packed = path.join(process.resourcesPath, 'agent-gateway');
  if (fs.existsSync(packed)) return packed;
  // Development: sibling directory
  const dev = path.join(__dirname, '..', 'agent-gateway');
  if (fs.existsSync(dev)) return dev;
  return null;
}

function findPython(gatewayDir) {
  const candidates = [];

  if (process.platform === 'win32') {
    candidates.push(
      path.join(gatewayDir, '.venv', 'Scripts', 'python.exe'),
      path.join(gatewayDir, 'venv',  'Scripts', 'python.exe'),
      path.join(__dirname, '..', '.venv', 'Scripts', 'python.exe'),
      'python',
    );
  } else {
    candidates.push(
      path.join(gatewayDir, '.venv', 'bin', 'python3'),
      path.join(gatewayDir, 'venv',  'bin', 'python3'),
      path.join(__dirname, '..', '.venv', 'bin', 'python3'),
      'python3',
      'python',
    );
  }

  for (const c of candidates) {
    if (c.includes(path.sep) && fs.existsSync(c)) return c;
    if (!c.includes(path.sep)) return c;  // rely on PATH
  }
  return 'python';
}

/**
 * Spawn the gateway and resolve when /health returns 200,
 * or reject after GATEWAY_READY_MS ms.
 */
function startGateway() {
  return new Promise((resolve, reject) => {
    const gwDir = findGatewayDir();
    if (!gwDir) {
      reject(new Error('Could not locate agent-gateway directory.'));
      return;
    }

    const python = findPython(gwDir);
    const args   = [
      '-m', 'uvicorn', 'app:app',
      '--host', GATEWAY_HOST,
      '--port', String(GATEWAY_PORT),
      '--log-level', 'warning',
    ];

    console.log(`[gateway] spawning: ${python} ${args.join(' ')} in ${gwDir}`);

    gatewayProcess = spawn(python, args, {
      cwd:   gwDir,
      stdio: ['ignore', 'pipe', 'pipe'],
      detached: false,
      windowsHide: true,  // hide console window on Windows
      env: { ...process.env, INTELLI_BOOTSTRAP_SECRET: BOOTSTRAP_SECRET },
    });

    gatewayProcess.stdout.on('data', d => console.log('[gw]', d.toString().trim()));
    gatewayProcess.stderr.on('data', d => console.error('[gw]', d.toString().trim()));
    gatewayProcess.on('error', err => {
      console.error('[gateway] spawn error:', err);
      reject(err);
    });
    gatewayProcess.on('exit', (code, sig) => {
      console.log(`[gateway] exited (code=${code}, sig=${sig})`);
      gatewayProcess = null;
      gatewayReady   = false;
    });

    // Poll /health until it responds 200 or we time out
    const deadline = Date.now() + GATEWAY_READY_MS;
    function poll() {
      if (Date.now() > deadline) {
        reject(new Error(`Gateway did not become healthy within ${GATEWAY_READY_MS}ms`));
        return;
      }
      http.get(GATEWAY_HEALTH, res => {
        if (res.statusCode === 200) {
          console.log('[gateway] ready ✓');
          gatewayReady = true;
          resolve();
        } else {
          setTimeout(poll, HEALTH_POLL_MS);
        }
        res.resume();
      }).on('error', () => setTimeout(poll, HEALTH_POLL_MS));
    }
    setTimeout(poll, 600);  // give uvicorn a moment before first probe
  });
}

function stopGateway() {
  if (!gatewayProcess) return;
  console.log('[gateway] shutting down…');
  try {
    if (process.platform === 'win32') {
      // On Windows, kill the entire process tree
      spawn('taskkill', ['/pid', String(gatewayProcess.pid), '/f', '/t'], {
        windowsHide: true,
      });
    } else {
      gatewayProcess.kill('SIGTERM');
    }
  } catch (e) {
    console.error('[gateway] kill error:', e);
  }
  gatewayProcess = null;
  gatewayReady   = false;
}

// ─────────────────────────────────────────────────────────────────────────────
// Tab management helpers
// ─────────────────────────────────────────────────────────────────────────────

function tabBounds(win) {
  const [w, h] = win.getContentSize();
  const sideW  = sidebarOpen  ? SIDEBAR_WIDTH : 0;
  const panelW = panelVisible ? PANEL_WIDTH   : 0;
  return { x: 0, y: CHROME_HEIGHT, width: Math.max(0, w - sideW - panelW), height: Math.max(0, h - CHROME_HEIGHT) };
}

/** Bounds for the admin-hub sidebar BrowserView when open. */
function sidebarBounds(win) {
  const [w, h] = win.getContentSize();
  return { x: Math.max(0, w - SIDEBAR_WIDTH), y: CHROME_HEIGHT, width: SIDEBAR_WIDTH, height: Math.max(0, h - CHROME_HEIGHT) };
}

/**
 * Navigation guard — synchronous private-IP / dangerous-scheme check.
 * Returns null if the URL is allowed, or a reason string if it should be blocked.
 */
function _isBlockedURL(urlStr) {
  try {
    const u = new URL(urlStr);
    const proto = u.protocol;

    // Always allow the gateway admin UI
    if (urlStr.startsWith(GATEWAY_ORIGIN + '/')) return null;

    // Block inherently dangerous schemes
    if (['javascript:', 'data:', 'vbscript:'].includes(proto)) {
      return `Blocked scheme: ${proto.slice(0, -1)}`;
    }

    // Only apply host checks for http/https/ftp
    if (!['http:', 'https:', 'ftp:'].includes(proto)) return null;

    const h = u.hostname.toLowerCase();

    // Loopback hostnames (but allow 127.0.0.1:8080 = gateway)
    if (h === 'localhost' || h === '::1' || h === '0.0.0.0') {
      if (h === 'localhost' && String(u.port) === String(GATEWAY_PORT)) return null;
      return `Loopback host blocked: ${h}`;
    }

    // Private IPv4 ranges
    const ipv4 = h.match(/^(\d+)\.(\d+)\.(\d+)\.(\d+)$/);
    if (ipv4) {
      const [, a, b] = ipv4.map(Number);
      if (a === 127) {
        if (String(u.port) === String(GATEWAY_PORT)) return null; // gateway
        return `Loopback IP blocked: ${h}`;
      }
      if (a === 10)                              return `Private IP (10.x) blocked`;
      if (a === 172 && b >= 16 && b <= 31)      return `Private IP (172.16-31.x) blocked`;
      if (a === 192 && b === 168)               return `Private IP (192.168.x) blocked`;
      if (a === 169 && b === 254)               return `Link-local IP blocked`;
    }

    // .internal / .local / .lan hostnames
    if (h.endsWith('.internal') || h.endsWith('.local') || h.endsWith('.lan')) {
      return `Internal hostname blocked: ${h}`;
    }

    return null; // allowed
  } catch {
    return null; // malformed URL — let the browser handle it
  }
}

/**
 * Create a new tab and attach it to the window.
 * Returns the tab id.
 */
function createTab(url = NEW_TAB_URL, win = mainWin) {
  const id   = nextTabId++;
  const view = new BrowserView({
    webPreferences: {
      nodeIntegration:              false,
      contextIsolation:             true,
      sandbox:                      true,
      webviewTag:                   false,
    },
  });

  // Do NOT addBrowserView here — switchTab will add it so we never get a
  // brief blank-view flash over the current tab while the new one loads.
  view.setAutoResize({ width: true, height: true });
  view.webContents.loadURL(url);

  // Forward page events to the chrome renderer
  view.webContents.on('page-title-updated', (_, title) => {
    notifyChrome('tab-title-updated', { id, title });
  });

  // Navigation guard: block private IPs and dangerous schemes synchronously.
  view.webContents.on('will-navigate', (event, navUrl) => {
    const reason = _isBlockedURL(navUrl);
    if (reason) {
      event.preventDefault();
      const warningURL = `${GATEWAY_ORIGIN}/ui/index.html#blocked`;
      view.webContents.loadURL(warningURL);
      notifyChrome('nav-blocked', { id, url: navUrl, reason });
      console.warn(`[NavGuard] Blocked navigation to ${navUrl}: ${reason}`);
    }
  });

  view.webContents.on('did-navigate', (_, navUrl) => {
    if (activeTabId === id) notifyChrome('url-changed', { id, url: navUrl });
    // Record visit in history (skip internal gateway UI)
    if (!navUrl.startsWith(GATEWAY_ORIGIN)) {
      pushHistory(navUrl, view.webContents.getTitle());
    }
  });
  view.webContents.on('did-navigate-in-page', (_, navUrl) => {
    if (activeTabId === id) notifyChrome('url-changed', { id, url: navUrl });
  });
  view.webContents.on('page-favicon-updated', (_, favicons) => {
    notifyChrome('tab-favicon-updated', { id, favicon: favicons[0] || null });
  });
  // Right-click context menu on the page — adds Chrome-style Inspect option
  // (HTML menus beneath BrowserViews are inaccessible, so we must use native).
  view.webContents.on('context-menu', (_, params) => {
    const items = [];

    // Back / Forward / Reload
    if (view.webContents.canGoBack())
      items.push({ label: 'Back',    click: () => view.webContents.goBack() });
    if (view.webContents.canGoForward())
      items.push({ label: 'Forward', click: () => view.webContents.goForward() });
    if (items.length)
      items.push({ label: 'Reload',  click: () => view.webContents.reload() });
    if (items.length)
      items.push({ type: 'separator' });

    // Link actions
    if (params.linkURL) {
      items.push({ label: 'Open Link in New Tab',  click: () => createTab(params.linkURL) });
      items.push({ label: 'Copy Link Address',     click: () => clipboard.writeText(params.linkURL) });
      items.push({ type: 'separator' });
    }

    // Text selection
    if (params.selectionText) {
      items.push({ role: 'copy' });
      items.push({ type: 'separator' });
    }

    // Inspect Element — always present
    items.push({
      label: 'Inspect',
      click: () => view.webContents.inspectElement(params.x, params.y),
    });

    Menu.buildFromTemplate(items).popup({ window: mainWin });
  });
  // Auto-inject the admin token into gateway UI pages so users don’t have
  // to paste a bearer token manually on every admin page load.
  view.webContents.on('did-finish-load', () => {
    if (!adminToken) return;
    const url = view.webContents.getURL();
    if (!url.startsWith(GATEWAY_ORIGIN + '/ui/')) return;
    view.webContents.executeJavaScript(`
      (function(tok) {
        try { localStorage.setItem('gw_token', tok); } catch(e) {}
        var inp = document.getElementById('token-input');
        if (inp) inp.value = tok;
        if (typeof connect === 'function') connect();
      })(${JSON.stringify(adminToken)})
    `).catch(() => {});
  });

  // Auto-push a tab snapshot to the gateway after every real page load so
  // the AI chat always has fresh page context when the user enables "Page" context.
  view.webContents.on('did-finish-load', async () => {
    const url = view.webContents.getURL();
    // Skip blank, new-tab, and gateway admin pages
    if (!url || url === 'about:blank' || url.startsWith(GATEWAY_ORIGIN + '/ui/')) return;
    // Only push for the currently active tab
    if (view !== tabs[activeTabId]?.view) return;
    try {
      const html  = await view.webContents.executeJavaScript('document.documentElement.outerHTML').catch(() => '');
      const title = view.webContents.getTitle();
      const payload = JSON.stringify({ url, title, html });
      const req = http.request({
        hostname: GATEWAY_HOST,
        port:     GATEWAY_PORT,
        path:     '/tab/snapshot',
        method:   'PUT',
        headers:  {
          'Content-Type':   'application/json',
          'Content-Length': Buffer.byteLength(payload),
        },
      });
      req.on('error', () => {}); // silently ignore if gateway not yet up
      req.end(payload);
    } catch (_) {}
  });

  tabs[id] = { id, view };
  switchTab(id, win);
  return id;
}

/**
 * Switch the visible tab.
 */
function switchTab(id, win = mainWin) {
  // Hide all views
  for (const t of Object.values(tabs)) {
    win.removeBrowserView(t.view);
  }
  const tab = tabs[id];
  if (!tab) return;
  win.addBrowserView(tab.view);
  tab.view.setBounds(tabBounds(win));
  activeTabId = id;

  const wc = tab.view.webContents;
  notifyChrome('url-changed', { id, url: wc.getURL() });
  notifyChrome('nav-state', {
    id,
    canGoBack:    wc.canGoBack(),
    canGoForward: wc.canGoForward(),
  });
  notifyTabsUpdated();
}

/**
 * Close a tab. If it is the active one, activate the next or previous.
 */
function closeTab(id, win = mainWin) {
  const tab = tabs[id];
  if (!tab) return;
  win.removeBrowserView(tab.view);
  tab.view.webContents.destroy();
  delete tabs[id];

  if (Object.keys(tabs).length === 0) {
    // No tabs left — open a fresh one
    createTab(NEW_TAB_URL, win);
    return;
  }
  if (activeTabId === id) {
    const remaining = Object.keys(tabs).map(Number).sort((a, b) => a - b);
    switchTab(remaining[remaining.length - 1], win);
  } else {
    // Non-active tab closed — tab bar still needs to update
    notifyTabsUpdated();
  }
}

/**
 * Send an event to the chrome renderer (browser.html).
 */
function notifyChrome(channel, payload) {
  if (mainWin && !mainWin.isDestroyed()) {
    mainWin.webContents.send(channel, payload);
  }
}

/**
 * Broadcast the full tab list to the chrome renderer so the tab bar stays
 * in sync after any create / switch / close operation.
 */
function notifyTabsUpdated() {
  const list = Object.values(tabs).map(t => ({
    id:     t.id,
    url:    t.view.webContents.getURL(),
    title:  t.view.webContents.getTitle(),
    favicon: null,           // favicon updates arrive via separate event
    active: t.id === activeTabId,
  }));
  notifyChrome('tabs-updated', list);
}

// ─────────────────────────────────────────────────────────────────────────────
// IPC handlers (called by the chrome renderer via preload.js)
// ─────────────────────────────────────────────────────────────────────────────

function registerIPC() {
  // New tab
  ipcMain.handle('new-tab', (_, url) => {
    return createTab(url || NEW_TAB_URL);
  });

  // Close tab
  ipcMain.handle('close-tab', (_, id) => {
    closeTab(id);
  });

  // Duplicate a tab — open a new tab at the same URL as the given tab
  ipcMain.handle('duplicate-tab', (_, id) => {
    const tab = tabs[id];
    if (!tab) return null;
    const url = tab.view.webContents.getURL() || NEW_TAB_URL;
    return createTab(url);
  });

  // Right-click context menu on a tab — shown as a native OS menu so it
  // renders above all BrowserViews (HTML menus would be hidden under them).
  ipcMain.handle('show-tab-ctx', (_, { tabId, tabUrl }) => {
    const menu = Menu.buildFromTemplate([
      {
        label: 'New Tab',
        click: () => createTab(),
      },
      {
        label: 'Duplicate Tab',
        click: () => {
          const url = tabs[tabId]?.view.webContents.getURL() || tabUrl || NEW_TAB_URL;
          createTab(url);
        },
      },
      { type: 'separator' },
      {
        label: 'Close Tab',
        click: () => closeTab(tabId),
      },
    ]);
    menu.popup({ window: mainWin });
  });

  // Switch active tab
  ipcMain.handle('switch-tab', (_, id) => {
    switchTab(id);
    return activeTabId;
  });

  // Navigate active tab
  ipcMain.handle('navigate', (_, url) => {
    const tab = tabs[activeTabId];
    if (!tab) return;
    // Ensure the URL has a scheme
    let target = url.trim();
    if (!target.startsWith('http://') && !target.startsWith('https://') && !target.startsWith('intelli://')) {
      if (target.includes('.') && !target.includes(' ')) {
        target = 'https://' + target;
      } else {
        // Treat as a search query — use DuckDuckGo (privacy-preserving default)
        target = 'https://duckduckgo.com/?q=' + encodeURIComponent(target);
      }
    }
    tab.view.webContents.loadURL(target);
    return target;
  });

  // Back / Forward / Reload / Stop
  ipcMain.handle('go-back',    () => tabs[activeTabId]?.view.webContents.goBack());
  ipcMain.handle('go-forward', () => tabs[activeTabId]?.view.webContents.goForward());
  ipcMain.handle('reload',     () => tabs[activeTabId]?.view.webContents.reload());
  ipcMain.handle('stop',       () => tabs[activeTabId]?.view.webContents.stop());

  // Query current state
  ipcMain.handle('get-active-url', () => {
    return tabs[activeTabId]?.view.webContents.getURL() || '';
  });

  ipcMain.handle('get-tabs', () => {
    return Object.values(tabs).map(t => ({
      id:    t.id,
      url:   t.view.webContents.getURL(),
      title: t.view.webContents.getTitle(),
      active: t.id === activeTabId,
    }));
  });

  ipcMain.handle('get-gateway-status', () => ({
    ready: gatewayReady,
    origin: GATEWAY_ORIGIN,
    pid: gatewayProcess?.pid ?? null,
  }));

  // Navigate active tab to the Intelli admin hub
  ipcMain.handle('go-home', () => {
    tabs[activeTabId]?.view.webContents.loadURL(HOME_URL);
  });

  // Open external links in the system browser
  ipcMain.handle('open-external', (_, url) => shell.openExternal(url));

  // Return the auto-minted admin bearer token (used by the admin UI)
  ipcMain.handle('get-admin-token', () => adminToken);

  // ── Tab snapshot — capture the full HTML of the active tab ──────────────
  ipcMain.handle('get-tab-snapshot', async () => {
    const tab = tabs[activeTabId];
    if (!tab) return null;
    const wc = tab.view.webContents;
    try {
      const html = await wc.executeJavaScript('document.documentElement.outerHTML');
      return { url: wc.getURL(), title: wc.getTitle(), html };
    } catch (e) {
      console.error('[tab-snapshot] executeJavaScript failed:', e.message);
      return { url: wc.getURL(), title: wc.getTitle(), html: '' };
    }
  });

  // ── captureTab — on-demand agent snapshot via POST /tab/preview ──────────
  // Captures page HTML, posts to the gateway's sanitization + consent endpoint,
  // and returns the sanitized preview JSON to the caller (sidebar / agent panel).
  ipcMain.handle('capture-tab', async () => {
    const tab = tabs[activeTabId];
    if (!tab) return null;
    const wc  = tab.view.webContents;
    const url = wc.getURL();
    const title = wc.getTitle();

    let html = '';
    try {
      html = await wc.executeJavaScript('document.documentElement.outerHTML');
    } catch (e) {
      console.error('[capture-tab] HTML capture failed:', e.message);
    }

    // Optionally capture a page screenshot (NativeImage → base64 PNG)
    let screenshotDataUrl = null;
    try {
      const image = await wc.capturePage();
      if (!image.isEmpty()) screenshotDataUrl = image.toDataURL();
    } catch (e) {
      console.error('[capture-tab] capturePage failed:', e.message);
    }

    // POST to gateway /tab/preview (handles sanitization + consent logging)
    const payload = JSON.stringify({ html, url, title });
    return new Promise((resolve) => {
      const reqOpts = {
        hostname: GATEWAY_HOST,
        port:     GATEWAY_PORT,
        path:     '/tab/preview',
        method:   'POST',
        headers:  {
          'Content-Type':   'application/json',
          'Content-Length': Buffer.byteLength(payload),
          ...(adminToken ? { Authorization: 'Bearer ' + adminToken } : {}),
        },
      };
      const req = http.request(reqOpts, (res) => {
        let body = '';
        res.on('data', (chunk) => { body += chunk; });
        res.on('end', () => {
          let result = null;
          try { result = JSON.parse(body); } catch { /* non-JSON */ }
          resolve({
            ok:                res.statusCode >= 200 && res.statusCode < 300,
            status:            res.statusCode,
            url,
            title,
            screenshotDataUrl, // null if capturePage failed / not available
            result,            // sanitized preview from gateway
          });
        });
      });
      req.on('error', (e) => {
        console.error('[capture-tab] gateway POST failed:', e.message);
        resolve({ ok: false, status: 0, url, title, screenshotDataUrl: null, result: null });
      });
      req.write(payload);
      req.end();
    });
  });

  // ── Script injection — run JS in the active tab (used by addon system) ───
  ipcMain.handle('inject-script', async (_, code) => {
    const tab = tabs[activeTabId];
    if (!tab) return null;
    try {
      return await tab.view.webContents.executeJavaScript(code);
    } catch (e) {
      console.error('[inject-script] error:', e.message);
      return null;
    }
  });

  // Toggle DevTools for the active page (Ctrl+Shift+I / right-click Inspect)
  ipcMain.handle('toggle-devtools', () => {
    const tab = tabs[activeTabId];
    if (!tab) return;
    const wc = tab.view.webContents;
    if (wc.isDevToolsOpened()) wc.closeDevTools();
    else wc.openDevTools();
  });

  // Toggle DevTools for the chrome renderer window itself
  ipcMain.handle('toggle-chrome-devtools', () => {
    if (!mainWin) return;
    const wc = mainWin.webContents;
    if (wc.isDevToolsOpened()) wc.closeDevTools();
    else wc.openDevTools();
  });

  // ── Bookmarks ────────────────────────────────────────────────────────────
  ipcMain.handle('bookmarks-list',   () => loadBookmarks());
  ipcMain.handle('bookmarks-add',    (_, { url, title, favicon }) => {
    const bm = loadBookmarks();
    if (bm.find(b => b.url === url)) return bm;        // already saved
    bm.unshift({ id: Date.now(), url, title: title || url, favicon: favicon || null, addedAt: new Date().toISOString() });
    saveBookmarks(bm);
    return bm;
  });
  ipcMain.handle('bookmarks-remove', (_, url) => {
    const bm = loadBookmarks().filter(b => b.url !== url);
    saveBookmarks(bm);
    return bm;
  });
  ipcMain.handle('bookmarks-has', (_, url) => {
    return loadBookmarks().some(b => b.url === url);
  });

  // ── History ───────────────────────────────────────────────────────────────
  ipcMain.handle('history-list',   (_, limit) => loadHistory().slice(0, limit || 200));
  ipcMain.handle('history-clear',  () => { saveHistory([]); return true; });
  ipcMain.handle('history-remove', (_, url) => {
    const h = loadHistory().filter(e => e.url !== url);
    saveHistory(h);
    return h;
  });

  // ── Zoom ──────────────────────────────────────────────────────────────────
  ipcMain.handle('zoom-in',    () => {
    const wc = tabs[activeTabId]?.view.webContents;
    if (wc) wc.setZoomLevel(wc.getZoomLevel() + 0.5);
  });
  ipcMain.handle('zoom-out',   () => {
    const wc = tabs[activeTabId]?.view.webContents;
    if (wc) wc.setZoomLevel(wc.getZoomLevel() - 0.5);
  });
  ipcMain.handle('zoom-reset', () => {
    const wc = tabs[activeTabId]?.view.webContents;
    if (wc) wc.setZoomLevel(0);
  });
  ipcMain.handle('zoom-get', () => {
    return tabs[activeTabId]?.view.webContents.getZoomLevel() ?? 0;
  });

  // ── Panel visibility (shrinks tab BrowserView so panels are not hidden) ──────
  ipcMain.handle('panel-visible', (_, isOpen) => {
    panelVisible = !!isOpen;
    const tab = tabs[activeTabId];
    if (tab && mainWin) tab.view.setBounds(tabBounds(mainWin));
  });

  // ── Downloads ─────────────────────────────────────────────────────────────
  ipcMain.handle('open-downloads-folder', () => {
    shell.openPath(app.getPath('downloads'));
  });

  // ── App menu (three-dot) — native popup with Chrome-style structure ───────
  ipcMain.handle('show-app-menu', () => {
    const zl = tabs[activeTabId]?.view.webContents.getZoomLevel() ?? 0;
    const pct = Math.round(100 * Math.pow(1.2, zl));
    const menu = Menu.buildFromTemplate([
      { label: 'New Tab',                accelerator: 'CmdOrCtrl+T', click: () => createTab() },
      { label: 'New Window',             click: () => { /* future */ } },
      { type: 'separator' },
      {
        label: 'Bookmarks', submenu: [
          { label: 'Bookmark This Tab…', accelerator: 'CmdOrCtrl+D',       click: () => notifyChrome('request-bookmark-toggle') },
          { label: 'Show All Bookmarks', accelerator: 'CmdOrCtrl+Shift+O', click: () => notifyChrome('open-panel', 'bookmarks') },
        ],
      },
      {
        label: 'History', submenu: [
          { label: 'Show Full History',    accelerator: 'CmdOrCtrl+H', click: () => notifyChrome('open-panel', 'history') },
          { label: 'Clear Browsing Data…',                              click: () => notifyChrome('open-panel', 'clear-data') },
        ],
      },
      {
        label: 'Downloads',              accelerator: 'CmdOrCtrl+J',
        click: () => shell.openPath(app.getPath('downloads')),
      },
      { type: 'separator' },
      {
        label: `Zoom  —  ${pct}%`, submenu: [
          { label: 'Zoom In',    accelerator: 'CmdOrCtrl+=',         click: () => { const wc = tabs[activeTabId]?.view.webContents; if (wc) wc.setZoomLevel(wc.getZoomLevel() + 0.5); notifyChrome('zoom-changed'); } },
          { label: 'Zoom Out',   accelerator: 'CmdOrCtrl+-',         click: () => { const wc = tabs[activeTabId]?.view.webContents; if (wc) wc.setZoomLevel(wc.getZoomLevel() - 0.5); notifyChrome('zoom-changed'); } },
          { label: 'Reset Zoom', accelerator: 'CmdOrCtrl+0',         click: () => { const wc = tabs[activeTabId]?.view.webContents; if (wc) wc.setZoomLevel(0); notifyChrome('zoom-changed'); } },
          { type: 'separator' },
          { label: 'Full Screen', accelerator: 'F11',                click: () => mainWin?.setFullScreen(!mainWin.isFullScreen()) },
        ],
      },
      {
        label: 'Print…',               accelerator: 'CmdOrCtrl+P',
        click: () => tabs[activeTabId]?.view.webContents.print(),
      },
      { type: 'separator' },
      {
        label: 'Extensions / Addons', submenu: [
          { label: 'Manage Intelli Addons', click: () => createTab(`${GATEWAY_ORIGIN}/ui/addons.html`) },
          { label: 'Chrome Web Store',      click: () => createTab('https://chrome.google.com/webstore') },
          { label: 'Developer Mode Addons', click: () => notifyChrome('open-panel', 'dev-addons') },
        ],
      },
      { type: 'separator' },
      {
        label: 'Developer Tools', submenu: [
          { label: 'Inspect Page',          accelerator: 'CmdOrCtrl+Shift+I', click: () => { const wc = tabs[activeTabId]?.view.webContents; if (wc) wc.toggleDevTools(); } },
          { label: 'Inspect Chrome Shell',                                     click: () => mainWin?.webContents.toggleDevTools() },
          { label: 'View Page Source',      accelerator: 'CmdOrCtrl+U',       click: () => { const url = tabs[activeTabId]?.view.webContents.getURL(); if (url) createTab('view-source:' + url); } },
          { label: 'JavaScript Console',                                        click: () => { const wc = tabs[activeTabId]?.view.webContents; if (wc) { wc.openDevTools({ mode: 'detach', activate: true }); } } },
        ],
      },
      { type: 'separator' },
      {
        label: 'Settings',
        click: () => notifyChrome('open-panel', 'settings'),
      },
      {
        label: 'About Intelli',
        click: () => dialog.showMessageBox(mainWin, {
          type: 'info',
          title: 'Intelli',
          message: 'Intelli Browser',
          detail: `Version: ${app.getVersion()}\nElectron: ${process.versions.electron}\nChrome: ${process.versions.chrome}`,
        }),
      },
    ]);
    menu.popup({ window: mainWin });
  });

  // ── Window controls (custom chrome buttons — needed because titleBarStyle:'hidden') ──
  ipcMain.handle('win-minimize',     () => mainWin?.minimize());
  ipcMain.handle('win-maximize',     () => {
    if (!mainWin) return false;
    if (mainWin.isMaximized()) mainWin.unmaximize(); else mainWin.maximize();
    return mainWin.isMaximized();
  });
  ipcMain.handle('win-close',        () => mainWin?.close());
  ipcMain.handle('win-is-maximized', () => mainWin?.isMaximized() ?? false);

  // ── Browser automation command handlers ─────────────────────────────────
  // These execute commands from the agent gateway's browser_tools.py queue.
  // The gateway enqueues commands; Electron polls, executes, and posts results.

  async function executeBrowserCommand(cmd) {
    const { id, command, args } = cmd;
    const tab = tabs[activeTabId];
    if (!tab) {
      return { id, result: { error: 'No active tab' } };
    }
    const wc = tab.view.webContents;

    try {
      switch (command) {
        case 'click': {
          const { selector, button = 'left' } = args;
          const code = `
            (function() {
              const el = document.querySelector(${JSON.stringify(selector)});
              if (!el) return { error: 'Element not found: ${selector}' };
              el.scrollIntoView({ block: 'center' });
              const rect = el.getBoundingClientRect();
              el.click();
              return { success: true, bounds: { x: rect.x, y: rect.y, w: rect.width, h: rect.height } };
            })()
          `;
          const res = await wc.executeJavaScript(code);
          return { id, result: res };
        }

        case 'type': {
          const { selector, text, clear = true } = args;
          const code = `
            (function() {
              const el = document.querySelector(${JSON.stringify(selector)});
              if (!el) return { error: 'Element not found: ${selector}' };
              el.scrollIntoView({ block: 'center' });
              el.focus();
              if (${clear}) el.value = '';
              el.value += ${JSON.stringify(text)};
              el.dispatchEvent(new Event('input', { bubbles: true }));
              el.dispatchEvent(new Event('change', { bubbles: true }));
              return { success: true };
            })()
          `;
          const res = await wc.executeJavaScript(code);
          return { id, result: res };
        }

        case 'scroll': {
          const { pixels = 0, to_bottom = false } = args;
          const code = to_bottom
            ? 'window.scrollTo(0, document.body.scrollHeight); ({ success: true })'
            : `window.scrollBy(0, ${pixels}); ({ success: true })`;
          const res = await wc.executeJavaScript(code);
          return { id, result: res };
        }

        case 'navigate': {
          const { url } = args;
          await wc.loadURL(url);
          return { id, result: { success: true, url } };
        }

        case 'screenshot': {
          const img = await wc.capturePage();
          const b64 = img.toPNG().toString('base64');
          return { id, result: { success: true, image: b64 } };
        }

        case 'eval': {
          const { code } = args;
          const res = await wc.executeJavaScript(code);
          return { id, result: { success: true, result: res } };
        }

        case 'wait': {
          const { selector, timeout = 10 } = args;
          const startTime = Date.now();
          const code = `
            (function() {
              return new Promise((resolve) => {
                const check = () => {
                  const el = document.querySelector(${JSON.stringify(selector)});
                  if (el) {
                    resolve({ success: true });
                  } else if (Date.now() - ${startTime} > ${timeout * 1000}) {
                    resolve({ error: 'Timeout waiting for ${selector}' });
                  } else {
                    setTimeout(check, 100);
                  }
                };
                check();
              });
            })()
          `;
          const res = await wc.executeJavaScript(code);
          return { id, result: res };
        }

        default:
          return { id, result: { error: `Unknown command: ${command}` } };
      }
    } catch (err) {
      return { id, result: { error: err.message } };
    }
  }

  // Poll the gateway for browser automation commands every 500ms
  async function pollBrowserCommands() {
    if (!gatewayReady || !adminToken) return;

    try {
      const body = JSON.stringify({});
      const req = http.request({
        hostname: GATEWAY_HOST,
        port:     GATEWAY_PORT,
        path:     '/browser/command-queue',
        method:   'GET',
        headers:  {
          'Authorization': `Bearer ${adminToken}`,
          'Content-Type':  'application/json',
        },
      }, res => {
        let data = '';
        res.on('data', d => data += d);
        res.on('end', async () => {
          try {
            const json = JSON.parse(data);
            if (json.command && json.id) {
              // Execute the command
              const result = await executeBrowserCommand(json);
              // Post result back to gateway
              const resultBody = JSON.stringify(result);
              const postReq = http.request({
                hostname: GATEWAY_HOST,
                port:     GATEWAY_PORT,
                path:     '/browser/result',
                method:   'POST',
                headers:  {
                  'Authorization':  `Bearer ${adminToken}`,
                  'Content-Type':   'application/json',
                  'Content-Length': Buffer.byteLength(resultBody),
                },
              });
              postReq.write(resultBody);
              postReq.end();
            }
          } catch (e) {
            console.error('[browser-commands] parse error:', e.message);
          }
        });
      });
      req.on('error', () => { /* silent fail — gateway might be restarting */ });
      req.end();
    } catch (e) {
      console.error('[browser-commands] poll error:', e.message);
    }
  }

  // Start polling loop
  setInterval(pollBrowserCommands, 500);

  // ── Sidebar toggle ──────────────────────────────────────────────────────
  // Creates the sidebar BrowserView lazily on first use, then shows/hides it
  // by adding/removing it from the window and resizing the active tab view.
  ipcMain.handle('toggle-sidebar', () => {
    if (!mainWin) return { open: false };

    if (!sidebarView) {
      sidebarView = new BrowserView({
        webPreferences: {
          nodeIntegration:  false,
          contextIsolation: true,
          sandbox:          true,
          webviewTag:       false,
        },
      });
      sidebarView.webContents.loadURL(`${GATEWAY_ORIGIN}/ui/chat.html`);
    }

    sidebarOpen = !sidebarOpen;

    if (sidebarOpen) {
      mainWin.addBrowserView(sidebarView);
      sidebarView.setBounds(sidebarBounds(mainWin));
    } else {
      mainWin.removeBrowserView(sidebarView);
    }

    // Resize the active tab to fill the remaining horizontal space
    const tab = tabs[activeTabId];
    if (tab) tab.view.setBounds(tabBounds(mainWin));

    return { open: sidebarOpen };
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Window construction
// ─────────────────────────────────────────────────────────────────────────────

function buildAppMenu() {
  const template = [
    {
      label: 'File',
      submenu: [
        { label: 'New Tab',       accelerator: 'CmdOrCtrl+T', click: () => createTab() },
        { label: 'Close Tab',     accelerator: 'CmdOrCtrl+W', click: () => closeTab(activeTabId) },
        { type: 'separator' },
        { label: 'Quit Intelli',  accelerator: 'CmdOrCtrl+Q', role: 'quit' },
      ],
    },
    {
      label: 'View',
      submenu: [
        { label: 'Reload Tab',       accelerator: 'CmdOrCtrl+R', click: () => tabs[activeTabId]?.view.webContents.reload() },
        { label: 'Force Reload',     accelerator: 'CmdOrCtrl+Shift+R', click: () => tabs[activeTabId]?.view.webContents.reloadIgnoringCache() },
        { type: 'separator' },
        { label: 'Toggle DevTools (page)',  accelerator: 'F12', click: () => tabs[activeTabId]?.view.webContents.toggleDevTools() },
        { label: 'Toggle DevTools (chrome)', click: () => mainWin?.webContents.toggleDevTools() },
        { type: 'separator' },
        { label: 'Zoom In',   accelerator: 'CmdOrCtrl+Plus',  role: 'zoomIn'  },
        { label: 'Zoom Out',  accelerator: 'CmdOrCtrl+-',     role: 'zoomOut' },
        { label: 'Reset Zoom',accelerator: 'CmdOrCtrl+0',     role: 'resetZoom' },
        { type: 'separator' },
        { label: 'Toggle Fullscreen', accelerator: 'F11', role: 'togglefullscreen' },
      ],
    },
    {
      label: 'History',
      submenu: [
        { label: 'Back',    accelerator: 'Alt+Left',  click: () => tabs[activeTabId]?.view.webContents.goBack() },
        { label: 'Forward', accelerator: 'Alt+Right', click: () => tabs[activeTabId]?.view.webContents.goForward() },
      ],
    },
    {
      label: 'Gateway',
      submenu: [
        { label: 'Open Admin Hub',  click: () => createTab(HOME_URL) },
        { label: 'Open Audit Log',  click: () => createTab(`${GATEWAY_ORIGIN}/ui/audit.html`) },
        { label: 'Open User Mgmt', click: () => createTab(`${GATEWAY_ORIGIN}/ui/users.html`) },
        { label: 'Open Status',     click: () => createTab(`${GATEWAY_ORIGIN}/ui/status.html`) },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));

  // ── Auto-updater IPC ─────────────────────────────────────────────────────
  ipcMain.handle('install-update', () => {
    autoUpdater?.quitAndInstall();
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Content Security Policy — applied to the chrome renderer (browser.html).
// We intercept response headers for file:// requests so Electron's built-in
// security check (which reads the HTTP-level header, not the meta tag) is
// satisfied and the console warning disappears.
// ─────────────────────────────────────────────────────────────────────────────
function installCSP() {
  const CHROME_CSP = [
    "default-src 'self'",
    "script-src  'self'",
    "style-src   'self'",
    "img-src     'self' data: https: http://127.0.0.1:8080",
    "connect-src http://127.0.0.1:8080",
  ].join('; ');

  // Narrow to the exact chrome renderer URL (browser.html) so we don't
  // accidentally clobber other file:// pages (e.g. splash.html).
  const filter = { urls: ['file://*browser.html*', 'file://*browser.html'] };
  session.defaultSession.webRequest.onHeadersReceived(filter, (details, callback) => {
    const hdrs = { ...details.responseHeaders };
    hdrs['Content-Security-Policy'] = [CHROME_CSP];
    callback({ responseHeaders: hdrs });
  });
}

function createMainWindow() {
  mainWin = new BrowserWindow({
    width:           1280,
    height:          800,
    minWidth:        640,
    minHeight:       400,
    backgroundColor: '#0f1117',
    titleBarStyle:   'hidden',
    trafficLightPosition: { x: 12, y: 10 },
    webPreferences: {
      preload:          path.join(__dirname, 'preload.js'),
      nodeIntegration:  false,
      contextIsolation: true,
      sandbox:          false,  // preload needs non-sandboxed for IPC
    },
    show: false,
    icon: path.join(__dirname, 'assets', 'icon.ico'),
  });

  mainWin.loadFile(path.join(__dirname, 'src', 'browser.html'));

  mainWin.once('ready-to-show', () => {
    mainWin.show();
    // On first launch (no admin yet) open the setup wizard, otherwise the hub
    createTab(needsSetup ? SETUP_URL : HOME_URL);
  });

  // Keep BrowserView bounds in sync when window is resized
  function syncBounds() {
    const tab = tabs[activeTabId];
    if (tab) tab.view.setBounds(tabBounds(mainWin));
    if (sidebarOpen && sidebarView) sidebarView.setBounds(sidebarBounds(mainWin));
  }

  mainWin.on('resize',            syncBounds);
  mainWin.on('enter-full-screen', syncBounds);
  mainWin.on('leave-full-screen', syncBounds);
  // restore fires when the window comes back from minimized — resize does NOT
  // always fire in that case, so the BrowserView would keep its collapsed bounds.
  mainWin.on('restore',           syncBounds);

  mainWin.on('closed', () => { mainWin = null; });

  // Forward maximize/unmaximize state to chrome renderer (updates button icon)
  // and re-sync bounds so the BrowserView fills the new window size.
  mainWin.on('maximize',   () => { syncBounds(); notifyChrome('win-maximize-changed', true);  });
  mainWin.on('unmaximize', () => { syncBounds(); notifyChrome('win-maximize-changed', false); });

  buildAppMenu();
}

// ─────────────────────────────────────────────────────────────────────────────
// App lifecycle
// ─────────────────────────────────────────────────────────────────────────────

app.whenReady().then(async () => {
  installCSP();   // must be first — sets up webRequest before any window loads
  setupDownloads(session.defaultSession);
  registerIPC();

  // Show a loading window while the gateway boots
  const splash = new BrowserWindow({
    width: 400,
    height: 260,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    resizable: false,
    backgroundColor: '#0f1117',
  });
  splash.loadFile(path.join(__dirname, 'src', 'splash.html'));

  try {
    await startGateway();
  } catch (err) {
    splash.destroy();
    dialog.showErrorBox(
      'Intelli — Gateway failed to start',
      `The embedded agent gateway could not be started.\n\n${err.message}\n\nEnsure Python 3.10+ is installed and the agent-gateway dependencies are installed:\n  pip install -r agent-gateway/requirements.txt`,
    );
    app.quit();
    return;
  }

  // Mint an admin bearer token using the bootstrap secret so all admin UI
  // pages are automatically authenticated without needing manual token paste.
  try {
    const body = JSON.stringify({ secret: BOOTSTRAP_SECRET });
    adminToken = await new Promise((resolve, reject) => {
      const req = http.request({
        hostname: GATEWAY_HOST,
        port:     GATEWAY_PORT,
        path:     '/admin/bootstrap-token',
        method:   'POST',
        headers:  { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
      }, res => {
        let data = '';
        res.on('data', d => data += d);
        res.on('end', () => {
          try {
            const json = JSON.parse(data);
            if (json.access_token) resolve(json.access_token);
            else reject(new Error('no access_token in response'));
          } catch (e) { reject(e); }
        });
        res.on('error', reject);
      });
      req.on('error', reject);
      req.write(body);
      req.end();
    });
    console.log('[gateway] admin token acquired ✓');
  } catch (e) {
    console.warn('[gateway] bootstrap-token failed (admin pages need manual token):', e.message);
  }

  // Check whether first-run setup is needed (no admin account created yet)
  needsSetup = await new Promise((resolve) => {
    http.get(`${GATEWAY_ORIGIN}/admin/setup-status`, (res) => {
      let data = '';
      res.on('data', d => { data += d; });
      res.on('end', () => {
        try { resolve(JSON.parse(data).needs_setup === true); }
        catch { resolve(false); }
      });
    }).on('error', () => resolve(false));
  });
  if (needsSetup) console.log('[gateway] first-run setup required — opening wizard');

  // Create the main window BEFORE destroying the splash so there is never a
  // moment with zero open windows on Windows/Linux.  If splash is destroyed
  // first, window-all-closed fires immediately and app.quit() kills the process.
  createMainWindow();
  splash.destroy();

  // Auto-update: listen for events and relay to the chrome renderer.
  // checkForUpdates() is deferred 5 s so the window has time to fully load.
  if (autoUpdater) {
    autoUpdater.on('update-available', (info) => {
      mainWin?.webContents.send('update-available', {
        version: info.version,
        releaseDate: info.releaseDate,
      });
    });
    autoUpdater.on('update-downloaded', () => {
      mainWin?.webContents.send('update-downloaded');
    });
    autoUpdater.on('error', (e) => {
      console.warn('[updater] error:', e.message);
    });
    setTimeout(() => {
      try { autoUpdater.checkForUpdates(); }
      catch (e) { console.warn('[updater] checkForUpdates failed:', e.message); }
    }, 5000);
  }
});

// macOS: re-create window on dock icon click
app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createMainWindow();
});

// Quit when all windows are closed (except on macOS)
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

// Shut down gateway before quitting
app.on('before-quit', () => {
  stopGateway();
});

// Security: restrict what new windows can be opened
app.on('web-contents-created', (_, contents) => {
  contents.setWindowOpenHandler(({ url }) => {
    // Open external links in the system browser
    if (!url.startsWith(GATEWAY_ORIGIN) && !url.startsWith('file://')) {
      shell.openExternal(url);
      return { action: 'deny' };
    }
    // For gateway-origin links opened by the page, create a new tab instead
    setImmediate(() => createTab(url));
    return { action: 'deny' };
  });
});
