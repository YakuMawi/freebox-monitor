// common.js — Freebox Monitor
// Helpers partagés entre index.html et settings.html (CSRF + thème clair/sombre),
// auparavant dupliqués dans les deux templates.
//
// Chargé en script externe : autorisé par la CSP via 'self' dans script-src
// sans avoir besoin du nonce par-requête (réservé aux scripts inline).

let _csrfToken = '';

async function initCsrf() {
  try {
    const r = await fetch('/api/csrf-token');
    const d = await r.json();
    _csrfToken = d.token || '';
  } catch (e) {
    console.error('initCsrf — les requêtes PATCH/POST seront rejetées', e);
  }
}

function _ch(extra) {
  return Object.assign({}, extra, { 'X-CSRF-Token': _csrfToken });
}

// Thème clair/sombre. L'icône est appliquée à '#theme-btn' et, si présent,
// '#theme-icon-mobile' (seul index.html a une barre de navigation mobile).
function _applyThemeIcon() {
  const t = document.documentElement.getAttribute('data-theme') || 'dark';
  const icon = t === 'dark' ? '☀️' : '🌙';
  const btn = document.getElementById('theme-btn');
  if (btn) btn.textContent = icon;
  const mobileIcon = document.getElementById('theme-icon-mobile');
  if (mobileIcon) mobileIcon.textContent = icon;
}

// Synchronise l'icône avec le thème courant au chargement de la page.
function initThemeIcon() {
  _applyThemeIcon();
}

// Bascule le thème. onToggle (optionnel) est appelé avec le nouveau thème —
// utilisé par index.html pour redessiner le calendrier avec les bonnes couleurs.
function toggleTheme(onToggle) {
  const cur = document.documentElement.getAttribute('data-theme') || 'dark';
  const next = cur === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('fbx-theme', next);
  _applyThemeIcon();
  if (typeof onToggle === 'function') onToggle(next);
}
