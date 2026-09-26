// The stage: a square top-down canvas of the house. World coordinates are
// metres with y pointing UP; the canvas has y pointing down, so every
// conversion goes through toScreen()/toWorld().
//
// The stage only DRAWS a scene it is given. It never decides anything: the
// executed path, barrier, chevrons and verdict-coloured highlights exist in the
// scene only because the director copied them from a Decision returned by the
// gate. Violet (--intent) is the model's raw, untrusted command.

const DRAG_THRESHOLD = 6;
const SNAP_MS = 400;
const PAD_M = 0.4;

const TOKENS = [
  'bg', 'surface', 'surface-2', 'ink', 'ink-2', 'line', 'floor', 'room-tint', 'grid',
  'zone-slow', 'zone-deny', 'robot-body', 'robot-face', 'robot-eye', 'child', 'adult',
  'intent', 'yun-fill', 'jeol-fill', 'bul-fill', 'yun-fg', 'jeol-fg', 'bul-fg', 'seal-ink', 'focus',
];
const UI_FONT = 'system-ui, "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR", sans-serif';
const SEAL_FONT = '"Noto Serif CJK KR", "AppleMyungjo", "Batang", serif';

// Fixed art. It never enters the world JSON.
const ROOMS = [
  { name: '주방·식당', x0: 0, y0: 0, x1: 4, y1: 10, tint: false },
  { name: '복도', x0: 4, y0: 0, x1: 6, y1: 10, tint: true },
  { name: '거실', x0: 6, y0: 0, x1: 10, y1: 7, tint: false },
  { name: '', x0: 6, y0: 7, x1: 7, y1: 10, tint: false },
  { name: '아이 방', x0: 7, y0: 7, x1: 10, y1: 10, tint: true },
];
export const DECOR = {
  table: { x0: 2.4, y0: 2.5, x1: 3.9, y1: 3.6 },
  knife: { x: 3.2, y: 3.0 },
  cup: { x: 3.6, y: 3.3 },
  sink: { x0: 0.3, y0: 0.2, x1: 1.7, y1: 0.6 },
  dock: { x: 1, y: 9 },
  sofa: { x0: 7.2, y0: 1.0, x1: 9.4, y1: 1.8 },
  bed: { x0: 8.4, y0: 8.2, x1: 9.8, y1: 9.6 },
  fakeTag: { x: 7.3, y: 6.8 },
};

const clamp01 = (v) => Math.max(0, Math.min(1, v));
const easeOut = (t) => 1 - (1 - t) ** 3;
const lerp = (a, b, t) => ({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t });
export const dist = (a, b) => Math.hypot(b.x - a.x, b.y - a.y);

/** Progress 0..1 of a timed element at clock `c` (dur ≤ 0 means instant). */
export function prog(el, c) {
  if (!el) return 0;
  if (c < el.t0) return 0;
  if (!(el.dur > 0)) return 1;
  return clamp01((c - el.t0) / el.dur);
}

/** Where the robot is drawn right now (executed motion, or easing after a world jump). */
export function robotPose(scene, clock, real) {
  const e = scene.exec;
  if (e && e.kind === 'move' && clock >= e.t0) return lerp(e.from, e.to, prog(e, clock));
  const s = scene.robot.snap;
  if (s) {
    const t = scene.reduced ? 1 : clamp01((real - s.t0) / SNAP_MS);
    if (t < 1) return lerp(s.from, scene.robot.pose, easeOut(t));
  }
  return scene.robot.pose;
}

export class Stage {
  constructor(canvas, wrap) {
    this.canvas = canvas;
    this.wrap = wrap;
    this.ctx = canvas.getContext('2d');
    this.bounds = { minX: -PAD_M, minY: -PAD_M, maxX: 10 + PAD_M, maxY: 10 + PAD_M };
    this.cssW = 0;
    this.scale = 1;
    this.onResize = null;
    this.readPalette();
    new ResizeObserver(() => this.resize()).observe(wrap);
    window.addEventListener('resize', () => this.resize(true));
  }

  readPalette() {
    const cs = getComputedStyle(document.documentElement);
    this.pal = {};
    for (const t of TOKENS) this.pal[t] = cs.getPropertyValue(`--${t}`).trim() || '#888';
  }

  /** Square view of workspace ∪ zones, padded 0.4 m. */
  setPolicy(policy) {
    const rects = [policy.envelope.workspace, ...(policy.zones ?? []).map((z) => z.area)];
    let minX = Math.min(...rects.map((r) => r.min.x)) - PAD_M;
    let minY = Math.min(...rects.map((r) => r.min.y)) - PAD_M;
    let maxX = Math.max(...rects.map((r) => r.max.x)) + PAD_M;
    let maxY = Math.max(...rects.map((r) => r.max.y)) + PAD_M;
    const span = Math.max(maxX - minX, maxY - minY, 1);
    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;
    if (!Number.isFinite(span) || span > 1e6) {
      minX = -PAD_M; minY = -PAD_M; maxX = 10 + PAD_M; maxY = 10 + PAD_M;
    } else {
      minX = cx - span / 2; maxX = cx + span / 2; minY = cy - span / 2; maxY = cy + span / 2;
    }
    this.bounds = { minX, minY, maxX, maxY };
    this.resize(true);
  }

  resize(force = false) {
    const W = Math.round(this.wrap.clientWidth);
    if (!W || (!force && W === this.cssW)) return;
    this.cssW = W;
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.round(W * dpr);
    this.canvas.height = Math.round(W * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.scale = W / (this.bounds.maxX - this.bounds.minX);
    this.onResize?.();
  }

  toScreen(p) {
    return {
      x: (p.x - this.bounds.minX) * this.scale,
      y: (this.bounds.maxY - p.y) * this.scale, // y flips here
    };
  }

  toWorld(sx, sy) {
    return { x: this.bounds.minX + sx / this.scale, y: this.bounds.maxY - sy / this.scale };
  }

  clamp(p) {
    const b = this.bounds;
    return { x: Math.min(b.maxX, Math.max(b.minX, p.x)), y: Math.min(b.maxY, Math.max(b.minY, p.y)) };
  }

  static snap(p) {
    return { x: Math.round(p.x * 20) / 20, y: Math.round(p.y * 20) / 20 };
  }

  rectPx(r) {
    const a = this.toScreen({ x: r.x0 ?? r.min.x, y: r.y1 ?? r.max.y });
    const b = this.toScreen({ x: r.x1 ?? r.max.x, y: r.y0 ?? r.min.y });
    return { x: a.x, y: a.y, w: b.x - a.x, h: b.y - a.y };
  }

  font(weight, size, family = UI_FONT) {
    return `${weight} ${size}px ${family}`;
  }

  text(t, x, y, color, font, align = 'center', baseline = 'middle', halo = true) {
    const { ctx } = this;
    ctx.font = font;
    ctx.textAlign = align;
    ctx.textBaseline = baseline;
    if (halo) {
      ctx.lineJoin = 'round';
      ctx.lineWidth = 4;
      ctx.strokeStyle = this.pal.floor;
      ctx.strokeText(t, x, y);
    }
    ctx.fillStyle = color;
    ctx.fillText(t, x, y);
  }

  pill(t, x, y, color, { size = 12, fill, textColor } = {}) {
    const { ctx } = this;
    ctx.font = this.font(700, size);
    const w = ctx.measureText(t).width + 14;
    const h = size + 10;
    const left = Math.min(Math.max(2, x - w / 2), this.cssW - w - 2);
    const top = Math.min(Math.max(2, y - h / 2), this.cssW - h - 2);
    ctx.fillStyle = fill ?? this.pal.surface;
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    roundRect(ctx, left, top, w, h, h / 2);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = textColor ?? color;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(t, left + w / 2, top + h / 2 + 0.5);
  }

  // ───────────────────────── Drawing ─────────────────────────

  render(scene, clock, real) {
    const { ctx, pal } = this;
    const W = this.cssW;
    if (!W || !scene.policy) return;
    ctx.save();
    ctx.clearRect(0, 0, W, W);
    ctx.fillStyle = pal['surface-2'];
    ctx.fillRect(0, 0, W, W);
    this.focus = scene.focus?.length ? new Set(scene.focus) : null;

    this.drawRooms();
    this.drawZones(scene, clock);
    this.drawWorkspace(scene, clock);
    const pose = robotPose(scene, clock, real);
    const holding = this.displayHolding(scene, clock);
    const ghostHolding = scene.ghost ? this.ghostHolding(scene.ghost, clock) : null;
    this.drawDecor(scene, holding, ghostHolding, real);
    this.drawHighlights(scene, clock, real, 'under');
    this.drawIntent(scene, clock);
    this.drawExec(scene, clock);
    this.drawGhostPath(scene, clock);
    this.drawBarrier(scene, clock);
    for (const h of scene.world.humans ?? []) this.drawHuman(h, scene, real);
    if (scene.ghost) this.drawGhost(scene, clock, real);
    ctx.save();
    ctx.globalAlpha = scene.realAlpha ?? 1;
    this.drawRobot(scene, pose, holding, clock, real);
    ctx.restore();
    this.drawHighlights(scene, clock, real, 'over');
    this.drawLab(scene, pose);
    ctx.restore();
  }

  alphaFor(id) {
    return !this.focus || this.focus.has(id) ? 1 : 0.55;
  }

  drawRooms() {
    const { ctx, pal } = this;
    for (const r of ROOMS) {
      const px = this.rectPx(r);
      ctx.fillStyle = r.tint ? pal['room-tint'] : pal.floor;
      ctx.fillRect(px.x, px.y, px.w, px.h);
    }
    // 1 m grid.
    ctx.strokeStyle = pal.grid;
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let k = 0; k <= 10; k++) {
      const a = this.toScreen({ x: k, y: 0 });
      const b = this.toScreen({ x: k, y: 10 });
      ctx.moveTo(Math.round(a.x) + 0.5, a.y);
      ctx.lineTo(Math.round(b.x) + 0.5, b.y);
      const c = this.toScreen({ x: 0, y: k });
      const d = this.toScreen({ x: 10, y: k });
      ctx.moveTo(c.x, Math.round(c.y) + 0.5);
      ctx.lineTo(d.x, Math.round(d.y) + 0.5);
    }
    ctx.stroke();
    const size = this.cssW < 420 ? 11 : 12;
    for (const r of ROOMS) {
      if (!r.name) continue;
      const p = this.toScreen({ x: r.x0, y: r.y1 });
      this.text(r.name, p.x + 6, p.y + 6, pal['ink-2'], this.font(600, size), 'left', 'top');
    }
  }

  zoneHighlight(scene, id, clock) {
    return (scene.hl ?? []).find((h) => h.kind === 'zone' && h.id === id && clock >= h.t0);
  }

  drawZones(scene, clock) {
    const { ctx, pal } = this;
    for (const z of scene.policy.zones ?? []) {
      const r = this.rectPx(z.area);
      const hl = this.zoneHighlight(scene, z.id, clock);
      const za = this.alphaFor(z.id);
      let boost = 0;
      if (hl) {
        const t = (clock - hl.t0) / 1000;
        boost = scene.reduced ? 0.6 : (t < 1.2 ? Math.abs(Math.sin(t * Math.PI * 1.7)) : 0.6);
      }
      ctx.save();
      ctx.beginPath();
      ctx.rect(r.x, r.y, r.w, r.h);
      ctx.clip();
      if (z.no_entry) {
        ctx.globalAlpha = (0.14 + boost * 0.3) * za;
        ctx.strokeStyle = pal['zone-deny'];
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        for (let d = -r.h; d < r.w; d += 10) {
          ctx.moveTo(r.x + d, r.y + r.h); ctx.lineTo(r.x + d + r.h, r.y);
          ctx.moveTo(r.x + d, r.y); ctx.lineTo(r.x + d + r.h, r.y + r.h);
        }
        ctx.stroke();
      } else if (z.speed_limit != null) {
        ctx.globalAlpha = (0.18 + boost * 0.3) * za;
        ctx.strokeStyle = pal['zone-slow'];
        ctx.lineWidth = 2;
        ctx.beginPath();
        for (let d = -r.h; d < r.w; d += 12) {
          ctx.moveTo(r.x + d, r.y + r.h); ctx.lineTo(r.x + d + r.h, r.y);
        }
        ctx.stroke();
      }
      ctx.restore();
      ctx.save();
      ctx.globalAlpha = za;
      if (z.no_entry) {
        ctx.setLineDash([6, 4]);
        ctx.strokeStyle = pal['zone-deny'];
        ctx.lineWidth = 2;
        ctx.strokeRect(r.x + 1, r.y + 1, r.w - 2, r.h - 2);
        ctx.setLineDash([]);
        const c = { x: r.x + r.w / 2, y: r.y + r.h / 2 };
        const lockScale = hl && !scene.reduced ? 1 + 0.35 * Math.max(0, 1 - (clock - hl.t0) / 900) : 1;
        this.drawLock(c.x, c.y - 8, Math.max(9, Math.min(16, r.w / 12)) * lockScale, pal['zone-deny']);
        this.pill('출입 금지', c.x, c.y + 18, pal['zone-deny'], { size: this.cssW < 420 ? 11 : 12 });
      } else if (z.speed_limit != null) {
        const c = this.toScreen({ x: (z.area.min.x + z.area.max.x) / 2, y: Math.min(z.area.max.y, 10) - 1.1 });
        const pulse = hl && !scene.reduced ? 1 + 0.25 * Math.max(0, Math.sin(Math.min(1, (clock - hl.t0) / 600) * Math.PI)) : 1;
        this.drawRoundel(c.x, c.y, pulse, `${fmt(z.speed_limit)} m/s`, pal['zone-slow']);
      }
      ctx.restore();
    }
  }

  drawLock(x, y, s, color) {
    const { ctx } = this;
    ctx.save();
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = Math.max(1.5, s / 6);
    ctx.beginPath();
    ctx.arc(x, y - s * 0.25, s * 0.42, Math.PI, 0);
    ctx.stroke();
    roundRect(ctx, x - s * 0.62, y - s * 0.25, s * 1.24, s * 0.95, s * 0.15);
    ctx.fill();
    ctx.restore();
  }

  /** Speed-limit sign: one readable label ("0.3 m/s") in a thick zone-coloured pill. */
  drawRoundel(x, y, pulse, label, color) {
    const { ctx, pal } = this;
    const size = (this.cssW < 420 ? 12 : 13) * pulse;
    ctx.font = this.font(800, size);
    const w = ctx.measureText(label).width + 14;
    const h = size + 12;
    ctx.beginPath();
    roundRect(ctx, x - w / 2, y - h / 2, w, h, h / 2);
    ctx.fillStyle = pal.surface;
    ctx.fill();
    ctx.lineWidth = 3;
    ctx.strokeStyle = color;
    ctx.stroke();
    this.text(label, x, y + 0.5, pal.ink, this.font(800, size), 'center', 'middle', false);
  }

  drawWorkspace(scene, clock) {
    const { ctx, pal } = this;
    const ws = scene.policy.envelope.workspace;
    const r = this.rectPx(ws);
    const hl = (scene.hl ?? []).find((h) => h.kind === 'workspace' && clock >= h.t0);
    ctx.save();
    ctx.strokeStyle = hl ? pal['bul-fg'] : pal.line;
    ctx.lineWidth = hl ? 3 : 1.5;
    if (hl && !scene.reduced) ctx.globalAlpha = 0.5 + 0.5 * Math.abs(Math.sin((clock - hl.t0) / 150));
    ctx.strokeRect(r.x, r.y, r.w, r.h);
    ctx.restore();
  }

  displayHolding(scene, clock) {
    const e = scene.exec;
    if (e && e.kind === 'reach' && clock >= e.t0) return prog(e, clock) >= 0.5 ? e.holdAfter : e.holdBefore;
    return scene.robot.holding;
  }

  ghostHolding(g, clock) {
    if (g.kind === 'reach') return prog(g, clock) >= 0.5 ? g.holdAfter : g.holdBefore;
    return g.holding;
  }

  drawDecor(scene, holding, ghostHolding, real) {
    const { ctx, pal } = this;
    const px = (r) => this.rectPx(r);
    ctx.save();
    // Sink counter.
    let r = px(DECOR.sink);
    ctx.globalAlpha = this.alphaFor('sink');
    ctx.fillStyle = pal.surface;
    ctx.strokeStyle = pal.line;
    ctx.lineWidth = 1.5;
    roundRect(ctx, r.x, r.y, r.w, r.h, 3); ctx.fill(); ctx.stroke();
    ctx.beginPath();
    ctx.ellipse(r.x + r.w * 0.5, r.y + r.h / 2, r.w * 0.16, r.h * 0.28, 0, 0, Math.PI * 2);
    ctx.stroke();
    // Table.
    r = px(DECOR.table);
    ctx.globalAlpha = this.alphaFor('table');
    ctx.fillStyle = pal['room-tint'];
    roundRect(ctx, r.x, r.y, r.w, r.h, 6); ctx.fill(); ctx.stroke();
    this.text('식탁', r.x + r.w / 2, r.y + r.h - 9, pal['ink-2'], this.font(600, 11), 'center', 'middle', false);
    // Cup.
    let c = this.toScreen(DECOR.cup);
    ctx.fillStyle = pal.surface;
    ctx.beginPath(); ctx.arc(c.x, c.y, Math.max(4, 0.09 * this.scale), 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    // Knife on the table when nobody holds it.
    if (holding !== 'knife' && ghostHolding !== 'knife') {
      c = this.toScreen(DECOR.knife);
      ctx.globalAlpha = this.alphaFor('knife');
      this.drawKnife(c.x, c.y, -0.5, Math.max(16, 0.34 * this.scale));
    }
    // Dock.
    c = this.toScreen(DECOR.dock);
    ctx.globalAlpha = this.alphaFor('dock');
    const s = Math.max(14, 0.42 * this.scale);
    ctx.fillStyle = pal.surface;
    roundRect(ctx, c.x - s / 2, c.y - s / 2, s, s, 4); ctx.fill(); ctx.stroke();
    ctx.fillStyle = pal['jeol-fg'];
    ctx.beginPath();
    ctx.moveTo(c.x + s * 0.08, c.y - s * 0.34); ctx.lineTo(c.x - s * 0.2, c.y + s * 0.05); ctx.lineTo(c.x, c.y + s * 0.05);
    ctx.lineTo(c.x - s * 0.08, c.y + s * 0.34); ctx.lineTo(c.x + s * 0.2, c.y - s * 0.05); ctx.lineTo(c.x, c.y - s * 0.05);
    ctx.closePath(); ctx.fill();
    this.text('충전소', c.x, c.y + s / 2 + 8, pal['ink-2'], this.font(600, 11));
    // Sofa.
    r = px(DECOR.sofa);
    ctx.globalAlpha = this.alphaFor('sofa');
    ctx.fillStyle = pal['room-tint'];
    ctx.strokeStyle = pal.line;
    roundRect(ctx, r.x, r.y, r.w, r.h, 8); ctx.fill(); ctx.stroke();
    this.text('소파', r.x + r.w / 2, r.y + r.h / 2, pal['ink-2'], this.font(600, 11), 'center', 'middle', false);
    // Bed and teddy.
    r = px(DECOR.bed);
    ctx.globalAlpha = this.alphaFor('child-room');
    ctx.fillStyle = pal.surface;
    roundRect(ctx, r.x, r.y, r.w, r.h, 6); ctx.fill(); ctx.stroke();
    ctx.fillStyle = pal['room-tint'];
    roundRect(ctx, r.x + 4, r.y + 4, r.w - 8, r.h * 0.25, 4); ctx.fill();
    const t = { x: r.x + r.w * 0.3, y: r.y + r.h * 0.62 };
    ctx.fillStyle = '#b07a4a';
    ctx.beginPath(); ctx.arc(t.x, t.y, Math.max(3.5, r.w * 0.07), 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.arc(t.x - r.w * 0.05, t.y - r.w * 0.06, Math.max(1.5, r.w * 0.03), 0, Math.PI * 2);
    ctx.arc(t.x + r.w * 0.05, t.y - r.w * 0.06, Math.max(1.5, r.w * 0.03), 0, Math.PI * 2); ctx.fill();
    // Fake "충전소" tag (card 2).
    const d = scene.decor ?? {};
    if (d.fakeTag || d.peelT) {
      let a = 1;
      let fall = 0;
      if (d.peelT) {
        const k = scene.reduced ? 1 : clamp01((real - d.peelT) / 400);
        a = 1 - k; fall = k * 14;
      }
      if (a > 0) {
        c = this.toScreen(DECOR.fakeTag);
        ctx.globalAlpha = a * this.alphaFor('fakeTag');
        const q = Math.max(16, 0.4 * this.scale);
        ctx.save();
        ctx.translate(c.x, c.y + fall);
        ctx.rotate(0.12 + fall / 40);
        ctx.fillStyle = '#fff';
        ctx.strokeStyle = pal.intent;
        ctx.lineWidth = 2;
        ctx.fillRect(-q / 2, -q / 2, q, q);
        ctx.strokeRect(-q / 2, -q / 2, q, q);
        ctx.fillStyle = '#222';
        const cell = q / 5;
        const bits = [1, 1, 0, 1, 1, 1, 0, 1, 0, 1, 0, 1, 1, 0, 0, 1, 0, 0, 1, 1, 1, 1, 0, 1, 1];
        bits.forEach((b, i) => { if (b) ctx.fillRect(-q / 2 + (i % 5) * cell + 1, -q / 2 + Math.floor(i / 5) * cell + 1, cell - 1, cell - 1); });
        ctx.restore();
        this.text("가짜 '충전소'", c.x, c.y + q / 2 + 10 + fall, pal.intent, this.font(700, 11));
      }
    }
    ctx.restore();
  }

  drawKnife(x, y, angle, len) {
    const { ctx, pal } = this;
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(angle);
    // Handle.
    ctx.fillStyle = '#6b4a2b';
    roundRect(ctx, -len * 0.5, -len * 0.07, len * 0.38, len * 0.14, len * 0.05);
    ctx.fill();
    // Blade with a thin danger outline (not a verdict colour fill).
    ctx.beginPath();
    ctx.moveTo(-len * 0.12, -len * 0.09);
    ctx.lineTo(len * 0.36, -len * 0.06);
    ctx.lineTo(len * 0.5, len * 0.02);
    ctx.lineTo(-len * 0.12, len * 0.09);
    ctx.closePath();
    ctx.fillStyle = '#d7dde3';
    ctx.fill();
    ctx.strokeStyle = pal['bul-fg'];
    ctx.lineWidth = 1.2;
    ctx.stroke();
    ctx.restore();
  }

  drawHighlights(scene, clock, real, layer) {
    const { ctx, pal } = this;
    for (const h of scene.hl ?? []) {
      if (clock < h.t0) continue;
      const a = scene.reduced ? 1 : clamp01((clock - h.t0) / 250);
      ctx.save();
      ctx.globalAlpha = a;
      if (h.kind === 'ring' && layer === 'under') {
        const c = this.toScreen(h.center);
        const rad = h.r * this.scale;
        ctx.setLineDash([7, 5]);
        ctx.lineWidth = 2.5;
        ctx.strokeStyle = h.tone === 'bul' ? pal['bul-fg'] : pal['jeol-fg'];
        ctx.fillStyle = h.tone === 'bul' ? pal['bul-fill'] : pal['jeol-fill'];
        ctx.beginPath();
        ctx.arc(c.x, c.y, rad, 0, Math.PI * 2);
        ctx.globalAlpha = a * 0.08;
        ctx.fill();
        ctx.globalAlpha = a;
        ctx.stroke();
        ctx.setLineDash([]);
        const lp = { x: c.x, y: c.y - rad };
        this.pill(`${fmt(h.r)} m`, lp.x, lp.y, h.tone === 'bul' ? pal['bul-fg'] : pal['jeol-fg'], { size: 11 });
      } else if (h.kind === 'glint' && layer === 'over' && !scene.reduced) {
        const k = (clock - h.t0) / 500;
        if (k < 2) {
          const p = robotPose(scene, clock, real);
          const s = this.toScreen(p);
          const off = Math.max(14, 0.36 * this.scale);
          ctx.globalAlpha = Math.sin((k % 1) * Math.PI);
          ctx.strokeStyle = '#fff';
          ctx.lineWidth = 2;
          const gx = s.x + off * 1.1;
          const gy = s.y - off * 0.2;
          ctx.beginPath();
          ctx.moveTo(gx - 7, gy); ctx.lineTo(gx + 7, gy); ctx.moveTo(gx, gy - 7); ctx.lineTo(gx, gy + 7);
          ctx.stroke();
        }
      } else if (h.kind === 'shield' && layer === 'over') {
        const p = robotPose(scene, clock, real);
        const s = this.toScreen(p);
        const rad = Math.max(26, 0.62 * this.scale);
        ctx.strokeStyle = pal.intent;
        ctx.lineWidth = 2;
        ctx.setLineDash([4, 3]);
        ctx.beginPath();
        for (let i = 0; i < 6; i++) {
          const ang = Math.PI / 6 + (i * Math.PI) / 3;
          const x = s.x + rad * Math.cos(ang);
          const y = s.y + rad * Math.sin(ang);
          if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y);
        }
        ctx.closePath();
        ctx.stroke();
        ctx.setLineDash([]);
        // A violet zig-zag signal bouncing off the shield (from the top-right).
        const k = scene.reduced ? 1 : clamp01((clock - h.t0) / 700);
        const start = { x: s.x + rad * 2.6, y: s.y - rad * 2.2 };
        const hit = { x: s.x + rad * 0.75, y: s.y - rad * 0.65 };
        const back = { x: s.x + rad * 2.4, y: s.y - rad * 0.2 };
        ctx.strokeStyle = pal.intent;
        ctx.lineWidth = 2;
        const zig = (a0, b0, t) => {
          const n = 6;
          ctx.moveTo(a0.x, a0.y);
          for (let i = 1; i <= n * t; i++) {
            const q = lerp(a0, b0, i / n);
            const nx = -(b0.y - a0.y); const ny = b0.x - a0.x; const L = Math.hypot(nx, ny) || 1;
            const o = (i % 2 ? 5 : -5);
            ctx.lineTo(q.x + (nx / L) * o, q.y + (ny / L) * o);
          }
        };
        ctx.beginPath();
        zig(start, hit, Math.min(1, k * 2));
        if (k > 0.5) zig(hit, back, (k - 0.5) * 2);
        ctx.stroke();
      }
      ctx.restore();
    }
  }

  drawArrowPath(a, b, color, { dash, width, head = true, alpha = 1 }) {
    const { ctx } = this;
    ctx.save();
    ctx.globalAlpha *= alpha;
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = width;
    ctx.lineCap = 'round';
    if (dash) ctx.setLineDash(dash);
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
    ctx.setLineDash([]);
    if (head && dist(a, b) > 8) {
      const ang = Math.atan2(b.y - a.y, b.x - a.x);
      const hs = 9;
      ctx.beginPath();
      ctx.moveTo(b.x, b.y);
      ctx.lineTo(b.x - hs * Math.cos(ang - 0.45), b.y - hs * Math.sin(ang - 0.45));
      ctx.lineTo(b.x - hs * Math.cos(ang + 0.45), b.y - hs * Math.sin(ang + 0.45));
      ctx.closePath();
      ctx.fill();
    }
    ctx.restore();
  }

  drawIntent(scene, clock) {
    const it = scene.intent;
    if (!it || clock < it.t0) return;
    const { pal, ctx } = this;
    let alpha = it.alpha ?? 1;
    if (it.fadeT0 != null && clock >= it.fadeT0) alpha *= 1 - clamp01((clock - it.fadeT0) / 400);
    if (alpha <= 0) return;
    const a = this.toScreen(it.from);
    const b = this.toScreen(it.to);
    const p = prog(it, clock);
    const end = lerp(a, b, p);
    if (it.crack && clock >= it.crack.t0) {
      // Up to the barrier: intact. Beyond: four fading segments.
      const cut = this.toScreen(it.crack.at);
      this.drawArrowPath(a, cut, pal.intent, { dash: [6, 6], width: 2, head: false, alpha });
      const k = scene.reduced ? 1 : clamp01((clock - it.crack.t0) / 400);
      const fade = 1 - 0.7 * k;
      for (let i = 0; i < 4; i++) {
        const s0 = lerp(cut, b, i / 4 + 0.04);
        const s1 = lerp(cut, b, (i + 1) / 4 - 0.04);
        const off = k * (i % 2 ? 3 : -3);
        this.drawArrowPath({ x: s0.x, y: s0.y + off }, { x: s1.x, y: s1.y - off }, pal.intent,
          { dash: [6, 6], width: 2, head: i === 3, alpha: alpha * fade });
      }
    } else {
      this.drawArrowPath(a, end, pal.intent, { dash: [6, 6], width: 2, head: p >= 1, alpha });
    }
    if (p >= 1 && it.speed != null && !it.noPill && dist(a, b) > 40) {
      ctx.save();
      ctx.globalAlpha = alpha;
      const m = lerp(a, b, it.pillT ?? 0.3);
      this.pill(`요청 ${fmt(it.speed)} m/s`, m.x, m.y - 14, pal.intent, { size: 11 });
      ctx.restore();
    }
  }

  drawExec(scene, clock) {
    const e = scene.exec;
    if (!e || clock < e.t0 || e.kind !== 'move') return;
    const color = this.pal[`${e.verdict}-fill`];
    const a = this.toScreen(e.from);
    const b = this.toScreen(e.to);
    const p = prog(e, clock);
    const cur = lerp(a, b, p);
    this.drawArrowPath(a, b, color, { width: 3, head: false, alpha: 0.25 });
    this.drawArrowPath(a, cur, color, { width: 3, head: false });
    if (e.chevrons && dist(a, b) > 30) {
      const { ctx } = this;
      const ang = Math.atan2(b.y - a.y, b.x - a.x);
      ctx.save();
      ctx.strokeStyle = this.pal['jeol-fill'];
      ctx.lineWidth = 3;
      ctx.lineCap = 'round';
      for (const f of [0.3, 0.5, 0.7]) {
        const m = lerp(a, b, f);
        ctx.save();
        ctx.translate(m.x, m.y);
        ctx.rotate(ang);
        for (const dx of [-5, 3]) {
          ctx.beginPath();
          ctx.moveTo(dx - 5, -7); ctx.lineTo(dx + 2, 0); ctx.lineTo(dx - 5, 7);
          ctx.stroke();
        }
        ctx.restore();
      }
      ctx.restore();
    }
  }

  drawGhostPath(scene, clock) {
    const g = scene.ghost;
    if (!g || g.kind === 'none') return;
    const a = this.toScreen(g.from);
    const b = this.toScreen(g.kind === 'move' ? g.to : g.at);
    this.drawArrowPath(a, b, this.pal.intent, { dash: [6, 6], width: 2, head: true, alpha: 0.8 });
  }

  drawBarrier(scene, clock) {
    const br = scene.barrier;
    if (!br || clock < br.t0) return;
    const { ctx, pal } = this;
    const c = this.toScreen(br.at);
    const k = scene.reduced ? 1 : clamp01((clock - br.t0) / 160);
    const len = Math.max(30, 0.9 * this.scale) * (0.3 + 0.7 * easeOut(k));
    const ang = Math.atan2(-(br.dir.y), br.dir.x) + Math.PI / 2; // perpendicular to the path (screen space)
    ctx.save();
    ctx.translate(c.x, c.y);
    ctx.rotate(ang);
    ctx.globalAlpha = k;
    ctx.fillStyle = pal['bul-fill'];
    // Brush-stroke bar: thick middle, tapered ends.
    ctx.beginPath();
    ctx.moveTo(-len / 2, 0);
    ctx.quadraticCurveTo(-len / 4, -4.5, 0, -3);
    ctx.quadraticCurveTo(len / 4, -2, len / 2, -0.5);
    ctx.quadraticCurveTo(len / 4, 3.5, 0, 3);
    ctx.quadraticCurveTo(-len / 4, 3, -len / 2, 0);
    ctx.fill();
    ctx.restore();
  }

  drawHuman(h, scene, real) {
    const { ctx, pal } = this;
    const child = h.class === 'child';
    const bob = !scene.reduced && child ? Math.sin(real / 318) * 0.03 : 0;
    const s = this.toScreen({ x: h.pos.x, y: h.pos.y + bob });
    const size = Math.max(child ? 11 : 13, (child ? 0.225 : 0.275) * this.scale);
    const color = child ? pal.child : pal.adult;
    const unknown = scene.decor?.childUnknown && child;
    ctx.save();
    ctx.globalAlpha = this.alphaFor(h.id);
    // Focus pulse (twice) at beat start.
    if (this.focus?.has(h.id) && scene.focusT != null && !scene.reduced) {
      const k = (real - scene.focusT) / 700;
      if (k >= 0 && k < 2) {
        ctx.save();
        ctx.globalAlpha = 0.5 * (1 - (k % 1));
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(s.x, s.y, size * (1.2 + (k % 1) * 1.2), 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();
      }
    }
    if (unknown) ctx.setLineDash([3, 3]);
    // Body.
    ctx.beginPath();
    ctx.moveTo(s.x - size * 0.62, s.y + size * 0.95);
    ctx.quadraticCurveTo(s.x - size * 0.6, s.y + size * 0.05, s.x, s.y + size * 0.05);
    ctx.quadraticCurveTo(s.x + size * 0.6, s.y + size * 0.05, s.x + size * 0.62, s.y + size * 0.95);
    ctx.closePath();
    if (unknown) { ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.stroke(); } else { ctx.fillStyle = color; ctx.fill(); }
    // Head.
    ctx.beginPath();
    ctx.arc(s.x, s.y - size * 0.38, size * 0.42, 0, Math.PI * 2);
    if (unknown) ctx.stroke(); else { ctx.fillStyle = color; ctx.fill(); ctx.strokeStyle = pal.floor; ctx.lineWidth = 1.5; ctx.stroke(); }
    ctx.setLineDash([]);
    if (child && !unknown) {
      // Toy.
      ctx.fillStyle = pal['jeol-fill'];
      ctx.beginPath();
      ctx.arc(s.x + size * 0.78, s.y + size * 0.55, size * 0.2, 0, Math.PI * 2);
      ctx.fill();
    }
    if (unknown) this.text('?', s.x, s.y - size * 0.36, color, this.font(800, size * 0.7), 'center', 'middle', false);
    // De-emphasis dims the figure only; the name label stays at full contrast.
    ctx.globalAlpha = 1;
    this.text(child ? '아이' : '어른', s.x, s.y + size * 0.95 + 9, color, this.font(700, 12));
    ctx.restore();
  }

  /** The robot body. opts.ghost draws the unfiltered twin (violet, hatched, no badge). */
  drawRobotShape(s, heading, holding, opts) {
    const { ctx, pal } = this;
    const size = Math.max(26, 0.6 * this.scale);
    const half = size / 2;
    ctx.save();
    ctx.translate(s.x, s.y);
    ctx.rotate(-heading + (opts.wiggle ?? 0));
    // Gripper (front, +x).
    ctx.strokeStyle = opts.ghost ? pal.intent : pal['robot-body'];
    ctx.lineWidth = Math.max(2.5, size * 0.09);
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(half * 0.9, -half * 0.35); ctx.lineTo(half * 1.35, -half * 0.3);
    ctx.moveTo(half * 0.9, half * 0.35); ctx.lineTo(half * 1.35, half * 0.3);
    ctx.stroke();
    if (holding) this.drawHeld(holding, half * 1.45, 0, size);
    // Body.
    roundRect(ctx, -half, -half, size, size, size * 0.24);
    if (opts.ghost) {
      ctx.fillStyle = pal.surface;
      ctx.fill();
      ctx.save();
      ctx.clip();
      ctx.strokeStyle = pal.intent;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      for (let d = -size; d < size * 2; d += 5) { ctx.moveTo(-half + d, -half); ctx.lineTo(-half + d - size, half); }
      ctx.stroke();
      ctx.restore();
      roundRect(ctx, -half, -half, size, size, size * 0.24);
      ctx.strokeStyle = pal.intent;
      ctx.lineWidth = 2;
      ctx.stroke();
    } else {
      ctx.fillStyle = pal['robot-body'];
      ctx.fill();
    }
    // Face plate + eyes toward the heading.
    ctx.fillStyle = opts.ghost ? pal.intent : pal['robot-face'];
    roundRect(ctx, half * 0.2, -half * 0.62, half * 0.62, half * 1.24, half * 0.2);
    ctx.fill();
    const eye = opts.eyeRed ? pal['bul-fill'] : (opts.ghost ? pal.surface : pal['robot-eye']);
    ctx.fillStyle = eye;
    const eh = opts.blink ? half * 0.05 : half * 0.2;
    for (const ey of [-half * 0.3, half * 0.3]) {
      ctx.beginPath();
      ctx.ellipse(half * 0.52, ey, half * 0.13, eh, 0, 0, Math.PI * 2);
      ctx.fill();
    }
    // Haetae badge on the back.
    if (!opts.ghost) {
      const b = Math.max(12, size * 0.42);
      ctx.save();
      ctx.translate(-half * 0.45, 0);
      ctx.rotate(heading - (opts.wiggle ?? 0)); // keep the glyph upright
      if (opts.glow) {
        ctx.shadowColor = pal['seal-ink'];
        ctx.shadowBlur = 12;
      }
      ctx.fillStyle = opts.glow ? '#fff3c4' : pal['robot-face'];
      roundRect(ctx, -b / 2, -b / 2, b, b, 2);
      ctx.fill();
      ctx.shadowBlur = 0;
      ctx.fillStyle = pal['robot-body'];
      ctx.font = this.font(700, b * 0.8, SEAL_FONT);
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('獬', 0, b * 0.05);
      ctx.restore();
    }
    ctx.restore();
    return size;
  }

  drawHeld(obj, x, y, size) {
    const { ctx, pal } = this;
    if (obj === 'knife') {
      this.drawKnife(x + size * 0.25, y, 0, size * 0.75);
    } else if (obj === 'cup') {
      ctx.fillStyle = pal.surface; ctx.strokeStyle = pal['ink-2']; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.arc(x + size * 0.1, y, size * 0.14, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    } else {
      ctx.fillStyle = pal['jeol-fill'];
      ctx.beginPath(); ctx.arc(x + size * 0.1, y, size * 0.13, 0, Math.PI * 2); ctx.fill();
    }
  }

  drawRobot(scene, pose, holding, clock, real) {
    const { ctx, pal } = this;
    const s = this.toScreen(pose);
    const r = scene.robot;
    const reduced = scene.reduced;
    const react = r.reactT != null && !reduced ? real - r.reactT : -1;
    const eyeRed = react >= 0 && react < 450 && (Math.floor(react / 110) % 2 === 0);
    const wiggle = react >= 0 && react < 400 ? Math.sin((react / 400) * Math.PI * 4) * (4 * Math.PI / 180) : 0;
    const blink = !reduced && (real % 4200) < 130;
    const glow = r.badgeT != null && real - r.badgeT < 300;

    // Arm reach (grasp / place).
    const e = scene.exec;
    if (e && e.kind === 'reach' && clock >= e.t0) {
      const p = prog(e, clock);
      const ext = p < 0.5 ? p * 2 : (1 - p) * 2;
      const t = this.toScreen(e.at);
      const tip = lerp(s, t, ext);
      ctx.strokeStyle = pal['robot-body'];
      ctx.lineWidth = 5;
      ctx.lineCap = 'round';
      ctx.beginPath(); ctx.moveTo(s.x, s.y); ctx.lineTo(tip.x, tip.y); ctx.stroke();
      if (holding) this.drawHeld(holding, tip.x, tip.y - 10, 26);
    }

    // Gate ring when the mode is not normal (mode read from sim.mode()).
    if (scene.mode && scene.mode !== 'normal') {
      const rad = 0.55 * this.scale;
      ctx.save();
      if (scene.mode === 'caution') {
        ctx.setLineDash([5, 4]);
        ctx.strokeStyle = pal['jeol-fg'];
      } else {
        ctx.strokeStyle = pal['bul-fg'];
      }
      ctx.lineWidth = 2.5;
      ctx.beginPath(); ctx.arc(s.x, s.y, Math.max(rad, 22), 0, Math.PI * 2); ctx.stroke();
      ctx.setLineDash([]);
      if (scene.mode !== 'caution') this.pill('⏸', s.x + Math.max(rad, 22) * 0.75, s.y - Math.max(rad, 22) * 0.75, pal['bul-fg'], { size: 10 });
      ctx.restore();
    }

    const size = this.drawRobotShape(s, r.heading ?? 0, holding, { eyeRed, wiggle, blink, glow });

    // Sensor glitch sparks after a fault.
    if (r.glitchT != null && !reduced && real - r.glitchT < 700) {
      const k = (real - r.glitchT) / 700;
      ctx.save();
      ctx.strokeStyle = pal['jeol-fill'];
      ctx.lineWidth = 2;
      ctx.globalAlpha = 1 - k;
      for (let i = 0; i < 5; i++) {
        const ang = i * 1.3 + k * 2;
        const r0 = size * 0.7;
        const r1 = size * (0.95 + k * 0.4);
        ctx.beginPath();
        ctx.moveTo(s.x + Math.cos(ang) * r0, s.y + Math.sin(ang) * r0);
        ctx.lineTo(s.x + Math.cos(ang + 0.2) * (r0 + r1) / 2, s.y + Math.sin(ang - 0.2) * (r0 + r1) / 2);
        ctx.lineTo(s.x + Math.cos(ang) * r1, s.y + Math.sin(ang) * r1);
        ctx.stroke();
      }
      ctx.restore();
    }

    // Executed speed while moving (from decision.action.speed).
    if (e && e.kind === 'move' && clock >= e.t0 && prog(e, clock) < 1) {
      this.pill(`${fmt(e.speed)} m/s`, s.x, s.y + size / 2 + 26, pal[`${e.verdict}-fg`], { size: 11 });
    }
    this.text('로봇', s.x, s.y + size / 2 + 9, pal['ink-2'], this.font(700, 11));
  }

  drawGhost(scene, clock, real) {
    const g = scene.ghost;
    const { ctx, pal } = this;
    let pose = g.from;
    if (g.kind === 'move') pose = lerp(g.from, g.to, prog(g, clock));
    const s = this.toScreen(pose);
    const holding = this.ghostHolding(g, clock);
    const heading = g.kind === 'move' ? Math.atan2(g.to.y - g.from.y, g.to.x - g.from.x) : (scene.robot.heading ?? 0);
    ctx.save();
    ctx.globalAlpha = 0.75;
    if (g.kind === 'reach' && clock >= g.t0) {
      const p = prog(g, clock);
      const ext = p < 0.5 ? p * 2 : (1 - p) * 2;
      const t = this.toScreen(g.at);
      const tip = lerp(s, t, ext);
      ctx.strokeStyle = pal.intent;
      ctx.lineWidth = 5;
      ctx.lineCap = 'round';
      ctx.beginPath(); ctx.moveTo(s.x, s.y); ctx.lineTo(tip.x, tip.y); ctx.stroke();
      if (holding) this.drawHeld(holding, tip.x, tip.y - 10, 26);
    }
    const size = this.drawRobotShape(s, heading, g.kind === 'reach' ? null : holding, { ghost: true });
    ctx.restore();
    this.pill('필터 없음 · 모델 명령 원본', s.x, s.y - size / 2 - 16, pal.intent, { size: 11 });
    if (g.kind === 'move' && prog(g, clock) < 1 && clock >= g.t0) {
      this.pill(`${fmt(g.speed)} m/s`, s.x, s.y + size / 2 + 26, pal.intent, { size: 11 });
    }
  }

  drawLab(scene, pose) {
    const lab = scene.lab;
    if (!lab) return;
    const { ctx, pal } = this;
    if (lab.preview) {
      const a = this.toScreen(pose);
      const b = this.toScreen(lab.preview);
      this.drawArrowPath(a, b, pal.intent, { dash: [6, 6], width: 2, head: true, alpha: 0.9 });
    }
    if (lab.cursor) {
      const s = this.toScreen(lab.cursor);
      ctx.strokeStyle = pal.focus;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(s.x, s.y, 10, 0, Math.PI * 2);
      for (const [dx, dy] of [[-1, 0], [1, 0], [0, -1], [0, 1]]) {
        ctx.moveTo(s.x + dx * 4, s.y + dy * 4);
        ctx.lineTo(s.x + dx * 16, s.y + dy * 16);
      }
      ctx.stroke();
    }
    if (lab.grabbed) {
      const h = scene.world.humans.find((x) => x.id === lab.grabbed);
      if (h) {
        const s = this.toScreen(h.pos);
        ctx.strokeStyle = pal.focus;
        ctx.lineWidth = 3;
        ctx.beginPath(); ctx.arc(s.x, s.y, 22, 0, Math.PI * 2); ctx.stroke();
      }
    }
  }

  // ───────────────────────── Pointer input (lab only) ─────────────────────────

  hitHuman(e, humans) {
    const r = this.canvas.getBoundingClientRect();
    const sx = e.clientX - r.left;
    const sy = e.clientY - r.top;
    for (let i = humans.length - 1; i >= 0; i--) {
      const s = this.toScreen(humans[i].pos);
      if (Math.hypot(s.x - sx, s.y - sy) <= Math.max(18, 0.3 * this.scale)) return humans[i];
    }
    return null;
  }

  eventToWorld(e) {
    const r = this.canvas.getBoundingClientRect();
    return this.toWorld(e.clientX - r.left, e.clientY - r.top);
  }

  /**
   * handlers: { active(), onPointer(p|null), onCommit(p), onHumanMove(id,p), onHumanDrop(id),
   *             getHumans(), onInactiveClick() }
   */
  bindPointer(h) {
    const c = this.canvas;
    let drag = null;
    c.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      if (!h.active()) { h.onInactiveClick?.(); return; }
      c.setPointerCapture(e.pointerId);
      const human = this.hitHuman(e, h.getHumans());
      drag = { id: e.pointerId, human: human?.id ?? null, x0: e.clientX, y0: e.clientY, moved: false };
      if (human) c.classList.add('grabbing');
    });
    c.addEventListener('pointermove', (e) => {
      if (!h.active()) return;
      const p = this.eventToWorld(e);
      if (drag && drag.id === e.pointerId) {
        if (!drag.moved && Math.hypot(e.clientX - drag.x0, e.clientY - drag.y0) > DRAG_THRESHOLD) drag.moved = true;
        if (drag.human && drag.moved) { h.onHumanMove(drag.human, Stage.snap(this.clamp(p))); return; }
      }
      if (e.pointerType !== 'touch') {
        c.classList.toggle('grab', !drag && !!this.hitHuman(e, h.getHumans()));
        h.onPointer(Stage.snap(p));
      }
    });
    const end = (e, cancelled) => {
      if (!drag || drag.id !== e.pointerId) return;
      const d = drag;
      drag = null;
      c.classList.remove('grabbing');
      if (cancelled || !h.active()) return;
      const p = this.eventToWorld(e);
      if (d.human && d.moved) h.onHumanDrop(d.human);
      else if (!d.moved) h.onCommit(Stage.snap(p));
      if (e.pointerType === 'touch') h.onPointer(null);
    };
    c.addEventListener('pointerup', (e) => end(e, false));
    c.addEventListener('pointercancel', (e) => end(e, true));
    c.addEventListener('pointerleave', (e) => { if (!drag && e.pointerType !== 'touch' && h.active()) h.onPointer(null); });
  }
}

function fmt(v) {
  return typeof v === 'number' && Number.isFinite(v) ? String(Math.round(v * 100) / 100) : String(v);
}

export function roundRect(ctx, x, y, w, h, r) {
  r = Math.max(0, Math.min(r, w / 2, h / 2));
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}
