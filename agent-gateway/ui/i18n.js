/**
 * i18n.js — Internationalisation + Accessibility bootstrap (Item 14)
 *
 * Provides:
 *   t(key, fallback?)  — look up a translated string
 *   setLang(code)      — switch language at runtime (future use)
 *
 * Also auto-enhances ARIA semantics on DOMContentLoaded for every admin page:
 *   - aria-live="polite" on #toast / .toast notification regions
 *   - aria-live="status" on status indicators (#status-dot, .status-dot)
 *   - aria-label on icon-only buttons (✕ delete, ⟳ refresh, ▶ run …)
 *   - role="region" + aria-label on <section> elements without a label
 *   - Marks the primary <main> with role="main" (explicit, belt-and-braces)
 *   - Adds skip-to-content link as first child of <body>
 */

/* ── String table ──────────────────────────────────────────────────────── */

const LANG = {};

LANG['en'] = {
  /* Generic */
  'btn.refresh'        : 'Refresh',
  'btn.delete'         : 'Delete',
  'btn.close'          : 'Close',
  'btn.run'            : 'Run now',
  'btn.approve'        : 'Approve',
  'btn.reject'         : 'Reject',
  'btn.send'           : 'Send',
  'btn.save'           : 'Save',
  'btn.cancel'         : 'Cancel',
  'btn.connect'        : 'Connect',
  'btn.add'            : 'Add',
  'btn.remove'         : 'Remove',
  'btn.enable_all'     : 'Enable all tasks',
  'btn.disable_all'    : 'Disable all tasks',

  /* Status */
  'status.connecting'  : 'Connecting…',
  'status.connected'   : 'Connected',
  'status.error'       : 'Connection error',
  'status.loading'     : 'Loading…',
  'status.ok'          : 'OK',

  /* Pages */
  'page.hub'           : 'Admin Hub',
  'page.approvals'     : 'Approvals',
  'page.audit'         : 'Audit Log',
  'page.capabilities'  : 'Capabilities',
  'page.consent'       : 'Consent Log',
  'page.content_filter': 'Content Filter',
  'page.memory'        : 'Agent Memory',
  'page.metrics'       : 'Metrics',
  'page.providers'     : 'Providers',
  'page.rate_limits'   : 'Rate Limits',
  'page.schedule'      : 'Scheduled Tasks',
  'page.status'        : 'Status',
  'page.tab_permission': 'Tab Permissions',
  'page.users'         : 'Users & API Keys',
  'page.webhooks'      : 'Webhooks',

  /* Notifications */
  'toast.saved'        : 'Saved',
  'toast.deleted'      : 'Deleted',
  'toast.error'        : 'An error occurred',
  'toast.no_items'     : 'No items found',
};

let _currentLang = 'en';

/**
 * Translate a key.  Falls back to the key itself if not found.
 * @param {string} key
 * @param {string} [fallback]
 * @returns {string}
 */
export function t(key, fallback) {
  return (LANG[_currentLang] ?? {})[key] ?? fallback ?? key;
}

/**
 * Switch active language and re-apply translations.
 * @param {string} code  BCP-47 language code (e.g. 'en', 'fr')
 */
export function setLang(code) {
  _currentLang = code;
  _applyTranslations();
}

/* ── Apply [data-i18n] translations ─────────────────────────────────────── */

function _applyTranslations() {
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    const val = t(key);
    if (val && val !== key) {
      if (el.tagName === 'INPUT' && (el.type === 'placeholder' || el.hasAttribute('placeholder'))) {
        el.placeholder = val;
      } else {
        el.textContent = val;
      }
    }
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    const key = el.getAttribute('data-i18n-placeholder');
    const val = t(key);
    if (val && val !== key) el.placeholder = val;
  });
  document.querySelectorAll('[data-i18n-aria]').forEach(el => {
    const key = el.getAttribute('data-i18n-aria');
    const val = t(key);
    if (val && val !== key) el.setAttribute('aria-label', val);
  });
}

/* ── Icon-to-label mapping for icon-only buttons ────────────────────────── */

const ICON_LABELS = {
  '✕': t('btn.delete', 'Delete'),
  '×': t('btn.delete', 'Delete'),
  '✗': t('btn.reject', 'Reject'),
  '✓': t('btn.approve', 'Approve'),
  '⟳': t('btn.refresh', 'Refresh'),
  '↺': t('btn.refresh', 'Refresh'),
  '▶': t('btn.run', 'Run now'),
  '⏸': 'Pause',
  '🗑': t('btn.delete', 'Delete'),
};

/* ── ARIA auto-enhancement ───────────────────────────────────────────────── */

function _enhanceAria() {
  /* 1. Skip-to-content link */
  const main = document.querySelector('main');
  if (main && !document.getElementById('skip-nav')) {
    if (!main.id) main.id = 'main-content';
    const skip = document.createElement('a');
    skip.id = 'skip-nav';
    skip.href = '#' + main.id;
    skip.textContent = 'Skip to main content';
    skip.setAttribute('class', 'sr-only-focusable');
    // Inject skip-link CSS if not already present
    if (!document.getElementById('_i18n_sr_style')) {
      const style = document.createElement('style');
      style.id = '_i18n_sr_style';
      style.textContent = [
        '.sr-only-focusable{position:absolute;top:-999px;left:-999px;width:1px;height:1px;overflow:hidden;}',
        '.sr-only-focusable:focus{top:8px;left:8px;width:auto;height:auto;padding:8px 16px;',
        'background:#6c63ff;color:#fff;border-radius:4px;z-index:9999;font-size:14px;}',
        '[aria-hidden="true"]{speak:none;}',
      ].join('');
      document.head.appendChild(style);
    }
    document.body.insertBefore(skip, document.body.firstChild);
  }

  /* 2. <main> landmark */
  document.querySelectorAll('main').forEach(el => {
    if (!el.getAttribute('role')) el.setAttribute('role', 'main');
  });

  /* 3. <header> landmark */
  document.querySelectorAll('body > header').forEach(el => {
    if (!el.getAttribute('role')) el.setAttribute('role', 'banner');
  });

  /* 4. Live regions for toast / status notifications */
  const toastEl = document.getElementById('toast');
  if (toastEl && !toastEl.getAttribute('aria-live')) {
    toastEl.setAttribute('aria-live', 'polite');
    toastEl.setAttribute('aria-atomic', 'true');
  }
  document.querySelectorAll('.toast,[id*="toast"]').forEach(el => {
    if (!el.getAttribute('aria-live')) {
      el.setAttribute('aria-live', 'polite');
      el.setAttribute('aria-atomic', 'true');
    }
  });

  /* 5. Status dots — polite live + descriptive role */
  document.querySelectorAll('#status-dot,.status-dot').forEach(el => {
    if (!el.getAttribute('role')) el.setAttribute('role', 'status');
    if (!el.getAttribute('aria-live')) el.setAttribute('aria-live', 'polite');
    if (!el.getAttribute('aria-label')) el.setAttribute('aria-label', t('status.connecting'));
  });

  /* 6. Icon-only buttons: add aria-label when button has no accessible name */
  document.querySelectorAll('button').forEach(btn => {
    /* Skip if already labelled */
    if (btn.getAttribute('aria-label') || btn.getAttribute('aria-labelledby')) return;
    const text = (btn.textContent ?? '').trim();
    /* Only patch buttons whose visible content is a single icon character */
    if ([...text].length <= 2 && ICON_LABELS[text]) {
      btn.setAttribute('aria-label', ICON_LABELS[text]);
      /* Mark decorative icon as aria-hidden to avoid double-reading */
      [...btn.childNodes].forEach(n => {
        if (n.nodeType === Node.TEXT_NODE) n.replaceWith(
          Object.assign(document.createElement('span'), {
            textContent: n.textContent,
            ariaHidden: 'true',
          })
        );
      });
    } else if (btn.title) {
      /* Promote title to aria-label for screen readers */
      btn.setAttribute('aria-label', btn.title);
    }
  });

  /* 7. Form inputs — ensure each has an accessible name */
  document.querySelectorAll('input,select,textarea').forEach(input => {
    if (input.getAttribute('aria-label') || input.getAttribute('aria-labelledby')) return;
    /* Check for associated <label> via for= */
    const id = input.id;
    if (id && document.querySelector(`label[for="${id}"]`)) return;
    /* Fall back to placeholder */
    if (input.placeholder) {
      input.setAttribute('aria-label', input.placeholder);
    } else if (input.title) {
      input.setAttribute('aria-label', input.title);
    }
  });

  /* 8. Tables — add accessible description from the nearest heading */
  document.querySelectorAll('table:not([aria-label]):not([aria-labelledby])').forEach(tbl => {
    /* Walk back to find the nearest preceding heading */
    let el = tbl.previousElementSibling;
    while (el && !['H1','H2','H3','H4','H5','H6'].includes(el.tagName)) {
      el = el.previousElementSibling;
    }
    if (el) {
      const caption = el.textContent.trim();
      if (caption) tbl.setAttribute('aria-label', caption);
    }
  });

  /* 9. Decorative icons (SVG/emoji in spans with no text role) — aria-hidden */
  document.querySelectorAll('.icon,[aria-hidden]').forEach(el => {
    if (!el.getAttribute('aria-hidden')) el.setAttribute('aria-hidden', 'true');
  });

  /* 10. External links — visually hidden "(opens in new tab)" notification */
  document.querySelectorAll('a[target="_blank"]:not([aria-label])').forEach(a => {
    const existing = a.getAttribute('aria-label') || a.textContent.trim();
    if (existing) a.setAttribute('aria-label', existing + ' (opens in new tab)');
  });
}

/* ── Bootstrap on DOMContentLoaded ─────────────────────────────────────── */

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    _applyTranslations();
    _enhanceAria();
  });
} else {
  _applyTranslations();
  _enhanceAria();
}
