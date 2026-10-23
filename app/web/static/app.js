'use strict';
/* Pi Sentinel — consola de administración local.
   Sin dependencias externas (funciona sin Internet). Todo texto dinámico se inserta con
   textContent / nodos de texto; nunca innerHTML. */

const S = { csrf: null, session: null, status: null, settings: null, route: 'panel', sub: null, cleanup: [], view: null };

// ======================================================================= utilidades DOM

const $ = (sel, root = document) => root.querySelector(sel);
const nn = (...a) => a.flat(Infinity).filter((x) => x !== null && x !== undefined && x !== false && x !== '');

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'value') el.value = v;
    else if (k === 'checked' || k === 'disabled' || k === 'selected' || k === 'open') el[k] = !!v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of nn(kids)) el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return el;
}

const NS = 'http://www.w3.org/2000/svg';
const ICONS = {
  dashboard: 'M3 3h7v9H3z|M14 3h7v5h-7z|M14 12h7v9h-7z|M3 16h7v5H3z',
  camera: 'M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z|M12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  history: 'M12 8v4l3 3|M3.05 11a9 9 0 1 1 .5 4|M3 4v5h5',
  users: 'M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2|M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z|M23 21v-2a4 4 0 0 0-3-3.87|M16 3.13a4 4 0 0 1 0 7.75',
  volume: 'M11 5 6 9H2v6h4l5 4V5z|M19.07 4.93a10 10 0 0 1 0 14.14|M15.54 8.46a5 5 0 0 1 0 7.07',
  mute: 'M11 5 6 9H2v6h4l5 4V5z|M23 9l-6 6|M17 9l6 6',
  bell: 'M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9|M13.73 21a2 2 0 0 1-3.46 0',
  server: 'M2 4h20v6H2z|M2 14h20v6H2z|M6 7h.01|M6 17h.01',
  sliders: 'M4 21v-7|M4 10V3|M12 21v-9|M12 8V3|M20 21v-5|M20 12V3|M1 14h6|M9 8h6|M17 16h6',
  shield: 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z',
  play: 'M6 4l14 8-14 8V4z',
  stop: 'M6 6h12v12H6z',
  check: 'M20 6 9 17l-5-5',
  x: 'M18 6 6 18|M6 6l12 12',
  alert: 'M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z|M12 9v4|M12 17h.01',
  info: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z|M12 16v-4|M12 8h.01',
  refresh: 'M23 4v6h-6|M1 20v-6h6|M3.51 9a9 9 0 0 1 14.85-3.36L23 10|M1 14l4.64 4.36A9 9 0 0 0 20.49 15',
  download: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4|M7 10l5 5 5-5|M12 15V3',
  upload: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4|M17 8l-5-5-5 5|M12 3v12',
  trash: 'M3 6h18|M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6|M10 11v6|M14 11v6|M9 6V4h6v2',
  menu: 'M3 12h18|M3 6h18|M3 18h18',
  moon: 'M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z',
  sun: 'M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10z|M12 1v2|M12 21v2|M4.22 4.22l1.42 1.42|M18.36 18.36l1.42 1.42|M1 12h2|M21 12h2|M4.22 19.78l1.42-1.42|M18.36 5.64l1.42-1.42',
  logout: 'M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4|M16 17l5-5-5-5|M21 12H9',
  eye: 'M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z|M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
  bluetooth: 'M6.5 6.5l11 11L12 23V1l5.5 5.5-11 11',
  activity: 'M22 12h-4l-3 9L9 3l-3 9H2',
  user: 'M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2|M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  plus: 'M12 5v14|M5 12h14',
  edit: 'M12 20h9|M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z',
  thermo: 'M14 14.76V3.5a2.5 2.5 0 0 0-5 0v11.26a4.5 4.5 0 1 0 5 0z',
  cpu: 'M4 4h16v16H4z|M9 9h6v6H9z|M9 1v3|M15 1v3|M9 20v3|M15 20v3|M20 9h3|M20 14h3|M1 9h3|M1 14h3',
  disk: 'M22 12H2|M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z|M6 16h.01|M10 16h.01',
  clock: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z|M12 6v6l4 2',
  lock: 'M5 11h14v10H5z|M8 11V7a4 4 0 0 1 8 0v4',
  key: 'M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.78 7.78 5.5 5.5 0 0 1 7.78-7.78zm0 0L15.5 7.5m0 0 3 3L22 7l-3-3m-3.5 3.5L19 4',
  file: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z|M14 2v6h6',
  music: 'M9 18V5l12-2v13|M6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6z|M18 19a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
  wifi: 'M5 12.55a11 11 0 0 1 14.08 0|M1.42 9a16 16 0 0 1 21.16 0|M8.53 16.11a6 6 0 0 1 6.95 0|M12 20h.01',
};
function icon(name, cls = 'icon') {
  const s = document.createElementNS(NS, 'svg');
  s.setAttribute('viewBox', '0 0 24 24');
  s.setAttribute('class', cls);
  s.setAttribute('aria-hidden', 'true');
  for (const d of (ICONS[name] || ICONS.info).split('|')) {
    const p = document.createElementNS(NS, 'path');
    p.setAttribute('d', d);
    s.append(p);
  }
  return s;
}

// ======================================================================= formato

const fmtTime = (ts) => (ts ? new Date(ts * 1000).toLocaleString('es-ES', { dateStyle: 'short', timeStyle: 'medium' }) : '—');
function fmtAgo(ts) {
  if (!ts) return '—';
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return 'hace unos segundos';
  if (s < 3600) return `hace ${Math.round(s / 60)} min`;
  if (s < 86400) return `hace ${Math.round(s / 3600)} h`;
  return `hace ${Math.round(s / 86400)} d`;
}
function fmtDur(sec) {
  if (sec == null) return '—';
  const d = Math.floor(sec / 86400), hh = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  return d ? `${d} d ${hh} h` : hh ? `${hh} h ${m} min` : `${m} min`;
}
function fmtBytes(b) {
  if (b == null) return '—';
  const u = ['B', 'KB', 'MB', 'GB', 'TB']; let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(i ? 1 : 0)} ${u[i]}`;
}

const STATUS = { AUTHORIZED: ['Autorizada', 'ok'], UNKNOWN: ['Desconocida', 'err'], UNVERIFIED: ['No verificable', 'warn'] };
const MODE = { stopped: ['Detenida', 'off', 'stop'], test: ['Modo prueba', 'warn', 'eye'], active: ['Vigilando', 'ok', 'shield'] };
const STATE = { ok: ['Operativo', 'ok'], warn: ['Atención', 'warn'], error: ['Error', 'err'], disabled: ['Inactivo', 'off'] };
const ALARM = { NONE: ['—', 'off'], TRIGGERED: ['Sonó', 'ok'], DISABLED: ['Desactivada', 'off'], MUTED: ['Silenciada', 'warn'], RATE_LIMITED: ['Limitada', 'warn'], BUSY: ['Ya sonando', 'info'], ERROR: ['Error de audio', 'err'], SUPPRESSED: ['Suprimida', 'warn'] };
const NOTIF = { NONE: ['—', 'off'], QUEUED: ['En cola', 'info'], PENDING: ['Pendiente', 'warn'], SENT: ['Enviada', 'ok'], FAILED: ['Fallida', 'err'], DISABLED: ['ntfy desactivado', 'off'], NOT_CONFIGURED: ['Sin configurar', 'warn'], DUPLICATE: ['Duplicada', 'off'], ERROR: ['Error', 'err'], SUPPRESSED: ['Suprimida', 'warn'] };

const badge = (text, tone = 'off', plain = false) => h('span', { class: `badge tone-${tone}${plain ? ' plain' : ''}` }, text);
const fromMap = (map, key) => badge(...(map[key] || [key || '—', 'off']));
const getPath = (o, p) => p.split('.').reduce((a, k) => (a == null ? a : a[k]), o);
function setPath(o, p, v) { const ks = p.split('.'); let c = o; ks.slice(0, -1).forEach((k) => (c = c[k] = c[k] || {})); c[ks[ks.length - 1]] = v; }

// ======================================================================= API

function errMessage(data, status) {
  const d = data && data.detail;
  if (Array.isArray(d)) return d.map((e) => (e.loc ? e.loc.filter((x) => x !== 'body').join('.') + ': ' : '') + e.msg).join('; ');
  return d || `Error ${status}`;
}

async function api(method, url, body, opts = {}) {
  const headers = {};
  if (S.csrf && method !== 'GET') headers['X-CSRF-Token'] = S.csrf;
  let payload;
  if (opts.raw) { payload = body; headers['Content-Type'] = opts.contentType || 'application/octet-stream'; }
  else if (body !== undefined) { payload = JSON.stringify(body); headers['Content-Type'] = 'application/json'; }
  let res;
  try { res = await fetch(url, { method, headers, body: payload, credentials: 'same-origin' }); }
  catch (e) { throw new Error('No se pudo contactar con Pi Sentinel. ¿El servicio está en marcha?'); }
  if (opts.blob && res.ok) return res.blob();
  let data = null;
  try { data = await res.json(); } catch (e) { /* sin cuerpo JSON */ }
  if (res.status === 401 && !opts.noLogin) { showLogin(); throw new Error('La sesión ha caducado'); }
  if (!res.ok) throw new Error(errMessage(data, res.status));
  return data;
}

async function act(fn, okMsg) {
  try { const r = await fn(); if (okMsg) toast(okMsg, 'ok'); return r; }
  catch (e) { toast(e.message, 'err'); return null; }
}

// ======================================================================= componentes

function toast(msg, tone = 'info') {
  const ic = { ok: 'check', err: 'alert', warn: 'alert', info: 'info' }[tone];
  const el = h('div', { class: `toast ${tone}`, role: tone === 'err' ? 'alert' : 'status' }, icon(ic),
    h('div', { class: 'toast-msg' }, msg), h('button', { 'aria-label': 'Cerrar', onclick: () => el.remove() }, icon('x', 'icon icon-sm')));
  const box = $('#toasts');
  box.append(el);
  while (box.children.length > 4) box.firstChild.remove(); // como máximo 4 avisos a la vez
  setTimeout(() => el.remove(), tone === 'err' ? 9000 : 4000);
}

function btn(label, opts = {}) {
  const cls = ['btn'];
  if (opts.variant) cls.push(`btn-${opts.variant}`);
  if (opts.size === 'sm') cls.push('btn-sm');
  if (!label) cls.push('btn-icon');
  const b = h('button', { type: opts.type || 'button', class: cls.join(' '), title: opts.title || (label ? null : opts.label), 'aria-label': label ? null : opts.label, disabled: opts.disabled },
    opts.icon ? icon(opts.icon, 'icon icon-sm') : null, label ? h('span', {}, label) : null);
  if (opts.onClick) {
    b.addEventListener('click', async (e) => {
      if (b.dataset.busy) return;
      b.dataset.busy = '1'; b.classList.add('is-loading'); b.disabled = true;
      try { await opts.onClick(e); } finally { delete b.dataset.busy; b.classList.remove('is-loading'); b.disabled = !!opts.disabled; }
    });
  }
  return b;
}

function card({ title, subtitle, actions, body, foot, cls }) {
  return h('section', { class: `card ${cls || ''}` },
    title ? h('div', { class: 'card-head' }, h('div', {}, h('h2', {}, title), subtitle ? h('p', {}, subtitle) : null), actions ? h('div', { class: 'actions' }, actions) : null) : null,
    body !== undefined ? h('div', { class: 'card-body' }, body) : null,
    foot ? h('div', { class: 'card-foot' }, foot) : null);
}

function stat(label, value, { hint, tone = 'accent', ic = 'activity' } = {}) {
  const v = h('div', { class: 'stat-value' }, value);
  const hn = h('div', { class: 'stat-hint' }, hint || '');
  const ico = h('div', { class: `stat-icon ${tone === 'accent' ? '' : tone}` }, icon(ic));
  const el = h('div', { class: 'card stat' }, ico, h('div', {}, h('div', { class: 'stat-label' }, label), v, hn));
  el.set = (val, hint2, tone2) => { v.textContent = val; if (hint2 !== undefined) hn.textContent = hint2; if (tone2) ico.className = `stat-icon ${tone2 === 'accent' ? '' : tone2}`; };
  return el;
}

function alertBox(tone, text, title) {
  return h('div', { class: `alert ${tone}` }, icon({ ok: 'check', err: 'alert', warn: 'alert', info: 'info' }[tone]),
    h('div', {}, title ? h('strong', {}, title, ' ') : null, text));
}

function empty(text, ic = 'info', action) {
  return h('div', { class: 'empty' }, icon(ic), h('div', {}, text), action || null);
}

function table(headers, rows, emptyText = 'Sin datos') {
  if (!rows.length) return empty(emptyText);
  return h('div', { class: 'table-wrap' }, h('table', {},
    h('thead', {}, h('tr', {}, headers.map((t) => h('th', {}, t)))),
    h('tbody', {}, rows)));
}

function pageHead(title, desc, actions) {
  return h('div', { class: 'page-head' }, h('div', {}, h('h1', {}, title), desc ? h('p', {}, desc) : null), actions ? h('div', { class: 'actions' }, actions) : null);
}

function switchInput(checked, onChange) {
  const inp = h('input', { type: 'checkbox', checked, role: 'switch' });
  if (onChange) inp.addEventListener('change', () => onChange(inp.checked));
  return { el: h('label', { class: 'switch' }, inp, h('span', {})), input: inp };
}

function bar(pct, tone) {
  const t = tone || (pct >= 90 ? 'err' : pct >= 75 ? 'warn' : '');
  const i = h('i', {}); i.style.width = `${Math.max(0, Math.min(100, pct || 0))}%`;
  return h('div', { class: `bar ${t}` }, i);
}

function tabs(items, current, onSelect) {
  return h('div', { class: 'tabs', role: 'tablist' }, items.map(([k, label, ic]) =>
    h('button', { class: 'tab', role: 'tab', 'aria-selected': String(k === current), onclick: () => onSelect(k) }, ic ? icon(ic, 'icon icon-sm') : null, label)));
}

// ----------------------------------------------------------------------- diálogos

function dialog({ title, message, tone = 'accent', ic = 'info', body, confirmText = 'Aceptar', cancelText = 'Cancelar', danger = false, validate }) {
  return new Promise((resolve) => {
    const prev = document.activeElement;
    const ok = h('button', { class: `btn ${danger ? 'btn-danger' : 'btn-primary'}`, type: 'submit' }, confirmText);
    const form = h('form', { class: 'dialog', role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
      h('div', { class: 'dialog-head' }, h('div', { class: `stat-icon ${tone === 'accent' ? '' : tone}` }, icon(ic)), h('div', {}, h('h2', {}, title))),
      h('div', { class: 'dialog-body' }, message ? h('p', {}, message) : null, body || null),
      h('div', { class: 'dialog-foot' }, cancelText ? h('button', { class: 'btn', type: 'button', onclick: () => close(null) }, cancelText) : null, ok));
    const ov = h('div', { class: 'overlay' }, form);
    function close(v) { ov.remove(); document.removeEventListener('keydown', onKey); if (prev && prev.focus) prev.focus(); resolve(v); }
    function onKey(e) { if (e.key === 'Escape') close(null); }
    form.addEventListener('submit', (e) => { e.preventDefault(); if (validate && !validate()) return; close(true); });
    ov.addEventListener('mousedown', (e) => { if (e.target === ov) close(null); });
    document.addEventListener('keydown', onKey);
    document.body.append(ov);
    const first = form.querySelector('input, select') || ok;
    first.focus();
    if (validate) { const upd = () => (ok.disabled = !validate()); form.addEventListener('input', upd); upd(); }
  });
}

const confirmDialog = (o) => dialog({ ic: o.danger ? 'alert' : 'info', tone: o.danger ? 'err' : 'accent', ...o }).then((v) => v === true);

async function promptDialog({ title, label, value = '', maxlength = 60, confirmText = 'Guardar' }) {
  const inp = h('input', { class: 'input', type: 'text', value, maxlength, 'aria-label': label });
  const r = await dialog({ title, message: label, body: inp, confirmText, ic: 'edit', validate: () => inp.value.trim().length > 0 });
  return r ? inp.value.trim() : null;
}

async function typeToConfirm({ title, message, word, confirmText }) {
  const inp = h('input', { class: 'input', type: 'text', placeholder: word, autocomplete: 'off', 'aria-label': `Escribe ${word}` });
  const r = await dialog({ title, message, body: h('div', {}, h('p', { class: 'small' }, `Escribe ${word} para confirmar.`), inp), confirmText, danger: true, ic: 'alert', tone: 'err', validate: () => inp.value === word });
  return r ? inp.value : null;
}

// ----------------------------------------------------------------------- formularios de ajustes

/* fields: [{path,label,help,type:'switch'|'select'|'range'|'number'|'text',min,max,step,int,options,num,unit,placeholder}] */
function settingsForm(fields, { onSaved, saveText = 'Guardar cambios' } = {}) {
  const ctl = {};
  const initial = {};
  const body = h('div', {});
  for (const f of fields) {
    const cur = getPath(S.settings, f.path);
    initial[f.path] = cur;
    let control, input;
    if (f.type === 'switch') {
      const sw = switchInput(!!cur); input = sw.input; control = sw.el;
    } else if (f.type === 'select') {
      input = h('select', { class: 'input' }, f.options.map(([v, t]) => h('option', { value: v, selected: String(v) === String(cur) }, t)));
      control = input;
    } else if (f.type === 'range') {
      input = h('input', { class: 'input', type: 'range', min: f.min, max: f.max, step: f.step, value: cur });
      const out = h('span', { class: 'range-value' }, String(cur) + (f.unit || ''));
      input.addEventListener('input', () => (out.textContent = input.value + (f.unit || '')));
      control = h('div', { class: 'field-control' }, input, out);
    } else {
      input = h('input', { class: 'input', type: f.type || 'text', value: cur ?? '', min: f.min, max: f.max, step: f.step, placeholder: f.placeholder, autocomplete: 'off' });
      control = input;
    }
    ctl[f.path] = { f, input };
    body.append(h('div', { class: 'field' },
      h('div', {}, h('div', { class: 'field-label' }, f.label), f.help ? h('div', { class: 'field-help' }, f.help) : null),
      h('div', { class: 'field-control' }, control)));
  }
  const err = h('div', { class: 'form-error hidden' });
  const value = ({ f, input }) => {
    if (f.type === 'switch') return input.checked;
    if (f.type === 'number' || f.type === 'range' || f.num) { const n = f.int ? parseInt(input.value, 10) : parseFloat(input.value); return Number.isNaN(n) ? input.value : n; }
    return input.value;
  };
  const isDirty = () => Object.values(ctl).some((c) => String(value(c)) !== String(initial[c.f.path]));
  const save = btn(saveText, { variant: 'primary', icon: 'check', type: 'submit' });
  const discard = h('button', { class: 'btn btn-ghost', type: 'button', onclick: () => reset() }, 'Descartar');
  const formBar = h('div', { class: 'form-bar hidden' }, h('span', {}, 'Hay cambios sin guardar'), h('div', { class: 'actions' }, discard, save));
  const form = h('form', { class: 'form', novalidate: true }, body, err, formBar);
  function refresh() { formBar.classList.toggle('hidden', !isDirty()); }
  function reset() {
    for (const { f, input } of Object.values(ctl)) {
      const v = initial[f.path];
      if (f.type === 'switch') input.checked = !!v; else input.value = v ?? '';
      input.dispatchEvent(new Event('input'));
    }
    err.classList.add('hidden'); refresh();
  }
  form.addEventListener('input', refresh);
  form.addEventListener('change', refresh);
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const patch = {};
    for (const c of Object.values(ctl)) if (String(value(c)) !== String(initial[c.f.path])) setPath(patch, c.f.path, value(c));
    if (!Object.keys(patch).length) return;
    save.disabled = true; save.classList.add('is-loading');
    try {
      const r = await api('PUT', '/api/settings', patch);
      S.settings = r.settings;
      for (const c of Object.values(ctl)) initial[c.f.path] = getPath(S.settings, c.f.path);
      err.classList.add('hidden'); refresh();
      toast(r.restart_required.length ? 'Guardado. Reinicia el servicio para aplicar los ajustes de red.' : 'Ajustes guardados', r.restart_required.length ? 'warn' : 'ok');
      if (onSaved) onSaved(patch);
    } catch (e) { err.textContent = e.message; err.classList.remove('hidden'); }
    finally { save.disabled = false; save.classList.remove('is-loading'); }
  });
  form.isDirty = isDirty;
  return form;
}

// ----------------------------------------------------------------------- vista previa

function livePreview({ overlay = true } = {}) {
  const img = h('img', { alt: 'Vista previa de la cámara', class: 'hidden' });
  const live = h('span', { class: 'preview-live hidden' }, h('span', { class: 'dot' }), 'EN DIRECTO');
  const msgText = h('div', {}, 'Vista previa desactivada para ahorrar recursos');
  const msg = h('div', { class: 'preview-msg' }, icon('camera', 'icon icon-lg'), msgText);
  const el = h('div', { class: 'preview' }, img, live, msg);
  let running = false, url = null, timer = null, ov = overlay;
  async function tick() {
    if (!running) return;
    try {
      const blob = await api('GET', `/api/camera/snapshot.jpg?overlay=${ov ? 1 : 0}&t=${Date.now()}`, undefined, { blob: true });
      if (!running) return;
      const next = URL.createObjectURL(blob);
      img.src = next; if (url) URL.revokeObjectURL(url); url = next;
      img.classList.remove('hidden'); live.classList.remove('hidden'); msg.classList.add('hidden');
      timer = setTimeout(tick, 600);
    } catch (e) {
      img.classList.add('hidden'); live.classList.add('hidden'); msg.classList.remove('hidden'); msgText.textContent = e.message;
      timer = setTimeout(tick, 2500);
    }
  }
  const p = {
    el,
    start() { if (running) return; running = true; msgText.textContent = 'Conectando con la cámara…'; msg.classList.remove('hidden'); tick(); },
    stop() { running = false; clearTimeout(timer); if (url) { URL.revokeObjectURL(url); url = null; } img.classList.add('hidden'); live.classList.add('hidden'); img.removeAttribute('src'); msg.classList.remove('hidden'); msgText.textContent = 'Vista previa desactivada para ahorrar recursos'; },
    setOverlay(v) { ov = v; },
    get running() { return running; },
  };
  S.cleanup.push(() => p.stop());
  return p;
}

function previewSwitch(preview, label = 'Vista previa') {
  const sw = switchInput(false, (v) => (v ? preview.start() : preview.stop()));
  return h('div', { class: 'row' }, sw.el, h('span', {}, label));
}

// ======================================================================= vistas

const views = {};

// ----------------------------------------------------------------------- PANEL

views.panel = {
  title: 'Panel', crumb: 'Monitorización',
  async build(root) {
    const heroIcon = h('div', { class: 'hero-icon' }, icon('shield'));
    const heroTitle = h('h2', {});
    const heroText = h('p', { class: 'muted' });
    const note = h('div', {});
    const segBtns = {};
    const seg = h('div', { class: 'segmented', role: 'group', 'aria-label': 'Modo de vigilancia' },
      ['stopped', 'test', 'active'].map((m) => (segBtns[m] = h('button', { class: `seg-${m}`, 'aria-pressed': 'false', onclick: () => setMode(m) }, icon(MODE[m][2], 'icon icon-sm'), MODE[m][0]))));
    const hero = h('section', { class: 'card hero' }, heroIcon, h('div', { class: 'hero-text' }, heroTitle, heroText), seg);

    const sEvents = stat('Eventos (24 h)', '—', { ic: 'history' });
    const sUnknown = stat('Desconocidos (24 h)', '—', { ic: 'alert', tone: 'err' });
    const sPeople = stat('Personas autorizadas', '—', { ic: 'users', tone: 'ok' });
    const sNotif = stat('Notificaciones pendientes', '—', { ic: 'bell', tone: 'info' });

    const health = h('div', { class: 'health' });
    const live = h('div', { class: 'stack' });
    const recentBody = h('div', {});
    const testCard = h('div', { class: 'hidden' });

    root.append(
      pageHead('Panel de control', 'Estado de la vigilancia, salud de los componentes y actividad reciente.',
        [btn('Probar alarma', { icon: 'volume', onClick: () => testSound('/api/alarm/test') }),
         btn('Notificación de prueba', { icon: 'bell', onClick: async () => { const r = await act(() => api('POST', '/api/notifications/test')); if (r) toast(r.ok ? 'Notificación de prueba enviada' : r.error, r.ok ? 'ok' : 'err'); } })]),
      h('div', { class: 'stack' },
        hero, note,
        h('div', { class: 'grid grid-4' }, sEvents, sUnknown, sPeople, sNotif),
        testCard,
        h('div', { class: 'grid grid-3' },
          h('section', { class: 'card span-2' }, h('div', { class: 'card-head' }, h('div', {}, h('h2', {}, 'Salud del sistema'), h('p', {}, 'Estado en tiempo real de cada componente'))), h('div', { class: 'card-body', style: { padding: '14px 0 0' } }, health)),
          card({ title: 'En directo', subtitle: 'Último análisis de la cámara', body: live })),
        card({ title: 'Actividad reciente', subtitle: 'Últimos eventos registrados', actions: [btn('Ver historial', { size: 'sm', variant: 'ghost', onClick: () => go('historial') })], body: recentBody })));

    let lastEventId = null;
    async function loadRecent() {
      const r = await api('GET', '/api/events?limit=6').catch(() => null);
      const u = await api('GET', `/api/events?status=UNKNOWN&limit=1&since=${Date.now() / 1000 - 86400}`).catch(() => null);
      if (u) sUnknown.set(String(u.total), u.total ? 'Revisa el historial' : 'Sin incidencias', u.total ? 'err' : 'ok');
      if (!r) return;
      recentBody.replaceChildren(r.events.length
        ? table(['Fecha', 'Estado', 'Alarma', 'Notificación', 'Evento'], r.events.map((e) => h('tr', {},
          h('td', {}, fmtTime(e.started_at), h('div', { class: 'subtle small' }, fmtAgo(e.started_at))), h('td', {}, fromMap(STATUS, e.identity_status)),
          h('td', {}, fromMap(ALARM, e.alarm_state)), h('td', {}, fromMap(NOTIF, e.notification)), h('td', { class: 'mono subtle' }, e.id))))
        : empty('Todavía no hay eventos. Aparecerán aquí cuando se detecte una presencia.', 'history'));
    }
    async function setMode(m) {
      if (m === S.status?.mode) return;
      if (m === 'active' && S.status && S.status.people.active_ready < 1) {
        const go2 = await confirmDialog({ title: 'Falta tu perfil facial', message: 'Antes de vigilar debes registrar tu rostro; si no, cualquier persona —incluido tú— se consideraría desconocida.', confirmText: 'Registrar ahora' });
        if (go2) go('personas');
        return;
      }
      await act(() => api('POST', '/api/surveillance', { mode: m }), `Modo: ${MODE[m][0]}`);
      refreshStatus();
    }
    const COMP = [['camera', 'Cámara', 'camera'], ['detector', 'Detector de personas', 'user'], ['face', 'Reconocimiento facial', 'eye'], ['alarm', 'Audio', 'volume'], ['ntfy', 'Notificaciones', 'bell'], ['analysis', 'Análisis', 'activity']];
    this.onStatus = (s) => {
      const [label, tone] = MODE[s.mode];
      hero.className = `card hero ${s.mode}`;
      heroIcon.replaceChildren(icon(MODE[s.mode][2]));
      heroTitle.textContent = s.mode === 'active' ? 'Vigilancia activa' : s.mode === 'test' ? 'Modo prueba' : 'Vigilancia detenida';
      heroText.textContent = s.mode === 'active' ? 'Se analiza la cámara; las presencias desconocidas activan la alarma y la notificación.'
        : s.mode === 'test' ? 'Se analiza la imagen y se muestra qué ocurriría, sin alarma, sin notificaciones y sin guardar nada.'
          : 'La cámara está apagada. Actívala para empezar a vigilar.';
      for (const [m, b] of Object.entries(segBtns)) b.setAttribute('aria-pressed', String(m === s.mode));
      note.replaceChildren(...nn(s.mode_note ? alertBox('warn', s.mode_note) : null,
        s.people.active_ready < 1 ? alertBox('info', h('span', {}, 'Aún no hay ningún perfil autorizado listo. ', h('a', { href: '#personas' }, 'Registra tu rostro'), ' antes de activar la vigilancia.')) : null,
        s.alarm.last_error ? alertBox('err', h('span', {}, s.alarm.last_error, ' ', h('a', { href: '#sonido/salida' }, 'Abrir diagnóstico de audio'))) : null));
      sEvents.set(String(s.recent_events_24h), s.last_event ? `Último ${fmtAgo(s.last_event.started_at)}` : 'Sin eventos');
      sPeople.set(`${s.people.active_ready}/${s.people.total}`, 'activas / registradas', s.people.active_ready ? 'ok' : 'warn');
      sNotif.set(String(s.ntfy.pending), `${s.ntfy.sent} enviadas · ${s.ntfy.failed} fallidas`, s.ntfy.failed ? 'warn' : 'info');
      health.replaceChildren(...COMP.map(([k, name]) => {
        const c = s.components[k] || { state: 'disabled', message: '' };
        const st = STATE[c.state] || STATE.disabled;
        return h('div', { class: 'health-item' }, h('span', { class: `dot ${st[1]}` }), h('div', {}, h('div', { class: 'health-name' }, name, ' ', badge(st[0], st[1])), h('div', { class: 'health-msg' }, c.message + (k === 'ntfy' && c.pending ? ` · ${c.pending} pendientes` : ''))));
      }));
      live.replaceChildren(s.mode === 'stopped'
        ? empty('Sin análisis: la vigilancia está detenida.', 'camera')
        : h('dl', { class: 'kv' },
          h('dt', {}, 'Personas'), h('dd', {}, String(s.live.persons)),
          h('dt', {}, 'Rostros'), h('dd', {}, String(s.live.faces)),
          h('dt', {}, 'Presencias'), h('dd', {}, String((s.live.tracks || []).length)),
          h('dt', {}, 'Detector'), h('dd', {}, s.live.person_ms != null ? `${s.live.person_ms} ms` : '—'),
          h('dt', {}, 'Caras'), h('dd', {}, s.live.face_ms != null ? `${s.live.face_ms} ms` : '—'),
          h('dt', {}, 'Actualizado'), h('dd', {}, s.live.age != null ? `hace ${s.live.age} s` : '—')));
      if (s.mode === 'test') {
        testCard.className = '';
        const ev = s.test_events.slice().reverse();
        const ACT = { would_alert: ['Habría sonado la alarma y enviado notificación', 'err'], would_log: ['Se habría registrado', 'info'], ignored: ['Ignorado por la política', 'off'], reclassified: ['Reclasificado como autorizado', 'ok'] };
        testCard.replaceChildren(card({ title: 'Modo prueba: lo que habría ocurrido', subtitle: 'Nada de esto suena, se notifica ni se guarda',
          body: ev.length ? table(['Clasificación', 'Acción'], ev.map((e) => h('tr', {}, h('td', {}, fromMap(STATUS, e.status)), h('td', {}, badge(...(ACT[e.action] || [e.action, 'off'])))))) : empty('Colócate delante de la cámara para ver cómo se clasifica la presencia.', 'eye') }));
      } else testCard.className = 'hidden';
      const leId = s.last_event && s.last_event.id;
      if (leId !== lastEventId) { lastEventId = leId; loadRecent(); }
    };
    loadRecent();
  },
};

async function testSound(url) {
  const r = await act(() => api('POST', url));
  if (!r) return;
  if (r.state === 'TRIGGERED') toast('Reproduciendo… Si no oyes nada, abre Sonido → Salida de audio.', 'ok');
  else toast(r.error || `No se reprodujo: ${(ALARM[r.state] || [r.state])[0]}`, 'err');
}

// ----------------------------------------------------------------------- CÁMARA

views.camara = {
  title: 'Cámara', crumb: 'Monitorización',
  async build(root) {
    const pv = livePreview();
    const ov = switchInput(true, (v) => pv.setOverlay(v));
    const sState = stat('Estado', '—', { ic: 'camera' });
    const sFps = stat('Fotogramas/s', '—', { ic: 'activity' });
    const sRes = stat('Resolución', '—', { ic: 'eye' });
    const sRec = stat('Reconexiones', '—', { ic: 'refresh' });
    const checkOut = h('div', { class: 'stack' });
    const devs = h('div', { class: 'small muted' }, 'Buscando dispositivos…');
    root.append(
      pageHead('Cámara', 'Vista previa en directo, estado de la captura y parámetros de análisis. Las imágenes no se guardan.'),
      h('div', { class: 'stack' },
        h('div', { class: 'grid grid-4' }, sState, sFps, sRes, sRec),
        h('div', { class: 'grid grid-2' },
          card({ title: 'Vista previa', subtitle: 'Con la vigilancia activa se dibuja lo detectado', actions: [previewSwitch(pv), h('div', { class: 'row small' }, ov.el, 'Anotaciones')], body: pv.el }),
          card({ title: 'Comprobación', subtitle: 'Analiza una imagen: luz, nitidez, personas y rostros',
            actions: [btn('Comprobar', { icon: 'check', variant: 'primary', onClick: runCheck })], body: h('div', { class: 'stack' }, checkOut, devs) })),
        card({ title: 'Captura y análisis', subtitle: 'Cambiar dispositivo o resolución reinicia la captura automáticamente', body: settingsForm([
          { path: 'camera.backend', label: 'Controlador', help: 'Automático elige Picamera2 si hay cámara oficial', type: 'select', options: [['auto', 'Automático'], ['opencv', 'USB / V4L2 (OpenCV)'], ['picamera2', 'Cámara Raspberry Pi (Picamera2)']] },
          { path: 'camera.device', label: 'Dispositivo', help: 'Índice (0, 1…) o ruta /dev/videoN', type: 'text' },
          { path: 'camera.width', label: 'Ancho', type: 'number', min: 160, max: 1920, int: true },
          { path: 'camera.height', label: 'Alto', type: 'number', min: 120, max: 1080, int: true },
          { path: 'camera.capture_fps', label: 'Captura (fps)', help: 'Menos fps = menos CPU', type: 'number', min: 1, max: 30, step: 1 },
          { path: 'detection.analysis_fps', label: 'Análisis por segundo', help: 'En Raspberry Pi 4/5 empieza con 1-2', type: 'number', min: 0.1, max: 10, step: 0.1 },
          { path: 'detection.input_size', label: 'Resolución del detector', type: 'select', num: true, int: true, options: [[320, '320 px · rápido'], [416, '416 px · equilibrado'], [512, '512 px'], [640, '640 px · preciso']] },
          { path: 'detection.person_confidence', label: 'Confianza mínima de persona', type: 'range', min: 0.2, max: 0.9, step: 0.05 },
          { path: 'detection.motion_gate', label: 'Analizar solo con movimiento', help: 'Con la escena quieta se analiza cada pocos segundos', type: 'switch' },
        ]) })));
    async function runCheck() {
      checkOut.replaceChildren(h('div', { class: 'skeleton', style: { minHeight: '90px' } }));
      const r = await act(() => api('POST', '/api/camera/check'));
      if (!r) { checkOut.replaceChildren(); return; }
      checkOut.replaceChildren(h('dl', { class: 'kv' },
        h('dt', {}, 'Resolución recibida'), h('dd', {}, r.resolution),
        h('dt', {}, 'Brillo / nitidez'), h('dd', {}, `${r.brightness} / ${r.sharpness}`),
        h('dt', {}, 'Detector de personas'), h('dd', {}, r.person_detector.ok ? `${r.person_detector.persons} persona(s) · ${r.person_detector.ms} ms` : badge(`Error: ${r.person_detector.error}`, 'err')),
        h('dt', {}, 'Rostros'), h('dd', {}, r.faces.ok ? `${r.faces.count} · ${r.faces.ms} ms` : badge(`Error: ${r.faces.error}`, 'err'))),
      ...r.hints.map((t) => alertBox('warn', t)), r.hints.length ? null : alertBox('ok', 'La imagen tiene luz y nitidez suficientes.'));
    }
    api('GET', '/api/camera/devices').then((d) => {
      devs.textContent = d.devices.length ? `Dispositivos detectados: ${d.devices.map((x) => x.label).join(', ')}` : 'No se detecta ninguna cámara (/dev/video*). Conecta una cámara USB o activa la cámara de la Raspberry Pi.';
    }).catch(() => {});
    this.onStatus = (s) => {
      const c = s.components.camera;
      const st = STATE[c.state] || STATE.disabled;
      sState.set(st[0], c.message, st[1] === 'ok' ? 'ok' : st[1] === 'err' ? 'err' : 'off');
      sFps.set(String(c.fps), `${c.frames_total} fotogramas`);
      sRes.set(c.resolution, `${c.invalid_frames} inválidos`);
      sRec.set(String(c.reconnects), 'desde el arranque', c.reconnects ? 'warn' : 'accent');
    };
  },
};

// ----------------------------------------------------------------------- HISTORIAL

views.historial = {
  title: 'Historial', crumb: 'Monitorización',
  async build(root) {
    const PAGE = 25;
    let offset = 0;
    const fStatus = h('select', { class: 'input', 'aria-label': 'Estado' }, [['', 'Todos los estados'], ['UNKNOWN', 'Desconocidas'], ['UNVERIFIED', 'No verificables'], ['AUTHORIZED', 'Autorizadas']].map(([v, t]) => h('option', { value: v }, t)));
    const fRange = h('select', { class: 'input', 'aria-label': 'Periodo' }, [['1', 'Últimas 24 h'], ['7', 'Últimos 7 días'], ['30', 'Últimos 30 días'], ['', 'Todo']].map(([v, t]) => h('option', { value: v, selected: v === '7' }, t)));
    const exportLink = h('a', { class: 'btn', download: 'pi-sentinel-eventos.csv' }, icon('download', 'icon icon-sm'), 'Exportar CSV');
    const body = h('div', {});
    const pager = h('div', { class: 'pager' });
    const query = () => {
      const p = new URLSearchParams();
      if (fStatus.value) p.set('status', fStatus.value);
      if (fRange.value) p.set('since', String(Date.now() / 1000 - Number(fRange.value) * 86400));
      return p;
    };
    async function load() {
      const q = query();
      exportLink.href = `/api/events.csv?${q}`;
      q.set('limit', PAGE); q.set('offset', offset);
      const r = await act(() => api('GET', `/api/events?${q}`));
      if (!r) return;
      body.replaceChildren(table(['Fecha y hora', 'Clasificación', 'Alarma', 'Notificación', 'Duración', 'Evento'], r.events.map((e) => h('tr', {},
        h('td', {}, fmtTime(e.started_at)), h('td', {}, fromMap(STATUS, e.identity_status)), h('td', {}, fromMap(ALARM, e.alarm_state)),
        h('td', {}, fromMap(NOTIF, e.notification)),
        h('td', { class: 'small' }, e.detail || (e.ended_at ? `${Math.max(1, Math.round(e.ended_at - e.started_at))} s` : badge('En curso', 'info'))),
        h('td', { class: 'mono subtle' }, e.id))), 'No hay eventos con estos filtros.'));
      const from = r.total ? offset + 1 : 0, to = Math.min(offset + PAGE, r.total);
      pager.replaceChildren(h('span', {}, `${from}–${to} de ${r.total}`), h('div', { class: 'actions' },
        btn('Anterior', { size: 'sm', disabled: offset === 0, onClick: () => { offset = Math.max(0, offset - PAGE); return load(); } }),
        btn('Siguiente', { size: 'sm', disabled: to >= r.total, onClick: () => { offset += PAGE; return load(); } })));
    }
    fStatus.addEventListener('change', () => { offset = 0; load(); });
    fRange.addEventListener('change', () => { offset = 0; load(); });
    root.append(
      pageHead('Historial de eventos', 'Solo se guardan metadatos: nunca fotografías ni vídeo de las personas detectadas.',
        [btn('Borrar historial', { icon: 'trash', variant: 'danger', onClick: async () => {
          if (await confirmDialog({ title: 'Borrar todo el historial', message: 'Se eliminarán todos los eventos registrados. Esta acción no se puede deshacer.', confirmText: 'Borrar', danger: true })) {
            const r = await act(() => api('DELETE', '/api/events')); if (r) { toast(`${r.deleted} eventos eliminados`, 'ok'); offset = 0; load(); }
          }
        } })]),
      h('div', { class: 'stack' },
        h('section', { class: 'card' }, h('div', { class: 'toolbar' }, fStatus, fRange, h('div', { style: { marginLeft: 'auto' } }, exportLink), btn('', { icon: 'refresh', label: 'Actualizar', onClick: load })), body, pager),
        card({ title: 'Retención', subtitle: 'Los eventos más antiguos se eliminan automáticamente', body: settingsForm([{ path: 'storage.history_retention_days', label: 'Conservar el historial', help: 'Días', type: 'number', min: 1, max: 3650, int: true }]) })));
    load();
  },
};

// ----------------------------------------------------------------------- PERSONAS

const POSE_STEPS = [
  { pose: 'frontal', text: 'Mira de frente a la cámara' },
  { pose: 'frontal', text: 'De frente, con expresión neutra' },
  { pose: 'lateral', text: 'Gira ligeramente la cabeza a un lado' },
  { pose: 'lateral', text: 'Gira ligeramente la cabeza al otro lado' },
  { pose: 'any', text: 'Cambia la luz (otra lámpara o ventana) y mira de frente' },
  { pose: 'any', text: 'Sonríe o cambia la expresión' },
];

views.personas = {
  title: 'Personas autorizadas', crumb: 'Configuración',
  async build(root) {
    const data = await act(() => api('GET', '/api/people'));
    if (!data) return;
    const people = data.people;
    const reload = () => go('personas');

    if (!people.length) {
      const nameIn = h('input', { class: 'input', type: 'text', value: 'Propietario', maxlength: 40, 'aria-label': 'Nombre del perfil' });
      root.append(pageHead('Personas autorizadas', 'Perfiles faciales de las personas que no deben activar la alarma.'),
        card({ title: 'Bienvenido: registra tu rostro', subtitle: 'Primer paso para distinguir a las personas desconocidas', body: h('div', { class: 'stack' },
          h('p', {}, 'Se guardan solo representaciones numéricas (embeddings) en esta Raspberry Pi. No se guardan fotos ni se envía nada fuera.'),
          alertBox('info', 'Registrar más muestras mejora el reconocimiento, pero no garantiza una identificación perfecta: puede haber errores en ambos sentidos.'),
          h('div', { class: 'inline-form' }, nameIn, btn('Crear perfil y empezar', { variant: 'primary', icon: 'plus', onClick: async () => { const r = await act(() => api('POST', '/api/people', { name: nameIn.value })); if (r) reload(); } }))) }));
      return;
    }

    let selected = people[0].id;
    let session = 0;
    let testTimer = null;
    S.cleanup.push(() => clearInterval(testTimer));
    const pv = livePreview();

    const listHost = h('div', {});
    function renderList() {
      listHost.replaceChildren(table(['Nombre', 'Estado', 'Muestras', 'Actualizado', ''], people.map((p) => {
        const pct = Math.min(100, (p.sample_count / data.min_samples) * 100);
        return h('tr', {},
          h('td', {}, h('strong', {}, p.name)),
          h('td', {}, !p.enabled ? badge('Desactivado', 'off') : p.ready ? badge('Activo', 'ok') : badge('Incompleto', 'warn')),
          h('td', {}, h('div', { class: 'progress-cell' }, h('span', { class: 'small' }, `${p.sample_count}/${data.min_samples}`), bar(pct, p.ready ? 'ok' : 'warn'))),
          h('td', { class: 'small muted' }, fmtAgo(p.updated_at)),
          h('td', {}, h('div', { class: 'table-actions' },
            btn('Muestras', { size: 'sm', icon: 'camera', onClick: () => { selected = p.id; sel.value = String(p.id); session = 0; renderSteps(); renderSamples(); capCard.scrollIntoView({ behavior: 'smooth' }); } }),
            btn(p.enabled ? 'Desactivar' : 'Activar', { size: 'sm', onClick: async () => { await act(() => api('PATCH', `/api/people/${p.id}`, { enabled: !p.enabled }), p.enabled ? 'Perfil desactivado' : 'Perfil activado'); reload(); } }),
            btn('', { size: 'sm', icon: 'edit', label: 'Renombrar', onClick: async () => { const n = await promptDialog({ title: 'Renombrar perfil', label: 'Nuevo nombre', value: p.name, maxlength: 40 }); if (n) { await act(() => api('PATCH', `/api/people/${p.id}`, { name: n }), 'Perfil renombrado'); reload(); } } }),
            btn('', { size: 'sm', icon: 'trash', label: 'Eliminar', variant: 'ghost', onClick: async () => {
              if (await confirmDialog({ title: `Eliminar a ${p.name}`, message: 'Se borrará el perfil y todas sus muestras faciales. No se puede deshacer.', confirmText: 'Eliminar', danger: true })) { await act(() => api('DELETE', `/api/people/${p.id}`), 'Perfil eliminado'); reload(); }
            } }))));
      })));
    }
    async function refreshList() { const d = await api('GET', '/api/people').catch(() => null); if (d) { people.splice(0, people.length, ...d.people); renderList(); } }

    // registro de muestras
    const sel = h('select', { class: 'input', 'aria-label': 'Perfil' }, people.map((p) => h('option', { value: p.id }, p.name)));
    sel.addEventListener('change', () => { selected = parseInt(sel.value, 10); session = 0; renderSteps(); renderSamples(); });
    const stepsEl = h('div', { class: 'stepper' });
    const capMsg = h('div', {});
    const samplesEl = h('div', { class: 'samples' });
    const countEl = h('p', { class: 'small muted' });
    function renderSteps() {
      stepsEl.replaceChildren(...POSE_STEPS.map((st, i) => h('div', { class: `step ${i < session ? 'done' : i === session ? 'current' : ''}` },
        h('span', { class: 'step-n' }, i < session ? icon('check', 'icon icon-sm') : String(i + 1)), st.text)));
    }
    async function renderSamples() {
      const r = await api('GET', `/api/people/${selected}/samples`).catch(() => null);
      if (!r) return;
      countEl.textContent = `${r.samples.length} muestra(s) · recomendado 8-10 con distinta luz y ángulo (mínimo ${data.min_samples}).`;
      samplesEl.replaceChildren(...(r.samples.length ? r.samples.map((s, i) => h('span', { class: 'sample', title: fmtTime(s.created_at) }, `#${i + 1} · ${s.pose} · ${Math.round(s.quality * 100)}%`,
        btn('', { size: 'sm', icon: 'x', label: 'Eliminar muestra', variant: 'ghost', onClick: async () => { await act(() => api('DELETE', `/api/people/${selected}/samples/${s.id}`)); renderSamples(); refreshList(); } }))) : [h('span', { class: 'small muted' }, 'Sin muestras todavía.')]));
    }
    const capBtn = btn('Capturar muestra', { variant: 'primary', icon: 'camera', onClick: async () => {
      if (!pv.running) pv.start();
      const pose = session < POSE_STEPS.length ? POSE_STEPS[session].pose : 'any';
      const r = await act(() => api('POST', `/api/people/${selected}/samples/capture`, { pose }));
      if (r && r.ok) { session++; capMsg.replaceChildren(alertBox('ok', `Muestra guardada (${r.samples}).${r.ready ? ' El perfil ya es utilizable.' : ''}`)); renderSteps(); renderSamples(); refreshList(); }
      else if (r) capMsg.replaceChildren(alertBox('warn', r.reason));
    } });
    const capCard = card({ title: 'Registro de muestras faciales', subtitle: 'Sigue los pasos; solo se guarda el embedding, nunca la foto', actions: [previewSwitch(pv)],
      body: h('div', { class: 'grid grid-2' }, pv.el, h('div', { class: 'stack' }, sel, stepsEl, h('div', { class: 'row' }, capBtn), capMsg, h('h3', {}, 'Muestras guardadas'), samplesEl, countEl)) });

    // prueba en directo
    const testOut = h('div', { class: 'stack' });
    const cont = switchInput(false, (v) => { clearInterval(testTimer); if (v) { pv.start(); runTest(); testTimer = setInterval(runTest, 1800); } });
    async function runTest() {
      const r = await api('POST', '/api/recognition/test').catch((e) => { toast(e.message, 'err'); return null; });
      if (!r) { clearInterval(testTimer); cont.input.checked = false; return; }
      if (!r.profiles_active) { testOut.replaceChildren(alertBox('warn', 'Ningún perfil activo tiene muestras suficientes: no se puede comparar.')); return; }
      if (!r.faces.length) { testOut.replaceChildren(empty('No se detecta ninguna cara en la imagen.', 'eye')); return; }
      testOut.replaceChildren(...r.faces.map((f) => h('div', { class: 'card', style: { padding: '12px 14px' } },
        h('div', { class: 'row' }, fromMap(STATUS, f.status), f.name ? h('strong', {}, f.name) : null),
        f.scores ? h('div', { class: 'mono subtle', style: { marginTop: '6px' } }, Object.entries(f.scores).map(([n, v]) => `${n}: ${v.toFixed(3)}`).join(' · '), ` (umbral ${r.threshold})`) : null,
        !f.quality_ok ? h('div', { class: 'small', style: { marginTop: '6px' } }, 'No verificable: ' + f.reasons.join('; ')) : null)));
    }

    // calibración
    const calOut = h('div', { class: 'stack' });
    function showCal(c) {
      const fmt = (x) => (x ? `n=${x.n} · mín ${x.min} · p10 ${x.p10} · media ${x.mean} · máx ${x.max}` : 'sin medir');
      calOut.replaceChildren(...nn(
        h('dl', { class: 'kv' }, h('dt', {}, 'Tu rostro'), h('dd', { class: 'mono' }, fmt(c.genuine)), h('dt', {}, 'Persona no registrada'), h('dd', { class: 'mono' }, fmt(c.impostor))),
        c.advice ? alertBox(c.suggested_threshold ? 'info' : 'warn', c.advice) : null,
        c.suggested_threshold ? h('div', { class: 'row' }, h('span', {}, `Umbral sugerido: `, h('strong', {}, String(c.suggested_threshold)), ` (actual ${c.current_threshold})`),
          btn('Aplicar', { size: 'sm', variant: 'primary', onClick: async () => { await act(() => api('PUT', '/api/settings', { face: { similarity_threshold: c.suggested_threshold } }), 'Umbral aplicado'); reload(); } })) : null));
    }
    showCal(data.calibration);
    async function measure(kind) {
      toast('Midiendo durante unos segundos: colócate frente a la cámara…', 'info');
      const r = await act(() => api('POST', '/api/recognition/calibrate', { kind, frames: 12 }));
      if (r) showCal(r);
    }

    root.append(
      pageHead('Personas autorizadas', 'Perfiles faciales de las personas que no deben activar la alarma.',
        [btn('Añadir persona', { variant: 'primary', icon: 'plus', onClick: async () => { const n = await promptDialog({ title: 'Nueva persona autorizada', label: 'Nombre', maxlength: 40, confirmText: 'Crear' }); if (n) { const r = await act(() => api('POST', '/api/people', { name: n }), 'Perfil creado'); if (r) reload(); } } })]),
      h('div', { class: 'stack' },
        h('section', { class: 'card' }, listHost),
        capCard,
        h('div', { class: 'grid grid-2' },
          card({ title: 'Probar el reconocimiento', subtitle: 'Compruébalo antes de activar la vigilancia', actions: [btn('Probar ahora', { icon: 'play', onClick: runTest }), h('div', { class: 'row small' }, cont.el, 'Continua')],
            body: h('div', { class: 'stack' }, h('p', { class: 'small muted' }, 'La «similitud» es una puntuación de parecido entre representaciones faciales, no una probabilidad de identidad.'), testOut) }),
          card({ title: 'Cómo se interpretan los resultados', body: h('div', { class: 'stack small' },
            h('div', {}, badge('Autorizada', 'ok'), ' El rostro supera el umbral con un perfil activo. No suena la alarma.'),
            h('div', {}, badge('Desconocida', 'err'), ' Rostro válido que no alcanza el umbral con ningún perfil. Se confirma tras varias observaciones.'),
            h('div', {}, badge('No verificable', 'warn'), ' Hay una persona pero no se ve un rostro apto (lejos, de espaldas, borroso…). Su política es configurable.'),
            alertBox('warn', 'Puede equivocarse con poca luz, gafas, mascarilla o ángulos extremos. Una foto o pantalla podría confundirlo (no hay detección de vida).')) })),
        card({ title: 'Umbral y calibración', subtitle: 'Ajusta el equilibrio entre falsas alarmas y desconocidos aceptados', body: h('div', { class: 'grid grid-2' },
          settingsForm([
            { path: 'face.similarity_threshold', label: 'Umbral de similitud', help: 'Más alto = más estricto. Referencia SFace: 0.363', type: 'range', min: 0.2, max: 0.8, step: 0.01 },
            { path: 'face.min_face_px', label: 'Tamaño mínimo del rostro', help: 'Píxeles; menores → «no verificable»', type: 'number', min: 24, max: 400, int: true },
            { path: 'face.min_sharpness', label: 'Nitidez mínima', type: 'number', min: 0, max: 500, step: 1 },
            { path: 'face.max_yaw', label: 'Giro máximo del rostro', type: 'number', min: 0.1, max: 1.5, step: 0.05 },
            { path: 'face.min_samples', label: 'Muestras mínimas por perfil', type: 'number', min: 1, max: 50, int: true },
          ], { onSaved: reload }),
          h('div', { class: 'stack' }, h('h3', {}, 'Calibración asistida'),
            h('p', { class: 'small muted' }, '1) Mide tu rostro. 2) Si puedes, mide a alguien NO registrado. Si las puntuaciones se separan se sugiere un umbral intermedio.'),
            h('div', { class: 'row' }, btn('Medir mi rostro', { icon: 'user', onClick: () => measure('genuine') }), btn('Medir persona no registrada', { icon: 'users', onClick: () => measure('impostor') })), calOut)) }),
        card({ title: 'Datos biométricos', subtitle: 'Elimina todos los perfiles y muestras faciales y detiene la vigilancia', cls: 'danger',
          body: btn('Eliminar todos los datos biométricos', { variant: 'danger', icon: 'trash', onClick: async () => {
            const w = await typeToConfirm({ title: 'Eliminar todos los datos biométricos', message: 'Se borrarán todos los perfiles y muestras faciales de forma permanente.', word: 'ELIMINAR', confirmText: 'Eliminar todo' });
            if (w) { const r = await act(() => api('POST', '/api/biometrics/delete-all', { confirm: w })); if (r) { toast(`Eliminados ${r.deleted_profiles} perfil(es)`, 'ok'); reload(); } }
          } }) })));
    renderList(); renderSteps(); renderSamples();
  },
};

// ----------------------------------------------------------------------- SONIDO

const SOUND_TABS = [['salida', 'Salida de audio', 'bluetooth'], ['alarma', 'Alarma', 'alert'], ['entrada', 'Sonido de entrada', 'bell'], ['biblioteca', 'Biblioteca', 'music'], ['politica', 'Política de alertas', 'sliders']];

views.sonido = {
  title: 'Sonido y alarmas', crumb: 'Configuración',
  async build(root) {
    const sub = SOUND_TABS.some(([k]) => k === S.sub) ? S.sub : 'salida';
    const host = h('div', {});
    root.append(pageHead('Sonido y alarmas', 'Salida de audio, alarma de desconocidos, sonido de entrada y biblioteca de sonidos.'),
      tabs(SOUND_TABS, sub, (k) => go(`sonido/${k}`)), host);
    await soundTabs[sub](host);
  },
};

const soundTabs = {
  async salida(root) {
    const out = h('div', { class: 'stack' }, h('div', { class: 'skeleton' }));
    root.append(out);
    async function load() {
      const d = await act(() => api('GET', '/api/audio/diagnostics'));
      if (!d) { out.replaceChildren(alertBox('err', 'No se pudo obtener el diagnóstico de audio.')); return; }
      render(d);
    }
    function render(d) {
      const ck = (state, name, val) => h('div', { class: 'check' }, h('span', { class: `check-icon ${state}` }, icon(state === 'ok' ? 'check' : state === 'info' ? 'info' : state === 'warn' ? 'alert' : 'x')), h('div', { class: 'check-name' }, name), h('div', { class: 'check-val' }, val));
      const btSinks = d.sinks.filter((s) => s.bluetooth);
      const defSink = d.sinks.find((s) => s.name === d.default_sink);
      const checks = h('div', { class: 'checklist' },
        ck(d.players.length ? 'ok' : 'err', 'Reproductores', d.players.length ? d.players.join(', ') : 'Ninguno instalado'),
        ck(d.as_root ? 'err' : 'ok', 'Usuario', `${d.user} (uid ${d.uid})${d.as_root ? ' — no ejecutes Pi Sentinel con sudo' : ''}`),
        ck(d.runtime_dir ? 'ok' : 'err', 'Sesión de audio', d.runtime_dir ? `${d.runtime_dir} · ${[d.pipewire_socket ? 'PipeWire' : null, d.pulse_socket ? 'PulseAudio' : null].filter(Boolean).join(' + ')}` : 'No encontrada (PipeWire/PulseAudio no accesibles)'),
        ck('info', 'Ejecución', d.systemd ? 'Servicio systemd' : 'Manual (terminal)'),
        ck(d.bluetooth_connected.length ? 'ok' : 'warn', 'Bluetooth conectado', d.bluetooth_connected.length ? d.bluetooth_connected.join(', ') : 'Ningún dispositivo conectado (o bluetoothctl no disponible)'),
        ck(defSink ? (btSinks.length && !defSink.bluetooth && !d.configured_device ? 'warn' : 'ok') : (d.sinks.length ? 'warn' : 'info'), 'Salida predeterminada', defSink ? `${defSink.description || defSink.name}${defSink.bluetooth ? ' (Bluetooth)' : ''}` : d.default_sink || 'Desconocida (pactl no disponible)'),
        ck(d.ffmpeg ? 'ok' : 'info', 'Formatos de audio', d.ffmpeg ? 'WAV, MP3, OGG, FLAC, M4A (ffmpeg disponible)' : 'WAV (instala ffmpeg para MP3/OGG/FLAC/M4A)'));

      // selección de salida
      const devSel = h('select', { class: 'input', 'aria-label': 'Salida de audio' },
        h('option', { value: '' }, `Predeterminada del sistema${defSink ? ` (${defSink.description || defSink.name})` : ''}`),
        d.sinks.map((s) => h('option', { value: s.name, selected: s.name === d.configured_device }, `${s.bluetooth ? '🔵 ' : ''}${s.description || s.name}${s.state ? ` · ${s.state.toLowerCase()}` : ''}`)),
        d.configured_device && !d.sinks.some((s) => s.name === d.configured_device) ? h('option', { value: d.configured_device, selected: true }, `${d.configured_device} (no disponible ahora)`) : null);
      const playerSel = h('select', { class: 'input', 'aria-label': 'Reproductor' }, [['auto', 'Automático (recomendado)'], ['pw-play', 'pw-play · PipeWire'], ['paplay', 'paplay · PulseAudio'], ['aplay', 'aplay · ALSA directo']].map(([v, t]) => h('option', { value: v, selected: v === d.configured_player }, t)));
      const manual = h('input', { class: 'input', type: 'text', placeholder: 'Nombre manual (avanzado), p. ej. plughw:1,0', value: d.sinks.some((s) => s.name === d.configured_device) ? '' : (d.configured_device || '') });
      const target = () => manual.value.trim() || devSel.value;
      const save = btn('Guardar salida', { variant: 'primary', icon: 'check', onClick: async () => {
        const r = await act(() => api('PUT', '/api/settings', { alarm: { player: playerSel.value, audio_device: target() } }), 'Salida de audio guardada');
        if (r) { S.settings = r.settings; load(); }
      } });

      // pruebas
      const results = {};
      const rows = ['auto', ...d.players].map((pl) => {
        const res = h('td', { class: 'small' }, h('span', { class: 'subtle' }, 'Sin probar'));
        results[pl] = res;
        return h('tr', {}, h('td', {}, h('strong', {}, pl === 'auto' ? 'Automático' : pl)),
          h('td', {}, res),
          h('td', {}, h('div', { class: 'table-actions' }, btn('Probar', { size: 'sm', icon: 'play', onClick: async () => {
            res.replaceChildren(h('span', { class: 'subtle' }, 'Reproduciendo…'));
            const r = await act(() => api('POST', '/api/audio/test', { player: pl, device: target() }));
            if (!r) { res.replaceChildren(badge('Error', 'err')); return; }
            if (r.ok) {
              res.replaceChildren(badge('Sin errores', 'ok'), ` ${r.player_used || pl} · ${r.ms} ms · ¿lo has oído? `,
                btn('Sí, usar este', { size: 'sm', variant: 'primary', onClick: async () => {
                  const rr = await act(() => api('PUT', '/api/settings', { alarm: { player: pl, audio_device: target() } }), 'Configuración de audio guardada');
                  if (rr) S.settings = rr.settings;
                } }));
            } else res.replaceChildren(badge('Falló', 'err'), ' ', h('span', { class: 'mono' }, r.error));
          } }))));
      });

      out.replaceChildren(
        ...d.hints.map((t, i) => alertBox(i === 0 && d.hints.length === 1 && t.startsWith('No se detectan') ? 'ok' : 'warn', t)),
        h('div', { class: 'grid grid-2' },
          card({ title: 'Diagnóstico', subtitle: 'Por qué suena (o no) la Raspberry Pi', actions: [btn('Volver a analizar', { size: 'sm', icon: 'refresh', onClick: load })], body: checks }),
          card({ title: 'Salida y reproductor', subtitle: 'Elige el altavoz donde sonarán la alarma y el sonido de entrada', body: h('div', { class: 'stack' },
            h('label', {}, h('div', { class: 'field-label' }, 'Salida de audio'), devSel),
            h('label', {}, h('div', { class: 'field-label' }, 'Reproductor'), playerSel),
            h('details', { class: 'raw' }, h('summary', {}, 'Avanzado: nombre de salida manual'), h('div', { style: { padding: '12px 14px' } }, manual)),
            h('div', { class: 'row' }, save),
            h('p', { class: 'small muted' }, 'Consejo Bluetooth: empareja y conecta el altavoz en el sistema, pulsa «Volver a analizar» y elígelo aquí. Ajusta el silencio previo si se corta el principio del sonido.')) })),
        card({ title: 'Probar reproducción', subtitle: 'Reproduce un tono corto con cada reproductor y la salida elegida. El resultado es el real del reproductor.', body: table(['Reproductor', 'Resultado', ''], rows, 'No hay reproductores instalados.') }),
        card({ title: 'Ajustes de reproducción', body: settingsForm([
          { path: 'alarm.lead_in_seconds', label: 'Silencio previo', help: 'Los altavoces Bluetooth tardan en «despertar» y se comen el inicio del sonido', type: 'number', min: 0, max: 3, step: 0.1 },
        ]) }),
        h('details', { class: 'raw' }, h('summary', {}, 'Detalles técnicos (wpctl status · aplay -l · entorno)'),
          h('pre', {}, [`Usuario: ${d.user} (uid ${d.uid})`, `XDG_RUNTIME_DIR: ${d.env_xdg || '—'}`, `PULSE_SERVER: ${d.env_pulse || '—'}`, '', d.wpctl_status || '(wpctl no disponible)', '', d.alsa_cards || '(aplay no disponible)'].join('\n'))));
    }
    load();
  },

  async alarma(root) {
    const al = await act(() => api('GET', '/api/alarm'));
    if (!al) return;
    const st = al.status;
    root.append(h('div', { class: 'grid grid-3' },
      h('div', { class: 'stack' },
        card({ title: 'Estado', body: h('div', { class: 'stack' },
          h('div', { class: 'row' }, st.audio_available ? badge('Audio disponible', 'ok') : badge('Sin audio', 'err'), st.enabled ? badge('Activada', 'ok') : badge('Desactivada', 'off'), st.muted ? badge('Silenciada', 'warn') : null),
          h('dl', { class: 'kv' }, h('dt', {}, 'Reproductor'), h('dd', {}, st.player || '—'), h('dt', {}, 'Última vez'), h('dd', {}, fmtTime(st.last_played_at))),
          st.last_error ? alertBox('err', h('span', {}, st.last_error, ' ', h('a', { href: '#sonido/salida' }, 'Diagnosticar'))) : null) }),
        card({ title: 'Acciones', body: h('div', { class: 'stack' },
          btn('Probar alarma', { variant: 'primary', icon: 'play', onClick: () => testSound('/api/alarm/test') }),
          btn('Detener sonido', { icon: 'stop', onClick: () => act(() => api('POST', '/api/alarm/stop'), 'Sonido detenido') }),
          btn(st.muted ? 'Reactivar sonido' : 'Silenciar 30 min', { icon: st.muted ? 'volume' : 'mute', onClick: async () => { await act(() => api('POST', '/api/alarm/mute', { minutes: st.muted ? null : 30 }), st.muted ? 'Sonido reactivado' : 'Silenciado 30 min (alarma y entrada)'); go('sonido/alarma'); } })) })),
      h('div', { class: 'span-2' }, card({ title: 'Alarma de presencia desconocida', subtitle: 'Suena en la Raspberry Pi cuando se confirma una presencia desconocida', body: settingsForm([
        { path: 'alarm.enabled', label: 'Alarma sonora', help: 'Desactívala para vigilar en silencio; las notificaciones siguen', type: 'switch' },
        { path: 'alarm.sound', label: 'Sonido', type: 'select', options: soundOptions(al.sounds) },
        { path: 'alarm.volume', label: 'Volumen', type: 'range', min: 0, max: 1, step: 0.05 },
        { path: 'alarm.duration_seconds', label: 'Duración máxima', help: 'Segundos', type: 'number', min: 1, max: 300, step: 1 },
        { path: 'alarm.repetitions', label: 'Repeticiones', type: 'number', min: 1, max: 50, int: true },
        { path: 'alarm.min_interval_seconds', label: 'Tiempo mínimo entre alarmas', help: 'Segundos', type: 'number', min: 0, max: 3600, step: 1 },
      ]) }))));
  },

  async entrada(root) {
    const al = await act(() => api('GET', '/api/alarm'));
    if (!al) return;
    const ch = al.chime;
    root.append(h('div', { class: 'grid grid-3' },
      h('div', { class: 'stack' },
        card({ title: 'Sonido de entrada', body: h('div', { class: 'stack' },
          h('p', { class: 'small muted' }, 'Un sonido breve cuando se confirma que alguien ha entrado. Es independiente de la alarma y de las notificaciones; si suena la alarma, no se superpone.'),
          h('div', { class: 'row' }, ch.enabled ? badge('Activado', 'ok') : badge('Desactivado', 'off'), ch.muted ? badge('Silenciado', 'warn') : null),
          ch.last_error ? alertBox('err', h('span', {}, ch.last_error, ' ', h('a', { href: '#sonido/salida' }, 'Diagnosticar'))) : null,
          btn('Probar sonido de entrada', { variant: 'primary', icon: 'play', onClick: () => testSound('/api/alarm/chime-test') })) })),
      h('div', { class: 'span-2' }, card({ title: 'Configuración', body: settingsForm([
        { path: 'entry.enabled', label: 'Sonido de entrada', type: 'switch' },
        { path: 'entry.sound', label: 'Sonido', help: 'Añade los tuyos en Biblioteca', type: 'select', options: soundOptions(al.sounds) },
        { path: 'entry.volume', label: 'Volumen', type: 'range', min: 0, max: 1, step: 0.05 },
        { path: 'entry.duration_seconds', label: 'Duración máxima', help: 'Segundos', type: 'number', min: 1, max: 300, step: 1 },
        { path: 'entry.repetitions', label: 'Repeticiones', type: 'number', min: 1, max: 50, int: true },
        { path: 'entry.min_interval_seconds', label: 'Tiempo mínimo entre sonidos', help: 'Segundos', type: 'number', min: 0, max: 3600, step: 1 },
        { path: 'entry.on_authorized', label: 'Persona autorizada', type: 'switch' },
        { path: 'entry.on_unknown', label: 'Persona desconocida', help: 'Solo si la alarma está desactivada o silenciada', type: 'switch' },
        { path: 'entry.on_unverified', label: 'Persona no verificable', type: 'switch' },
      ]) }))));
  },

  async biblioteca(root) {
    const al = await act(() => api('GET', '/api/alarm'));
    if (!al) return;
    const diag = await api('GET', '/api/audio/diagnostics').catch(() => null);
    const reload = () => go('sonido/biblioteca');
    const file = h('input', { type: 'file', accept: 'audio/*,.wav,.mp3,.ogg,.flac,.m4a', class: 'sr-only' });
    const drop = h('label', { class: 'dropzone' }, icon('upload'), h('strong', {}, 'Sube un sonido desde tu dispositivo'),
      h('span', { class: 'small' }, diag && diag.ffmpeg ? 'WAV, MP3, OGG, FLAC o M4A · máx. 8 MB · se recorta a 60 s' : 'WAV (8/16/24/32 bit, cualquier frecuencia) · máx. 8 MB · para MP3/OGG instala ffmpeg'), file);
    async function upload(f) {
      if (!f) return;
      if (f.size > 8 * 1024 * 1024) { toast('El fichero supera 8 MB', 'err'); return; }
      drop.classList.add('drag');
      const r = await act(() => api('POST', `/api/alarm/sounds?name=${encodeURIComponent(f.name)}`, f, { raw: true, contentType: f.type || 'application/octet-stream' }));
      drop.classList.remove('drag');
      if (r) { toast(`Sonido «${r.name}» añadido${r.truncated ? ' (recortado a 60 s)' : ''}`, 'ok'); reload(); }
    }
    file.addEventListener('change', () => upload(file.files[0]));
    drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('drag'); });
    drop.addEventListener('dragleave', () => drop.classList.remove('drag'));
    drop.addEventListener('drop', (e) => { e.preventDefault(); drop.classList.remove('drag'); upload(e.dataTransfer.files[0]); });
    const cfg = S.settings;
    const rows = al.sounds.map((s) => {
      const audio = h('audio', { controls: true, preload: 'none', src: `/api/alarm/sounds/${encodeURIComponent(s.name)}` });
      const uses = nn(cfg.alarm.sound === s.name ? badge('Alarma', 'err') : null, cfg.entry.sound === s.name ? badge('Entrada', 'info') : null);
      return h('tr', {},
        h('td', {}, h('strong', {}, soundLabel(s)), h('div', { class: 'subtle small mono' }, s.name)),
        h('td', {}, s.builtin ? badge('Integrado', 'accent', true) : badge('Propio', 'off', true)),
        h('td', {}, uses.length ? h('div', { class: 'row' }, uses) : h('span', { class: 'subtle' }, '—')),
        h('td', { style: { minWidth: '220px' } }, audio),
        h('td', {}, h('div', { class: 'table-actions' },
          btn('Alarma', { size: 'sm', disabled: cfg.alarm.sound === s.name, onClick: async () => { const r = await act(() => api('PUT', '/api/settings', { alarm: { sound: s.name } }), 'Sonido de alarma cambiado'); if (r) { S.settings = r.settings; reload(); } } }),
          btn('Entrada', { size: 'sm', disabled: cfg.entry.sound === s.name, onClick: async () => { const r = await act(() => api('PUT', '/api/settings', { entry: { sound: s.name } }), 'Sonido de entrada cambiado'); if (r) { S.settings = r.settings; reload(); } } }),
          s.builtin ? null : btn('', { size: 'sm', icon: 'trash', label: 'Eliminar', variant: 'ghost', onClick: async () => {
            if (await confirmDialog({ title: `Eliminar «${s.name}»`, message: 'Si estaba en uso, se volverá al sonido integrado.', confirmText: 'Eliminar', danger: true })) { const r = await act(() => api('DELETE', `/api/alarm/sounds/${encodeURIComponent(s.name)}`), 'Sonido eliminado'); if (r) { S.settings = await api('GET', '/api/settings'); reload(); } }
          } }))));
    });
    root.append(h('div', { class: 'stack' }, drop,
      h('section', { class: 'card' }, h('div', { class: 'card-head' }, h('div', {}, h('h2', {}, 'Sonidos'), h('p', {}, 'Escúchalos en el navegador y asígnalos a la alarma o al sonido de entrada'))), h('div', { class: 'card-body', style: { padding: '12px 0 0' } }, table(['Nombre', 'Tipo', 'En uso', 'Escuchar', ''], rows)))));
  },

  async politica(root) {
    root.append(card({ title: 'Confirmación y política de alertas', subtitle: 'Cuándo se considera confirmada una presencia y qué se hace en cada caso', body: settingsForm([
      { path: 'policy.unknown', label: 'Persona desconocida', type: 'select', options: [['alert', 'Alertar (alarma + notificación)'], ['log', 'Solo registrar'], ['ignore', 'Ignorar']] },
      { path: 'policy.unverified', label: 'Persona no verificable', help: 'El modo estricto puede dar falsas alarmas si alguien está de espaldas o lejos', type: 'select', options: [['log', 'Solo registrar (recomendado)'], ['alert', 'Alertar (modo estricto)'], ['ignore', 'Ignorar']] },
      { path: 'policy.authorized', label: 'Persona autorizada', type: 'select', options: [['ignore', 'Ignorar (no se guarda nada)'], ['log', 'Registrar presencia']] },
      { path: 'tracking.confirm_seconds', label: 'Tiempo de confirmación', help: 'Segundos de presencia antes de confirmar un desconocido', type: 'number', min: 0, max: 60, step: 0.5 },
      { path: 'tracking.min_observations', label: 'Observaciones mínimas', type: 'number', min: 1, max: 50, int: true },
      { path: 'tracking.exit_seconds', label: 'Ausencia para considerar que salió', help: 'Segundos', type: 'number', min: 1, max: 600, step: 1 },
      { path: 'tracking.alert_cooldown_seconds', label: 'Tiempo entre alertas', help: 'Los eventos dentro de este margen se registran sin volver a alertar', type: 'number', min: 0, max: 3600, step: 1 },
      { path: 'tracking.unverified_timeout_seconds', label: 'Espera antes de «no verificable»', help: 'Segundos', type: 'number', min: 1, max: 600, step: 1 },
    ]) }));
  },
};

function soundLabel(s) {
  return s.name === 'default' ? 'Sirena integrada' : s.name === 'entrada.wav' ? 'Timbre de entrada' : s.name.replace(/\.wav$/, '');
}
const soundOptions = (sounds) => sounds.map((s) => [s.name, `${soundLabel(s)}${s.builtin ? ' (integrado)' : ''}`]);

// ----------------------------------------------------------------------- NOTIFICACIONES

views.notificaciones = {
  title: 'Notificaciones', crumb: 'Configuración',
  async build(root) {
    const n = await act(() => api('GET', '/api/notifications'));
    if (!n) return;
    const reload = () => go('notificaciones');
    const s = n.status;
    const stTone = { ok: 'ok', idle: 'ok', disabled: 'off', unconfigured: 'warn', error: 'err' }[s.state];
    const stLabel = { ok: 'Conectado', idle: 'Listo', disabled: 'Desactivado', unconfigured: 'Sin configurar', error: 'Error' }[s.state];
    const topicBox = h('div', { class: 'mono' }, S.settings.ntfy.topic_set ? S.settings.ntfy.topic_masked : 'sin configurar');
    const subBox = h('div', {});
    const tokIn = h('input', { class: 'input', type: 'password', placeholder: s.token_set ? `Guardado: ${s.token_hint}` : 'Token (opcional)', autocomplete: 'off', 'aria-label': 'Token de ntfy' });
    root.append(
      pageHead('Notificaciones push', 'Avisos al móvil mediante ntfy. Sin fotos, nombres ni datos biométricos.',
        [btn('Enviar notificación de prueba', { variant: 'primary', icon: 'bell', onClick: async () => { const r = await act(() => api('POST', '/api/notifications/test')); if (r) { toast(r.ok ? 'Notificación de prueba enviada' : r.error, r.ok ? 'ok' : 'err'); reload(); } } })]),
      h('div', { class: 'stack' },
        h('div', { class: 'grid grid-4' },
          stat('Estado', stLabel, { ic: 'wifi', tone: stTone === 'off' ? 'off' : stTone, hint: s.message }),
          stat('Pendientes', String(s.pending), { ic: 'clock', tone: s.pending ? 'warn' : 'accent', hint: 'en cola local' }),
          stat('Enviadas', String(s.sent), { ic: 'check', tone: 'ok', hint: `Último envío ${fmtAgo(s.last_success)}` }),
          stat('Fallidas', String(s.failed), { ic: 'alert', tone: s.failed ? 'err' : 'off', hint: s.failed ? 'Puedes reintentarlas' : '—' })),
        s.last_error ? alertBox('err', s.last_error, 'Último error:') : null,
        h('div', { class: 'grid grid-2' },
          card({ title: 'Servidor y prioridad', body: settingsForm([
            { path: 'ntfy.enabled', label: 'Enviar notificaciones', type: 'switch' },
            { path: 'ntfy.server', label: 'URL del servidor', help: 'https://ntfy.sh (público) o tu servidor privado', type: 'text' },
            { path: 'ntfy.priority', label: 'Prioridad', type: 'select', num: true, int: true, options: [[1, '1 · mínima'], [2, '2 · baja'], [3, '3 · normal'], [4, '4 · alta'], [5, '5 · urgente']] },
            { path: 'ntfy.max_attempts', label: 'Reintentos máximos', type: 'number', min: 1, max: 100, int: true },
            { path: 'ntfy.max_age_hours', label: 'Descartar pendientes tras', help: 'Horas sin poder enviarse', type: 'number', min: 0.1, max: 720, step: 0.5 },
            { path: 'ntfy.queue_max', label: 'Máximo de pendientes', type: 'number', min: 1, max: 5000, int: true },
          ], { onSaved: reload }) }),
          h('div', { class: 'stack' },
            card({ title: 'Tópico', subtitle: 'Suscríbete a él desde la app ntfy del móvil', body: h('div', { class: 'stack' }, topicBox,
              h('div', { class: 'row' },
                btn('Mostrar', { icon: 'eye', onClick: async () => { const r = await act(() => api('GET', '/api/notifications/topic')); if (r) { topicBox.textContent = r.topic || 'sin configurar'; subBox.replaceChildren(r.topic ? alertBox('info', `En la app ntfy: «Suscribirse a un tema» → ${r.topic}${r.subscribe_url.startsWith('https://ntfy.sh/') ? '' : ` (servidor ${r.subscribe_url.replace(/\/[^/]+$/, '')})`}`) : ''); } } }),
                btn('Generar tópico aleatorio', { icon: 'refresh', onClick: async () => {
                  if (S.settings.ntfy.topic_set && !(await confirmDialog({ title: 'Generar un tópico nuevo', message: 'Tendrás que volver a suscribir el móvil al nuevo tópico.', confirmText: 'Generar' }))) return;
                  const r = await act(() => api('POST', '/api/notifications/generate-topic'), 'Tópico nuevo generado');
                  if (r) { S.settings = await api('GET', '/api/settings'); topicBox.textContent = r.topic; subBox.replaceChildren(alertBox('info', 'Suscribe el móvil a este tópico. No lo compartas ni lo publiques.')); }
                } })),
              subBox,
              alertBox('warn', 'Un tópico aleatorio es difícil de adivinar, pero NO es autenticación: con el servidor público, quien lo conozca puede leer tus avisos.')) }),
            card({ title: 'Token de acceso', subtitle: 'Solo para servidores ntfy con autenticación', body: h('div', { class: 'stack' },
              h('div', { class: 'inline-form' }, tokIn,
                btn('Guardar', { onClick: async () => { if (!tokIn.value.trim()) { toast('Escribe un token', 'warn'); return; } const r = await act(() => api('PUT', '/api/notifications/token', { token: tokIn.value.trim() }), 'Token guardado'); if (r) reload(); } }),
                s.token_set ? btn('Quitar', { variant: 'ghost', onClick: async () => { await act(() => api('PUT', '/api/notifications/token', { token: null }), 'Token eliminado'); reload(); } }) : null),
              h('p', { class: 'small muted' }, 'Se guarda con permisos restrictivos y nunca se vuelve a mostrar completo.')) }))),
        card({ title: 'Historial de envíos', actions: [btn('Reintentar fallidas', { size: 'sm', icon: 'refresh', onClick: async () => { const r = await act(() => api('POST', '/api/notifications/retry-failed')); if (r) { toast(`${r.requeued} reencoladas`, 'ok'); reload(); } } }),
          btn('Borrar', { size: 'sm', variant: 'ghost', icon: 'trash', onClick: async () => { if (await confirmDialog({ title: 'Borrar historial de envíos', message: 'No afecta a las notificaciones pendientes.', confirmText: 'Borrar', danger: true })) { await act(() => api('DELETE', '/api/notifications/history'), 'Historial de envíos borrado'); reload(); } } })],
          body: table(['Fecha', 'Tipo', 'Estado', 'Intentos', 'Detalle'], n.history.map((r) => h('tr', {}, h('td', {}, fmtTime(r.created_at)), h('td', {}, r.kind === 'test' ? 'Prueba' : 'Alerta'),
            h('td', {}, fromMap(NOTIF, r.status)), h('td', {}, String(r.attempts)), h('td', { class: 'small' }, r.last_error || (r.status === 'PENDING' ? `Próximo intento ${fmtTime(r.next_attempt_at)}` : '')))), 'Sin envíos todavía.') }),
        alertBox('info', 'La entrega depende del móvil: ahorro de batería, permisos de notificación y conectividad. Una notificación no equivale a una llamada ni está garantizada al instante.')));
  },
};

// ----------------------------------------------------------------------- SISTEMA

views.sistema = {
  title: 'Sistema', crumb: 'Administración',
  async build(root) {
    const sys = await act(() => api('GET', '/api/system'));
    if (!sys) return;
    const PAGE = 20;
    let offset = 0;
    const auditBody = h('div', {});
    const pager = h('div', { class: 'pager' });
    async function loadAudit() {
      const r = await act(() => api('GET', `/api/audit?limit=${PAGE}&offset=${offset}`));
      if (!r) return;
      auditBody.replaceChildren(table(['Fecha', 'Acción', 'Detalle', 'Origen'], r.entries.map((e) => h('tr', {},
        h('td', {}, fmtTime(e.ts)), h('td', {}, badge(e.label, e.action === 'login.fail' ? 'err' : e.action.includes('delete') || e.action.includes('clear') ? 'warn' : 'accent', true)),
        h('td', { class: 'small' }, e.detail || '—'), h('td', { class: 'mono subtle' }, e.ip || '—'))), 'Sin acciones registradas.'));
      const to = Math.min(offset + PAGE, r.total);
      pager.replaceChildren(h('span', {}, `${r.total ? offset + 1 : 0}–${to} de ${r.total}`), h('div', { class: 'actions' },
        btn('Anterior', { size: 'sm', disabled: offset === 0, onClick: () => { offset = Math.max(0, offset - PAGE); return loadAudit(); } }),
        btn('Siguiente', { size: 'sm', disabled: to >= r.total, onClick: () => { offset += PAGE; return loadAudit(); } })));
    }
    const mem = sys.memory || {};
    const tempTone = sys.cpu_temp == null ? 'off' : sys.cpu_temp >= 75 ? 'err' : sys.cpu_temp >= 65 ? 'warn' : 'ok';
    const memCard = stat('Memoria', mem.used_pct != null ? `${mem.used_pct}%` : '—', { ic: 'cpu', hint: mem.total ? `${fmtBytes(mem.total - mem.available)} de ${fmtBytes(mem.total)}` : '' });
    memCard.querySelector('div:last-child').append(bar(mem.used_pct));
    const diskCard = stat('Disco', `${sys.disk.used_pct}%`, { ic: 'disk', hint: `${fmtBytes(sys.disk.free)} libres` });
    diskCard.querySelector('div:last-child').append(bar(sys.disk.used_pct));
    root.append(
      pageHead('Sistema', 'Estado del equipo y registro de auditoría de las acciones administrativas.', [btn('Actualizar', { icon: 'refresh', onClick: () => go('sistema') })]),
      h('div', { class: 'stack' },
        sys.temp_warning ? alertBox('err', `La CPU está a ${sys.cpu_temp} °C. La Raspberry Pi reduce su velocidad por encima de 80 °C: mejora la ventilación o usa un disipador.`) : null,
        h('div', { class: 'grid grid-4' },
          stat('Temperatura CPU', sys.cpu_temp != null ? `${sys.cpu_temp} °C` : '—', { ic: 'thermo', tone: tempTone, hint: sys.load ? `Carga ${sys.load.join(' · ')}` : '' }),
          memCard, diskCard,
          stat('Tiempo activo', fmtDur(sys.uptime_app), { ic: 'clock', hint: `Equipo: ${fmtDur(sys.uptime_host)}` })),
        h('div', { class: 'grid grid-3' },
          card({ title: 'Información', body: h('dl', { class: 'kv' },
            h('dt', {}, 'Versión'), h('dd', {}, `Pi Sentinel ${sys.version}`),
            h('dt', {}, 'Equipo'), h('dd', {}, `${sys.hostname} · ${sys.model}`),
            h('dt', {}, 'Sistema'), h('dd', {}, `${sys.os} · kernel ${sys.kernel}`),
            h('dt', {}, 'Software'), h('dd', {}, `Python ${sys.python} · OpenCV ${sys.opencv || '—'} · ${sys.cpu_count} CPU`),
            h('dt', {}, 'Datos'), h('dd', { class: 'mono' }, sys.data_dir),
            h('dt', {}, 'Base de datos'), h('dd', {}, fmtBytes(sys.db_size))) }),
          h('section', { class: 'card span-2' }, h('div', { class: 'card-head' }, h('div', {}, h('h2', {}, 'Registro de auditoría'), h('p', {}, 'Quién cambió qué y cuándo. Nunca incluye secretos ni valores sensibles.'))),
            h('div', { class: 'card-body', style: { padding: '12px 0 0' } }, auditBody), pager))));
    loadAudit();
  },
};

// ----------------------------------------------------------------------- AJUSTES

views.ajustes = {
  title: 'Ajustes', crumb: 'Administración',
  async build(root) {
    const s = S.session;
    const cur = h('input', { class: 'input', type: 'password', autocomplete: 'current-password', 'aria-label': 'Contraseña actual' });
    const nw = h('input', { class: 'input', type: 'password', autocomplete: 'new-password', 'aria-label': 'Nueva contraseña' });
    const nw2 = h('input', { class: 'input', type: 'password', autocomplete: 'new-password', 'aria-label': 'Repite la contraseña' });
    const msg = h('div', { class: 'form-error hidden' });
    root.append(
      pageHead('Ajustes', 'Identificación del dispositivo, seguridad de acceso y red.'),
      h('div', { class: 'grid grid-2' },
        h('div', { class: 'stack' },
          card({ title: 'General', body: settingsForm([{ path: 'general.device_name', label: 'Nombre del dispositivo', help: 'Aparece en las notificaciones. Vacío = nombre del equipo', type: 'text', placeholder: 'p. ej. salón' }]) }),
          card({ title: 'Acceso desde la red', subtitle: 'Requiere reiniciar el servicio', body: h('div', { class: 'stack' },
            alertBox('warn', 'Por defecto la interfaz solo escucha en 127.0.0.1. Para abrirla a tu red doméstica necesitas contraseña. No la expongas a Internet; para HTTPS usa un proxy inverso.'),
            settingsForm([
              { path: 'web.allow_lan', label: 'Permitir acceso desde la red local', type: 'switch' },
              { path: 'web.host', label: 'Dirección de escucha', help: '127.0.0.1 (solo local) o 0.0.0.0 (red local)', type: 'text' },
              { path: 'web.port', label: 'Puerto', type: 'number', min: 1024, max: 65535, int: true },
            ])) })),
        h('div', { class: 'stack' },
          card({ title: 'Contraseña de administración', subtitle: s.password_set ? 'Hay una contraseña definida' : 'Sin contraseña: solo accesible desde este equipo', body: h('form', { class: 'stack', onsubmit: async (e) => {
            e.preventDefault(); msg.classList.add('hidden');
            if (nw.value !== nw2.value) { msg.textContent = 'Las contraseñas no coinciden'; msg.classList.remove('hidden'); return; }
            try { const r = await api('POST', '/api/password', { current: cur.value, new: nw.value }); S.csrf = r.csrf; S.session.password_set = true; toast('Contraseña guardada', 'ok'); go('ajustes'); }
            catch (err) { msg.textContent = err.message; msg.classList.remove('hidden'); }
          } },
            s.password_set ? h('label', {}, h('div', { class: 'field-label' }, 'Contraseña actual'), cur) : null,
            h('label', {}, h('div', { class: 'field-label' }, 'Nueva contraseña'), h('div', { class: 'field-help' }, 'Mínimo 10 caracteres'), nw),
            h('label', {}, h('div', { class: 'field-label' }, 'Repite la contraseña'), nw2), msg,
            h('div', {}, h('button', { class: 'btn btn-primary', type: 'submit' }, icon('lock', 'icon icon-sm'), 'Guardar contraseña'))) }),
          card({ title: 'Sesión', body: h('div', { class: 'stack' },
            h('p', { class: 'small muted' }, s.auth_required ? 'Sesión iniciada con contraseña. Caduca tras 12 horas.' : 'Acceso local sin contraseña.'),
            s.auth_required ? h('div', {}, btn('Cerrar sesión', { icon: 'logout', onClick: doLogout })) : null) }))));
  },
};

// ======================================================================= estructura y navegación

const NAV = [
  ['Monitorización', [['panel', 'Panel', 'dashboard'], ['camara', 'Cámara', 'camera'], ['historial', 'Historial', 'history']]],
  ['Configuración', [['personas', 'Personas', 'users'], ['sonido', 'Sonido y alarmas', 'volume'], ['notificaciones', 'Notificaciones', 'bell']]],
  ['Administración', [['sistema', 'Sistema', 'server'], ['ajustes', 'Ajustes', 'sliders']]],
];
const ALIASES = { alarmas: 'sonido' };

function getTheme() { try { return localStorage.getItem('ps-theme') || 'auto'; } catch (e) { return 'auto'; } }
function applyTheme(t) {
  if (t === 'auto') document.documentElement.removeAttribute('data-theme'); else document.documentElement.setAttribute('data-theme', t);
  try { localStorage.setItem('ps-theme', t); } catch (e) { /* almacenamiento no disponible */ }
}
const isDark = () => document.documentElement.getAttribute('data-theme') === 'dark' || (getTheme() === 'auto' && window.matchMedia('(prefers-color-scheme: dark)').matches);

function buildShell() {
  const nav = h('nav', { 'aria-label': 'Secciones' }, NAV.map(([group, items]) => h('div', { class: 'nav-group' }, h('div', { class: 'nav-label' }, group),
    items.map(([k, label, ic]) => h('button', { class: 'nav-item', 'data-route': k, onclick: () => go(k) }, icon(ic), h('span', {}, label), k === 'sonido' ? h('span', { class: 'nav-badge hidden', id: 'nav-audio-badge' }, '!') : null)))));
  const sidebar = h('aside', { class: 'sidebar', id: 'sidebar' },
    h('div', { class: 'brand' }, h('div', { class: 'brand-mark' }, icon('shield')), h('div', {}, h('div', { class: 'brand-name' }, 'Pi Sentinel'), h('div', { class: 'brand-sub', id: 'brand-device' }, 'Vigilancia local'))),
    nav, h('div', { class: 'sidebar-foot' }, h('div', {}, `Versión ${S.session.version || ''}`), h('div', {}, 'Procesamiento 100 % local')));
  const backdrop = h('div', { class: 'backdrop', onclick: () => toggleMenu(false) });
  const themeBtn = btn('', { icon: isDark() ? 'sun' : 'moon', label: 'Cambiar tema', variant: 'ghost', onClick: () => {
    applyTheme(isDark() ? 'light' : 'dark');
    themeBtn.replaceChildren(icon(isDark() ? 'sun' : 'moon', 'icon icon-sm'));
  } });
  const topbar = h('header', { class: 'topbar' },
    btn('', { icon: 'menu', label: 'Menú', variant: 'ghost', onClick: () => toggleMenu() }),
    h('div', { class: 'topbar-title' }, h('div', { class: 'crumb', id: 'crumb' }), h('h1', { id: 'page-title' })),
    h('div', { class: 'topbar-right' },
      h('span', { class: 'chip keep', id: 'chip-mode' }, h('span', { class: 'dot' }), '…'),
      h('span', { class: 'chip chip-hide-sm', id: 'chip-camera' }, h('span', { class: 'dot' }), 'Cámara'),
      h('span', { class: 'chip chip-hide-sm', id: 'chip-audio' }, h('span', { class: 'dot' }), 'Audio'),
      h('span', { class: 'chip chip-hide-sm', id: 'chip-ntfy' }, h('span', { class: 'dot' }), 'ntfy'),
      themeBtn,
      S.session.auth_required ? btn('', { icon: 'logout', label: 'Cerrar sesión', variant: 'ghost', onClick: doLogout }) : null));
  topbar.firstChild.classList.add('menu-btn');
  const app = $('#app');
  app.className = '';
  app.replaceChildren(h('div', { class: 'shell' }, sidebar, backdrop, h('div', { class: 'main' }, topbar, h('main', { class: 'content', id: 'view', tabindex: '-1' }))));
}

function toggleMenu(force) {
  const sb = $('#sidebar'), bd = $('.backdrop');
  const open = force ?? !sb.classList.contains('open');
  sb.classList.toggle('open', open); bd.classList.toggle('open', open);
}

function setChip(id, tone, text) {
  const c = document.getElementById(id);
  if (!c) return;
  c.replaceChildren(h('span', { class: `dot ${tone}${tone === 'ok' && id === 'chip-mode' ? ' pulse' : ''}` }), text);
}

let navToken = 0;
async function go(route) {
  if (window.innerWidth <= 860) toggleMenu(false);
  const [main, sub] = route.split('/');
  const r = ALIASES[main] || main;
  const key = views[r] ? r : 'panel';
  S.cleanup.forEach((fn) => { try { fn(); } catch (e) { /* ya detenido */ } });
  S.cleanup = [];
  S.route = key; S.sub = sub || null;
  const hash = '#' + key + (sub ? '/' + sub : '');
  if (location.hash !== hash) history.replaceState(null, '', hash);
  document.querySelectorAll('.nav-item').forEach((b) => b.setAttribute('aria-current', b.dataset.route === key ? 'page' : 'false'));
  const v = views[key];
  $('#page-title').textContent = v.title;
  $('#crumb').textContent = v.crumb;
  document.title = `${v.title} · Pi Sentinel`;
  const root = $('#view');
  root.replaceChildren(h('div', { class: 'skeleton' }));
  const token = ++navToken;
  const frag = h('div', {});
  v.onStatus = null;
  S.view = v;
  try { await v.build.call(v, frag); }
  catch (e) { frag.replaceChildren(alertBox('err', e.message, 'No se pudo cargar la página.')); }
  if (token !== navToken) return; // el usuario ya navegó a otra página
  root.replaceChildren(...frag.childNodes);
  root.scrollTop = 0; window.scrollTo(0, 0);
  if (S.status && v.onStatus) v.onStatus(S.status);
}

async function refreshStatus() {
  let s;
  try { s = await api('GET', '/api/status'); } catch (e) { setChip('chip-mode', 'err', 'Sin conexión'); return; }
  S.status = s;
  const [label, tone] = MODE[s.mode];
  setChip('chip-mode', tone, label);
  const c = s.components;
  const t = (st) => ({ ok: 'ok', warn: 'warn', error: 'err', disabled: 'off' }[st] || 'off');
  setChip('chip-camera', t(c.camera.state), 'Cámara');
  setChip('chip-audio', t(c.alarm.state), 'Audio');
  setChip('chip-ntfy', t(c.ntfy.state), 'ntfy');
  const badgeEl = document.getElementById('nav-audio-badge');
  if (badgeEl) badgeEl.classList.toggle('hidden', !(c.alarm.state === 'error' || s.alarm.last_error));
  const dev = document.getElementById('brand-device');
  if (dev) dev.textContent = s.device_name;
  if (S.view && S.view.onStatus) { try { S.view.onStatus(s); } catch (e) { console.error(e); } }
}

// ======================================================================= sesión

function showLogin() {
  if ($('#login-screen')) return;
  const pw = h('input', { class: 'input', type: 'password', id: 'login-pw', autocomplete: 'current-password', required: true, 'aria-label': 'Contraseña' });
  const err = h('div', { class: 'form-error hidden' });
  const submit = h('button', { class: 'btn btn-primary', type: 'submit', style: { width: '100%' } }, icon('lock', 'icon icon-sm'), 'Entrar');
  const form = h('form', { class: 'card login-card stack' },
    h('div', { class: 'brand' }, h('div', { class: 'brand-mark' }, icon('shield')), h('div', {}, h('div', { class: 'brand-name' }, 'Pi Sentinel'), h('div', { class: 'brand-sub' }, 'Consola de administración'))),
    h('div', {}, h('h2', {}, 'Iniciar sesión'), h('p', { class: 'muted small' }, 'Introduce la contraseña de administración.')),
    h('label', {}, h('div', { class: 'field-label' }, 'Contraseña'), pw), err, submit);
  form.addEventListener('submit', async (e) => {
    e.preventDefault(); err.classList.add('hidden'); submit.disabled = true;
    try {
      const s = await (await fetch('/api/session', { credentials: 'same-origin' })).json(); // CSRF fresco (la sesión pudo caducar)
      S.csrf = s.csrf;
      const r = await fetch('/api/login', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': S.csrf }, body: JSON.stringify({ password: pw.value }) });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) { err.textContent = errMessage(data, r.status); err.classList.remove('hidden'); pw.select(); return; }
      S.csrf = data.csrf;
      screen.remove();
      await start();
    } catch (ex) { err.textContent = 'No se pudo contactar con el servicio.'; err.classList.remove('hidden'); }
    finally { submit.disabled = false; }
  });
  const screen = h('div', { class: 'login overlay', id: 'login-screen' }, form);
  document.body.append(screen);
  pw.focus();
}

async function doLogout() {
  if (!(await confirmDialog({ title: 'Cerrar sesión', message: '¿Quieres cerrar la sesión de administración?', confirmText: 'Cerrar sesión' }))) return;
  await act(() => api('POST', '/api/logout'));
  location.reload();
}

let pollTimer = null;
async function start() {
  const r = await fetch('/api/session', { credentials: 'same-origin' });
  S.session = await r.json();
  S.csrf = S.session.csrf;
  if (S.session.auth_required && !S.session.authenticated) { $('#app').replaceChildren(); showLogin(); return; }
  S.settings = await api('GET', '/api/settings');
  buildShell();
  await refreshStatus();
  const wanted = location.hash.slice(1);
  await go(wanted || (S.session.first_run ? 'personas' : 'panel'));
  if (!pollTimer) pollTimer = setInterval(() => { if (!document.hidden && !$('#login-screen')) refreshStatus(); }, 2000);
}

window.addEventListener('hashchange', () => {
  const want = location.hash.slice(1);
  const cur = S.route + (S.sub ? '/' + S.sub : '');
  if (want && want !== cur && $('#view')) go(want);
});
applyTheme(getTheme());
start().catch((e) => { $('#app').replaceChildren(h('div', { class: 'boot' }, alertBox('err', e.message, 'Error al iniciar la interfaz.'))); });
