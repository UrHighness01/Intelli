'use strict';
/**
 * preload-page.js — injected into every BrowserView (web pages).
 *
 * Supprime les fingerprints Electron / automation pour passer les checks
 * de YouTube, Google reCAPTCHA et Cloudflare.
 *
 * Exécuté AVANT tout script de la page grâce à contextIsolation:false.
 */

// ── 1. navigator.webdriver ─────────────────────────────────────────────────
try {
  Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined,
    configurable: true,
  });
} catch (_) {}

// ── 2. Supprimer window.process (révèle Electron via process.versions.electron)
try {
  delete window.process;
} catch (_) {
  try {
    Object.defineProperty(window, 'process', { get: () => undefined, configurable: true });
  } catch (_2) {}
}

// ── 3. navigator.plugins ───────────────────────────────────────────────────
try {
  const fakePlugins = [
    { name: 'PDF Viewer',             filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
    { name: 'Chrome PDF Viewer',      filename: 'internal-pdf-viewer', description: '' },
    { name: 'Chromium PDF Viewer',    filename: 'internal-pdf-viewer', description: '' },
    { name: 'Microsoft Edge PDF Viewer', filename: 'internal-pdf-viewer', description: '' },
    { name: 'WebKit built-in PDF',    filename: 'internal-pdf-viewer', description: '' },
  ];
  Object.defineProperty(navigator, 'plugins', {
    get: () => {
      const arr = fakePlugins.map(p => {
        const plugin = Object.create(window.Plugin ? window.Plugin.prototype : {});
        Object.defineProperties(plugin, {
          name:        { value: p.name,        enumerable: true },
          filename:    { value: p.filename,    enumerable: true },
          description: { value: p.description, enumerable: true },
          length:      { value: 0,             enumerable: true },
        });
        return plugin;
      });
      if (window.PluginArray) Object.setPrototypeOf(arr, window.PluginArray.prototype);
      Object.defineProperty(arr, 'length', { value: fakePlugins.length });
      arr.item = (i) => arr[i];
      arr.namedItem = (n) => arr.find(p => p.name === n) || null;
      arr.refresh = () => {};
      return arr;
    },
    configurable: true,
  });
} catch (_) {}

// ── 4. navigator.languages + navigator.language ────────────────────────────
try {
  Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'], configurable: true });
  Object.defineProperty(navigator, 'language',  { get: () => 'en-US', configurable: true });
} catch (_) {}

// ── 5. navigator.hardwareConcurrency + deviceMemory ───────────────────────
try { Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8, configurable: true }); } catch (_) {}
try { Object.defineProperty(navigator, 'deviceMemory',        { get: () => 8, configurable: true }); } catch (_) {}

// ── 6. window.chrome — objet complet comme dans vrai Chrome ───────────────
//   YouTube vérifie chrome.csi(), chrome.loadTimes(), chrome.runtime.sendMessage
try {
  const t0 = Date.now();
  window.chrome = {
    csi: () => ({
      startE:    t0,
      onloadT:   t0 + 120,
      pageT:     t0 + 150,
      tran:      15,
    }),
    loadTimes: () => ({
      commitLoadTime:             t0 / 1000,
      connectionInfo:             'h2',
      finishDocumentLoadTime:     (t0 + 120) / 1000,
      finishLoadTime:             (t0 + 150) / 1000,
      firstPaintAfterLoadTime:    0,
      firstPaintTime:             (t0 + 80) / 1000,
      navigationType:             'Other',
      npnNegotiatedProtocol:      'h2',
      requestTime:                t0 / 1000,
      startLoadTime:              t0 / 1000,
      wasAlternateProtocolAvailable: false,
      wasFetchedViaSpdy:          true,
      wasNpnNegotiated:           true,
    }),
    app: {
      isInstalled:    false,
      getDetails:     () => null,
      getIsInstalled: () => false,
      installState:   () => {},
      runningState:   () => 'cannot_run',
      InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
      RunningState:  { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' },
    },
    runtime: {
      id:           undefined,
      connect:      () => {},
      sendMessage:  () => {},
      onConnect:    { addListener: () => {}, removeListener: () => {} },
      onMessage:    { addListener: () => {}, removeListener: () => {} },
    },
    webstore: {
      onInstallStageChanged: { addListener: () => {} },
      onDownloadProgress:    { addListener: () => {} },
      install:               () => {},
    },
  };
} catch (_) {}

// ── 7. document.hasFocus() → toujours true ────────────────────────────────
//   BrowserView non-focusé retourne false → drapeau automation pour YouTube.
try {
  Object.defineProperty(document, 'hasFocus', {
    value: () => true,
    writable: true,
    configurable: true,
  });
} catch (_) {}

// ── 8. WebGL vendor / renderer ────────────────────────────────────────────
try {
  const patchGL = (Ctx) => {
    if (!Ctx) return;
    const orig = Ctx.prototype.getParameter;
    Ctx.prototype.getParameter = function (param) {
      if (param === 37445) return 'Google Inc. (Intel)';
      // Renderer string must match the real OS — D3D11 only exists on Windows
      const renderer = process.platform === 'darwin'
        ? 'ANGLE (Intel, ANGLE Metal Renderer: Intel(R) Iris(TM) Plus Graphics, Unspecified Version)'
        : process.platform === 'win32'
          ? 'ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)'
          : 'ANGLE (Intel, Mesa Intel(R) UHD Graphics 620 (KBL GT2), OpenGL 4.6)';
      if (param === 37446) return renderer;
      return orig.call(this, param);
    };
  };
  patchGL(window.WebGLRenderingContext);
  patchGL(window.WebGL2RenderingContext);
} catch (_) {}

// ── 9. Permissions API ─────────────────────────────────────────────────────
try {
  const origQuery = window.navigator.permissions.query.bind(navigator.permissions);
  window.navigator.permissions.__proto__.query = (params) => {
    if (params.name === 'notifications') {
      return Promise.resolve({ state: 'default', onchange: null });
    }
    if (['camera', 'microphone', 'geolocation'].includes(params.name)) {
      return Promise.resolve({ state: 'prompt', onchange: null });
    }
    return origQuery(params).catch(() => Promise.resolve({ state: 'prompt', onchange: null }));
  };
} catch (_) {}

// ── 10. screen / window dimensions cohérentes ─────────────────────────────
try { Object.defineProperty(window, 'outerWidth',  { get: () => window.innerWidth,       configurable: true }); } catch (_) {}
try { Object.defineProperty(window, 'outerHeight', { get: () => window.innerHeight + 74, configurable: true }); } catch (_) {}

// ── 11. navigator.platform — must match the real OS, never hardcode Windows ─
try {
  const _navPlatform = process.platform === 'darwin' ? 'MacIntel'
                     : process.platform === 'win32'  ? 'Win32'
                     : 'Linux x86_64';
  Object.defineProperty(navigator, 'platform', { get: () => _navPlatform, configurable: true });
} catch (_) {}
