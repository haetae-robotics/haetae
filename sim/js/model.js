// Pure shared data and maths for both stages (2D map and 3D diorama).
// Fixed art only: nothing here ever enters the world JSON or the gate, and
// nothing here decides anything.

export const SNAP_MS = 400;

export const ROOMS = [
  { name: '주방·식당', x0: 0, y0: 0, x1: 4, y1: 10, tint: false, floor: 'kitchen' },
  { name: '복도', x0: 4, y0: 0, x1: 6, y1: 10, tint: true, floor: 'hall' },
  { name: '거실', x0: 6, y0: 0, x1: 10, y1: 7, tint: false, floor: 'living' },
  { name: '', x0: 6, y0: 7, x1: 7, y1: 10, tint: false, floor: 'living' },
  { name: '아이 방', x0: 7, y0: 7, x1: 10, y1: 10, tint: true, floor: 'child' },
];

/** 2D map decor (the flat fallback keeps its original footprints). */
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

/**
 * 3D diorama footprints (world metres). Each was checked against every
 * scripted robot pose (radius 0.3) and every executed path of the cards and
 * the dinner party, so nothing seems to block a robot the gate let through.
 */
export const FURN3D = {
  sink: { x0: 0.3, y0: 0.2, x1: 1.7, y1: 0.6, h: 0.5 },
  table: { x0: 3.35, y0: 2.4, x1: 3.95, y1: 3.7, h: 0.42 },
  chair: { x: 3.65, y: 4.0, w: 0.4, d: 0.4 },
  knife: { x: 3.2, y: 3.0 },
  cup: { x: 3.6, y: 3.3 },
  sofa: { x0: 7.2, y0: 1.0, x1: 9.4, y1: 1.6 },
  coffee: { x: 8.3, y: 3.6, w: 0.8, d: 0.5, h: 0.2 },
  lamp: { x: 9.6, y: 6.4 },
  rug: { x: 8, y: 4, r: 1.1 },
  bed: { x0: 8.9, y0: 8.3, x1: 9.8, y1: 9.8 },
  mat: { x0: 8.8, y0: 7.25, x1: 9.8, y1: 7.75 },
  toybox: { x: 7.4, y: 9.5 },
  dock: { x: 1, y: 9 },
  fakeTag: { x: 7.3, y: 6.94 },
  peer: { x: 6.8, y: -0.9 },
};

export const clamp01 = (v) => Math.max(0, Math.min(1, v));
export const easeOut = (t) => 1 - (1 - t) ** 3;
export const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);
export const lerp = (a, b, t) => ({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t });
export const dist = (a, b) => Math.hypot(b.x - a.x, b.y - a.y);

/** Progress 0..1 of a timed element at clock `c` (dur <= 0 means instant). */
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

/** What the robot holds right now (a grasp/place reach swaps it halfway). */
export function displayHolding(scene, clock) {
  const e = scene.exec;
  if (e && e.kind === 'reach' && clock >= e.t0) return prog(e, clock) >= 0.5 ? e.holdAfter : e.holdBefore;
  return scene.robot.holding;
}

/** What the unfiltered ghost holds (only what the proposal carries). */
export function ghostHolding(g, clock) {
  if (g.kind === 'reach') return prog(g, clock) >= 0.5 ? g.holdAfter : g.holdBefore;
  return g.holding;
}

/** Ghost robot position at clock c. */
export function ghostPose(g, clock) {
  if (g.kind === 'move') return lerp(g.from, g.to, prog(g, clock));
  return g.from;
}

/** Shortest distance between segments a0-a1 and b0-b1 (world metres). */
export function segSegDist(a0, a1, b0, b1) {
  const cross = (o, p, q) => (p.x - o.x) * (q.y - o.y) - (p.y - o.y) * (q.x - o.x);
  const d1 = cross(b0, b1, a0);
  const d2 = cross(b0, b1, a1);
  const d3 = cross(a0, a1, b0);
  const d4 = cross(a0, a1, b1);
  if (((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) && ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0))) return 0;
  const ps = (p, s0, s1) => {
    const dx = s1.x - s0.x;
    const dy = s1.y - s0.y;
    const L = dx * dx + dy * dy;
    const t = L ? clamp01(((p.x - s0.x) * dx + (p.y - s0.y) * dy) / L) : 0;
    return Math.hypot(p.x - (s0.x + dx * t), p.y - (s0.y + dy * t));
  };
  return Math.min(ps(a0, b0, b1), ps(a1, b0, b1), ps(b0, a0, a1), ps(b1, a0, a1));
}

/** Liang-Barsky: parameter where segment A->B first enters rect r ({min,max}), or null. */
export function segEnterT(A, B, r) {
  const dx = B.x - A.x;
  const dy = B.y - A.y;
  let t0 = 0;
  let t1 = 1;
  const clip = (p, q) => {
    if (p === 0) return q >= 0;
    const t = q / p;
    if (p < 0) { if (t > t1) return false; if (t > t0) t0 = t; } else { if (t < t0) return false; if (t < t1) t1 = t; }
    return true;
  };
  if (clip(-dx, A.x - r.min.x) && clip(dx, r.max.x - A.x) && clip(-dy, A.y - r.min.y) && clip(dy, r.max.y - A.y)) return t0;
  return null;
}
