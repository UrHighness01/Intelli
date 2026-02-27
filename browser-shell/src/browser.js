'use strict';

/* ── State ─────────────────────────────────────────────────────── */
let _tabs      = [];   // [{ id, title, url, favicon }]
let _activeId  = null;
let _canBack   = false;
let _canFwd    = false;
let _currentUrl = '';

/* ── DOM refs ───────────────────────────────────────────────────── */

/* Chrome-style tab groups — state */
const CG_COLORS = [
  { id: 'grey',   hex: '#5f6368' },
  { id: 'blue',   hex: '#1a73e8' },
  { id: 'red',    hex: '#d93025' },
  { id: 'yellow', hex: '#f29900' },
  { id: 'green',  hex: '#1e8e3e' },
  { id: 'pink',   hex: '#e52592' },
  { id: 'purple', hex: '#a142f4' },
  { id: 'cyan',   hex: '#007b83' },
];
let _chromGroups = (() => { try { return JSON.parse(localStorage.getItem('chg') || '[]'); } catch { return []; } })();
let _cgNextId    = _chromGroups.length ? Math.max(..._chromGroups.map(g => g.id)) + 1 : 1;
let _cgPanelCtx  = null;   // { action, tabId?, groupId? }
let _chgSelColor = CG_COLORS[1].hex;
let _cgCtxId       = null;   // groupId for chip ctx menu
let _dragChipGrpId = null;   // groupId being dragged for chip reorder

const $tabs    = document.getElementById('tabs');
const $newTab  = document.getElementById('new-tab-btn');
const $back    = document.getElementById('btn-back');
const $fwd     = document.getElementById('btn-forward');
const $reload  = document.getElementById('btn-reload');
const $home    = document.getElementById('btn-home');
const $urlIn   = document.getElementById('url-input');
const $gwDot   = document.getElementById('gw-dot');

/* ── Tab rendering ──────────────────────────────────────────────── */
function _makeTabSide(t) {
  const isAdminHub = t.url && t.url.startsWith('http://127.0.0.1:8080/ui/');
  const displayTitle = isAdminHub ? ('⚙ ' + (t.title || 'Admin Hub')) : (t.title || t.url || 'New Tab');
  const side = document.createElement('div');
  side.className = 'split-side';
  side.dataset.id = t.id;
  const fav = document.createElement('img');
  fav.className = 'tab-favicon split-fav' + (t.favicon ? '' : ' hidden');
  fav.src = t.favicon || '';
  fav.alt = '';
  const ttl = document.createElement('span');
  ttl.className = 'tab-title';
  ttl.textContent = displayTitle;
  side.append(fav, ttl);
  return side;
}

/* ── Chrome-style tab group helpers ─────────────────────────────── */
function _cgSave()         { localStorage.setItem('chg', JSON.stringify(_chromGroups)); }
function _cgGroupOf(tabId) { return _chromGroups.find(g => g.tabIds.includes(Number(tabId))) || null; }

function _cgMakeChip(g) {
  const chip = document.createElement('div');
  chip.className = 'tab-group-chip' + (g.collapsed ? ' collapsed' : '');
  chip.dataset.groupId = g.id;
  chip.style.background = g.color;
  const dot = document.createElement('span');
  dot.className = 'tab-group-chip-dot';
  const lbl = document.createElement('span');
  lbl.className = 'tab-group-chip-label';
  lbl.textContent = g.name || '';
  chip.append(dot, lbl);
  chip.title = g.name ? `Groupe : ${g.name}` : 'Groupe sans nom';
  chip.addEventListener('click', e => {
    e.stopPropagation();
    const wasCollapsed = g.collapsed;
    g.collapsed = !g.collapsed;
    _cgSave();
    renderTabs(_tabs);
    // When re-expanding: if a split pair in this group is paused, auto-restore it
    if (wasCollapsed && !g.collapsed) {
      const pairedTab = _tabs.find(t =>
        g.tabIds.includes(Number(t.id)) && t.pairId != null && t.pairPaused
      );
      if (pairedTab) window.electronAPI.switchTab(pairedTab.id);
    }
  });
  chip.addEventListener('dblclick', e => {
    e.stopPropagation();
    _cgOpenGroupPanel('rename', { groupId: g.id });
  });
  chip.addEventListener('contextmenu', e => {
    e.preventDefault();
    e.stopPropagation();
    window.electronAPI.showGroupCtx(g.id);
  });

  // ── Drag-to-reorder chip ─────────────────────────────────────────────────
  chip.draggable = true;
  chip.addEventListener('dragstart', e => {
    _dragChipGrpId = g.id;
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('chip-group-id', String(g.id));
    requestAnimationFrame(() => chip.classList.add('chip-dragging'));
  });
  chip.addEventListener('dragend', () => {
    chip.classList.remove('chip-dragging');
    _dragChipGrpId = null;
    document.querySelectorAll('.chip-drag-over').forEach(el => el.classList.remove('chip-drag-over'));
  });
  chip.addEventListener('dragover', e => {
    if (_dragChipGrpId === null || _dragChipGrpId === g.id) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    chip.classList.add('chip-drag-over');
  });
  chip.addEventListener('dragleave', e => {
    if (!chip.contains(e.relatedTarget)) chip.classList.remove('chip-drag-over');
  });
  chip.addEventListener('drop', e => {
    e.preventDefault();
    e.stopPropagation();
    chip.classList.remove('chip-drag-over');
    if (_dragChipGrpId === null || _dragChipGrpId === g.id) return;
    const dragGrp = _chromGroups.find(x => x.id === _dragChipGrpId);
    if (!dragGrp) return;
    // Find first tab of target group (g) in current tab order (_tabs)
    const targetFirstTab = _tabs.find(t => g.tabIds.includes(Number(t.id)));
    if (!targetFirstTab) return;
    // Reorder tabs in main process
    window.electronAPI.reorderGroup(dragGrp.tabIds, targetFirstTab.id);
    // Mirror reorder in _chromGroups array (cosmetic — renderTabs derives order from tabOrder)
    const fromIdx = _chromGroups.findIndex(x => x.id === _dragChipGrpId);
    const toIdx   = _chromGroups.findIndex(x => x.id === g.id);
    if (fromIdx !== -1 && toIdx !== -1) {
      const [moved] = _chromGroups.splice(fromIdx, 1);
      _chromGroups.splice(toIdx, 0, moved);
      _cgSave();
    }
    _dragChipGrpId = null;
  });

  return chip;
}

function renderTabs(tabs) {
  _tabs = tabs;
  // Cancel any pending/open preview on re-render
  _cancelTabPreview();
  $tabs.innerHTML = '';
  // Grouped tab IDs — these are hidden from the tab bar
  const groupedIds = new Set((_groupedTabs || []).map(g => g.id));

  // Build pair map from pairId fields: pairId → { left, right }
  const pairMap = new Map();
  for (const t of tabs) {
    if (t.pairId !== null && t.pairId !== undefined) {
      if (!pairMap.has(t.pairId)) pairMap.set(t.pairId, { left: null, right: null });
      const entry = pairMap.get(t.pairId);
      if (t.pairLeft) entry.left  = t;
      else            entry.right = t;
    }
  }
  const pairedIds = new Set();
  for (const [, entry] of pairMap) {
    if (entry.left)  pairedIds.add(entry.left.id);
    if (entry.right) pairedIds.add(entry.right.id);
  }

  // ── Chrome groups cleanup (remove closed tabs, drop empty groups) ──────────
  const _cgActiveIds = new Set(tabs.map(t => Number(t.id)));
  const _cgSnap = JSON.stringify(_chromGroups);
  _chromGroups = _chromGroups
    .map(g => ({ ...g, tabIds: g.tabIds.filter(id => _cgActiveIds.has(id)) }))
    .filter(g => g.tabIds.length > 0);  // drop empty groups
  if (JSON.stringify(_chromGroups) !== _cgSnap) _cgSave();

  // ── Sort tabs for display: admin-hub first, split pills second, rest after ──
  const _isAdminHub  = t => !!(t.url && t.url.startsWith('http://127.0.0.1:8080/ui/'));
  const _isPairLeft  = t => pairedIds.has(t.id) && t.pairLeft;
  const _renderRank  = t => _isAdminHub(t) ? 0 : _isPairLeft(t) ? 1 : 2;
  const sortedTabs   = [...tabs].sort((a, b) => _renderRank(a) - _renderRank(b));

  // First occurrence of each group in tab-order (where the chip is rendered)
  const _cgFirstOf = new Map();  // groupId → tabId
  for (const t of sortedTabs) {
    const _cg0 = _cgGroupOf(t.id);
    if (_cg0 && !_cgFirstOf.has(_cg0.id)) _cgFirstOf.set(_cg0.id, t.id);
  }

  for (const t of sortedTabs) {
    if (groupedIds.has(t.id)) continue;  // inactive tabs hidden from tab bar

    // ── Chrome-style tab group chip (before the first tab of each group) ──────
    const _cgg = _cgGroupOf(t.id);
    if (_cgg && _cgFirstOf.get(_cgg.id) === t.id) {
      $tabs.appendChild(_cgMakeChip(_cgg));
    }
    if (_cgg && _cgg.collapsed) continue;

    // ── Merged split pill (render once, at the left tab's position) ──────────
    if (pairedIds.has(t.id)) {
      if (!t.pairLeft) continue;   // skip right tab — rendered inside the merged pill
      const pEntry = pairMap.get(t.pairId);
      if (!pEntry || !pEntry.left || !pEntry.right) { /* fall through — render as normal tab */ }
      else {
      const leftTab  = pEntry.left;
      const rightTab = pEntry.right;
      const isPaused = leftTab.pairPaused || false;

      const el = document.createElement('div');
      el.className = 'tab split-merged' + (isPaused ? ' paused' : ' active');
      el.dataset.leftId  = leftTab.id;
      el.dataset.rightId = rightTab.id;

      const leftSide  = _makeTabSide(leftTab);
      leftSide.classList.add('split-side-left');
      const divider = document.createElement('span');
      divider.className = 'split-divider';
      const rightSide = _makeTabSide(rightTab);
      rightSide.classList.add('split-side-right');
      // Highlight the focused side using pairFocused sent from main
      if (!isPaused) {
        if (leftTab.pairFocused)       leftSide.classList.add('split-focused');
        else if (rightTab.pairFocused) rightSide.classList.add('split-focused');
        else                           leftSide.classList.add('split-focused'); // default
      }

      // Apply initial ratio to flex widths for visual sizing
      const _initRatio = isPaused ? 0.5 : (leftTab.pairRatio ?? 0.5);
      leftSide.style.flex  = String(_initRatio);
      rightSide.style.flex = String(1 - _initRatio);

      el.append(leftSide, divider, rightSide);

      // Left side click → focus left side (URL bar shows left URL)
      leftSide.addEventListener('click', e => {
        e.stopPropagation();
        window.electronAPI.switchTab(leftTab.id);
      });
      // Right side click → focus right side (URL bar shows right URL, no swap)
      rightSide.addEventListener('click', e => {
        e.stopPropagation();
        window.electronAPI.switchTab(rightTab.id);
      });
      // Right-click → context menu
      el.addEventListener('contextmenu', e => {
        e.preventDefault();
        _cancelTabPreview();
        // Show ctx for whichever side was clicked
        const clickedRight = e.target.closest('.split-side-right');
        const ctxTab = clickedRight ? rightTab : leftTab;
        window.electronAPI.showTabCtx(ctxTab.id, ctxTab.url || '', _chromGroups);
      });

      if (!isPaused) {
        // ── Divider drag — slide to resize the split ratio ──────────────────
        divider.addEventListener('mousedown', e => {
          if (e.button !== 0) return;
          e.preventDefault();
          e.stopPropagation();
          divider.classList.add('split-divider-dragging');
          const onMove = me => {
            const totalW = window.innerWidth || document.documentElement.clientWidth;
            const ratio  = Math.max(0.15, Math.min(0.85, me.clientX / totalW));
            leftSide.style.flex  = String(ratio);
            rightSide.style.flex = String(1 - ratio);
            window.electronAPI.setSplitRatio(leftTab.id, ratio);
          };
          const onUp = () => {
            divider.classList.remove('split-divider-dragging');
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
          };
          document.addEventListener('mousemove', onMove);
          document.addEventListener('mouseup', onUp);
        });

        // ── Drag-to-swap: glisser un côté sur l'autre pour inverser gauche/droite ──
        const _setupSplitDrag = (dragSide, dropSide) => {
          dragSide.draggable = true;
          dragSide.addEventListener('dragstart', e => {
            e.dataTransfer.setData('split-swap', String(leftTab.id));
            e.dataTransfer.effectAllowed = 'move';
            requestAnimationFrame(() => dragSide.classList.add('split-dragging'));
          });
          dragSide.addEventListener('dragend', () => dragSide.classList.remove('split-dragging'));
          dropSide.addEventListener('dragover', e => {
            if (e.dataTransfer.types.includes('split-swap')) {
              e.preventDefault();
              e.dataTransfer.dropEffect = 'move';
              dropSide.classList.add('split-drop-over');
            }
          });
          dropSide.addEventListener('dragleave', () => dropSide.classList.remove('split-drop-over'));
          dropSide.addEventListener('drop', e => {
            dropSide.classList.remove('split-drop-over');
            if (e.dataTransfer.getData('split-swap')) {
              e.stopPropagation();
              window.electronAPI.swapSplitSides(leftTab.id);
            }
          });
        };
        _setupSplitDrag(leftSide, rightSide);
        _setupSplitDrag(rightSide, leftSide);
      }
      $tabs.appendChild(el);
      continue;
      } // end else (complete pair)
    } // end if pairedIds

    // ── Normal tab ───────────────────────────────────────────────────────────
    const el = document.createElement('div');
    const _cggN = _cgGroupOf(t.id);
    el.className = 'tab' + (t.id === _activeId ? ' active' : '') + (_cggN ? ' in-group' : '');
    if (_cggN) el.style.setProperty('--tg-color', _cggN.color);
    el.dataset.id = t.id;
    // Stamp the chrome-group id so drag validation can read it from the DOM
    el.dataset.groupId = _cggN ? String(_cggN.id) : '';

    const fav = document.createElement('img');
    fav.className = 'tab-favicon' + (t.favicon ? '' : ' hidden');
    fav.src = t.favicon || '';
    fav.alt = '';

    const isAdminHub = t.url && t.url.startsWith('http://127.0.0.1:8080/ui/');
    if (isAdminHub) el.classList.add('admin-hub');
    const displayTitle = isAdminHub ? ('⚙ ' + (t.title || 'Admin Hub')) : (t.title || t.url || 'New Tab');

    const title = document.createElement('span');
    title.className = 'tab-title';
    title.textContent = displayTitle;

    const close = document.createElement('span');
    close.className = 'tab-close';
    close.textContent = '×';
    close.title = 'Close tab';
    close.addEventListener('click', e => {
      e.stopPropagation();
      window.electronAPI.closeTab(t.id);
    });

    const muteIcon = document.createElement('span');
    if (t.muted) {
      muteIcon.className = 'tab-audio-icon';
      muteIcon.textContent = '🔇';
      muteIcon.title = 'Son coupé';
    } else if (t.audible) {
      muteIcon.className = 'tab-audio-icon';
      muteIcon.textContent = '🔊';
      muteIcon.title = 'Son en cours';
    } else {
      muteIcon.className = 'tab-audio-icon hidden';
      muteIcon.textContent = '';
    }

    el.append(fav, title, muteIcon, close);
    el.addEventListener('mouseenter', () => _scheduleTabPreview(el, t));
    el.addEventListener('mouseleave', _cancelTabPreview);
    el.addEventListener('click', () => {
      if (_tabDragWasActive) { _tabDragWasActive = false; return; }
      window.electronAPI.switchTab(t.id);
    });
    if (isAdminHub) el.addEventListener('dblclick', e => { e.stopPropagation(); window.electronAPI.newTab(); });
    // Right-click → native OS context menu (renders above BrowserViews)
    el.addEventListener('contextmenu', e => {
      e.preventDefault();
      _cancelTabPreview();
      window.electronAPI.showTabCtx(t.id, t.url || '', _chromGroups);
    });
    // Mouse-based drag-to-reorder (more reliable than HTML5 DnD in Electron drag region)
    el.addEventListener('mousedown', e => {
      if (e.button !== 0) return;
      if (e.target.closest('.tab-close')) return;
      _initTabDrag(t.id, el, e.clientX);
    });

    $tabs.appendChild(el);
  }
}

/* ── Floating tab hover preview (BrowserWindow via main) ── */
let _tpTimer  = null;

/* ── Tab drag-to-reorder — mouse-based (Chrome-style ghost + drop line) ─── */

/** Returns the chrome-group that contains tabId, or null if ungrouped. */
function _cgOf(tabId) {
  return _chromGroups.find(g => g.tabIds.includes(Number(tabId))) ?? null;
}

/**
 * Returns the groupId string stamped on a rendered tab element, or '' if ungrouped.
 * Reading from the DOM is more reliable than looking up _chromGroups at drag time.
 */
function _domGroupId(tabId) {
  return $tabs.querySelector(`.tab[data-id="${tabId}"]`)?.dataset.groupId ?? '';
}

/**
 * Returns true if dropping before targetId is allowed for the current drag.
 * Rules:
 *  – A grouped tab may only be reordered within its own group.
 *  – A tab must not be dropped BETWEEN two consecutive members of a group it
 *    doesn't belong to (that would visually split the group).
 *  – A grouped tab may only be appended at the end if the last tab is in the
 *    same group (otherwise the tab would leave its group's contiguous block).
 */
function _dragDropIsValid(targetId) {
  const allTabs = Array.from($tabs.querySelectorAll('.tab:not(.dragging)'));

  // --- Appending at the very end ---
  if (targetId === null) {
    if (_dragTabGroupId === '') return true; // ungrouped tab can always go last
    // Grouped tab: only valid if the current last visible tab is in the same group
    if (allTabs.length === 0) return true;
    const lastGid = allTabs[allTabs.length - 1].dataset.groupId ?? '';
    return lastGid === _dragTabGroupId;
  }

  const targetGrpId = _domGroupId(targetId);

  // Rule 1: a grouped tab may only be reordered within its own group
  if (_dragTabGroupId !== '' && _dragTabGroupId !== targetGrpId) return false;

  // Rule 2: the gap (prev_tab → targetId) must not be INSIDE a foreign group.
  // If the tab just before the drop position is in the same group as targetId,
  // and that group is not the dragged tab's group, inserting here would split it.
  const idx = allTabs.findIndex(el => el.dataset.id == String(targetId));
  if (idx > 0) {
    const prevGid = allTabs[idx - 1].dataset.groupId ?? '';
    if (prevGid !== '' && prevGid === targetGrpId && _dragTabGroupId !== targetGrpId) {
      return false; // would split the group
    }
  }

  return true;
}

let _dragTabId       = null;   // id of tab being dragged
let _dragTabEl       = null;   // its DOM element
let _dragGhost       = null;   // floating visual clone
let _dragActive      = false;  // true once movement threshold passed
let _dragTabGroupId  = '';     // data-group-id of the dragged tab ('' = ungrouped)
let _dragStartX      = 0;
let _dragDropX       = 0;
let _tabDragWasActive = false; // suppresses the residual click after a drag

function _initTabDrag(tabId, el, startX) {
  _dragTabId       = tabId;
  _dragTabEl       = el;
  _dragStartX      = startX;
  _dragActive      = false;
  // Capture the group id from the DOM element right now — reliable snapshot
  _dragTabGroupId  = el.dataset.groupId ?? '';
}

function _getTabDropTarget(x) {
  const tabEls = Array.from($tabs.querySelectorAll('.tab:not(.dragging)'));
  for (let i = 0; i < tabEls.length; i++) {
    const r = tabEls[i].getBoundingClientRect();
    if (x < r.right) {
      return x <= r.left + r.width / 2
        ? Number(tabEls[i].dataset.id)
        : (tabEls[i + 1] ? Number(tabEls[i + 1].dataset.id) : null);
    }
  }
  return null;
}

function _updateTabDropLine(x) {
  let line = document.getElementById('_tdi');
  if (!line) {
    line = document.createElement('div');
    line.id = '_tdi';
    line.className = 'tab-drop-line';
    document.body.appendChild(line);
  }
  const tid = _getTabDropTarget(x);
  // Hide line when no target or when the drop would cross a group boundary
  if (tid === null || (_dragTabId !== null && !_dragDropIsValid(tid))) {
    line.style.display = 'none'; return;
  }
  const targetEl = $tabs.querySelector(`[data-id="${tid}"]`);
  if (!targetEl) { line.style.display = 'none'; return; }
  const r = targetEl.getBoundingClientRect();
  line.style.display = 'block';
  line.style.left    = r.left + 'px';
  line.style.top     = r.top  + 'px';
}

function _cleanupTabDrag() {
  _dragTabEl?.classList.remove('dragging');
  _dragGhost?.remove();
  document.getElementById('_tdi')?.remove();
  _dragTabId = null; _dragTabEl = null; _dragGhost = null;
  _dragTabGroupId = '';
  _dragActive = false; _dragStartX = 0; _dragDropX = 0;
}

document.addEventListener('mousemove', function _onTabDragMove(e) {
  if (_dragTabId === null) return;
  if (!_dragActive) {
    if (Math.abs(e.clientX - _dragStartX) < 8) return;
    _dragActive = true;
    _cancelTabPreview();
    _dragTabEl.classList.add('dragging');
    const r   = _dragTabEl.getBoundingClientRect();
    const fav = _dragTabEl.querySelector('.tab-favicon:not(.hidden)');
    const tit = _dragTabEl.querySelector('.tab-title');
    _dragGhost = document.createElement('div');
    _dragGhost.className = 'tab-drag-ghost';
    _dragGhost.style.width = Math.min(r.width, 200) + 'px';
    if (fav) {
      const fi = fav.cloneNode(true);
      fi.style.cssText = 'width:14px;height:14px;flex-shrink:0;object-fit:contain;';
      _dragGhost.appendChild(fi);
    }
    const sp = document.createElement('span');
    sp.textContent = tit ? tit.textContent : '';
    sp.style.cssText = 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;';
    _dragGhost.appendChild(sp);
    document.body.appendChild(_dragGhost);
  }
  const r = _dragTabEl.getBoundingClientRect();
  _dragGhost.style.left = (e.clientX - _dragGhost.offsetWidth * 0.3) + 'px';
  _dragGhost.style.top  = r.top + 'px';
  _dragDropX = e.clientX;
  _updateTabDropLine(e.clientX);
});

document.addEventListener('mouseup', function _onTabDragUp() {
  if (_dragTabId === null) return;
  if (_dragActive) {
    _tabDragWasActive = true;
    const targetId = _getTabDropTarget(_dragDropX);
    if (targetId !== null && targetId !== _dragTabId && _dragDropIsValid(targetId)) {
      window.electronAPI.reorderTab(_dragTabId, targetId);
    }
  }
  _cleanupTabDrag();
});

function _scheduleTabPreview(tabEl, t) {
  if (t.id === _activeId) return;   // no preview for the already-active tab
  _cancelTabPreview();
  _tpTimer = setTimeout(() => {
    _tpTimer = null;
    const rect = tabEl.getBoundingClientRect();
    // Convert to screen coords
    const sx = window.screenX + rect.left;
    const sy = window.screenY + rect.bottom + 4;
    window.electronAPI.showTabPreview({
      tabId:   t.id,
      screenX: Math.round(sx),
      screenY: Math.round(sy),
      title:   t.title || '',
      url:     t.url   || '',
      favicon: t.favicon || null,
    });
  }, 700);
}

function _cancelTabPreview() {
  clearTimeout(_tpTimer);
  _tpTimer = null;
  window.electronAPI.hideTabPreview();
}

// Always hide preview when the chrome window loses focus or is hidden
window.addEventListener('blur',              _cancelTabPreview);
document.addEventListener('visibilitychange', () => {
  if (document.hidden) _cancelTabPreview();
});

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
// Merge with existing _tabs to preserve cached favicons, muted and audible state
// (the main process sends favicon:null because favicons arrive via a separate IPC event).
window.electronAPI.onTabsUpdated(incoming => {
  const faviconCache = Object.fromEntries(_tabs.map(t => [t.id, t.favicon]));
  const audioCache   = Object.fromEntries(_tabs.map(t => [t.id, { muted: t.muted, audible: t.audible }]));
  const merged = incoming.map(t => ({
    ...t,
    favicon:  faviconCache[t.id] ?? t.favicon,
    muted:    t.muted   !== undefined ? t.muted   : (audioCache[t.id]?.muted   ?? false),
    audible:  t.audible !== undefined ? t.audible : (audioCache[t.id]?.audible ?? false),
  }));
  const active = merged.find(t => t.active);

  // Record last-seen time for the tab that is LOSING focus (was active before)
  if (_activeId && active && active.id !== _activeId) {
    _tabLastSeen[_activeId] = Date.now();
  }
  // Initialize last-seen for any brand-new tab that's not active
  for (const t of merged) {
    if (!t.active && _tabLastSeen[t.id] === undefined) {
      _tabLastSeen[t.id] = Date.now();
    }
  }

  if (active) {
    _activeId = active.id;
    _tgMarkActive(active.id);
  }

  // Purge grouped entries whose tab was closed externally
  const ids = new Set(merged.map(t => t.id));
  const before = _groupedTabs.length;
  _groupedTabs = _groupedTabs.filter(g => ids.has(g.id));
  if (_groupedTabs.length !== before) _tgSave();

  renderTabs(merged);
  const activeTab = merged.find(t => t.id === _activeId);
  if (activeTab?.url) updateAddressBar(activeTab.url);
  renderTabGroupList();
});

window.electronAPI.onNavState(({ id, canGoBack, canGoForward }) => {
  if (id === _activeId) setNavState(canGoBack, canGoForward);
});

window.electronAPI.onTabMuted(({ id, muted, audible }) => {
  const tab = _tabs.find(t => t.id === id);
  if (tab) {
    tab.muted = muted;
    if (audible !== undefined) tab.audible = audible;
    renderTabs(_tabs);
  }
});

window.electronAPI.onSplitChanged(() => {
  // split state tracked via tabs-updated
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
document.getElementById('intelli-logo')?.addEventListener('click', () => window.electronAPI.goHome());
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
  if (ctrl && e.shiftKey && (e.key === 'g' || e.key === 'G')) { e.preventDefault(); openPanel('inactive-tabs'); }

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

const $backdrop  = document.getElementById('panel-backdrop');
let _openPanel  = null;

async function openPanel(name) {
  closeAllPanels();
  const el = document.getElementById('panel-' + name);
  if (!el) return;
  _openPanel = name;
  el.classList.remove('hidden');
  $backdrop.classList.remove('hidden');
  window.electronAPI.setPanelVisible(true);
  if (name === 'bookmarks')       loadBookmarksPanel();
  if (name === 'history')         loadHistoryPanel();
  if (name === 'settings')        { renderTabGroupList(); loadSettingsPanel(); }
}

function closeAllPanels() {
  document.querySelectorAll('.side-panel').forEach(p => {
    p.classList.add('hidden');
    p.style.visibility = '';
  });
  document.getElementById('panel-inactive-tabs')?.classList.add('hidden');
  $backdrop.classList.add('hidden');
  _openPanel = null;
  window.electronAPI.setPanelVisible(false);
}

$backdrop.addEventListener('click', closeAllPanels);
document.querySelectorAll('.panel-close').forEach(btn => btn.addEventListener('click', closeAllPanels));

// ── Panel onglets inactifs — boutons footer ──────────────────────────────────
document.getElementById('tg-panel-restore-all')?.addEventListener('click', () => {
  const all = [..._groupedTabs];
  _groupedTabs = [];
  _tgSave();
  closeAllPanels();
  (async () => {
    for (const g of all) {
      const exists = _tabs.some(t => Number(t.id) === Number(g.id));
      if (!exists) await window.electronAPI.newTab(g.url || '');
    }
    renderTabs(_tabs);
    renderTabGroupList();
  })();
});
document.getElementById('tg-panel-clear-all')?.addEventListener('click', async () => {
  const ids = _groupedTabs.map(t => Number(t.id));
  for (const id of ids) await window.electronAPI.closeTab(id);
  _groupedTabs = [];
  _tgSave();
  renderTabs(_tabs);
  closeAllPanels();
  renderTabGroupList();
});
document.getElementById('ita-search')?.addEventListener('input', () => _tgFillPanel());

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

/* ─── Bookmarks bar ────────────────────────────────────── */
const $bookmarksBar    = document.getElementById('bookmarks-bar');

/* ── Horizontal wheel scroll — tab bar & bookmarks bar ─── */
[document.getElementById('tabs'), document.getElementById('bookmarks-bar')].forEach(el => {
  if (!el) return;
  el.addEventListener('wheel', e => {
    // Use vertical delta for horizontal scroll (classic one-finger scroll)
    const delta = e.deltaY !== 0 ? e.deltaY : e.deltaX;
    if (Math.abs(delta) < 2) return;
    e.preventDefault();
    el.scrollBy({ left: delta * 1.5, behavior: 'smooth' });
  }, { passive: false });
});

/* ── Chip-over-standalone-tab drop ─────────────────────────────────────────
   Allows a group chip to be dragged onto any standalone .tab so the whole
   group is repositioned before that tab (left half) or after it (right half).*/
(function _setupChipOverTabDrop() {
  const tabsEl = document.getElementById('tabs');
  if (!tabsEl) return;

  // Returns the tab ID to insert before, accounting for left/right half of tabEl.
  // Returns null to append at end.
  function _chipInsertBeforeId(tabEl, clientX) {
    const r = tabEl.getBoundingClientRect();
    const onRightHalf = clientX > r.left + r.width / 2;
    if (!onRightHalf) {
      return Number(tabEl.dataset.id ?? tabEl.dataset.leftId ?? -1);
    }
    // Find the next sibling .tab (skip chips)
    let next = tabEl.nextElementSibling;
    while (next && !next.classList.contains('tab')) next = next.nextElementSibling;
    if (!next) return null; // drop at end
    return Number(next.dataset.id ?? next.dataset.leftId ?? -1);
  }

  function _clearChipTabHighlight() {
    document.querySelectorAll('.tab.chip-drag-over-left, .tab.chip-drag-over-right')
      .forEach(el => { el.classList.remove('chip-drag-over-left', 'chip-drag-over-right'); });
  }

  /**
   * Returns true if dropping the dragged chip at this position is valid.
   * Blocks if the drop would insert the group BETWEEN two consecutive tabs
   * that both belong to the same foreign group (which would split it).
   */
  function _chipDropIsValid(tabEl, clientX, dragGrpId) {
    const r = tabEl.getBoundingClientRect();
    const onRightHalf = clientX > r.left + r.width / 2;
    const dragGrpIdStr = String(dragGrpId);
    if (onRightHalf) {
      // Gap between tabEl and its next .tab
      let next = tabEl.nextElementSibling;
      while (next && !next.classList.contains('tab')) next = next.nextElementSibling;
      if (next) {
        const leftGid  = tabEl.dataset.groupId ?? '';
        const rightGid = next.dataset.groupId ?? '';
        if (leftGid !== '' && leftGid === rightGid && leftGid !== dragGrpIdStr) return false;
      }
    } else {
      // Gap between prev .tab and tabEl
      let prev = tabEl.previousElementSibling;
      while (prev && !prev.classList.contains('tab')) prev = prev.previousElementSibling;
      if (prev) {
        const leftGid  = prev.dataset.groupId ?? '';
        const rightGid = tabEl.dataset.groupId ?? '';
        if (leftGid !== '' && leftGid === rightGid && leftGid !== dragGrpIdStr) return false;
      }
    }
    return true;
  }

  tabsEl.addEventListener('dragover', e => {
    if (_dragChipGrpId === null) return;
    const tabEl = e.target.closest('.tab');
    if (!tabEl) return;
    const dragGrp = _chromGroups.find(x => x.id === _dragChipGrpId);
    if (!dragGrp) return;
    const tabId = Number(tabEl.dataset.id ?? tabEl.dataset.leftId ?? -1);
    if (dragGrp.tabIds.includes(tabId)) return;
    // Block drop that would insert the group between two tabs of a foreign group
    if (!_chipDropIsValid(tabEl, e.clientX, _dragChipGrpId)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    _clearChipTabHighlight();
    const r = tabEl.getBoundingClientRect();
    const onRight = e.clientX > r.left + r.width / 2;
    tabEl.classList.add(onRight ? 'chip-drag-over-right' : 'chip-drag-over-left');
  });
  tabsEl.addEventListener('dragleave', e => {
    if (_dragChipGrpId === null) return;
    const tabEl = e.target.closest('.tab');
    if (!tabEl) return;
    // Only clear if actually leaving the tab (not entering a child element)
    if (!tabEl.contains(e.relatedTarget)) {
      tabEl.classList.remove('chip-drag-over-left', 'chip-drag-over-right');
    }
  });
  tabsEl.addEventListener('drop', e => {
    if (_dragChipGrpId === null) return;
    const tabEl = e.target.closest('.tab');
    if (!tabEl) return;
    const dragGrp = _chromGroups.find(x => x.id === _dragChipGrpId);
    const tabId   = Number(tabEl.dataset.id ?? tabEl.dataset.leftId ?? -1);
    if (!dragGrp || dragGrp.tabIds.includes(tabId)) return;
    // Block drop that would insert the group between two tabs of a foreign group
    if (!_chipDropIsValid(tabEl, e.clientX, _dragChipGrpId)) return;
    e.preventDefault();
    e.stopPropagation();
    _clearChipTabHighlight();
    document.querySelectorAll('.chip-drag-over').forEach(el => el.classList.remove('chip-drag-over'));
    const insertBeforeId = _chipInsertBeforeId(tabEl, e.clientX);
    if (insertBeforeId !== null) {
      window.electronAPI.reorderGroup(dragGrp.tabIds, insertBeforeId);
    } else {
      window.electronAPI.reorderGroup(dragGrp.tabIds, -1);
    }
    _dragChipGrpId = null;
  });
})();

let _bmBarVisible      = false;

function _bmBarSetVisible(v) {
  _bmBarVisible = !!v;
  // Update --chrome-h on :root so both html + body resize together
  if (_bmBarVisible) {
    document.documentElement.style.setProperty('--chrome-h',
      'calc(var(--tab-h) + var(--addr-h) + var(--bm-bar-h))');
  } else {
    document.documentElement.style.removeProperty('--chrome-h');
  }
  if ($bookmarksBar) $bookmarksBar.classList.toggle('hidden', !_bmBarVisible);
}

async function renderBookmarksBar(bm) {
  if (!$bookmarksBar || !_bmBarVisible) return;
  if (!bm) bm = await window.electronAPI.bookmarksList();
  $bookmarksBar.innerHTML = '';
  if (!bm.length) {
    const hint = document.createElement('span');
    hint.className = 'bm-bar-hint';
    hint.textContent = 'Ajoutez des favoris ici avec Ctrl+D';
    $bookmarksBar.appendChild(hint);
    return;
  }
  // Groups always on the left, regular bookmarks on the right
  const sortedBm = [...bm].sort((a, b) => {
    if (a.type === 'group' && b.type !== 'group') return -1;
    if (a.type !== 'group' && b.type === 'group') return  1;
    return 0;
  });
  for (const b of sortedBm) {
    const chip = document.createElement('button');
    chip.className = 'bm-chip';

    if (b.type === 'group') {
      // ── Group bookmark: colored dot + group name ──────────────────
      chip.title = b.name
        ? `${b.name} (${(b.tabs || []).length} onglets)`
        : `Groupe (${(b.tabs || []).length} onglets)`;
      const dot = document.createElement('span');
      dot.className = 'bm-chip-group-dot';
      dot.style.background = b.color || '#888';
      chip.appendChild(dot);
      const label = document.createElement('span');
      label.className = 'bm-chip-label';
      label.textContent = b.name || '';
      chip.appendChild(label);
      chip.addEventListener('click', async () => {
        if (!(b.tabs || []).length) return;
        // Remove from bookmarks bar first
        await window.electronAPI.bookmarksRemove(b.url);
        // Create all tabs then add the group atomically (avoids cleanup race)
        const tabIds = [];
        for (const t of (b.tabs || [])) {
          const id = await window.electronAPI.newTab(t.url);
          if (id != null) tabIds.push(id);
        }
        if (tabIds.length) {
          _chromGroups.push({ id: Date.now(), name: b.name, color: b.color, collapsed: false, tabIds });
          _cgSave();
          // Fetch fresh tab list from main so _cgActiveIds includes the new tabs
          const freshTabs = await window.electronAPI.getTabs();
          renderTabs(freshTabs);
          await window.electronAPI.switchTab(tabIds[0]);
        }
      });
    } else {
      // ── Regular bookmark ──────────────────────────────────────────
      chip.title = b.url;
      if (b.favicon) {
        const img = document.createElement('img');
        img.className = 'bm-chip-icon';
        img.src = b.favicon;
        img.onerror = () => img.remove();
        chip.appendChild(img);
      }
      const label = document.createElement('span');
      label.className = 'bm-chip-label';
      label.textContent = b.title || b.url;
      chip.appendChild(label);
      chip.addEventListener('click', () => { window.electronAPI.navigate(b.url); });
    }

    chip.addEventListener('contextmenu', e => {
      e.preventDefault();
      // Use a native OS menu so it renders above the BrowserView layer
      window.electronAPI.showBmCtx(b);
    });
    $bookmarksBar.appendChild(chip);
  }
}

// Handle actions from the native bookmarks-bar context menu
window.electronAPI.onBmCtxAction(async ({ action, bm }) => {
  if (action === 'open') {
    if (bm.type === 'group') {
      // Restore group from bookmarks (same as chip click)
      if (!(bm.tabs || []).length) return;
      await window.electronAPI.bookmarksRemove(bm.url);
      const tabIds = [];
      // Track origIdx→newId mapping to restore split pairs
      const tabMapping = []; // [{ origIdx, newId }]
      for (let i = 0; i < bm.tabs.length; i++) {
        const t = bm.tabs[i];
        const id = await window.electronAPI.newTab(t.url);
        if (id != null) { tabIds.push(id); tabMapping.push({ origIdx: i, newId: id }); }
      }
      if (tabIds.length) {
        _chromGroups.push({ id: Date.now(), name: bm.name, color: bm.color, collapsed: false, tabIds });
        _cgSave();
        const freshTabs = await window.electronAPI.getTabs();
        renderTabs(freshTabs);
        await window.electronAPI.switchTab(tabIds[0]);
        // Restore split pairs: group saved tabs by their original pairId
        const pairMap = {};
        for (const { origIdx, newId } of tabMapping) {
          const origTab = bm.tabs[origIdx];
          if (origTab.pairId != null) {
            if (!pairMap[origTab.pairId]) pairMap[origTab.pairId] = {};
            if (origTab.pairLeft) pairMap[origTab.pairId].left = newId;
            else                  pairMap[origTab.pairId].right = newId;
          }
        }
        for (const { left, right } of Object.values(pairMap)) {
          if (left != null && right != null) {
            await window.electronAPI.enterSplitPair(left, right);
          }
        }
      }
    } else {
      window.electronAPI.navigate(bm.url);
    }
  } else if (action === 'open-tab') {
    window.electronAPI.newTab(bm.url);
  } else if (action === 'edit') {
    // Open the side panel for rename (avoids unreliable prompt() in Electron)
    _cgOpenGroupPanel('bm-rename', { bm });
  } else if (action === 'delete') {
    await window.electronAPI.bookmarksRemove(bm.url);
    refreshBookmarkStar();
    await loadBookmarksPanel();
  }
});

/** Save all tabs of a group as a single group-bookmark (restores with color circle). */
async function _cgSaveGroupToBookmarks(groupId) {
  const g = _chromGroups.find(x => x.id === groupId);
  if (!g) return;
  const tabs = [];
  for (const tabId of (g.tabIds || [])) {
    const t = _tabs.find(x => x.id === tabId);
    if (t && t.url && !t.url.startsWith('about:')) {
      tabs.push({
        url:      t.url,
        title:    t.title   || t.url,
        favicon:  t.favicon || null,
        // Preserve split-pair info so restoring the group also restores the split
        pairId:   t.pairId   ?? null,
        pairLeft: t.pairLeft ?? null
      });
    }
  }
  if (tabs.length) {
    await window.electronAPI.bookmarkAddGroup({
      name:  g.name  || '',
      color: g.color || '#888',
      tabs
    });
  }
}

// Live re-render bar when bookmarks list changes
window.electronAPI.onBookmarksChanged(bm => { if (_bmBarVisible) renderBookmarksBar(bm); });
// Sync when main tells us the bar state changed (e.g. from another trigger)
window.electronAPI.onBookmarksBarState(v => {
  _bmBarSetVisible(v);
  const $t = document.getElementById('setting-bookmarks-bar');
  if ($t) $t.checked = _bmBarVisible;
  if (_bmBarVisible) renderBookmarksBar();
});

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
let _tgSelPop     = new Set();  // IDs selected for bulk actions (unused placeholder)
let _groupedTabs  = JSON.parse(localStorage.getItem('tg_grouped') || '[]')
                      .map(g => ({ ...g, id: Number(g.id) }));
                          // [{ id, url, title, favicon, groupedAt }] — ids always numeric

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

/* ── Onglets inactifs — bouton tab-bar ──────────────────────────────── */
const $tgBtn   = document.getElementById('btn-tab-groups');
const $tgBadge = document.getElementById('tg-badge');

// Open the inactive-tabs native popup (BrowserWindow with alwaysOnTop)
// so it renders above all BrowserViews without any page disruption.
$tgBtn?.addEventListener('mousedown', (e) => { e.preventDefault(); }); // prevent focus-on-click
$tgBtn?.addEventListener('click', (e) => {
  e.stopPropagation();
  $tgBtn.blur();
  const rect = $tgBtn.getBoundingClientRect();
  window.electronAPI.showInactiveTabsPopup({
    tabs: _groupedTabs,
    btnRect: {
      clientX: rect.left,
      clientY: rect.top,
      width:   rect.width,
      height:  rect.height,
    },
  });
});


window.electronAPI.onRestoreInactiveTab(id => {
  console.log('[tgRestore] received id:', id, 'grouped:', JSON.stringify(_groupedTabs.map(t => t.id)));
  const g = _groupedTabs.find(t => Number(t.id) === Number(id));
  if (g) {
    _tgRestoreTab(g, true).catch(err => console.error('[tgRestore] error:', err));
  } else {
    console.warn('[tgRestore] tab not found for id:', id);
  }
});

window.electronAPI.onRemoveInactiveTab(async id => {
  const numId = Number(id);
  await window.electronAPI.closeTab(numId);
  _groupedTabs = _groupedTabs.filter(t => Number(t.id) !== numId);
  _tgSave();
  renderTabGroupList();
});

window.electronAPI.onClearInactiveTabs(async () => {
  const ids = _groupedTabs.map(t => Number(t.id));
  for (const id of ids) await window.electronAPI.closeTab(id);
  _groupedTabs = [];
  _tgSave();
  renderTabGroupList();
});

window.electronAPI.onRestoreAllInactiveTabs(() => {
  const all = [..._groupedTabs];
  _groupedTabs = [];
  _tgSave();
  // Restore each tab sequentially (open URL for each)
  (async () => {
    for (const g of all) {
      const numId = Number(g.id);
      const exists = _tabs.some(t => Number(t.id) === numId);
      if (exists) {
        // just un-group it, don't navigate
      } else {
        await window.electronAPI.newTab(g.url || '');
      }
    }
    renderTabs(_tabs);
    renderTabGroupList();
  })();
});


/* ── Selection bars ──────────────────────────────────────────────────────── */
function _tgUpdateSelBar() {}



function _tgBuildRow(g) {
  const row = document.createElement('div');
  row.className = 'tg-item';
  row.dataset.id = g.id;
  row.title = 'Cliquer pour rouvrir';

  // Favicon
  if (g.favicon) {
    const img = document.createElement('img');
    img.src = g.favicon; img.alt = '';
    row.appendChild(img);
  } else {
    const ph = document.createElement('div');
    ph.className = 'tg-item-favicon-ph';
    row.appendChild(ph);
  }

  // Info — titre + URL
  const info = document.createElement('div');
  info.className = 'tg-item-info';
  const titleEl = document.createElement('div');
  titleEl.className = 'tg-item-title';
  titleEl.textContent = g.title || g.url || 'Onglet';
  titleEl.title = g.title || '';
  const urlEl = document.createElement('div');
  urlEl.className = 'tg-item-url';
  urlEl.textContent = g.url;
  urlEl.title = g.url;
  info.append(titleEl, urlEl);

  // Time badge
  const timeEl = document.createElement('div');
  timeEl.className = 'tg-item-time';
  timeEl.textContent = _tgRelTime(g.groupedAt);

  // Bouton × : supprimer sans restaurer
  const closeBtn = document.createElement('button');
  closeBtn.className = 'tg-item-close';
  closeBtn.textContent = '×';
  closeBtn.title = 'Supprimer de la liste';
  closeBtn.addEventListener('click', e => {
    e.stopPropagation();
    _groupedTabs = _groupedTabs.filter(t => t.id !== g.id);
    _tgSave();
    renderTabGroupList();
  });

  row.append(info, timeEl, closeBtn);

  // Clic n'importe où = restaurer l'onglet
  row.addEventListener('click', () => _tgRestoreTab(g, true));

  return row;
}

async function _tgRestoreTab(g, closeUI = true) {
  console.log('[tgRestore] restoring tab:', g.id, g.url, '| _tabs ids:', _tabs.map(t => t.id));
  // Remove from group list FIRST so _tgCheck doesn't re-group it
  _groupedTabs = _groupedTabs.filter(t => Number(t.id) !== Number(g.id));
  _tgSelPop.delete(g.id);
  _tgSave();

  _tabLastSeen[g.id] = Date.now();

  // IDs can become strings after JSON round-trip — always coerce to number
  const numId = Number(g.id);
  const tabStillExists = _tabs.some(t => Number(t.id) === numId);
  console.log('[tgRestore] tabStillExists:', tabStillExists, 'numId:', numId);

  if (tabStillExists) {
    // If this tab is in a split pair and its partner is also grouped, restore the partner too
    const thisTab = _tabs.find(t => Number(t.id) === numId);
    if (thisTab?.pairId != null) {
      const partnerId = _tabs.find(t => t.pairId === thisTab.pairId && Number(t.id) !== numId)?.id;
      if (partnerId != null && _groupedTabs.some(ge => ge.id === partnerId)) {
        _groupedTabs = _groupedTabs.filter(ge => ge.id !== partnerId);
        _tgSave();
      }
    }
    // BrowserView is still alive — just switch to it
    _activeId = numId;
    await window.electronAPI.switchTab(numId);
    renderTabs(_tabs);
  } else {
    // Tab was destroyed (e.g. app restarted, old session) — recreate it
    console.log('[tgRestore] tab not in _tabs, opening URL:', g.url);
    _activeId = null;   // will be corrected by onTabsUpdated after newTab
    await window.electronAPI.newTab(g.url || '');
  }

  if (closeUI) {
    closeAllPanels();
  }
  renderTabGroupList();
}

/* Remplit le panel onglets inactifs */
function _tgFillPanel() {
  const $list     = document.getElementById('tg-panel-list');
  const $subtitle = document.getElementById('ita-subtitle');
  const $search   = document.getElementById('ita-search');
  if (!$list) return;

  const count = _groupedTabs.length;
  if ($subtitle) {
    $subtitle.textContent = count === 0 ? 'Aucun onglet inactif'
      : count === 1 ? '1 onglet inactif'
      : `${count} onglets inactifs`;
  }

  const query = ($search?.value || '').trim().toLowerCase();
  const items = query
    ? _groupedTabs.filter(g =>
        (g.title || '').toLowerCase().includes(query) ||
        (g.url   || '').toLowerCase().includes(query))
    : _groupedTabs;

  $list.innerHTML = '';
  for (const g of items) {
    $list.appendChild(_tgBuildRow(g));
  }
}

/* Met à jour bouton tab-bar + badge + listes */
function renderTabGroupList() {
  const count = _groupedTabs.length;

  // ── Bouton tab-bar : visible si feature active OU s'il y a des onglets groupés ──
  if ($tgBtn) {
    if (_tgEnabled || count > 0) {
      $tgBtn.classList.remove('hidden');
      // Suppress stale :hover that Chromium shows when the button shifts under the cursor
      // after a DOM reflow without a real mousemove event.
      $tgBtn.classList.add('no-hover');
      $tgBtn.addEventListener('mousemove', () => $tgBtn.classList.remove('no-hover'), { once: true });
    } else {
      $tgBtn.classList.add('hidden');
    }
  }
  // Badge : affiché seulement s'il y a des onglets groupés
  if ($tgBadge) {
    $tgBadge.textContent = count;
    $tgBadge.style.display = count > 0 ? '' : 'none';
  }

  // Refresh panel list if it's open
  if (_openPanel === 'inactive-tabs') _tgFillPanel();
}


/* Vérifie périodiquement les onglets inactifs */
function _tgCheck() {
  if (!_tgEnabled) return;
  const threshold = _tgDelayMin * 60 * 1000;
  const now = Date.now();
  let changed = false;
  for (const tab of _tabs) {
    if (tab.id === _activeId) continue;                          // onglet actif = jamais groupé
    if (_groupedTabs.some(g => g.id === tab.id)) continue;      // déjà groupé
    const last = _tabLastSeen[tab.id];
    if (last === undefined) continue;                            // pas encore suivi
    if (now - last >= threshold) {
      _groupedTabs.push({
        id:        tab.id,
        url:       tab.url   || '',
        title:     tab.title || tab.url || 'Onglet',
        favicon:   tab.favicon || null,
        groupedAt: now,
      });
      changed = true;
    }
  }
  if (changed) {
    _tgSave();
    renderTabs(_tabs);          // retirer les onglets groupés de la barre
    renderTabGroupList();       // les ajouter dans la liste
  }
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

// ── Bookmarks bar toggle ───────────────────────────────────────────
const $bmBarToggle = document.getElementById('setting-bookmarks-bar');
if ($bmBarToggle) {
  $bmBarToggle.addEventListener('change', () => {
    window.electronAPI.setBookmarksBarVisible($bmBarToggle.checked);
  });
}

// Lancer le check toutes les 10 secondes (assez précis pour un délai de 1 min)
setInterval(_tgCheck, 10_000);

// Restaurer l'état du bouton au démarrage
renderTabGroupList();

// Clic droit sur un onglet → "Mettre en onglet inactif"
// Note: manual grouping always works regardless of _tgEnabled (which only controls auto-grouping timer)
window.electronAPI.onGroupTab(({ id: rawId, url, title, favicon }) => {
  const id = Number(rawId);  // always numeric
  if (_groupedTabs.some(g => g.id === id)) return;   // already grouped
  // Use cached favicon from _tabs if main process sent null
  const cachedTab = _tabs.find(t => t.id === id);
  _groupedTabs.push({
    id,
    url:       url   || cachedTab?.url   || '',
    title:     title || cachedTab?.title || url || 'Onglet',
    favicon:   favicon ?? cachedTab?.favicon ?? null,
    groupedAt: Date.now(),
  });
  _tgSave();
  renderTabs(_tabs);        // retire l'onglet de la tab bar
  renderTabGroupList();     // l'ajoute dans la liste

  // Si c'était l'onglet actif, basculer automatiquement sur un autre
  if (id === _activeId) {
    const next = _tabs.find(t => t.id !== id && !_groupedTabs.some(g => g.id === t.id));
    if (next) {
      _activeId = next.id;
      window.electronAPI.switchTab(next.id);
    }
  }
});

/* ─── Settings panel ────────────────────────────────────────────── */
async function loadSettingsPanel() {
  const s = await window.electronAPI.getSettings();
  const $sel  = document.getElementById('setting-newtab');
  const $row  = document.getElementById('setting-newtab-custom-row');
  const $inp  = document.getElementById('setting-newtab-custom-url');
  if (!$sel) return;
  $sel.value = s.newtab || 'duckduckgo';
  // Si la valeur sauvegardée n'existe plus dans le select (ex: 'blank', 'home', 'tor'),
  // l'option ne sera pas trouvée → selectedIndex=-1 → on remet duckduckgo par défaut.
  if ($sel.selectedIndex === -1) {
    $sel.value = 'duckduckgo';
    s.newtab   = 'duckduckgo';
    window.electronAPI.saveSettings(s);
  }
  $row.style.display = ($sel.value === 'custom') ? '' : 'none';
  if (s.customUrl) $inp.value = s.customUrl;
}

async function _saveNewtabSetting() {
  const $sel = document.getElementById('setting-newtab');
  const $inp = document.getElementById('setting-newtab-custom-url');
  const s    = await window.electronAPI.getSettings();
  s.newtab    = $sel.value;
  s.customUrl = $inp?.value.trim() || '';
  await window.electronAPI.saveSettings(s);
}

document.getElementById('setting-newtab')?.addEventListener('change', () => {
  const val  = document.getElementById('setting-newtab').value;
  const $row = document.getElementById('setting-newtab-custom-row');
  $row.style.display = (val === 'custom') ? '' : 'none';
  if (val !== 'custom') _saveNewtabSetting();
});
document.getElementById('setting-newtab-save')?.addEventListener('click', _saveNewtabSetting);
document.getElementById('setting-newtab-custom-url')?.addEventListener('keydown', e => {
  if (e.key === 'Enter') _saveNewtabSetting();
});

document.getElementById('setting-open-hub')      ?.addEventListener('click', () => { window.electronAPI.goHome(); closeAllPanels(); });
document.getElementById('setting-open-addons')   ?.addEventListener('click', () => { window.electronAPI.newTab('http://127.0.0.1:8080/ui/addons.html'); closeAllPanels(); });
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
/* Chrome-style tab groups: panel + chip ctx menu + IPC */

// Panel (side-panel) for group create/rename/recolor
const $chgColorsEl   = document.getElementById('chg-colors');
const $chgColorLabel = document.getElementById('chg-color-label');
const $chgNameInput  = document.getElementById('chg-name-input');
const $chgPanelTitle = document.getElementById('chg-panel-title');
const $chgSaveBtn    = document.getElementById('chg-save-btn');
const $chgCancelBtn  = document.getElementById('chg-cancel-btn');

function _cgBuildSwatches(selHex) {
  if (!$chgColorsEl) return;
  $chgColorsEl.innerHTML = '';
  for (const c of CG_COLORS) {
    const sw = document.createElement('div');
    sw.className = 'chg-color-swatch' + (c.hex === selHex ? ' selected' : '');
    sw.style.background = c.hex;
    sw.title = c.id;
    sw.addEventListener('click', () => {
      $chgColorsEl.querySelectorAll('.chg-color-swatch').forEach(s => s.classList.remove('selected'));
      sw.classList.add('selected');
      _chgSelColor = c.hex;
    });
    $chgColorsEl.appendChild(sw);
  }
}

function _cgOpenGroupPanel(action, ctx) {
  _cgPanelCtx = { action, ...ctx };
  const showColor = action !== 'bm-rename' || (ctx.bm && ctx.bm.type === 'group');
  $chgColorLabel?.classList.toggle('hidden', !showColor);
  $chgColorsEl?.classList.toggle('hidden', !showColor);
  if (action === 'new') {
    if ($chgPanelTitle) $chgPanelTitle.textContent = 'Nouveau groupe';
    if ($chgNameInput)  $chgNameInput.value = '';
    _chgSelColor = CG_COLORS[0].hex;
  } else if (action === 'bm-rename') {
    const bm = ctx.bm;
    if ($chgPanelTitle) $chgPanelTitle.textContent = bm.type === 'group' ? 'Renommer le groupe' : 'Renommer le favori';
    if ($chgNameInput)  $chgNameInput.value = bm.type === 'group' ? (bm.name || '') : (bm.title || bm.url || '');
    _chgSelColor = bm.color || CG_COLORS[0].hex;
  } else {
    const g = _chromGroups.find(x => x.id === ctx.groupId);
    if (!g) return;
    if ($chgPanelTitle) $chgPanelTitle.textContent = 'Modifier le groupe';
    if ($chgNameInput)  $chgNameInput.value = g.name || '';
    _chgSelColor = g.color;
  }
  if (showColor) _cgBuildSwatches(_chgSelColor);
  openPanel('tab-group');
  requestAnimationFrame(() => $chgNameInput?.focus());
}

function _cgSaveGroupPanel() {
  if (!_cgPanelCtx) return;
  const name  = ($chgNameInput?.value || '').trim();
  const color = _chgSelColor;
  if (_cgPanelCtx.action === 'bm-rename') {
    // Rename a bookmark (regular or group) from the bookmarks bar
    const bm = _cgPanelCtx.bm;
    closeAllPanels();
    (async () => {
      await window.electronAPI.bookmarksRemove(bm.url);
      if (bm.type === 'group') {
        await window.electronAPI.bookmarkAddGroup({ name: name || bm.name || '', color, tabs: bm.tabs });
      } else {
        await window.electronAPI.bookmarksAdd(bm.url, name || bm.title || bm.url, bm.favicon);
      }
      refreshBookmarkStar();
      await loadBookmarksPanel();
    })();
    return;
  }
  if (_cgPanelCtx.action === 'new') {
    const tabId = Number(_cgPanelCtx.tabId);
    _chromGroups = _chromGroups.map(g => ({ ...g, tabIds: g.tabIds.filter(id => id !== tabId) }));
    _chromGroups.push({ id: _cgNextId++, name, color, collapsed: false, tabIds: [tabId] });
  } else {
    const g = _chromGroups.find(x => x.id === _cgPanelCtx.groupId);
    if (g) { g.name = name; g.color = color; }
  }
  _cgSave();
  closeAllPanels();
  renderTabs(_tabs);
}

$chgSaveBtn?.addEventListener('click',   _cgSaveGroupPanel);
$chgCancelBtn?.addEventListener('click', closeAllPanels);
document.addEventListener('keydown', e => {
  const _panel = document.getElementById('panel-tab-group');
  if (!_panel || _panel.classList.contains('hidden')) return;
  if (e.key === 'Enter') { e.preventDefault(); _cgSaveGroupPanel(); }
});

// Chip context menu
const $chgCtx        = document.getElementById('chg-ctx');
const $chgCtxOverlay = document.getElementById('chg-ctx-overlay');

function _cgShowCtxMenu(groupId, x, y) {
  _cgCtxId = groupId;
  if ($chgCtx) {
    $chgCtx.style.left = Math.min(x, window.innerWidth  - 210) + 'px';
    $chgCtx.style.top  = Math.min(y, window.innerHeight - 210) + 'px';
    $chgCtx.classList.remove('hidden');
  }
  $chgCtxOverlay?.classList.remove('hidden');
}

function _cgHideCtxMenu() {
  $chgCtx?.classList.add('hidden');
  $chgCtxOverlay?.classList.add('hidden');
  _cgCtxId = null;
}

// Overlay click = dismiss menu (catches clicks anywhere, including BrowserView area)
$chgCtxOverlay?.addEventListener('mousedown', () => _cgHideCtxMenu());
// Also dismiss on Escape
document.addEventListener('keydown', e => { if (e.key === 'Escape') _cgHideCtxMenu(); }, true);
// Also dismiss on window blur (e.g. clicking another app)
window.addEventListener('blur', _cgHideCtxMenu);

document.getElementById('chgctx-newtab')?.addEventListener('click', () => {
  _cgHideCtxMenu();
  window.electronAPI.newTab();
});
document.getElementById('chgctx-rename')?.addEventListener('click', () => {
  const gid = _cgCtxId; _cgHideCtxMenu();
  _cgOpenGroupPanel('rename', { groupId: gid });
});
document.getElementById('chgctx-color')?.addEventListener('click', () => {
  const gid = _cgCtxId; _cgHideCtxMenu();
  _cgOpenGroupPanel('editColor', { groupId: gid });
});
document.getElementById('chgctx-ungroup')?.addEventListener('click', () => {
  const gid = _cgCtxId; _cgHideCtxMenu();
  _chromGroups = _chromGroups.filter(g => g.id !== gid);
  _cgSave();
  renderTabs(_tabs);
});
document.getElementById('chgctx-close-group')?.addEventListener('click', () => {
  const gid = _cgCtxId; _cgHideCtxMenu();
  const g = _chromGroups.find(x => x.id === gid);
  if (!g) return;
  const ids = [...g.tabIds];
  _chromGroups = _chromGroups.filter(x => x.id !== gid);
  _cgSave();
  ids.forEach(id => window.electronAPI.closeTab(id));
});

document.getElementById('chgctx-close-save-bm')?.addEventListener('click', async () => {
  const gid = _cgCtxId; _cgHideCtxMenu();
  await _cgSaveGroupToBookmarks(gid);
  const g = _chromGroups.find(x => x.id === gid);
  if (!g) return;
  const ids = [...g.tabIds];
  _chromGroups = _chromGroups.filter(x => x.id !== gid);
  _cgSave();
  ids.forEach(id => window.electronAPI.closeTab(id));
});

// ── Native group ctx menu action handler (Menu.popup replaces the HTML menu) ──
window.electronAPI.onGroupCtxAction(async ({ action, groupId }) => {
  if (action === 'rename') {
    _cgOpenGroupPanel('rename', { groupId });
  } else if (action === 'color') {
    _cgOpenGroupPanel('editColor', { groupId });
  } else if (action === 'ungroup') {
    _chromGroups = _chromGroups.filter(g => g.id !== groupId);
    _cgSave();
    renderTabs(_tabs);
  } else if (action === 'close-save-bm') {
    await _cgSaveGroupToBookmarks(groupId);
    const g = _chromGroups.find(x => x.id === groupId);
    if (!g) return;
    const ids = [...g.tabIds];
    _chromGroups = _chromGroups.filter(x => x.id !== groupId);
    _cgSave();
    ids.forEach(id => window.electronAPI.closeTab(id));
  } else if (action === 'close-group') {
    const g = _chromGroups.find(x => x.id === groupId);
    if (!g) return;
    const ids = [...g.tabIds];
    _chromGroups = _chromGroups.filter(x => x.id !== groupId);
    _cgSave();
    ids.forEach(id => window.electronAPI.closeTab(id));
  }
});

// onTabGroupAction: from native context menu (main.js sends this)
window.electronAPI.onTabGroupAction(({ action, tabId, groupId }) => {
  if (action === 'new') {
    _cgOpenGroupPanel('new', { tabId });
  } else if (action === 'add') {
    const numId = Number(tabId);
    _chromGroups = _chromGroups.map(g => ({ ...g, tabIds: g.tabIds.filter(id => id !== numId) }));
    const target = _chromGroups.find(g => g.id === groupId);
    if (target) { target.tabIds.push(numId); _cgSave(); renderTabs(_tabs); }
  } else if (action === 'remove') {
    const numId = Number(tabId);
    _chromGroups = _chromGroups.map(g => ({ ...g, tabIds: g.tabIds.filter(id => id !== numId) }));
    _cgSave();
    renderTabs(_tabs);
  }
});

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

// Initialize bookmarks bar from settings (runs once on page load)
window.electronAPI.getSettings().then(s => {
  _bmBarSetVisible(s.bookmarksBar !== false);
  const $t = document.getElementById('setting-bookmarks-bar');
  if ($t) $t.checked = _bmBarVisible;
  if (_bmBarVisible) renderBookmarksBar();
});

init();

/* ── F5 to reload active tab ────────────────────────────────────── */
document.addEventListener('keydown', e => {
  if (e.key === 'F5') {
    e.preventDefault();
    window.electronAPI.reload();
  }
});
