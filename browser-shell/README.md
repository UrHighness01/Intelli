# Intelli Browser

A Chromium-based desktop browser built with Electron that bundles the **Intelli agent gateway** as an embedded backend service.  
When you open the app the gateway starts automatically in the background; when you close the window the gateway process is cleanly terminated.

---

## Features

| Feature | Details |
|---|---|
| Full browser | Address bar, tabs, back/forward/reload, navigate any URL |
| Embedded gateway | Python uvicorn process spawned on launch, hidden from taskbar |
| Gateway lifecycle | Starts before first window opens; killed on window close or Quit |
| Admin shortcuts | Gateway menu links directly to Admin Hub, Audit log, Users, Status |
| Dark chrome UI | Tab bar + address bar matching Intelli's `#0f1117` / `#6c63ff` theme |
| Gateway status dot | Address bar indicator — orange (starting), green (ready), red (error) |
| Search fallback | Non-URL address bar input queries DuckDuckGo |
| Home URL | `http://127.0.0.1:8080/ui/` (Intelli Admin Hub) |
| Windows packaging | NSIS `.exe` installer via electron-builder |
| Linux packaging | `.deb` + AppImage (planned) |

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Node.js ≥ 18 | Electron build toolchain |
| Python 3.10+ | For the embedded gateway |
| Gateway deps | `pip install -r ../agent-gateway/requirements.txt` |

---

## Quick start (development)

```bash
cd browser-shell

# 1. Install JS dependencies
npm install

# 2. Generate the placeholder icon (first run only)
node generate-icon.js

# 3. Launch in dev mode (gateway auto-starts from ../agent-gateway/)
npm start
```

The gateway is discovered at `../agent-gateway/` in dev mode and at
`resources/agent-gateway/` inside a packaged build.

---

## Build (Windows installer)

```bash
npm run build
# → dist/Intelli-Setup-0.1.0.exe
```

Requires Windows or a cross-compile environment with Wine.

---

## Build (Linux)

```bash
npm run build:linux
# → dist/intelli-browser_0.1.0_amd64.deb
# → dist/intelli-browser-0.1.0.AppImage
```

---

## Directory layout

```
browser-shell/
├── main.js              ← Electron main process (gateway lifecycle, tab management, IPC)
├── preload.js           ← contextBridge API exposed to chrome renderer
├── package.json         ← electron + electron-builder config
├── generate-icon.js     ← one-shot script to create placeholder icon.ico
├── assets/
│   └── icon.ico         ← app icon (generated; replace with branded version)
└── src/
    ├── browser.html     ← chrome UI shell (tab bar + address bar only)
    ├── browser.css      ← dark-theme styles for the chrome UI
    ├── browser.js       ← renderer-side logic; calls window.electronAPI.*
    └── splash.html      ← startup splash shown while gateway boots
```

---

## Architecture

```
┌────────────────────────────────────────────────────┐
│  Electron main process  (main.js)                  │
│  ┌──────────────┐   ┌──────────────────────────┐   │
│  │ Gateway      │   │ BrowserWindow            │   │
│  │ spawn/kill   │   │  browser.html (chrome)   │   │
│  │ Python       │   │  ├── tab bar             │   │
│  │ uvicorn      │   │  └── address bar         │   │
│  └──────────────┘   │                          │   │
│                     │  BrowserView × N (tabs)  │   │
│                     │  ├── Tab 1 (page)         │   │
│                     │  ├── Tab 2 (page)         │   │
│                     │  └── …                   │   │
│                     └──────────────────────────┘   │
└────────────────────────────────────────────────────┘
         │ IPC (contextBridge)
         ▼
   preload.js  →  window.electronAPI
```

- **`BrowserView`** renders actual web pages (one per tab) — positioned below the 88 px chrome strip.  
- **`browser.html`** is the chrome UI and never navigates; it only draws tab/address controls.  
- **`contextBridge`** in `preload.js` is the only bridge between sandboxed renderer and main process.

---

## Keyboard shortcuts

| Key | Action |
|---|---|
| `Ctrl+T` | New tab |
| `Ctrl+W` | Close active tab |
| `Ctrl+R` | Reload |
| `Ctrl+L` | Focus address bar |
| `Alt+Left` | Back |
| `Alt+Right` | Forward |
| `Ctrl+Shift+A` | Toggle admin-hub sidebar (☰ button) |
| `Ctrl+1`–`8` | Switch to tab N |
| `Ctrl+9` | Switch to last tab |

---

## Admin sidebar

Pressing **Ctrl+Shift+A** (or clicking the ☰ button at the right end of the address
bar) toggles a 340 px admin panel alongside the active tab.

The panel loads the Admin Hub (`/ui/`) from the running gateway inside a separate
`BrowserView`.  It is created lazily on first use — no overhead until first
toggle.

When the sidebar is open:
- The active tab BrowserView shrinks by 340 px on the right.
- Window resize / enter-fullscreen / leave-fullscreen automatically reposition
  both views.
- The ☰ button turns accent purple to indicate the open state.

To close, press **Ctrl+Shift+A** again or click the button a second time.

---

## Replacing the placeholder icon

`generate-icon.js` creates a solid purple 16×16 `.ico` suitable only for development.  
For release, replace `assets/icon.ico` with a proper multi-resolution `.ico` (16, 32, 48, 256 px).  
Tools: [GIMP](https://www.gimp.org/), [IcoFX](https://icofx.ro/), or the npm package `png-to-ico`.
