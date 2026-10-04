// Haetae simulator — bootstrap. Loads the WASM gate and the policy, builds the
// mission gate, and wires the stage, overlay, director and lab together.
//
// HARD RULE: JavaScript never decides, alters or fakes a safety verdict.
// Every verdict, fired list, clamped action and speed cap shown comes from
// gate.judge() (the real haetae-core compiled to WebAssembly).

import { loadEngine, Gate, policyError } from './js/engine.js';
import { Stage } from './js/stage.js';
import { Overlay, Strip, buildLadder } from './js/overlay.js';
import { Director } from './js/director.js';
import { Lab } from './js/lab.js';
import { EventLog } from './js/log.js';
import { CARDS } from './js/cards.js';

const $ = (id) => document.getElementById(id);
const PREFS_KEY = 'haetae.sim.prefs.v2';
const STAGE_KEY = 'haetae.sim.stage.v1'; // '3d' | '2d' (the viewer's choice in 보기)
const WATCHED_KEY = 'haetae.sim.watched.v3'; // { policy: hash, cards: { num: verdicts } }
const params = new URLSearchParams(location.search);

const store = {
  get(k) { try { return JSON.parse(localStorage.getItem(k)); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
  del(k) { try { localStorage.removeItem(k); } catch { /* ignore */ } },
};

const reduceMq = matchMedia('(prefers-reduced-motion: reduce)');
const darkMq = matchMedia('(prefers-color-scheme: dark)');

const app = {
  missionGate: null,
  policyObj: null,
  policyTextValue: '',
  defaultPolicyText: '',
  modified: false,
  kiosk: params.get('kiosk') === '1',
  prefs: { theme: 'system', reduceSet: null, autoSet: null, get reduced() { return this.reduceSet ?? reduceMq.matches; }, get auto() { return this.autoSet ?? !this.reduced; } },
  watched: new Map(),
  gate() { return app.lab?.active ? app.lab.gate : app.missionGate; },
  policy() { return app.policyObj; },
  policyText() { return app.policyTextValue; },
  policyModified() { return app.modified; },
};

// ───────────────────────── Stage selection (3D diorama or 2D map) ─────────────────────────

/** Probe WebGL on a throwaway canvas (never on #map, which must stay context-free until chosen). */
function probeWebGL() {
  if (params.get('webgl') === '0') return { ok: false, soft: false };
  try {
    const c = document.createElement('canvas');
    const gl = c.getContext('webgl2') || c.getContext('webgl');
    if (!gl) return { ok: false, soft: false };
    let soft = false;
    const ext = gl.getExtension('WEBGL_debug_renderer_info');
    if (ext) soft = /SwiftShader|llvmpipe|Software/i.test(String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) ?? ''));
    gl.getExtension('WEBGL_lose_context')?.loseContext();
    return { ok: true, soft };
  } catch {
    return { ok: false, soft: false };
  }
}

function createStage(kind) {
  const canvas = $('map');
  const wrap = $('map-wrap');
  const three = kind === '3d' && app.Stage3D;
  document.body.classList.toggle('stage-3d', !!three);
  document.body.classList.toggle('stage-2d', !three);
  if (three) {
    try {
      return new app.Stage3D(canvas, wrap, {
        kiosk: app.kiosk,
        onContextLost: () => onContextLost(),
        hint: (key, text) => app.toastOnce(key, text),
      });
    } catch (e) {
      console.warn('Haetae: 3D stage failed, using the 2D map', e);
      freshCanvas();
      document.body.classList.remove('stage-3d');
      document.body.classList.add('stage-2d');
    }
  }
  return new Stage($('map'), wrap);
}

/** Replace #map with a fresh canvas carrying the same id, role, label, tabindex and classes. */
function freshCanvas() {
  const old = $('map');
  const c = document.createElement('canvas');
  for (const a of ['id', 'role', 'aria-label', 'aria-roledescription', 'tabindex', 'class']) {
    const v = old.getAttribute(a);
    if (v != null) c.setAttribute(a, v);
  }
  old.replaceWith(c);
  return c;
}

/** Runtime swap (보기 switch or WebGL context loss). */
app.swapStage = async (kind, { lost = false } = {}) => {
  if (kind === app.stage?.kind) return;
  if (kind === '3d' && !app.Stage3D) {
    try {
      ({ Stage3D: app.Stage3D } = await import('./js/scene3d.js'));
    } catch (e) {
      console.warn('Haetae: could not load the 3D stage', e);
      app.toast('3D 화면을 불러오지 못해 2D 지도로 보여 드려요.');
      syncStageRadio();
      return;
    }
  }
  const old = app.stage;
  old.dispose();
  if (old.kind === '3d' && !lost) old.forceLoss?.();
  freshCanvas();
  app.stage = createStage(kind);
  const st = app.stage;
  app.overlay.setStage(st);
  app.director.stage = st;
  st.bindPointer(app.lab.pointerHandlers());
  app.lab.bindMapKeys();
  st.setPolicy(app.policyObj);
  if (app.lab.active) app.lab.applyCanvasRole();
  app.director.describeMap(app.director.scene.world);
  app.overlay.relayout();
  syncStageRadio();
};

function onContextLost() {
  app.no3d = true; // for the rest of the session
  setTimeout(() => {
    app.swapStage('2d', { lost: true });
    app.toast('3D 화면이 멈춰 2D 지도로 바꿨어요.');
  }, 0);
}

function syncStageRadio() {
  const k = app.stage?.kind ?? '2d';
  const r = document.querySelector(`input[name="stage"][value="${k}"]`);
  if (r) r.checked = true;
  const three = document.querySelector('input[name="stage"][value="3d"]');
  if (three) three.disabled = !app.webgl || !!app.no3d;
}

function fatal(message) {
  window.__haetaeFailed = true;
  const f = $('fatal');
  f.hidden = false;
  f.textContent = `시뮬레이터를 시작하지 못했습니다. ${message} — sim/build.sh로 WASM을 빌드하고 sim/ 폴더를 HTTP로 서빙하세요 (python3 -m http.server -d sim 8000).`;
  document.querySelectorAll('.card-btn, #intro-cta').forEach((b) => { b.disabled = true; });
  $('intro').hidden = true;
}

// ───────────────────────── Preferences ─────────────────────────

function loadPrefs() {
  const p = store.get(PREFS_KEY) ?? {};
  app.prefs.theme = ['light', 'dark'].includes(p.theme) ? p.theme : 'system';
  app.prefs.reduceSet = typeof p.reduce === 'boolean' ? p.reduce : null;
  app.prefs.autoSet = typeof p.auto === 'boolean' ? p.auto : null;
  store.del('haetae.sim.watched.v2'); // older format carried no policy fingerprint
}

/** Short fingerprint of a policy text, so recorded glyphs are only shown under the policy that produced them. */
function policyHash(text) {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) { h ^= text.charCodeAt(i); h = Math.imul(h, 16777619); }
  return (h >>> 0).toString(36);
}

/** Restore watched-card glyphs recorded under the policy now in force (the default one at boot). */
function loadWatched() {
  app.watched.clear();
  const w = store.get(WATCHED_KEY);
  if (!w || typeof w !== 'object' || w.policy !== policyHash(app.policyTextValue) || typeof w.cards !== 'object') return;
  for (const [k, v] of Object.entries(w.cards ?? {})) if (Array.isArray(v)) app.watched.set(Number(k), v);
}

function savePrefs() {
  store.set(PREFS_KEY, { theme: app.prefs.theme, reduce: app.prefs.reduceSet, auto: app.prefs.autoSet });
}

function applyPrefs() {
  const root = document.documentElement;
  if (app.prefs.theme === 'system') root.removeAttribute('data-theme'); else root.dataset.theme = app.prefs.theme;
  root.dataset.reduce = String(app.prefs.reduced);
  document.querySelector(`input[name="theme"][value="${app.prefs.theme}"]`).checked = true;
  $('pref-reduce').checked = app.prefs.reduced;
  $('pref-auto').checked = app.prefs.auto;
  app.stage?.readPalette();
}

app.markWatched = (num, verdicts) => {
  app.watched.set(num, verdicts);
  // Only glyphs from the default policy survive a reload (an edited policy is not persisted).
  if (app.modified) return;
  store.set(WATCHED_KEY, { policy: policyHash(app.policyTextValue), cards: Object.fromEntries(app.watched) });
};

// ───────────────────────── Live region & toasts ─────────────────────────

// One polite message at a time: each stays long enough to be read before the next.
const liveQueue = [];
let liveTimer = 0;
function pumpLive() {
  if (liveTimer || !liveQueue.length) return;
  const text = liveQueue.shift();
  const live = $('live');
  live.textContent = '';
  requestAnimationFrame(() => { live.textContent = text; });
  liveTimer = setTimeout(() => { liveTimer = 0; pumpLive(); }, Math.max(1200, Math.min(4500, text.length * 60)));
}
app.announce = (text) => {
  if (!text) return;
  liveQueue.push(text);
  if (liveQueue.length > 4) liveQueue.splice(0, liveQueue.length - 4);
  pumpLive();
};
/** A new scene started: drop queued messages and let the next one speak at once. */
app.announceClear = () => {
  liveQueue.length = 0;
  clearTimeout(liveTimer);
  liveTimer = 0;
};

let toastTimer = 0;
app.toast = (text) => {
  const t = $('toast');
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 4000);
};
const toasted = new Set();
app.toastOnce = (key, text) => {
  if (toasted.has(key)) return;
  toasted.add(key);
  app.toast(text);
};

// ───────────────────────── Policy ─────────────────────────

/** Rebuild the mission gate. Fail-closed: an invalid policy keeps the old gate. */
app.applyPolicy = (text) => {
  const err = policyError(text);
  if (err) { app.toast(`정책이 거부되었습니다: ${err}`); return false; }
  let g;
  try { g = new Gate(text); } catch (e) { app.toast(e.message); return false; }
  if (app.missionGate && app.missionGate.mode !== 'normal') g.raise(app.missionGate.mode);
  app.missionGate?.free();
  app.missionGate = g;
  app.policyObj = JSON.parse(text);
  app.policyTextValue = text;
  app.stage.setPolicy(app.policyObj);
  const wasDefault = !app.modified;
  app.modified = text !== app.defaultPolicyText;
  $('policy-badge').hidden = !app.modified;
  if (app.director) {
    app.watched.clear();
    store.del(WATCHED_KEY);
    app.director.renderRail();
    const d = app.director;
    const t = app.lab?.active ? app.lab.clock : (d.lastRec?.now ?? d.recs.at(-1)?.now ?? 0);
    app.log.add({ t, kind: 'policy', text: `정책 적용 — 모드 ${g.mode} 유지${wasDefault && app.modified ? ' (정책 수정됨)' : ''}` });
  }
  return true;
};

// ───────────────────────── Sheets ─────────────────────────

let detailOpener = null;
app.openDetail = (section) => {
  const dlg = $('detail');
  detailOpener = document.activeElement;
  $('detail-body').innerHTML = app.director.detailHtml(section);
  if (!dlg.open) dlg.showModal();
  if (section === 'transcript') $('dt-transcript')?.scrollIntoView({ block: 'start' });
};

app.openLab = (tab) => app.lab.open(tab);

app.pickCard = (card) => {
  if (app.lab.active) app.lab.close();
  closeView();
  app.director.startCard(card, { focusStage: true });
};

app.playStream = (text) => {
  const d = app.director;
  d.startCard(CARDS.find((c) => c.stream));
  // Restart the player with the pasted stream instead of the example file.
  d.player?.stop(true);
  d.startStream({ stream: text }, { custom: true });
};

function closeView() {
  $('view-pop').hidden = true;
  $('btn-view').setAttribute('aria-expanded', 'false');
}

// ───────────────────────── Boot ─────────────────────────

async function boot() {
  loadPrefs();
  applyPrefs();
  // Choose the stage before anyone calls getContext on #map.
  const asked = params.get('stage') ?? store.get(STAGE_KEY);
  const probe = probeWebGL();
  app.webgl = probe.ok;
  let kind = '3d';
  let noGlToast = false;
  if (asked === '2d') kind = '2d';
  else if (!probe.ok) { kind = '2d'; noGlToast = true; } else if (probe.soft && asked !== '3d') kind = '2d'; // software renderer: 보기 still offers 3D
  document.body.classList.add(kind === '3d' ? 'stage-3d' : 'stage-2d');
  // three.js is loaded only here, in parallel with the WASM engine.
  const three = kind === '3d'
    ? import('./js/scene3d.js').then((m) => m.Stage3D).catch((e) => { console.warn('Haetae: 3D modules failed, using the 2D map', e); return null; })
    : Promise.resolve(null);
  let version;
  try {
    [{ version }, app.Stage3D] = await Promise.all([loadEngine(), three]);
  } catch (e) {
    fatal(`WASM 판정 엔진을 불러오지 못했습니다: ${e.message}`);
    return;
  }
  if (kind === '3d' && !app.Stage3D) kind = '2d';
  let policyText;
  try {
    const res = await fetch('./examples/policy.json', { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    policyText = await res.text();
  } catch (e) {
    fatal(`예제 정책(examples/policy.json)을 불러오지 못했습니다: ${e.message}`);
    return;
  }
  app.defaultPolicyText = policyText;
  $('intro-foot').textContent = `판정은 전부 실제 Rust 게이트(WebAssembly v${version})가 내립니다.`;

  app.stage = createStage(kind);
  app.overlay = new Overlay(app.stage);
  app.strip = new Strip();
  app.log = new EventLog($('event-log'));
  buildLadder();
  if (!app.applyPolicy(policyText)) { fatal('예제 정책이 거부되었습니다.'); return; }
  loadWatched();
  app.director = new Director(app);
  app.lab = new Lab(app);
  app.stage.bindPointer(app.lab.pointerHandlers());
  app.lab.bindMapKeys();
  $('lab-policy').value = policyText;
  app.lab.validatePolicy();

  bindUi();
  syncStageRadio();
  if (noGlToast) app.toast('이 브라우저에서는 3D를 쓸 수 없어 2D 지도로 보여 드려요.');
  app.director.renderRail();
  darkMq.addEventListener('change', () => app.stage.readPalette());
  reduceMq.addEventListener('change', () => applyPrefs());

  window.__haetaeBooted = true;
  window.__haetae = app; // debugging handle (read-only use)
  $('fatal').hidden = true;

  if (app.kiosk) {
    app.director.startCard(CARDS[0]);
  } else {
    app.director.showIntro();
    app.director.teaser(() => new Gate(policyText));
  }
  let last = performance.now();
  let toolsAt = 0;
  const frame = (now) => {
    try {
      const dt = Math.min(100, now - last);
      last = now;
      app.lab.tick(dt);
      app.director.frame(now);
      if (now - toolsAt > 500) { toolsAt = now; app.overlay.placeToolbar(); }
    } catch (e) {
      if (!frame.reported) { frame.reported = true; console.error('Haetae frame error:', e); }
    }
    requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);

  if (params.has('selftest')) {
    const { runSelftest } = await import('./js/selftest.js');
    runSelftest(app.defaultPolicyText);
  }
}

// ───────────────────────── UI wiring ─────────────────────────

function bindUi() {
  const d = app.director;
  $('intro-cta').addEventListener('click', () => {
    $('intro-cta').classList.remove('pulse');
    d.startCard(CARDS[0]);
    $('btn-primary').focus();
  });
  $('intro-list').addEventListener('click', () => {
    const first = document.querySelector('.card-btn');
    first?.scrollIntoView({ behavior: app.prefs.reduced ? 'auto' : 'smooth', block: 'center' });
    first?.focus({ preventScroll: true });
  });
  $('intro-lab').addEventListener('click', () => app.openLab());

  $('btn-primary').addEventListener('click', () => d.primary());
  $('btn-ghost').addEventListener('click', () => d.ghostKey());
  $('btn-pause').addEventListener('click', () => d.pauseToggle());
  $('btn-detail').addEventListener('click', () => app.openDetail());

  // Auto-advance pauses while the pointer or focus is in the caption/transport.
  for (const id of ['caption', 'transport']) {
    const el = $(id);
    el.addEventListener('pointerenter', () => { d.block = true; });
    el.addEventListener('pointerleave', () => { d.block = el.contains(document.activeElement) && document.activeElement.matches(':focus-visible'); });
    el.addEventListener('focusin', (e) => { if (e.target.matches(':focus-visible')) d.block = true; });
    el.addEventListener('focusout', () => { d.block = false; });
  }

  $('btn-help').addEventListener('click', () => $('help').showModal());
  $('help-intro').addEventListener('click', () => { $('help').close(); if (app.lab.active) app.lab.close(); d.showIntro(); $('intro-cta').focus(); });
  $('help-close').addEventListener('click', () => $('help').close());
  $('detail-close').addEventListener('click', () => $('detail').close());
  $('detail').addEventListener('close', () => { (detailOpener && document.contains(detailOpener) ? detailOpener : $('btn-primary')).focus?.(); });
  for (const id of ['detail', 'help']) {
    $(id).addEventListener('click', (e) => { if (e.target === $(id)) $(id).close(); }); // backdrop click
  }

  $('btn-lab').addEventListener('click', () => (app.lab.active ? app.lab.close() : app.openLab()));
  $('policy-badge').addEventListener('click', () => app.openLab('policy'));

  $('btn-view').addEventListener('click', () => {
    const pop = $('view-pop');
    pop.hidden = !pop.hidden;
    $('btn-view').setAttribute('aria-expanded', String(!pop.hidden));
    if (!pop.hidden) pop.querySelector('input:checked')?.focus();
  });
  document.addEventListener('click', (e) => {
    if (!$('view-pop').hidden && !e.target.closest('.view-wrap')) closeView();
  });
  document.querySelectorAll('input[name="theme"]').forEach((r) => r.addEventListener('change', () => {
    app.prefs.theme = r.value;
    savePrefs();
    applyPrefs();
  }));
  $('pref-reduce').addEventListener('change', () => {
    app.prefs.reduceSet = $('pref-reduce').checked;
    savePrefs();
    applyPrefs();
  });
  document.querySelectorAll('input[name="stage"]').forEach((r) => r.addEventListener('change', () => {
    if (!r.checked) return;
    store.set(STAGE_KEY, r.value);
    app.swapStage(r.value);
  }));
  $('pref-auto').addEventListener('change', () => {
    app.prefs.autoSet = $('pref-auto').checked;
    savePrefs();
    applyPrefs();
  });

  document.addEventListener('keydown', onKey);
  if (app.kiosk) bindKiosk();
}

function onKey(e) {
  const d = app.director;
  if (e.key === 'Escape') {
    if (!$('view-pop').hidden) { closeView(); $('btn-view').focus(); return; }
    if (d.mode === 'intro' && !$('intro').hidden && !document.querySelector('dialog[open]')) {
      $('intro').hidden = true;
      document.body.dataset.state = 'idle';
      return;
    }
  }
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (document.querySelector('dialog[open].modal')) return;
  const t = e.target;
  if (t.closest?.('input, textarea, select, [contenteditable="true"], dialog.lab')) return;
  if (t === $('map') && app.lab.active) return;
  const k = e.key;
  const code = e.code;
  const btn = t.closest?.('button');
  if (k === ' ' && btn) return; // Space activates the focused button
  if (k === ' ') { e.preventDefault(); d.pauseToggle(); }
  else if (k === 'ArrowRight' || code === 'KeyN') { e.preventDefault(); if (d.mode === 'intro') d.startCard(CARDS[0]); else d.primary(); }
  else if (k === 'ArrowLeft') { e.preventDefault(); d.back(); }
  else if (code === 'KeyR') { if (d.card) d.startCard(d.card); }
  else if (code === 'KeyH') d.ghostKey();
  else if (code === 'KeyL') app.lab.active ? app.lab.close() : app.openLab();
  else if (k === '?') { e.preventDefault(); $('help').showModal(); }
  else if (k === '[') app.stage.viewCmd('left');
  else if (k === ']') app.stage.viewCmd('right');
  else if (k === '+' || k === '=') app.stage.viewCmd('in');
  else if (k === '-' || k === '_') app.stage.viewCmd('out');
  else if (code === 'KeyV') app.stage.viewCmd('home');
  else if (/^[1-8]$/.test(k)) app.pickCard(CARDS[Number(k) - 1]);
}

function bindKiosk() {
  let idle = 0;
  const d = app.director;
  const poke = () => {
    d.paused = true;
    d.syncPause();
    clearTimeout(idle);
    idle = setTimeout(() => {
      d.paused = false;
      d.syncPause();
      app.stage.resetView?.(); // hand the camera back to the scene after a visitor's drag
    }, 60000);
  };
  ['pointerdown', 'keydown'].forEach((ev) => document.addEventListener(ev, poke, { capture: true }));
}

boot().catch((e) => fatal(`예상치 못한 오류: ${e?.message ?? e}`));
