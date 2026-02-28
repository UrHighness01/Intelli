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
  webContents: electronWebContents,
} = require('electron');
const path   = require('path');
const fs     = require('fs');
const crypto = require('crypto');
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
const BOOKMARKS_BAR_H   = 30;   // px — bookmark bar (shown/hidden)
const SIDEBAR_WIDTH     = 340;  // px — admin hub sidebar panel
const PANEL_WIDTH       = 360;  // px — right-side overlay panels (bookmarks, history, etc.)
const GATEWAY_READY_MS  = 15000;
const HEALTH_POLL_MS    = 400;
// Resolved at startup from settings.json; updated live via IPC.
const _startSettings = loadSettings();
let NEW_TAB_URL = _newtabToUrl(_startSettings);
const SETUP_URL         = `${GATEWAY_ORIGIN}/ui/setup.html`;

// ─── Browser language → Accept-Language header map ───────────────────────────
// Changing any entry live (via save-settings IPC) takes effect on the next
// request — no restart required.
const LANG_HEADERS = {
  'en':    'en-US,en;q=0.9',
  'fr':    'fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7',
  'es':    'es-ES,es;q=0.9,en-US;q=0.8,en;q=0.7',
  'de':    'de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7',
  'it':    'it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7',
  'pt':    'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7',
  'nl':    'nl-NL,nl;q=0.9,en-US;q=0.8,en;q=0.7',
  'pl':    'pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7',
  'ru':    'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
  'ja':    'ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7',
  'ko':    'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
  'zh-CN': 'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7',
  'zh-TW': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
  'ar':    'ar;q=0.9,en-US;q=0.8,en;q=0.7',
  'tr':    'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7',
};
let _acceptLanguage = LANG_HEADERS[_startSettings.language] || LANG_HEADERS['en'];

// ─── Anti-fingerprint — must be set before app is ready ─────────────────────
// Removes the flags that tell Google reCAPTCHA / bot-detection we are Electron.
app.commandLine.appendSwitch('disable-blink-features', 'AutomationControlled');
app.commandLine.appendSwitch('disable-features', 'AutomationControlled,HardwareMediaKeyHandling,MediaFoundationVideoCapture');
app.commandLine.appendSwitch('no-first-run');
app.commandLine.appendSwitch('no-default-browser-check');
app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required');
app.commandLine.appendSwitch('force-color-profile', 'srgb');

// Build a platform-aware UA so the OS in the UA matches the real OS.
// Spoofing Windows on Linux creates a detectable platform mismatch that
// bot-detection systems (X.com, Google, Cloudflare) catch immediately.
const _osPlatform = process.platform;
const CHROME_UA = _osPlatform === 'darwin'
  ? 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.6834.110 Safari/537.36'
  : _osPlatform === 'win32'
    ? 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.6834.110 Safari/537.36'
    : 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.6834.110 Safari/537.36';
const SEC_CH_UA_PLATFORM = _osPlatform === 'darwin' ? '"macOS"' : _osPlatform === 'win32' ? '"Windows"' : '"Linux"';

// ─── Anti-detect script injected into every page via executeJavaScript() ──────
// Runs in the main world on dom-ready / did-navigate / did-navigate-in-page.
// contextIsolation:true keeps Node globals (process, require, Buffer) out of
// the page world entirely — no need to delete them here.
// navigator.webdriver is already undefined via disable-blink-features=AutomationControlled.
const _navPlatform   = _osPlatform === 'darwin' ? 'MacIntel' : _osPlatform === 'win32' ? 'Win32' : 'Linux x86_64';
const _webglRenderer = _osPlatform === 'darwin'
  ? 'ANGLE (Intel, ANGLE Metal Renderer: Intel(R) Iris(TM) Plus Graphics, Unspecified Version)'
  : _osPlatform === 'win32'
    ? 'ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)'
    : 'ANGLE (Intel, Mesa Intel(R) UHD Graphics 620 (KBL GT2), OpenGL 4.6)';
// Path where we write the generated antidetect preload at startup.
// Placed in the OS temp dir so it survives restarts but is writable.
const _ANTIDETECT_PRELOAD_PATH = path.join(app.getPath('temp'), 'intelli-antidetect-preload.js');
// Separate direct-execution preload for the Google auth popup (contextIsolation:false).
// Runs ANTIDETECT_JS straight in the main world — no <script> element trick needed.
const _POPUP_PRELOAD_PATH = path.join(app.getPath('temp'), 'intelli-popup-preload.js');

const ANTIDETECT_JS = `(function(){
  // 1. navigator.webdriver
  try{Object.defineProperty(navigator,'webdriver',{get:()=>undefined,configurable:true});}catch(_){}
  // 2. navigator.platform — must match real OS
  try{Object.defineProperty(navigator,'platform',{get:()=>'${_navPlatform}',configurable:true});}catch(_){}
  // 3. navigator.languages / language — match Accept-Language header
  try{Object.defineProperty(navigator,'languages',{get:()=>['en-US','en'],configurable:true});}catch(_){}
  try{Object.defineProperty(navigator,'language',{get:()=>'en-US',configurable:true});}catch(_){}
  // 4. navigator.hardwareConcurrency + deviceMemory
  try{Object.defineProperty(navigator,'hardwareConcurrency',{get:()=>8,configurable:true});}catch(_){}
  try{Object.defineProperty(navigator,'deviceMemory',{get:()=>8,configurable:true});}catch(_){}
  // 5. navigator.plugins — Chrome PDF viewer list
  try{
    const fp=[{name:'PDF Viewer',filename:'internal-pdf-viewer',description:'Portable Document Format'},{name:'Chrome PDF Viewer',filename:'internal-pdf-viewer',description:''},{name:'Chromium PDF Viewer',filename:'internal-pdf-viewer',description:''}];
    const arr=fp.map(p=>{const pl=Object.create(Plugin?Plugin.prototype:{});['name','filename','description'].forEach(k=>Object.defineProperty(pl,k,{value:p[k],enumerable:true}));return pl;});
    arr.item=i=>arr[i];arr.namedItem=n=>arr.find(p=>p.name===n)||null;arr.refresh=()=>{};
    Object.defineProperty(arr,'length',{value:fp.length});
    Object.defineProperty(navigator,'plugins',{get:()=>arr,configurable:true});
  }catch(_){}
  // 6. document.hasFocus — BrowserView without focus returns false
  try{Object.defineProperty(document,'hasFocus',{value:()=>true,writable:true,configurable:true});}catch(_){}
  // 7. window.chrome — full object real Chrome exposes
  try{
    if(!window.__intelli_spoofed__){
      const t0=performance.timeOrigin+performance.now();
      window.chrome=window.chrome||{};
      window.chrome.csi=()=>({startE:t0,onloadT:t0+120,pageT:t0+150,tran:15});
      window.chrome.loadTimes=()=>({commitLoadTime:t0/1000,connectionInfo:'h2',finishDocumentLoadTime:(t0+120)/1000,finishLoadTime:(t0+150)/1000,firstPaintAfterLoadTime:0,firstPaintTime:(t0+80)/1000,navigationType:'Other',npnNegotiatedProtocol:'h2',requestTime:t0/1000,startLoadTime:t0/1000,wasAlternateProtocolAvailable:false,wasFetchedViaSpdy:true,wasNpnNegotiated:true});
      if(!window.chrome.runtime)window.chrome.runtime={id:undefined,connect:()=>{},sendMessage:()=>{},onConnect:{addListener:()=>{},removeListener:()=>{}},onMessage:{addListener:()=>{},removeListener:()=>{}}};
      if(!window.chrome.app)window.chrome.app={isInstalled:false,getDetails:()=>null,getIsInstalled:()=>false,runningState:()=>'cannot_run'};
      window.__intelli_spoofed__=true;
    }
  }catch(_){}
  // 8. WebGL renderer — D3D11 string on Linux = instant automation flag
  try{
    const patchGL=Ctx=>{
      if(!Ctx)return;
      const orig=Ctx.prototype.getParameter;
      Ctx.prototype.getParameter=function(p){
        if(p===37445)return'Google Inc. (Intel)';
        if(p===37446)return'${_webglRenderer}';
        return orig.call(this,p);
      };
    };
    patchGL(WebGLRenderingContext);patchGL(WebGL2RenderingContext);
  }catch(_){}
  // 9. Permissions API
  try{
    const orig=navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.__proto__.query=p=>{
      if(p.name==='notifications')return Promise.resolve({state:'default',onchange:null});
      if(['camera','microphone','geolocation'].includes(p.name))return Promise.resolve({state:'prompt',onchange:null});
      return orig(p).catch(()=>Promise.resolve({state:'prompt',onchange:null}));
    };
  }catch(_){}
  // 10. outerWidth / outerHeight
  try{Object.defineProperty(window,'outerWidth',{get:()=>window.innerWidth,configurable:true});}catch(_){}
  try{Object.defineProperty(window,'outerHeight',{get:()=>window.innerHeight+74,configurable:true});}catch(_){}
  // 11. chrome.webstore — Google uses its presence to detect real Chrome
  try{
    if(window.chrome&&!window.chrome.webstore){
      window.chrome.webstore={
        onInstallStageChanged:{addListener:()=>{},removeListener:()=>{}},
        onDownloadProgress:{addListener:()=>{},removeListener:()=>{}},
        install:(u,ok,fail)=>{if(fail)fail('This feature requires the Chrome Web Store');}
      };
    }
  }catch(_){}
  // 12. navigator.userAgentData — exposes "Electron" brand if not overridden
  try{
    const _brands=[{brand:'Not A(Brand',version:'8'},{brand:'Chromium',version:'132'},{brand:'Google Chrome',version:'132'}];
    const _fullBrands=[{brand:'Not A(Brand',version:'8.0.0.0'},{brand:'Chromium',version:'132.0.6834.110'},{brand:'Google Chrome',version:'132.0.6834.110'}];
    const _platform='${_osPlatform === 'win32' ? 'Windows' : _osPlatform === 'darwin' ? 'macOS' : 'Linux'}';
    const _uad={
      brands:_brands,
      mobile:false,
      platform:_platform,
      getHighEntropyValues(hints){
        const r={brands:_brands,mobile:false,platform:_platform,architecture:'x86',bitness:'64',model:'',platformVersion:'6.1.0',fullVersionList:_fullBrands,uaFullVersion:'132.0.6834.110',wow64:false};
        return Promise.resolve(Object.fromEntries(hints.map(h=>[h,r[h]])));
      },
      toJSON(){return{brands:_brands,mobile:false,platform:_platform};}
    };
    Object.defineProperty(navigator,'userAgentData',{get:()=>_uad,configurable:true});
  }catch(_){}
})();`;

// ─── Google Sign-in popup ────────────────────────────────────────────────────────
// Google detects Electron BrowserView as an "insecure embedded webview" and
// blocks sign-in.  Opening a standalone BrowserWindow with the same session
// makes it indistinguishable from a normal Chrome popup window.
// Cookies are shared (same defaultSession) so auth persists to all tabs.

let _googleAuthWin = null;

const GOOGLE_AUTH_HOSTS = [
  'accounts.google.com',
  'accounts.youtube.com',
];

function _isGoogleAuthUrl(urlStr) {
  try {
    const u = new URL(urlStr);
    // Any URL on accounts.google.com or accounts.youtube.com is a Google auth page
    return GOOGLE_AUTH_HOSTS.includes(u.hostname);
  } catch { return false; }
}

function openGoogleAuthPopup(url) {
  if (_googleAuthWin && !_googleAuthWin.isDestroyed()) {
    _googleAuthWin.loadURL(url);
    _googleAuthWin.focus();
    return;
  }
  _googleAuthWin = new BrowserWindow({
    width:           520,
    height:          680,
    title:           'Sign in with Google',
    autoHideMenuBar: true,
    webPreferences: {
      session:          session.defaultSession,   // shared cookies!
      nodeIntegration:  false,
      // contextIsolation:false — preload runs directly in the main world,
      // so ANTIDETECT_JS patches navigator/window.chrome BEFORE any page
      // inline script executes.  No <script> element trick, no CSP issue.
      contextIsolation: false,
      preload: _POPUP_PRELOAD_PATH,
    },
  });
  _googleAuthWin.setMenuBarVisibility(false);
  _googleAuthWin.webContents.setUserAgent(CHROME_UA);
  // dom-ready injection kept as fallback in case preload file isn't ready
  _googleAuthWin.webContents.on('dom-ready', () => {
    if (!_googleAuthWin?.isDestroyed()) {
      _googleAuthWin.webContents.executeJavaScript(ANTIDETECT_JS).catch(() => {});
    }
  });
  // Close popup once Google redirects away from accounts (sign-in done)
  // or if it navigates to a non-Google page (OAuth callback)
  _googleAuthWin.webContents.on('will-navigate', (_, navUrl) => {
    try {
      const u = new URL(navUrl);
      if (!u.hostname.endsWith('.google.com') && !u.hostname.endsWith('.youtube.com')) {
        // OAuth callback going elsewhere — open in Intelli tab + close popup
        setImmediate(() => createTab(navUrl));
        _googleAuthWin?.close();
      }
    } catch (_) {}
  });
  _googleAuthWin.on('closed', () => { _googleAuthWin = null; });
  _googleAuthWin.loadURL(url);
  _googleAuthWin.show();
}

// ─── Extension context-menu bridge ──────────────────────────────────────────
// Tracks background/popup webContents for loaded extensions so we can
// read their chrome.contextMenus registrations and dispatch click events.

/** extId → webContents (background page or popup page) */
const _extBgPages = new Map();
/** extId → [{id, title, contexts, enabled, parentId}] */
const _extContextMenuCache = new Map();

// Injected into every chrome-extension:// webContents after load.
// Monkey-patches chrome.contextMenus.create/update/remove so registrations
// are captured in window._intelliCtxItems (readable via executeJavaScript).
const _EXT_CTX_BRIDGE = `(function(){
  if(window.__intelliCtxBridge__)return;
  window.__intelliCtxBridge__=true;
  window._intelliCtxItems={};
  function _hook(){
    if(!window.chrome||!chrome.contextMenus)return;
    if(chrome.contextMenus.__hooked__)return;
    chrome.contextMenus.__hooked__=true;
    var _c=chrome.contextMenus.create.bind(chrome.contextMenus);
    chrome.contextMenus.create=function(p,cb){
      var k=String(p.id||Object.keys(window._intelliCtxItems).length);
      window._intelliCtxItems[k]=Object.assign({},p,{id:k});
      return _c(p,cb);
    };
    var _u=chrome.contextMenus.update.bind(chrome.contextMenus);
    chrome.contextMenus.update=function(id,changes,cb){
      if(window._intelliCtxItems[id])Object.assign(window._intelliCtxItems[id],changes);
      return _u(id,changes,cb);
    };
    var _r=chrome.contextMenus.remove.bind(chrome.contextMenus);
    chrome.contextMenus.remove=function(id,cb){
      delete window._intelliCtxItems[id];
      return _r(id,cb);
    };
    chrome.contextMenus.removeAll=function(cb){
      window._intelliCtxItems={};
      return _r&&typeof _r==='function'?_r(cb):undefined;
    };
  }
  // Try immediately and at staggered delays for lazy-init extensions
  _hook();
  setTimeout(_hook,500);
  setTimeout(_hook,1500);
})();`;

async function _fetchExtContextMenuItems(extId, wc) {
  if (!wc || wc.isDestroyed()) return;
  try {
    const raw = await wc.executeJavaScript('JSON.stringify(window._intelliCtxItems||{})');
    const obj = JSON.parse(raw || '{}');
    const items = Object.entries(obj).map(([id, p]) => ({
      id:       String(p.id !== undefined ? p.id : id),
      title:    String(p.title || id),
      contexts: Array.isArray(p.contexts) ? p.contexts : ['all'],
      enabled:  p.enabled !== false,
      parentId: p.parentId != null ? String(p.parentId) : null,
    }));
    if (items.length) _extContextMenuCache.set(extId, items);
  } catch (_) {}
}

async function _refreshExtContextMenus() {
  // Also scan all live webContents for any extension pages not yet tracked
  try {
    const all = electronWebContents.getAllWebContents();
    for (const wc of all) {
      if (wc.isDestroyed()) continue;
      const url = wc.getURL();
      const m = url.match(/^chrome-extension:\/\/([a-z]{32})\//);
      if (m && !_extBgPages.has(m[1])) {
        _extBgPages.set(m[1], wc);
        try { await wc.executeJavaScript(_EXT_CTX_BRIDGE); } catch (_) {}
        setTimeout(() => _fetchExtContextMenuItems(m[1], wc), 800);
      }
    }
  } catch (_) {}
  for (const [extId, wc] of _extBgPages) {
    if (wc.isDestroyed()) { _extBgPages.delete(extId); continue; }
    await _fetchExtContextMenuItems(extId, wc);
  }
}

let _ctxRefreshTimer = null;
function _scheduleCtxMenuRefresh(delay = 2000) {
  clearTimeout(_ctxRefreshTimer);
  _ctxRefreshTimer = setTimeout(async function _tick() {
    await _refreshExtContextMenus();
    _ctxRefreshTimer = setTimeout(_tick, 8000);
  }, delay);
}

function _dispatchExtCtxClick(extId, itemId, params) {
  const wc = _extBgPages.get(extId);
  if (!wc || wc.isDestroyed()) return;
  const info = {
    menuItemId:    itemId,
    selectionText: params.selectionText || '',
    linkUrl:       params.linkURL       || '',
    srcUrl:        params.srcURL        || '',
    pageUrl:       params.pageURL       || '',
    frameUrl:      params.frameURL      || '',
    editable:      params.isEditable    || false,
  };
  wc.executeJavaScript(`
    (function(){
      try{
        if(chrome&&chrome.contextMenus&&chrome.contextMenus.onClicked){
          chrome.contextMenus.onClicked.dispatch(${JSON.stringify(info)},{url:${JSON.stringify(params.pageURL||'')}});
        }
      }catch(e){}
    })()
  `).catch(()=>{});
}

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
let tabOrder    = [];   // ordered list of tab IDs — drives the tab bar order
let activeTabId = null;
let splitPairs  = [];  // [{ leftId, rightId, paused }] — all active split pairs
let nextTabId   = 1;
const tabPreviews = {};  // tabId → data:URL screenshot (captured when tab is grouped)
let _hoverWin     = null;  // floating preview BrowserWindow (module-scope for cleanup)
function _closeHoverWin() {
  const w = _hoverWin;
  _hoverWin = null;          // null first so any re-entrant calls are no-ops
  if (w && !w.isDestroyed()) w.destroy();
}

// ─── User-data paths ──────────────────────────────────────────────────────────
function userDataFile(name) {
  return path.join(app.getPath('userData'), name);
}

// ─── Chrome Extension storage ─────────────────────────────────────────────────
// Extensions are stored unpacked in <userData>/chrome-extensions/<uuid>/
// The index file tracks metadata: [{id, name, version, path, enabled}]
const CHROME_EXT_DIR  = () => path.join(app.getPath('userData'), 'chrome-extensions');
const CHROME_EXT_FILE = () => userDataFile('chrome-extensions.json');

function _readExtIndex() {
  try { return JSON.parse(fs.readFileSync(CHROME_EXT_FILE(), 'utf8')); }
  catch { return []; }
}
function _writeExtIndex(list) {
  fs.writeFileSync(CHROME_EXT_FILE(), JSON.stringify(list, null, 2));
}

/** Extract a CRX3 file to a fresh subdirectory. Returns the extracted dir path. */
async function _extractCrx(crxPath) {
  const buf = fs.readFileSync(crxPath);
  const magic = buf.toString('ascii', 0, 4);
  if (magic !== 'Cr24') throw new Error('Not a valid CRX file (bad magic bytes)');
  const version = buf.readUInt32LE(4);
  if (version !== 3) throw new Error(`Unsupported CRX version ${version} (only CRX3 is supported)`);
  const headerSize = buf.readUInt32LE(8);
  const zipOffset  = 12 + headerSize;
  const zipBuf     = buf.slice(zipOffset);

  // Write the embedded ZIP to a temp file then extract it
  const uuid    = crypto.randomUUID();
  const tmpZip  = path.join(os.tmpdir(), `intelli-crx-${uuid}.zip`);
  fs.writeFileSync(tmpZip, zipBuf);

  const destDir = path.join(CHROME_EXT_DIR(), uuid);
  fs.mkdirSync(destDir, { recursive: true });

  await new Promise((resolve, reject) => {
    // `unzip` is standard on Linux/macOS; use PowerShell on Windows
    const cmd  = process.platform === 'win32' ? 'powershell' : 'unzip';
    const args = process.platform === 'win32'
      ? ['-NoProfile', '-Command', `Expand-Archive -Force -Path '${tmpZip}' -DestinationPath '${destDir}'`]
      : ['-o', tmpZip, '-d', destDir];
    execFile(cmd, args, (err) => {
      try { fs.unlinkSync(tmpZip); } catch (_) {}
      if (err) reject(new Error(`Failed to extract CRX: ${err.message}`));
      else resolve();
    });
  });
  return destDir;
}

/** Load all saved (enabled) extensions into the default session. */
async function loadSavedExtensions() {
  const list = _readExtIndex();
  for (const ext of list) {
    if (!ext.enabled) continue;
    try {
      await session.defaultSession.loadExtension(ext.path, { allowFileAccess: true });
      console.log(`[ext] loaded "${ext.name}" (${ext.id})`);
    } catch (e) {
      console.warn(`[ext] failed to load "${ext.name}": ${e.message}`);
    }
  }
  // Allow extension background pages to finish registering contextMenus items
  if (list.some(e => e.enabled)) _scheduleCtxMenuRefresh(5000);
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
let bookmarksBarVisible = (_startSettings.bookmarksBar !== false); // default true

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

    // Kill any stale process already bound to our port (happens when Electron
    // is quit and restarted before the previous gateway process fully exits).
    // This ensures our new process gets the port AND that our BOOTSTRAP_SECRET
    // matches the running gateway (otherwise the bootstrap-token call would 403).
    try {
      const { execSync } = require('child_process');
      if (process.platform === 'win32') {
        execSync(`FOR /F "tokens=5" %P IN ('netstat -ano ^| findstr :${GATEWAY_PORT}') DO taskkill /PID %P /F`, { stdio: 'ignore', shell: true });
      } else {
        // fuser -k kills any process on the port; sleep gives the OS time to release it
        execSync(`fuser -k ${GATEWAY_PORT}/tcp 2>/dev/null; sleep 0.5; true`, { stdio: 'ignore', shell: true });
      }
    } catch (_) { /* ignore — port may not have been in use */ }

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

function chromeH() {
  return CHROME_HEIGHT + (bookmarksBarVisible ? BOOKMARKS_BAR_H : 0);
}

function tabBounds(win) {
  const [w, h] = win.getContentSize();
  const sideW  = sidebarOpen  ? SIDEBAR_WIDTH : 0;
  const panelW = panelVisible ? PANEL_WIDTH   : 0;
  const totalW = Math.max(0, w - sideW - panelW);
  const ch = chromeH();
  if (getActivePair() !== null) {
    return { x: 0, y: ch, width: Math.floor(totalW / 2) - 1, height: Math.max(0, h - ch) };
  }
  return { x: 0, y: ch, width: totalW, height: Math.max(0, h - ch) };
}

/** Bounds for the admin-hub sidebar BrowserView when open. */
function sidebarBounds(win) {
  const [w, h] = win.getContentSize();
  const ch = chromeH();
  return { x: Math.max(0, w - SIDEBAR_WIDTH), y: ch, width: SIDEBAR_WIDTH, height: Math.max(0, h - ch) };
}

/** Bounds for the right-hand BrowserView in split mode. */
function splitRightBounds(win) {
  const [w, h] = win.getContentSize();
  const sideW  = sidebarOpen  ? SIDEBAR_WIDTH : 0;
  const panelW = panelVisible ? PANEL_WIDTH   : 0;
  const totalW = Math.max(0, w - sideW - panelW);
  const half   = Math.floor(totalW / 2);
  const ch = chromeH();
  return { x: half + 1, y: ch, width: totalW - half - 1, height: Math.max(0, h - ch) };
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
function createTab(url = NEW_TAB_URL, win = mainWin, fromTabId = null) {
  // The Admin Hub index page is a singleton — if one is already open, switch to it.
  // Other /ui/ sub-pages (chat, canvas, memory, etc.) are NOT singletons and should
  // always open as a fresh tab (or switch to themselves if the same exact URL is open).
  if (url.startsWith(GATEWAY_ORIGIN + '/ui/')) {
    const isHubIndex = /\/ui\/?(?:index\.html)?(?:[?#]|$)/.test(url);
    if (isHubIndex) {
      // Switch to the existing hub tab if any
      const existing = _existingAdminHubId();
      if (existing !== null) { switchTab(existing, win); return existing; }
    } else {
      // For other admin pages, only deduplicate against the exact same URL
      const existing = Object.values(tabs).find(t =>
        !t.view.webContents.isDestroyed() &&
        (t.view.webContents.getURL() || '') === url
      );
      if (existing) { switchTab(existing.id, win); return existing.id; }
    }
  }
  const id   = nextTabId++;
  const view = new BrowserView({
    webPreferences: {
      nodeIntegration:  false,
      contextIsolation: true,   // MUST be true — keeps Node globals out of page world
      sandbox:          true,   // full sandbox — no Node in page renderer
      webviewTag:       false,
      // No preload: with contextIsolation:true a preload runs in an isolated
      // context and cannot override page-visible navigator.* properties.
      // All anti-detect overrides run via executeJavaScript() (main world) below.
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

  // Navigation guard: block private IPs and dangerous schemes synchronously.
  // Both will-navigate (JS/link-initiated) AND will-redirect (HTTP 302s) are
  // intercepted so Google sign-in is caught regardless of how the redirect arrives.
  function _navGuard(event, navUrl) {
    if (_isGoogleAuthUrl(navUrl)) {
      event.preventDefault();
      openGoogleAuthPopup(navUrl);
      return;
    }
    const reason = _isBlockedURL(navUrl);
    if (reason) {
      event.preventDefault();
      const warningURL = `${GATEWAY_ORIGIN}/ui/index.html#blocked`;
      view.webContents.loadURL(warningURL);
      notifyChrome('nav-blocked', { id, url: navUrl, reason });
      console.warn(`[NavGuard] Blocked navigation to ${navUrl}: ${reason}`);
    }
  }
  view.webContents.on('will-navigate', _navGuard);
  view.webContents.on('will-redirect', _navGuard);

  view.webContents.on('did-navigate', (_, navUrl) => {
    if (activeTabId === id) {
      notifyChrome('url-changed', { id, url: navUrl });
      notifyChrome('nav-state', {
        id,
        canGoBack:    view.webContents.navigationHistory.canGoBack(),
        canGoForward: view.webContents.navigationHistory.canGoForward(),
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
        canGoBack:    view.webContents.navigationHistory.canGoBack(),
        canGoForward: view.webContents.navigationHistory.canGoForward(),
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
    if (view.webContents.navigationHistory.canGoBack())
      items.push({ label: 'Back',    click: () => view.webContents.navigationHistory.goBack() });
    if (view.webContents.navigationHistory.canGoForward())
      items.push({ label: 'Forward', click: () => view.webContents.navigationHistory.goForward() });
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

    // ── Extension context menus ───────────────────────────────────────────
    // Determine click context so we include only items that apply
    const _clickCtx = params.selectionText
      ? 'selection'
      : params.linkURL
        ? 'link'
        : (params.mediaType && params.mediaType !== 'none')
          ? params.mediaType  // 'image' | 'video' | 'audio'
          : 'page';

    const _enabledIds = new Set(_readExtIndex().filter(r => r.enabled).map(r => r.id));
    let _extItemsAdded = false;

    for (const [extId, extItems] of _extContextMenuCache) {
      if (!_enabledIds.has(extId)) continue;
      // Top-level items whose contexts match
      const topItems = extItems.filter(item => {
        if (!item.enabled || item.parentId) return false;
        const ctx = item.contexts;
        return ctx.includes('all') || ctx.includes(_clickCtx)
          || (_clickCtx === 'selection' && ctx.includes('selection'))
          || (_clickCtx === 'link'      && ctx.includes('link'))
          || (ctx.includes('editable')  && params.isEditable)
          || ctx.includes('page');
      });
      if (!topItems.length) continue;

      if (!_extItemsAdded) {
        items.push({ type: 'separator' });
        _extItemsAdded = true;
      }

      const extRec   = _readExtIndex().find(r => r.id === extId);
      const extLabel = extRec?.name || extId.slice(0, 10);

      // Build a submenu per extension if there are multiple items,
      // otherwise inline the single item directly
      if (topItems.length === 1) {
        const item  = topItems[0];
        const label = item.title.replace(/%s/g, params.selectionText || '');
        const children = extItems.filter(c => c.parentId === item.id && c.enabled);
        if (children.length) {
          items.push({
            label,
            submenu: children.map(c => ({
              label: c.title.replace(/%s/g, params.selectionText || ''),
              click: () => _dispatchExtCtxClick(extId, c.id, params),
            })),
          });
        } else {
          items.push({ label, click: () => _dispatchExtCtxClick(extId, item.id, params) });
        }
      } else {
        items.push({
          label: extLabel,
          submenu: topItems.map(item => {
            const label    = item.title.replace(/%s/g, params.selectionText || '');
            const children = extItems.filter(c => c.parentId === item.id && c.enabled);
            if (children.length) {
              return {
                label,
                submenu: children.map(c => ({
                  label: c.title.replace(/%s/g, params.selectionText || ''),
                  click: () => _dispatchExtCtxClick(extId, c.id, params),
                })),
              };
            }
            return { label, click: () => _dispatchExtCtxClick(extId, item.id, params) };
          }),
        });
      }
    }

    Menu.buildFromTemplate(items).popup({ window: mainWin });
  });
  // Single combined did-finish-load handler — consolidates admin-token injection,
  // addon re-injection (SPA-safe), and tab snapshot push so only one listener is
  // registered per webContents instead of three, reducing MaxListeners noise.
  view.webContents.on('did-finish-load', async () => {
    const url = view.webContents.getURL();

    // ── Admin-token injection (gateway UI pages only) ──────────────────────
    if (adminToken && url.startsWith(GATEWAY_ORIGIN + '/ui/')) {
      view.webContents.executeJavaScript(`
        (function(tok) {
          try { localStorage.setItem('gw_token', tok); } catch(e) {}
          if (typeof applyToken === 'function') {
            applyToken(tok);
            if (typeof loadPersonas === 'function') loadPersonas();
          }
        })(${JSON.stringify(adminToken)})
      `).catch(() => {});
      return; // nothing else applies to admin pages
    }

    // ── Below: non-gateway, active-tab-only operations ─────────────────────
    if (!url || url === 'about:blank') return;
    if (view !== tabs[activeTabId]?.view) return;

    // ── Addon re-injection (SPA-safe: 200 ms / 1 s / 3 s) ─────────────────
    // For SPAs (React, Vue, etc.) elements render AFTER did-finish-load, so
    // we re-run at staggered delays to ensure the addon catches late DOM.
    if (gatewayReady) {
      const _addonUrl = url;
      function _reInjectAddons(delay) {
        setTimeout(() => {
          // Guard: view may have navigated away during the delay
          const currentUrl = view.webContents.getURL();
          if (currentUrl !== _addonUrl) return;
          http.get(`${GATEWAY_ORIGIN}/tab/active-addons`, res => {
            let _addonData = '';
            res.on('data', d => { _addonData += d; });
            res.on('end', async () => {
              try {
                const addons = JSON.parse(_addonData);
                if (!Array.isArray(addons) || addons.length === 0) return;
                for (const addon of addons) {
                  if (addon.url_pattern && !currentUrl.includes(addon.url_pattern)) {
                    console.log(`[addon] skip "${addon.name}" — url_pattern "${addon.url_pattern}" not in ${currentUrl}`);
                    continue;
                  }
                  // Never inject addons into the Google auth popup
                  const _popWcId = _googleAuthWin && !_googleAuthWin.isDestroyed() ? _googleAuthWin.webContents.id : -1;
                  if (view.webContents.id === _popWcId) continue;
                  try {
                    // Execute addon.code_js directly — avoids CodeQL js/improper-code-sanitization (CWE-116).
                    await view.webContents.executeJavaScript(addon.code_js);
                    console.log(`[addon] re-injected "${addon.name}" (+${delay}ms) on ${currentUrl}`);
                  } catch (err) {
                    console.error(`[addon] re-inject "${addon.name}" CRASHED:`, err.message);
                  }
                }
              } catch (e) {
                console.error('[active-addons] parse error:', e.message);
              }
            });
          }).on('error', () => {});
        }, delay);
      }
      _reInjectAddons(200);
      _reInjectAddons(1000);
      _reInjectAddons(3000);
    }

    // ── Tab snapshot push ──────────────────────────────────────────────────
    // Pushes the current page HTML to the gateway so the AI chat always has
    // fresh context when the user enables "Page" context.
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
  // Admin hub pages always go to the left of regular internet tabs.
  if (url.startsWith(GATEWAY_ORIGIN + '/ui/')) {
    const firstRegIdx = tabOrder.findIndex(tid =>
      !(tabs[tid]?.view?.webContents?.getURL() || '').startsWith(GATEWAY_ORIGIN + '/ui/')
    );
    tabOrder.splice(firstRegIdx === -1 ? tabOrder.length : firstRegIdx, 0, id);
  } else {
    tabOrder.push(id);
  }
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
      notifyChrome('nav-state', { id: activeTabId, canGoBack: wc2.navigationHistory.canGoBack(), canGoForward: wc2.navigationHistory.canGoForward() });
    }
    notifyTabsUpdated();
    return;
  }

  // ─── Solo (unpaired) tab clicked ─────────────────────────────────────────
  if (activePair) activePair.paused = true;   // pause any visible split pair
  activeTabId = id;
  win.addBrowserView(tab.view);
  tab.view.setBounds(tabBounds(win));
  // Give the BrowserView OS focus so wheel/scroll events reach it on Linux
  // (on Linux, scroll events follow keyboard focus rather than cursor position).
  tab.view.webContents.focus();
  const wc = tab.view.webContents;
  notifyChrome('url-changed', { id, url: wc.getURL() });
  notifyChrome('nav-state', { id, canGoBack: wc.navigationHistory.canGoBack(), canGoForward: wc.navigationHistory.canGoForward() });
  notifyTabsUpdated();

  // Push a fresh snapshot for the newly-active tab so "Page" context always
  // reflects the page the user is actually looking at.
  const snapUrl = wc.getURL();
  if (snapUrl && snapUrl !== 'about:blank' && !snapUrl.startsWith(GATEWAY_ORIGIN + '/ui/')) {
    wc.executeJavaScript('document.documentElement.outerHTML').then(html => {
      const title   = wc.getTitle();
      const payload = JSON.stringify({ url: snapUrl, title, html });
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
    }).catch(() => {});
  }
}

/**
 * Close a tab. If it is the active one, activate the next or previous.
 */
function closeTab(id, win = mainWin) {
  const tab = tabs[id];
  if (!tab) return;
  // Remove pair if the closing tab was part of one
  const closingPair = getPairOf(id);
  if (closingPair) {
    splitPairs = splitPairs.filter(p => p !== closingPair);
    if (!getActivePair()) notifyChrome('split-changed', splitPairs.length > 0 ? {} : null);
  }
  win.removeBrowserView(tab.view);
  tab.view.webContents.destroy();
  delete tabs[id];
  tabOrder = tabOrder.filter(tid => tid !== id);

  if (Object.keys(tabs).length === 0) {
    // No tabs left — open a fresh one
    createTab(NEW_TAB_URL, win);
    return;
  }

  // If no admin hub tabs remain, reopen the index so the user always has one.
  const hasAdminTab = Object.values(tabs).some(t =>
    !t.view.webContents.isDestroyed() &&
    (t.view.webContents.getURL() || '').startsWith(GATEWAY_ORIGIN + '/ui/')
  );
  if (!hasAdminTab) createTab(HOME_URL, win);

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
      id:      t.id,
      url:     t.view.webContents.getURL(),
      title:   t.view.webContents.getTitle(),
      favicon: null,           // favicon updates arrive via separate event
      active:  t.id === activeTabId,
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

  // Reorder tabs by drag-and-drop.
  // Zone rule: admin hub tabs stay left of regular tabs and vice-versa.
  ipcMain.handle('reorder-tab', (_, dragId, targetId) => {
    const from = tabOrder.indexOf(dragId);
    const to   = tabOrder.indexOf(targetId);
    if (from === -1 || to === -1 || from === to) return;

    const isAdminUrl = (id) =>
      (tabs[id]?.view?.webContents?.getURL() || '').startsWith(GATEWAY_ORIGIN + '/ui/');
    // Prevent dragging across zone boundary
    if (isAdminUrl(dragId) !== isAdminUrl(targetId)) return;

    tabOrder.splice(from, 1);
    tabOrder.splice(to, 0, dragId);
    notifyTabsUpdated();
  });

  // Right-click context menu on a tab — shown as a native OS menu so it
  // renders above all BrowserViews (HTML menus would be hidden under them).
  ipcMain.handle('show-tab-ctx', (_, { tabId, tabUrl, groups }) => {
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
        label: isMuted ? '🔊 Unmute' : '🔇 Mute',
        click: () => {
          if (!wc) return;
          wc.setAudioMuted(!isMuted);
          notifyChrome('tab-muted', { id: tabId, muted: !isMuted });
          notifyTabsUpdated();
        },
      }] : []),
      // Split: close the pair this tab belongs to; show "fractionner" for any unpaired inactive tab
      ...(getPairOf(tabId)
        ? [{ label: '⊟ Close Split View', click: () => exitSplitForTab(tabId) }]
        : (!isActive
            ? [{ label: '⊟ Split View', click: () => enterSplit(tabId) }]
            : [])),
      { type: 'separator' },
      ...(!isAdminHub ? [{
        label: 'Move to Inactive Tabs',
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
      // ── Chrome-style tab groups ──────────────────────────────────────────
      ...(!isAdminHub ? [{
        label: '🏷 Add to Group',
        submenu: [
          { label: '＋ New Group', click: () => mainWin.webContents.send('tab-group-action', { action: 'new', tabId }) },
          ...((groups && groups.length > 0) ? [{ type: 'separator' }] : []),
          ...((groups || []).map(g => ({
            label: `● ${g.name || '(no name)'}`,

            click: () => mainWin.webContents.send('tab-group-action', { action: 'add', tabId, groupId: g.id }),
          }))),
        ],
      }] : []),
      ...((groups || []).some(g => g.tabIds && g.tabIds.includes(tabId))
        ? [{ label: 'Remove from Group', click: () => mainWin.webContents.send('tab-group-action', { action: 'remove', tabId }) }]
        : []),
      { type: 'separator' },
      ...(!isAdminHub ? [{ label: 'Close Tab', click: () => closeTab(tabId) }] : []),
      // Close all tabs to the right of this one
      ...(() => {
        const idx = tabOrder.indexOf(tabId);
        const toClose = idx >= 0
          ? tabOrder.slice(idx + 1).filter(id => {
              const u = tabs[id]?.view?.webContents?.getURL() || '';
              return !u.startsWith(GATEWAY_ORIGIN + '/ui/');
            })
          : [];
        return toClose.length > 0 ? [{
          label: 'Close Tabs to the Right',
          click: () => toClose.forEach(id => closeTab(id)),
        }] : [];
      })(),
    ];
    Menu.buildFromTemplate(items).popup({ window: mainWin });
  });

  // Native context menu for tab group chips
  ipcMain.handle('show-group-ctx', (_, { groupId }) => {
    const send = (action) => mainWin?.webContents.send('group-ctx-action', { action, groupId });
    const items = [
      { label: 'New Tab in Group', click: () => createTab() },
      { type: 'separator' },
      { label: '\u270F\uFE0F Rename',               click: () => send('rename')       },
      { label: '\u25CF Change Color',               click: () => send('color')        },
      { type: 'separator' },
      { label: 'Ungroup',                            click: () => send('ungroup')      },
      { label: '\uD83D\uDCC1 Close Group',           click: () => send('close-save-bm') },
      { label: 'Close and Delete',                   click: () => send('close-group')   },
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
  ipcMain.handle('go-back',    () => tabs[activeTabId]?.view.webContents.navigationHistory.goBack());
  ipcMain.handle('go-forward', () => tabs[activeTabId]?.view.webContents.navigationHistory.goForward());
  ipcMain.handle('reload',     () => tabs[activeTabId]?.view.webContents.reload());
  ipcMain.handle('stop',       () => tabs[activeTabId]?.view.webContents.stop());

  // Query current state
  ipcMain.handle('get-active-url', () => {
    return tabs[activeTabId]?.view.webContents.getURL() || '';
  });

  ipcMain.handle('get-tabs', () => {
    return tabOrder.filter(id => tabs[id]).map(id => {
      const t = tabs[id];
      return {
        id:     t.id,
        url:    t.view.webContents.getURL(),
        title:  t.view.webContents.getTitle(),
        active: t.id === activeTabId,
      };
    });
  });

  ipcMain.handle('get-gateway-status', () => ({
    ready: gatewayReady,
    origin: GATEWAY_ORIGIN,
    pid: gatewayProcess?.pid ?? null,
  }));

  // Navigate to the Intelli admin hub index.
  // Looks for an existing index tab first; if none, opens a new one.
  ipcMain.handle('go-home', () => {
    // Navigate the active tab to the admin hub, or create it if none is open.
    const tab = tabs[activeTabId];
    if (tab && !tab.view.webContents.isDestroyed()) {
      tab.view.webContents.loadURL(HOME_URL);
    } else {
      createTab(HOME_URL);
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
    // Update Accept-Language header live — takes effect on the next request
    _acceptLanguage = LANG_HEADERS[s.language] || LANG_HEADERS['en'];
  });

  // ── Bookmarks ────────────────────────────────────────────────────────────
  ipcMain.handle('bookmarks-list',   () => loadBookmarks());
  ipcMain.handle('bookmarks-add',    (_, { url, title, favicon }) => {
    const bm = loadBookmarks();
    if (bm.find(b => b.url === url)) return bm;        // already saved
    bm.unshift({ id: Date.now(), url, title: title || url, favicon: favicon || null, addedAt: new Date().toISOString() });
    saveBookmarks(bm);
    notifyChrome('bookmarks-changed', bm);
    return bm;
  });
  ipcMain.handle('bookmarks-remove', (_, url) => {
    const bm = loadBookmarks().filter(b => b.url !== url);
    saveBookmarks(bm);
    notifyChrome('bookmarks-changed', bm);
    return bm;
  });
  ipcMain.handle('bookmarks-has', (_, url) => {
    return loadBookmarks().some(b => b.url === url);
  });
  ipcMain.handle('bookmarks-add-group', (_, { name, color, tabs }) => {
    const bm = loadBookmarks();
    const id = Date.now();
    bm.unshift({ id, type: 'group', url: 'group:' + id, name: name || '', color: color || '#888', tabs: tabs || [], addedAt: new Date().toISOString() });
    saveBookmarks(bm);
    notifyChrome('bookmarks-changed', bm);
    return bm;
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

  // ── Bookmarks bar visibility ──────────────────────────────────────────
  ipcMain.handle('set-bookmarks-bar-visible', (_, v) => {
    bookmarksBarVisible = !!v;
    const s = loadSettings(); s.bookmarksBar = bookmarksBarVisible; saveSettingsData(s);
    notifyChrome('bookmarks-bar-state', bookmarksBarVisible);
    // Recalculate BrowserView bounds with new chrome height
    const ap = getActivePair();
    if (ap) {
      if (tabs[ap.leftId])  tabs[ap.leftId].view.setBounds(tabBounds(mainWin));
      if (tabs[ap.rightId]) tabs[ap.rightId].view.setBounds(splitRightBounds(mainWin));
    } else {
      const tab = tabs[activeTabId];
      if (tab && mainWin) tab.view.setBounds(tabBounds(mainWin));
    }
  });

  // ── Inactive-tabs popup (custom BrowserWindow — renders above BrowserViews) ───
  let _tabsPopup = null;

  function _closeTabsPopup() {
    const w = _tabsPopup;
    _tabsPopup = null;         // null first so the blur-timer callback is a no-op
    if (w && !w.isDestroyed()) w.destroy(); // destroy (not close) avoids Linux GTK re-entrant signal
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

  // ── Chrome Extensions ────────────────────────────────────────────────────

  /** Return the current extension index merged with live session data. */
  ipcMain.handle('ext-list', async () => {
    const live = session.defaultSession.getAllExtensions();
    const liveMap = Object.fromEntries(live.map(e => [e.id, e]));
    const list = _readExtIndex();
    return list.map(rec => {
      // Read manifest to derive options / popup page URLs
      let optionsUrl = null;
      let popupUrl   = null;
      try {
        const mf = JSON.parse(fs.readFileSync(path.join(rec.path, 'manifest.json'), 'utf8'));
        const optPage = mf.options_ui?.page || mf.options_page || null;
        const popPage = mf.action?.default_popup || mf.browser_action?.default_popup || null;
        if (optPage) optionsUrl = `chrome-extension://${rec.id}/${optPage.replace(/^\//, '')}`;
        if (popPage) popupUrl   = `chrome-extension://${rec.id}/${popPage.replace(/^\//, '')}`;
      } catch (_) {}
      return {
        ...rec,
        liveLoaded: !!liveMap[rec.id],
        optionsUrl,
        popupUrl,
      };
    });
  });

  /** Pick an unpacked extension folder, load it, persist to index. */
  ipcMain.handle('ext-load-unpacked', async () => {
    const result = await dialog.showOpenDialog(mainWin, {
      title: 'Select unpacked extension folder',
      properties: ['openDirectory'],
      buttonLabel: 'Load Extension',
    });
    if (result.canceled || !result.filePaths.length) return { ok: false, reason: 'cancelled' };
    const extPath = result.filePaths[0];
    const manifestPath = path.join(extPath, 'manifest.json');
    if (!fs.existsSync(manifestPath)) return { ok: false, reason: 'No manifest.json found in selected folder' };
    let manifest;
    try { manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8')); }
    catch (e) { return { ok: false, reason: `Invalid manifest.json: ${e.message}` }; }
    let loaded;
    try { loaded = await session.defaultSession.loadExtension(extPath, { allowFileAccess: true }); }
    catch (e) { return { ok: false, reason: e.message }; }
    const list  = _readExtIndex();
    const extId = loaded.id;
    if (!list.find(r => r.id === extId)) {
      list.push({ id: extId, name: manifest.name || 'Unknown', version: manifest.version || '', path: extPath, enabled: true });
      _writeExtIndex(list);
    }
    return { ok: true, id: extId, name: manifest.name, version: manifest.version };
  });

  /** Pick a CRX file, extract it, load it, persist to index. */
  ipcMain.handle('ext-load-crx', async () => {
    const result = await dialog.showOpenDialog(mainWin, {
      title: 'Select CRX extension file',
      properties: ['openFile'],
      filters: [{ name: 'Chrome Extension', extensions: ['crx'] }],
      buttonLabel: 'Load CRX',
    });
    if (result.canceled || !result.filePaths.length) return { ok: false, reason: 'cancelled' };
    const crxPath = result.filePaths[0];
    let extPath;
    try { extPath = await _extractCrx(crxPath); }
    catch (e) { return { ok: false, reason: `CRX extraction failed: ${e.message}` }; }
    const manifestPath = path.join(extPath, 'manifest.json');
    if (!fs.existsSync(manifestPath)) return { ok: false, reason: 'Extracted CRX has no manifest.json' };
    let manifest;
    try { manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8')); }
    catch (e) { return { ok: false, reason: `Invalid manifest.json: ${e.message}` }; }
    let loaded;
    try { loaded = await session.defaultSession.loadExtension(extPath, { allowFileAccess: true }); }
    catch (e) { return { ok: false, reason: e.message }; }
    const extId = loaded.id;
    const list  = _readExtIndex();
    if (!list.find(r => r.id === extId)) {
      list.push({ id: extId, name: manifest.name || 'Unknown', version: manifest.version || '', path: extPath, enabled: true });
      _writeExtIndex(list);
    }
    return { ok: true, id: extId, name: manifest.name, version: manifest.version };
  });

  /** Remove a Chrome extension (unload from session + delete from index). */
  ipcMain.handle('ext-remove', async (_, extId) => {
    try { await session.defaultSession.removeExtension(extId); } catch (_) {}
    let list = _readExtIndex();
    const rec = list.find(r => r.id === extId);
    list = list.filter(r => r.id !== extId);
    _writeExtIndex(list);
    // Clean up extracted dir if inside userData
    if (rec && rec.path.startsWith(CHROME_EXT_DIR())) {
      try { fs.rmSync(rec.path, { recursive: true, force: true }); } catch (_) {}
    }
    return { ok: true };
  });

  /** Enable or disable a Chrome extension (persists; re-loads session on enable). */
  ipcMain.handle('ext-toggle', async (_, { extId, enabled }) => {
    const list = _readExtIndex();
    const rec  = list.find(r => r.id === extId);
    if (!rec) return { ok: false, reason: 'Extension not found' };
    rec.enabled = !!enabled;
    _writeExtIndex(list);
    if (!enabled) {
      try { await session.defaultSession.removeExtension(extId); } catch (_) {}
    } else {
      try { await session.defaultSession.loadExtension(rec.path, { allowFileAccess: true }); } catch (e) {
        return { ok: false, reason: e.message };
      }
    }
    return { ok: true };
  });

  /** Rename (set custom display name for) a Chrome extension in the index. */
  ipcMain.handle('ext-rename', (_, { extId, name }) => {
    const list = _readExtIndex();
    const rec  = list.find(r => r.id === extId);
    if (!rec) return { ok: false, reason: 'Extension not found' };
    rec.name = (name || '').trim() || rec.name;
    _writeExtIndex(list);
    return { ok: true, name: rec.name };
  });

  // ── Downloads ─────────────────────────────────────────────────────────────
  ipcMain.handle('open-downloads-folder', () => {
    shell.openPath(app.getPath('downloads'));
  });

  // ── Extension API Audit ───────────────────────────────────────────────────
  ipcMain.handle('get-ext-api-audit', () => _apiAuditEntries);
  ipcMain.handle('clear-ext-api-audit', async () => {
    _apiAuditEntries = [];
    await fs.promises.writeFile(EXT_API_AUDIT_LOG(), '').catch(() => {});
    return { ok: true };
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
          { label: 'Manage Intelli Addons',    click: () => createTab(`${GATEWAY_ORIGIN}/ui/addons.html`) },
          { label: 'Manage Chrome Extensions', click: () => notifyChrome('open-panel', 'chrome-ext') },
          { label: 'Chrome Web Store',         click: () => createTab('https://chrome.google.com/webstore') },
          { label: 'Developer Mode Addons',    click: () => notifyChrome('open-panel', 'dev-addons') },
          { type: 'separator' },
          { label: 'Extension API Audit',      click: () => notifyChrome('open-panel', 'ext-audit') },
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

  // ── Addon injection queue ───────────────────────────────────────────────
  // Polls /tab/inject-queue every 2 s.  Each item returned is a JS snippet
  // written by an agent (or activated via the Addons admin UI).  We run it
  // inside the currently-active browser tab — NOT the sidebar or any gateway
  // UI page.
  function pollInjectQueue() {
    if (!gatewayReady) return;
    http.get(`${GATEWAY_ORIGIN}/tab/inject-queue`, res => {
      let data = '';
      res.on('data', d => { data += d; });
      res.on('end', async () => {
        try {
          const items = JSON.parse(data);          // [{name, code_js}, …]
          if (!Array.isArray(items) || items.length === 0) return;

          const tab = tabs[activeTabId];
          if (!tab) return;
          const url = tab.view.webContents.getURL();
          // Only inject into real external pages, not gateway admin UI
          if (!url || url === 'about:blank' || url.startsWith(GATEWAY_ORIGIN + '/ui/')) return;

          for (const item of items) {
            try {
              // Execute item.code_js directly without embedding it in a template
              // literal — avoids CodeQL js/improper-code-sanitization (CWE-116).
              // Errors thrown by the page-side code propagate as rejected Promises
              // and are caught by the outer catch block below.
              await tab.view.webContents.executeJavaScript(item.code_js);
              console.log(`[addon] injected "${item.name}" into ${url}`);
            } catch (err) {
              console.error(`[addon] inject "${item.name}" CRASHED:`, err.message);
            }
          }
        } catch (e) {
          console.error('[inject-queue] parse error:', e.message);
        }
      });
    }).on('error', () => { /* gateway may be restarting */ });
  }

  setInterval(pollInjectQueue, 2000);

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
      sidebarView.webContents.loadURL(`${GATEWAY_ORIGIN}/ui/chat.html`, {
        extraHeaders: 'Cache-Control: no-cache\r\nPragma: no-cache\r\n',
      });
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
        { label: 'Reload Tab',   accelerator: 'CmdOrCtrl+R', click: () => tabs[activeTabId]?.view.webContents.reload() },
        { label: 'Reload Tab',   accelerator: 'F5',           click: () => tabs[activeTabId]?.view.webContents.reload(), visible: false },
        { label: 'Force Reload', accelerator: 'CmdOrCtrl+Shift+R', click: () => tabs[activeTabId]?.view.webContents.reloadIgnoringCache() },
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
        { label: 'Back',    accelerator: 'Alt+Left',  click: () => tabs[activeTabId]?.view.webContents.navigationHistory.goBack() },
        { label: 'Forward', accelerator: 'Alt+Right', click: () => tabs[activeTabId]?.view.webContents.navigationHistory.goForward() },
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

  // Handle both the browser shell page's own CSP and Google auth page CSP
  // in a single onHeadersReceived handler (Electron only supports 1 listener).
  // For Google auth pages we STRIP their CSP entirely so the antidetect
  // preload's <script> injection (which lacks a CSP nonce) is not blocked.
  const _GOOGLE_AUTH_HOSTS_RE = /^accounts\.(google|youtube)\.com$/;
  session.defaultSession.webRequest.onHeadersReceived({ urls: ['<all_urls>'] }, (details, callback) => {
    const hdrs = { ...details.responseHeaders };
    const url = details.url || '';

    // --- browser.html: apply our strict own CSP ---
    if (url.includes('browser.html')) {
      hdrs['Content-Security-Policy'] = [CHROME_CSP];
      return callback({ responseHeaders: hdrs });
    }

    // --- Google auth pages: remove CSP so preload injection works ---
    try {
      const _host = new URL(url).hostname;
      if (_GOOGLE_AUTH_HOSTS_RE.test(_host)) {
        // Delete CSP headers case-insensitively (Electron lowercases response headers)
        for (const k of Object.keys(hdrs)) {
          const kl = k.toLowerCase();
          if (kl === 'content-security-policy' || kl === 'content-security-policy-report-only') {
            delete hdrs[k];
          }
        }
      }
    } catch (_) {}

    callback({ responseHeaders: hdrs });
  });
}

// ─── Extension API Audit Agent ────────────────────────────────────────────────
// Statically scans installed Chrome extension JS files (in a worker thread)
// to detect calls to chrome.* APIs that Intelli has NOT shimmed.
// Results are persisted to a JSONL file and pushed live to the main window.

const EXT_API_AUDIT_LOG = () => path.join(app.getPath('userData'), 'ext-api-audit.jsonl');

// All chrome.* API methods that Intelli currently shims / supports.
const _SHIMMED_APIS = new Set([
  // runtime
  'runtime.sendMessage','runtime.connect','runtime.getURL','runtime.getManifest',
  'runtime.id','runtime.lastError','runtime.reload','runtime.setUninstallURL',
  'runtime.requestUpdateCheck','runtime.getPlatformInfo','runtime.getPackageDirectoryEntry',
  'runtime.onMessage','runtime.onConnect','runtime.onInstalled','runtime.onStartup','runtime.onSuspend',
  // ── NEW: openOptionsPage — Electron routes it to a new BrowserWindow;
  //         we intercept via browser-window-created and createTab() instead.
  'runtime.openOptionsPage',
  // storage
  'storage.local.get','storage.local.set','storage.local.remove','storage.local.clear',
  'storage.sync.get','storage.sync.set','storage.sync.remove','storage.sync.clear',
  'storage.local','storage.sync','storage.onChanged',
  // tabs
  'tabs.query','tabs.get','tabs.create','tabs.update','tabs.remove','tabs.duplicate',
  'tabs.sendMessage','tabs.onUpdated','tabs.onActivated','tabs.onRemoved',
  'tabs.captureVisibleTab','tabs.executeScript','tabs.insertCSS','tabs.move','tabs.reload',
  // windows
  'windows.getAll','windows.getCurrent','windows.get','windows.create','windows.update','windows.remove',
  'windows.onFocusChanged','windows.onCreated','windows.onRemoved',
  // alarms
  'alarms.create','alarms.get','alarms.getAll','alarms.clear','alarms.clearAll',
  'alarms.onAlarm',
  // contextMenus
  'contextMenus.create','contextMenus.update','contextMenus.remove',
  'contextMenus.removeAll','contextMenus.onClicked',
  // notifications
  'notifications.create','notifications.update','notifications.clear',
  'notifications.getAll','notifications.onClicked','notifications.onClosed',
  'notifications.onButtonClicked',
  // scripting
  'scripting.executeScript','scripting.insertCSS','scripting.removeCSS',
  'scripting.registerContentScripts','scripting.unregisterContentScripts',
  'scripting.getRegisteredContentScripts',
  // declarativeNetRequest
  'declarativeNetRequest.updateDynamicRules','declarativeNetRequest.getDynamicRules',
  'declarativeNetRequest.updateStaticRules','declarativeNetRequest.getSessionRules',
  'declarativeNetRequest.updateSessionRules',
  // webRequest
  'webRequest.onBeforeRequest','webRequest.onBeforeSendHeaders',
  'webRequest.onHeadersReceived','webRequest.onResponseStarted',
  'webRequest.onCompleted','webRequest.onErrorOccurred',
  // identity
  'identity.getAuthToken','identity.launchWebAuthFlow','identity.removeCachedAuthToken',
  // cookies
  'cookies.get','cookies.getAll','cookies.set','cookies.remove','cookies.onChanged',
  // history
  'history.addUrl','history.deleteUrl','history.search','history.getVisits',
  'history.deleteAll','history.deleteRange',
  // bookmarks
  'bookmarks.get','bookmarks.search','bookmarks.create','bookmarks.remove',
  'bookmarks.update','bookmarks.move','bookmarks.getTree','bookmarks.getRecent',
  // action / browserAction
  'action.setIcon','action.setTitle','action.setBadgeText','action.setBadgeBackgroundColor',
  'action.getTitle','action.getBadgeText','action.enable','action.disable',
  'action.setPopup','action.openPopup',
  // ── NEW: action.onClicked fires when the extension toolbar icon is clicked.
  //         Electron dispatches this natively; we also add a browser-window-
  //         created listener to route any popup window into an Intelli tab.
  'action.onClicked',
  'browserAction.setIcon','browserAction.setTitle','browserAction.setBadgeText',
  'browserAction.onClicked',
  // ── NEW: chrome.downloads — fully supported by Electron 35's session layer.
  //         Extensions can download files; Electron routes them through the
  //         session's will-download event and the OS download manager.
  'downloads.download','downloads.search','downloads.pause','downloads.resume',
  'downloads.cancel','downloads.erase','downloads.open','downloads.show',
  'downloads.showDefaultFolder','downloads.drag','downloads.acceptDanger',
  'downloads.setShelfEnabled','downloads.onCreated','downloads.onChanged','downloads.onErased',
  // sidePanel
  'sidePanel.open','sidePanel.setOptions','sidePanel.getOptions','sidePanel.setPanelBehavior',
  // i18n
  'i18n.getMessage','i18n.getUILanguage','i18n.detectLanguage',
  // miscellaneous commonly used
  'permissions.contains','permissions.request','permissions.remove','permissions.getAll',
  'extension.getURL','extension.getBackgroundPage','extension.getViews',
  'management.getAll','management.get','management.getSelf',
  'offscreen.createDocument','offscreen.closeDocument','offscreen.hasDocument',
  'commands.getAll','commands.onCommand',
  'system.memory.getInfo','system.cpu.getInfo','system.storage.getInfo',
]);

let _apiAuditEntries = [];  // in-memory cache (also written to JSONL)

// ── Worker source (eval'd in a worker_threads context) ────────────────────────
const _SCAN_WORKER_SRC = `
const { workerData, parentPort } = require('worker_threads');
const fs   = require('fs');
const path = require('path');
const shimmed = new Set(workerData.shimmed);
const API_RE  = /chrome\\.([a-zA-Z]+(?:\\.[a-zA-Z]+)+)\\s*(?:\\(|;|\\.)/g;

// Normalize a raw API match to its canonical 2-segment form, checking whether
// the API or any of its shorter prefixes is already shimmed.
// Returns null (shimmed / skip) or a canonical not-shimmed API name to report.
function resolveApi(raw) {
  if (shimmed.has(raw)) return null;
  const parts = raw.split('.');
  if (parts.length >= 3) {
    // Check 3-seg prefix (e.g. storage.local.get)
    if (shimmed.has(parts.slice(0, 3).join('.'))) return null;
    // Check 2-seg prefix (e.g. runtime.onMessage) — suppresses
    // false positives like runtime.onMessage.addListener
    if (shimmed.has(parts.slice(0, 2).join('.'))) return null;
    // Genuinely unshimmed — canonicalize to 2 segments for consistency
    return parts.slice(0, 2).join('.');
  }
  return raw; // 2-seg and not shimmed → real gap
}

for (const { extId, extPath, extName } of workerData.entries) {
  let jsFiles = [];
  try {
    const walk = (dir, depth) => {
      if (depth > 6) return;
      let entries;
      try { entries = fs.readdirSync(dir, { withFileTypes: true }); }
      catch (_) { return; }
      for (const e of entries) {
        const full = path.join(dir, e.name);
        if (e.isDirectory()) walk(full, depth + 1);
        else if (e.isFile() && e.name.endsWith('.js')) jsFiles.push(full);
      }
    };
    walk(extPath, 0);
  } catch (_) {}

  const unknown = new Map();
  for (const file of jsFiles) {
    let src = '';
    try { src = fs.readFileSync(file, 'utf8'); } catch (_) { continue; }
    let m;
    API_RE.lastIndex = 0;
    while ((m = API_RE.exec(src)) !== null) {
      const canonical = resolveApi(m[1]);
      if (canonical && !unknown.has(canonical)) unknown.set(canonical, path.basename(file));
    }
  }
  parentPort.postMessage({ extId, extName, apis: [...unknown.entries()].map(([api, src]) => ({ api, src })) });
}
parentPort.postMessage({ done: true });
`;

let _scanWorkerRunning = false;

function _startScanWorker(entries) {
  if (_scanWorkerRunning) return;
  _scanWorkerRunning = true;
  const { Worker: _W } = require('worker_threads');
  let src;
  try { src = require('vm').Script; } catch (_) {} // just a warm-up import
  const w = new _W(_SCAN_WORKER_SRC, {
    eval: true,
    workerData: { shimmed: [..._SHIMMED_APIS], entries },
    resourceLimits: { maxOldGenerationSizeMb: 64 },
  });
  w.on('message', async (msg) => {
    if (msg.done) { _scanWorkerRunning = false; return; }
    const { extId, extName, apis } = msg;
    if (!apis.length) return;
    console.log(`[ext-api-audit] Scan "${extName}": ${apis.length} unimplemented API(s)`);
    for (const { api, src } of apis) {
      await _logUnknownApi(extId, api, src, extName);
    }
  });
  w.on('error', (e) => { console.error('[ext-api-audit] Worker error:', e.message); _scanWorkerRunning = false; });
  w.on('exit',  ()  => { _scanWorkerRunning = false; });
}

async function _logUnknownApi(extId, api, source, knownExtName) {
  const existing = _apiAuditEntries.find(e => e.extId === extId && e.api === api);
  if (existing) return; // already logged
  const entry = { ts: Date.now(), extId, extName: knownExtName || extId, api, source };
  _apiAuditEntries.push(entry);
  try { await fs.promises.appendFile(EXT_API_AUDIT_LOG(), JSON.stringify(entry) + '\n'); } catch (_) {}
  console.log(`[ext-api-audit] ${entry.extName}: ${api} (${source})`);
  if (mainWin && !mainWin.isDestroyed()) {
    mainWin.webContents.send('ext-api-audit-new', entry);
  }
}

async function _initExtApiAuditAgent() {
  // 1. Load persisted entries from JSONL
  try {
    const raw = await fs.promises.readFile(EXT_API_AUDIT_LOG(), 'utf8').catch(() => '');
    for (const line of raw.split('\n')) {
      if (!line.trim()) continue;
      try { _apiAuditEntries.push(JSON.parse(line)); } catch (_) {}
    }
    // Migration: drop entries that are now covered by _SHIMMED_APIS
    // (previously logged as false positives before the scanner was fixed).
    const before = _apiAuditEntries.length;
    _apiAuditEntries = _apiAuditEntries.filter(e => {
      const parts = (e.api || '').split('.');
      if (_SHIMMED_APIS.has(e.api)) return false;
      if (parts.length >= 2 && _SHIMMED_APIS.has(parts.slice(0, 2).join('.'))) return false;
      if (parts.length >= 3 && _SHIMMED_APIS.has(parts.slice(0, 3).join('.'))) return false;
      return true;
    });
    if (_apiAuditEntries.length !== before) {
      // Rewrite log without the now-resolved entries
      await fs.promises.writeFile(
        EXT_API_AUDIT_LOG(),
        _apiAuditEntries.map(e => JSON.stringify(e)).join('\n') + (_apiAuditEntries.length ? '\n' : '')
      ).catch(() => {});
      console.log(`[ext-api-audit] Cleaned ${before - _apiAuditEntries.length} now-shimmed entries from log`);
    }
    console.log(`[ext-api-audit] Loaded ${_apiAuditEntries.length} existing entries`);
  } catch (_) {}

  // 2. Schedule a static scan 12 s after startup (avoids competing with tab init)
  setTimeout(() => {
    const exts = session.defaultSession.getAllExtensions();
    if (!exts.length) { console.log('[ext-api-audit] No extensions to scan.'); return; }
    const entries = exts.map(e => ({ extId: e.id, extPath: e.path, extName: e.name }));
    console.log(`[ext-api-audit] Starting static scan of ${entries.length} extension(s)…`);
    _startScanWorker(entries);
  }, 12000);
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
    icon: path.join(__dirname, 'assets', process.platform === 'win32' ? 'icon.ico' : 'icon.png'),
  });

  mainWin.loadFile(path.join(__dirname, 'src', 'browser.html'));
  // Each BrowserView (tab) causes Electron to internally attach a 'closed'
  // listener to the host BrowserWindow so it can clean up when the window
  // is destroyed.  With many tabs this exceeds Node's default limit of 10.
  // Raise it to 100 (one per potential tab) to silence the false-positive
  // MaxListenersExceededWarning without hiding real leaks.
  mainWin.setMaxListeners(100);

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

  // ── Generate & register the antidetect preload script ───────────────────
  // ANTIDETECT_JS patches navigator.userAgentData, window.chrome, etc.
  // Injecting via executeJavaScript at dom-ready is TOO LATE — Google's
  // bot-detection inline scripts run before DOMContentLoaded.
  // Solution: write a preload script that injects into the MAIN world via a
  // <script> element SYNCHRONOUSLY, before any page code runs, then register
  // it on the session so it applies to every frame.
  try {
    const _preloadContent = [
      "'use strict';",
      "// Generated by Intelli – injects antidetect payload into the main world",
      "// before any page script (including Google's bot-detection inline code).",
      "try {",
      "  const _s = document.createElement('script');",
      `  _s.textContent = ${JSON.stringify(ANTIDETECT_JS)};`,
      "  (document.documentElement || document.head || document.body).appendChild(_s);",
      "  _s.remove();",
      "} catch (_) {}",
    ].join('\n');
    fs.writeFileSync(_ANTIDETECT_PRELOAD_PATH, _preloadContent, 'utf8');

    // Popup preload: direct IIFE — contextIsolation:false means this runs in the
    // main world already, so no <script> injection needed.  Preloads bypass CSP.
    // CRITICAL: also clean up Electron globals that contextIsolation:false leaks
    // into the main world (window.process.versions.electron, window.Buffer, etc.).
    // These must be removed AFTER the IIFE so they're available during patching.
    const _popupPreloadContent = [
      "'use strict';",
      "// Generated by Intelli – runs ANTIDETECT_JS directly in the main world.",
      // ANTIDETECT_JS already starts with (function(){ and ends with })(); — no extra wrapper needed.
      ANTIDETECT_JS,
      "// ── Remove Electron-specific globals (contextIsolation:false leaks these) ──",
      "// Google's bot-detection checks window.process.versions.electron.",
      "try { Object.defineProperty(window, 'process', { get: () => undefined, configurable: true, enumerable: false }); } catch (_) {}",
      "try { Object.defineProperty(window, 'Buffer', { get: () => undefined, configurable: true, enumerable: false }); } catch (_) {}",
      "// global is normally === window in browsers; Electron sets it to its own obj.",
      "try { if (window.global !== window) Object.defineProperty(window, 'global', { get: () => window, configurable: true }); } catch (_) {}",
    ].join('\n');
    fs.writeFileSync(_POPUP_PRELOAD_PATH, _popupPreloadContent, 'utf8');

    // Register on the session so it applies to every frame (BrowserViews + popups).
    // setPreloads is the stable cross-version API; registerPreloadScript is newer.
    // Try both so it works across Electron versions.
    let _registered = false;
    if (typeof session.defaultSession.registerPreloadScript === 'function') {
      try {
        session.defaultSession.registerPreloadScript({
          id:     'intelli-antidetect',
          type:   'frame',
          script: _ANTIDETECT_PRELOAD_PATH,
        });
        _registered = true;
      } catch (_) { /* try setPreloads fallback below */ }
    }
    if (!_registered && typeof session.defaultSession.setPreloads === 'function') {
      session.defaultSession.setPreloads([_ANTIDETECT_PRELOAD_PATH]);
      _registered = true;
    }
    if (!_registered) {
      console.warn('[antidetect] no preload registration API available; relying on dom-ready injection only');
    }
    console.log('[antidetect] preload registered:', _ANTIDETECT_PRELOAD_PATH);
  } catch (e) {
    console.warn('[antidetect] failed to register preload:', e.message);
  }

  // ── Override User-Agent on the default session ──────────────────────────
  // Replaces "Electron/29.x.x" with a clean Chrome 122 UA so Google, Cloudflare
  // and reCAPTCHA don't fingerprint us as automation.
  session.defaultSession.setUserAgent(CHROME_UA);

  // Note: session.setUserAgentMetadata() does NOT exist in Electron 35.
  // The Sec-CH-UA client-hint brands are patched via onBeforeSendHeaders below
  // (sets Sec-CH-UA / Sec-CH-UA-Full-Version-List / Sec-CH-UA-Arch etc.), and
  // the JS-level navigator.userAgentData is patched by ANTIDETECT_JS injected
  // at dom-ready into every page.  Google's "not secure" check is server-side
  // (header-based), so the header fixes plus X-Client-Data are sufficient.

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

  // Strip any Electron-identifying headers and inject full Chrome header set.
  // X-Client-Data is a Chrome field-trial protobuf header sent to all Google
  // properties.  Its absence is Google's primary signal that the browser is NOT
  // real Chrome — adding it (any plausible value) removes that signal.
  const _SEC_CH_UA       = '"Not A(Brand";v="8", "Chromium";v="132", "Google Chrome";v="132"';
  const _SEC_CH_UA_FULL  = '"Not A(Brand";v="8.0.0.0", "Chromium";v="132.0.6834.110", "Google Chrome";v="132.0.6834.110"';
  // Real Chrome 132 field-trial header value (protobuf-encoded, base64)
  const _X_CLIENT_DATA   = 'CJa2yQEIprbJAQipncoBCKijygEIkqHLAQiFoM0BCIWkzQEI3tXNAQ==';

  session.defaultSession.webRequest.onBeforeSendHeaders((details, callback) => {
    const h = details.requestHeaders;

    // Core UA / client-hints spoofing
    h['User-Agent']                  = CHROME_UA;
    h['Sec-CH-UA']                   = _SEC_CH_UA;
    h['Sec-CH-UA-Mobile']            = '?0';
    h['Sec-CH-UA-Platform']          = SEC_CH_UA_PLATFORM;
    h['Sec-CH-UA-Full-Version-List'] = _SEC_CH_UA_FULL;
    h['Sec-CH-UA-Arch']              = '"x86"';
    h['Sec-CH-UA-Bitness']           = '"64"';
    h['Sec-CH-UA-Model']             = '""';
    h['Accept-Language']             = _acceptLanguage;

    // X-Client-Data: Chrome sends this to all Google domains; its absence is the
    // #1 signal Google uses to detect non-Chrome browsers (including Electron).
    try {
      const _reqHost = new URL(details.url).hostname;
      if (_reqHost.endsWith('.google.com') || _reqHost.endsWith('.googleapis.com') ||
          _reqHost.endsWith('.youtube.com') || _reqHost.endsWith('.gstatic.com') ||
          _reqHost.endsWith('.googlevideo.com')) {
        h['X-Client-Data'] = _X_CLIENT_DATA;
        // DEBUG: write headers to file so we can verify what's actually sent
        if (_reqHost === 'accounts.google.com' && details.resourceType === 'mainFrame') {
          const _dbgLine = JSON.stringify({
            url: details.url.split('?')[0],
            ua:  h['User-Agent'],
            sch: h['Sec-CH-UA'],
            xcd: h['X-Client-Data'],
            keys: Object.keys(h),
          }) + '\n';
          fs.appendFileSync('/tmp/intelli-google-headers.txt', _dbgLine);
        }
      }
    } catch (_) {}

    // Remove any headers that could expose the Electron runtime
    delete h['X-Electron-Version'];
    delete h['X-Electron-App-Version'];

    callback({ cancel: false, requestHeaders: h });
  });

  setupDownloads(session.defaultSession);
  registerIPC();
  await loadSavedExtensions();

  // ── Deep Google sign-in intercept via webRequest ────────────────────────────
  // This catches the case where loadURL() is called directly (address bar) or
  // an HTTP redirect arrives — neither of which fires will-navigate/will-redirect.
  session.defaultSession.webRequest.onBeforeRequest(
    { urls: ['https://accounts.google.com/*', 'https://accounts.youtube.com/*'] },
    (details, callback) => {
      // Only intercept top-level navigations (not XHRs, images, etc from Google pages)
      if (details.resourceType !== 'mainFrame') {
        callback({ cancel: false });
        return;
      }
      // Allow requests that come FROM the popup window itself (avoid infinite loop)
      if (_googleAuthWin && !_googleAuthWin.isDestroyed() &&
          details.webContentsId === _googleAuthWin.webContents.id) {
        callback({ cancel: false });
        return;
      }
      // Cancel the BrowserView navigation and open popup instead
      callback({ cancel: true });
      setImmediate(() => {
        openGoogleAuthPopup(details.url);
        // Navigate the source tab back so it doesn't show a broken-page error
        try {
          const srcWc = electronWebContents.fromId(details.webContentsId);
          if (srcWc && !srcWc.isDestroyed()) {
            if (srcWc.navigationHistory?.canGoBack()) srcWc.navigationHistory.goBack();
            else srcWc.loadURL('about:blank');
          }
        } catch (_) {}
      });
    }
  );

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
  // Start extension API audit agent — scans ext JS files in a worker thread
  // to find unimplemented chrome.* calls. Runs 12 s after startup.
  _initExtApiAuditAgent();
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

// Route extension-opened windows (openOptionsPage, action popup) into Intelli tabs.
// When an extension calls chrome.runtime.openOptionsPage() Electron creates a new
// BrowserWindow with the chrome-extension:// options URL.  We catch it here and
// redirect it to Intelli's createTab() so it opens in the custom tab bar instead.
app.on('browser-window-created', (_, win) => {
  if (win === mainWin) return; // ignore the main window itself
  win.webContents.once('did-finish-load', () => {
    const url = win.webContents.getURL();
    // Only intercept chrome-extension:// pages that match a known extension
    const m = url.match(/^chrome-extension:\/\/([a-z]{32})\//);
    if (!m) return;
    const extId = m[1];
    const list  = _readExtIndex();
    if (!list.find(r => r.id === extId)) return; // not one of ours
    // It's an options/popup window opened by one of our extensions.
    // Redirect into Intelli's tab system and destroy the standalone window.
    setImmediate(() => {
      try { createTab(url); } catch (_) {}
      try { if (!win.isDestroyed()) win.destroy(); } catch (_) {}
    });
  });
});

// Quit when all windows are closed (except on macOS)
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

// Shut down gateway before quitting; also destroy any floating preview window
app.on('before-quit', () => {
  _closeHoverWin();
  stopGateway();
  // Write clean-exit sentinel so next launch skips the Service Worker DB wipe
  try { fs.writeFileSync(_CLEAN_EXIT_SENTINEL, Date.now().toString()); } catch (_) {}
});

// Security: restrict what new windows can be opened
app.on('web-contents-created', (_, contents) => {
  // Detect chrome-extension:// pages (background pages, popups, options pages)
  // and inject the context-menu bridge so we can read registered items.
  contents.on('did-finish-load', async () => {
    const url = contents.getURL();
    const m = url.match(/^chrome-extension:\/\/([a-z]{32})\//);
    if (m) {
      const extId = m[1];
      try {
        await contents.executeJavaScript(_EXT_CTX_BRIDGE);
        _extBgPages.set(extId, contents);
        // Allow background scripts to call contextMenus.create before we read
        setTimeout(() => _fetchExtContextMenuItems(extId, contents), 1200);
        setTimeout(() => _fetchExtContextMenuItems(extId, contents), 4000);
        _scheduleCtxMenuRefresh(3000);
      } catch (_) {}
    }
  });

  contents.setWindowOpenHandler(({ url }) => {
    // Google sign-in opened via window.open() — route to dedicated popup
    if (_isGoogleAuthUrl(url)) {
      setImmediate(() => openGoogleAuthPopup(url));
      return { action: 'deny' };
    }
    // Always open gateway-origin links as a new Intelli tab
    if (url.startsWith(GATEWAY_ORIGIN) || url.startsWith('file://')) {
      setImmediate(() => createTab(url));
      return { action: 'deny' };
    }
    // Open http/https links (including OAuth, external sites) as new Intelli tabs
    // so auth flows and popups stay inside the browser.
    if (url.startsWith('http://') || url.startsWith('https://')) {
      setImmediate(() => createTab(url));
      return { action: 'deny' };
    }
    // Non-web schemes (mailto:, tel:, magnet:, etc.) → system handler
    shell.openExternal(url);
    return { action: 'deny' };
  });
});
