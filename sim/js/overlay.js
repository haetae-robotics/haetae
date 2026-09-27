// DOM layer over the map and around it: command slip, seal, reason callout,
// pipeline strip, context chips, mode ladder, beat dots / filmstrip, end card
// and the live region. Plus the anchor geometry that decides WHERE a seal
// lands. Placement only: every function here reads names from
// decision.fired and numbers from the policy; none of them changes, drops or
// re-derives a verdict. A seal element can only be built from a Decision.

import { VERDICT_UI, verdictIcon, sourceName, zoneName, kindOf, capShort, actionTarget } from './copy.js';
import { num, MODE_KO, MODES } from './explain.js';

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};

// ───────────────────────── Geometry (placement only) ─────────────────────────

const sub = (a, b) => ({ x: a.x - b.x, y: a.y - b.y });
const add = (a, b) => ({ x: a.x + b.x, y: a.y + b.y });
const mul = (a, k) => ({ x: a.x * k, y: a.y * k });
const len = (a) => Math.hypot(a.x, a.y);

/** Liang–Barsky: parameter t0 where segment A→B first enters rect, or null. */
function segEnter(A, B, r) {
  const d = sub(B, A);
  let t0 = 0;
  let t1 = 1;
  const clip = (p, q) => {
    if (p === 0) return q >= 0;
    const t = q / p;
    if (p < 0) { if (t > t1) return false; if (t > t0) t0 = t; } else { if (t < t0) return false; if (t < t1) t1 = t; }
    return true;
  };
  if (clip(-d.x, A.x - r.min.x) && clip(d.x, r.max.x - A.x) && clip(-d.y, A.y - r.min.y) && clip(d.y, r.max.y - A.y)) return t0;
  return null;
}

/** Parameter where A→B leaves rect r (A inside), or null. */
function segLeave(A, B, r) {
  const inside = (p) => p.x >= r.min.x && p.x <= r.max.x && p.y >= r.min.y && p.y <= r.max.y;
  if (!inside(A) || inside(B)) return null;
  let lo = 0; let hi = 1;
  for (let i = 0; i < 40; i++) {
    const m = (lo + hi) / 2;
    if (inside(add(A, mul(sub(B, A), m)))) lo = m; else hi = m;
  }
  return lo;
}

/** First t in [0,1] where |A + t(B−A) − H| ≤ d, or null. */
function firstWithin(A, B, H, d) {
  const D = sub(B, A);
  const F = sub(A, H);
  const a = D.x * D.x + D.y * D.y;
  const b = 2 * (F.x * D.x + F.y * D.y);
  const c = F.x * F.x + F.y * F.y - d * d;
  if (c <= 0) return 0;
  if (a === 0) return null;
  const disc = b * b - 4 * a * c;
  if (disc < 0) return null;
  const t = (-b - Math.sqrt(disc)) / (2 * a);
  return t >= 0 && t <= 1 ? t : null;
}

function closestT(A, B, H) {
  const D = sub(B, A);
  const a = D.x * D.x + D.y * D.y;
  if (a === 0) return 0;
  return Math.max(0, Math.min(1, ((H.x - A.x) * D.x + (H.y - A.y) * D.y) / a));
}

/** Distance from H to the segment A→B. */
const segDist = (A, B, H) => len(sub(add(A, mul(sub(B, A), closestT(A, B, H))), H));

/**
 * Where the seal lands. Returns { type:'world', p, dir } or { type:'slip' }.
 * `head` is the headline fired name (copy.headline).
 */
export function sealAnchor(decision, proposal, world, policy, head, avoid = []) {
  const a = sealAnchorRaw(decision, proposal, world, policy, head);
  // Step back along the path when a focus prop (e.g. the fake tag) sits under the seal.
  if (a.type === 'world' && decision.verdict === 'bul' && avoid.length) {
    const back = mul(a.dir, -1);
    for (let k = 0; k < 4 && avoid.some((q) => len(sub(q, a.p)) < 0.9); k++) a.p = add(a.p, mul(back, 0.3));
  }
  return a;
}

function sealAnchorRaw(decision, proposal, world, policy, head) {
  const A = world.robot.pose;
  const target = actionTarget(proposal.action);
  const B = target ?? A;
  const dir = len(sub(B, A)) > 1e-9 ? mul(sub(B, A), 1 / len(sub(B, A))) : { x: 1, y: 0 };
  const at = (t) => ({ type: 'world', p: add(A, mul(sub(B, A), t)), dir });
  if (decision.verdict === 'yun') return { type: 'world', p: target ?? A, dir };
  if (decision.verdict === 'jeol') {
    const mid = add(A, mul(sub(B, A), 0.5));
    const n = { x: -dir.y, y: dir.x };
    const c = { x: 5, y: 5 };
    const toward = (n.x * (c.x - mid.x) + n.y * (c.y - mid.y)) >= 0 ? n : mul(n, -1);
    // Never cover a person with the seal: flip sides or step further out if needed.
    // (the name label sits about 0.45 m under a person's centre)
    const clear = (q) => (world.humans ?? []).every((h) => len(sub(h.pos, q)) >= 0.8
      && len(sub({ x: h.pos.x, y: h.pos.y - 0.45 }, q)) >= 0.95);
    const cands = [0.5, -0.5, 1, -1].map((k) => add(mid, mul(toward, k)));
    return { type: 'world', p: cands.find(clear) ?? cands[0], dir };
  }
  // bul
  if (!target) return { type: 'slip' };
  if (head.startsWith('zone:')) {
    const z = policy.zones?.find((q) => q.id === head.slice(5));
    if (z?.no_entry) {
      const t = segEnter(A, B, z.area);
      return t == null ? { type: 'world', p: A, dir } : at(t);
    }
  }
  const rule = policy.rules?.find((r) => r.id === head);
  if (rule?.when?.human_within) {
    const hw = rule.when.human_within;
    const humans = (world.humans ?? []).filter((h) => !hw.class || h.class === hw.class);
    let best = null;
    for (const h of humans) {
      const t = firstWithin(A, B, h.pos, hw.distance);
      if (t != null && (best == null || t < best)) best = t;
    }
    if (best != null) return at(best);
    if (humans.length) {
      const near = humans.reduce((m, h) => (len(sub(h.pos, A)) < len(sub(m.pos, A)) ? h : m));
      return at(closestT(A, B, near.pos));
    }
    return { type: 'slip' };
  }
  if (head === 'envelope:workspace') {
    const t = segLeave(A, B, policy.envelope.workspace);
    return t == null ? at(1) : at(t);
  }
  if (head === 'envelope:pose') return { type: 'world', p: A, dir };
  return { type: 'slip' };
}

/**
 * Highlights for every fired name (and nothing for names that did not fire).
 * Returns { canvas: [...scene.hl items without t0], dom: {...flags} }.
 */
export function highlightsFor(decision, proposal, world, policy, now) {
  const canvas = [];
  const dom = {};
  for (const name of decision.fired ?? []) {
    const rule = policy.rules?.find((r) => r.id === name);
    if (rule) {
      const hw = rule.when?.human_within;
      if (hw) {
        const tone = rule.then === 'bul' ? 'bul' : 'jeol';
        // Ring only the people the path actually comes within the rule's
        // distance of (placement geometry, as in sealAnchor). If the geometry
        // finds nobody, ring the nearest candidate so the cause stays visible.
        const A = world.robot.pose;
        const B = actionTarget(proposal.action) ?? A;
        const cands = (world.humans ?? []).filter((h) => !hw.class || h.class === hw.class);
        let near = cands.filter((h) => segDist(A, B, h.pos) <= hw.distance + 1e-9);
        if (!near.length && cands.length) {
          near = [cands.reduce((m, h) => (segDist(A, B, h.pos) < segDist(A, B, m.pos) ? h : m))];
        }
        for (const h of near) canvas.push({ kind: 'ring', center: h.pos, r: hw.distance, tone });
        if (tone === 'bul' && (world.robot.holding || proposal.action?.object)) canvas.push({ kind: 'glint' });
      }
      if (rule.when?.confidence_below != null) {
        dom.haze = true;
        dom.confidence = { value: world.confidence, threshold: rule.when.confidence_below };
      }
      continue;
    }
    if (name.startsWith('zone:')) {
      const z = policy.zones?.find((q) => q.id === name.slice(5));
      if (z) canvas.push({ kind: 'zone', id: z.id, deny: !!z.no_entry });
      continue;
    }
    if (name.startsWith('mode:')) { dom.mode = name.slice(5); continue; }
    switch (name) {
      case 'envelope:max_speed':
        dom.speedChip = { req: proposal.action?.speed, max: policy.envelope.max_speed };
        break;
      case 'envelope:workspace':
      case 'envelope:pose':
        canvas.push({ kind: 'workspace' });
        break;
      case 'source:not-allowed':
        dom.sourceStrike = true;
        canvas.push({ kind: 'shield' });
        break;
      case 'stop:unvetted-source':
        dom.sourceNote = '기록됨';
        break;
      case 'stale:world':
        dom.stale = { age: now - world.stamp_ms };
        break;
      case 'stale:proposal':
      case 'invalid:timestamp':
        dom.tapeAlarm = true;
        break;
      default:
        break;
    }
  }
  // Brake stack: two or more cap-kind names fired on a jeol.
  if (decision.verdict === 'jeol') {
    const caps = (decision.fired ?? []).filter((n) => kindOf(n, policy) === 'cap');
    if (caps.length >= 2) {
      dom.brakeStack = caps.map((n) => ({ text: capShort(n, policy), n }));
    }
  }
  return { canvas, dom };
}

// ───────────────────────── Verdict tag ─────────────────────────

/**
 * Build a verdict tag from a Decision: a verdict icon (circle / diamond /
 * octagon) plus the Korean word, and later the reason label. There is
 * intentionally no way to build one without a Decision object.
 */
export function makeTag(decision, { tag } = {}) {
  const v = decision.verdict;
  const ui = VERDICT_UI[v];
  const root = el('div', `vtag vtag-${v}`);
  const leader = el('span', 'vt-leader');
  const dot = el('span', 'vt-dot');
  const box = el('div', 'vt-box');
  box.innerHTML = verdictIcon(v, 'vt-icon');
  const text = el('div', 'vt-text');
  const l1 = el('div', 'vt-l1');
  l1.append(el('span', 'vt-word', ui.word));
  text.append(l1);
  if (tag) text.append(el('div', 'vt-tag', tag));
  box.append(text);
  root.append(leader, dot, box);
  root.parts = { leader, dot, box, text, l1 };
  return root;
}

// ───────────────────────── Overlay ─────────────────────────

/** Overlap area of two {x,y,w,h} rects. */
function overlapArea(a, b) {
  const w = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
  const h = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
  return w > 0 && h > 0 ? w * h : 0;
}

const YUN_SETTLE_MS = 1500;
const EDGE = 8;

export class Overlay {
  constructor(stage) {
    this.root = $('overlay');
    this.area = $('map-area');
    this.wrap = $('map-wrap');
    this.items = []; // { el, world, h, dx, dy, kind, layout?, after? }
    this.mobileMq = matchMedia('(max-width: 719px)');
    this.tabletMq = matchMedia('(max-width: 1099px)');
    this.viewQueued = false;
    this.setStage(stage);
  }

  /** Runtime stage swap (3D <-> 2D): re-hook resize and camera changes. */
  setStage(stage) {
    this.stage = stage;
    stage.onResize = () => { this.relayout(); this.placeToolbar(); };
    stage.onView = () => {
      if (this.viewQueued) return;
      this.viewQueued = true;
      requestAnimationFrame(() => { this.viewQueued = false; this.relayout(); });
    };
    this.placeToolbar();
  }

  get mobile() { return this.mobileMq.matches; }
  get is3d() { return this.stage.kind === '3d'; }

  sealPx() { return this.mobile ? 32 : 40; }

  /** Map size in px. */
  get W() { return this.stage.cssW; }
  get H() { return this.stage.cssH; }

  /** Height (m) above the floor for an overlay anchor, 3D only. */
  hFor(kind) { return this.is3d ? ({ robot: 1.0, tag: 1.35, barrier: 1.05, yun: 0.4, chip: 1.2, person: 1.35 }[kind] ?? 0) : 0; }

  /** The view toolbar sits in the map's top-right corner but comes after the transport in tab order. */
  placeToolbar() {
    const bar = $('view-tools');
    if (!bar) return;
    const stage = $('stage');
    const w = this.wrap.getBoundingClientRect();
    const s = stage.getBoundingClientRect();
    bar.classList.toggle('compact', w.width < 440 || w.height < 340);
    bar.style.top = `${Math.round(w.top - s.top + 8)}px`;
    bar.style.right = `${Math.round(s.right - w.right + 8)}px`;
    // The default framing keeps the house clear of the toolbar (map px, 6 px air).
    const bw = bar.offsetWidth;
    const bh = bar.offsetHeight;
    this.stage.setReserve?.(bw && this.is3d ? [{ x: Math.round(w.width) - 8 - bw - 6, y: 0, w: bw + 14, h: 8 + bh + 6 }] : []);
  }

  place(e, x, y) {
    e.style.left = `${Math.round(x)}px`;
    e.style.top = `${Math.round(y)}px`;
  }

  track(e, world, kind, dx = 0, dy = 0, h = 0) {
    const item = { el: e, world, kind, dx, dy, h };
    this.items.push(item);
    this.layoutItem(item);
    return item;
  }

  layoutItem(it) {
    if (it.layout) {
      it.layout();
      if (it.after) it.after();
      return;
    }
    if (!it.world) return;
    const s = this.stage.toScreen(it.world, it.h ?? 0);
    this.place(it.el, s.x + it.dx, s.y + it.dy);
    it.el.classList.toggle('offmap', !!s.behind || s.x < 0 || s.x > this.W || s.y < 0 || s.y > this.H);
    if (it.after) it.after();
  }

  relayout() {
    // The slip first: tags stamped on it follow it.
    if (this.slipInfo) this.positionSlip();
    for (const it of this.items) this.layoutItem(it);
  }

  remove(kind) {
    this.items = this.items.filter((it) => {
      if (kind && it.kind !== kind) return true;
      it.el.remove();
      return false;
    });
  }

  clearAll() {
    this.items.forEach((it) => it.el.remove());
    this.items = [];
    this.root.replaceChildren();
    this.slipInfo = null;
    this.slip = null;
    this.seal = null;
    this.sealItem = null;
    this.callout = null;
  }

  /** Current beat's tag shrinks into a trail mark; everything else of the beat goes away. */
  toTrail() {
    for (const e of [...this.root.children]) {
      const keep = e.classList.contains('vtag') && !e.classList.contains('on-slip')
        && !e.classList.contains('ghost-real') && !e.classList.contains('preview');
      if (keep) { e.classList.remove('settled'); e.classList.add('trail'); } else e.remove();
    }
    this.items = this.items.filter((it) => this.root.contains(it.el));
    for (const it of this.items) it.after = null;
    this.slip = null;
    this.slipInfo = null;
    this.seal = null;
    this.sealItem = null;
    this.callout = null;
    // The trail mark lands on its barrier anchor now, not at the old box corner.
    for (const it of this.items) this.layoutItem(it);
  }

  // ── Obstacles (placement only): what an overlay box must not cover ──

  /** A person's figure plus the name label, in overlay px. */
  personRect(p) {
    if (this.is3d) {
      const b = this.stage.projBox(p, 0.22, 1.3);
      return { x: b.x - 4, y: b.y - 18, w: b.w + 8, h: b.h + 18 };
    }
    const s = this.stage.toScreen(p);
    const size = Math.max(13, 0.275 * this.stage.pxPerM(p));
    return { x: s.x - size * 0.95, y: s.y - size * 0.9, w: size * 1.9, h: size * 1.85 + 16 };
  }

  robotRect(p) {
    if (this.is3d) {
      const b = this.stage.projBox(p, 0.34, 1.0);
      return { x: b.x - 4, y: b.y - 16, w: b.w + 8, h: b.h + 16 };
    }
    const s = this.stage.toScreen(p);
    const size = Math.max(26, 0.6 * this.stage.pxPerM(p));
    return { x: s.x - size * 0.8, y: s.y - size * 0.8, w: size * 1.6, h: size * 1.6 + 12 };
  }

  /** Small boxes sampled along the world segment a->b. */
  pathRects(a, b, wgt) {
    const A = this.stage.toScreen(a);
    const B = this.stage.toScreen(b);
    const n = Math.max(1, Math.min(80, Math.ceil(Math.hypot(B.x - A.x, B.y - A.y) / 14)));
    const out = [];
    for (let i = 0; i <= n; i++) {
      const x = A.x + ((B.x - A.x) * i) / n;
      const y = A.y + ((B.y - A.y) * i) / n;
      out.push({ x: x - 6, y: y - 6, w: 12, h: 12, wgt });
    }
    return out;
  }

  domRect(e, pad = 0) {
    if (!e || e.hidden) return null;
    const r = e.getBoundingClientRect();
    if (!r.width) return null;
    const o = this.root.getBoundingClientRect();
    return { x: r.left - o.left - pad, y: r.top - o.top - pad, w: r.width + pad * 2, h: r.height + pad * 2 };
  }

  trailRects(wgt) {
    return [...this.root.querySelectorAll('.vtag.trail .vt-dot')].map((e) => ({ ...this.domRect(e, 4), wgt })).filter((r) => r.w);
  }

  /**
   * Obstacles for a scene: { robot, humans, targets } are world points
   * (targets: where the robot is sent / ends up; the path runs robot -> target).
   */
  sceneObstacles({ robot, humans = [], targets = [] } = {}, { slip = true, trail = true, ladder = true, pathW = 1, humanW = 3 } = {}) {
    const obs = [];
    for (const h of humans) obs.push({ ...this.personRect(h), wgt: humanW });
    if (robot) {
      obs.push({ ...this.robotRect(robot), wgt: 3 });
      for (const t of targets) {
        if (!t) continue;
        obs.push({ ...this.robotRect(t), wgt: 2 });
        obs.push(...this.pathRects(robot, t, pathW));
      }
    }
    if (slip && this.slip && !this.slipInfo?.mobile) {
      const r = this.domRect(this.slip, 4);
      if (r) obs.push({ ...r, wgt: 3 });
    }
    if (trail) obs.push(...this.trailRects(1));
    if (ladder) {
      const r = this.domRect($('ladder'), 2);
      if (r) obs.push({ ...r, wgt: 2 });
    }
    const tools = this.domRect($('view-tools'), 2);
    if (tools && this.is3d) obs.push({ ...tools, wgt: 3 });
    return obs;
  }

  /**
   * Pick the candidate rect with the least weighted overlap; order breaks ties.
   * `near` ({x, y, k}) adds k per px of distance from that point, so a box
   * stays close to what it labels unless something important is in the way.
   */
  pickRect(cands, obs, bounds, near = null) {
    let best = null;
    cands.forEach((c, i) => {
      const r = { x: c.x, y: c.y, w: c.w, h: c.h };
      let score = i * 0.5;
      if (near) score += near.k * Math.hypot(r.x + r.w / 2 - near.x, r.y + r.h / 2 - near.y);
      const out = Math.max(0, bounds.x0 - r.x) + Math.max(0, r.x + r.w - bounds.x1)
        + Math.max(0, bounds.y0 - r.y) + Math.max(0, r.y + r.h - bounds.y1);
      score += out * 400;
      for (const o of obs) score += overlapArea(r, o) * (o.wgt ?? 1);
      if (!best || score < best.score) best = { ...c, score };
    });
    return best;
  }

  // ── Command slip (the model's words; violet = untrusted intent) ──

  showSlip(proposal, { say, tape, robot, target, humans, reduced, zigzag, from }) {
    this.slip?.remove();
    const s = el('div', 'slip');
    const src = el('div', 'slip-src');
    src.append(el('span', 'slip-dot'), document.createTextNode(sourceName(proposal.source)));
    if (proposal.source === 'vla') src.title = 'VLA: 영상·언어로 동작을 만드는 AI 모델';
    s.append(src);
    if (say && !this.tabletMq.matches) s.append(el('div', 'slip-say', `“${say}”`));
    const act = el('div', 'slip-act');
    act.append(document.createTextNode(slipAction(proposal.action)));
    s.append(act);
    if (proposal.action?.speed != null) {
      const sp = el('div', 'slip-speed');
      sp.innerHTML = `<span class="req">${num(proposal.action.speed)}</span> m/s`;
      s.append(sp);
      this.slipSpeed = sp;
    } else {
      this.slipSpeed = null;
    }
    if (tape) s.append(el('div', 'slip-tape', tape));
    // Mobile: never drawn over the map; a small envelope tab above the robot instead.
    if (this.mobile) {
      const t = el('div', 'slip-tab', '✉');
      this.root.append(t);
      this.slip = t;
      this.slipInfo = { robot, mobile: true };
      this.positionSlip();
      return t;
    }
    this.root.append(s);
    this.slip = s;
    this.slipInfo = { robot, target, humans: (humans ?? []).map((h) => h.pos ?? h) };
    this.positionSlip();
    if (!reduced && from) {
      const r0 = from.getBoundingClientRect();
      const r1 = s.getBoundingClientRect();
      const dx = r0.left + r0.width / 2 - (r1.left + r1.width / 2);
      const dy = r0.top + r0.height / 2 - (r1.top + r1.height / 2);
      const frames = zigzag
        ? [
          { transform: `translate(${dx}px, ${dy}px) scale(.35)`, opacity: 0 },
          { transform: `translate(${dx * 0.66 + 30}px, ${dy * 0.66}px) scale(.55)`, opacity: 1, offset: 0.33 },
          { transform: `translate(${dx * 0.33 - 30}px, ${dy * 0.33}px) scale(.75)`, offset: 0.66 },
          { transform: 'none', opacity: 1 },
        ]
        : [{ transform: `translate(${dx}px, ${dy}px) scale(.35)`, opacity: 0 }, { transform: 'none', opacity: 1 }];
      s.animate(frames, { duration: 450, easing: 'cubic-bezier(.2,.8,.2,1)' });
    }
    return s;
  }

  positionSlip() {
    const s = this.slip;
    if (!s || !this.slipInfo) return;
    const info = this.slipInfo;
    const r = this.stage.toScreen(info.robot, this.hFor('robot'));
    if (info.mobile) {
      this.place(s, Math.max(14, Math.min(this.W - 14, r.x)), Math.max(12, Math.min(this.H - 12, r.y - (this.is3d ? 14 : 40))));
      return;
    }
    const minX = 4;
    const maxXEdge = this.W - 4;
    // Never cover the people, the earlier tags, the mode ladder or the path.
    const obs = this.sceneObstacles(
      { robot: info.robot, humans: info.humans, targets: info.target ? [info.target] : [] },
      { slip: false },
    );
    const t = info.target ? this.stage.toScreen(info.target) : { x: r.x, y: r.y - 100 };
    const dx = t.x - r.x;
    const dy = t.y - r.y;
    const solve = () => {
      const w = s.offsetWidth;
      const h = s.offsetHeight;
      const gap = this.is3d ? 18 : 30;
      const above = { x: r.x - w / 2, y: r.y - gap - h };
      const below = { x: r.x - w / 2, y: r.y + gap + 14 };
      const left = { x: r.x - gap - w, y: r.y - h / 2 };
      const right = { x: r.x + gap, y: r.y - h / 2 };
      // Preference: the side away from where the command points.
      let order;
      if (Math.abs(dy) >= Math.abs(dx)) order = [dy < 0 ? below : above, dx > 0 ? left : right, dx > 0 ? right : left, dy < 0 ? above : below];
      else order = [dx > 0 ? left : right, dy < 0 ? below : above, dy < 0 ? above : below, dx > 0 ? right : left];
      // Slid variants of each side, still anchored to the robot.
      const cands = [];
      for (const c of order) {
        cands.push(c);
        if (c === above || c === below) cands.push({ x: c.x - w * 0.45, y: c.y }, { x: c.x + w * 0.45, y: c.y });
        else cands.push({ x: c.x, y: c.y - h * 0.6 }, { x: c.x, y: c.y + h * 0.6 });
      }
      return this.pickRect(cands.map((c) => ({ ...c, w, h })), obs, { x0: minX, x1: maxXEdge, y0: 4, y1: this.H - 4 });
    };
    s.classList.remove('compact');
    let pick = solve();
    if (pick.score >= 60 && s.querySelector('.slip-say')) {
      s.classList.add('compact'); // two-line form: source + action
      pick = solve();
    }
    const x = Math.max(minX, Math.min(maxXEdge - pick.w, pick.x));
    const y = Math.max(4, Math.min(this.H - pick.h - 4, pick.y));
    s.style.left = `${Math.round(x)}px`;
    s.style.top = `${Math.round(y)}px`;
  }

  /** Slip speed line: ~~2.5~~ -> 0.2 m/s, the new value from decision.action.speed. */
  slipSpeedDiff(executed) {
    if (!this.slipSpeed) return;
    const req = this.slipSpeed.querySelector('.req')?.textContent;
    this.slipSpeed.innerHTML = `<s>${req}</s> → <b>${num(executed)}</b> m/s`;
  }

  slipTapeAlarm() {
    this.slip?.querySelector('.slip-tape')?.classList.add('alarm');
  }

  // ── Verdict tag ──

  /** Where a slip-anchored tag goes (in overlay px). */
  slipPoint() {
    const s = this.slip;
    if (!s) return null;
    if (this.slipInfo?.mobile) return { x: parseFloat(s.style.left), y: parseFloat(s.style.top), mobile: true };
    return { x: parseFloat(s.style.left) + s.offsetWidth - 10, y: parseFloat(s.style.top) + 4 };
  }

  /** Screen point of an item's anchor (clamped into the map; `docked` when it was off-map). */
  anchorPoint(it) {
    if (it.kind === 'seal-slip') {
      const p = this.slipPoint() ?? { x: this.W / 2, y: this.H / 2 };
      return { ...p, docked: false };
    }
    const s = this.stage.toScreen(it.world, it.h ?? 0);
    const off = s.behind || s.x < EDGE || s.x > this.W - EDGE || s.y < EDGE || s.y > this.H - EDGE;
    return {
      x: Math.max(EDGE, Math.min(this.W - EDGE, s.x)),
      y: Math.max(EDGE, Math.min(this.H - EDGE, s.behind ? this.H - EDGE : s.y)),
      docked: off,
    };
  }

  landSeal(decision, anchor, { reduced, tag, slam = true, settle = true } = {}) {
    const v = decision.verdict;
    const root = makeTag(decision, { tag });
    root.classList.add(reduced ? 'fade' : `enter-${v}`);
    if (this.mobile) root.parts.box.classList.add('pill');
    this.root.append(root);
    const h = this.is3d
      ? (v === 'yun' ? this.hFor('yun') : (anchor.type === 'world' ? this.hFor('barrier') : 0))
      : 0;
    const item = anchor.type === 'slip'
      ? { el: root, world: null, kind: 'seal-slip', h: 0 }
      : { el: root, world: anchor.p, kind: 'seal', h };
    if (anchor.type === 'slip') {
      root.classList.add('on-slip');
      this.slip?.classList.add('stamped');
    }
    item.layout = () => this.layoutTag(item);
    this.items.push(item);
    item.layout();
    this.seal = root;
    this.sealItem = item;
    // A pass steps aside after 1.5 s so the robot arriving under it stays visible.
    if (v === 'yun' && settle) {
      setTimeout(() => { if (root.isConnected && !root.classList.contains('trail')) root.classList.add('settled'); }, YUN_SETTLE_MS);
    }
    if (v === 'bul' && slam && !reduced) {
      const ring = el('div', 'ink-ring');
      this.root.append(ring);
      const a = this.anchorPoint(item);
      this.place(ring, a.x, a.y);
      setTimeout(() => ring.remove(), 600);
    }
    return item;
  }

  /** Place a tag's box near its anchor, avoiding what it must not cover, with a leader line. */
  layoutTag(it) {
    const root = it.el;
    const { box, dot, leader } = root.parts;
    const A = this.anchorPoint(it);
    root.classList.toggle('docked', A.docked);
    this.place(dot, A.x, A.y);
    const W = this.W;
    const H = this.H;
    if (root.classList.contains('trail')) {
      box.style.left = `${Math.round(A.x)}px`;
      box.style.top = `${Math.round(A.y)}px`;
      leader.style.width = '0px';
      return;
    }
    const w = box.offsetWidth;
    const h = box.offsetHeight;
    let pick;
    if (it.kind === 'seal-slip' && !A.mobile) {
      // Stamped on the command slip's corner, like stamping a document.
      pick = { x: A.x - w + 14, y: A.y - h + 6 };
    } else if (root.classList.contains('preview')) {
      pick = { x: A.x - w / 2, y: A.y - 22 - h };
    } else {
      const gap = this.mobile ? 12 : 18;
      const cands = [];
      const up = { x: A.x - w / 2, y: A.y - gap - h, side: 'above' };
      const rt = { x: A.x + gap, y: A.y - h - 4, side: 'right' };
      const lt = { x: A.x - gap - w, y: A.y - h - 4, side: 'left' };
      const dn = { x: A.x - w / 2, y: A.y + gap, side: 'below' };
      const order = { right: [rt, lt, up, dn], left: [lt, rt, up, dn] }[this.prefer] ?? [up, rt, lt, dn];
      for (const c of order) {
        cands.push({ ...c, w, h });
        if (c.side === 'above' || c.side === 'below') cands.push({ ...c, x: c.x - w * 0.4, w, h }, { ...c, x: c.x + w * 0.4, w, h });
        else cands.push({ ...c, y: c.y + h * 0.5 + 4, w, h }, { ...c, y: c.y - h * 0.6, w, h });
      }
      // The people the rule protects and the verdict effect itself (barrier,
      // dome, speed gate) must stay visible: they weigh as much as the anchor.
      const obs = this.sceneObstacles(this.calloutScene ?? {}, { pathW: this.mobile ? 0.35 : 1, humanW: 6 });
      obs.push({ x: A.x - 12, y: A.y - 12, w: 24, h: 24, wgt: 6 });
      if (this.is3d && !root.classList.contains('ghost-real')) {
        for (const k of ['barrier', 'dome', 'arch']) {
          const b = this.stage.screenBox?.(k);
          if (b) obs.push({ x: b.x - 3, y: b.y - 3, w: b.w + 6, h: b.h + 6, wgt: 6 });
        }
      }
      if (this.is3d) {
        const g = this.stage.screenBox?.('ghost');
        if (g) obs.push({ ...g, wgt: 3 });
      }
      const bounds = { x0: EDGE, x1: W - EDGE, y0: EDGE, y1: H - EDGE };
      pick = this.pickRect(cands, obs, bounds, { x: A.x, y: A.y, k: 1 });
      // Still over the scene (more than ~40% of the tag on what matters)? Dock it to
      // the map's top or bottom edge; the leader keeps it tied to its anchor.
      // cover: the larger of (share of the tag over the scene) and (share of the
      // most-hidden protected person / verdict effect under the tag).
      const heavy = obs.filter((o) => (o.wgt ?? 1) >= 3);
      const key = obs.filter((o) => (o.wgt ?? 1) >= 6 && o.w * o.h > 600);
      const cover = (r) => Math.max(
        Math.min(1, heavy.reduce((a, o) => a + overlapArea(r, o), 0) / Math.max(1, r.w * r.h)),
        ...key.map((o) => overlapArea(r, o) / (o.w * o.h)),
      );
      const clampR = (r) => ({ ...r, x: Math.max(EDGE, Math.min(W - EDGE - w, r.x)), y: Math.max(EDGE, Math.min(H - EDGE - h, r.y)) });
      if (cover(clampR(pick)) > 0.4) {
        const chips = this.domRect($('ctx-chips'), 2);
        const dockObs = chips ? [...obs, { ...chips, wgt: 2 }] : obs;
        const docks = [];
        for (const y of [EDGE, H - EDGE - h]) {
          for (const x of [A.x - w / 2, A.x - w * 0.9, A.x - w * 0.1, EDGE, W - EDGE - w]) docks.push({ x, y, w, h, side: 'dock' });
        }
        const d = this.pickRect(docks, dockObs, bounds, { x: A.x, y: A.y, k: 0.2 });
        if (d && cover(clampR(d)) < cover(clampR(pick))) pick = d;
      }
    }
    const x = Math.max(EDGE, Math.min(W - EDGE - w, pick.x));
    const y = Math.max(EDGE, Math.min(H - EDGE - h, pick.y));
    box.style.left = `${Math.round(x)}px`;
    box.style.top = `${Math.round(y)}px`;
    // Leader: from the anchor to the nearest point of the box.
    const nx = Math.max(x, Math.min(x + w, A.x));
    const ny = Math.max(y, Math.min(y + h, A.y));
    const L = Math.hypot(nx - A.x, ny - A.y);
    leader.style.left = `${Math.round(A.x)}px`;
    leader.style.top = `${Math.round(A.y)}px`;
    leader.style.width = `${Math.max(0, L - 2)}px`;
    leader.style.transform = `rotate(${Math.atan2(ny - A.y, nx - A.x)}rad)`;
  }

  /** The reason: the label (and on desktop "+n · 자세히"), inside the verdict tag. */
  showCallout(verdict, labelText, others, { brake, onDetail, prefer, robot, humans, targets }) {
    this.prefer = prefer ?? null;
    this.calloutScene = { robot: robot ?? null, humans: humans ?? [], targets: (targets ?? []).filter(Boolean) };
    const root = this.seal;
    if (!root) return;
    const { text, l1 } = root.parts;
    text.querySelectorAll('.vt-l2, .brake').forEach((e) => e.remove());
    l1.querySelector('.vt-label')?.remove();
    l1.append(el('span', 'vt-label', ` · ${labelText}`));
    if (!this.mobile) {
      const l2 = el('div', 'vt-l2');
      if (others > 0) l2.append(document.createTextNode(`+${others} · `));
      const more = el('button', 'co-more', '자세히 ▸');
      more.type = 'button';
      more.tabIndex = -1; // the caption's 자세히 ▸ is the keyboard path
      more.addEventListener('click', onDetail);
      l2.append(more);
      text.append(l2);
      if (brake?.length) {
        const b = el('div', 'brake');
        brake.forEach((x, i) => {
          if (i) b.append(document.createTextNode(' · '));
          b.append(x.bold ? el('b', null, x.text) : document.createTextNode(x.text));
        });
        text.append(b);
      }
    }
    root.classList.add('explained');
    this.callout = root;
    this.sealItem?.layout();
  }

  /** A DOM chip tied to a world point (e.g. 요청 2.5 -> 한계 1 m/s at the robot). */
  worldChip(text, world, cls, dy = -44) {
    const c = el('div', `speed-chip ${cls ?? ''}`, text);
    this.root.append(c);
    const h = this.hFor('chip');
    return this.track(c, world, 'chip', 0, this.is3d ? -10 : dy, h);
  }

  staleStamp() {
    const s = el('div', 'stale-stamp', '❚❚ 화면 멈춤');
    this.root.append(s);
    const it = { el: s, world: null, kind: 'stamp', layout: () => this.place(s, this.W / 2, this.H * 0.22) };
    this.items.push(it);
    it.layout();
  }

  /** Aim preview tag in the lab (a real labGate.judge() Decision). */
  preview(decision, world) {
    const old = this.items.find((i) => i.kind === 'preview');
    if (old) { old.el.remove(); this.items = this.items.filter((i) => i !== old); }
    if (!decision) return;
    const root = makeTag(decision, { tag: '미리보기 (실제 판정)' });
    root.classList.add('preview');
    this.root.append(root);
    const item = { el: root, world, kind: 'preview', h: 0 };
    item.layout = () => this.layoutTag(item);
    this.items.push(item);
    item.layout();
  }
}

function slipAction(a) {
  if (!a) return '?';
  const p = (q) => `(${num(q.x)}, ${num(q.y)})`;
  switch (a.type) {
    case 'move_to': return `이동 ${p(a.goal)}`;
    case 'grasp': return `집기 ${({ knife: '칼', cup: '컵', toy: '장난감' })[a.object] ?? a.object} ${p(a.at)}`;
    case 'place': return `놓기 ${p(a.at)}`;
    case 'stop': return '정지';
    default: return a.type;
  }
}

// ───────────────────────── Strip, chips, ladder, progress ─────────────────────────

const SRC_ICON = {
  vla: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 2l7 8-7 8-7-8z" fill="none" stroke="currentColor" stroke-width="2"/></svg>',
  planner: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 15l5-5 3 3 6-7" fill="none" stroke="currentColor" stroke-width="2"/></svg>',
  teleop: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="7" r="3.5" fill="none" stroke="currentColor" stroke-width="2"/><path d="M3 18c1-4 4-5.5 7-5.5s6 1.5 7 5.5" fill="none" stroke="currentColor" stroke-width="2"/></svg>',
  peer: '<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="4" y="5" width="12" height="10" rx="3" fill="none" stroke="currentColor" stroke-width="2"/><circle cx="8" cy="10" r="1.3" fill="currentColor"/><circle cx="12" cy="10" r="1.3" fill="currentColor"/></svg>',
  model: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 2l7 8-7 8-7-8z" fill="none" stroke="currentColor" stroke-width="2"/></svg>',
};

export class Strip {
  constructor() {
    this.root = $('strip');
    this.src = $('strip-src');
    this.cap = $('strip-cap');
    this.idle();
  }

  idle() {
    this.root.dataset.state = 'idle';
    this.root.classList.remove('struck', 'bypass', 'zigzag');
    this.setSource(null);
  }

  setSource(source, policy) {
    const icon = this.src.querySelector('.src-icon');
    const name = this.src.querySelector('.src-name');
    const note = this.src.querySelector('.src-note');
    icon.innerHTML = SRC_ICON[source] ?? SRC_ICON.model;
    name.textContent = source ? sourceName(source) : 'AI 모델';
    // Policy configuration (like zones on the floor), not a verdict.
    const unlisted = source && policy && !(policy.allowed_sources ?? []).includes(source);
    note.textContent = unlisted ? '⚠ 미등록' : '';
    note.hidden = !unlisted;
    this.root.classList.remove('struck');
    this.src.querySelector('.src-log').hidden = true;
  }

  set(state) {
    this.root.dataset.state = state;
  }

  verdict(decision) {
    this.root.dataset.state = decision.verdict;
    this.cap.textContent = decision.verdict === 'jeol' && decision.speed_cap != null ? `≤${num(decision.speed_cap)} m/s` : '';
  }

  strike() { this.root.classList.add('struck'); }
  logged() { this.src.querySelector('.src-log').hidden = false; }
  bypass(on) { this.root.classList.toggle('bypass', on); if (on) this.root.dataset.state = 'bypass'; }
  zigzag(on) { this.root.classList.toggle('zigzag', on); }
}

export function setChips(list) {
  const box = $('ctx-chips');
  box.replaceChildren(...list.map((c) => el('span', `ctx-chip ${c.tone ?? ''}`, c.text)));
}

export function setLadder(mode, visible, pulse = false) {
  const lad = $('ladder');
  lad.hidden = !visible;
  const idx = MODES.indexOf(mode);
  for (const li of lad.children) {
    const i = MODES.indexOf(li.dataset.mode);
    li.classList.toggle('lit', i === idx);
    li.classList.toggle('below', i < idx);
    li.classList.remove('pulse');
    if (li.dataset.mode === mode) li.setAttribute('aria-current', 'step'); else li.removeAttribute('aria-current');
  }
  if (pulse) {
    const lit = lad.querySelector('.lit');
    if (lit) { void lit.offsetWidth; lit.classList.add('pulse'); }
  }
  const vig = $('fx-vignette');
  vig.dataset.mode = mode === 'normal' ? '' : mode === 'caution' ? 'caution' : 'hold';
}

export function buildLadder() {
  const lad = $('ladder');
  lad.replaceChildren(...[...MODES].reverse().map((m) => {
    const li = el('li', null, MODE_KO[m]);
    li.dataset.mode = m;
    return li;
  }));
}

/** Beat dots with recorded verdict icons, or the 7-slot filmstrip for card 8. */
export function setProgress({ total, index, verdicts = [], film = false }) {
  const box = $('progress');
  box.className = film ? 'progress film' : 'progress';
  box.replaceChildren();
  if (!total) { box.removeAttribute('role'); box.removeAttribute('aria-label'); return; }
  for (let i = 0; i < total; i++) {
    const v = verdicts[i];
    const d = el('span', `dot${i === index ? ' cur' : ''}${v ? ` v-${v}` : ''}`);
    if (v) d.innerHTML = verdictIcon(v);
    else d.textContent = film ? String(i + 1) : '';
    d.setAttribute('aria-hidden', 'true');
    box.append(d);
  }
  const words = verdicts.filter(Boolean).map((v) => VERDICT_UI[v]?.word).filter(Boolean);
  const head = film ? `명령 ${words.length}/${total}` : `장면 ${index + 1}/${total}`;
  box.setAttribute('role', 'img');
  box.setAttribute('aria-label', words.length ? `${head}, 지난 판정: ${words.join(', ')}` : head);
}

