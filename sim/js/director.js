// The director: card → beats → end card, the §3f beat timeline, the attract
// teaser, the unfiltered comparison and the card-8 stream adapter.
//
// HARD RULE. This is the only card-mode caller of gate.judge(). Every
// verdict, fired list, clamped action and speed cap it shows comes from the
// frozen Decision that judge() returned. Narration never predicts; every
// post-verdict string is keyed by the returned verdict or fired names.

import { CARDS, TEASER_BEAT } from './cards.js';
import {
  VERDICT_UI, verdictCopy, verdictHtml, verdictIcon, sentence, label, sortedFired, sourceName, commandSpoken,
  actionText, outroFor, GENERIC_OUTRO, STRICT, badgeKind, KIND_BADGE, capLimit, actionTarget, zoneName,
} from './copy.js';
import { sealAnchor, highlightsFor, setChips, setLadder, setProgress } from './overlay.js';
import { playUnfiltered } from './ghost.js';
import { ScenarioPlayer, PROPOSAL_CAPTIONS } from './scenario.js';
import { MODE_KO, num, pt } from './explain.js';
import { dist, robotPose, DECOR } from './model.js';

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

const T_FULL = { slip: 300, path: 750, pathDur: 500, judge: 1250, seal: 1550, hl: 1750, cap: 1900, exec: 2300, maxMotion: 3500 };
const T_REDUCED = { slip: 0, path: 0, pathDur: 0, judge: 250, seal: 250, hl: 250, cap: 250, exec: 250, maxMotion: 0 };
const T_STREAM = { slip: 0, path: 150, pathDur: 300, judge: 0, seal: 900, hl: 1000, cap: 1000, exec: 1250, maxMotion: 2500 };
const T_STREAM_REDUCED = { ...T_REDUCED, judge: 0 };
const DWELL = { yun: 2500, jeol: 4500, bul: 4500, error: 4500 };
const BIG = 1e9;

function deepFreeze(o) {
  if (o && typeof o === 'object' && !Object.isFrozen(o)) {
    Object.freeze(o);
    for (const v of Object.values(o)) deepFreeze(v);
  }
  return o;
}

const clone = (o) => JSON.parse(JSON.stringify(o));

/** Pausable event timeline in ms since beat start. */
class Timeline {
  constructor() { this.t = 0; this.et = 0; this.events = []; }
  at(t, fn) { this.events.push({ t, fn }); }
  tick(dt) { this.t += dt; this.flush(); }
  jump(t) { this.t = Math.max(this.t, t); this.flush(); }
  flush() {
    for (;;) {
      let idx = -1;
      for (let i = 0; i < this.events.length; i++) {
        if (this.events[i].t <= this.t && (idx < 0 || this.events[i].t < this.events[idx].t)) idx = i;
      }
      if (idx < 0) return;
      const [e] = this.events.splice(idx, 1);
      this.et = e.t; // the event's own time, so a skipped beat lands in its end state
      e.fn();
    }
  }
}

export class Director {
  /**
   * @param {object} app { stage, overlay, strip, log, gate(), policy(), policyText(), prefs,
   *   announce(t), toast(t), openDetail(), openLab(), watched: Map, saveWatched(), kiosk, policyModified() }
   */
  constructor(app) {
    this.app = app;
    this.stage = app.stage;
    this.overlay = app.overlay;
    this.strip = app.strip;
    this.tl = new Timeline();
    this.mode = 'intro';
    this.card = null;
    this.bi = 0;
    this.phase = 'idle';
    this.recs = [];
    this.transcript = [];
    this.lastRec = null;
    this.paused = false;
    this.block = false; // pointer/focus inside the caption/transport
    this.dwell = null;
    this.caption = { html: '', cmd: '' };
    const w0 = CARDS[0].beats[0].world;
    this.scene = {
      policy: null,
      world: clone(w0),
      robot: { pose: { ...w0.robot.pose }, holding: w0.robot.holding, heading: Math.PI * 0.6 },
      mode: 'normal',
      focus: null,
      decor: {},
      intent: null,
      exec: null,
      barrier: null,
      hl: [],
      ghost: null,
      realAlpha: 1,
      lab: null,
      reduced: false,
      fx: null, // { decision (frozen), anchor, t0, head, from, target }: the verdict moment
      trail: [], // earlier barriers of this card
      peer: false,
      signal: null,
    };
    this.last = performance.now();
    // rAF stops in a hidden tab, so the frame loop cannot notice; hold the player here too.
    document.addEventListener('visibilitychange', () => {
      this.holdPlayer(document.hidden || !!document.querySelector('dialog[open].modal'));
    });
  }

  get reduced() { return this.app.prefs.reduced; }
  get gate() { return this.app.gate(); }
  get policy() { return this.app.policy(); }

  // ───────────────────────── Frame loop ─────────────────────────

  frame(real) {
    const dt = Math.min(100, Math.max(0, real - this.last));
    this.last = real;
    this.scene.reduced = this.reduced;
    this.scene.policy = this.policy;
    const modal = !!document.querySelector('dialog[open].modal');
    const hold = this.paused || modal;
    this.holdPlayer(modal || document.hidden);
    if (!hold) this.tl.tick(dt);
    this.tickDwell(dt, hold || document.hidden);
    this.stage.render(this.scene, this.tl.t, real);
  }

  /** The stream player runs on timers; hold it with the timeline (modal sheet, hidden tab). */
  holdPlayer(on) {
    const p = this.player;
    if (!p || this.mode !== 'stream') { this.playerHeld = false; return; }
    if (on && !this.playerHeld && p.running && !p.paused) {
      this.playerHeld = true;
      p.pause();
    } else if (!on && this.playerHeld) {
      this.playerHeld = false;
      if (p.running && p.paused) p.resume();
    }
  }

  tickDwell(dt, hold) {
    const d = this.dwell;
    const btn = $('btn-primary');
    if (!d) { btn.style.removeProperty('--p'); btn.classList.remove('counting'); return; }
    const auto = this.app.kiosk || this.app.prefs.auto;
    if (!auto) { btn.classList.remove('counting'); return; }
    btn.classList.add('counting');
    if (!hold && !this.block) d.left -= dt;
    btn.style.setProperty('--p', String(Math.max(0, Math.min(1, 1 - d.left / d.total))));
    if (d.left <= 0) {
      this.dwell = null;
      btn.classList.remove('counting');
      d.fn();
    }
  }

  startDwell(ms, fn) {
    this.dwell = { left: ms, total: ms, fn };
  }

  // ───────────────────────── Intro and teaser ─────────────────────────

  showIntro() {
    this.stopAll();
    this.mode = 'intro';
    this.card = null;
    document.body.dataset.state = 'intro';
    $('intro').hidden = false;
    this.stage.resetView(); // back to the default framing (clear of the camera toolbar)
    this.setWorld(CARDS[0].beats[0].world, false);
    this.scene.decor = {};
    this.scene.focus = null;
    this.strip.idle();
    this.setTitle(null);
    setProgress({ total: 0 });
    this.updateLadder(false);
    this.chipsFor(null, null);
    this.renderRail();
    this.updateTransport();
  }

  /** Attract teaser: a REAL judge() call on a throwaway gate, card 1 beat 1 data. */
  teaser(makeGate) {
    const b = TEASER_BEAT;
    const tl = this.tl = new Timeline();
    const sc = this.scene;
    const reduced = this.reduced;
    const t0 = reduced ? 0 : 400;
    tl.at(t0, () => {
      if (this.mode !== 'intro') return;
      sc.intent = { from: b.world.robot.pose, to: actionTarget(b.proposal.action), t0: tl.et, dur: reduced ? 0 : 600, speed: b.proposal.action.speed };
    });
    tl.at(t0 + (reduced ? 0 : 800), () => {
      if (this.mode !== 'intro') return;
      let d;
      let g;
      try {
        g = makeGate();
        d = deepFreeze(g.judge(b.proposal, b.world, b.now));
      } catch {
        sc.intent = null;
        return; // skipped silently
      } finally {
        try { g?.free(); } catch { /* ignore */ }
      }
      const copy = verdictCopy(d, this.ctx({ proposal: b.proposal, world: b.world, now: b.now, decision: d }));
      const anchor = sealAnchor(d, b.proposal, b.world, this.policy, copy.head);
      sc.fx = { decision: d, anchor, t0: tl.et, head: copy.head, from: b.world.robot.pose, target: actionTarget(b.proposal.action) };
      this.overlay.landSeal(d, anchor, { reduced, tag: '실제 판정' });
      if (d.verdict === 'bul' && anchor.type === 'world') {
        sc.barrier = { at: anchor.p, dir: anchor.dir, t0: tl.et };
        sc.intent.crack = { at: anchor.p, t0: tl.et };
        if (!reduced) sc.robot.reactT = performance.now();
      }
      this.overlay.showCallout(d.verdict, copy.label, copy.others, {
        onDetail: () => {}, prefer: 'left', robot: b.world.robot.pose, humans: b.world.humans.map((h) => h.pos),
        targets: [actionTarget(b.proposal.action), actionTarget(d.action)],
      });
      this.teaserDone = true;
    });
    if (!reduced) {
      tl.at(3400, () => {
        if (this.mode !== 'intro') return;
        this.overlay.root.classList.add('fading');
        if (sc.intent) sc.intent.fadeT0 = tl.et;
        setTimeout(() => {
          if (this.mode !== 'intro') return;
          this.overlay.clearAll();
          this.overlay.root.classList.remove('fading');
          sc.intent = null;
          sc.barrier = null;
          sc.fx = null;
          sc.robot.reactT = null;
          $('intro-cta').classList.add('pulse');
        }, 400);
      });
    }
  }

  hideIntro() {
    $('intro').hidden = true;
    this.overlay.root.classList.remove('fading');
  }

  // ───────────────────────── Cards ─────────────────────────

  stopAll() {
    this.dwell = null;
    this.app.announceClear?.();
    if (this.player) {
      const p = this.player;
      this.player = null;
      p.stop(true);
    }
    if (this.mode === 'ghost') this.leaveGhostVisuals();
    this.tl = new Timeline();
    this.overlay.clearAll();
    $('endcard').hidden = true;
    this.fxReset();
    const sc = this.scene;
    sc.intent = null; sc.exec = null; sc.barrier = null; sc.hl = []; sc.ghost = null; sc.realAlpha = 1;
    sc.lab = null; sc.fx = null; sc.trail = []; sc.peer = false; sc.signal = null; sc.robot.reactT = null;
    $('speed-badge').hidden = true;
  }

  startCard(card, { focusStage = false } = {}) {
    if (!card) return;
    this.stopAll();
    this.hideIntro();
    this.card = card;
    this.recs = [];
    this.transcript = [];
    this.lastRec = null;
    this.paused = false;
    this.syncPause();
    try { this.gate.reset(); } catch { /* gate rebuilt elsewhere */ }
    this.scene.mode = this.gate.mode;
    this.scene.decor = {};
    this.mode = card.stream ? 'stream' : 'card';
    document.body.dataset.state = this.mode;
    this.setTitle(card);
    this.renderRail();
    this.stage.frameBeat({ phase: 'card' });
    if (focusStage && this.overlay.mobile) {
      $('stage').scrollIntoView({ behavior: this.reduced ? 'auto' : 'smooth', block: 'start' });
    }
    if (card.stream) this.startStream();
    else this.startBeat(0);
  }

  setTitle(card, bi = 0) {
    const t = $('stage-title');
    if (!card) { t.textContent = '해태가 막는다 · 공격을 골라 보세요'; return; }
    const n = String(card.num).padStart(2, '0');
    const pos = card.stream ? '' : ` · ${bi + 1}/${card.beats.length}`;
    t.textContent = `${n} ${card.title}${pos}`;
  }

  /** Start beat i of the current card. Beats before i replay instantly (real judge calls). */
  gotoBeat(i) {
    if (!this.card || this.card.stream) return;
    const card = this.card;
    const target = Math.max(0, Math.min(card.beats.length - 1, i));
    this.startCard(card);
    for (let j = 0; j < target; j++) {
      this.startBeat(j, { instant: true });
      this.tl.jump(BIG);
      this.dwell = null;
    }
    this.startBeat(target);
  }

  startBeat(i, { instant = false } = {}) {
    const card = this.card;
    const beat = card.beats[i];
    this.bi = i;
    this.phase = 'anim';
    this.dwell = null;
    this.setTitle(card, i);
    const rec = { beat, card, index: i, proposal: beat.proposal, world: beat.world, now: beat.now, decision: null };
    this.recs[i] = rec;
    this.recs.length = i + 1;
    this.transcript.length = i;
    setProgress({ total: card.beats.length, index: i, verdicts: this.recs.map((r) => r?.decision?.verdict) });
    this.scheduleBeat(rec, instant || this.reduced ? T_REDUCED : T_FULL, { fresh: true, instant });
    // Reset the stage for the new beat right away, even while paused, so the
    // previous beat's verdict never sits under the new beat's title.
    this.tl.flush();
    this.updateTransport();
  }

  ctx(rec) {
    return { policy: this.policy, proposal: rec.proposal, world: rec.world, now: rec.now, decision: rec.decision };
  }

  /** The §3f timeline for one proposal beat. */
  scheduleBeat(rec, T, opts) {
    const tl = this.tl = new Timeline();
    const sc = this.scene;
    const gate = opts.gate ?? this.gate;
    const beat = rec.beat ?? {};
    const card = rec.card;
    const p = rec.proposal;
    const w = rec.world;
    const target = actionTarget(p.action);

    tl.at(0, () => {
      this.overlay.toTrail();
      this.fxReset();
      // The previous beat's barrier sinks into the card's trail (3D form of the tag trail).
      if (sc.fx && sc.barrier) sc.trail = [...(sc.trail ?? []), { at: sc.barrier.at, dir: sc.barrier.dir }].slice(-3);
      sc.intent = null; sc.exec = null; sc.barrier = null; sc.hl = []; sc.ghost = null; sc.realAlpha = 1;
      sc.fx = null; sc.robot.reactT = null;
      sc.peer = p.source === 'peer';
      sc.signal = null;
      sc.focus = beat.focus ?? card?.focus ?? null;
      sc.focusT = performance.now();
      sc.decor = { ...(beat.decor ?? {}) };
      if (beat.decor?.fakeTagPeel) sc.decor.peelT = performance.now();
      if (beat.narr) this.setCaption(esc(beat.narr), '', 'narr');
      if (beat.narr && !opts.instant && !this.reduced) this.app.announce(beat.narr);
      if (opts.fresh && beat.fault) {
        const before = gate.mode;
        const changed = gate.raise(beat.fault.raise_to);
        sc.robot.glitchT = performance.now();
        this.app.log.add({ t: rec.now, kind: 'fault', text: `${beat.fault.code}: ${beat.fault.raise_to} 요청 → ${changed ? `${before} → ${gate.mode}` : `변화 없음 (${gate.mode})`}` });
      }
      sc.mode = gate.mode;
      this.updateLadder(!!beat.fault);
      this.setWorld(w, true);
      this.chipsFor(w, rec.now);
      this.strip.setSource(p.source, this.policy);
      this.strip.zigzag(p.source === 'peer');
      this.strip.set('idle');
      this.describeMap(w);
      if (!opts.instant && !opts.lab && !opts.stream) {
        const pts = [w.robot.pose, target];
        const f = sc.focus ?? [];
        for (const h of w.humans ?? []) if (f.includes(h.id)) pts.push(h.pos);
        if (f.includes('fakeTag') && sc.decor.fakeTag) pts.push(DECOR.fakeTag);
        if (sc.peer) pts.push({ x: 6.8, y: -0.9 });
        this.stage.frameBeat({ phase: 'beat', points: pts });
      }
    });

    tl.at(T.slip, () => {
      this.overlay.showSlip(p, {
        say: beat.say,
        tape: this.tapeFor(rec),
        robot: w.robot.pose,
        target,
        humans: w.humans ?? [],
        reduced: this.reduced || opts.instant,
        zigzag: p.source === 'peer',
        from: $('strip-src'),
      });
      this.strip.set('packet');
      sc.robot.slipT = performance.now();
      if (p.source === 'peer') sc.signal = { t0: tl.et };
      const cmd = `명령 · ${sourceName(p.source)} → ${actionText(p.action)}`;
      this.setCaption(this.caption.html, esc(cmd), this.caption.tone);
      if (!opts.instant && !this.reduced) this.app.announce(`명령: ${commandSpoken(p)}`);
    });

    tl.at(T.path, () => {
      if (target) {
        sc.intent = { from: w.robot.pose, to: target, t0: tl.et, dur: T.pathDur, speed: p.action.speed ?? null };
        sc.robot.heading = Math.atan2(target.y - w.robot.pose.y, target.x - w.robot.pose.x);
      }
    });

    tl.at(T.judge, () => {
      if (!rec.decision && !rec.error) {
        try {
          rec.decision = deepFreeze(gate.judge(p, w, rec.now));
          this.logDecision(rec);
        } catch (e) {
          rec.error = e?.message ?? String(e);
          this.app.log.add({ t: rec.now, kind: 'error', text: `판정 실패 (입력 오류): ${rec.error}` });
        }
      }
      sc.robot.badgeT = performance.now();
      this.stage.pulse('judge');
      this.strip.set('judge');
    });

    tl.at(T.seal, () => {
      if (rec.error) {
        this.setCaption('이 장면을 판정하지 못했습니다 (입력 오류)', this.caption.cmd, 'error');
        this.app.announce('이 장면을 판정하지 못했습니다 (입력 오류)');
        this.strip.set('idle');
        return;
      }
      this.revealSeal(rec, opts);
    });

    tl.at(T.hl, () => { if (rec.decision) this.revealHighlights(rec); });

    tl.at(T.cap, () => {
      if (!rec.decision) return;
      const c = rec.copy;
      const d = rec.decision;
      this.setCaption(`${verdictHtml(d.verdict)} · ${esc(c.sentence)}`, this.caption.cmd, d.verdict);
      if (!opts.instant) {
        const ui = VERDICT_UI[d.verdict];
        const verdictLine = `해태 판정: ${ui.word}. ${c.sentence}${c.others ? ` 함께 걸린 조건 ${c.others}개.` : ''}`;
        // Reduced motion: narration, command and verdict land together, so say them as one message.
        const lead = this.reduced ? `${beat.narr ? `${beat.narr} ` : ''}명령: ${commandSpoken(p)}. ` : '';
        this.app.announce(lead + verdictLine);
      }
      this.transcript[rec.index ?? this.transcript.length] = {
        narr: beat.narr ?? '',
        cmd: `${sourceName(p.source)} → ${actionText(p.action)}`,
        verdict: d.verdict,
        sentence: c.sentence,
      };
    });

    tl.at(T.exec, () => {
      const dur = rec.decision ? this.startExec(rec, T, tl.et, sc) : 0;
      if (sc.exec?.kind === 'move' && dur > 0 && !opts.instant && !opts.lab && !opts.stream) this.stage.frameBeat({ phase: 'exec', points: [sc.exec.from, sc.exec.to] });
      tl.at(tl.et + dur, () => this.execEnd(rec, opts));
    });
  }

  tapeFor(rec) {
    const ahead = rec.proposal.timestamp_ms - rec.now;
    const age = rec.now - rec.proposal.timestamp_ms;
    if (age >= 1000) return `◀◀ ${(age / 1000).toFixed(1)}초 전 명령`;
    if (ahead >= 1000) return `+${(ahead / 1000).toFixed(1)}초 뒤 시각`;
    return null;
  }

  revealSeal(rec, opts) {
    const sc = this.scene;
    const d = rec.decision;
    const reduced = this.reduced || opts.instant;
    rec.copy = verdictCopy(d, this.ctx(rec));
    const avoid = sc.decor?.fakeTag ? [DECOR.fakeTag] : [];
    const anchor = sealAnchor(d, rec.proposal, rec.world, this.policy, rec.copy.head, avoid);
    rec.anchor = anchor;
    // The verdict moment for the stage: a reference to the frozen Decision (drawing only).
    sc.fx = { decision: d, anchor, t0: this.tl.et, head: rec.copy.head, from: rec.world.robot.pose, target: actionTarget(rec.proposal.action) };
    this.overlay.landSeal(d, anchor, { reduced });
    this.keepPillClear(anchor);
    this.strip.verdict(d);
    if (d.verdict === 'bul') {
      if (anchor.type === 'world' && actionTarget(rec.proposal.action) && sc.intent) {
        sc.barrier = { at: anchor.p, dir: anchor.dir, t0: this.tl.et };
        sc.intent.crack = { at: anchor.p, t0: this.tl.et };
      } else if (sc.intent) {
        sc.intent.fadeT0 = this.tl.et; // the command itself never got past the gate
      }
      sc.robot.reactT = performance.now();
      this.stage.react('bul', anchor, reduced);
    }
    if (d.verdict === 'jeol' && d.action?.type === 'move_to' && d.action.speed !== rec.proposal.action.speed) {
      this.overlay.slipSpeedDiff(d.action.speed);
    }
    if (opts.stream) this.updateFilm();
    else if (rec.card && !rec.lab) {
      setProgress({ total: rec.card.beats.length, index: rec.index, verdicts: this.recs.map((r) => r?.decision?.verdict) });
    }
  }

  /** Slide the canvas "요청 X m/s" path pill out from under a world-anchored seal (placement only). */
  keepPillClear(anchor) {
    const it = this.scene.intent;
    if (!it || anchor.type !== 'world') return;
    const st = this.stage;
    const s = st.toScreen(anchor.p);
    const half = this.overlay.sealPx() / 2 + 4;
    const pw = 14 + 7 * `요청 ${num(it.speed ?? 0)} m/s`.length; // rough pill size at 11 px
    const A = st.toScreen(it.from);
    const B = st.toScreen(it.to);
    const at = (t) => ({ x: A.x + (B.x - A.x) * t, y: A.y + (B.y - A.y) * t - 14 });
    const clear = (p) => Math.abs(p.x - s.x) > pw / 2 + half || Math.abs(p.y - s.y) > 11 + half;
    const t = [0.3, 0.12, 0.55, 0.75, 0.9].find((k) => clear(at(k)));
    if (t == null) it.noPill = true; else it.pillT = t;
  }

  revealHighlights(rec) {
    const sc = this.scene;
    const d = rec.decision;
    const H = highlightsFor(d, rec.proposal, rec.world, this.policy, rec.now);
    sc.hl = H.canvas.map((h) => ({ ...h, t0: this.tl.et }));
    const dom = H.dom;
    if (dom.haze) $('fx-haze').classList.add('on');
    if (dom.mode) this.updateLadder(true);
    if (dom.speedChip && !this.overlay.mobile) {
      // Mobile: the slip is a tab and the canvas path pill already shows the request.
      const tg = actionTarget(rec.proposal.action);
      const side = tg && tg.x < rec.world.robot.pose.x ? -1 : 1;
      const it = this.overlay.worldChip(`요청 ${num(dom.speedChip.req)} → 한계 ${num(dom.speedChip.max)} m/s`, rec.world.robot.pose, 'intent', -34);
      it.dx = side * 80;
      this.overlay.layoutItem(it);
      if (sc.intent) sc.intent.noPill = true; // same requested speed, said once
    }
    if (dom.sourceStrike) {
      this.strip.strike();
      if (sc.signal) sc.signal.bounceT = this.tl.et; // the peer's signal bounces off the shield
    }
    if (dom.sourceNote) this.strip.logged();
    if (dom.tapeAlarm) this.overlay.slipTapeAlarm();
    if (dom.stale) {
      $('map').classList.add('desat');
      this.overlay.staleStamp();
    }
    this.chipsFor(rec.world, rec.now, { stale: !!dom.stale, confidence: dom.confidence });
    let brake = null;
    if (dom.brakeStack) {
      brake = dom.brakeStack.map((b) => ({ text: b.text, bold: d.speed_cap != null && Math.abs((capLimit(b.n, this.policy) ?? NaN) - d.speed_cap) < 1e-9 }));
    }
    this.overlay.showCallout(d.verdict, rec.copy.label, rec.copy.others, {
      brake,
      onDetail: () => this.app.openDetail(),
      robot: rec.world.robot.pose,
      humans: (rec.world.humans ?? []).map((h) => h.pos),
      // The path the model asked for and where the robot really ends up (decision.action).
      targets: [actionTarget(rec.proposal.action), actionTarget(d.action)],
    });
    this.lastRec = rec;
  }

  /** Execute decision.action (never the raw proposal). Returns the motion time in ms. */
  startExec(rec, T, t0, sc, alpha) {
    const d = rec.decision;
    const w = rec.world;
    const a = d.action;
    if (d.verdict === 'bul' || !a) return 0;
    const reduced = T.maxMotion === 0;
    const from = { ...w.robot.pose };
    let k = 1;
    let dur = 0;
    if (a.type === 'move_to') {
      const dd = dist(from, a.goal);
      if (a.speed > 0 && dd > 1e-6) {
        const raw = (dd / a.speed) * 1000;
        k = reduced ? 1 : Math.max(1, raw / T.maxMotion);
        dur = reduced ? 0 : raw / k;
      }
      // A zero-speed move never gets anywhere: the robot stays where it is.
      const to = a.speed > 0 ? { ...a.goal } : { ...from };
      sc.exec = { kind: 'move', verdict: d.verdict, from, to, speed: a.speed, t0, dur, chevrons: d.verdict === 'jeol' };
      if (a.speed > 0) sc.robot.heading = Math.atan2(a.goal.y - from.y, a.goal.x - from.x);
    } else if (a.type === 'grasp' || a.type === 'place') {
      const cap = d.speed_cap;
      if (!(cap > 0)) return 0;
      const raw = Math.max(600, ((2 * dist(from, a.at)) / cap) * 1000);
      k = reduced ? 1 : Math.max(1, raw / T.maxMotion);
      dur = reduced ? 0 : raw / k;
      sc.exec = {
        kind: 'reach', verdict: d.verdict, from, at: { ...a.at }, t0, dur,
        holdBefore: w.robot.holding, holdAfter: a.type === 'grasp' ? a.object : null,
      };
    } else {
      sc.exec = { kind: 'stop', verdict: d.verdict, from, t0, dur: reduced ? 0 : 300 };
      dur = reduced ? 0 : 300;
    }
    if (alpha == null) {
      rec.k = k;
      const badge = $('speed-badge');
      badge.hidden = !(k > 1.05);
      badge.textContent = `×${k.toFixed(1)} 배속`;
    }
    return dur;
  }

  execEnd(rec, opts) {
    const sc = this.scene;
    const e = sc.exec;
    if (e?.kind === 'move') sc.robot.pose = { ...e.to };
    if (e?.kind === 'reach') sc.robot.holding = e.holdAfter;
    sc.robot.snap = null;
    $('speed-badge').hidden = true;
    this.phase = 'revealed';
    opts.onDone?.(rec.decision, { pose: { ...sc.robot.pose }, holding: sc.robot.holding });
    if (opts.stream) { rec.onDone?.(); return; }
    if (rec.lab) return;
    const v = rec.decision?.verdict ?? 'error';
    const last = this.bi >= this.card.beats.length - 1;
    this.startDwell(DWELL[v], () => (last ? this.showEnd() : this.startBeat(this.bi + 1)));
    this.updateTransport();
  }

  advance() {
    if (this.mode !== 'card') return;
    if (this.phase === 'anim') { this.tl.jump(BIG); return; }
    this.dwell = null;
    if (this.bi >= this.card.beats.length - 1) this.showEnd();
    else this.startBeat(this.bi + 1);
  }

  back() {
    if (this.mode === 'card' || this.mode === 'done') {
      const target = this.mode === 'done' ? this.card.beats.length - 1 : this.bi - (this.phase === 'anim' ? 1 : 0);
      this.gotoBeat(Math.max(0, target));
    }
  }

  // ───────────────────────── World helpers ─────────────────────────

  setWorld(w, snap) {
    const sc = this.scene;
    const from = robotPose(sc, this.tl.t, performance.now());
    sc.exec = null;
    sc.world = clone(w);
    sc.robot.pose = { ...w.robot.pose };
    sc.robot.holding = w.robot.holding ?? null;
    sc.robot.snap = snap && dist(from, w.robot.pose) > 1e-6 ? { from, t0: performance.now() } : null;
  }

  chipsFor(w, now, { stale = false } = {}) {
    const list = [];
    const mode = this.scene.mode;
    if (mode && mode !== 'normal') list.push({ text: `모드: ${MODE_KO[mode] ?? mode}`, tone: mode === 'caution' ? 'warn' : 'alarm' });
    if (w && w.confidence < 0.9) list.push({ text: `인식 신뢰도 ${Math.round(w.confidence * 100)}%`, tone: 'warn' });
    if (w && now != null && now - w.stamp_ms > 0) list.push({ text: `인식 나이 ${((now - w.stamp_ms) / 1000).toFixed(1)}초`, tone: stale ? 'alarm' : '' });
    setChips(list);
  }

  updateLadder(pulse) {
    const mode = this.gate?.mode ?? 'normal';
    this.scene.mode = mode;
    const visible = mode !== 'normal' || !!this.card?.ladder;
    setLadder(mode, visible, pulse && !this.reduced);
  }

  fxReset() {
    $('fx-haze').classList.remove('on');
    $('map').classList.remove('desat');
    $('map-wrap').classList.remove('shake');
  }

  describeMap(w) {
    const pol = this.policy;
    const parts = [`로봇 ${pt(w.robot.pose)}${w.robot.holding === 'knife' ? ', 칼을 들고 있음' : w.robot.holding ? `, ${w.robot.holding}을(를) 들고 있음` : ''}`];
    for (const h of w.humans ?? []) parts.push(`${h.class === 'child' ? '아이' : '어른'} ${pt(h.pos)}`);
    const zones = (pol?.zones ?? []).map((z) => (z.no_entry ? `${zoneName(z.id)} 출입 금지` : z.speed_limit != null ? `${zoneName(z.id)} ${num(z.speed_limit)} m/s 제한` : zoneName(z.id)));
    const prefix = this.stage.kind === '3d' ? '모형 집' : '지도';
    $('map').setAttribute('aria-label', `${prefix}: ${parts.join('. ')}. ${zones.join(', ')}.`);
    $('entities').replaceChildren(...[...parts, ...zones].map((t) => {
      const li = document.createElement('li');
      li.textContent = t;
      return li;
    }));
  }

  setCaption(html, cmd, tone) {
    this.caption = { html, cmd, tone };
    const t = $('cap-text');
    t.innerHTML = html;
    t.dataset.tone = tone ?? '';
    $('cap-cmd').innerHTML = cmd;
    $('btn-detail').hidden = !(this.lastRec?.decision && ['yun', 'jeol', 'bul'].includes(tone));
  }

  logDecision(rec) {
    const d = rec.decision;
    this.app.log.add({
      t: rec.now,
      kind: 'proposal',
      verdict: d.verdict,
      text: `#${rec.proposal.id} ${rec.proposal.source}: ${actionText(rec.proposal.action)}`,
      detail: `fired: ${d.fired.join(', ') || '없음'} · 실행: ${actionText(d.action)} · 상한: ${d.speed_cap == null ? '없음' : `${num(d.speed_cap)} m/s`} · 모드: ${d.mode}`,
      json: { proposal: rec.proposal, world: rec.world, now: rec.now, decision: d },
    });
  }

  // ───────────────────────── End card ─────────────────────────

  showEnd() {
    const card = this.card;
    this.dwell = null;
    this.mode = 'done';
    document.body.dataset.state = 'done';
    const recs = this.recs.filter(Boolean);
    const verdicts = recs.map((r) => r.decision?.verdict).filter(Boolean);
    const attack = recs.filter((r) => (r.beat.key || r.beat.attack) && r.decision);
    let outro;
    let warn = false;
    if (attack.some((r) => r.decision.verdict === 'yun')) {
      outro = GENERIC_OUTRO.yun;
      warn = true;
    } else if (attack.length) {
      const strict = attack.map((r) => r.decision.verdict).sort((a, b) => STRICT[b] - STRICT[a])[0];
      outro = outroFor(card, strict);
    } else {
      outro = '';
    }
    const counts = { yun: 0, jeol: 0, bul: 0 };
    verdicts.forEach((v) => { counts[v] += 1; });
    const keyRec = recs.find((r) => r.beat.key);
    this.overlay.toTrail();
    const box = $('endcard');
    const sealsRow = verdicts.map((v) => `<span class="v v-${v}">${verdictHtml(v)}</span>`).join('<span class="sep">·</span>');
    let html = `<div class="ec-seals" role="img" aria-label="이번 장면 판정: ${verdicts.map((v) => VERDICT_UI[v].word).join(', ')}">${sealsRow}</div>`;
    html += `<p class="ec-outro">${warn ? '<span class="warn" aria-hidden="true">⚠</span> ' : ''}${esc(outro)}</p>`;
    if (verdicts.some((v) => v !== 'yun')) {
      html += `<p class="ec-ww">해태 없이: 모델 명령 ${recs.length}개가 모두 그대로 실행 · 해태와 함께: 차단 ${counts.bul} · 감속 ${counts.jeol} · 통과 ${counts.yun}</p>`;
    }
    if (this.app.policyModified()) html += '<p class="ec-note">정책 수정됨 — 판정은 지금 적용된 정책으로 계산했습니다.</p>';
    html += '<div class="ec-actions"></div>';
    box.innerHTML = html;
    const actions = box.querySelector('.ec-actions');
    const btn = (text, fn, cls = 'btn-secondary') => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = cls;
      b.textContent = text;
      b.addEventListener('click', fn);
      actions.append(b);
      return b;
    };
    btn('↻ 다시', () => this.startCard(card));
    if (keyRec?.decision && keyRec.decision.verdict !== 'yun') btn('해태 없이 다시 보기', () => this.startGhost(keyRec));
    if (card.operatorReset) {
      const b = btn('운영자 리셋', () => {
        this.gate.reset();
        this.updateLadder(true);
        this.chipsFor(null, null);
        this.app.log.add({ t: recs.at(-1)?.now ?? 0, kind: 'mode', text: `운영자 리셋 → ${this.gate.mode}` });
        this.app.announce(`운영자 리셋. 모드: ${MODE_KO[this.gate.mode]}`);
        b.disabled = true;
      });
    }
    btn('대본 보기', () => this.app.openDetail('transcript'), 'linkbtn');
    box.hidden = false;
    // The caption no longer speaks for the last beat: a neutral tally instead.
    this.setCaption(esc(`장면 끝 · 차단 ${counts.bul} · 감속 ${counts.jeol} · 통과 ${counts.yun}`), '', 'end');
    this.stage.frameBeat({ phase: 'end' });
    this.app.markWatched(card.num, verdicts);
    this.renderRail();
    this.app.announce(`장면 끝. ${verdicts.map((v) => VERDICT_UI[v].word).join(', ')}. ${outro}`);
    this.updateTransport();
    if (this.app.kiosk) this.startDwell(6000, () => this.startCard(this.nextCard()));
  }

  nextCard() {
    const i = CARDS.indexOf(this.card);
    return CARDS[(i + 1) % CARDS.length];
  }

  // ───────────────────────── Without Haetae (unfiltered) ─────────────────────────

  canGhost() {
    if (this.mode === 'card' && this.phase === 'revealed') {
      const r = this.recs[this.bi];
      return !!(r?.decision && r.decision.verdict !== 'yun');
    }
    return false;
  }

  startGhost(rec) {
    if (!rec?.decision || rec.decision.verdict === 'yun' || this.mode === 'ghost') return;
    const sc = this.scene;
    const dwell = this.dwell;
    this.dwell = null;
    this.ghostReturn = {
      dwell: dwell ? { ...dwell, left: dwell.total } : null,
      mode: this.mode,
      phase: this.phase,
      tlT: this.tl.t,
      endcard: !$('endcard').hidden,
      caption: { ...this.caption },
      strip: this.strip.root.dataset.state,
      scene: {
        world: sc.world, robot: { ...sc.robot }, intent: sc.intent, exec: sc.exec, barrier: sc.barrier,
        hl: sc.hl, focus: sc.focus, decor: sc.decor, fx: sc.fx, trail: sc.trail, peer: sc.peer, signal: sc.signal,
      },
      rec,
    };
    this.mode = 'ghost';
    document.body.dataset.state = 'ghost';
    $('endcard').hidden = true;
    this.overlay.root.classList.add('ghosting');
    $('map-area').classList.add('unfiltered');
    $('ghost-banner').hidden = false;
    this.strip.setSource(rec.proposal.source, this.policy);
    this.strip.bypass(true);
    this.fxReset();

    const tl = this.tl = new Timeline();
    sc.world = clone(rec.world);
    sc.robot = { ...sc.robot, pose: { ...rec.world.robot.pose }, holding: rec.world.robot.holding ?? null, snap: null, reactT: null };
    sc.intent = null; sc.barrier = null; sc.hl = []; sc.exec = null; sc.focus = null;
    sc.fx = null; sc.trail = []; sc.signal = null; sc.peer = rec.proposal.source === 'peer';
    sc.decor = { ...(rec.beat?.decor ?? {}) };
    delete sc.decor.peelT;
    sc.realAlpha = 0.4;

    // Same k as the gated run; a denied run computes k from the raw speed.
    let k = rec.k ?? 1;
    const a = rec.proposal.action;
    if (rec.decision.verdict === 'bul' && a?.type === 'move_to' && a.speed > 0) {
      const raw = (dist(rec.world.robot.pose, a.goal) / a.speed) * 1000;
      k = this.reduced ? 1 : Math.max(1, raw / T_FULL.maxMotion);
    }
    if (this.reduced) k = BIG; // end pose at once
    const g = playUnfiltered({ world: rec.world, proposal: rec.proposal, k }, { clock: () => tl.t, setGhost: (x) => { sc.ghost = x; } });
    // The real robot re-plays its RECORDED decision.action, dimmed, same k.
    const T = { ...T_FULL, maxMotion: this.reduced ? 0 : T_FULL.maxMotion };
    let realDur = 0;
    if (rec.decision.action && rec.decision.verdict !== 'bul') {
      realDur = this.startExec(rec, T, 250, sc, 0.4);
      if (sc.exec && rec.k && !this.reduced && sc.exec.kind === 'move') {
        const raw = (dist(sc.exec.from, sc.exec.to) / sc.exec.speed) * 1000;
        sc.exec.dur = raw / k;
        realDur = sc.exec.dur;
      }
    }
    // Recorded seal of that beat, dimmed (belongs to the real robot, not the ghost).
    const anchor = rec.anchor?.type === 'world' ? rec.anchor : { type: 'world', p: { x: rec.world.robot.pose.x, y: rec.world.robot.pose.y + 0.9 } };
    const keepSeal = this.overlay.seal;
    this.ghostSeal = this.overlay.landSeal(rec.decision, anchor, { reduced: true });
    this.overlay.seal = keepSeal;
    this.ghostSeal.el.classList.add('ghost-real');

    {
      const pa = rec.proposal.action;
      const tgt = pa?.type === 'move_to' ? pa.goal : pa?.at;
      this.stage.frameBeat({ phase: 'ghost', points: [rec.world.robot.pose, tgt] });
    }
    const badge = $('speed-badge');
    badge.hidden = !(k > 1.05 && k < BIG);
    badge.textContent = `×${k.toFixed(1)} 배속`;

    const raw = rec.beat?.raw ?? '모델의 명령을 그대로 실행합니다.';
    const fault = rec.beat?.fault ? ' 고장 신호를 받아 줄 게이트가 없습니다.' : '';
    // The command line names the proposal this ghost is executing, not the last beat's.
    const cmd = `명령 · ${sourceName(rec.proposal.source)} → ${actionText(rec.proposal.action)}`;
    this.setCaption(`<span class="neutral" aria-hidden="true">⚠</span> ${esc(raw)}${esc(fault)}`, esc(cmd), 'ghost');
    this.app.announce(`비교 재생, 해태 없음, 판정 없음. ${raw}${fault}`);
    this.app.log.add({ t: rec.now, kind: 'comparison', text: `#${rec.proposal.id} 필터 없이 실행: ${actionText(rec.proposal.action)}`, detail: '판정 없음 — 게이트를 부르지 않음' });
    this.ghostDone = false;
    const total = Math.max(g.duration, 250 + realDur);
    tl.at(this.reduced ? 0 : total + 900, () => { this.ghostDone = true; badge.hidden = true; this.updateTransport(); });
    this.updateTransport();
  }

  leaveGhostVisuals() {
    this.overlay.root.classList.remove('ghosting');
    $('map-area').classList.remove('unfiltered');
    $('ghost-banner').hidden = true;
    this.strip.bypass(false);
    if (this.ghostSeal) { this.ghostSeal.el.remove(); this.overlay.items = this.overlay.items.filter((i) => i !== this.ghostSeal); }
    this.ghostSeal = null;
    this.scene.ghost = null;
    this.scene.realAlpha = 1;
    $('speed-badge').hidden = true;
  }

  exitGhost() {
    if (this.mode !== 'ghost') return;
    const r = this.ghostReturn;
    this.leaveGhostVisuals();
    const sc = this.scene;
    Object.assign(sc, r.scene);
    this.tl = new Timeline();
    this.tl.t = r.tlT;
    this.mode = r.mode;
    this.phase = r.phase;
    document.body.dataset.state = r.mode;
    if (r.mode === 'card' && this.recs[this.bi]) this.strip.setSource(this.recs[this.bi].proposal.source, this.policy);
    this.strip.set(r.strip);
    this.setCaption(r.caption.html, r.caption.cmd, r.caption.tone);
    if (r.endcard) $('endcard').hidden = false;
    // Back to the gated end state, countdown included.
    if (r.dwell) this.dwell = r.dwell;
    else if (r.mode === 'card' && r.phase === 'revealed') {
      const v = this.recs[this.bi]?.decision?.verdict ?? 'error';
      const last = this.bi >= this.card.beats.length - 1;
      this.startDwell(DWELL[v], () => (last ? this.showEnd() : this.startBeat(this.bi + 1)));
    } else if (r.mode === 'done' && this.app.kiosk) {
      this.startDwell(6000, () => this.startCard(this.nextCard()));
    }
    this.app.announce('해태를 다시 켰습니다.');
    this.updateTransport();
  }

  // ───────────────────────── Card 8: the dinner-party stream ─────────────────────────

  startStream(src = {}, { custom = false } = {}) {
    this.mode = 'stream';
    document.body.dataset.state = 'stream';
    this.streamRecs = [];
    this.streamWorld = null;
    this.swallowCaption = false;
    this.customStream = custom;
    this.streamSrc = src;
    // Kiosk never waits for a press; reduced motion there just steps slower.
    this.manual = !this.app.kiosk && (!this.app.prefs.auto || this.reduced);
    this.pausedOnce = false;
    this.playerHeld = false;
    setProgress({ total: 7, index: 0, verdicts: [], film: true });
    this.updateFilm();
    const player = new ScenarioPlayer(this.streamApi());
    if (this.app.kiosk && this.reduced) player.dwellMs = 6000;
    this.player = player;
    this.updateTransport();
    // The applied policy is what the mission gate uses; no need to fetch policy.json.
    player.start({ policy: this.app.policyText(), ...src });
  }

  updateFilm() {
    const vs = (this.streamRecs ?? []).map((r) => r.decision?.verdict).filter(Boolean);
    setProgress({ total: Math.max(7, vs.length), index: vs.length, verdicts: vs, film: true });
  }

  streamApi() {
    const self = this;
    const live = () => self.player && self.mode === 'stream';
    return {
      begin(_policyText, world) {
        // The mission gate holds the applied policy (examples/policy.json unless edited in the lab).
        self.gate.reset();
        self.scene.mode = self.gate.mode;
        self.streamWorld = world;
        self.setWorld(world, false);
        self.scene.decor = {};
        self.updateLadder(false);
        self.chipsFor(world, world.stamp_ms);
        self.describeMap(world);
      },
      world(w, now) {
        if (!live()) return;
        self.streamWorld = w;
        self.overlay.toTrail();
        self.scene.intent = null; self.scene.barrier = null; self.scene.hl = [];
        self.setWorld(w, true);
        self.chipsFor(w, now);
        self.describeMap(w);
      },
      rejectedWorld() {},
      fault(f, now) {
        if (!live()) return;
        const before = self.gate.mode;
        const changed = self.gate.raise(f.raise_to);
        self.scene.robot.glitchT = performance.now();
        self.updateLadder(true);
        self.chipsFor(self.streamWorld, now);
        self.app.log.add({ t: now, kind: 'fault', text: `${f.code ?? ''}: ${f.raise_to} 요청 → ${changed ? `${before} → ${self.gate.mode}` : `변화 없음 (${self.gate.mode})`}` });
      },
      run(proposal, now) {
        if (!live()) return { decision: null, done: Promise.resolve() };
        const generic = `제안 #${proposal.id} (${sourceName(proposal.source)}): ${actionText(proposal.action)}`;
        const rec = {
          proposal, world: self.streamWorld, now, card: self.card,
          beat: { narr: self.customStream ? generic : (PROPOSAL_CAPTIONS[proposal.id] ?? generic) },
          decision: null, index: self.streamRecs.length,
        };
        try {
          rec.decision = deepFreeze(self.gate.judge(proposal, self.streamWorld, now));
          self.logDecision(rec);
        } catch (e) {
          rec.error = e?.message ?? String(e);
          self.app.log.add({ t: now, kind: 'error', text: `판정 실패 (입력 오류): ${rec.error}` });
        }
        self.streamRecs.push(rec);
        self.swallowCaption = true;
        const done = new Promise((res) => { rec.onDone = res; });
        self.scheduleBeat(rec, self.reduced ? T_STREAM_REDUCED : T_STREAM, { stream: true, fresh: false });
        self.tl.flush();
        if (rec.error) rec.onDone();
        return { decision: rec.decision, done };
      },
      fastForward() { self.tl.jump(BIG); },
      caption(text) {
        if (!live()) return;
        if (self.swallowCaption) { self.swallowCaption = false; return; }
        self.setCaption(esc(text), '', 'narr');
        self.app.announce(text);
      },
      controls({ running, paused }) {
        if (!live()) return;
        if (self.playerHeld) return; // held by a modal sheet / hidden tab, not by the viewer
        if (running && self.manual && !self.pausedOnce) {
          self.pausedOnce = true;
          self.player.pause();
          return;
        }
        self.streamPaused = paused;
        self.syncPause();
      },
      end(summary) {
        if (!summary || !self.player) return;
        self.showStreamEnd(summary);
      },
      error(message) {
        if (!self.player) return;
        self.player = null;
        self.mode = 'streamError';
        self.setCaption(esc(message), '', 'error');
        self.app.announce(message);
        self.updateTransport();
      },
    };
  }

  showStreamEnd(summary) {
    this.player = null;
    this.mode = 'streamDone';
    document.body.dataset.state = 'done';
    const c = summary.counts;
    const box = $('endcard');
    const seals = summary.decisions.map((d) => `<span class="v v-${d.verdict}">${verdictHtml(d.verdict)}</span>`).join('<span class="sep">·</span>');
    const words = summary.decisions.map((d) => VERDICT_UI[d.verdict].word).join(', ');
    box.innerHTML = `<div class="ec-seals" role="img" aria-label="판정: ${words}">${seals}</div>
      <p class="ec-score">통과 ${c.yun} · 감속 ${c.jeol} · 차단 ${c.bul}</p>
      <p class="ec-ww">해태 없이: 모델 명령 ${summary.decisions.length}개가 모두 그대로 실행 · 해태와 함께: 차단 ${c.bul} · 감속 ${c.jeol} · 통과 ${c.yun}</p>
      ${this.app.policyModified() ? '<p class="ec-note">정책 수정됨 — 판정은 지금 적용된 정책으로 계산했습니다.</p>' : ''}
      <div class="ec-actions"><button type="button" class="btn-secondary" id="ec-lab">⚙ 실험실에서 직접 해 보기</button>
      <button type="button" class="linkbtn" id="ec-transcript">대본 보기</button></div>`;
    box.querySelector('#ec-lab').addEventListener('click', () => this.app.openLab());
    box.querySelector('#ec-transcript').addEventListener('click', () => this.app.openDetail('transcript'));
    box.hidden = false;
    this.transcript = this.streamRecs.filter((r) => r.decision).map((r) => ({
      narr: r.beat.narr, cmd: `${sourceName(r.proposal.source)} → ${actionText(r.proposal.action)}`,
      verdict: r.decision.verdict, sentence: r.copy?.sentence ?? verdictCopy(r.decision, this.ctx(r)).sentence,
    }));
    this.setCaption(esc(`${this.customStream ? '스트림' : '저녁 파티'} 끝 · 통과 ${c.yun} · 감속 ${c.jeol} · 차단 ${c.bul}`), '', 'end');
    this.stage.frameBeat({ phase: 'end' });
    // A pasted/uploaded stream is not card 8: never recorded as watched.
    if (!this.customStream) this.app.markWatched(8, summary.decisions.map((d) => d.verdict));
    this.renderRail();
    this.app.announce(`${this.customStream ? '스트림 재생' : '저녁 파티'} 끝. 통과 ${c.yun}, 감속 ${c.jeol}, 차단 ${c.bul}.`);
    this.updateTransport();
    if (this.app.kiosk) this.startDwell(6000, () => this.startCard(CARDS[0]));
  }

  // ───────────────────────── Lab beats ─────────────────────────

  /** Play the full timeline for a lab command. `gate` is the lab's own gate. */
  playLabBeat({ proposal, world, now, gate, onDone }) {
    this.dwell = null;
    this.overlay.clearAll();
    const rec = { proposal, world, now, beat: { narr: '실험실 명령' }, decision: null, lab: true, index: 0 };
    this.transcript = [];
    this.scheduleBeat(rec, this.reduced ? T_REDUCED : T_FULL, { gate, fresh: false, onDone, lab: true });
    return rec;
  }

  enterLab() {
    this.stopAll();
    this.hideIntro();
    this.mode = 'lab';
    this.card = null;
    document.body.dataset.state = 'lab';
    this.setTitle(null);
    $('stage-title').textContent = '⚙ 실험실 · 지도를 눌러 직접 명령해 보세요';
    setProgress({ total: 0 });
    this.strip.idle();
    this.setCaption('실험실 모드: 지도 위를 가리키면 실제 판정 미리보기가 뜹니다. 누르면 명령을 보냅니다.', '', 'narr');
    this.renderRail();
    this.updateTransport();
  }

  // ───────────────────────── Transport ─────────────────────────

  pauseToggle() {
    if (this.mode === 'stream' && this.player) {
      if (this.player.paused) { this.player.resume(); this.paused = false; } else { this.player.pause(); this.paused = true; }
    } else if (['card', 'done', 'streamDone'].includes(this.mode)) {
      this.paused = !this.paused;
    }
    this.syncPause();
  }

  syncPause() {
    const p = this.paused || (this.mode === 'stream' && this.streamPaused && !this.manual);
    const b = $('btn-pause');
    b.textContent = p ? '▶' : '❚❚';
    b.setAttribute('aria-label', p ? '계속' : '일시정지');
    b.setAttribute('aria-pressed', String(p));
  }

  primary() {
    switch (this.mode) {
      case 'intro': this.startCard(CARDS[0]); break;
      case 'card': this.advance(); break;
      case 'ghost': this.exitGhost(); break;
      case 'done': {
        const last = CARDS.indexOf(this.card) === CARDS.length - 1;
        this.startCard(last ? CARDS[0] : this.nextCard());
        break;
      }
      case 'stream':
        if (this.player) {
          if (this.player.animating) this.player.next();
          else if (this.player.paused || this.manual) this.player.next();
          else this.player.next();
        }
        break;
      case 'streamDone': this.startCard(CARDS[0]); break;
      case 'streamError': {
        const src = this.streamSrc;
        const custom = this.customStream;
        this.startCard(this.card);
        if (custom) { this.player?.stop(true); this.startStream(src, { custom: true }); }
        break;
      }
      default: break;
    }
  }

  ghostKey() {
    const ghostBtn = $('btn-ghost');
    const hadFocus = document.activeElement === ghostBtn;
    if (this.mode === 'ghost') this.exitGhost();
    else if (this.canGhost()) this.startGhost(this.recs[this.bi]);
    else if (this.mode === 'done') {
      const keyRec = this.recs.find((r) => r?.beat?.key);
      if (keyRec?.decision && keyRec.decision.verdict !== 'yun') this.startGhost(keyRec);
    }
    // The pressed button may just have been hidden: keep keyboard focus in the transport.
    if (hadFocus) {
      const to = !ghostBtn.hidden && ghostBtn.offsetParent ? ghostBtn : $('btn-primary');
      if (document.activeElement !== to) to.focus({ preventScroll: true });
    }
  }

  updateTransport() {
    const prim = $('btn-primary');
    const label = prim.querySelector('.label');
    const ghost = $('btn-ghost');
    const pause = $('btn-pause');
    let text = '다음 ▶';
    let showGhost = false;
    let showPause = true;
    switch (this.mode) {
      case 'card':
        showGhost = this.canGhost();
        break;
      case 'ghost':
        text = '해태 켜고 돌아가기';
        showPause = false;
        break;
      case 'done': {
        const i = CARDS.indexOf(this.card);
        text = i === CARDS.length - 1 ? '↻ 처음부터' : `다음 공격: ${CARDS[i + 1].title} →`;
        showPause = !!this.app.kiosk;
        break;
      }
      case 'streamDone':
        text = '↻ 처음부터';
        showPause = false;
        break;
      case 'stream':
        showPause = !this.manual; // step-by-step: 다음 is the control
        break;
      case 'streamError':
        text = '↻ 다시 시도';
        showPause = false;
        break;
      default:
        break;
    }
    label.textContent = text;
    ghost.hidden = !showGhost;
    pause.hidden = !showPause;
    prim.classList.toggle('ghosting', this.mode === 'ghost');
  }

  // ───────────────────────── Rail ─────────────────────────

  renderRail() {
    const list = $('cards');
    const watched = this.app.watched;
    if (!list.children.length) {
      for (const c of CARDS) {
        const li = document.createElement('li');
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'card-btn';
        b.dataset.card = String(c.num);
        b.innerHTML = `<span class="c-num">${c.num}</span><span class="c-body"><span class="c-title">${esc(c.title)}${c.star ? ' <span aria-hidden="true">★</span>' : ''}</span><span class="c-threat" id="threat-${c.num}">${esc(c.threat)}</span></span><span class="c-badge" id="badge-${c.num}"></span><span class="sr-only" id="status-${c.num}"></span>`;
        b.setAttribute('aria-describedby', `threat-${c.num} status-${c.num}`);
        b.title = c.threat;
        b.addEventListener('click', () => this.app.pickCard(c));
        li.append(b);
        list.append(li);
      }
    }
    for (const c of CARDS) {
      const b = list.querySelector(`[data-card="${c.num}"]`);
      const badge = $(`badge-${c.num}`);
      const status = $(`status-${c.num}`);
      const seen = watched.get(c.num);
      if (seen) {
        // Long runs (the 7-command dinner party) as counts, so the title keeps its room.
        const icons = seen.length > 4
          ? ['yun', 'jeol', 'bul'].filter((v) => seen.includes(v)).map((v) => `${verdictIcon(v, 'g')}<span class="n" aria-hidden="true">${seen.filter((x) => x === v).length}</span>`).join('')
          : seen.map((v) => verdictIcon(v, 'g')).join('');
        // The watched mark is a word: the check mark now means 통과.
        badge.innerHTML = `${icons}<span class="seen" aria-hidden="true">봄</span>`;
        status.textContent = `본 장면: ${seen.map((v) => VERDICT_UI[v]?.word).join(', ')}`;
      } else {
        badge.innerHTML = `<span class="tag">${esc(c.tag)}</span>`;
        status.textContent = '아직 안 봄';
      }
      if (this.card === c && this.mode !== 'lab') b.setAttribute('aria-current', 'true'); else b.removeAttribute('aria-current');
    }
    $('watched').textContent = `본 ${watched.size}/${CARDS.length}`;
  }

  // ───────────────────────── Detail sheet content ─────────────────────────

  detailHtml(section) {
    const rec = this.lastRec;
    let html = '';
    if (rec?.decision && section !== 'transcript-only') {
      const d = rec.decision;
      const ui = VERDICT_UI[d.verdict];
      const ctx = this.ctx(rec);
      html += `<div class="dt-head v-${d.verdict}">${verdictIcon(d.verdict, 'dt-icon')}<span class="dt-word">${ui.word}</span><code>${d.verdict}</code></div>`;
      const names = sortedFired(d, this.policy);
      if (names.length) {
        html += '<h3>걸린 조건</h3><ul class="dt-fired">';
        for (const n of names) {
          html += `<li><div class="f-top"><strong>${esc(label(n, ctx))}</strong><span class="kind">${KIND_BADGE[badgeKind(n, this.policy)]}</span></div><p>${esc(sentence(n, ctx))}</p><code>${esc(n)}</code></li>`;
        }
        html += '</ul>';
      } else {
        html += `<p class="dt-none">${esc(rec.copy?.sentence ?? '')}</p>`;
      }
      const req = actionText(rec.proposal.action);
      const ex = actionText(d.action);
      html += `<h3>요청 → 실행</h3><p class="dt-flow"><span class="intent">${esc(req)}</span> → <span class="${req !== ex ? 'changed' : ''}">${esc(ex)}</span></p>`;
      html += `<dl class="dt-meta"><dt>속도 상한 (speed_cap)</dt><dd>${d.speed_cap == null ? '없음' : `${num(d.speed_cap)} m/s`}</dd><dt>모드</dt><dd>${esc(MODE_KO[d.mode] ?? d.mode)} <code>${esc(d.mode)}</code></dd><dt>명령 id</dt><dd>${d.proposal_id}</dd></dl>`;
      if (rec.card?.footnote) html += `<p class="dt-foot">${esc(rec.card.footnote)}</p>`;
      html += `<details class="dt-json"><summary>원본 JSON</summary><pre>${esc(JSON.stringify({ proposal: rec.proposal, world: rec.world, now: rec.now, decision: d }, null, 2))}</pre></details>`;
    } else if (section !== 'transcript-only') {
      html += '<p class="dt-none">아직 판정이 없습니다. 공격 카드를 골라 보세요.</p>';
    }
    const tr = this.transcript.filter(Boolean);
    if (tr.length) {
      html += '<h3 id="dt-transcript">이번 장면 대본</h3><ol class="dt-transcript">';
      for (const t of tr) {
        html += `<li><p class="narr">${esc(t.narr)}</p><p class="cmd">명령 · ${esc(t.cmd)}</p><p class="res v-${t.verdict}">${verdictHtml(t.verdict)} · ${esc(t.sentence)}</p></li>`;
      }
      html += '</ol>';
    }
    return html;
  }
}

