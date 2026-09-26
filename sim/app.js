// Haetae simulator — entry point.
//
// HARD RULE: JavaScript never decides or alters a safety verdict. Every
// verdict, fired list, clamped action and speed cap shown or executed comes
// from gate.judge() (the real haetae-core compiled to WebAssembly). This file
// builds proposals, keeps the simulated world and clock, and animates what
// the gate returned.

import { loadEngine, Gate, policyError } from './js/engine.js';
import { MapView } from './js/map.js';
import { EventLog } from './js/log.js';
import { ScenarioPlayer } from './js/scenario.js';
import {
  VERDICTS, MODES, MODE_KO, SOURCE_KO, KIND_KO,
  num, pt, simTime, actionText, actionTarget, explainFired, objectEmoji, objectLabel,
} from './js/explain.js';

const PERCEPTION_TICK_MS = 100;  // sim-time period of the perception heartbeat
const PREVIEW_REFRESH_MS = 120;  // re-judge the hover preview this often (clock and world move)
const SNAP_MS = 400;             // visual easing when a scenario world line relocates the robot
const HINT_MS = 4500;

const $ = (id) => document.getElementById(id);

const ui = {
  fatal: $('fatal'),
  version: $('engine-version'),
  ladder: $('mode-ladder'),
  clock: $('clock'),
  worldAge: $('world-age'),
  canvas: $('map-canvas'),
  mapWrap: $('map-wrap'),
  chip: $('map-chip'),
  hint: $('map-hint'),
  object: $('object-select'),
  source: $('source-select'),
  speed: $('speed-slider'),
  speedOut: $('speed-out'),
  confidence: $('confidence-slider'),
  confidenceOut: $('confidence-out'),
  freeze: $('freeze-toggle'),
  addAdult: $('add-adult'),
  addChild: $('add-child'),
  resetWorld: $('reset-world'),
  resetMode: $('reset-mode'),
  sendStop: $('send-stop'),
  badge: $('verdict-badge'),
  glyph: $('verdict-glyph'),
  vname: $('verdict-name'),
  vko: $('verdict-ko'),
  dProposal: $('d-proposal'),
  dAction: $('d-action'),
  dCap: $('d-cap'),
  dMode: $('d-mode'),
  dFired: $('d-fired'),
  log: $('event-log'),
  logClear: $('log-clear'),
  policyEditor: $('policy-editor'),
  policyStatus: $('policy-status'),
  policyApply: $('policy-apply'),
  policyRevert: $('policy-revert'),
  scenarioStart: $('scenario-start'),
  scenarioBar: $('scenario-bar'),
  scenarioStep: $('scenario-step'),
  scenarioCaption: $('scenario-caption'),
  scenarioPause: $('scenario-pause'),
  scenarioNext: $('scenario-next'),
  scenarioStop: $('scenario-stop'),
  scenarioSummary: $('scenario-summary'),
  announcer: $('announcer'),
  cursorAnnouncer: $('cursor-announcer'),
};

/** Mutable simulator state. `world` is the trusted WorldSnapshot handed to the gate. */
const state = {
  policy: null,           // parsed applied policy (drawing + explanations only)
  policyText: '',
  examplePolicyText: '',
  exampleWorld: null,
  world: null,
  clockMs: 0,             // trusted sim clock (interactive mode)
  speedFactor: 1,
  frozen: false,          // 인식 정지: world.stamp_ms stops advancing
  nextId: 1,
  mode: 'normal',
  motion: null,           // { kind:'move'|'reach', ..., resolve }
  visual: null,           // scenario only: animated robot, kept apart from the trusted world
  snap: null,             // scenario only: easing after a world line moves the robot
  rejected: null,         // last bul overlay
  pointer: null,          // map point under the mouse
  cursor: null,           // keyboard cursor
  cursorOn: false,
  grabbedHumanId: null,   // keyboard-grabbed human
  preview: null,
  previewAt: 0,
  placed: [],             // cosmetic: objects put down with 놓기
  scenarioActive: false,
  policyDraft: null,      // policy editor text saved while a scenario borrows the editor
};

let gate = null;
let map = null;
let log = null;
let player = null;

// ───────────────────────── Boot ─────────────────────────

function fatal(message) {
  window.__haetaeFailed = true;
  ui.fatal.hidden = false;
  ui.fatal.innerHTML = '';
  const strong = document.createElement('strong');
  strong.textContent = '시뮬레이터를 시작하지 못했습니다. ';
  const msg = document.createElement('span');
  msg.textContent = message;
  const help = document.createElement('div');
  help.innerHTML = '먼저 <code>sim/build.sh</code>로 WASM을 빌드한 뒤, <code>sim/</code> 폴더를 HTTP로 서빙하세요: '
    + '<code>python3 -m http.server -d sim 8000</code> → <code>http://localhost:8000/</code> (file:// 로는 동작하지 않습니다)';
  ui.fatal.append(strong, msg, help);
  ui.version.textContent = '엔진 없음';
}

async function fetchText(path) {
  const res = await fetch(path, { cache: 'no-store' });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.text();
}

async function boot() {
  let version;
  try {
    ({ version } = await loadEngine());
  } catch (e) {
    fatal(`WASM 판정 엔진(./pkg/haetae_wasm.js, haetae_wasm_bg.wasm)을 불러오지 못했습니다: ${e.message}`);
    return;
  }

  let policyText;
  let world;
  try {
    [policyText, world] = await Promise.all([
      fetchText('./examples/policy.json'),
      fetchText('./examples/world.json').then((t) => JSON.parse(t)),
    ]);
  } catch (e) {
    fatal(`예제 파일(./examples/policy.json, world.json)을 불러오지 못했습니다: ${e.message}`);
    return;
  }

  ui.version.textContent = `haetae-core v${version}`;
  state.examplePolicyText = policyText;
  state.exampleWorld = world;

  log = new EventLog(ui.log);
  map = new MapView(ui.canvas, ui.mapWrap, {
    onPointer: (p) => { state.pointer = p; refreshPreview(performance.now(), true); },
    onCommit: commitAt,
    onHumanMove: moveHuman,
    onHumanDrop: dropHuman,
    getHumans: () => state.world.humans ?? [],
  });
  player = new ScenarioPlayer(scenarioApi);

  state.clockMs = world.stamp_ms; // the interactive clock starts at world.json's stamp
  ui.policyEditor.value = policyText;
  if (!applyPolicy(policyText, '예제 정책 로드')) {
    fatal(`예제 정책이 거부되었습니다: ${policyError(policyText)}`);
    return;
  }
  loadWorld(world);
  log.add({ t: state.clockMs, kind: 'world', text: `초기 월드 로드 — 로봇 ${pt(world.robot.pose)}, 사람 ${(world.humans ?? []).length}명` });

  bindControls();
  validatePolicyEditor();
  ui.scenarioStart.disabled = false;
  window.__haetaeBooted = true;
  // A slow first load may have tripped the boot watchdog in index.html.
  ui.fatal.hidden = true;
  ui.fatal.replaceChildren();
  requestAnimationFrame(frame);
}

// ───────────────────────── World & clock ─────────────────────────

/** Replace the interactive world with a copy of `w`, stamped fresh at the current clock. */
function loadWorld(w) {
  abortMotion();
  state.world = structuredClone(w);
  state.world.humans ??= [];
  state.world.robot.holding ??= null;
  state.world.stamp_ms = Math.floor(state.clockMs);
  state.placed = [];
  state.rejected = null;
  state.visual = null;
  state.snap = null;
  syncConfidence();
}

function syncConfidence() {
  ui.confidence.value = state.world.confidence;
  ui.confidenceOut.textContent = num(state.world.confidence);
}

let lastFrame = performance.now();
let lastStatus = 0;
let frameErrorReported = false;

/** rAF loop. Always reschedules, so one bad frame cannot freeze the simulator. */
function frame(now) {
  try {
    drawFrame(now);
  } catch (e) {
    if (!frameErrorReported) {
      frameErrorReported = true;
      console.error('Haetae sim frame error:', e);
      log?.add({ t: state.clockMs, kind: 'error', text: `화면 갱신 오류: ${e?.message ?? e}` });
    }
  } finally {
    requestAnimationFrame(frame);
  }
}

function drawFrame(now) {
  const dtReal = Math.min(100, Math.max(0, now - lastFrame));
  lastFrame = now;
  const dtSim = dtReal * state.speedFactor;

  if (!state.scenarioActive) {
    state.clockMs += dtSim;
    // Perception heartbeat: a fresh snapshot every 100 ms of sim time, unless frozen.
    if (!state.frozen && state.clockMs - state.world.stamp_ms >= PERCEPTION_TICK_MS) {
      state.world.stamp_ms = Math.floor(state.clockMs);
    }
  }

  stepMotion(dtSim);
  refreshPreview(now, false);

  const robot = displayRobot(now);
  map.draw({
    policy: state.policy,
    humans: state.world.humans,
    robot,
    mode: state.mode,
    motion: state.motion,
    rejected: state.rejected,
    preview: state.preview ? { ...state.preview, from: robot.pose } : null,
    cursor: state.cursorOn ? state.cursor : null,
    grabbedHumanId: state.grabbedHumanId,
    placed: state.placed,
  }, now);

  if (now - lastStatus > 100) {
    lastStatus = now;
    updateStatus();
  }
}

function updateStatus() {
  const now = Math.floor(state.clockMs);
  ui.clock.textContent = simTime(now);
  const age = now - state.world.stamp_ms;
  const budget = state.policy?.freshness?.world_max_age_ms ?? 500;
  const frozen = state.frozen && !state.scenarioActive ? ' · 정지' : '';
  ui.worldAge.textContent = `인식 나이 ${age} ms (한도 ${budget})${frozen}`;
  ui.worldAge.classList.toggle('stale', age > budget);
}

// ───────────────────────── Robot body & motion ─────────────────────────

/**
 * The robot state that motion animates. Interactive: the trusted world itself
 * (perception reports where the robot actually is). Scenario: a visual copy,
 * because there the world comes only from the stream, exactly like the CLI.
 */
function body() {
  if (state.scenarioActive) {
    state.visual ??= { pose: { ...state.world.robot.pose }, holding: state.world.robot.holding ?? null };
    return state.visual;
  }
  return state.world.robot;
}

/** Where to draw the robot: the animated body, easing toward the world after a jump. */
function displayRobot(now) {
  if (state.visual) return state.visual;
  const snap = state.snap;
  if (snap) {
    const t = Math.min(1, (now - snap.t0) / SNAP_MS);
    if (t < 1) {
      const e = 1 - (1 - t) ** 3; // ease-out
      const to = state.world.robot.pose;
      return {
        pose: { x: snap.from.x + (to.x - snap.from.x) * e, y: snap.from.y + (to.y - snap.from.y) * e },
        holding: state.world.robot.holding ?? null,
      };
    }
    state.snap = null;
  }
  return state.world.robot;
}

const dist = (a, b) => Math.hypot(b.x - a.x, b.y - a.y);

function startMove(action, decision, proposal) {
  const b = body();
  if (!(action.speed > 0)) {
    hint('실행 속도가 0 m/s로 제한되어 로봇이 움직이지 않습니다.');
    return Promise.resolve();
  }
  if (dist(b.pose, action.goal) < 1e-6) return Promise.resolve();
  return new Promise((resolve) => {
    state.motion = {
      kind: 'move',
      to: { ...action.goal },
      speed: action.speed,                      // executed speed from decision.action
      requested: proposal.action.speed,
      verdict: decision.verdict,
      resolve,
    };
  });
}

function startReach(action, decision) {
  // speed_cap bounds ALL motion for this action, arm included. A zero (or
  // missing) cap means the arm may not move, so nothing is grasped or placed.
  const cap = decision.speed_cap;
  if (!(cap > 0)) {
    hint('속도 상한이 0 m/s라 팔을 움직일 수 없어 집기/놓기를 실행하지 않습니다.');
    return Promise.resolve();
  }
  const b = body();
  const object = action.type === 'grasp' ? action.object : b.holding;
  if (action.type === 'place' && !object) hint('들고 있는 물건이 없어 빈 손으로 놓기 동작만 합니다.');
  // The arm travels out and back no faster than the gate's cap. The floor only
  // slows very short reaches down so they stay visible; there is no ceiling.
  const d = dist(b.pose, action.at);
  const duration = Math.max(600, (2 * d / cap) * 1000);
  if (duration > 8000) {
    hint(`팔이 속도 상한 ${num(cap)} m/s에 맞춰 천천히 움직입니다 (시뮬레이션 시간 약 ${Math.round(duration / 1000)}초).`);
  }
  return new Promise((resolve) => {
    state.motion = {
      kind: 'reach',
      op: action.type,
      at: { ...action.at },
      object,
      progress: 0,
      duration,
      applied: false,
      verdict: decision.verdict,
      resolve,
    };
  });
}

/** Grasp/place takes effect when the arm reaches the target. */
function applyReach(m) {
  if (m.applied) return;
  m.applied = true;
  const b = body();
  if (m.op === 'grasp') {
    b.holding = m.object;
    const i = state.placed.findIndex((it) => it.object === m.object && dist(it.at, m.at) < 0.6);
    if (i >= 0) state.placed.splice(i, 1);
  } else if (m.op === 'place' && b.holding) {
    state.placed.push({ object: b.holding, at: { ...m.at } });
    b.holding = null;
  }
  if (!state.scenarioActive) {
    log.add({
      t: state.clockMs,
      kind: 'world',
      text: m.op === 'grasp'
        ? `로봇이 ${objectLabel(m.object)}을(를) 집음 → world.robot.holding = "${m.object}"`
        : m.object
          ? `로봇이 ${objectLabel(m.object)}을(를) ${pt(m.at)}에 놓음 → world.robot.holding = null`
          : `빈 손으로 ${pt(m.at)}에 놓기 동작 (들고 있던 물건 없음)`,
    });
  }
}

function stepMotion(dtSim) {
  const m = state.motion;
  if (!m) return;
  const b = body();
  if (m.kind === 'move') {
    const remaining = dist(b.pose, m.to);
    const step = (m.speed * dtSim) / 1000;
    if (step >= remaining) {
      b.pose = { ...m.to };
      finishMotion();
    } else {
      b.pose = {
        x: b.pose.x + ((m.to.x - b.pose.x) / remaining) * step,
        y: b.pose.y + ((m.to.y - b.pose.y) / remaining) * step,
      };
    }
  } else {
    m.progress = Math.min(1, m.progress + dtSim / m.duration);
    if (m.progress >= 0.5) applyReach(m);
    if (m.progress >= 1) finishMotion();
  }
}

function finishMotion() {
  const m = state.motion;
  state.motion = null;
  m?.resolve();
}

/** Complete the current animation instantly (scenario ⏭ during a move). */
function fastForward() {
  const m = state.motion;
  if (!m) return;
  if (m.kind === 'move') body().pose = { ...m.to };
  else applyReach(m);
  finishMotion();
}

/** Stop the current animation where it is. */
function abortMotion(reason) {
  const m = state.motion;
  if (!m) return;
  state.motion = null;
  m.resolve();
  if (reason && log) log.add({ t: state.clockMs, kind: 'exec', text: reason });
}

// ───────────────────────── Proposals & decisions ─────────────────────────

function settings() {
  return {
    type: document.querySelector('input[name="action-type"]:checked').value,
    object: ui.object.value,
    source: ui.source.value,
    speed: Number(ui.speed.value),
  };
}

function buildAction(s, p) {
  switch (s.type) {
    case 'grasp': return { type: 'grasp', object: s.object, at: { x: p.x, y: p.y } };
    case 'place': return { type: 'place', at: { x: p.x, y: p.y } };
    default: return { type: 'move_to', goal: { x: p.x, y: p.y }, speed: s.speed };
  }
}

function makeProposal(action, id, source = settings().source) {
  return { id, source, timestamp_ms: Math.floor(state.clockMs), action };
}

/** Ask the gate. Malformed input is an engine error, never a guessed verdict. */
function judge(proposal, nowMs) {
  try {
    return { decision: gate.judge(proposal, state.world, nowMs) };
  } catch (e) {
    return { error: e?.message ?? String(e) };
  }
}

/** Judge a proposal, show and log the decision, then execute what the gate returned. */
function runProposal(proposal, nowMs) {
  const from = { ...displayRobot(performance.now()).pose };
  const r = judge(proposal, nowMs);
  if (r.error) {
    showEngineError(proposal, r.error);
    log.add({ t: nowMs, kind: 'error', text: `제안 #${proposal.id} 판정 불가 — 엔진이 입력을 거부: ${r.error}` });
    announce(`판정 불가: ${r.error}`);
    return { decision: null, done: Promise.resolve() };
  }
  const d = r.decision;
  showDecision(d, proposal);
  logDecision(d, proposal, nowMs);
  // During playback the scenario caption (a live region) already speaks the verdict.
  if (!state.scenarioActive) announceDecision(d);
  updateMode();
  return { decision: d, done: execute(d, proposal, from) };
}

function execute(d, proposal, from) {
  state.rejected = null;
  if (d.verdict === 'bul' || !d.action) {
    state.rejected = { from, to: actionTarget(proposal.action), robot: from, t0: performance.now() };
    return Promise.resolve();
  }
  const a = d.action;
  switch (a.type) {
    case 'stop':
      abortMotion('정지(stop) 실행 — 로봇이 그 자리에 멈춤');
      return Promise.resolve();
    case 'move_to':
      return startMove(a, d, proposal);
    case 'grasp':
    case 'place':
      return startReach(a, d);
    default:
      return Promise.resolve();
  }
}

function commitAt(p) {
  if (!gate) return;
  if (state.scenarioActive) {
    hint('시나리오 재생 중에는 지도 클릭이 무시됩니다. ⏹ 중지 후 사용하세요.');
    return;
  }
  if (state.motion) {
    hint('로봇이 동작 중이라 클릭을 무시했습니다 — 끝난 뒤 다시 클릭하거나 ■ 정지를 누르세요.');
    return;
  }
  const proposal = makeProposal(buildAction(settings(), p), state.nextId++);
  runProposal(proposal, Math.floor(state.clockMs));
  refreshPreview(performance.now(), true);
}

function sendStop() {
  if (!gate || state.scenarioActive) return;
  const proposal = makeProposal({ type: 'stop' }, state.nextId++);
  runProposal(proposal, Math.floor(state.clockMs));
}

/** Hover/keyboard preview: what the gate WOULD decide. Not recorded, id not consumed. */
function refreshPreview(now, force) {
  const target = state.pointer ?? (state.cursorOn ? state.cursor : null);
  if (!target || !gate || state.scenarioActive) {
    state.preview = null;
    return;
  }
  if (!force && now - state.previewAt < PREVIEW_REFRESH_MS) return;
  const s = settings();
  const proposal = makeProposal(buildAction(s, target), state.nextId, s.source);
  const r = judge(proposal, Math.floor(state.clockMs));
  const holding = displayRobot(now).holding;
  state.preview = {
    to: target,
    decision: r.decision ?? null,
    error: r.error,
    emoji: s.type === 'grasp' ? objectEmoji(s.object) : s.type === 'place' && holding ? objectEmoji(holding) : null,
  };
  state.previewAt = now;
}

// ───────────────────────── Mode ladder ─────────────────────────

function updateMode() {
  const m = gate.mode;
  const changed = m !== state.mode;
  state.mode = m;
  for (const li of ui.ladder.children) {
    if (li.dataset.mode === m) li.setAttribute('aria-current', 'step');
    else li.removeAttribute('aria-current');
  }
  if (changed) announce(`모드 변경: ${m} (${MODE_KO[m]})`);
}

/**
 * Raise the mode through the gate. If the result is stop-only (hold and up),
 * the executor halts the motion in progress: the gate now denies everything
 * but stop, so an approved action must not keep running.
 */
function raiseMode(target, { code, t }) {
  const before = gate.mode;
  let changed;
  try {
    changed = gate.raise(target);
  } catch (e) {
    log.add({ t, kind: 'error', text: `모드 상승 실패: ${e.message}` });
    return;
  }
  const after = gate.mode;
  log.add({
    t,
    kind: 'fault',
    text: `${code ? `${code}: ` : ''}${target}(으)로 상승 요청 → ${changed ? `${before} → ${after}` : `변화 없음 (현재 ${after})`}`,
  });
  updateMode();
  if (MODES.indexOf(after) >= MODES.indexOf('hold') && state.motion) {
    abortMotion(`모드 ${after}: 정지 전용 모드라 실행기가 진행 중인 동작을 멈춤`);
  }
}

function resetMode() {
  const before = gate.mode;
  gate.reset();
  log.add({ t: state.clockMs, kind: 'mode', text: `운영자 리셋: ${before} → ${gate.mode}` });
  updateMode();
}

// ───────────────────────── Decision panel ─────────────────────────

function showDecision(d, proposal) {
  const v = VERDICTS[d.verdict];
  ui.badge.dataset.verdict = d.verdict;
  ui.glyph.textContent = v.glyph;
  ui.vname.textContent = d.verdict;
  ui.vko.textContent = v.ko;
  ui.chip.dataset.verdict = d.verdict;
  ui.chip.textContent = `#${d.proposal_id} ${v.glyph} ${d.verdict}`;

  ui.dProposal.textContent = `#${proposal.id} · ${proposal.source} (${SOURCE_KO[proposal.source] ?? '?'}) · ${actionText(proposal.action)}`;
  ui.dAction.textContent = actionText(d.action);

  ui.dCap.replaceChildren();
  if (d.speed_cap == null) {
    ui.dCap.textContent = d.action?.type === 'stop' ? '없음 (정지는 제한 없음)' : '없음';
  } else if (d.verdict === 'jeol') {
    const strong = document.createElement('span');
    strong.className = 'cap-jeol';
    strong.textContent = `節 ${num(d.speed_cap)} m/s`;
    const detail = d.action?.type === 'move_to'
      ? ` — 요청 ${num(proposal.action.speed)} m/s → 실행 ${num(d.action.speed)} m/s로 감속`
      : ' — 팔 동작 속도에 적용';
    ui.dCap.append(strong, detail);
  } else {
    ui.dCap.textContent = `${num(d.speed_cap)} m/s`;
  }
  ui.dMode.textContent = `${d.mode} (${MODE_KO[d.mode] ?? ''})`;

  ui.dFired.replaceChildren();
  if (!d.fired.length) {
    const li = document.createElement('li');
    li.className = 'muted';
    li.textContent = '없음 — 모든 검사 통과';
    ui.dFired.append(li);
  }
  for (const name of d.fired) {
    const f = explainFired(name, state.policy);
    const li = document.createElement('li');
    const code = document.createElement('code');
    code.textContent = f.name;
    const kind = document.createElement('span');
    kind.className = 'fired-kind';
    kind.textContent = KIND_KO[f.kind];
    const text = document.createElement('span');
    text.className = 'fired-text';
    text.textContent = f.text;
    li.append(code, kind, text);
    ui.dFired.append(li);
  }
}

function showEngineError(proposal, message) {
  ui.badge.dataset.verdict = 'error';
  ui.glyph.textContent = '⚠';
  ui.vname.textContent = '판정 불가';
  ui.vko.textContent = `엔진이 입력을 거부했습니다 — 실행하지 않음: ${message}`;
  ui.dProposal.textContent = `#${proposal.id}`;
  ui.dAction.textContent = '없음 (실행하지 않음)';
  ui.dCap.textContent = '—';
  ui.dMode.textContent = gate.mode;
  ui.dFired.replaceChildren();
  ui.chip.textContent = '';
}

function logDecision(d, proposal, t) {
  const cap = d.speed_cap == null ? '없음' : `${num(d.speed_cap)} m/s`;
  log.add({
    t,
    kind: 'proposal',
    verdict: d.verdict,
    text: `#${proposal.id} ${proposal.source}: ${actionText(proposal.action)}`,
    detail: `fired: ${d.fired.join(', ') || '없음'} · 실행: ${actionText(d.action)} · 상한: ${cap} · 모드: ${d.mode}`,
  });
}

function announceDecision(d) {
  const v = VERDICTS[d.verdict];
  const fired = d.fired.length ? `, 발동: ${d.fired.join(', ')}` : '';
  const cap = d.verdict === 'jeol' && d.speed_cap != null ? `, 속도 상한 ${num(d.speed_cap)} m/s` : '';
  announce(`제안 ${d.proposal_id} 판정: ${d.verdict} ${v.ko}${cap}${fired}`);
}

function announce(text) {
  ui.announcer.textContent = '';
  // Re-set on the next frame so repeated messages are announced again.
  requestAnimationFrame(() => { ui.announcer.textContent = text; });
}

let hintTimer = 0;
function hint(text) {
  ui.hint.textContent = text;
  clearTimeout(hintTimer);
  hintTimer = setTimeout(() => { ui.hint.textContent = ''; }, HINT_MS);
}

// ───────────────────────── Humans ─────────────────────────

function moveHuman(id, p) {
  if (state.scenarioActive) {
    hint('시나리오 재생 중에는 월드를 바꿀 수 없습니다.');
    return;
  }
  const h = state.world.humans.find((x) => x.id === id);
  if (h) h.pos = { x: p.x, y: p.y };
}

function dropHuman(id) {
  if (state.scenarioActive) return;
  const h = state.world.humans.find((x) => x.id === id);
  if (h) log.add({ t: state.clockMs, kind: 'world', text: `${h.class === 'child' ? '아이' : '어른'} ${h.id} 위치 → ${pt(h.pos)}` });
}

function addHuman(cls) {
  const prefix = cls === 'child' ? 'kid' : 'adult';
  let n = 1;
  while (state.world.humans.some((h) => h.id === `${prefix}${n}`)) n++;
  const ws = state.policy.envelope.workspace;
  // A row of six slots near the centre (children one row lower), kept inside the workspace.
  const k = (n - 1) % 6;
  const want = {
    x: (ws.min.x + ws.max.x) / 2 - 1.5 + k * 0.6,
    y: (ws.min.y + ws.max.y) / 2 - 1 - (cls === 'child' ? 0.6 : 0),
  };
  const pos = MapView.snap({
    x: Math.min(ws.max.x, Math.max(ws.min.x, want.x)),
    y: Math.min(ws.max.y, Math.max(ws.min.y, want.y)),
  });
  state.world.humans.push({ id: `${prefix}${n}`, class: cls, pos });
  log.add({ t: state.clockMs, kind: 'world', text: `${cls === 'child' ? '아이' : '어른'} ${prefix}${n} 추가 @ ${pt(pos)} — 드래그해서 옮기세요` });
}

// ───────────────────────── Keyboard on the map ─────────────────────────

function bindMapKeyboard() {
  const c = ui.canvas;
  const showCursor = () => {
    state.cursorOn = true;
    const p = displayRobot(performance.now()).pose;
    state.cursor ??= MapView.snap(map.clamp({ x: p.x + 1, y: p.y + 1 }));
    refreshPreview(performance.now(), true);
  };
  // Keyboard focus only: a mouse click on the map should not summon the cursor.
  c.addEventListener('focus', () => { if (c.matches(':focus-visible')) showCursor(); });
  c.addEventListener('blur', () => {
    state.cursorOn = false;
    releaseGrab();
    refreshPreview(performance.now(), true);
  });
  c.addEventListener('keydown', (e) => {
    if (!state.cursorOn) showCursor();
    const step = e.shiftKey ? 1 : 0.25;
    const delta = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] }[e.key];
    if (delta) {
      e.preventDefault();
      const grabbed = state.world.humans.find((h) => h.id === state.grabbedHumanId);
      if (grabbed) {
        moveHuman(grabbed.id, MapView.snap(map.clamp({ x: grabbed.pos.x + delta[0], y: grabbed.pos.y + delta[1] })));
        state.cursor = { ...grabbed.pos };
      } else {
        state.cursor = MapView.snap(map.clamp({ x: state.cursor.x + delta[0], y: state.cursor.y + delta[1] }));
      }
      refreshPreview(performance.now(), true);
      announceCursor();
    } else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      if (state.grabbedHumanId) releaseGrab();
      else commitAt(state.cursor);
    } else if (e.code === 'KeyH' || e.key === 'h' || e.key === 'H') {
      // e.code: with a Korean input source e.key is 'ㅗ' for the same physical key.
      e.preventDefault();
      cycleGrab();
    } else if (e.key === 'Escape') {
      releaseGrab();
    }
  });
}

/**
 * Tell screen-reader users where the keyboard cursor is and what the gate
 * would decide there (state.preview, exactly as the gate returned it).
 * Debounced, and in its own live region so decision announcements survive.
 */
let cursorAnnounceTimer = 0;
function announceCursor() {
  clearTimeout(cursorAnnounceTimer);
  cursorAnnounceTimer = setTimeout(() => {
    const c = state.cursor;
    if (!state.cursorOn || !c) return;
    const grabbed = state.world.humans.find((h) => h.id === state.grabbedHumanId);
    let text;
    if (grabbed) {
      text = `${grabbed.class === 'child' ? '아이' : '어른'} ${grabbed.id} ${pt(grabbed.pos)}`;
    } else {
      text = `커서 ${pt(c)}`;
      const p = state.preview;
      if (p && p.to === c) {
        const d = p.decision;
        if (d) text += ` · 예상 ${d.verdict} ${VERDICTS[d.verdict].ko}${d.fired[0] ? ` · ${d.fired[0]}` : ''}`;
        else if (p.error) text += ' · 판정 불가';
      }
    }
    ui.cursorAnnouncer.textContent = text;
  }, 250);
}

function cycleGrab() {
  if (state.scenarioActive) {
    hint('시나리오 재생 중에는 월드를 바꿀 수 없습니다.');
    return;
  }
  const humans = state.world.humans;
  const i = humans.findIndex((h) => h.id === state.grabbedHumanId);
  if (i >= 0) dropHuman(humans[i].id);
  const next = humans[i + 1];
  state.grabbedHumanId = next?.id ?? null;
  if (next) {
    state.cursor = { ...next.pos };
    announce(`${next.class === 'child' ? '아이' : '어른'} ${next.id} 잡음. 화살표로 옮기고 Enter 또는 Esc로 놓기, H로 다음 사람.`);
  } else {
    announce('사람을 놓았습니다. 화살표는 커서를 움직입니다.');
  }
}

function releaseGrab() {
  if (!state.grabbedHumanId) return;
  dropHuman(state.grabbedHumanId);
  state.grabbedHumanId = null;
  announce('사람을 놓았습니다.');
}

// ───────────────────────── Policy editor ─────────────────────────

/** Build a fresh gate from `text`. Fail-closed: an invalid policy changes nothing. */
function applyPolicy(text, reason) {
  const err = policyError(text);
  if (err) {
    validatePolicyEditor();
    return false;
  }
  let next;
  try {
    next = new Gate(text);
  } catch (e) {
    setPolicyStatus('err', `✕ ${e.message}`);
    return false;
  }
  const before = gate?.mode;
  gate?.free();
  gate = next;
  state.policy = JSON.parse(text);
  state.policyText = text;
  map.setPolicy(state.policy);
  state.rejected = null;
  state.preview = null;
  const p = state.policy;
  log.add({
    t: state.clockMs,
    kind: 'policy',
    text: `${reason} — 판정 엔진을 새로 만듦${before ? ` (모드 ${before} → normal로 초기화)` : ''}`,
    detail: `구역 ${(p.zones ?? []).length}개 · 규칙 ${(p.rules ?? []).length}개 · 허용 출처 ${p.allowed_sources.join(', ') || '없음'} · 최대 속도 ${num(p.envelope.max_speed)} m/s`,
  });
  updateMode();
  validatePolicyEditor();
  return true;
}

/** Write the status only when it changes, so the live region is not re-announced on every keystroke. */
function setPolicyStatus(kind, message) {
  const cls = `policy-status ${kind}`;
  if (ui.policyStatus.className !== cls) ui.policyStatus.className = cls;
  if (ui.policyStatus.textContent !== message) ui.policyStatus.textContent = message;
}

function validatePolicyEditor() {
  const text = ui.policyEditor.value;
  const err = policyError(text);
  const dirty = text !== state.policyText;
  if (err) {
    setPolicyStatus('err', `✕ 거부됨: ${err}`);
    ui.policyEditor.setAttribute('aria-invalid', 'true');
    ui.policyApply.disabled = true;
  } else {
    setPolicyStatus('ok', dirty ? '✓ 유효한 정책 — “적용”을 누르면 반영됩니다' : '✓ 유효한 정책 (현재 적용됨)');
    ui.policyEditor.setAttribute('aria-invalid', 'false');
    ui.policyApply.disabled = !dirty || state.scenarioActive;
  }
}

// ───────────────────────── Scenario hooks ─────────────────────────

const scenarioApi = {
  begin(policyText, world) {
    abortMotion();
    // Keep the user's editor text (applied or not); leaveScenario() puts it back.
    if (!state.scenarioActive) state.policyDraft = ui.policyEditor.value;
    state.scenarioActive = true;
    setInteractive(false);
    ui.scenarioBar.hidden = false;
    ui.scenarioSummary.hidden = true;
    ui.policyEditor.value = policyText;
    state.clockMs = world.stamp_ms;
    if (!applyPolicy(policyText, '시나리오: 예제 정책으로 새 엔진 구성')) {
      throw new Error(policyError(policyText) ?? '정책 오류');
    }
    state.world = structuredClone(world); // world.json is the initial world, stamp untouched
    state.world.humans ??= [];
    state.visual = null;
    state.snap = null;
    state.placed = [];
    state.rejected = null;
    state.preview = null;
    state.frozen = false;
    ui.freeze.checked = false;
    syncConfidence();
    log.add({
      t: world.stamp_ms,
      kind: 'scenario',
      text: '──── 디너파티 공격 재생 시작 ────',
      detail: '여기부터 시각은 시나리오 파일의 시각입니다 (world.json을 초기 월드로, now = 최신 world.stamp_ms). 이전 항목과 시각이 이어지지 않습니다.',
    });
  },

  world(w, now) {
    const from = { ...displayRobot(performance.now()).pose };
    state.world = structuredClone(w);
    state.world.humans ??= [];
    state.clockMs = now;
    state.visual = null;
    if (dist(from, w.robot.pose) > 1e-6) state.snap = { from, t0: performance.now() };
    syncConfidence();
    log.add({
      t: now,
      kind: 'world',
      text: `월드 갱신 — 로봇 ${pt(w.robot.pose)}, 사람 ${state.world.humans.length}명, 신뢰도 ${num(w.confidence)}`,
      detail: w.robot.holding ? `들고 있음: ${objectLabel(w.robot.holding)}` : undefined,
    });
  },

  rejectedWorld(w, now, currentStamp) {
    state.clockMs = now;
    log.add({ t: now, kind: 'error', text: `out-of-order world 거부 — stamp_ms ${w.stamp_ms} < 현재 ${currentStamp}, 기존 월드 유지` });
  },

  fault(f, now) {
    raiseMode(f.raise_to, { code: f.code, t: now });
  },

  run(proposal, now) {
    return runProposal(proposal, now);
  },

  fastForward,

  caption(text, i, total) {
    ui.scenarioCaption.textContent = text;
    ui.scenarioStep.textContent = `${i} / ${total}`;
  },

  controls({ running, paused, stoppable = false }) {
    ui.scenarioPause.textContent = paused ? '▶ 계속' : '⏸ 일시정지';
    const next = [
      [ui.scenarioPause, !running],
      [ui.scenarioNext, !running],
      [ui.scenarioStop, !(running || stoppable)],
    ];
    // Disabling the focused button would drop focus to <body>; move it to ▶/↻ first.
    if (next.some(([el, off]) => off && el === document.activeElement)) ui.scenarioStart.focus();
    for (const [el, off] of next) el.disabled = off;
  },

  end(summary, nowMs) {
    if (!state.scenarioActive) return;
    leaveScenario(summary?.now ?? nowMs);

    if (!summary) {
      ui.scenarioCaption.textContent = '재생을 중지했습니다. 지금 상태에서 직접 조작할 수 있습니다.';
      log.add({ t: state.clockMs, kind: 'scenario', text: '시나리오 재생 중지' });
      return;
    }
    const { counts, decisions } = summary;
    const line = `재생 완료 — 允 yun ${counts.yun} · 節 jeol ${counts.jeol} · 不 bul ${counts.bul}`;
    ui.scenarioCaption.textContent = '시나리오 끝. 모드는 운영자 리셋 전까지 유지됩니다.';
    ui.scenarioSummary.hidden = false;
    ui.scenarioSummary.replaceChildren(document.createTextNode(`${line} (게이트 판정 기준)`));
    const seq = document.createElement('div');
    seq.className = 'seq';
    for (const d of decisions) {
      const chip = document.createElement('span');
      chip.className = `vchip vchip-${d.verdict}`;
      chip.textContent = `#${d.proposal_id} ${VERDICTS[d.verdict].glyph} ${d.verdict}`;
      seq.append(chip);
    }
    ui.scenarioSummary.append(seq);
    log.add({ t: state.clockMs, kind: 'scenario', text: line, detail: decisions.map((d) => `#${d.proposal_id} ${d.verdict}`).join(' · ') });
    announce(line);
  },

  error(message) {
    if (state.scenarioActive) leaveScenario();
    ui.scenarioBar.hidden = false;
    ui.scenarioCaption.textContent = message;
    ui.scenarioStep.textContent = '';
    log.add({ t: state.clockMs, kind: 'error', text: message });
    scenarioApi.controls({ running: false, paused: false });
  },
};

/**
 * Hand control back to interactive mode (shared by end and error): stop the
 * scenario's animation, let the drawn robot ease onto the trusted world,
 * unlock the controls and give the policy editor back its previous text.
 */
function leaveScenario(nowMs) {
  abortMotion();
  const from = { ...displayRobot(performance.now()).pose };
  state.scenarioActive = false;
  state.visual = null;
  state.snap = dist(from, state.world.robot.pose) > 1e-6 ? { from, t0: performance.now() } : null;
  if (nowMs != null) state.clockMs = nowMs;
  setInteractive(true);
  if (state.policyDraft != null) {
    ui.policyEditor.value = state.policyDraft;
    if (state.policyDraft !== state.policyText) {
      log.add({
        t: state.clockMs,
        kind: 'policy',
        text: '시나리오가 적용 정책을 예제 정책으로 바꿨습니다 — 편집기에는 이전 내용을 되돌려 놓았으니 “적용”으로 다시 반영할 수 있습니다',
      });
    }
    state.policyDraft = null;
  }
  validatePolicyEditor();
}

/** Interactive controls that would change the world or the gate are locked during playback. */
function setInteractive(on) {
  const els = [
    ui.resetMode, ui.sendStop, ui.addAdult, ui.addChild, ui.resetWorld,
    ui.confidence, ui.freeze, ui.policyRevert,
    ...document.querySelectorAll('[data-raise]'),
  ];
  for (const el of els) el.disabled = !on;
  if (!on) ui.policyApply.disabled = true;
  ui.scenarioStart.textContent = on ? '▶ 디너파티 공격 재생' : '↻ 처음부터 다시 재생';
}

// ───────────────────────── Controls ─────────────────────────

function bindControls() {
  const onSettingsChange = () => {
    const type = settings().type;
    ui.object.disabled = type !== 'grasp';
    ui.speed.disabled = type !== 'move_to';
    refreshPreview(performance.now(), true);
  };
  document.querySelectorAll('input[name="action-type"]').forEach((el) => el.addEventListener('change', onSettingsChange));
  ui.object.addEventListener('change', onSettingsChange);
  ui.source.addEventListener('change', onSettingsChange);
  onSettingsChange();

  ui.speed.addEventListener('input', () => {
    ui.speedOut.textContent = `${num(Number(ui.speed.value))} m/s`;
    refreshPreview(performance.now(), true);
  });

  ui.confidence.addEventListener('input', () => {
    state.world.confidence = Number(ui.confidence.value);
    ui.confidenceOut.textContent = num(state.world.confidence);
    refreshPreview(performance.now(), true);
  });
  ui.confidence.addEventListener('change', () => {
    log.add({ t: state.clockMs, kind: 'world', text: `인식 신뢰도 → ${num(state.world.confidence)}` });
  });

  ui.freeze.addEventListener('change', () => {
    state.frozen = ui.freeze.checked;
    log.add({
      t: state.clockMs,
      kind: 'world',
      text: state.frozen
        ? `인식 정지 — world.stamp_ms를 ${simTime(state.world.stamp_ms)}에 고정`
        : '인식 재개 — world.stamp_ms가 다시 시계를 따라감',
    });
  });

  document.querySelectorAll('input[name="speed-factor"]').forEach((el) => el.addEventListener('change', () => {
    state.speedFactor = Number(document.querySelector('input[name="speed-factor"]:checked').value);
  }));

  ui.addAdult.addEventListener('click', () => addHuman('adult'));
  ui.addChild.addEventListener('click', () => addHuman('child'));
  ui.resetWorld.addEventListener('click', () => {
    loadWorld(state.exampleWorld);
    log.add({ t: state.clockMs, kind: 'world', text: '월드 초기화 — world.json 상태로 (stamp는 현재 시계)' });
  });

  document.querySelectorAll('[data-raise]').forEach((btn) => btn.addEventListener('click', () => {
    raiseMode(btn.dataset.raise, { code: '수동 주입', t: state.clockMs });
  }));
  ui.resetMode.addEventListener('click', resetMode);
  ui.sendStop.addEventListener('click', sendStop);

  ui.logClear.addEventListener('click', () => log.clear());

  let validateTimer = 0;
  ui.policyEditor.addEventListener('input', () => {
    clearTimeout(validateTimer);
    validateTimer = setTimeout(validatePolicyEditor, 120);
  });
  ui.policyApply.addEventListener('click', () => {
    if (state.scenarioActive) return;
    clearTimeout(validateTimer);
    const refocus = document.activeElement === ui.policyApply;
    const text = ui.policyEditor.value;
    // Check first: an invalid edit must not stop the robot as a side effect.
    if (policyError(text)) {
      validatePolicyEditor();
    } else {
      abortMotion('정책 교체 — 진행 중인 동작을 멈춤');
      applyPolicy(text, '편집한 정책 적용');
    }
    // The button disables itself (no longer dirty, or invalid): keep focus on the result.
    if (refocus && ui.policyApply.disabled) ui.policyStatus.focus();
  });
  ui.policyRevert.addEventListener('click', () => {
    ui.policyEditor.value = state.examplePolicyText;
    validatePolicyEditor();
  });

  ui.scenarioStart.addEventListener('click', () => player.start());
  ui.scenarioPause.addEventListener('click', () => (player.paused ? player.resume() : player.pause()));
  ui.scenarioNext.addEventListener('click', () => player.next());
  ui.scenarioStop.addEventListener('click', () => player.stop());

  bindMapKeyboard();
}

boot().catch((e) => fatal(`예상치 못한 오류: ${e?.message ?? e}`));
