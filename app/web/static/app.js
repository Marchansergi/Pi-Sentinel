'use strict';
/* Pi Sentinel — interfaz local. Sin dependencias externas. Todo el texto dinámico se inserta con
   textContent (nunca innerHTML) para evitar XSS con nombres u otros datos. */

const state = { csrf: null, session: null, status: null, settings: null, view: null, cleanup: [] };

// ------------------------------------------------------------------ utilidades

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'value') el.value = v;
    else if (k === 'checked' || k === 'disabled' || k === 'selected') el[k] = !!v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const $ = (sel, root = document) => root.querySelector(sel);
// replaceChildren/append convierten null en el texto «null»: filtrar siempre
const nn = (...a) => a.flat().filter((x) => x !== null && x !== undefined && x !== false);

let toastTimer = null;
function toast(msg, isErr) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast' + (isErr ? ' err' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add('hidden'), isErr ? 7000 : 3500);
}

function errMessage(data, status) {
  const d = data && data.detail;
  if (Array.isArray(d)) return d.map((e) => (e.loc ? e.loc.slice(1).join('.') + ': ' : '') + e.msg).join('; ');
  return d || `Error ${status}`;
}

async function api(method, url, body, opts = {}) {
  const headers = {};
  if (state.csrf) headers['X-CSRF-Token'] = state.csrf;
  let payload;
  if (opts.raw) { payload = body; headers['Content-Type'] = opts.contentType || 'application/octet-stream'; }
  else if (body !== undefined) { payload = JSON.stringify(body); headers['Content-Type'] = 'application/json'; }
  let res;
  try { res = await fetch(url, { method, headers, body: payload, credentials: 'same-origin' }); }
  catch (e) { throw new Error('No se pudo contactar con Pi Sentinel (¿el servicio está en marcha?)'); }
  if (opts.blob && res.ok) return res.blob();
  let data = null;
  try { data = await res.json(); } catch (e) { /* sin cuerpo JSON */ }
  if (res.status === 401) { showLogin(); throw new Error('Sesión no iniciada'); }
  if (!res.ok) throw new Error(errMessage(data, res.status));
  return data;
}

async function act(fn, okMsg) {
  try { const r = await fn(); if (okMsg) toast(okMsg); return r; }
  catch (e) { toast(e.message, true); return null; }
}

const fmtTime = (ts) => (ts ? new Date(ts * 1000).toLocaleString('es-ES') : '—');
const STATUS_ES = { AUTHORIZED: 'Autorizada', UNKNOWN: 'Desconocida', UNVERIFIED: 'No verificable' };
const MODE_ES = { stopped: 'Detenida', test: 'Modo prueba', active: 'Vigilando' };
const STATE_ES = { ok: 'Correcto', warn: 'Atención', error: 'Error', disabled: 'Inactivo' };
const ALARM_ES = { NONE: '—', TRIGGERED: 'Sonó', DISABLED: 'Sonido desactivado', MUTED: 'Silenciada', RATE_LIMITED: 'Limitada', BUSY: 'Ya sonando', ERROR: 'Error de audio', SUPPRESSED: 'Suprimida (espera)' };
const NOTIF_ES = { NONE: '—', QUEUED: 'En cola', PENDING: 'Pendiente', SENT: 'Enviada', FAILED: 'Fallida', DISABLED: 'ntfy desactivado', NOT_CONFIGURED: 'Sin configurar', DUPLICATE: 'Duplicada', ERROR: 'Error', SUPPRESSED: 'Suprimida (espera)' };

const badge = (text, cls) => h('span', { class: 'badge ' + (cls || '') }, text);
const getPath = (o, p) => p.split('.').reduce((a, k) => (a == null ? a : a[k]), o);
function setPath(o, p, v) { const ks = p.split('.'); let c = o; ks.slice(0, -1).forEach((k) => (c = c[k] = c[k] || {})); c[ks[ks.length - 1]] = v; }

function field(label, control, hint) {
  return h('label', {}, label, control, hint ? h('span', { class: 'hint' }, hint) : null);
}

/* Formulario de ajustes genérico: fields = [{path,label,hint,type,min,max,step,int,options,num}] */
function settingsForm(fields, onSaved, submitText = 'Guardar cambios') {
  const inputs = {};
  const grid = h('div', { class: 'form-grid' });
  for (const f of fields) {
    const cur = getPath(state.settings, f.path);
    let ctl;
    if (f.type === 'select') {
      ctl = h('select', {}, f.options.map(([v, t]) => h('option', { value: v, selected: String(v) === String(cur) }, t)));
    } else if (f.type === 'checkbox') {
      ctl = h('input', { type: 'checkbox', checked: !!cur });
      grid.append(h('label', { class: 'inline' }, ctl, f.label, f.hint ? h('span', { class: 'hint' }, f.hint) : null));
      inputs[f.path] = { f, ctl };
      continue;
    } else if (f.type === 'range') {
      ctl = h('input', { type: 'range', min: f.min, max: f.max, step: f.step, value: cur });
      const out = h('span', { class: 'mono' }, String(cur));
      ctl.addEventListener('input', () => (out.textContent = ctl.value));
      grid.append(h('label', {}, [f.label, ' ', out], ctl, f.hint ? h('span', { class: 'hint' }, f.hint) : null));
      inputs[f.path] = { f, ctl };
      continue;
    } else {
      ctl = h('input', { type: f.type || 'text', value: cur, min: f.min, max: f.max, step: f.step });
    }
    grid.append(field(f.label, ctl, f.hint));
    inputs[f.path] = { f, ctl };
  }
  const msg = h('p', { class: 'error' });
  const form = h('form', { class: 'stack' }, grid, msg, h('div', { class: 'row' }, h('button', { class: 'primary', type: 'submit' }, submitText)));
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const patch = {};
    for (const { f, ctl } of Object.values(inputs)) {
      let v;
      if (f.type === 'checkbox') v = ctl.checked;
      else if (f.type === 'number' || f.type === 'range' || f.num) v = f.int ? parseInt(ctl.value, 10) : parseFloat(ctl.value);
      else v = ctl.value;
      setPath(patch, f.path, v);
    }
    msg.textContent = '';
    try {
      const r = await api('PUT', '/api/settings', patch);
      state.settings = r.settings;
      toast(r.restart_required.length ? 'Guardado. Reinicia el servicio para aplicar los ajustes de red.' : 'Ajustes guardados');
      if (onSaved) onSaved();
    } catch (e) { msg.textContent = e.message; }
  });
  return form;
}

// ------------------------------------------------------------------ vista previa en vivo

function livePreview({ overlay = 1 } = {}) {
  const img = h('img', { alt: 'Vista previa de la cámara', class: 'hidden' });
  const msg = h('div', { class: 'small' }, 'Vista previa desactivada (ahorra recursos).');
  const el = h('div', { class: 'preview' }, img, msg);
  let running = false, url = null, timer = null;
  async function tick() {
    if (!running) return;
    try {
      const blob = await api('GET', `/api/camera/snapshot.jpg?overlay=${overlay}&t=${Date.now()}`, undefined, { blob: true });
      if (!running) return;
      const next = URL.createObjectURL(blob);
      img.src = next; if (url) URL.revokeObjectURL(url); url = next;
      img.classList.remove('hidden'); msg.classList.add('hidden');
      timer = setTimeout(tick, 600);
    } catch (e) {
      img.classList.add('hidden'); msg.classList.remove('hidden'); msg.textContent = e.message;
      timer = setTimeout(tick, 2500);
    }
  }
  const api_ = {
    el,
    start() { if (running) return; running = true; msg.textContent = 'Conectando con la cámara…'; msg.classList.remove('hidden'); tick(); },
    stop() { running = false; clearTimeout(timer); if (url) { URL.revokeObjectURL(url); url = null; } img.classList.add('hidden'); img.removeAttribute('src'); msg.classList.remove('hidden'); msg.textContent = 'Vista previa desactivada (ahorra recursos).'; },
    get running() { return running; },
  };
  state.cleanup.push(() => api_.stop());
  return api_;
}

function previewToggle(preview) {
  const cb = h('input', { type: 'checkbox' });
  cb.addEventListener('change', () => (cb.checked ? preview.start() : preview.stop()));
  return h('label', { class: 'inline' }, cb, 'Mostrar vista previa');
}

// ------------------------------------------------------------------ componentes comunes

const COMPONENTS = [['camera', 'Cámara'], ['detector', 'Detector de personas'], ['face', 'Reconocimiento facial'], ['ntfy', 'Notificaciones (ntfy)'], ['alarm', 'Alarma sonora'], ['analysis', 'Análisis']];

function statusList(s) {
  return h('ul', { class: 'status-list' }, COMPONENTS.map(([k, label]) => {
    const c = (s && s.components[k]) || { state: 'disabled', message: '…' };
    const extra = k === 'ntfy' && c.pending ? ` · ${c.pending} pendientes` : '';
    return h('li', { 'data-k': k }, h('span', { class: 'dot ' + c.state, title: STATE_ES[c.state] }), h('span', { class: 'name' }, label, ' ', badge(STATE_ES[c.state], c.state)), h('span', { class: 'msg' }, c.message + extra));
  }));
}

// ------------------------------------------------------------------ PANEL

const views = {};

views.panel = {
  title: 'Panel',
  build(root) {
    const modeText = h('p', { class: 'muted' });
    const modeNote = h('p', { class: 'note hidden' });
    const bActive = h('button', { class: 'primary', onclick: () => setMode('active') }, 'Activar vigilancia');
    const bTest = h('button', { onclick: () => setMode('test') }, 'Modo prueba');
    const bStop = h('button', { onclick: () => setMode('stopped') }, 'Detener');
    const comp = h('div');
    const summary = h('div', { class: 'stack' });
    const bMute = h('button', { onclick: toggleMute }, 'Silenciar alarma 30 min');
    const testEvents = h('div', { class: 'card hidden' });
    root.append(
      h('div', { class: 'grid' },
        h('section', { class: 'card' }, h('h2', {}, 'Vigilancia'), modeText, modeNote, h('div', { class: 'row' }, bActive, bTest, bStop),
          h('p', { class: 'muted small' }, 'El modo prueba analiza la imagen y muestra qué ocurriría, pero no suena la alarma, no envía notificaciones y no guarda nada.')),
        h('section', { class: 'card' }, h('h2', {}, 'Estado de los componentes'), comp),
        h('section', { class: 'card' }, h('h2', {}, 'Resumen'), summary),
        h('section', { class: 'card' }, h('h2', {}, 'Acciones'),
          h('div', { class: 'row' }, bMute,
            h('button', { onclick: () => act(async () => { const r = await api('POST', '/api/alarm/test'); if (r.state !== 'TRIGGERED') throw new Error(r.error || 'Alarma: ' + (ALARM_ES[r.state] || r.state)); }, 'Probando alarma…') }, 'Probar alarma'),
            h('button', { onclick: () => act(async () => { const r = await api('POST', '/api/notifications/test'); if (!r.ok) throw new Error(r.error || 'No se pudo enviar'); }, 'Notificación de prueba enviada') }, 'Enviar notificación de prueba'))),
      ),
      testEvents);
    async function setMode(m) {
      if (m === 'active' && state.status && state.status.people.active_ready < 1) {
        toast('Antes de vigilar necesitas registrar tu rostro (Personas).', true); go('personas'); return;
      }
      await act(() => api('POST', '/api/surveillance', { mode: m }), 'Modo: ' + MODE_ES[m]);
      refreshStatus();
    }
    async function toggleMute() {
      const muted = state.status && state.status.alarm.muted;
      await act(() => api('POST', '/api/alarm/mute', { minutes: muted ? null : 30 }), muted ? 'Alarma reactivada' : 'Alarma silenciada 30 min');
      refreshStatus();
    }
    this.onStatus = (s) => {
      modeText.textContent = `Estado: ${MODE_ES[s.mode]}. Cámara ${s.components.camera.state === 'ok' ? 'activa' : 'inactiva'}.`;
      modeNote.textContent = s.mode_note; modeNote.classList.toggle('hidden', !s.mode_note);
      bActive.disabled = s.mode === 'active'; bTest.disabled = s.mode === 'test'; bStop.disabled = s.mode === 'stopped';
      comp.replaceChildren(statusList(s));
      const le = s.last_event;
      summary.replaceChildren(...nn(
        h('div', {}, h('strong', {}, 'Último evento: '), le ? [badge(STATUS_ES[le.identity_status] || le.identity_status, le.identity_status), ' ', fmtTime(le.started_at)] : 'ninguno'),
        h('div', {}, h('strong', {}, 'Eventos en 24 h: '), String(s.recent_events_24h)),
        h('div', {}, h('strong', {}, 'Personas autorizadas activas: '), `${s.people.active_ready} de ${s.people.total}`),
        s.mode !== 'stopped' ? h('div', { class: 'muted small' }, `En directo: ${s.live.persons} persona(s), ${s.live.faces} rostro(s)` + (s.live.person_ms !== undefined ? ` · detector ${s.live.person_ms} ms, caras ${s.live.face_ms} ms` : '')) : null,
        h('div', { class: 'muted small' }, `Dispositivo: ${s.device_name}`)));
      bMute.textContent = s.alarm.muted ? 'Reactivar alarma' : 'Silenciar alarma 30 min';
      if (s.mode === 'test') {
        testEvents.classList.remove('hidden');
        const ev = s.test_events.slice().reverse();
        testEvents.replaceChildren(h('h2', {}, 'Modo prueba: lo que habría ocurrido'),
          ev.length ? h('ul', {}, ev.map((e) => h('li', {}, badge(STATUS_ES[e.status] || e.status, e.status), ' ', ({ would_alert: 'habría sonado la alarma y enviado notificación', would_log: 'se habría registrado', ignored: 'ignorado por la política', reclassified: 'reclasificado como autorizado' })[e.action] || e.action)))
            : h('p', { class: 'muted' }, 'Colócate delante de la cámara para ver cómo se clasifica la presencia.'));
      } else testEvents.classList.add('hidden');
    };
    if (state.status) this.onStatus(state.status);
  },
};

// ------------------------------------------------------------------ CÁMARA

views.camara = {
  title: 'Cámara',
  build(root) {
    const pv = livePreview();
    const info = h('div', { class: 'stack' });
    const check = h('div', { class: 'stack' });
    const devs = h('p', { class: 'muted small' }, 'Buscando dispositivos…');
    const form = settingsForm([
      { path: 'camera.device', label: 'Dispositivo', hint: 'Índice (0, 1…) o /dev/videoN', type: 'text' },
      { path: 'camera.backend', label: 'Controlador', type: 'select', options: [['auto', 'Automático'], ['opencv', 'USB / V4L2 (OpenCV)'], ['picamera2', 'Cámara Raspberry Pi (Picamera2)']] },
      { path: 'camera.width', label: 'Ancho (px)', type: 'number', min: 160, max: 1920, int: true },
      { path: 'camera.height', label: 'Alto (px)', type: 'number', min: 120, max: 1080, int: true },
      { path: 'camera.capture_fps', label: 'Captura (fps)', type: 'number', min: 1, max: 30, step: 1, hint: 'Menos fps = menos CPU' },
      { path: 'detection.analysis_fps', label: 'Análisis (por segundo)', type: 'number', min: 0.1, max: 10, step: 0.1, hint: 'En una Raspberry Pi 4/5 empieza con 1-2' },
      { path: 'detection.input_size', label: 'Resolución del detector', type: 'select', num: true, int: true, options: [[320, '320 (rápido)'], [416, '416 (equilibrado)'], [512, '512'], [640, '640 (preciso, lento)']] },
      { path: 'detection.person_confidence', label: 'Confianza mínima de persona', type: 'range', min: 0.2, max: 0.9, step: 0.05 },
      { path: 'detection.motion_gate', label: 'Analizar solo con movimiento (ahorra CPU)', type: 'checkbox', hint: 'Con la escena quieta se analiza cada pocos segundos' },
    ]);
    root.append(h('div', { class: 'grid' },
      h('section', { class: 'card' }, h('h2', {}, 'Vista previa'), previewToggle(pv), pv.el,
        h('p', { class: 'muted small' }, 'Las imágenes se muestran en directo y no se guardan. En modo vigilancia la vista previa dibuja lo que se detecta.')),
      h('section', { class: 'card' }, h('h2', {}, 'Estado'), info,
        h('div', { class: 'row' }, h('button', { onclick: async () => { check.replaceChildren('Comprobando…'); const r = await act(() => api('POST', '/api/camera/check')); if (r) renderCheck(r); else check.replaceChildren(); } }, 'Comprobar cámara y detección')), check, devs),
      h('section', { class: 'card' }, h('h2', {}, 'Ajustes de cámara y análisis'), form,
        h('p', { class: 'muted small' }, 'Cambiar dispositivo o resolución reinicia la captura automáticamente.'))));
    function renderCheck(r) {
      check.replaceChildren(h('ul', {},
        h('li', {}, `Resolución recibida: ${r.resolution} · brillo ${r.brightness} · nitidez ${r.sharpness}`),
        h('li', {}, r.person_detector.ok ? `Detector de personas: OK, ${r.person_detector.persons} persona(s) en ${r.person_detector.ms} ms` : `Detector de personas: ERROR ${r.person_detector.error}`),
        h('li', {}, r.faces.ok ? `Rostros: ${r.faces.count} en ${r.faces.ms} ms` : `Reconocimiento facial: ERROR ${r.faces.error}`),
        ...r.hints.map((t) => h('li', { class: 'error' }, t))));
    }
    api('GET', '/api/camera/devices').then((d) => {
      devs.textContent = d.devices.length ? 'Dispositivos detectados: ' + d.devices.map((x) => x.label).join(', ') : 'No se detecta ninguna cámara (/dev/video*). Conecta una cámara USB o activa la cámara de Raspberry Pi.';
    }).catch(() => {});
    this.onStatus = (s) => {
      const c = s.components.camera;
      info.replaceChildren(h('div', {}, h('span', { class: 'dot ' + c.state }), ' ', badge(STATE_ES[c.state], c.state), ' ', c.message),
        h('div', { class: 'muted small' }, `Resolución ${c.resolution} · ${c.fps} fps reales · ${c.frames_total} fotogramas · ${c.invalid_frames} inválidos · ${c.reconnects} reconexiones`));
    };
    if (state.status) this.onStatus(state.status);
  },
};

// ------------------------------------------------------------------ PERSONAS AUTORIZADAS

const POSE_STEPS = [
  { pose: 'frontal', text: 'Mira de frente a la cámara' },
  { pose: 'frontal', text: 'De frente, expresión neutra' },
  { pose: 'lateral', text: 'Gira ligeramente la cabeza a un lado' },
  { pose: 'lateral', text: 'Gira ligeramente la cabeza al otro lado' },
  { pose: 'any', text: 'Cambia la luz (otra lámpara o ventana) y mira de frente' },
  { pose: 'any', text: 'Sonríe o cambia la expresión' },
];

views.personas = {
  title: 'Personas autorizadas',
  async build(root) {
    const data = await act(() => api('GET', '/api/people'));
    if (!data) return;
    const people = data.people;
    const pv = livePreview();
    let selected = people.length ? people[0].id : null;
    let session = 0;
    let testTimer = null;
    state.cleanup.push(() => clearInterval(testTimer));
    const reload = () => go('personas');

    // --- bienvenida
    if (!people.length) {
      const nameIn = h('input', { type: 'text', value: 'Propietario', maxlength: 40 });
      root.append(h('section', { class: 'card warn' }, h('h2', {}, 'Bienvenido a Pi Sentinel: registra tu rostro'),
        h('p', {}, 'Para distinguir a las personas desconocidas, primero hay que enseñar al sistema cómo eres. Se guardan solo representaciones numéricas (embeddings) en esta Raspberry Pi; no se guardan fotos ni se envía nada fuera.'),
        h('p', { class: 'note' }, 'Registrar más muestras mejora el reconocimiento, pero no garantiza una identificación perfecta: puede haber errores en ambos sentidos.'),
        field('Nombre del perfil', nameIn),
        h('div', { class: 'row' }, h('button', { class: 'primary', onclick: async () => { const r = await act(() => api('POST', '/api/people', { name: nameIn.value })); if (r) reload(); } }, 'Crear perfil y empezar'))));
    }

    // --- lista de perfiles
    const buildList = () => h('div', { class: 'table-wrap' }, h('table', {},
      h('thead', {}, h('tr', {}, ['Nombre', 'Estado', 'Muestras', 'Acciones'].map((t) => h('th', {}, t)))),
      h('tbody', {}, people.map((p) => h('tr', {},
        h('td', {}, p.name),
        h('td', {}, !p.enabled ? badge('Desactivado', 'warn') : p.ready ? badge('Activo', 'ok') : badge(`Incompleto (mín. ${data.min_samples})`, 'warn')),
        h('td', {}, String(p.sample_count)),
        h('td', {}, h('div', { class: 'row' },
          h('button', { onclick: () => { selected = p.id; renderSamples(); sel.value = p.id; } }, 'Muestras'),
          h('button', { onclick: async () => { await act(() => api('PATCH', `/api/people/${p.id}`, { enabled: !p.enabled }), p.enabled ? 'Perfil desactivado' : 'Perfil activado'); reload(); } }, p.enabled ? 'Desactivar' : 'Activar'),
          h('button', { onclick: async () => { const n = prompt('Nuevo nombre', p.name); if (n) { await act(() => api('PATCH', `/api/people/${p.id}`, { name: n }), 'Renombrado'); reload(); } } }, 'Renombrar'),
          h('button', { class: 'danger', onclick: async () => { if (confirm(`¿Eliminar a ${p.name} y todas sus muestras? No se puede deshacer.`)) { await act(() => api('DELETE', `/api/people/${p.id}`), 'Perfil eliminado'); reload(); } } }, 'Eliminar'))))))));
    const listHost = h('div', {}, people.length ? buildList() : h('p', { class: 'muted' }, 'Aún no hay perfiles.'));
    async function refreshList() { const d = await api('GET', '/api/people'); people.splice(0, people.length, ...d.people); listHost.replaceChildren(buildList()); }
    const addIn = h('input', { type: 'text', placeholder: 'Nombre de otra persona autorizada', maxlength: 40 });
    root.append(h('section', { class: 'card' }, h('h2', {}, 'Personas autorizadas'), listHost,
      h('div', { class: 'row' }, addIn, h('button', { onclick: async () => { if (!addIn.value.trim()) return; const r = await act(() => api('POST', '/api/people', { name: addIn.value }), 'Perfil creado'); if (r) reload(); } }, 'Añadir persona'))));

    if (!people.length) return;

    // --- registro de muestras
    const sel = h('select', {}, people.map((p) => h('option', { value: p.id }, p.name)));
    sel.addEventListener('change', () => { selected = parseInt(sel.value, 10); session = 0; renderSamples(); renderSteps(); });
    const stepsEl = h('ul', { class: 'steps' });
    const instr = h('p', { class: 'note' });
    const capMsg = h('p', { class: 'muted' });
    const samplesEl = h('div', { class: 'samples' });
    const countEl = h('p', { class: 'muted small' });
    function renderSteps() {
      stepsEl.replaceChildren(...POSE_STEPS.map((st, i) => h('li', { class: i < session ? 'done' : i === session ? 'current' : '' }, `${i + 1}. ${st.text}`)));
      instr.textContent = session < POSE_STEPS.length ? 'Siguiente: ' + POSE_STEPS[session].text : 'Pasos completados. Puedes añadir más muestras con distinta luz o postura, o probar el reconocimiento.';
    }
    async function renderSamples() {
      const r = await act(() => api('GET', `/api/people/${selected}/samples`));
      if (!r) return;
      const p = people.find((x) => x.id === selected);
      countEl.textContent = `${r.samples.length} muestra(s). Recomendado: 8-10 con distinta luz y ángulo (mínimo ${data.min_samples}).`;
      samplesEl.replaceChildren(...r.samples.map((s, i) => h('span', { class: 'sample', title: fmtTime(s.created_at) }, `#${i + 1} · ${s.pose} · calidad ${Math.round(s.quality * 100)}%`,
        h('button', { title: 'Eliminar muestra', onclick: async () => { await act(() => api('DELETE', `/api/people/${selected}/samples/${s.id}`)); renderSamples(); refreshList(); } }, '✕'))));
    }
    const capBtn = h('button', { class: 'primary', onclick: async () => {
      capBtn.disabled = true; capMsg.textContent = 'Capturando…';
      const pose = session < POSE_STEPS.length ? POSE_STEPS[session].pose : 'any';
      const r = await act(() => api('POST', `/api/people/${selected}/samples/capture`, { pose }));
      capBtn.disabled = false;
      if (r && r.ok) { session++; capMsg.textContent = `Muestra guardada (${r.samples}).` + (r.ready ? ' El perfil ya es utilizable.' : ''); capMsg.className = 'okmsg'; renderSteps(); renderSamples(); refreshList(); }
      else if (r) { capMsg.textContent = r.reason; capMsg.className = 'error'; }
    } }, 'Capturar muestra');
    root.append(h('section', { class: 'card' }, h('h2', {}, 'Registro de muestras faciales'),
      h('div', { class: 'grid' },
        h('div', { class: 'stack' }, previewToggle(pv), pv.el),
        h('div', { class: 'stack' }, field('Perfil', sel), stepsEl, instr, h('div', { class: 'row' }, capBtn), capMsg, h('h3', {}, 'Muestras guardadas'), samplesEl, countEl,
          h('p', { class: 'muted small' }, 'Una muestra se rechaza si la cara es pequeña, está borrosa, girada, cortada, mal iluminada o hay más de una persona. Se guarda solo el embedding, no la foto.')))));
    renderSteps(); renderSamples();

    // --- prueba en directo
    const out = h('div', { class: 'stack' });
    const cont = h('input', { type: 'checkbox' });
    async function runTest() {
      const r = await act(() => api('POST', '/api/recognition/test'));
      if (!r) { clearInterval(testTimer); cont.checked = false; return; }
      if (!r.profiles_active) { out.replaceChildren(h('p', { class: 'error' }, 'Ningún perfil activo con muestras suficientes: no se puede comparar.')); return; }
      if (!r.faces.length) { out.replaceChildren(h('p', { class: 'muted' }, 'No se detecta ninguna cara en la imagen.')); return; }
      out.replaceChildren(...r.faces.map((f) => h('div', { class: 'card' },
        badge(STATUS_ES[f.status] || f.status, f.status), ' ', f.name ? h('strong', {}, f.name) : null,
        f.scores ? h('div', { class: 'mono' }, Object.entries(f.scores).map(([n, v]) => `${n}: similitud ${v.toFixed(3)} (umbral ${r.threshold})`).join(' · ')) : null,
        !f.quality_ok ? h('div', { class: 'error' }, 'No verificable: ' + f.reasons.join('; ')) : h('div', { class: 'muted small' }, f.status === 'UNKNOWN' ? 'Rostro válido que no alcanza el umbral con ningún perfil.' : 'Coincide con un perfil.'))));
    }
    cont.addEventListener('change', () => { clearInterval(testTimer); if (cont.checked) { pv.start(); runTest(); testTimer = setInterval(runTest, 1800); } });
    root.append(h('section', { class: 'card' }, h('h2', {}, 'Probar el reconocimiento'),
      h('p', { class: 'muted' }, 'Comprueba en directo si el sistema te reconoce antes de activar la vigilancia. La «similitud» es una puntuación de parecido entre representaciones faciales, no una probabilidad de identidad.'),
      h('div', { class: 'row' }, h('button', { onclick: runTest }, 'Probar ahora'), h('label', { class: 'inline' }, cont, 'Prueba continua')), out));

    // --- umbral y calibración
    const cal = data.calibration;
    const calOut = h('div', { class: 'stack' });
    function showCal(c) {
      const fmt = (x) => (x ? `n=${x.n}, mín ${x.min}, p10 ${x.p10}, media ${x.mean}, máx ${x.max}` : 'sin medir');
      calOut.replaceChildren(...nn(h('div', {}, h('strong', {}, 'Tu rostro: '), fmt(c.genuine)), h('div', {}, h('strong', {}, 'Persona no registrada: '), fmt(c.impostor)),
        c.advice ? h('p', { class: 'note' }, c.advice) : null,
        c.suggested_threshold ? h('div', { class: 'row' }, h('span', {}, `Umbral sugerido: ${c.suggested_threshold} (actual ${c.current_threshold})`), h('button', { onclick: async () => { await act(() => api('PUT', '/api/settings', { face: { similarity_threshold: c.suggested_threshold } }), 'Umbral aplicado'); reload(); } }, 'Aplicar sugerido')) : null));
    }
    showCal(cal);
    async function measure(kind) { toast('Mide durante unos segundos: colócate frente a la cámara…'); const r = await act(() => api('POST', '/api/recognition/calibrate', { kind, frames: 12 })); if (r) showCal(r); }
    root.append(h('section', { class: 'card' }, h('h2', {}, 'Umbral y calibración'),
      settingsForm([
        { path: 'face.similarity_threshold', label: 'Umbral de similitud', type: 'range', min: 0.2, max: 0.8, step: 0.01, hint: 'Más alto = más estricto: menos desconocidos aceptados por error, pero más falsas alarmas contigo. Valor de referencia de SFace: 0.363.' },
        { path: 'face.min_face_px', label: 'Tamaño mínimo de rostro (px)', type: 'number', min: 24, max: 400, int: true, hint: 'Rostros menores → «no verificable»' },
        { path: 'face.min_sharpness', label: 'Nitidez mínima', type: 'number', min: 0, max: 500, step: 1 },
        { path: 'face.max_yaw', label: 'Giro máximo del rostro', type: 'number', min: 0.1, max: 1.5, step: 0.05 },
        { path: 'face.min_samples', label: 'Muestras mínimas por perfil', type: 'number', min: 1, max: 50, int: true },
      ], reload),
      h('h3', {}, 'Calibración asistida'),
      h('p', { class: 'muted small' }, '1) Mide tu rostro. 2) Si puedes, mide a alguien NO registrado. Si ambas puntuaciones se separan se sugiere un umbral intermedio. Sin separación clara, mejora luz, ángulo y muestras: ningún umbral elimina los errores por completo.'),
      h('div', { class: 'row' }, h('button', { onclick: () => measure('genuine') }, 'Medir mi rostro'), h('button', { onclick: () => measure('impostor') }, 'Medir persona no registrada')), calOut));

    // --- explicación y borrado
    root.append(h('section', { class: 'card' }, h('h2', {}, 'Cómo se interpretan los resultados'),
      h('ul', {}, h('li', {}, h('strong', {}, 'Autorizada: '), 'el rostro supera el umbral con un perfil activo. No suena la alarma.'),
        h('li', {}, h('strong', {}, 'Desconocida: '), 'rostro de calidad suficiente que no alcanza el umbral con ningún perfil. Se confirma tras varias observaciones y suena la alarma.'),
        h('li', {}, h('strong', {}, 'No verificable: '), 'hay una persona, pero no se ve un rostro apto (lejos, de espaldas, borroso, tapado…). No se considera desconocida automáticamente; su política es configurable en Alarmas.')),
      h('p', { class: 'note' }, 'El reconocimiento facial puede equivocarse: con poca luz, gafas, mascarilla, ángulos extremos o personas parecidas. Una foto o pantalla mostrada a la cámara también podría confundirlo (no hay detección de vida).')));
    const delIn = h('input', { type: 'text', placeholder: 'Escribe ELIMINAR' });
    root.append(h('section', { class: 'card danger' }, h('h2', {}, 'Datos biométricos'),
      h('p', {}, 'Elimina todos los perfiles y muestras faciales y detiene la vigilancia.'),
      h('div', { class: 'row' }, delIn, h('button', { class: 'danger', onclick: async () => { const r = await act(() => api('POST', '/api/biometrics/delete-all', { confirm: delIn.value })); if (r) { toast(`Eliminados ${r.deleted_profiles} perfil(es)`); reload(); } } }, 'Eliminar todo'))));
  },
};

// ------------------------------------------------------------------ ALARMAS

views.alarmas = {
  title: 'Alarmas',
  async build(root) {
    const al = await act(() => api('GET', '/api/alarm'));
    if (!al) return;
    const reload = () => go('alarmas');
    const soundOptions = al.sounds.map((s) => [s.name, s.name === 'default' ? 'Sirena integrada' : s.name === 'entrada.wav' ? 'Timbre de entrada (integrado)' : s.builtin ? `Integrado: ${s.name}` : s.name]);
    const statusEl = h('div', { class: 'stack' });
    const st = al.status;
    statusEl.append(...nn(h('div', {}, badge(st.audio_available ? 'Audio disponible' : 'Sin audio', st.audio_available ? 'ok' : 'error'), ' ', st.player ? `Reproductor: ${st.player}` : ''),
      st.last_error ? h('p', { class: 'error' }, st.last_error) : null,
      h('p', { class: 'muted small' }, 'Altavoz Bluetooth: conéctalo en el sistema y ponlo como salida predeterminada. Si no suena, ejecuta en la Pi: python -m app.main audio-test'), st.muted ? h('p', { class: 'note' }, 'La alarma está silenciada temporalmente.') : null));
    const up = h('input', { type: 'file', accept: '.wav,audio/wav' });
    root.append(h('div', { class: 'grid' },
      h('section', { class: 'card' }, h('h2', {}, 'Alarma sonora'), statusEl,
        h('div', { class: 'row' },
          h('button', { onclick: async () => { const r = await act(() => api('POST', '/api/alarm/test')); if (r && r.state !== 'TRIGGERED') toast(r.error || ALARM_ES[r.state] || r.state, true); else if (r) toast('Sonando…'); } }, 'Probar'),
          h('button', { onclick: () => act(() => api('POST', '/api/alarm/stop'), 'Parada') }, 'Detener sonido'),
          h('button', { onclick: async () => { await act(() => api('POST', '/api/alarm/mute', { minutes: st.muted ? null : 30 }), st.muted ? 'Reactivada' : 'Silenciada 30 min'); reload(); } }, st.muted ? 'Reactivar' : 'Silenciar 30 min')),
        h('h3', {}, 'Sonido propio'),
        h('p', { class: 'muted small' }, 'WAV PCM de 16 bits, máx. 8 MB y 60 s. Funciona sin Internet.'),
        h('div', { class: 'row' }, up, h('button', { onclick: async () => {
          const f = up.files[0]; if (!f) return toast('Elige un fichero .wav', true);
          const r = await act(() => api('POST', `/api/alarm/sounds?name=${encodeURIComponent(f.name)}`, f, { raw: true, contentType: 'audio/wav' }), 'Sonido añadido'); if (r) reload();
        } }, 'Subir')),
        al.sounds.filter((s) => !s.builtin).map((s) => h('div', { class: 'row' }, s.name, h('button', { class: 'ghost', onclick: async () => { await act(() => api('DELETE', `/api/alarm/sounds/${encodeURIComponent(s.name)}`), 'Eliminado'); reload(); } }, 'Eliminar')))),
      h('section', { class: 'card' }, h('h2', {}, 'Sonido y límites'),
        settingsForm([
          { path: 'alarm.enabled', label: 'Alarma sonora activada', type: 'checkbox', hint: 'Desactívala para vigilar en silencio (las notificaciones siguen)' },
          { path: 'alarm.sound', label: 'Sonido', type: 'select', options: soundOptions },
          { path: 'alarm.volume', label: 'Volumen', type: 'range', min: 0, max: 1, step: 0.05 },
          { path: 'alarm.duration_seconds', label: 'Duración máxima (s)', type: 'number', min: 1, max: 300, step: 1 },
          { path: 'alarm.repetitions', label: 'Repeticiones', type: 'number', min: 1, max: 50, int: true },
          { path: 'alarm.min_interval_seconds', label: 'Tiempo mínimo entre alarmas (s)', type: 'number', min: 0, max: 3600, step: 1 },
          { path: 'alarm.player', label: 'Reproductor de audio', type: 'select', options: [['auto', 'Automático (PipeWire → PulseAudio → ALSA)'], ['pw-play', 'pw-play (PipeWire)'], ['paplay', 'paplay (PulseAudio)'], ['aplay', 'aplay (ALSA directo)']], hint: 'Para altavoz Bluetooth usa pw-play o paplay. Se aplica también al sonido de entrada.' },
          { path: 'alarm.audio_device', label: 'Salida de audio (opcional)', type: 'text', hint: 'Vacío = la salida predeterminada del sistema. Ej. bluez_output.XX_XX… (pw-play/paplay) o plughw:1,0 (aplay). Ver «audio-test».' },
          { path: 'alarm.lead_in_seconds', label: 'Silencio previo (s)', type: 'number', min: 0, max: 3, step: 0.1, hint: 'Los altavoces Bluetooth tardan en «despertar» y se comen el inicio del sonido' },
        ])),
      h('section', { class: 'card' }, h('h2', {}, 'Sonido de entrada'),
        h('p', { class: 'muted small' }, 'Un sonido breve en la Raspberry Pi cuando se confirma que alguien ha entrado. Es independiente de la alarma y de las notificaciones; si suena la alarma de desconocido, no se superpone. Elige uno de los incluidos o sube el tuyo desde tu dispositivo (arriba, «Sonido propio»).'),
        h('div', { class: 'row' }, h('button', { onclick: async () => { const r = await act(() => api('POST', '/api/alarm/chime-test')); if (r && r.state !== 'TRIGGERED') toast(r.error || ALARM_ES[r.state] || r.state, true); else if (r) toast('Sonando…'); } }, 'Probar sonido de entrada')),
        settingsForm([
          { path: 'entry.enabled', label: 'Sonido de entrada activado', type: 'checkbox' },
          { path: 'entry.sound', label: 'Sonido', type: 'select', options: soundOptions },
          { path: 'entry.volume', label: 'Volumen', type: 'range', min: 0, max: 1, step: 0.05 },
          { path: 'entry.duration_seconds', label: 'Duración máxima (s)', type: 'number', min: 1, max: 300, step: 1 },
          { path: 'entry.repetitions', label: 'Repeticiones', type: 'number', min: 1, max: 50, int: true },
          { path: 'entry.min_interval_seconds', label: 'Tiempo mínimo entre sonidos (s)', type: 'number', min: 0, max: 3600, step: 1 },
          { path: 'entry.on_authorized', label: 'Cuando entra una persona autorizada', type: 'checkbox' },
          { path: 'entry.on_unknown', label: 'Cuando entra una persona desconocida (si la alarma está desactivada o silenciada)', type: 'checkbox' },
          { path: 'entry.on_unverified', label: 'Cuando entra una persona no verificable', type: 'checkbox' },
        ])),
      h('section', { class: 'card' }, h('h2', {}, 'Confirmación y política de alertas'),
        settingsForm([
          { path: 'tracking.confirm_seconds', label: 'Tiempo de confirmación (s)', type: 'number', min: 0, max: 60, step: 0.5, hint: 'Presencia mínima antes de dar un desconocido por confirmado' },
          { path: 'tracking.min_observations', label: 'Observaciones mínimas', type: 'number', min: 1, max: 50, int: true },
          { path: 'tracking.exit_seconds', label: 'Ausencia para considerar que salió (s)', type: 'number', min: 1, max: 600, step: 1 },
          { path: 'tracking.alert_cooldown_seconds', label: 'Tiempo entre alertas (s)', type: 'number', min: 0, max: 3600, step: 1, hint: 'Los eventos dentro de este margen se registran sin volver a alertar' },
          { path: 'tracking.unverified_timeout_seconds', label: 'Espera antes de «no verificable» (s)', type: 'number', min: 1, max: 600, step: 1 },
          { path: 'policy.unknown', label: 'Persona desconocida', type: 'select', options: [['alert', 'Alertar (alarma + notificación)'], ['log', 'Solo registrar'], ['ignore', 'Ignorar']] },
          { path: 'policy.unverified', label: 'Persona no verificable', type: 'select', options: [['log', 'Solo registrar (recomendado)'], ['alert', 'Alertar (modo estricto)'], ['ignore', 'Ignorar']], hint: 'El modo estricto puede dar falsas alarmas cuando alguien está de espaldas o lejos' },
          { path: 'policy.authorized', label: 'Persona autorizada', type: 'select', options: [['ignore', 'Ignorar (no se guarda nada)'], ['log', 'Registrar presencia']] },
        ]))));
  },
};

// ------------------------------------------------------------------ NOTIFICACIONES

views.notificaciones = {
  title: 'Notificaciones',
  async build(root) {
    const n = await act(() => api('GET', '/api/notifications'));
    if (!n) return;
    const reload = () => go('notificaciones');
    const topicBox = h('div', { class: 'mono' }, state.settings.ntfy.topic_set ? state.settings.ntfy.topic_masked : 'sin configurar');
    const subBox = h('p', { class: 'small muted' });
    const tokIn = h('input', { type: 'password', placeholder: n.status.token_set ? `Guardado: ${n.status.token_hint}` : 'Token (opcional)', autocomplete: 'off' });
    const s = n.status;
    const stateCls = { ok: 'ok', idle: 'ok', disabled: 'warn', unconfigured: 'warn', error: 'error' }[s.state];
    root.append(h('div', { class: 'grid' },
      h('section', { class: 'card' }, h('h2', {}, 'Estado de ntfy'),
        h('div', {}, badge(s.state === 'ok' ? 'Conectado' : s.state === 'error' ? 'Error' : s.state === 'disabled' ? 'Desactivado' : s.state === 'unconfigured' ? 'Sin configurar' : 'Listo', stateCls), ' ', s.message),
        h('p', { class: 'small' }, `Pendientes: ${s.pending} · Enviadas: ${s.sent} · Fallidas: ${s.failed}`),
        h('p', { class: 'small muted' }, `Último envío correcto: ${fmtTime(s.last_success)}`), s.last_error ? h('p', { class: 'error small' }, s.last_error) : null,
        h('div', { class: 'row' },
          h('button', { class: 'primary', onclick: async () => { const r = await act(() => api('POST', '/api/notifications/test')); if (r) { toast(r.ok ? 'Notificación de prueba enviada' : r.error, !r.ok); reload(); } } }, 'Enviar notificación de prueba'),
          h('button', { onclick: async () => { const r = await act(() => api('POST', '/api/notifications/retry-failed')); if (r) { toast(`${r.requeued} reencoladas`); reload(); } } }, 'Reintentar fallidas')),
        h('p', { class: 'muted small' }, 'La entrega depende del móvil: ahorro de batería, permisos de notificación, prioridad y conectividad. Una notificación no equivale a una llamada ni está garantizada al instante.')),
      h('section', { class: 'card' }, h('h2', {}, 'Configuración'),
        settingsForm([
          { path: 'ntfy.enabled', label: 'Enviar notificaciones push', type: 'checkbox' },
          { path: 'ntfy.server', label: 'URL del servidor', type: 'text', hint: 'https://ntfy.sh (público) o tu servidor privado' },
          { path: 'ntfy.priority', label: 'Prioridad', type: 'select', num: true, int: true, options: [[1, '1 · mínima'], [2, '2 · baja'], [3, '3 · normal'], [4, '4 · alta'], [5, '5 · urgente']] },
          { path: 'ntfy.max_attempts', label: 'Reintentos máximos', type: 'number', min: 1, max: 100, int: true },
          { path: 'ntfy.max_age_hours', label: 'Descartar pendientes tras (horas)', type: 'number', min: 0.1, max: 720, step: 0.5 },
          { path: 'ntfy.queue_max', label: 'Máximo de pendientes', type: 'number', min: 1, max: 5000, int: true },
        ], reload),
        h('h3', {}, 'Tópico'),
        topicBox,
        h('div', { class: 'row' },
          h('button', { onclick: async () => { const r = await act(() => api('GET', '/api/notifications/topic')); if (r) { topicBox.textContent = r.topic || 'sin configurar'; subBox.textContent = r.topic ? `En la app ntfy del móvil: «Suscribirse a un tema» → ${r.topic}` + (r.subscribe_url.startsWith('https://ntfy.sh') ? '' : ` (servidor: ${r.subscribe_url.replace(/\/[^/]+$/, '')})`) : ''; } } }, 'Mostrar'),
          h('button', { onclick: async () => { if (state.settings.ntfy.topic_set && !confirm('Se generará un tópico nuevo y habrás de volver a suscribir el móvil. ¿Continuar?')) return; const r = await act(() => api('POST', '/api/notifications/generate-topic'), 'Tópico nuevo generado'); if (r) { const s2 = await api('GET', '/api/settings'); state.settings = s2; topicBox.textContent = r.topic; subBox.textContent = 'Suscribe el móvil a este tópico. No lo compartas ni lo publiques.'; } } }, 'Generar tópico aleatorio')),
        subBox,
        h('p', { class: 'note small' }, 'Un tópico aleatorio es difícil de adivinar, pero NO es autenticación: con el servidor público, quien lo conozca puede leer tus avisos. Para mayor protección usa un servidor ntfy privado con token.'),
        h('h3', {}, 'Token (servidores con autenticación)'),
        h('div', { class: 'row' }, tokIn,
          h('button', { onclick: async () => { if (!tokIn.value.trim()) return toast('Escribe un token', true); const r = await act(() => api('PUT', '/api/notifications/token', { token: tokIn.value.trim() }), 'Token guardado'); if (r) reload(); } }, 'Guardar'),
          h('button', { class: 'ghost', onclick: async () => { await act(() => api('PUT', '/api/notifications/token', { token: null }), 'Token eliminado'); reload(); } }, 'Quitar')),
        h('p', { class: 'muted small' }, 'El token se guarda con permisos restrictivos y nunca se vuelve a mostrar completo.')),
      h('section', { class: 'card' }, h('h2', {}, 'Historial de envíos'),
        n.history.length ? h('div', { class: 'table-wrap' }, h('table', {}, h('thead', {}, h('tr', {}, ['Fecha', 'Tipo', 'Estado', 'Intentos', 'Detalle'].map((t) => h('th', {}, t)))),
          h('tbody', {}, n.history.map((r) => h('tr', {}, h('td', {}, fmtTime(r.created_at)), h('td', {}, r.kind === 'test' ? 'prueba' : 'alerta'), h('td', {}, badge(NOTIF_ES[r.status] || r.status, r.status)), h('td', {}, String(r.attempts)), h('td', { class: 'small' }, r.last_error || (r.status === 'PENDING' ? `próximo intento ${fmtTime(r.next_attempt_at)}` : ''))))))) : h('p', { class: 'muted' }, 'Sin envíos todavía.'),
        h('div', { class: 'row' }, h('button', { class: 'ghost', onclick: async () => { await act(() => api('DELETE', '/api/notifications/history'), 'Historial de envíos borrado'); reload(); } }, 'Borrar historial de envíos')))));
  },
};

// ------------------------------------------------------------------ HISTORIAL

views.historial = {
  title: 'Historial',
  async build(root) {
    const r = await act(() => api('GET', '/api/events?limit=100'));
    if (!r) return;
    const reload = () => go('historial');
    root.append(h('section', { class: 'card' }, h('h2', {}, 'Eventos'),
      h('p', { class: 'muted small' }, 'Solo se guardan metadatos. No se almacenan fotografías ni vídeo de las personas detectadas.'),
      r.events.length ? h('div', { class: 'table-wrap' }, h('table', {}, h('thead', {}, h('tr', {}, ['Fecha y hora', 'Identificador', 'Tipo', 'Reconocimiento', 'Alarma', 'Notificación', 'Detalle'].map((t) => h('th', {}, t)))),
        h('tbody', {}, r.events.map((e) => h('tr', {}, h('td', {}, fmtTime(e.started_at)), h('td', { class: 'mono' }, e.id), h('td', {}, 'Presencia'),
          h('td', {}, badge(STATUS_ES[e.identity_status] || e.identity_status, e.identity_status)), h('td', {}, badge(ALARM_ES[e.alarm_state] || e.alarm_state, e.alarm_state)),
          h('td', {}, badge(NOTIF_ES[e.notification] || e.notification, e.notification)), h('td', { class: 'small' }, e.detail || (e.ended_at ? `Duración ≈ ${Math.max(1, Math.round(e.ended_at - e.started_at))} s` : 'En curso'))))))) : h('p', { class: 'muted' }, 'Sin eventos registrados.'),
      h('div', { class: 'row' }, h('button', { onclick: reload }, 'Actualizar'),
        h('button', { class: 'danger', onclick: async () => { if (confirm('¿Borrar todo el historial de eventos?')) { await act(() => api('DELETE', '/api/events'), 'Historial borrado'); reload(); } } }, 'Borrar historial'))),
      h('section', { class: 'card' }, h('h2', {}, 'Retención'),
        settingsForm([{ path: 'storage.history_retention_days', label: 'Conservar el historial (días)', type: 'number', min: 1, max: 3650, int: true }])));
  },
};

// ------------------------------------------------------------------ AJUSTES / SEGURIDAD

views.ajustes = {
  title: 'Ajustes',
  async build(root) {
    const s = state.session;
    const cur = h('input', { type: 'password', autocomplete: 'current-password' });
    const nw = h('input', { type: 'password', autocomplete: 'new-password' });
    const msg = h('p', { class: 'error' });
    root.append(h('div', { class: 'grid' },
      h('section', { class: 'card' }, h('h2', {}, 'General'),
        settingsForm([{ path: 'general.device_name', label: 'Nombre del dispositivo', type: 'text', hint: 'Aparece en las notificaciones. Vacío = nombre del equipo' }])),
      h('section', { class: 'card' }, h('h2', {}, 'Contraseña de administración'),
        h('p', { class: 'muted small' }, s.password_set ? 'Hay una contraseña definida.' : 'Sin contraseña (solo accesible desde este equipo). Es obligatoria para abrir la interfaz a la red local.'),
        s.password_set ? field('Contraseña actual', cur) : null, field('Nueva contraseña (mín. 10 caracteres)', nw), msg,
        h('button', { class: 'primary', onclick: async () => {
          msg.textContent = '';
          try { const r = await api('POST', '/api/password', { current: cur.value, new: nw.value }); state.csrf = r.csrf; state.session.password_set = true; toast('Contraseña guardada'); go('ajustes'); }
          catch (e) { msg.textContent = e.message; }
        } }, 'Guardar contraseña')),
      h('section', { class: 'card' }, h('h2', {}, 'Acceso desde la red (requiere reiniciar)'),
        h('p', { class: 'note small' }, 'Por defecto la interfaz solo escucha en 127.0.0.1. Para abrirla a tu red doméstica necesitas contraseña y habilitarlo aquí. No la expongas a Internet. Para HTTPS usa un proxy inverso o define PI_SENTINEL_TLS_CERT/KEY.'),
        settingsForm([
          { path: 'web.allow_lan', label: 'Permitir acceso desde la red local', type: 'checkbox' },
          { path: 'web.host', label: 'Dirección de escucha', type: 'text', hint: '127.0.0.1 (solo local) o 0.0.0.0 (red local)' },
          { path: 'web.port', label: 'Puerto', type: 'number', min: 1024, max: 65535, int: true },
        ])),
      h('section', { class: 'card' }, h('h2', {}, 'Sesión'), h('button', { onclick: doLogout }, 'Cerrar sesión'))));
  },
};

// ------------------------------------------------------------------ navegación y sesión

const NAV = [['panel', 'Panel'], ['camara', 'Cámara'], ['personas', 'Personas'], ['alarmas', 'Alarmas'], ['notificaciones', 'Notificaciones'], ['historial', 'Historial'], ['ajustes', 'Ajustes']];

function renderNav() {
  $('#nav').replaceChildren(...NAV.map(([k, label]) => h('button', { 'aria-current': state.view === k ? 'page' : null, onclick: () => go(k) }, label)));
}

async function go(view) {
  state.cleanup.forEach((fn) => { try { fn(); } catch (e) { /* ya detenido */ } });
  state.cleanup = [];
  state.view = view;
  history.replaceState(null, '', '#' + view);
  renderNav();
  const root = $('#view');
  root.replaceChildren();
  const v = views[view];
  v.onStatus = null;
  document.title = `${v.title} · Pi Sentinel`;
  try { await v.build.call(v, root); } catch (e) { root.append(h('p', { class: 'error' }, e.message)); }
  if (state.status && v.onStatus) v.onStatus(state.status);
}

async function refreshStatus() {
  try {
    const s = await api('GET', '/api/status');
    state.status = s;
    const pill = $('#mode-pill');
    pill.textContent = MODE_ES[s.mode]; pill.className = 'pill ' + s.mode;
    $('#device-name').textContent = s.device_name;
    const v = views[state.view];
    if (v && v.onStatus) v.onStatus(s);
  } catch (e) { /* se reintenta en el siguiente ciclo */ }
}

function showLogin() { $('#login').classList.remove('hidden'); $('#login-pw').focus(); }

async function doLogout() {
  await act(() => api('POST', '/api/logout'));
  location.reload();
}

$('#login-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  $('#login-err').textContent = '';
  try {
    const r = await fetch('/api/login', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': state.csrf }, body: JSON.stringify({ password: $('#login-pw').value }) });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) { $('#login-err').textContent = errMessage(data, r.status); return; }
    state.csrf = data.csrf; $('#login-pw').value = '';
    $('#login').classList.add('hidden');
    await start();
  } catch (e) { $('#login-err').textContent = 'No se pudo contactar con el servicio.'; }
});

async function start() {
  const r = await fetch('/api/session', { credentials: 'same-origin' });
  state.session = await r.json();
  state.csrf = state.session.csrf;
  if (state.session.auth_required && !state.session.authenticated) { showLogin(); return; }
  $('#logout').classList.toggle('hidden', !state.session.auth_required);
  state.settings = await api('GET', '/api/settings');
  await refreshStatus();
  const wanted = location.hash.slice(1);
  const first = state.session.first_run && !wanted ? 'personas' : null;
  await go(views[wanted] ? wanted : first || 'panel');
  if (!window.__poll) window.__poll = setInterval(() => { if (!document.hidden) refreshStatus(); }, 2000);
}

$('#logout').addEventListener('click', doLogout);
start().catch((e) => { $('#view').append(h('p', { class: 'error' }, 'Error al iniciar la interfaz: ' + e.message)); });
