// Top-down canvas map. World coordinates are metres with y pointing UP;
// the canvas has y pointing down, so every conversion goes through
// toScreen()/toWorld(). The map only draws what it is given.

import { VERDICTS, num, objectEmoji } from './explain.js';

const MARGIN = 28;          // px around the drawn area (axis labels live here)
const DRAG_THRESHOLD = 6;   // px of movement before a press becomes a drag
const PAD_M = 0.5;          // m drawn beyond workspace ∪ zones, so targets outside the envelope are clickable
const GRID_MIN_PX = 20;     // grid lines at least this far apart on screen
const GRID_MAX_LINES = 500; // per axis; beyond this the grid is skipped
const MIN_MAP_PX = 160;     // minimum drawable height, so a long thin workspace never gets scale 0

const PALETTE_VARS = [
  'map-bg', 'map-outside', 'grid', 'grid-label', 'workspace',
  'zone-noentry', 'zone-noentry-line', 'zone-speed', 'zone-speed-line',
  'robot', 'robot-fill', 'adult', 'child', 'text', 'text-dim',
  'yun', 'jeol', 'bul', 'focus', 'tooltip-bg', 'tooltip-text',
  'tooltip-yun', 'tooltip-jeol', 'tooltip-bul',
  'mode-caution', 'mode-hold', 'mode-estop', 'font',
];

export class MapView {
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {HTMLElement} wrap  container whose width the canvas follows
   * @param {object} handlers   { onPointer(p|null), onCommit(p), onHumanMove(id,p), onHumanDrop(id), getHumans() }
   */
  constructor(canvas, wrap, handlers) {
    this.canvas = canvas;
    this.wrap = wrap;
    this.ctx = canvas.getContext('2d');
    this.h = handlers;
    this.inner = { minX: 0, minY: 0, maxX: 10, maxY: 10 };  // workspace ∪ zones
    this.bounds = { minX: 0, minY: 0, maxX: 10, maxY: 10 }; // inner + padding: the drawn area
    this.cssW = 0;
    this.cssH = 0;
    this.drag = null;
    this.readPalette();

    new ResizeObserver(() => this.resize()).observe(wrap);
    window.addEventListener('resize', () => this.resize(true));
    matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => this.readPalette());
    this.bindPointer();
  }

  readPalette() {
    const cs = getComputedStyle(document.documentElement);
    this.pal = {};
    for (const v of PALETTE_VARS) this.pal[v] = cs.getPropertyValue(`--${v}`).trim();
  }

  /** Derive the drawn area from the policy: workspace ∪ zones, plus a margin outside the envelope. */
  setPolicy(policy) {
    const ws = policy.envelope.workspace;
    const rects = [ws, ...(policy.zones ?? []).map((z) => z.area)];
    const b = {
      minX: Math.min(...rects.map((r) => r.min.x)),
      minY: Math.min(...rects.map((r) => r.min.y)),
      maxX: Math.max(...rects.map((r) => r.max.x)),
      maxY: Math.max(...rects.map((r) => r.max.y)),
    };
    // Guard against a zero-size workspace.
    if (b.maxX - b.minX < 1) b.maxX = b.minX + 1;
    if (b.maxY - b.minY < 1) b.maxY = b.minY + 1;
    this.inner = b;
    const pad = Math.max(PAD_M, 0.05 * Math.max(b.maxX - b.minX, b.maxY - b.minY));
    this.bounds = { minX: b.minX - pad, minY: b.minY - pad, maxX: b.maxX + pad, maxY: b.maxY + pad };
    this.resize(true);
  }

  /** Fit the canvas to its container, HiDPI-crisp. */
  resize(force = false) {
    const W = this.wrap.clientWidth;
    if (!W || (!force && W === this.cssW)) return;
    const { minX, minY, maxX, maxY } = this.bounds;
    const spanX = maxX - minX;
    const spanY = maxY - minY;
    // Stacked (narrow) layouts keep the map shorter so the verdict stays in view.
    const maxH = Math.max(280, window.innerHeight * (window.innerWidth <= 900 ? 0.55 : 0.72));
    const ideal = (W - 2 * MARGIN) * (spanY / spanX) + 2 * MARGIN;
    const H = Math.round(Math.min(Math.max(ideal || 0, 2 * MARGIN + MIN_MAP_PX), maxH));

    this.cssW = W;
    this.cssH = H;
    this.canvas.style.height = `${H}px`;
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.round(W * dpr);
    this.canvas.height = Math.round(H * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    this.scale = Math.min((W - 2 * MARGIN) / spanX, (H - 2 * MARGIN) / spanY);
    // A span too large for doubles (e.g. ±1e308) cannot be drawn: mark the map unusable.
    if (!Number.isFinite(spanX) || !Number.isFinite(spanY) || !(this.scale > 0) || !Number.isFinite(this.scale)) this.scale = 0;
    this.ox = (W - spanX * this.scale) / 2;
    this.oy = (H - spanY * this.scale) / 2;
  }

  /** Whether screen ↔ world conversion is meaningful for the current policy. */
  usable() {
    return this.scale > 0;
  }

  toScreen(p) {
    return {
      x: this.ox + (p.x - this.bounds.minX) * this.scale,
      y: this.oy + (this.bounds.maxY - p.y) * this.scale, // y flips here
    };
  }

  toWorld(sx, sy) {
    return {
      x: this.bounds.minX + (sx - this.ox) / this.scale,
      y: this.bounds.maxY - (sy - this.oy) / this.scale,
    };
  }

  /** Keep a point inside the drawn area. */
  clamp(p) {
    const b = this.bounds;
    return {
      x: Math.min(b.maxX, Math.max(b.minX, p.x)),
      y: Math.min(b.maxY, Math.max(b.minY, p.y)),
    };
  }

  /** Snap to 5 cm so proposals read cleanly in the log. */
  static snap(p) {
    return { x: Math.round(p.x * 20) / 20, y: Math.round(p.y * 20) / 20 };
  }

  /** The world point under a pointer event, or null when the map cannot be used. */
  eventToWorld(e) {
    if (!this.usable()) return null;
    const r = this.canvas.getBoundingClientRect();
    return this.toWorld(e.clientX - r.left, e.clientY - r.top);
  }

  humanRadiusPx(h) {
    return Math.max(h.class === 'child' ? 8 : 11, (h.class === 'child' ? 0.18 : 0.26) * this.scale);
  }

  hitHuman(e) {
    const r = this.canvas.getBoundingClientRect();
    const sx = e.clientX - r.left;
    const sy = e.clientY - r.top;
    const humans = this.h.getHumans();
    for (let i = humans.length - 1; i >= 0; i--) {
      const s = this.toScreen(humans[i].pos);
      const rad = Math.max(16, this.humanRadiusPx(humans[i]) + 4);
      if (Math.hypot(s.x - sx, s.y - sy) <= rad) return humans[i];
    }
    return null;
  }

  // ───────────── Pointer input (mouse, pen and touch) ─────────────

  bindPointer() {
    const c = this.canvas;

    c.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      c.setPointerCapture(e.pointerId);
      const human = this.hitHuman(e);
      this.drag = { id: e.pointerId, human: human?.id ?? null, x0: e.clientX, y0: e.clientY, moved: false };
      if (human) c.classList.add('grabbing');
    });

    // Proposal targets are NOT clamped: a point outside the workspace goes to
    // the gate as-is, and the gate decides envelope:workspace. Only dragged
    // humans are kept on the drawn area so they cannot be lost off-canvas.
    c.addEventListener('pointermove', (e) => {
      const p = this.eventToWorld(e);
      const d = this.drag;
      if (d && d.id === e.pointerId) {
        if (!d.moved && Math.hypot(e.clientX - d.x0, e.clientY - d.y0) > DRAG_THRESHOLD) d.moved = true;
        if (d.human && d.moved) {
          if (p) this.h.onHumanMove(d.human, MapView.snap(this.clamp(p)));
          return;
        }
      }
      if (e.pointerType !== 'touch') {
        c.classList.toggle('grab', !d && !!p && !!this.hitHuman(e));
        this.h.onPointer(p ? MapView.snap(p) : null);
      }
    });

    const end = (e, cancelled) => {
      const d = this.drag;
      if (!d || d.id !== e.pointerId) return;
      this.drag = null;
      c.classList.remove('grabbing');
      if (cancelled) return;
      const p = this.eventToWorld(e);
      if (d.human && d.moved) this.h.onHumanDrop(d.human);
      else if (!d.moved && p) this.h.onCommit(MapView.snap(p));
      if (e.pointerType === 'touch') this.h.onPointer(null);
    };
    c.addEventListener('pointerup', (e) => end(e, false));
    c.addEventListener('pointercancel', (e) => end(e, true));
    c.addEventListener('pointerleave', (e) => {
      if (!this.drag && e.pointerType !== 'touch') this.h.onPointer(null);
    });
  }

  // ───────────── Drawing ─────────────

  /**
   * @param {object} vm view model:
   *   { policy, humans, robot:{pose, holding}, mode, motion, rejected, preview,
   *     cursor, grabbedHumanId, placed }
   */
  draw(vm, now) {
    const { ctx, pal } = this;
    ctx.save();
    ctx.clearRect(0, 0, this.cssW, this.cssH);
    ctx.fillStyle = pal['map-outside'];
    ctx.fillRect(0, 0, this.cssW, this.cssH);
    if (!this.usable()) {
      this.haloText('정책의 작업 공간이 너무 커서 지도를 그릴 수 없습니다', this.cssW / 2, this.cssH / 2,
        pal['text-dim'], this.font(600, 13), 'center', 'middle');
      ctx.restore();
      return;
    }

    this.drawWorkspace(vm.policy);
    this.drawZones(vm.policy);
    this.drawPlaced(vm.placed);
    this.drawRejected(vm.rejected, now);
    this.drawMotionPath(vm);
    this.drawPreview(vm);
    for (const h of vm.humans) this.drawHuman(h, h.id === vm.grabbedHumanId);
    this.drawRobot(vm);
    this.drawCursor(vm.cursor);
    this.drawTooltip(vm.preview);
    ctx.restore();
  }

  font(weight, size) {
    return `${weight} ${size}px ${this.pal.font || 'system-ui, sans-serif'}`;
  }

  /** Text with a halo so it stays readable over hatching and grid. */
  haloText(text, x, y, color, font, align = 'left', baseline = 'alphabetic') {
    const { ctx } = this;
    ctx.font = font;
    ctx.textAlign = align;
    ctx.textBaseline = baseline;
    ctx.lineJoin = 'round';
    ctx.lineWidth = 4;
    ctx.strokeStyle = this.pal['map-bg'];
    ctx.strokeText(text, x, y);
    ctx.fillStyle = color;
    ctx.fillText(text, x, y);
  }

  rectPx(r) {
    const a = this.toScreen({ x: r.min.x, y: r.max.y }); // top-left on screen
    const b = this.toScreen({ x: r.max.x, y: r.min.y }); // bottom-right on screen
    return { x: a.x, y: a.y, w: b.x - a.x, h: b.y - a.y };
  }

  /**
   * Grid step in metres from screen density: the smallest of 1·2·5·10ⁿ that is
   * at least GRID_MIN_PX apart, never below 1 m. Null when no sane grid exists.
   */
  gridStep() {
    const target = Math.max(1, GRID_MIN_PX / this.scale);
    const p = 10 ** Math.floor(Math.log10(target));
    const step = [1, 2, 5, 10].map((m) => m * p).find((v) => v >= target);
    return Number.isFinite(step) && step > 0 ? step : null;
  }

  drawWorkspace(policy) {
    const { ctx, pal, inner: b } = this;
    const all = this.rectPx({ min: { x: b.minX, y: b.minY }, max: { x: b.maxX, y: b.maxY } });
    ctx.fillStyle = pal['map-bg'];
    ctx.fillRect(all.x, all.y, all.w, all.h);

    // Grid lines at integer multiples of `step`, looped by index so the loop
    // always terminates (x += step can stall at huge magnitudes).
    const step = this.gridStep();
    const kx0 = step ? Math.ceil(b.minX / step) : 0;
    const kx1 = step ? Math.floor(b.maxX / step) : -1;
    const ky0 = step ? Math.ceil(b.minY / step) : 0;
    const ky1 = step ? Math.floor(b.maxY / step) : -1;
    const gridOk = step && kx1 - kx0 <= GRID_MAX_LINES && ky1 - ky0 <= GRID_MAX_LINES;
    if (gridOk) {
      ctx.lineWidth = 1;
      ctx.strokeStyle = pal.grid;
      ctx.beginPath();
      for (let k = kx0; k <= kx1; k++) {
        const s = this.toScreen({ x: k * step, y: 0 });
        ctx.moveTo(Math.round(s.x) + 0.5, all.y);
        ctx.lineTo(Math.round(s.x) + 0.5, all.y + all.h);
      }
      for (let k = ky0; k <= ky1; k++) {
        const s = this.toScreen({ x: 0, y: k * step });
        ctx.moveTo(all.x, Math.round(s.y) + 0.5);
        ctx.lineTo(all.x + all.w, Math.round(s.y) + 0.5);
      }
      ctx.stroke();

      // Axis labels in metres: x along the bottom, y up the left side.
      ctx.fillStyle = pal['grid-label'];
      ctx.font = this.font(500, 10);
      const stepPx = step * this.scale;
      const widest = Math.max(ctx.measureText(num(kx0 * step)).width, ctx.measureText(num(kx1 * step)).width);
      const everyX = Math.max(1, Math.ceil((widest + 8) / stepPx));
      const everyY = Math.max(1, Math.ceil(16 / stepPx));
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      for (let k = kx0; k <= kx1; k++) {
        if ((k - kx0) % everyX) continue;
        const s = this.toScreen({ x: k * step, y: b.minY });
        ctx.fillText(num(k * step), s.x, s.y + 5);
      }
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';
      for (let k = ky0; k <= ky1; k++) {
        if ((k - ky0) % everyY) continue;
        const s = this.toScreen({ x: b.minX, y: k * step });
        ctx.fillText(num(k * step), s.x - 5, s.y);
      }
      ctx.textAlign = 'left';
      ctx.textBaseline = 'bottom';
      ctx.fillText('m', all.x + all.w + 4, all.y + all.h + 16);
    }

    // Workspace border (the envelope).
    const ws = this.rectPx(policy.envelope.workspace);
    ctx.lineWidth = 2.5;
    ctx.strokeStyle = pal.workspace;
    ctx.strokeRect(ws.x, ws.y, ws.w, ws.h);
  }

  drawZones(policy) {
    const { ctx, pal } = this;
    for (const z of policy.zones ?? []) {
      const r = this.rectPx(z.area);
      ctx.save();
      if (z.no_entry) {
        ctx.fillStyle = pal['zone-noentry'];
        ctx.fillRect(r.x, r.y, r.w, r.h);
        // Diagonal hatch, clipped to the zone.
        ctx.beginPath();
        ctx.rect(r.x, r.y, r.w, r.h);
        ctx.clip();
        ctx.strokeStyle = pal['zone-noentry-line'];
        ctx.globalAlpha = 0.35;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        for (let d = -r.h; d < r.w; d += 11) {
          ctx.moveTo(r.x + d, r.y + r.h);
          ctx.lineTo(r.x + d + r.h, r.y);
        }
        ctx.stroke();
        ctx.restore();
        ctx.save();
        ctx.strokeStyle = pal['zone-noentry-line'];
        ctx.lineWidth = 2;
        ctx.strokeRect(r.x, r.y, r.w, r.h);
        this.zoneLabel([`⛔ ${z.id}`, '진입 금지'], r, pal['zone-noentry-line']);
      } else if (z.speed_limit != null) {
        ctx.fillStyle = pal['zone-speed'];
        ctx.fillRect(r.x, r.y, r.w, r.h);
        ctx.strokeStyle = pal['zone-speed-line'];
        ctx.setLineDash([6, 4]);
        ctx.lineWidth = 1.5;
        ctx.strokeRect(r.x, r.y, r.w, r.h);
        ctx.setLineDash([]);
        this.zoneLabel([z.id, `≤ ${num(z.speed_limit)} m/s`], r, pal['zone-speed-line']);
      }
      ctx.restore();
    }
  }

  /** Zone label at the zone's top-left: one line if it fits, else two; kept on the canvas. */
  zoneLabel([head, tail], r, color) {
    const { ctx } = this;
    const font = this.font(700, 12);
    ctx.font = font;
    const one = `${head} · ${tail}`;
    const lines = ctx.measureText(one).width <= r.w - 12 ? [one] : [head, tail];
    const w = Math.max(...lines.map((l) => ctx.measureText(l).width));
    const x = Math.max(4, Math.min(r.x + 6, this.cssW - w - 4));
    lines.forEach((l, i) => this.haloText(l, x, r.y + 6 + i * 15, color, font, 'left', 'top'));
  }

  drawPlaced(placed) {
    const { ctx } = this;
    for (const item of placed) {
      const s = this.toScreen(item.at);
      ctx.font = this.font(400, 18);
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(objectEmoji(item.object), s.x, s.y);
    }
  }

  verdictColor(v) {
    return { yun: this.pal.yun, jeol: this.pal.jeol, bul: this.pal.bul }[v] ?? this.pal['text-dim'];
  }

  drawCross(p, color, size) {
    const { ctx } = this;
    const s = this.toScreen(p);
    ctx.strokeStyle = color;
    ctx.lineWidth = 3.5;
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(s.x - size, s.y - size);
    ctx.lineTo(s.x + size, s.y + size);
    ctx.moveTo(s.x + size, s.y - size);
    ctx.lineTo(s.x - size, s.y + size);
    ctx.stroke();
    ctx.lineCap = 'butt';
  }

  /** bul: red dashed path with ✕ at the target. Fades but stays visible until the next commit. */
  drawRejected(rej, now) {
    if (!rej) return;
    const { ctx, pal } = this;
    const age = (now - rej.t0) / 1000;
    ctx.save();
    ctx.globalAlpha = Math.max(0.4, 1 - Math.max(0, age - 3) / 2);
    if (rej.from && rej.to) {
      const a = this.toScreen(rej.from);
      const b = this.toScreen(rej.to);
      ctx.strokeStyle = pal.bul;
      ctx.lineWidth = 2.5;
      ctx.setLineDash([8, 6]);
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
      ctx.setLineDash([]);
      this.drawCross(rej.to, pal.bul, 9);
      this.haloText('不 거부', b.x + 12, b.y - 10, pal.bul, this.font(800, 13));
    } else {
      // A rejected action with no target on the map: mark the robot.
      this.drawCross(rej.robot, pal.bul, 14);
    }
    ctx.restore();
  }

  /** The approved path while the robot executes it. */
  drawMotionPath(vm) {
    const m = vm.motion;
    if (!m || m.kind !== 'move') return;
    const { ctx } = this;
    const color = this.verdictColor(m.verdict);
    const a = this.toScreen(vm.robot.pose);
    const b = this.toScreen(m.to);
    ctx.strokeStyle = color;
    ctx.lineWidth = 2.5;
    ctx.setLineDash([4, 5]);
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.arc(b.x, b.y, 7, 0, Math.PI * 2);
    ctx.stroke();
  }

  drawPreview(vm) {
    const p = vm.preview;
    if (!p) return;
    const { ctx } = this;
    const color = p.decision ? this.verdictColor(p.decision.verdict) : this.pal['text-dim'];
    const a = this.toScreen(p.from);
    const b = this.toScreen(p.to);
    ctx.save();
    ctx.globalAlpha = 0.85;
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(b.x, b.y, 5, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
    if (p.emoji) {
      ctx.font = this.font(400, 16);
      ctx.textAlign = 'center';
      ctx.textBaseline = 'bottom';
      ctx.fillText(p.emoji, b.x, b.y - 6);
    }
    ctx.restore();
  }

  drawHuman(h, grabbed) {
    const { ctx, pal } = this;
    const s = this.toScreen(h.pos);
    const r = this.humanRadiusPx(h);
    const color = h.class === 'child' ? pal.child : pal.adult;
    ctx.beginPath();
    ctx.arc(s.x, s.y, r, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.lineWidth = grabbed ? 4 : 2;
    ctx.strokeStyle = grabbed ? pal.focus : pal['map-bg'];
    ctx.stroke();
    const label = `${h.class === 'child' ? '아이' : '어른'} ${h.id}`;
    this.haloText(label, s.x, s.y + r + 4, color, this.font(700, 12), 'center', 'top');
  }

  drawRobot(vm) {
    const { ctx, pal } = this;
    const pose = vm.robot.pose;
    const s = this.toScreen(pose);
    const r = Math.max(12, 0.3 * this.scale);
    const ring = { caution: pal['mode-caution'], hold: pal['mode-hold'], safe_park: pal['mode-estop'], estop: pal['mode-estop'] }[vm.mode] ?? pal.robot;

    // Arm (grasp / place reach animation).
    const m = vm.motion;
    let tip = null;
    if (m && m.kind === 'reach') {
      const ext = m.progress < 0.5 ? m.progress * 2 : (1 - m.progress) * 2;
      const t = this.toScreen(m.at);
      tip = { x: s.x + (t.x - s.x) * ext, y: s.y + (t.y - s.y) * ext };
      // The object waiting at the grasp point until the arm picks it up.
      if (m.op === 'grasp' && m.progress < 0.5) {
        ctx.font = this.font(400, 18);
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(objectEmoji(m.object), t.x, t.y);
      }
      ctx.strokeStyle = pal.robot;
      ctx.lineWidth = 5;
      ctx.lineCap = 'round';
      ctx.beginPath();
      ctx.moveTo(s.x, s.y);
      ctx.lineTo(tip.x, tip.y);
      ctx.stroke();
      ctx.lineCap = 'butt';
      ctx.beginPath();
      ctx.arc(tip.x, tip.y, 5, 0, Math.PI * 2);
      ctx.fillStyle = pal.robot;
      ctx.fill();
      const carried = (m.op === 'grasp' && m.progress >= 0.5) || (m.op === 'place' && m.progress < 0.5);
      if (carried && m.object) {
        ctx.font = this.font(400, 18);
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(objectEmoji(m.object), tip.x, tip.y - 12);
      }
    }

    ctx.beginPath();
    ctx.arc(s.x, s.y, r, 0, Math.PI * 2);
    ctx.fillStyle = pal['robot-fill'];
    ctx.fill();
    ctx.lineWidth = vm.mode === 'normal' ? 3 : 4.5;
    ctx.strokeStyle = ring;
    ctx.stroke();
    ctx.font = this.font(800, Math.min(14, r));
    ctx.fillStyle = pal.robot;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('H', s.x, s.y + 1);
    this.haloText('로봇', s.x, s.y + r + 4, pal.robot, this.font(700, 12), 'center', 'top');

    // Held object (hidden while it rides on the arm).
    const onArm = m && m.kind === 'reach' && ((m.op === 'place' && m.progress < 0.5) || (m.op === 'grasp' && m.progress >= 0.5));
    if (vm.robot.holding && !onArm) {
      ctx.font = this.font(400, 20);
      ctx.textAlign = 'left';
      ctx.textBaseline = 'bottom';
      ctx.fillText(objectEmoji(vm.robot.holding), s.x + r * 0.6, s.y - r * 0.5);
    }

    // Executed speed while moving; jeol shows the clamp prominently.
    if (m && m.kind === 'move') {
      const text = m.verdict === 'jeol'
        ? `節 ${num(m.speed)} m/s (요청 ${num(m.requested)})`
        : `${num(m.speed)} m/s`;
      const color = this.verdictColor(m.verdict);
      this.pill(text, s.x, s.y - r - 10, color, m.verdict === 'jeol' ? 14 : 12);
    }
  }

  /** A filled label centred at (x, bottom y). */
  pill(text, x, y, color, size) {
    const { ctx, pal } = this;
    ctx.font = this.font(800, size);
    const w = ctx.measureText(text).width + 14;
    const h = size + 10;
    let left = Math.min(Math.max(4, x - w / 2), this.cssW - w - 4);
    const top = Math.max(4, y - h);
    ctx.fillStyle = pal['map-bg'];
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    roundRect(ctx, left, top, w, h, h / 2);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = color;
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, left + 7, top + h / 2 + 1);
  }

  drawCursor(c) {
    if (!c) return;
    const { ctx, pal } = this;
    const s = this.toScreen(c);
    ctx.strokeStyle = pal.focus;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(s.x, s.y, 10, 0, Math.PI * 2);
    ctx.moveTo(s.x - 16, s.y);
    ctx.lineTo(s.x - 4, s.y);
    ctx.moveTo(s.x + 4, s.y);
    ctx.lineTo(s.x + 16, s.y);
    ctx.moveTo(s.x, s.y - 16);
    ctx.lineTo(s.x, s.y - 4);
    ctx.moveTo(s.x, s.y + 4);
    ctx.lineTo(s.x, s.y + 16);
    ctx.stroke();
  }

  /** Hover tooltip: verdict the gate would give + first fired name. */
  drawTooltip(p) {
    if (!p) return;
    const { ctx, pal } = this;
    const d = p.decision;
    const lines = d
      ? [
        `${VERDICTS[d.verdict].glyph} ${d.verdict} · ${VERDICTS[d.verdict].ko}`,
        d.fired[0] ? `${d.fired[0]}${d.fired.length > 1 ? ` 외 ${d.fired.length - 1}` : ''}` : '발동한 검사 없음',
      ]
      : ['판정 불가', p.error ?? ''];
    const pos = `(${num(p.to.x)}, ${num(p.to.y)})`;
    lines.push(pos);

    ctx.font = this.font(700, 12);
    const w = Math.max(...lines.map((l) => ctx.measureText(l).width)) + 22;
    const h = lines.length * 16 + 10;
    const s = this.toScreen(p.to);
    let x = s.x + 16;
    let y = s.y - h - 12;
    if (x + w > this.cssW - 4) x = s.x - w - 16;
    if (y < 4) y = s.y + 16;

    ctx.fillStyle = pal['tooltip-bg'];
    roundRect(ctx, x, y, w, h, 6);
    ctx.fill();
    // Accent tuned for the tooltip background (which inverts per theme).
    ctx.fillStyle = pal[`tooltip-${d?.verdict ?? 'bul'}`] || pal.bul;
    ctx.fillRect(x, y + 4, 4, h - 8);
    ctx.fillStyle = pal['tooltip-text'];
    ctx.textAlign = 'left';
    ctx.textBaseline = 'top';
    lines.forEach((l, i) => {
      ctx.font = this.font(i === 0 ? 800 : 500, 12);
      ctx.fillText(l, x + 12, y + 6 + i * 16);
    });
  }
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}
