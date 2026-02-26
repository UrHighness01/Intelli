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
  nativeImage,
  screen,
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
// Resolved at startup from settings.json; updated live via IPC.
const _startSettings = loadSettings();
let NEW_TAB_URL = _newtabToUrl(_startSettings);
const SETUP_URL         = `${GATEWAY_ORIGIN}/ui/setup.html`;

// ─── Anti-fingerprint — must be set before app is ready ─────────────────────
// Removes the flags that tell Google reCAPTCHA / bot-detection we are Electron.
app.commandLine.appendSwitch('disable-blink-features', 'AutomationControlled');
app.commandLine.appendSwitch('disable-features', 'AutomationControlled,HardwareMediaKeyHandling,MediaFoundationVideoCapture');
app.commandLine.appendSwitch('no-first-run');
app.commandLine.appendSwitch('no-default-browser-check');
app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required');
app.commandLine.appendSwitch('force-color-profile', 'srgb');

// Real Chrome 122 UA string (Electron 29 = Chrome 122). The word "Electron"
// is removed so sites cannot trivially fingerprint us.
const CHROME_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36';

// ─── Anti-détection injectable dans le monde principal ───────────────────────
// Exécuté via executeJavaScript() (monde principal) à chaque dom-ready,
// did-navigate et did-navigate-in-page.
// Note: avec contextIsolation:true, window.process/Buffer/global ne sont JAMAIS
// exposés dans le monde de la page — pas besoin de les supprimer ici.
// navigator.webdriver est déjà géré par disable-blink-features=AutomationControlled.
const ANTIDETECT_JS = `(function(){
  // document.hasFocus() — BrowserView non-focusé retourne false → YouTube le détecte
  try { Object.defineProperty(document,'hasFocus',{value:()=>true,writable:true,configurable:true}); } catch(_) {}
  // Compléter window.chrome si absent ou incomplet (Chromium l'expose nativement mais
  // sans csi() / loadTimes() dans certaines versions d'Electron)
  try {
    if(window.chrome && !window.__intel_spoofed__) {
      const t0 = performance.now ? (performance.timeOrigin + performance.now()) : Date.now();
      if(!window.chrome.csi) window.chrome.csi = () => ({startE:t0,onloadT:t0+120,pageT:t0+150,tran:15});
      if(!window.chrome.loadTimes) window.chrome.loadTimes = () => ({commitLoadTime:t0/1000,connectionInfo:'h2',finishDocumentLoadTime:(t0+120)/1000,finishLoadTime:(t0+150)/1000,firstPaintAfterLoadTime:0,firstPaintTime:(t0+80)/1000,navigationType:'Other',npnNegotiatedProtocol:'h2',requestTime:t0/1000,startLoadTime:t0/1000,wasAlternateProtocolAvailable:false,wasFetchedViaSpdy:true,wasNpnNegotiated:true});
      if(!window.chrome.runtime) window.chrome.runtime={id:undefined,connect:()=>{},sendMessage:()=>{},onConnect:{addListener:()=>{},removeListener:()=>{}},onMessage:{addListener:()=>{},removeListener:()=>{}}};
      if(!window.chrome.app) window.chrome.app={isInstalled:false,getDetails:()=>null,getIsInstalled:()=>false,runningState:()=>'cannot_run'};
      window.__intel_spoofed__ = true;
    }
  } catch(_) {}
})();`;

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
let tabOrder    = [];   // ordered array of tab IDs (for drag-to-reorder)
let activeTabId = null;
let splitPairs  = [];  // [{ leftId, rightId, paused }] — all active split pairs
let nextTabId   = 1;
const tabPreviews = {};  // tabId → data:URL screenshot (captured when tab is grouped)
let _hoverWin     = null;  // floating preview BrowserWindow (module-scope for cleanup)
function _closeHoverWin() {
  if (_hoverWin && !_hoverWin.isDestroyed()) { _hoverWin.destroy(); }
  _hoverWin = null;
}

// ─── User-data paths ──────────────────────────────────────────────────────────
function userDataFile(name) {
  return path.join(app.getPath('userData'), name);
}

// ─── Settings ─────────────────────────────────────────────────────────────────
function loadSettings() {
  try { return JSON.parse(fs.readFileSync(userDataFile('settings.json'), 'utf8')); }
  catch { return {}; }
}
function saveSettingsData(s) {
  fs.writeFileSync(userDataFile('settings.json'), JSON.stringify(s, null, 2));
}
// Ensures a custom URL has a valid protocol prefix.
function _normalizeCustomUrl(url) {
  if (!url) return '';
  const u = url.trim();
  if (!u) return '';
  if (/^https?:\/\//i.test(u)) return u;
  return 'https://' + u;
}
// Resolves a newtab setting value to a loadable URL.
function _newtabToUrl(s) {
  if (s.newtab === 'home')       return 'http://127.0.0.1:8080/ui/';
  if (s.newtab === 'google')     return 'https://www.google.com/';
  if (s.newtab === 'duckduckgo') return 'https://duckduckgo.com/';
  if (s.newtab === 'brave')      return 'https://search.brave.com/';
  if (s.newtab === 'custom' && s.customUrl) return _normalizeCustomUrl(s.customUrl);
  return 'https://duckduckgo.com/';  // default
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
  const totalW = Math.max(0, w - sideW - panelW);
  if (getActivePair() !== null) {
    return { x: 0, y: CHROME_HEIGHT, width: Math.floor(totalW / 2) - 1, height: Math.max(0, h - CHROME_HEIGHT) };
  }
  return { x: 0, y: CHROME_HEIGHT, width: totalW, height: Math.max(0, h - CHROME_HEIGHT) };
}

/** Bounds for the admin-hub sidebar BrowserView when open. */
function sidebarBounds(win) {
  const [w, h] = win.getContentSize();
  return { x: Math.max(0, w - SIDEBAR_WIDTH), y: CHROME_HEIGHT, width: SIDEBAR_WIDTH, height: Math.max(0, h - CHROME_HEIGHT) };
}

/** Bounds for the right-hand BrowserView in split mode. */
function splitRightBounds(win) {
  const [w, h] = win.getContentSize();
  const sideW  = sidebarOpen  ? SIDEBAR_WIDTH : 0;
  const panelW = panelVisible ? PANEL_WIDTH   : 0;
  const totalW = Math.max(0, w - sideW - panelW);
  const half   = Math.floor(totalW / 2);
  return { x: half + 1, y: CHROME_HEIGHT, width: totalW - half - 1, height: Math.max(0, h - CHROME_HEIGHT) };
}

// ── Split-pairs helpers ───────────────────────────────────────────────────────
/** Returns the split pair containing tab `id`, or null. */
function getPairOf(id) {
  return splitPairs.find(p => p.leftId === id || p.rightId === id) || null;
}

/** Returns the currently visible (non-paused) split pair, or null. */
function getActivePair() {
  return splitPairs.find(p => !p.paused) || null;
}

/**
 * Internal: show a split pair side-by-side, pausing all other pairs.
 * Sets activeTabId to pair.leftId.
 */
function _showPair(pair, win = mainWin) {
  for (const p of splitPairs) if (p !== pair) p.paused = true;
  pair.paused = false;
  activeTabId = pair.leftId;
  for (const t of Object.values(tabs)) win.removeBrowserView(t.view);
  const lt = tabs[pair.leftId];
  const rt = tabs[pair.rightId];
  if (lt && rt) {
    win.addBrowserView(lt.view);
    win.addBrowserView(rt.view);
    lt.view.setBounds(tabBounds(win));
    rt.view.setBounds(splitRightBounds(win));
  }
  notifyChrome('split-changed', { splitTabId: pair.rightId });
}

/** Enter split-view: active tab on left, `id` on right. Creates a new pair. */
function enterSplit(id, win = mainWin) {
  if (!tabs[id] || !tabs[activeTabId]) return;
  // Pause any currently visible pair
  const ap = getActivePair();
  if (ap) ap.paused = true;
  const pair = { leftId: activeTabId, rightId: id, paused: false };
  splitPairs.push(pair);
  _showPair(pair, win);
  notifyTabsUpdated();
}

/** Close the split pair that contains `tabId`. */
function exitSplitForTab(tabId, win = mainWin) {
  const pair = getPairOf(tabId);
  if (!pair) return;
  splitPairs = splitPairs.filter(p => p !== pair);
  for (const t of Object.values(tabs)) win.removeBrowserView(t.view);
  // If another pair exists, activate the first one; otherwise show solo
  const next = splitPairs[0];
  if (next) {
    next.paused = false;
    _showPair(next, win);
  } else {
    notifyChrome('split-changed', null);
    const at = tabs[activeTabId];
    if (at) { win.addBrowserView(at.view); at.view.setBounds(tabBounds(win)); }
  }
  notifyTabsUpdated();
}

/** Exit split-view for the active tab's pair (backward compat). */
function exitSplit(win = mainWin) {
  exitSplitForTab(activeTabId, win);
}

/**
 * Returns the id of an existing Admin Hub tab, or null if none is open.
 */
function _existingAdminHubId() {
  return Object.values(tabs).find(t =>
    !t.view.webContents.isDestroyed() &&
    (t.view.webContents.getURL() || '').startsWith(GATEWAY_ORIGIN + '/ui/')
  )?.id ?? null;
}

/**
 * Create a new tab and attach it to the window.
 * Returns the tab id.
 */
function createTab(url = NEW_TAB_URL, win = mainWin, fromTabId = null) {
  // Si l'URL est un Admin Hub et qu'un tel onglet existe déjà, basculer dessus.
  if (url.startsWith(GATEWAY_ORIGIN + '/ui/')) {
    const existing = _existingAdminHubId();
    if (existing !== null) { switchTab(existing, win); return existing; }
  }
  const id   = nextTabId++;
  const view = new BrowserView({
    webPreferences: {
      nodeIntegration:  false,
      contextIsolation: true,    // isolate Electron globals from page world
      sandbox:          true,    // full sandbox — no Node in page renderer
      webviewTag:       false,
    },
  });

  // Do NOT addBrowserView here — switchTab will add it so we never get a
  // brief blank-view flash over the current tab while the new one loads.
  view.setAutoResize({ width: true, height: true });
  // Force le bon UA sur chaque vue individuellement (le setUserAgent session-level
  // ne se propage pas toujours aux BrowserViews créées après-coup).
  view.webContents.setUserAgent(CHROME_UA);
  view.webContents.loadURL(url);

  view.webContents.on('dom-ready', () => {
    if (!view.webContents.isDestroyed()) {
      view.webContents.executeJavaScript(ANTIDETECT_JS).catch(() => {});
    }
  });
  // Forward page events to the chrome renderer
  view.webContents.on('page-title-updated', (_, title) => {
    notifyChrome('tab-title-updated', { id, title });
  });
  view.webContents.on('did-navigate', (_, navUrl) => {
    if (activeTabId === id) {
      notifyChrome('url-changed', { id, url: navUrl });
      notifyChrome('nav-state', {
        id,
        canGoBack:    view.webContents.canGoBack(),
        canGoForward: view.webContents.canGoForward(),
      });
    }
    // Record visit in history (skip internal gateway UI)
    if (!navUrl.startsWith(GATEWAY_ORIGIN)) {
      pushHistory(navUrl, view.webContents.getTitle());
    }
    // Ré-injecter anti-détection après chaque navigation dure
    if (!view.webContents.isDestroyed()) {
      view.webContents.executeJavaScript(ANTIDETECT_JS).catch(() => {});
    }
  });
  view.webContents.on('did-navigate-in-page', (_, navUrl) => {
    if (activeTabId === id) {
      notifyChrome('url-changed', { id, url: navUrl });
      notifyChrome('nav-state', {
        id,
        canGoBack:    view.webContents.canGoBack(),
        canGoForward: view.webContents.canGoForward(),
      });
    }
    // Ré-injecter le script anti-détection : lors d'une navigation SPA
    // (YouTube pushState entre vidéos), Electron re-injecte window.process
    // dans le monde de la page. On le supprime à nouveau immédiatement.
    if (!view.webContents.isDestroyed()) {
      view.webContents.executeJavaScript(ANTIDETECT_JS).catch(() => {});
    }
  });
  view.webContents.on('page-favicon-updated', (_, favicons) => {
    notifyChrome('tab-favicon-updated', { id, favicon: favicons[0] || null });
  });
  view.webContents.on('audio-state-changed', ({ audible }) => {
    notifyChrome('tab-muted', { id, muted: view.webContents.isAudioMuted(), audible });
  });
  // Capture a screenshot ~800ms after the page finishes loading,
  // but only while this tab is the active (visible) one.
  view.webContents.on('did-finish-load', () => {
    if (activeTabId !== id) return;
    setTimeout(() => {
      if (activeTabId !== id || view.webContents.isDestroyed()) return;
      view.webContents.capturePage().then(img => {
        if (!img.isEmpty()) tabPreviews[id] = img.resize({ width: 280, height: 175 }).toDataURL();
      }).catch(() => {});
    }, 800);
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

  tabs[id] = { id, view, parentId: fromTabId || null };
  tabOrder.push(id);
  switchTab(id, win);
  return id;
}

/**
 * Switch the visible tab.
 */
function switchTab(id, win = mainWin) {
  // Capture screenshot of the leaving tab so it can be shown on hover later.
  // Freeze the ID in a local const — activeTabId changes before .then() fires.
  if (activeTabId && activeTabId !== id && tabs[activeTabId]) {
    const _captureId = activeTabId;
    const _oldWc = tabs[_captureId].view.webContents;
    if (!_oldWc.isDestroyed()) {
      _oldWc.capturePage().then(img => {
        tabPreviews[_captureId] = img.resize({ width: 280, height: 175 }).toDataURL();
      }).catch(() => {});
    }
  }
  // Hide all views
  for (const t of Object.values(tabs)) {
    win.removeBrowserView(t.view);
  }
  const tab = tabs[id];
  if (!tab) return;

  const pair       = getPairOf(id);
  const activePair = getActivePair();

  if (pair) {
    if (pair.paused) {
      // ─── Restore paused pair ───────────────────────────────────────────────
      // If the right side was clicked, make it the active (left) side
      if (id === pair.rightId) {
        const tmp = pair.leftId; pair.leftId = pair.rightId; pair.rightId = tmp;
      }
      _showPair(pair, win);
    } else if (id === pair.rightId) {
      // ─── Swap sides in the active pair ────────────────────────────────────
      const tmp = pair.leftId; pair.leftId = pair.rightId; pair.rightId = tmp;
      activeTabId = pair.leftId;
      const lt = tabs[pair.leftId]; const rt = tabs[pair.rightId];
      if (lt && rt) {
        win.addBrowserView(lt.view); win.addBrowserView(rt.view);
        lt.view.setBounds(tabBounds(win)); rt.view.setBounds(splitRightBounds(win));
      } else { exitSplitForTab(id, win); }
    } else {
      // ─── Clicked active left — refresh bounds ─────────────────────────────
      const lt = tabs[pair.leftId]; const rt = tabs[pair.rightId];
      if (lt && rt) {
        win.addBrowserView(lt.view); win.addBrowserView(rt.view);
        lt.view.setBounds(tabBounds(win)); rt.view.setBounds(splitRightBounds(win));
      }
    }
    const wc2 = tabs[activeTabId]?.view.webContents;
    if (wc2) {
      notifyChrome('url-changed', { id: activeTabId, url: wc2.getURL() });
      notifyChrome('nav-state', { id: activeTabId, canGoBack: wc2.canGoBack(), canGoForward: wc2.canGoForward() });
    }
    notifyTabsUpdated();
    return;
  }

  // ─── Solo (unpaired) tab clicked ─────────────────────────────────────────
  if (activePair) activePair.paused = true;   // pause any visible split pair
  activeTabId = id;
  win.addBrowserView(tab.view);
  tab.view.setBounds(tabBounds(win));
  const wc = tab.view.webContents;
  notifyChrome('url-changed', { id, url: wc.getURL() });
  notifyChrome('nav-state', { id, canGoBack: wc.canGoBack(), canGoForward: wc.canGoForward() });
  notifyTabsUpdated();
}

/**
 * Close a tab. If it is the active one, activate the next or previous.
 */
function closeTab(id, win = mainWin) {
  const tab = tabs[id];
  if (!tab) return;
  // Admin Hub tabs are pinned — cannot be closed
  const _tabUrl = tab.view.webContents.isDestroyed() ? '' : (tab.view.webContents.getURL() || '');
  if (_tabUrl.startsWith(GATEWAY_ORIGIN + '/ui/')) return;
  // Remove pair if the closing tab was part of one
  const closingPair = getPairOf(id);
  if (closingPair) {
    splitPairs = splitPairs.filter(p => p !== closingPair);
    if (!getActivePair()) notifyChrome('split-changed', splitPairs.length > 0 ? {} : null);
  }
  win.removeBrowserView(tab.view);
  tab.view.webContents.destroy();
  delete tabs[id];
  tabOrder = tabOrder.filter(x => x !== id);

  if (Object.keys(tabs).length === 0) {
    // No tabs left — open a fresh one
    createTab(NEW_TAB_URL, win);
    return;
  }
  if (activeTabId === id) {
    const remaining = tabOrder.filter(x => tabs[x]);
    switchTab(remaining[remaining.length - 1] ?? remaining[0], win);
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
  const list = tabOrder.filter(id => tabs[id]).map(id => {
    const t = tabs[id];
    return {
    id:          t.id,
    url:         t.view.webContents.getURL(),
    title:       t.view.webContents.getTitle(),
    favicon:     null,           // favicon updates arrive via separate event
    active:      t.id === activeTabId,
    muted:       t.view.webContents.isAudioMuted(),
    audible:     t.view.webContents.isCurrentlyAudible(),
    pairId:      (() => { const p = getPairOf(t.id); return p ? splitPairs.indexOf(p) : null; })(),
    pairLeft:    (() => { const p = getPairOf(t.id); return p ? t.id === p.leftId : false; })(),
    pairPaused:  (() => { const p = getPairOf(t.id); return p ? p.paused : false; })(),
    // legacy flags kept for any remaining references
    split:       (() => { const p = getPairOf(t.id); return p ? t.id === p.rightId : false; })(),
    splitLeft:   (() => { const p = getPairOf(t.id); return p ? t.id === p.leftId : false; })(),
    splitPaused: (() => { const p = getPairOf(t.id); return p ? p.paused : false; })(),
    parentId:    t.parentId || null,
    parentTitle: t.parentId && tabs[t.parentId]
                   ? (tabs[t.parentId].view.webContents.getTitle() || null)
                   : null,
    };
  });
  notifyChrome('tabs-updated', list);
}

// ─────────────────────────────────────────────────────────────────────────────
// IPC handlers (called by the chrome renderer via preload.js)
// ─────────────────────────────────────────────────────────────────────────────

function registerIPC() {
  // New tab
  ipcMain.handle('new-tab', (_, url) => {
    return createTab(url || NEW_TAB_URL, mainWin, activeTabId);
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

  // Close split view (sent from the chrome renderer close-split button)
  ipcMain.on('close-split', () => exitSplit());

  // Swap left/right sides of a split pair (drag-to-swap from the tab bar pill)
  ipcMain.handle('swap-split-sides', (_, tabId) => {
    const pair = getPairOf(tabId);
    if (!pair || pair.paused) return;
    const tmp = pair.leftId; pair.leftId = pair.rightId; pair.rightId = tmp;
    activeTabId = pair.leftId;
    const lt = tabs[pair.leftId]; const rt = tabs[pair.rightId];
    if (lt && rt) {
      mainWin.removeBrowserView(lt.view); mainWin.removeBrowserView(rt.view);
      mainWin.addBrowserView(lt.view);    mainWin.addBrowserView(rt.view);
      lt.view.setBounds(tabBounds(mainWin));
      rt.view.setBounds(splitRightBounds(mainWin));
    }
    notifyTabsUpdated();
  });

  // Reorder tabs by drag-and-drop
  ipcMain.handle('reorder-tab', (_, dragId, targetId) => {
    // Admin Hub is always pinned at position 0 — it cannot be moved
    const adminId  = _existingAdminHubId();
    if (dragId === adminId) return;

    const from     = tabOrder.indexOf(dragId);
    let   to       = tabOrder.indexOf(targetId);
    if (from === -1 || to === -1) return;

    // Clamp: never allow a tab to be placed before the Admin Hub
    const adminPos = adminId ? tabOrder.indexOf(adminId) : -1;
    if (adminPos !== -1 && to <= adminPos) to = adminPos + 1;

    if (from === to) return;
    tabOrder.splice(from, 1);
    tabOrder.splice(to, 0, dragId);
    notifyTabsUpdated();
  });

  // Right-click context menu on a tab — shown as a native OS menu so it
  // renders above all BrowserViews (HTML menus would be hidden under them).
  ipcMain.handle('show-tab-ctx', (_, { tabId, tabUrl }) => {
    const wc         = tabs[tabId]?.view.webContents;
    const isMuted    = wc?.isAudioMuted() ?? false;
    const isSplit    = getPairOf(tabId) !== null;
    const isActive   = activeTabId === tabId;
    const isAdminHub = (tabUrl || '').startsWith(GATEWAY_ORIGIN + '/ui/');
    const items = [
      { label: 'New Tab',       click: () => createTab() },
      ...(!isAdminHub ? [{ label: 'Duplicate Tab', click: () => createTab(wc?.getURL() || tabUrl || NEW_TAB_URL) }] : []),
      { type: 'separator' },
      ...(!isAdminHub ? [{
        label: isMuted ? '🔊 Réactiver le son' : '🔇 Couper le son',
        click: () => {
          if (!wc) return;
          wc.setAudioMuted(!isMuted);
          notifyChrome('tab-muted', { id: tabId, muted: !isMuted });
          notifyTabsUpdated();
        },
      }] : []),
      // Split: close the pair this tab belongs to; show "fractionner" for any unpaired inactive tab
      ...(getPairOf(tabId)
        ? [{ label: '⊟ Fermer la vue fractionnée', click: () => exitSplitForTab(tabId) }]
        : (!isActive
            ? [{ label: '⊟ Vue fractionnée', click: () => enterSplit(tabId) }]
            : [])),
      { type: 'separator' },
      ...(!isAdminHub ? [{
        label: 'Mettre en onglet inactif',
        click: () => {
          const tab = tabs[tabId];
          if (!tab) return;
          const wc2 = tab.view.webContents;
          wc2.capturePage().then(img => {
            tabPreviews[tabId] = img.resize({ width: 320, height: 200 }).toDataURL();
          }).catch(() => {});
          notifyChrome('group-tab', {
            id:      tabId,
            url:     wc2.getURL()   || tabUrl || '',
            title:   wc2.getTitle() || '',
            favicon: null,
          });
        },
      }] : []),
      { type: 'separator' },
      ...(!isAdminHub ? [{ label: 'Close Tab', click: () => closeTab(tabId) }] : []),
    ];
    Menu.buildFromTemplate(items).popup({ window: mainWin });
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
    const existing = _existingAdminHubId();
    if (existing !== null) {
      switchTab(existing);
    } else {
      tabs[activeTabId]?.view.webContents.loadURL(HOME_URL);
    }
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

  // ── Settings ─────────────────────────────────────────────────────────────
  ipcMain.handle('get-settings', () => loadSettings());
  ipcMain.handle('save-settings', (_, s) => {
    saveSettingsData(s);
    // Update NEW_TAB_URL live so next Ctrl+T uses the new value immediately
    NEW_TAB_URL = _newtabToUrl(s);
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

  // ── Inactive-tabs popup (custom BrowserWindow — renders above BrowserViews) ───
  let _tabsPopup = null;

  function _closeTabsPopup() {
    if (_tabsPopup && !_tabsPopup.isDestroyed()) { _tabsPopup.close(); }
    _tabsPopup = null;
  }

  ipcMain.handle('show-inactive-tabs-popup', (_, { tabs, btnRect }) => {
    _closeTabsPopup();
    if (!tabs || tabs.length === 0) return;

    // Attach stored screenshots
    const tabsWithPreviews = tabs.map(g => ({
      ...g,
      preview: tabPreviews[Number(g.id)] || null,
    }));

    const popupW = 300;
    const rowH   = 42;
    const footerH = 34;
    const popupH  = Math.min(tabs.length * rowH + footerH + 8, 490);

    // Position below the button, clamp to screen
    const display = screen.getDisplayNearestPoint({ x: btnRect.screenX, y: btnRect.screenY });
    const { x: sx, y: sy, width: sw, height: sh } = display.bounds;
    let px = Math.round(btnRect.screenX);
    let py = Math.round(btnRect.screenY + btnRect.height);
    if (px + popupW > sx + sw) px = sx + sw - popupW - 4;
    if (py + popupH > sy + sh) py = Math.round(btnRect.screenY) - popupH;

    _tabsPopup = new BrowserWindow({
      x: px, y: py,
      width: popupW, height: popupH,
      frame: false, transparent: false,
      skipTaskbar: true, alwaysOnTop: true,
      resizable: false, movable: false,
      webPreferences: { nodeIntegration: true, contextIsolation: false },
    });
    _tabsPopup.loadFile(path.join(__dirname, 'src', 'inactive-tabs-popup.html'));
    _tabsPopup.once('ready-to-show', () => {
      if (!_tabsPopup || _tabsPopup.isDestroyed()) return;
      _tabsPopup.show();
      _tabsPopup.webContents.send('init-tabs', tabsWithPreviews);
    });
    _tabsPopup.on('blur', () => setTimeout(_closeTabsPopup, 150));
  });

  ipcMain.on('popup-restore', (_, id) => {
    _closeTabsPopup();
    mainWin.webContents.send('restore-inactive-tab', id);
  });
  ipcMain.on('popup-remove', (_, id) => {
    mainWin.webContents.send('remove-inactive-tab', id);
  });
  ipcMain.on('popup-restore-all', () => {
    _closeTabsPopup();
    mainWin.webContents.send('restore-all-inactive-tabs');
  });
  ipcMain.on('popup-clear', () => {
    _closeTabsPopup();
    mainWin.webContents.send('clear-inactive-tabs');
  });
  ipcMain.on('popup-close', _closeTabsPopup);

  // ── Tab hover preview ─────────────────────────────────────────────
  // Returns the cached screenshot (data: URL) for a given tabId.
  // Screenshots are captured on did-finish-load and on switchTab departure.
  ipcMain.handle('get-tab-preview', (_, tabId) => tabPreviews[Number(tabId)] || null);

  // ── Tab hover preview floating window ───────────────────────────
  // (_hoverWin and _closeHoverWin are module-scope — see top of file)

  ipcMain.handle('show-tab-preview', (_, { tabId, screenX, screenY, title, url, favicon }) => {
    _closeHoverWin();
    const preview = tabPreviews[Number(tabId)] || null;
    const hasImg  = !!preview;
    const W = 220;
    const H = hasImg ? 192 : 126;

    let domain = '';
    try { domain = new URL(url || '').hostname.replace(/^www\./, ''); } catch {}

    const disp = screen.getDisplayNearestPoint({ x: screenX, y: screenY });
    const { x: sx, width: sw, height: sh, y: sy } = disp.bounds;
    let px = Math.round(screenX);
    let py = Math.round(screenY);
    if (px + W > sx + sw) px = sx + sw - W - 4;
    if (py + H > sy + sh) py = py - H - 4;

    _hoverWin = new BrowserWindow({
      x: px, y: py, width: W, height: H,
      frame: false, transparent: true,
      skipTaskbar: true, alwaysOnTop: true,
      focusable: false, resizable: false, movable: false,
      webPreferences: { nodeIntegration: true, contextIsolation: false },
    });
    _hoverWin.setIgnoreMouseEvents(true);
    _hoverWin.loadFile(path.join(__dirname, 'src', 'tab-preview-card.html'));
    _hoverWin.once('ready-to-show', () => {
      if (!_hoverWin || _hoverWin.isDestroyed()) return;
      _hoverWin.showInactive();
      _hoverWin.webContents.send('init-preview', { title, url, favicon, domain, preview });
    });
  });

  ipcMain.on('hide-tab-preview', _closeHoverWin);

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
    const ap = getActivePair();
    if (ap) {
      if (tabs[ap.leftId])  tabs[ap.leftId].view.setBounds(tabBounds(mainWin));
      if (tabs[ap.rightId]) tabs[ap.rightId].view.setBounds(splitRightBounds(mainWin));
    } else {
      const tab = tabs[activeTabId];
      if (tab) tab.view.setBounds(tabBounds(mainWin));
    }
    if (sidebarOpen && sidebarView) sidebarView.setBounds(sidebarBounds(mainWin));
  }

  mainWin.on('resize',            syncBounds);
  mainWin.on('enter-full-screen', syncBounds);
  mainWin.on('leave-full-screen', syncBounds);
  // restore fires when the window comes back from minimized — resize does NOT
  // always fire in that case, so the BrowserView would keep its collapsed bounds.
  mainWin.on('restore',           syncBounds);

  mainWin.on('closed',  () => { _closeHoverWin(); mainWin = null; });
  mainWin.on('blur',    () => _closeHoverWin());

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

  // ── Override User-Agent on the default session ──────────────────────────
  // Replaces "Electron/29.x.x" with a clean Chrome 122 UA so Google, Cloudflare
  // and reCAPTCHA don't fingerprint us as automation.
  session.defaultSession.setUserAgent(CHROME_UA);

  // Grant media / DRM / notification permissions automatically so YouTube
  // and other media sites never get silently blocked.
  session.defaultSession.setPermissionRequestHandler((wc, permission, callback) => {
    // Deny only truly dangerous permissions; grant everything else
    const denied = ['openExternal'];
    callback(!denied.includes(permission));
  });
  session.defaultSession.setPermissionCheckHandler((wc, permission) => {
    const denied = ['openExternal'];
    return !denied.includes(permission);
  });

  // Strip the Sec-CH-UA hint that also reveals Electron
  session.defaultSession.webRequest.onBeforeSendHeaders((details, callback) => {
    const h = details.requestHeaders;
    h['User-Agent']          = CHROME_UA;
    h['Sec-CH-UA']           = '"Not A(Brand";v="99", "Chromium";v="122", "Google Chrome";v="122"';
    h['Sec-CH-UA-Mobile']    = '?0';
    h['Sec-CH-UA-Platform']  = '"Windows"';
    h['Accept-Language']     = 'fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7';
    delete h['X-Electron-Version'];
    delete h['X-Electron-App-Version'];
    callback({ cancel: false, requestHeaders: h });
  });

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

// Shut down gateway before quitting; also destroy any floating preview window
app.on('before-quit', () => {
  _closeHoverWin();
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
