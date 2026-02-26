'use strict';

/* ── State ─────────────────────────────────────────────────────── */
let _tabs      = [];   // [{ id, title, url, favicon }]
let _activeId  = null;
let _canBack   = false;
let _canFwd    = false;
let _currentUrl = '';

/* ── DOM refs ───────────────────────────────────────────────────── */
const $tabs    = document.getElementById('tabs');
const $newTab  = document.getElementById('new-tab-btn');
const $back    = document.getElementById('btn-back');
const $fwd     = document.getElementById('btn-forward');
const $reload  = document.getElementById('btn-reload');
const $home    = document.getElementById('btn-home');
const $urlIn   = document.getElementById('url-input');
const $gwDot   = document.getElementById('gw-dot');

/* ── Tab rendering ──────────────────────────────────────────────── */
function renderTabs(tabs) {
  _tabs = tabs;
  $tabs.innerHTML = '';
  for (const t of tabs) {
    const el = document.createElement('div');
    el.className = 'tab' + (t.id === _activeId ? ' active' : '');
    el.dataset.id = t.id;

    const fav = document.createElement('img');
    fav.className = 'tab-favicon' + (t.favicon ? '' : ' hidden');
    fav.src = t.favicon || '';
    fav.alt = '';

    const title = document.createElement('span');
    title.className = 'tab-title';
    title.textContent = t.title || t.url || 'New Tab';

    const close = document.createElement('span');
    close.className = 'tab-close';
    close.textContent = '×';
    close.title = 'Close tab';
    close.addEventListener('click', e => {
      e.stopPropagation();
      window.electronAPI.closeTab(t.id);
    });

    el.append(fav, title, close);
    el.addEventListener('click',       () => window.electronAPI.switchTab(t.id));
    el.addEventListener('contextmenu', e  => {
      e.preventDefault();
      // Native popup renders above all BrowserViews — pass id and current url
      window.electronAPI.showTabCtx(t.id, t.url || '');
    });
    $tabs.appendChild(el);
  }
}

/* ── Address bar ────────────────────────────────────────────────── */
function updateAddressBar(url) {
  _currentUrl = url || '';
  if (document.activeElement !== $urlIn) {
    $urlIn.value = _currentUrl;
  }
  refreshBookmarkStar();
}

function setNavState(canBack, canFwd) {
  _canBack = canBack;
  _canFwd  = canFwd;
  $back.disabled    = !canBack;
  $fwd.disabled     = !canFwd;
}

/* ── Gateway status dot ─────────────────────────────────────────── */
function setGwDot(state) {          // 'loading' | 'ready' | 'error'
  $gwDot.className = state;
  $gwDot.title = state === 'ready'   ? 'Gateway running'
               : state === 'loading' ? 'Gateway starting…'
               : 'Gateway not reachable';
}

async function pollGateway() {
  try {
    const s = await window.electronAPI.getGatewayStatus();
    setGwDot(s.ready ? 'ready' : 'error');
  } catch {
    setGwDot('error');
  }
}

/* ── Navigation ─────────────────────────────────────────────────── */
function navigate(raw) {
  const val = raw.trim();
  if (!val) return;
  window.electronAPI.navigate(val);
}

/* ── IPC event listeners ────────────────────────────────────────── */
window.electronAPI.onTabTitleUpdated(({ id, title }) => {
  const tab = _tabs.find(t => t.id === id);
  if (tab) { tab.title = title; renderTabs(_tabs); }
});

window.electronAPI.onTabFaviconUpdated(({ id, favicon }) => {
  const tab = _tabs.find(t => t.id === id);
  if (tab) { tab.favicon = favicon; renderTabs(_tabs); }
});

window.electronAPI.onUrlChanged(({ id, url }) => {
  const tab = _tabs.find(t => t.id === id);
  if (tab) { tab.url = url; }
  if (id === _activeId) updateAddressBar(url);
});

// Sync the full tab list (create / switch / close).
// Merge with existing _tabs to preserve cached favicons (the main process
// sends favicon:null because favicons arrive via a separate IPC event).
window.electronAPI.onTabsUpdated(incoming => {
  const faviconCache = Object.fromEntries(_tabs.map(t => [t.id, t.favicon]));
  const merged = incoming.map(t => ({ ...t, favicon: faviconCache[t.id] ?? t.favicon }));
  const active = merged.find(t => t.active);
  if (active) {
    _activeId = active.id;
    _tgMarkActive(active.id);
  }
  renderTabs(merged);
  const activeTab = merged.find(t => t.id === _activeId);
  if (activeTab?.url) updateAddressBar(activeTab.url);
});

window.electronAPI.onNavState(({ id, canGoBack, canGoForward }) => {
  if (id === _activeId) setNavState(canGoBack, canGoForward);
});

// Main-process can ask us to open a panel (e.g. from the native three-dot menu)
window.electronAPI.onOpenPanel(name => openPanel(name));
window.electronAPI.onRequestBookmarkToggle(() => toggleBookmarkCurrentPage());
window.electronAPI.onZoomChanged(() => refreshZoomIndicator());

/* ── Button handlers ────────────────────────────────────────────── */
$newTab.addEventListener('click', () => window.electronAPI.newTab());
$back.addEventListener('click',   () => window.electronAPI.goBack());
$fwd.addEventListener('click',    () => window.electronAPI.goForward());
$home.addEventListener('click',   () => window.electronAPI.goHome());
$reload.addEventListener('click', () => window.electronAPI.reload());

/* ── Address bar input ──────────────────────────────────────────── */
$urlIn.addEventListener('keydown', e => {
  if (e.key === 'Enter') { navigate($urlIn.value); $urlIn.blur(); }
  if (e.key === 'Escape') { updateAddressBar(_currentUrl); $urlIn.blur(); }
});
$urlIn.addEventListener('focus', () => $urlIn.select());

/* ── Keyboard shortcuts ─────────────────────────────────────────── */
document.addEventListener('keydown', e => {
  const ctrl = e.ctrlKey || e.metaKey;
  if (ctrl && e.key === 't') { e.preventDefault(); window.electronAPI.newTab(); }
  if (ctrl && e.key === 'w') { e.preventDefault(); window.electronAPI.closeTab(_activeId); }
  if (ctrl && (e.key === 'r' || e.key === 'R')) { e.preventDefault(); window.electronAPI.reload(); }
  if (ctrl && e.key === 'l') { e.preventDefault(); $urlIn.focus(); $urlIn.select(); }
  if (e.altKey && e.key === 'ArrowLeft')  { e.preventDefault(); window.electronAPI.goBack(); }
  if (e.altKey && e.key === 'ArrowRight') { e.preventDefault(); window.electronAPI.goForward(); }

  // DevTools
  if (ctrl && e.shiftKey && (e.key === 'i' || e.key === 'I')) { e.preventDefault(); window.electronAPI.toggleDevTools(); }
  if (e.key === 'F12') { e.preventDefault(); window.electronAPI.toggleDevTools(); }

  // Sidebar
  if (ctrl && e.shiftKey && (e.key === 'a' || e.key === 'A')) { e.preventDefault(); toggleSidebar(); }

  // Bookmarks
  if (ctrl && e.key === 'd') { e.preventDefault(); toggleBookmarkCurrentPage(); }
  if (ctrl && e.shiftKey && (e.key === 'o' || e.key === 'O')) { e.preventDefault(); openPanel('bookmarks'); }

  // History
  if (ctrl && e.key === 'h') { e.preventDefault(); openPanel('history'); }

  // Zoom
  if (ctrl && (e.key === '+' || e.key === '=')) { e.preventDefault(); window.electronAPI.zoomIn().then(refreshZoomIndicator); }
  if (ctrl && e.key === '-') { e.preventDefault(); window.electronAPI.zoomOut().then(refreshZoomIndicator); }
  if (ctrl && e.key === '0') { e.preventDefault(); window.electronAPI.zoomReset().then(refreshZoomIndicator); }

  // Tab switching Ctrl+1–9
  if (ctrl && e.key >= '1' && e.key <= '8') {
    e.preventDefault();
    const idx = parseInt(e.key, 10) - 1;
    if (_tabs[idx]) window.electronAPI.switchTab(_tabs[idx].id);
  }
  if (ctrl && e.key === '9') {
    e.preventDefault();
    if (_tabs.length) window.electronAPI.switchTab(_tabs[_tabs.length - 1].id);
  }

  // Close any open panel on Escape
  if (e.key === 'Escape' && _openPanel) { e.preventDefault(); closeAllPanels(); }
});

/* ── Window controls ────────────────────────────────────────────── */
const $winMin   = document.getElementById('btn-win-min');
const $winMax   = document.getElementById('btn-win-max');
const $winClose = document.getElementById('btn-win-close');

function _setMaxIcon(isMax) {
  if (!$winMax) return;
  $winMax.innerHTML = isMax ? '&#10697;' : '&#9633;';
  $winMax.title     = isMax ? 'Restore' : 'Maximize';
}

if ($winMin)   $winMin.addEventListener('click', () => window.electronAPI.minimizeWindow());
if ($winMax)   $winMax.addEventListener('click', async () => _setMaxIcon(await window.electronAPI.toggleMaximize()));
if ($winClose) $winClose.addEventListener('click', () => window.electronAPI.closeWindow());

// Keep icon in sync when OS maximizes/restores (e.g. Win+Up, double-click drag bar)
window.electronAPI.onMaximizeChange(_setMaxIcon);
// Set correct icon on initial load
window.electronAPI.isMaximized().then(_setMaxIcon);

/* ── Sidebar toggle ─────────────────────────────────────────────── */
const $btnSidebar = document.getElementById('btn-sidebar');
async function toggleSidebar() {
  const result = await window.electronAPI.toggleSidebar();
  if ($btnSidebar) {
    $btnSidebar.style.color = result.open ? 'var(--accent)' : '';
    $btnSidebar.title = result.open
      ? 'Close AI Chat sidebar (Ctrl+Shift+A)'
      : 'Open AI Chat sidebar (Ctrl+Shift+A)';
  }
}
if ($btnSidebar) $btnSidebar.addEventListener('click', toggleSidebar);

/* ── Three-dot app menu ─────────────────────────────────────────── */
const $btnAppMenu = document.getElementById('btn-app-menu');
if ($btnAppMenu) $btnAppMenu.addEventListener('click', () => window.electronAPI.showAppMenu());

/* ═══════════════════════════════════════════════════════════════
   PANEL SYSTEM
   Panels are fixed-position overlays in the chrome renderer,
   so they render above all BrowserViews.
   ═══════════════════════════════════════════════════════════════ */

const $backdrop = document.getElementById('panel-backdrop');
let _openPanel  = null;

function openPanel(name) {
  closeAllPanels();
  const el = document.getElementById('panel-' + name);
  if (!el) return;
  el.classList.remove('hidden');
  $backdrop.classList.remove('hidden');
  _openPanel = name;
  window.electronAPI.setPanelVisible(true);
  if (name === 'bookmarks') loadBookmarksPanel();
  if (name === 'history')   loadHistoryPanel();
  if (name === 'settings')  renderTabGroupList();
}

function closeAllPanels() {
  document.querySelectorAll('.side-panel').forEach(p => p.classList.add('hidden'));
  $backdrop.classList.add('hidden');
  _openPanel = null;
  window.electronAPI.setPanelVisible(false);
}

$backdrop.addEventListener('click', closeAllPanels);
document.querySelectorAll('.panel-close').forEach(btn => btn.addEventListener('click', closeAllPanels));

/* ─── Bookmark star ─────────────────────────────────────────────── */
const $bmStar    = document.getElementById('btn-bookmark-star');
const $bmQuick   = document.getElementById('bm-quick-add');
const $bmNameIn  = document.getElementById('bm-name-input');
const $bmSaveBtn = document.getElementById('bm-save-btn');
const $bmRemBtn  = document.getElementById('bm-remove-btn');
const $bmSearch  = document.getElementById('bm-search');
const $bmList    = document.getElementById('bm-list');
let _bmQuickUrl  = '';

async function refreshBookmarkStar() {
  if (!$bmStar || !_currentUrl || _currentUrl.startsWith('about:')) return;
  const has = await window.electronAPI.bookmarksHas(_currentUrl);
  $bmStar.innerHTML = has ? '&#9733;' : '&#9734;';
  $bmStar.classList.toggle('bookmarked', has);
  $bmStar.title = has ? 'Remove bookmark (Ctrl+D)' : 'Bookmark this tab (Ctrl+D)';
}

async function toggleBookmarkCurrentPage() {
  if (!_currentUrl || _currentUrl.startsWith('about:')) return;
  const has = await window.electronAPI.bookmarksHas(_currentUrl);
  if (has) {
    await window.electronAPI.bookmarksRemove(_currentUrl);
    await refreshBookmarkStar();
  } else {
    _bmQuickUrl = _currentUrl;
    const activeTab = _tabs.find(t => t.id === _activeId);
    openPanel('bookmarks');
    if ($bmQuick) $bmQuick.classList.remove('hidden');
    if ($bmNameIn) { $bmNameIn.value = activeTab?.title || _currentUrl; $bmNameIn.select(); $bmNameIn.focus(); }
  }
}

$bmStar?.addEventListener('click', toggleBookmarkCurrentPage);

$bmSaveBtn?.addEventListener('click', async () => {
  const activeTab = _tabs.find(t => t.id === _activeId);
  await window.electronAPI.bookmarksAdd(_bmQuickUrl, $bmNameIn?.value || _bmQuickUrl, activeTab?.favicon || null);
  $bmQuick?.classList.add('hidden');
  await refreshBookmarkStar();
  await loadBookmarksPanel();
});

$bmRemBtn?.addEventListener('click', async () => {
  await window.electronAPI.bookmarksRemove(_bmQuickUrl);
  $bmQuick?.classList.add('hidden');
  await refreshBookmarkStar();
  await loadBookmarksPanel();
});

async function loadBookmarksPanel() {
  const bm = await window.electronAPI.bookmarksList();
  renderBookmarkList(bm);
}

function renderBookmarkList(bm) {
  if (!$bmList) return;
  const q = ($bmSearch?.value || '').toLowerCase();
  const filtered = q ? bm.filter(b => b.title.toLowerCase().includes(q) || b.url.toLowerCase().includes(q)) : bm;
  $bmList.innerHTML = '';
  if (!filtered.length) {
    $bmList.innerHTML = '<div style="padding:16px 14px;color:var(--muted);font-size:12px">No bookmarks yet. Press Ctrl+D to bookmark a page.</div>';
    return;
  }
  for (const b of filtered) {
    const row = document.createElement('div');
    row.className = 'panel-list-item';

    if (b.favicon) {
      const icon = document.createElement('img');
      icon.className = 'item-icon';
      icon.src = b.favicon;
      row.appendChild(icon);
    }

    const titleEl = document.createElement('span');
    titleEl.className = 'item-title';
    titleEl.textContent = b.title || b.url;
    titleEl.title = b.url;

    const del = document.createElement('button');
    del.className = 'item-del';
    del.textContent = '×';
    del.title = 'Remove bookmark';
    del.addEventListener('click', async e => {
      e.stopPropagation();
      await window.electronAPI.bookmarksRemove(b.url);
      await refreshBookmarkStar();
      await loadBookmarksPanel();
    });

    row.append(titleEl, del);
    row.addEventListener('click', () => { window.electronAPI.navigate(b.url); closeAllPanels(); });
    $bmList.appendChild(row);
  }
}

$bmSearch?.addEventListener('input', () => window.electronAPI.bookmarksList().then(renderBookmarkList));

/* ─── History panel ─────────────────────────────────────────────── */
const $histSearch   = document.getElementById('hist-search');
const $histList     = document.getElementById('hist-list');
const $histClearBtn = document.getElementById('hist-clear-btn');
let _historyData    = [];

async function loadHistoryPanel() {
  _historyData = await window.electronAPI.historyList(500);
  renderHistoryList(_historyData);
}

function renderHistoryList(entries) {
  if (!$histList) return;
  const q = ($histSearch?.value || '').toLowerCase();
  const filtered = q
    ? entries.filter(e => (e.title || '').toLowerCase().includes(q) || e.url.toLowerCase().includes(q))
    : entries;
  $histList.innerHTML = '';
  if (!filtered.length) {
    $histList.innerHTML = '<div style="padding:16px 14px;color:var(--muted);font-size:12px">No history</div>';
    return;
  }
  let lastDay = '';
  for (const entry of filtered.slice(0, 300)) {
    const day = new Date(entry.visitedAt).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
    if (day !== lastDay) {
      const sep = document.createElement('div');
      sep.className = 'panel-section-label';
      sep.textContent = day;
      $histList.appendChild(sep);
      lastDay = day;
    }
    const row = document.createElement('div');
    row.className = 'panel-list-item';

    const titleEl = document.createElement('span');
    titleEl.className = 'item-title';
    titleEl.textContent = entry.title || entry.url;
    titleEl.title = entry.url;

    const time = document.createElement('span');
    time.className = 'item-sub';
    time.textContent = new Date(entry.visitedAt).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });

    const del = document.createElement('button');
    del.className = 'item-del';
    del.textContent = '×';
    del.title = 'Remove entry';
    del.addEventListener('click', async e => {
      e.stopPropagation();
      _historyData = await window.electronAPI.historyRemove(entry.url);
      renderHistoryList(_historyData);
    });

    row.append(titleEl, time, del);
    row.addEventListener('click', () => { window.electronAPI.navigate(entry.url); closeAllPanels(); });
    $histList.appendChild(row);
  }
}

$histSearch?.addEventListener('input', () => renderHistoryList(_historyData));

$histClearBtn?.addEventListener('click', async () => {
  if (!confirm('Clear all browsing history?')) return;
  await window.electronAPI.historyClear();
  _historyData = [];
  renderHistoryList([]);
});

/* ═══════════════════════════════════════════════════════════════
   TAB GROUPS — regroupe les onglets inactifs après N minutes
   ═══════════════════════════════════════════════════════════════ */
const TG_STORAGE_ENABLED = 'tg_enabled';
const TG_STORAGE_DELAY   = 'tg_delay_min';

let _tgEnabled    = localStorage.getItem(TG_STORAGE_ENABLED) === 'true';
let _tgDelayMin   = parseInt(localStorage.getItem(TG_STORAGE_DELAY) || '5', 10);
let _tabLastSeen  = {};   // { tabId → Date.now() }
let _groupedTabs  = JSON.parse(localStorage.getItem('tg_grouped') || '[]');
                          // [{ id, url, title, favicon, groupedAt }]

/* Persiste la liste groupée */
function _tgSave() {
  localStorage.setItem('tg_grouped', JSON.stringify(_groupedTabs));
}

/* Retourne une chaîne relative ex: "il y a 3 min" */
function _tgRelTime(ts) {
  const diff = Math.round((Date.now() - ts) / 60000);
  if (diff < 1)  return 'maintenant';
  if (diff === 1) return 'il y a 1 min';
  if (diff < 60) return `il y a ${diff} min`;
  const h = Math.floor(diff / 60);
  return h === 1 ? 'il y a 1 h' : `il y a ${h} h`;
}

/* ─── Popover grouped-tabs (tab bar) ──────────────────────────────────────── */
const $tgBtn       = document.getElementById('btn-tab-groups');
const $tgBadge     = document.getElementById('tg-badge');
const $tgPopover   = document.getElementById('tg-popover');
const $tgPopList   = document.getElementById('tg-popover-list');
const $tgPopBd     = document.getElementById('tg-popover-backdrop');
let   _tgPopOpen   = false;

function _tgOpenPopover() {
  if (!$tgPopover) return;
  _tgPopOpen = true;
  $tgPopover.classList.remove('hidden');
  $tgPopBd?.classList.remove('hidden');
  $tgBtn?.classList.add('active');
  _tgFillPopover();
}

function _tgClosePopover() {
  _tgPopOpen = false;
  $tgPopover?.classList.add('hidden');
  $tgPopBd?.classList.add('hidden');
  $tgBtn?.classList.remove('active');
}

$tgBtn?.addEventListener('click', () => _tgPopOpen ? _tgClosePopover() : _tgOpenPopover());
$tgPopBd?.addEventListener('click', _tgClosePopover);
document.getElementById('tg-popover-close')?.addEventListener('click', _tgClosePopover);

/* Construit une rangée de la liste (réutilisé dans popover & settings) */
function _tgBuildRow(g, containerClass, onOpen) {
  const row = document.createElement('div');
  row.className = containerClass;
  if (g.favicon) {
    const img = document.createElement('img');
    img.src = g.favicon; img.alt = '';
    row.appendChild(img);
  } else {
    const ph = document.createElement('div');
    ph.style.cssText = 'width:14px;height:14px;border-radius:3px;background:var(--surface2);flex-shrink:0';
    row.appendChild(ph);
  }
  const info = document.createElement('div');
  info.className = 'tg-item-info';
  const titleEl = document.createElement('div');
  titleEl.className = 'tg-item-title';
  try { titleEl.textContent = g.title || new URL(g.url).hostname || g.url; }
  catch { titleEl.textContent = g.title || g.url; }
  const timeEl = document.createElement('div');
  timeEl.className = 'tg-item-time';
  timeEl.textContent = _tgRelTime(g.groupedAt);
  info.append(titleEl, timeEl);
  const btn = document.createElement('button');
  btn.className = 'tg-item-restore';
  btn.textContent = 'Ouvrir';
  btn.addEventListener('click', e => { e.stopPropagation(); onOpen(g); });
  row.addEventListener('click', () => onOpen(g));
  row.append(info, btn);
  return row;
}

function _tgRestoreTab(g) {
  _groupedTabs = _groupedTabs.filter(t => t.id !== g.id);
  _tgSave();
  window.electronAPI.switchTab(g.id);
  renderTabGroupList();
  _tgClosePopover();
  closeAllPanels();
}

/* Remplit le popover de la tab bar */
function _tgFillPopover() {
  if (!$tgPopList) return;
  $tgPopList.innerHTML = '';
  for (const g of _groupedTabs) {
    $tgPopList.appendChild(_tgBuildRow(g, 'tg-item', _tgRestoreTab));
  }
}

/* Met à jour bouton tab-bar + badge + listes */
function renderTabGroupList() {
  const count = _groupedTabs.length;
  // ── Bouton tab-bar ──
  if ($tgBtn) {
    if (_tgEnabled && count > 0) {
      $tgBtn.classList.remove('hidden');
    } else {
      $tgBtn.classList.add('hidden');
      _tgClosePopover();
    }
  }
  if ($tgBadge) $tgBadge.textContent = count;

  // ── Popover (si ouvert) ──
  if (_tgPopOpen) _tgFillPopover();

  // ── Panel Settings liste ──
  const $list  = document.getElementById('tab-group-list');
  const $count = document.getElementById('tg-count');
  if ($count) $count.textContent = count ? `(${count})` : '';
  if ($list) {
    $list.innerHTML = '';
    for (const g of _groupedTabs) {
      $list.appendChild(_tgBuildRow(g, 'tg-item', _tgRestoreTab));
    }
  }
}

/* Vérifie périodiquement les onglets inactifs */
function _tgCheck() {
  if (!_tgEnabled) return;
  const threshold = _tgDelayMin * 60 * 1000;
  const now = Date.now();
  for (const tab of _tabs) {
    if (tab.id === _activeId) continue;                          // onglet actif = jamais groupé
    if (_groupedTabs.some(g => g.id === tab.id)) continue;      // déjà groupé
    const last = _tabLastSeen[tab.id] || now;
    if (now - last >= threshold) {
      _groupedTabs.push({
        id:        tab.id,
        url:       tab.url   || '',
        title:     tab.title || tab.url || 'Onglet',
        favicon:   tab.favicon || null,
        groupedAt: now,
      });
    }
  }
  _tgSave();
  renderTabGroupList();
}

/* Marque un onglet comme actif (réinitialise son timer) */
function _tgMarkActive(tabId) {
  _tabLastSeen[tabId] = Date.now();
  // retirer des groupés si l'utilisateur y navigue
  if (_groupedTabs.some(g => g.id === tabId)) {
    _groupedTabs = _groupedTabs.filter(g => g.id !== tabId);
    _tgSave();
    renderTabGroupList();
  }
}

// Contrôles dans le panel Settings
const $tgEnabled = document.getElementById('setting-tab-group-enabled');
const $tgDelay   = document.getElementById('setting-tab-group-delay');

if ($tgEnabled) {
  $tgEnabled.checked = _tgEnabled;
  $tgEnabled.addEventListener('change', () => {
    _tgEnabled = $tgEnabled.checked;
    localStorage.setItem(TG_STORAGE_ENABLED, _tgEnabled);
    renderTabGroupList();   // show / hide tab-bar button immediately
  });
}
if ($tgDelay) {
  $tgDelay.value = String(_tgDelayMin);
  $tgDelay.addEventListener('change', () => {
    _tgDelayMin = parseInt($tgDelay.value, 10);
    localStorage.setItem(TG_STORAGE_DELAY, _tgDelayMin);
  });
}

// Lancer le check toutes les 60 secondes
setInterval(_tgCheck, 60_000);

// Restaurer l'état du bouton au démarrage
renderTabGroupList();

// Synchroniser avec les événements IPC existants
window.electronAPI.onTabsUpdated(incoming => {
  // Purger les groupés si l'onglet n'existe plus
  const ids = new Set(incoming.map(t => t.id));
  _groupedTabs = _groupedTabs.filter(g => ids.has(g.id));
  _tgSave();
  renderTabGroupList();
});

/* ─── Settings panel ────────────────────────────────────────────── */
document.getElementById('setting-open-hub')      ?.addEventListener('click', () => { window.electronAPI.goHome(); closeAllPanels(); });
document.getElementById('setting-open-addons')   ?.addEventListener('click', () => { window.electronAPI.navigate('http://127.0.0.1:8080/ui/addons.html'); closeAllPanels(); });
document.getElementById('setting-open-downloads') ?.addEventListener('click', () => window.electronAPI.openDownloadsFolder());
document.getElementById('setting-devtools-page')  ?.addEventListener('click', () => window.electronAPI.toggleDevTools());
document.getElementById('setting-devtools-chrome')?.addEventListener('click', () => window.electronAPI.toggleChromeDevTools());

/* ─── Clear data panel ──────────────────────────────────────────── */
document.getElementById('clr-cancel-btn')?.addEventListener('click', closeAllPanels);
document.getElementById('clr-clear-btn')?.addEventListener('click', async () => {
  if (document.getElementById('clr-history')?.checked)   await window.electronAPI.historyClear();
  if (document.getElementById('clr-bookmarks')?.checked) {
    const all = await window.electronAPI.bookmarksList();
    for (const b of all) await window.electronAPI.bookmarksRemove(b.url);
  }
  closeAllPanels();
});

/* ─── Dev addons panel ──────────────────────────────────────────── */
const $devAddonCode   = document.getElementById('devaddon-code');
const $devAddonStatus = document.getElementById('devaddon-status');

document.getElementById('devaddon-run-btn')?.addEventListener('click', async () => {
  const code = $devAddonCode?.value?.trim();
  if (!code) return;
  try {
    await window.electronAPI.injectScript(code);
    if ($devAddonStatus) { $devAddonStatus.textContent = '✓ Injected successfully'; $devAddonStatus.style.color = 'var(--accent)'; }
  } catch (err) {
    if ($devAddonStatus) { $devAddonStatus.textContent = '✗ ' + err.message; $devAddonStatus.style.color = 'var(--danger)'; }
  }
  setTimeout(() => { if ($devAddonStatus) $devAddonStatus.textContent = ''; }, 3000);
});
document.getElementById('devaddon-store-btn')?.addEventListener('click', () => { window.electronAPI.newTab('https://chrome.google.com/webstore'); closeAllPanels(); });
document.getElementById('devaddon-mgr-btn')  ?.addEventListener('click', () => { window.electronAPI.newTab('http://127.0.0.1:8080/ui/addons.html'); closeAllPanels(); });

/* ─── Zoom indicator ────────────────────────────────────────────── */
const $zoomIndicator = document.getElementById('zoom-indicator');
const $zoomPct       = document.getElementById('zoom-pct');

async function refreshZoomIndicator() {
  const zl  = await window.electronAPI.zoomGet();
  const pct = Math.round(100 * Math.pow(1.2, zl));
  if ($zoomPct) $zoomPct.textContent = pct + '%';
  if ($zoomIndicator) $zoomIndicator.classList.toggle('hidden', zl === 0);
}

document.getElementById('btn-zoom-in')   ?.addEventListener('click', () => window.electronAPI.zoomIn().then(refreshZoomIndicator));
document.getElementById('btn-zoom-out')  ?.addEventListener('click', () => window.electronAPI.zoomOut().then(refreshZoomIndicator));
document.getElementById('btn-zoom-reset')?.addEventListener('click', () => window.electronAPI.zoomReset().then(refreshZoomIndicator));

/* ── Tab snapshot — push page HTML to gateway after navigation ──────── */
let _snapshotTimer = null;
window.electronAPI.onUrlChanged(({ id }) => {
  // Only push for the active tab; wait for page to settle before reading HTML
  if (id !== _activeId) return;
  clearTimeout(_snapshotTimer);
  _snapshotTimer = setTimeout(async () => {
    try {
      const snap = await window.electronAPI.getTabSnapshot();
      if (!snap || !snap.html) return;
      fetch('http://127.0.0.1:8080/tab/snapshot', {
        method:  'PUT',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(snap),
      }).catch(() => {/* gateway may not be ready yet */});
    } catch { /* ignore */ }
  }, 1800);
});

/* ── Addon injection polling — execute JS queued by agents ──────────── */
setInterval(async () => {
  try {
    const r = await fetch('http://127.0.0.1:8080/tab/inject-queue');
    if (!r.ok) return;
    const items = await r.json();
    for (const item of items) {
      try {
        await window.electronAPI.injectScript(item.code_js);
        console.log('[addons] injected:', item.name);
      } catch (e) {
        console.error('[addons] inject error for', item.name, e);
      }
    }
  } catch { /* gateway not reachable */ }
}, 3000);

/* ── Init ───────────────────────────────────────────────────────── */
async function init() {
  setGwDot('loading');
  setNavState(false, false);

  // Load current tabs from main process
  try {
    const tabs = await window.electronAPI.getTabs();
    // Determine active tab (first one, or annotated)
    if (tabs.length) {
      _activeId = tabs.find(t => t.active)?.id ?? tabs[0].id;
      renderTabs(tabs);
      const url = await window.electronAPI.getActiveUrl();
      updateAddressBar(url);
    }
  } catch (err) {
    console.error('init tabs failed', err);
  }

  await pollGateway();
  setInterval(pollGateway, 5000);
  refreshZoomIndicator();

  // ── Auto-update notifications ─────────────────────────────────────────────
  const $updateBar     = document.getElementById('update-bar');
  const $updateMsg     = document.getElementById('update-msg');
  const $updateVersion = document.getElementById('update-version');
  const $btnInstall    = document.getElementById('btn-update-install');
  const $btnDismiss    = document.getElementById('btn-update-dismiss');

  if (window.electronAPI?.onUpdateAvailable) {
    window.electronAPI.onUpdateAvailable((info) => {
      if ($updateVersion) $updateVersion.textContent = info.version || '';
      if ($updateMsg)     $updateMsg.firstChild.textContent = 'Update available: ';
      if ($btnInstall)    $btnInstall.textContent = 'Download & Install';
      $updateBar?.classList.remove('hidden');
    });

    window.electronAPI.onUpdateDownloaded(() => {
      if ($updateMsg)  $updateMsg.firstChild.textContent = 'Update downloaded — ';
      if ($btnInstall) $btnInstall.textContent = 'Restart & Install';
      $updateBar?.classList.remove('hidden');
    });

    $btnInstall?.addEventListener('click', () => {
      window.electronAPI.installUpdate();
    });

    $btnDismiss?.addEventListener('click', () => {
      $updateBar?.classList.add('hidden');
    });
  }
}

init();
