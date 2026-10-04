// ⚙ 실험실 — the advanced controls, tucked away in a drawer.
//
// The lab has its own gate (labGate), seeded from the world on screen. The
// aim preview is a real labGate.judge() call; sending a command hands the
// proposal to the director, which judges it at t=1250 with the same gate and
// plays the full beat timeline. JS never decides a verdict here either.

import { Gate, policyError } from './engine.js';
import { Stage } from './stage.js';
import { MODE_KO, num, pt } from './explain.js';
import { VERDICT_UI } from './copy.js';

const $ = (id) => document.getElementById(id);
const PREVIEW_MS = 120;
const clone = (o) => JSON.parse(JSON.stringify(o));

export class Lab {
  constructor(app) {
    this.app = app;
    this.active = false;
    this.gate = null;
    this.world = null;
    this.clock = 0;
    this.nextId = 1;
    this.busy = false;
    this.pointer = null;
    this.cursor = null;
    this.cursorOn = false;
    this.grabbed = null;
    this.previewAt = 0;
    this.dlg = $('lab');
    this.bindUi();
  }

  // ───────────────────────── Open / close ─────────────────────────

  open(tab) {
    const app = this.app;
    const dir = app.director;
    if (!this.active) {
      const seed = clone(dir.scene.world);
      seed.robot.pose = { ...dir.scene.robot.pose };
      seed.robot.holding = dir.scene.robot.holding ?? null;
      const missionMode = app.missionGate.mode;
      dir.enterLab();
      try {
        this.gate?.free();
        this.gate = new Gate(app.policyText());
      } catch (e) {
        app.toast(`실험실 게이트를 만들지 못했습니다: ${e.message}`);
        return;
      }
      if (missionMode !== 'normal') this.gate.raise(missionMode);
      this.clock = seed.stamp_ms;
      this.world = seed;
      this.world.stamp_ms = this.clock;
      this.active = true;
      this.busy = false;
      dir.scene.world = this.world;
      dir.scene.robot.pose = { ...seed.robot.pose };
      dir.scene.robot.holding = seed.robot.holding;
      dir.scene.lab = { cursor: null, preview: null, grabbed: null };
      dir.scene.mode = this.gate.mode;
      dir.updateLadder(false);
      this.applyCanvasRole();
      $('lab-chip').hidden = false;
      this.syncWorldUi();
      this.syncModeUi();
    }
    if (tab) this.selectTab(tab);
    if (!this.dlg.open) this.dlg.show();
    $('btn-lab').setAttribute('aria-expanded', 'true');
    this.dlg.querySelector('[role="tab"][aria-selected="true"]')?.focus();
  }

  close() {
    if (!this.active) return;
    this.active = false;
    this.dlg.close();
    $('btn-lab').setAttribute('aria-expanded', 'false');
    const c = $('map');
    c.setAttribute('role', 'img');
    c.removeAttribute('aria-roledescription');
    c.tabIndex = -1;
    this.app.stage.setLabMode(false);
    $('lab-chip').hidden = true;
    this.app.overlay.preview(null);
    this.app.director.scene.lab = null;
    this.app.director.showIntro();
    $('btn-lab').focus();
  }

  /** The map is an application while the lab is open (also after a stage swap). */
  applyCanvasRole() {
    const c = $('map');
    const three = this.app.stage.kind === '3d';
    c.setAttribute('role', 'application');
    c.setAttribute('aria-roledescription', three ? '모형 집' : '지도');
    c.setAttribute('aria-label', `실험실 ${three ? '모형 집' : '지도'}: 화살표로 커서 이동(Shift는 1 m${three ? ', 화면 방향 기준' : ''}), Enter로 명령 보내기, H로 사람 잡기, Esc로 놓기`);
    c.tabIndex = 0;
    this.app.stage.setLabMode(true);
  }

  // ───────────────────────── Clock & world ─────────────────────────

  tick(dt) {
    if (!this.active) return;
    this.clock += dt;
    const age = Number($('lab-age').value);
    this.world.stamp_ms = Math.floor(this.clock - age);
    if (performance.now() - this.previewAt > PREVIEW_MS) this.refreshPreview();
    const cs = $('lab-clock');
    if (cs) cs.textContent = `인식 나이 ${age} ms · 모드 ${MODE_KO[this.gate.mode]}`;
  }

  settings() {
    return {
      type: this.dlg.querySelector('input[name="lab-action"]:checked').value,
      object: $('lab-object').value,
      source: $('lab-source').value,
      speed: Number($('lab-speed').value),
    };
  }

  build(p, id) {
    const s = this.settings();
    let action;
    switch (s.type) {
      case 'grasp': action = { type: 'grasp', object: s.object, at: { x: p.x, y: p.y } }; break;
      case 'place': action = { type: 'place', at: { x: p.x, y: p.y } }; break;
      case 'stop': action = { type: 'stop' }; break;
      default: action = { type: 'move_to', goal: { x: p.x, y: p.y }, speed: s.speed };
    }
    return { id, source: s.source, timestamp_ms: Math.floor(this.clock), action };
  }

  /** Aim preview: what labGate WOULD decide here (real judge call, not recorded). */
  refreshPreview() {
    this.previewAt = performance.now();
    const target = this.pointer ?? (this.cursorOn ? this.cursor : null);
    const sc = this.app.director.scene;
    if (!this.active || this.busy || !target) {
      this.app.overlay.preview(null);
      if (sc.lab) sc.lab.preview = null;
      return;
    }
    const proposal = this.build(target, this.nextId);
    let d = null;
    try {
      d = this.gate.judge(proposal, this.world, Math.floor(this.clock));
    } catch {
      d = null;
    }
    this.lastPreview = d;
    this.app.overlay.preview(d, target);
    if (sc.lab) sc.lab.preview = proposal.action.type === 'stop' ? null : target;
  }

  commit(p) {
    if (!this.active) return;
    if (this.busy) { this.app.toast('로봇이 동작 중입니다. 끝난 뒤 다시 눌러 보세요.'); return; }
    const proposal = this.build(p, this.nextId++);
    this.send(proposal);
  }

  send(proposal) {
    this.busy = true;
    this.app.overlay.preview(null);
    const sc = this.app.director.scene;
    if (sc.lab) sc.lab.preview = null;
    const snapshot = clone(this.world);
    const now = Math.floor(this.clock);
    this.app.director.playLabBeat({
      proposal,
      world: snapshot,
      now,
      gate: this.gate,
      onDone: (decision, end) => {
        this.busy = false;
        if (!this.active) return;
        if (decision) {
          this.world.robot.pose = { ...end.pose };
          this.world.robot.holding = end.holding ?? null;
          $('lab-holding').value = this.world.robot.holding ?? '';
        }
        sc.world = this.world;
        this.syncModeUi();
      },
    });
  }

  addHuman(cls) {
    const prefix = cls === 'child' ? 'kid' : 'adult';
    let n = 1;
    while (this.world.humans.some((h) => h.id === `${prefix}${n}`)) n++;
    const k = (n - 1) % 6;
    const pos = { x: 3.5 + k * 0.6, y: cls === 'child' ? 4.4 : 5 };
    this.world.humans.push({ id: `${prefix}${n}`, class: cls, pos });
    this.app.log.add({ t: this.clock, kind: 'world', text: `${cls === 'child' ? '아이' : '어른'} ${prefix}${n} 추가 ${pt(pos)} — 끌어서 옮기세요` });
    this.syncWorldUi();
  }

  removeHuman() {
    const h = this.world.humans.pop();
    if (h) this.app.log.add({ t: this.clock, kind: 'world', text: `${h.id} 제거` });
    this.syncWorldUi();
  }

  raise(mode) {
    const before = this.gate.mode;
    const changed = this.gate.raise(mode);
    const out = this.dlg.querySelector(`[data-raise="${mode}"] + .raise-out`);
    if (out) out.textContent = changed ? '변경됨' : '이미 그 이상';
    this.app.log.add({ t: this.clock, kind: 'fault', text: `수동: ${mode} 요청 → ${changed ? `${before} → ${this.gate.mode}` : `변화 없음 (${this.gate.mode})`}` });
    this.syncModeUi();
  }

  syncModeUi() {
    const dir = this.app.director;
    dir.scene.mode = this.gate.mode;
    dir.updateLadder(false);
    $('lab-mode').textContent = `지금 모드: ${MODE_KO[this.gate.mode]} (${this.gate.mode})`;
  }

  syncWorldUi() {
    $('lab-holding').value = this.world.robot.holding ?? '';
    $('lab-confidence').value = this.world.confidence;
    $('lab-confidence-out').textContent = `${Math.round(this.world.confidence * 100)}%`;
    $('lab-humans').textContent = this.world.humans.map((h) => `${h.class === 'child' ? '아이' : '어른'} ${pt(h.pos)}`).join(', ') || '없음';
  }

  selectTab(name) {
    for (const t of this.dlg.querySelectorAll('[role="tab"]')) {
      const on = t.dataset.tab === name;
      t.setAttribute('aria-selected', String(on));
      t.tabIndex = on ? 0 : -1;
      $(t.getAttribute('aria-controls')).hidden = !on;
    }
  }

  // ───────────────────────── Map input ─────────────────────────

  pointerHandlers() {
    return {
      active: () => this.active,
      onPointer: (p) => { this.pointer = p; this.refreshPreview(); },
      onCommit: (p) => this.commit(p),
      onHumanMove: (id, p) => {
        const h = this.world.humans.find((x) => x.id === id);
        if (h) h.pos = p;
        this.refreshPreview();
      },
      onHumanDrop: (id) => {
        const h = this.world.humans.find((x) => x.id === id);
        if (h) this.app.log.add({ t: this.clock, kind: 'world', text: `${h.id} → ${pt(h.pos)}` });
        this.syncWorldUi();
      },
      getHumans: () => (this.active ? this.world.humans : []),
      onInactiveClick: () => this.app.toastOnce('map', '공격을 골라 보세요. 직접 명령은 ⚙ 실험실에서 할 수 있어요.'),
    };
  }

  bindMapKeys() {
    const c = $('map');
    if (this.boundCanvas === c) return;
    this.boundCanvas = c;
    const sc = () => this.app.director.scene;
    const show = () => {
      this.cursorOn = true;
      const p = sc().robot.pose;
      this.cursor ??= Stage.snap({ x: Math.min(10, p.x + 1), y: Math.min(10, p.y + 1) });
      if (sc().lab) sc().lab.cursor = this.cursor;
      this.refreshPreview();
    };
    c.addEventListener('focus', () => { if (this.active && c.matches(':focus-visible')) show(); });
    c.addEventListener('blur', () => {
      this.cursorOn = false;
      this.grabbed = null;
      if (sc().lab) { sc().lab.cursor = null; sc().lab.grabbed = null; }
      this.refreshPreview();
    });
    c.addEventListener('keydown', (e) => {
      if (!this.active) return;
      if (!this.cursorOn) show();
      const step = e.shiftKey ? 1 : 0.25;
      const stage = this.app.stage;
      // Camera-relative in 3D ("up" is the world axis closest to the view direction).
      const ax = stage.axisFor(e.key);
      const delta = ax ? [ax.x * step, ax.y * step] : null;
      if (delta) {
        e.preventDefault();
        e.stopPropagation();
        const g = this.world.humans.find((h) => h.id === this.grabbed);
        if (g) {
          g.pos = Stage.snap(stage.clamp({ x: g.pos.x + delta[0], y: g.pos.y + delta[1] }));
          this.cursor = { ...g.pos };
        } else {
          this.cursor = Stage.snap(stage.clamp({ x: this.cursor.x + delta[0], y: this.cursor.y + delta[1] }));
        }
        sc().lab.cursor = this.cursor;
        this.refreshPreview();
        this.announceCursor();
      } else if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        e.stopPropagation();
        if (this.grabbed) this.release(); else this.commit(this.cursor);
      } else if (e.code === 'KeyH') {
        e.preventDefault();
        e.stopPropagation();
        const hs = this.world.humans;
        const i = hs.findIndex((h) => h.id === this.grabbed);
        const next = hs[i + 1];
        this.grabbed = next?.id ?? null;
        sc().lab.grabbed = this.grabbed;
        if (next) { this.cursor = { ...next.pos }; this.app.announce(`${next.class === 'child' ? '아이' : '어른'} 잡음. 화살표로 옮기고 Enter로 놓기.`); }
        else this.app.announce('사람을 놓았습니다.');
      } else if (e.key === 'Escape' && this.grabbed) {
        e.stopPropagation();
        this.release();
      }
    });
  }

  release() {
    this.grabbed = null;
    this.app.director.scene.lab.grabbed = null;
    this.syncWorldUi();
    this.app.announce('사람을 놓았습니다.');
  }

  announceCursor() {
    clearTimeout(this.curTimer);
    this.curTimer = setTimeout(() => {
      if (!this.cursorOn) return;
      let t = `커서 ${pt(this.cursor)}`;
      const d = this.lastPreview;
      if (d) t += ` · 미리보기 ${VERDICT_UI[d.verdict].word}${d.fired[0] ? ` · ${d.fired[0]}` : ''}`;
      $('cursor-live').textContent = t;
    }, 250);
  }

  // ───────────────────────── Drawer UI ─────────────────────────

  bindUi() {
    const app = this.app;
    $('lab-close').addEventListener('click', () => this.close());
    this.dlg.addEventListener('cancel', (e) => { e.preventDefault(); this.close(); });
    this.dlg.addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.preventDefault(); this.close(); } });
    $('lab-collapse').addEventListener('click', () => {
      const on = this.dlg.classList.toggle('collapsed');
      $('lab-collapse').setAttribute('aria-expanded', String(!on));
      $('lab-collapse').textContent = on ? '서랍 펼치기 ▴' : '지도 보기 ▾';
    });

    const tabs = [...this.dlg.querySelectorAll('[role="tab"]')];
    tabs.forEach((t, i) => {
      t.addEventListener('click', () => this.selectTab(t.dataset.tab));
      t.addEventListener('keydown', (e) => {
        const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
        if (!d) return;
        e.preventDefault();
        const n = tabs[(i + d + tabs.length) % tabs.length];
        this.selectTab(n.dataset.tab);
        n.focus();
      });
    });

    const onSettings = () => {
      const s = this.settings();
      $('lab-object').disabled = s.type !== 'grasp';
      $('lab-speed').disabled = s.type !== 'move_to';
      $('lab-speed-out').textContent = `${num(s.speed)} m/s`;
      this.refreshPreview();
    };
    this.dlg.querySelectorAll('input[name="lab-action"]').forEach((r) => r.addEventListener('change', onSettings));
    ['lab-object', 'lab-source', 'lab-speed'].forEach((id) => $(id).addEventListener('input', onSettings));
    onSettings();

    $('lab-holding').addEventListener('change', () => {
      this.world.robot.holding = $('lab-holding').value || null;
      this.app.director.scene.robot.holding = this.world.robot.holding;
      this.app.log.add({ t: this.clock, kind: 'world', text: `들고 있는 물건 → ${this.world.robot.holding ?? '없음'}` });
    });
    $('lab-confidence').addEventListener('input', () => {
      this.world.confidence = Number($('lab-confidence').value);
      $('lab-confidence-out').textContent = `${Math.round(this.world.confidence * 100)}%`;
    });
    $('lab-age').addEventListener('input', () => { $('lab-age-out').textContent = `${$('lab-age').value} ms`; });
    $('lab-add-child').addEventListener('click', () => this.addHuman('child'));
    $('lab-add-adult').addEventListener('click', () => this.addHuman('adult'));
    $('lab-remove').addEventListener('click', () => this.removeHuman());
    this.dlg.querySelectorAll('[data-raise]').forEach((b) => b.addEventListener('click', () => this.raise(b.dataset.raise)));
    $('lab-reset').addEventListener('click', () => {
      this.gate.reset();
      this.dlg.querySelectorAll('.raise-out').forEach((o) => { o.textContent = ''; });
      this.app.log.add({ t: this.clock, kind: 'mode', text: `운영자 리셋 → ${this.gate.mode}` });
      this.syncModeUi();
    });
    $('lab-stop').addEventListener('click', () => {
      if (this.busy) { this.app.toast('동작이 끝난 뒤 보내 주세요.'); return; }
      this.send({ id: this.nextId++, source: $('lab-source').value, timestamp_ms: Math.floor(this.clock), action: { type: 'stop' } });
    });

    // Policy
    const ed = $('lab-policy');
    let vt = 0;
    ed.addEventListener('input', () => { clearTimeout(vt); vt = setTimeout(() => this.validatePolicy(), 120); });
    $('lab-apply').addEventListener('click', () => {
      const text = ed.value;
      const err = policyError(text);
      if (err) { this.validatePolicy(); return; }
      // A command still animating was captured with the current gate: let it
      // finish (judge + execute, instantly) before that gate is freed.
      if (this.busy) app.director.tl.jump(1e9);
      if (app.applyPolicy(text)) {
        try {
          const g = new Gate(text);
          if (this.gate && this.gate.mode !== 'normal') g.raise(this.gate.mode);
          this.gate?.free();
          this.gate = g;
        } catch (e) { app.toast(e.message); }
        this.syncModeUi();
        this.validatePolicy();
        this.app.announce('정책을 적용했습니다. 현재 모드를 유지합니다.');
      }
    });
    $('lab-revert').addEventListener('click', () => { ed.value = app.defaultPolicyText; this.validatePolicy(); });

    // Log
    $('lab-copy').addEventListener('click', async () => {
      const text = app.log.jsonl();
      try { await navigator.clipboard.writeText(text); app.toast('JSONL을 복사했습니다.'); } catch { app.toast('복사하지 못했습니다 (브라우저 권한).'); }
    });
    $('lab-clear').addEventListener('click', () => app.log.clear());
    $('lab-stream-play').addEventListener('click', () => {
      const text = $('lab-stream').value.trim();
      if (!text) { app.toast('JSONL 스트림을 붙여 넣거나 파일을 고르세요.'); return; }
      this.close();
      app.playStream(text);
    });
    $('lab-stream-file').addEventListener('change', async (e) => {
      const f = e.target.files?.[0];
      if (f) $('lab-stream').value = await f.text();
    });
  }

  validatePolicy() {
    const ed = $('lab-policy');
    const err = policyError(ed.value);
    const st = $('lab-policy-status');
    const dirty = ed.value !== this.app.policyText();
    if (err) {
      st.className = 'policy-status err';
      st.textContent = `✕ ${err}`;
      ed.setAttribute('aria-invalid', 'true');
      $('lab-apply').disabled = true;
    } else {
      st.className = 'policy-status ok';
      st.textContent = dirty ? '✓ 유효한 정책 — “적용”을 누르면 반영됩니다' : '✓ 지금 적용된 정책';
      ed.setAttribute('aria-invalid', 'false');
      $('lab-apply').disabled = !dirty;
    }
  }
}

