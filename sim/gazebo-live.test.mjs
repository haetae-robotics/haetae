import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
const { validTelemetry } = await import('data:text/javascript,' + encodeURIComponent(
  readFileSync(new URL('./telemetry.js', import.meta.url), 'utf8')));

// Exercise the live controls without needing WebGL or a running robot.
const source = readFileSync(new URL('./gazebo-live.js', import.meta.url), 'utf8')
  .replace(/^import .*;\n/gm, '');

function viewer(rigFactory = () => ({ update() {}, setBlocked() {},setHazardScene() {} })) {
  const elements = new Map();
  const requests = [];
  let stream;
  let finishConfig;
  let finishAdvance;
  let tick;
  let now = 10000;
  const element = () => ({
    textContent: '', hidden: false, disabled: false, dataset: {}, style: {}, children: [], listeners: {},
    setAttribute() {}, append() {},
    prepend(child) { this.children.unshift(child); },
    get lastChild() { return { remove: () => this.children.pop() }; },
    addEventListener(name, handler) { this.listeners[name] = handler; }
  });
  const get = (id) => {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  };
  vm.runInNewContext(source, {
    document: { querySelector: get, getElementById: get, createElement: element, createTextNode: (text) => text },
    window: { location: { hostname: '127.0.0.1' } },
    validTelemetry,
    createGazeboScene: rigFactory,
    Date: { now: () => now },
    setInterval(callback) { tick = callback; },
    EventSource: class { constructor() { stream = this; } close() {} },
    fetch(url, options) {
      requests.push({ url, options });
      if (url === '/viewer-config') return new Promise((resolve) => { finishConfig = resolve; });
      if (url === '/advance') return new Promise((resolve) => { finishAdvance = resolve; });
      return Promise.resolve({ ok: true });
    }
  });
  stream.onopen();
  return {
    get, requests,
    emit: (row) => stream.onmessage({ data: JSON.stringify(row) }),
    disconnect: () => stream.onerror(),
    tick: () => { now += 4000; tick(); },
    config: async (options = {}) => {
      finishConfig({ json: () => ({ step_through: true, manual_start: true, attack_probes: true, secured_gazebo: true, ...options }) });
      await new Promise(setImmediate);
    },
    advance: () => finishAdvance({ ok: true })
  };
}

test('reference controls remain available when WebGL or robot assets fail', async () => {
  for (const rig of [() => {throw new Error('WebGL unavailable');},
    () => ({ready: Promise.reject(new Error('model unavailable'))})]) {
    const page = viewer(rig);
    await page.config({household_hazards: false, gazebo_gui: false});
    page.emit({kind: 'phase', label: '3D 화면 준비 · 시작 버튼을 누르세요'});
    assert.equal(page.get('start-simulation').disabled, false);
    assert.match(page.get('start-simulation').textContent, /시뮬레이션 시작/);
  }
});

test('refresh restores a waiting scene even when configuration arrives later', async () => {
  const page = viewer();
  page.emit({ kind: 'checkpoint', waiting: true, token: 'wheel',
    label: '바퀴 정지 장면', detail: '사람 표시 유지', next_label: '팔 시험' });
  await page.config();
  assert.equal(page.get('detail').textContent, '사람 표시 유지');
  assert.equal(page.get('start-simulation').disabled, false);
  assert.equal(page.get('scene-hold').hidden, false);
  assert.match(page.get('start-simulation').textContent, /다음 단계/);
});

test('connection loss is not overwritten by a measurement delay', async () => {
  const page = viewer(); await page.config();
  page.emit({ kind: 'telemetry', sim_ms: 1000, x: 5, y: 5, speed: 0, joint: 0, humans: [] });
  page.disconnect();
  page.tick();
  assert.match(page.get('connection').textContent, /연결 끊김/);
  assert.equal(page.get('start-simulation').disabled, true);
});

test('terminal error survives stale timers and delayed configuration', async () => {
  const page = viewer();
  page.emit({ kind: 'telemetry', sim_ms: 1000, x: 5, y: 5, speed: 0, joint: 0, humans: [] });
  page.emit({ kind: 'checkpoint', waiting: true, token: 'wheel', next_label: '팔 시험' });
  page.emit({ kind: 'error', label: '시뮬레이션이 중단됐습니다', detail: '장면 확인 시간 초과' });
  await page.config();
  page.tick();
  page.disconnect();
  assert.equal(page.get('connection').textContent, '시뮬레이션 중단');
  assert.equal(page.get('detail').textContent, '장면 확인 시간 초과');
  assert.equal(page.get('start-simulation').disabled, true);
  assert.equal(page.get('scene-hold').hidden, true);
  assert.equal(page.get('stage-label').textContent, '실험 중단');
});

test('next sends the current token and a delayed response cannot clear a new checkpoint', async () => {
  const page = viewer();
  await page.config();
  page.emit({ kind: 'phase', label: '3D 화면 준비 · 시작 버튼을 누르세요' });
  await page.get('start-simulation').listeners.click();
  assert.equal(page.requests.at(-1).url, '/start');
  assert.equal(page.get('start-simulation').disabled, true);
  page.emit({ kind: 'checkpoint', waiting: true, token: 'wheel', next_label: '팔 시험' });
  const request = page.get('start-simulation').listeners.click();
  assert.equal(page.requests.at(-1).url, '/advance');
  assert.equal(page.requests.at(-1).options.headers['X-Checkpoint-Token'], 'wheel');
  assert.equal(page.get('start-simulation').disabled, true);
  page.emit({ kind: 'checkpoint', waiting: false, token: 'wheel' });
  page.emit({ kind: 'checkpoint', waiting: true, token: 'arm', next_label: '공격 시험' });
  page.advance();
  await request;
  assert.equal(page.get('start-simulation').disabled, false);
  assert.match(page.get('start-simulation').textContent, /공격 시험/);
  assert.equal(page.get('scene-hold').hidden, false);
  page.emit({ kind: 'result', result: { ok: true, arm_out_of_bounds_denied: true,
    sillok_incident_snapshot_fully_sealed: true } });
  assert.equal(page.get('start-simulation').disabled, true);
  assert.equal(page.get('scene-hold').hidden, true);
});

test('progress follows secure, isolated and baseline runner sequences', async () => {
  for (const [config, sequence] of [
    [{ attack_probes: true, secured_gazebo: true }, [1, 2, 3, 4, 5, 6]],
    [{ attack_probes: true, secured_gazebo: false }, [1, 2, 3, 5, 4, 6]],
    [{ attack_probes: false }, [1, 2, 3, 5]]
  ]) {
    const page = viewer(); await page.config(config);
    const phases = { 1: 'AI 바퀴 이동 명령', 2: 'AI 팔 범위 초과 명령',
      3: 'AI 정상 팔 이동 명령', 4: '외부 노드 바퀴 명령 공격',
      5: '두 번째 바퀴 이동', 6: '서명된 명령 재전송 공격' };
    for (const [position, stage] of sequence.entries()) {
      page.emit({ kind: 'phase', label: phases[stage] });
      assert.match(page.get('stage-label').textContent, new RegExp(`^${position + 1} / ${sequence.length}`));
      assert.equal(page.get('stage-' + stage).dataset.state, 'active');
      assert.equal(page.get('stage-' + stage).style.order, position);
    }
    assert.equal(page.get('stage-4').hidden, !config.attack_probes);
    assert.equal(page.get('stage-6').hidden, !config.attack_probes);
    page.emit({ kind: 'result', result: {} });
    for (const stage of sequence) assert.equal(page.get('stage-' + stage).dataset.state, 'visited');
  }
});


test('terminal badge follows the report even when raw summary flags claim success', async () => {
  const page = viewer(); await page.config();
  page.emit({ kind: 'result', report_status: 'incomplete', result: {
    ok: true, arm_out_of_bounds_denied: true, sillok_incident_snapshot_fully_sealed: true } });
  assert.equal(page.get('connection').textContent, '실험 결과 확인');
});

test('signature failure survives later world ACL success in either event order', async () => {
  for (const order of [['signature', 'world'], ['world', 'signature']]) {
    const page = viewer(); await page.config();
    for (const attack of order) page.emit({kind: 'attack_result', attack, blocked: attack === 'world'});
    assert.equal(page.get('attack-world').dataset.state, 'failed');
  }
});

test('invalid telemetry leaves last good measurements intact and finite recovery works', async () => {
  const page = viewer(); await page.config();
  const row = {kind: 'telemetry', sim_ms: 1000, x: 5, y: 5, speed: .1, joint: 0, humans: []};
  page.emit(row);
  const speed = page.get('speed').textContent;
  page.emit({...row, speed: null, x: null});
  assert.equal(page.get('speed').textContent, speed);
  assert.match(page.get('connection').textContent, /측정값 오류/);
  page.emit({...row, speed: .2});
  assert.match(page.get('speed').textContent, /0.200/);
});


test('household profile keeps all six stages without legacy attack probes', async () => {
  const page=viewer(); await page.config({household_hazards:true,attack_probes:false});
  page.emit({kind:'hazard_scene',stage:4,title:'배터리와 물',case:'water',contents:[]});
  assert.equal(page.get('stage-label').textContent,'4 / 6 · 물');
  assert.equal(page.get('hazard-fixtures').hidden,false);
  page.emit({kind:'hazard_decision',allowed:false,label:'배터리 차단'});
  assert.equal(page.get('verdict').textContent,'차단 · 계획 미전달');
  page.emit({kind:'checkpoint',waiting:true,token:'water',label:'물 · 차단',detail:'정지',next_label:'정상 동작 대조'});
  assert.equal(page.get('start-simulation').disabled,false);
  assert.match(page.get('start-simulation').textContent,/정상 동작 대조/);
});
