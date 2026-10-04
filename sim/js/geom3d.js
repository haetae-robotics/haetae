// Geometry, texture and material helpers for the 3D diorama.
// World coordinates are metres with y = north; the three.js frame is
// X = x - 5, Z = 5 - y, Y up. No text is ever drawn into a texture.

import * as THREE from 'three';

export const FLOOR_Y = 0.04;

const displayCoordinate = (value) => Number.isFinite(value) ? Math.max(-100, Math.min(100, value)) : 0;

export function w2v(p, h = 0, out = new THREE.Vector3()) {
  return out.set(displayCoordinate(p?.x) - 5, displayCoordinate(h), 5 - displayCoordinate(p?.y));
}

export function v2w(v) {
  return { x: v.x + 5, y: 5 - v.z };
}

/** Yaw (rotation.y) that turns local +X toward world direction (dx, dy). */
export const yawOf = (dx, dy) => Math.atan2(dy, dx);
/** Yaw that turns local +Z toward world direction (dx, dy). */
export const yawZ = (dx, dy) => Math.atan2(dx, -dy);

// ───────────────────────── Geometry cache ─────────────────────────

const geoCache = new Map();
/** Shared geometry by key (built once). */
export function geo(key, make) {
  let g = geoCache.get(key);
  if (!g) { g = make(); geoCache.set(key, g); }
  return g;
}
export function disposeGeoCache() {
  for (const g of geoCache.values()) g.dispose();
  geoCache.clear();
}

function roundedRectShape(w, h, r, shape = new THREE.Shape()) {
  const x = -w / 2;
  const y = -h / 2;
  r = Math.min(r, w / 2, h / 2);
  shape.moveTo(x + r, y);
  shape.lineTo(x + w - r, y);
  shape.quadraticCurveTo(x + w, y, x + w, y + r);
  shape.lineTo(x + w, y + h - r);
  shape.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  shape.lineTo(x + r, y + h);
  shape.quadraticCurveTo(x, y + h, x, y + h - r);
  shape.lineTo(x, y + r);
  shape.quadraticCurveTo(x, y, x + r, y);
  return shape;
}
export { roundedRectShape };

/** A rounded box of size w (x) x h (y) x d (z), centred on the origin. */
export function roundedBox(w, h, d, r) {
  return geo(`rbox:${w}:${h}:${d}:${r}`, () => {
    const rr = Math.min(r, w / 2 - 0.001, h / 2 - 0.001, d / 2 - 0.001);
    const shape = roundedRectShape(w - 2 * rr, h - 2 * rr, rr * 0.6);
    const g = new THREE.ExtrudeGeometry(shape, {
      depth: Math.max(0.001, d - 2 * rr), bevelEnabled: true, bevelThickness: rr, bevelSize: rr,
      bevelSegments: 2, curveSegments: 3,
    });
    g.center();
    g.computeVertexNormals();
    return g;
  });
}

/** The Haetae shield: flat top with a centre notch, rounded shoulders, pointed base. Unit width. */
export function shieldShape(w = 1, h = 1.2) {
  const s = new THREE.Shape();
  const hw = w / 2;
  const top = h / 2;
  s.moveTo(-hw, top);
  s.lineTo(-0.1 * w, top);
  s.lineTo(0, top - 0.09 * h);
  s.lineTo(0.1 * w, top);
  s.lineTo(hw, top);
  s.lineTo(hw, top - 0.35 * h);
  s.quadraticCurveTo(hw * 0.95, -top * 0.45, 0, -top);
  s.quadraticCurveTo(-hw * 0.95, -top * 0.45, -hw, top - 0.35 * h);
  s.lineTo(-hw, top);
  return s;
}

/** A 6-point lightning bolt, about unit height. */
export function boltShape() {
  const s = new THREE.Shape();
  s.moveTo(0.12, 0.5);
  s.lineTo(-0.28, -0.05);
  s.lineTo(-0.02, -0.05);
  s.lineTo(-0.12, -0.5);
  s.lineTo(0.28, 0.08);
  s.lineTo(0.02, 0.08);
  s.lineTo(0.12, 0.5);
  return s;
}

/** Flat dashed ring in the XZ plane (arc dashes), one geometry. */
export function dashedRing(r, width, dashes = 36, fill = 0.55) {
  const pos = [];
  const seg = 3;
  const r0 = r - width / 2;
  const r1 = r + width / 2;
  for (let i = 0; i < dashes; i++) {
    const a0 = (i / dashes) * Math.PI * 2;
    const a1 = a0 + (fill / dashes) * Math.PI * 2;
    for (let k = 0; k < seg; k++) {
      const b0 = a0 + ((a1 - a0) * k) / seg;
      const b1 = a0 + ((a1 - a0) * (k + 1)) / seg;
      const p = (rad, a) => [Math.cos(a) * rad, 0, -Math.sin(a) * rad];
      pos.push(...p(r0, b0), ...p(r1, b0), ...p(r1, b1), ...p(r0, b0), ...p(r1, b1), ...p(r0, b1));
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.computeVertexNormals();
  return g;
}

/**
 * Flat ribbon from world point a to b in the XZ plane at local Y 0, centred
 * on the origin at a (so the mesh is placed at a). With dash > 0 the ribbon
 * is made of dash quads (dash, gap), in path order, so setDrawRange grows it.
 * Returns { geometry, quads, length }.
 */
export function ribbon(a, b, width, dash = 0, gap = 0) {
  // Bound display geometry independently of policy validation of raw intent.
  const bounded = displayCoordinate;
  const dx = bounded(b?.x) - bounded(a?.x);
  const dz = -(bounded(b?.y) - bounded(a?.y));
  width = Number.isFinite(width) ? Math.max(0, Math.min(width, 10)) : 0;
  dash = Number.isFinite(dash) ? Math.max(0, dash) : 0;
  gap = Number.isFinite(gap) ? Math.max(0, gap) : 0;
  const L = Math.hypot(dx, dz);
  const ux = L ? dx / L : 1;
  const uz = L ? dz / L : 0;
  const nx = -uz * (width / 2);
  const nz = ux * (width / 2);
  const pos = [];
  const quad = (s0, s1) => {
    const x0 = ux * s0; const z0 = uz * s0;
    const x1 = ux * s1; const z1 = uz * s1;
    pos.push(x0 + nx, 0, z0 + nz, x0 - nx, 0, z0 - nz, x1 - nx, 0, z1 - nz,
      x0 + nx, 0, z0 + nz, x1 - nx, 0, z1 - nz, x1 + nx, 0, z1 + nz);
  };
  let quads = 0;
  if (dash > 0 && L > 0) {
    const count = Math.min(2000, Math.ceil(L / (dash + gap)));
    const stride = Math.max(dash + gap, L / count);
    for (let i = 0; i < count; i++) {
      const start = i * stride;
      quad(start, Math.min(L, start + dash)); quads++;
    }
  } else if (L > 0) {
    quad(0, L); quads = 1;
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('normal', new THREE.Float32BufferAttribute(pos.map((_, i) => (i % 3 === 1 ? 1 : 0)), 3));
  return { geometry: g, quads, length: L };
}

/** PlaneGeometry lying flat (XZ), size w x d, UVs in units of `tile` metres. */
export function floorPlane(w, d, tile = 1) {
  const g = new THREE.PlaneGeometry(w, d);
  g.rotateX(-Math.PI / 2);
  const uv = g.attributes.uv;
  for (let i = 0; i < uv.count; i++) uv.setXY(i, (uv.getX(i) * w) / tile, (uv.getY(i) * d) / tile);
  return g;
}

// ───────────────────────── Canvas textures (drawn once, no text) ─────────────────────────

function canvasTex(size, draw, { srgb = false, repeat = true } = {}) {
  const c = document.createElement('canvas');
  c.width = size;
  c.height = size;
  const ctx = c.getContext('2d');
  draw(ctx, size);
  const t = new THREE.CanvasTexture(c);
  if (srgb) t.colorSpace = THREE.SRGBColorSpace;
  if (repeat) { t.wrapS = THREE.RepeatWrapping; t.wrapT = THREE.RepeatWrapping; }
  t.anisotropy = 4;
  return t;
}

/** Seeded PRNG (mulberry32). */
function rng(seed) {
  return () => {
    seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export const TEX = {
  /** Diagonal hatch (alpha map, 1 tile = 0.5 m). */
  hatch: () => canvasTex(64, (x, s) => {
    x.fillStyle = '#000'; x.fillRect(0, 0, s, s);
    x.strokeStyle = '#fff'; x.lineWidth = 7;
    x.beginPath();
    for (let d = -s; d <= s * 2; d += s / 2) { x.moveTo(d, s); x.lineTo(d + s, 0); }
    x.stroke();
  }),
  /** Crosshatch (alpha map). */
  crosshatch: () => canvasTex(64, (x, s) => {
    x.fillStyle = '#000'; x.fillRect(0, 0, s, s);
    x.strokeStyle = '#fff'; x.lineWidth = 5;
    x.beginPath();
    for (let d = -s; d <= s * 2; d += s / 2) { x.moveTo(d, s); x.lineTo(d + s, 0); x.moveTo(d, 0); x.lineTo(d + s, s); }
    x.stroke();
  }),
  /** Hex lattice (alpha map) for the barrier and the shield dome. */
  hex: () => canvasTex(256, (x, s) => {
    x.fillStyle = '#2a2a2a'; x.fillRect(0, 0, s, s);
    const r = s / 8;
    const h = Math.sqrt(3) * r;
    x.strokeStyle = '#fff'; x.lineWidth = 5;
    for (let row = -1; row < s / h + 1; row++) {
      for (let col = -1; col < s / (1.5 * r) + 1; col++) {
        const cx = col * 1.5 * r;
        const cy = row * h + (col % 2 ? h / 2 : 0);
        x.beginPath();
        for (let i = 0; i < 6; i++) {
          const a = (Math.PI / 3) * i;
          const px = cx + r * 0.92 * Math.cos(a);
          const py = cy + r * 0.92 * Math.sin(a);
          if (i) x.lineTo(px, py); else x.moveTo(px, py);
        }
        x.closePath();
        x.stroke();
      }
    }
    // Vertical fade: brighter at the base.
    const g = x.createLinearGradient(0, 0, 0, s);
    g.addColorStop(0, 'rgba(255,255,255,0)');
    g.addColorStop(1, 'rgba(255,255,255,0.25)');
    x.fillStyle = g; x.fillRect(0, 0, s, s);
  }),
  /** Diagonal stripes (ghost hatch alpha map, and the unfiltered plinth band). */
  stripes: () => canvasTex(64, (x, s) => {
    x.fillStyle = '#555'; x.fillRect(0, 0, s, s);
    x.strokeStyle = '#fff'; x.lineWidth = 12;
    x.beginPath();
    for (let d = -s; d <= s * 2; d += s / 2) { x.moveTo(d, s); x.lineTo(d + s, 0); }
    x.stroke();
  }),
  /** A fixed QR-like 21 x 21 module pattern with a small bolt (no text). */
  qr: () => canvasTex(128, (x, s) => {
    const rand = rng(20240927);
    x.fillStyle = '#fff'; x.fillRect(0, 0, s, s);
    const m = s / 25;
    const o = m * 2;
    x.fillStyle = '#1b1b1b';
    const finder = (cx, cy) => {
      x.fillRect(o + cx * m, o + cy * m, 7 * m, 7 * m);
      x.fillStyle = '#fff'; x.fillRect(o + (cx + 1) * m, o + (cy + 1) * m, 5 * m, 5 * m);
      x.fillStyle = '#1b1b1b'; x.fillRect(o + (cx + 2) * m, o + (cy + 2) * m, 3 * m, 3 * m);
    };
    for (let i = 0; i < 21; i++) {
      for (let j = 0; j < 21; j++) {
        const inFinder = (i < 8 && j < 8) || (i > 12 && j < 8) || (i < 8 && j > 12);
        const inCentre = i > 7 && i < 13 && j > 7 && j < 13;
        if (!inFinder && !inCentre && rand() < 0.5) x.fillRect(o + i * m, o + j * m, m, m);
      }
    }
    finder(0, 0); finder(14, 0); finder(0, 14);
    // Small bolt in the centre.
    x.fillStyle = '#0EA5E9';
    x.beginPath();
    const c = s / 2;
    const k = m * 2.4;
    x.moveTo(c + 0.12 * k, c - 0.5 * k); x.lineTo(c - 0.28 * k, c + 0.05 * k); x.lineTo(c - 0.02 * k, c + 0.05 * k);
    x.lineTo(c - 0.12 * k, c + 0.5 * k); x.lineTo(c + 0.28 * k, c - 0.08 * k); x.lineTo(c + 0.02 * k, c - 0.08 * k);
    x.closePath(); x.fill();
    // Tape corners.
    x.fillStyle = 'rgba(230, 214, 170, .85)';
    for (const [tx, ty, a] of [[0, 0, -0.7], [s, 0, 0.7], [0, s, 0.7], [s, s, -0.7]]) {
      x.save(); x.translate(tx, ty); x.rotate(a); x.fillRect(-14, -5, 28, 10); x.restore();
    }
  }, { srgb: true, repeat: false }),
  /** Radial gradient alpha (contact shadow, glow). */
  glow: () => canvasTex(128, (x, s) => {
    const g = x.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2);
    g.addColorStop(0, '#fff');
    g.addColorStop(0.55, '#888');
    g.addColorStop(1, '#000');
    x.fillStyle = g; x.fillRect(0, 0, s, s);
  }, { repeat: false }),
  /** Kitchen tiles: 1 tile = 0.5 m, lines at 6% ink (multiplies the floor colour). */
  tile: () => canvasTex(64, (x, s) => {
    x.fillStyle = '#fff'; x.fillRect(0, 0, s, s);
    x.fillStyle = 'rgba(0,0,0,.09)';
    x.fillRect(0, 0, s, 2); x.fillRect(0, 0, 2, s);
  }, { srgb: true }),
  /** Hall planks running north-south (8 planks per tile). */
  planks: () => canvasTex(128, (x, s) => {
    x.fillStyle = '#fff'; x.fillRect(0, 0, s, s);
    const rand = rng(7);
    const w = s / 8;
    for (let i = 0; i < 8; i++) {
      x.fillStyle = `rgba(0,0,0,${0.02 + rand() * 0.05})`;
      x.fillRect(i * w, 0, w, s);
      x.fillStyle = 'rgba(0,0,0,.14)';
      x.fillRect(i * w, 0, 1.5, s);
      const j = rand() * s;
      x.fillRect(i * w, j, w, 1.5);
    }
  }, { srgb: true }),
};

// ───────────────────────── Palette-driven materials ─────────────────────────

/**
 * Materials cached by key; each remembers its colour token so a theme change
 * updates colours in place (nothing is rebuilt).
 */
export class Palette {
  constructor() {
    this.values = {};
    this.colors = {};
    this.mats = new Map(); // key -> { mat, token, dim, emissiveToken }
    this.dimTarget = new THREE.Color();
    this.tmp = new THREE.Color();
  }

  read(tokens) {
    const cs = getComputedStyle(document.documentElement);
    for (const t of tokens) {
      const v = cs.getPropertyValue(`--${t}`).trim();
      this.values[t] = v;
      if (/^(#|rgb|hsl)/i.test(v)) {
        this.colors[t] ??= new THREE.Color();
        try { this.colors[t].setStyle(v); } catch { this.colors[t].set('#888888'); }
      }
    }
    this.dimTarget.copy(this.color('d3-bg-bottom'));
    for (const e of this.mats.values()) this.apply(e);
  }

  num(t, fallback = 0) {
    const v = parseFloat(this.values[t]);
    return Number.isFinite(v) ? v : fallback;
  }

  color(t) {
    return this.colors[t] ?? this.tmp.set('#888888');
  }

  apply(e) {
    if (!e.token) return;
    e.mat.color.copy(this.color(e.token));
    if (e.mul) e.mat.color.multiplyScalar(e.mul);
    if (e.dim) e.mat.color.lerp(this.dimTarget, 0.35);
    if (e.emissiveToken && e.mat.emissive) {
      e.mat.emissive.copy(this.color(e.emissiveToken)).multiplyScalar(e.emissiveK ?? 1);
    }
  }

  /**
   * A cached material.
   * kind: 'lambert' | 'basic'; key must be unique per (token, variant, group).
   */
  mat(key, token, kind = 'lambert', opts = {}) {
    let e = this.mats.get(key);
    if (e) return e.mat;
    const M = kind === 'basic' ? THREE.MeshBasicMaterial : THREE.MeshLambertMaterial;
    const mat = new M(opts);
    if (kind === 'basic') mat.toneMapped = false;
    e = { mat, token, dim: false };
    this.mats.set(key, e);
    this.apply(e);
    return mat;
  }

  entry(key) { return this.mats.get(key); }

  setDim(key, on) {
    const e = this.mats.get(key);
    if (!e || e.dim === on) return;
    e.dim = on;
    this.apply(e);
  }

  setToken(key, token) {
    const e = this.mats.get(key);
    if (!e || e.token === token) return;
    e.token = token;
    this.apply(e);
  }

  dispose() {
    for (const e of this.mats.values()) e.mat.dispose();
    this.mats.clear();
  }
}
