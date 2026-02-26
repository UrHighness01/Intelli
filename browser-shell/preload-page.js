'use strict';
/**
 * preload-page.js — injected into every BrowserView (web pages).
 *
 * Removes the Electron / automation fingerprints that cause Google reCAPTCHA
 * and other bot-detection systems to challenge the browser.
 *
 * Technique mirrors what Brave does: override read-only navigator properties
 * before any page script can observe them.
 */

// ── 1. navigator.webdriver ─────────────────────────────────────────────────
//   Electron sets this to `true`; real browsers set it to `undefined`.
try {
  Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined,
    configurable: true,
  });
} catch (_) {}

// ── 2. navigator.plugins ───────────────────────────────────────────────────
//   Headless/Electron has 0 plugins; real Chrome has several.
try {
  const fakePlugins = [
    { name: 'Chrome PDF Plugin',       filename: 'internal-pdf-viewer',   description: 'Portable Document Format' },
    { name: 'Chrome PDF Viewer',       filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
    { name: 'Native Client',           filename: 'internal-nacl-plugin',  description: '' },
  ];
  Object.defineProperty(navigator, 'plugins', {
    get: () => {
      const arr = fakePlugins.map(p => {
        const plugin = Object.create(Plugin.prototype);
        Object.defineProperties(plugin, {
          name:        { value: p.name,        enumerable: true },
          filename:    { value: p.filename,    enumerable: true },
          description: { value: p.description, enumerable: true },
          length:      { value: 0,             enumerable: true },
        });
        return plugin;
      });
      Object.setPrototypeOf(arr, PluginArray.prototype);
      Object.defineProperty(arr, 'length', { value: fakePlugins.length });
      return arr;
    },
    configurable: true,
  });
} catch (_) {}

// ── 3. navigator.languages ─────────────────────────────────────────────────
try {
  Object.defineProperty(navigator, 'languages', {
    get: () => ['fr-CA', 'fr', 'en-CA', 'en'],
    configurable: true,
  });
} catch (_) {}

// ── 4. window.chrome ──────────────────────────────────────────────────────
//   Electron doesn't expose window.chrome; real Chrome does.
try {
  if (!window.chrome) {
    window.chrome = {
      app:     { isInstalled: false, InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }, RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' } },
      runtime: { id: undefined },
    };
  }
} catch (_) {}

// ── 5. Permissions API ─────────────────────────────────────────────────────
//   navigator.permissions.query({name:'notifications'}) returns 'denied'
//   in headless; spoof to behave like a real browser.
try {
  const origQuery = window.navigator.permissions.query.bind(navigator.permissions);
  window.navigator.permissions.__proto__.query = (params) => {
    if (params.name === 'notifications') {
      return Promise.resolve({ state: Notification.permission, onchange: null });
    }
    return origQuery(params);
  };
} catch (_) {}

// ── 6. WebGL vendor / renderer ────────────────────────────────────────────
//   Electron exposes "Google SwiftShader" which is an automation marker.
try {
  const getParam = WebGLRenderingContext.prototype.getParameter;
  WebGLRenderingContext.prototype.getParameter = function (param) {
    if (param === 37445) return 'Intel Inc.';                   // UNMASKED_VENDOR_WEBGL
    if (param === 37446) return 'Intel Iris OpenGL Engine';     // UNMASKED_RENDERER_WEBGL
    return getParam.call(this, param);
  };
} catch (_) {}
