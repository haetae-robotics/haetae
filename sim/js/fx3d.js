// Floor decals and effects of the 3D diorama: zone decals from the loaded
// policy, intent / executed / ghost ribbons, target markers, the crack beyond
// a barrier, rule rings, zone and workspace flashes, the lab aim reticle, and
// the verdict effects (barrier, shield dome, speed gate, speed ring, green
// glow, impact ring).
//
// verdict() is the only entry that creates verdict effects, and it requires
// the frozen Decision the director copied into scene.fx. Every other verdict
// colour here (the executed ribbon, rule rings, chevrons) is drawn only from
// scene.exec / scene.hl, which the director built from the same Decision.
// The ghost path uses --intent only.

import * as THREE from 'three';
import { FLOOR_Y, w2v, yawOf, yawZ, geo, ribbon, dashedRing, floorPlane, roundedBox } from './geom3d.js';
import { prog, clamp01, dist, lerp, robotPose, segEnterT } from './model.js';

const Y = {
  zone: FLOOR_Y + 0.006, ws: FLOOR_Y + 0.008, ring: FLOOR_Y + 0.012,
  back: FLOOR_Y + 0.016, path: FLOOR_Y + 0.02, exec: FLOOR_Y + 0.022, mark: FLOOR_Y + 0.026,
};
const RO = { zone: 1, ws: 2, ring: 3, back: 4, path: 5, exec: 6, mark: 7, fx: 10 };

const easeOutBack = (t, s = 1.7) => 1 + (s + 1) * (t - 1) ** 3 + s * (t - 1) ** 2;
const easeOut = (t) => 1 - (1 - t) ** 3;

function flat(m, ro) {
  m.castShadow = false;
  m.receiveShadow = false;
  m.renderOrder = ro;
  return m;
}

export class Fx {
  /**
   * @param {{scene: THREE.Scene, pal: object, tex: object, makeLabel: Function, camera: THREE.Camera}} ctx
   */
  constructor(ctx) {
    this.ctx = ctx;
    const pal = (this.pal = ctx.pal);
    this.root = new THREE.Group();
    ctx.scene.add(this.root);
    const B = (key, token, o = {}) => pal.mat(key, token, 'basic', { transparent: true, depthWrite: false, side: THREE.DoubleSide, ...o });

    // Zones and workspace (rebuilt on policy apply).
    this.zoneGroup = new THREE.Group();
    this.root.add(this.zoneGroup);
    this.zones = new Map();
    this.wsMat = B('ws', 'ink-2', { opacity: 0.3 });
    this.ws = null;

    // Intent (violet): dashed ribbon + backing, arrowhead, marker, chip, crack.
    this.intentMat = B('intent', 'intent');
    this.backMat = B('back', 'surface', { opacity: 0.85 });
    this.intent = flat(new THREE.Mesh(new THREE.BufferGeometry(), this.intentMat), RO.path);
    this.intentBack = flat(new THREE.Mesh(new THREE.BufferGeometry(), this.backMat), RO.back);
    this.head = flat(new THREE.Mesh(geo('arrow', () => {
      const s = new THREE.Shape(); s.moveTo(0.2, 0); s.lineTo(-0.05, 0.12); s.lineTo(-0.05, -0.12); s.lineTo(0.2, 0);
      return new THREE.ShapeGeometry(s).rotateX(-Math.PI / 2);
    }), this.intentMat), RO.path);
    this.marker = this.makeMarker(this.intentMat);
    this.chip = ctx.makeLabel('', 'lbl-chip lbl-intent', 5);
    this.root.add(this.intentBack, this.intent, this.head, this.marker, this.chip);
    this.crack = [];
    for (let i = 0; i < 4; i++) {
      const m = flat(new THREE.Mesh(new THREE.BufferGeometry(), B(`crack${i}`, 'intent')), RO.path);
      this.crack.push(m);
      this.root.add(m);
    }

    // Executed path (verdict fill from scene.exec, copied from decision.action).
    this.execMat = B('exec', 'd3-yun');
    this.execFullMat = B('exec-full', 'd3-yun', { opacity: 0.45 });
    const unit = geo('unit-ribbon-10', () => ribbon({ x: 0, y: 0 }, { x: 1, y: 0 }, 0.1).geometry);
    const unitB = geo('unit-ribbon-14', () => ribbon({ x: 0, y: 0 }, { x: 1, y: 0 }, 0.16).geometry);
    this.execBack = flat(new THREE.Mesh(unitB, this.backMat), RO.back);
    this.execFull = flat(new THREE.Mesh(unit, this.execFullMat), RO.exec);
    this.execDone = flat(new THREE.Mesh(unit, this.execMat), RO.exec + 1);
    this.root.add(this.execBack, this.execFull, this.execDone);
    this.chevrons = [];
    const chevMat = (this.chevMat = B('chev', 'd3-jeol', { opacity: 1 }));
    for (let i = 0; i < 3; i++) {
      const c = new THREE.Group();
      for (const s of [1, -1]) {
        const bar = flat(new THREE.Mesh(geo('chev-bar', () => new THREE.PlaneGeometry(0.2, 0.05).rotateX(-Math.PI / 2)), chevMat), RO.mark);
        bar.rotation.y = s * 0.7;
        bar.position.set(-0.05, 0, s * 0.06);
        c.add(bar);
      }
      this.chevrons.push(c);
      this.root.add(c);
    }

    // Ghost path (violet only, never a verdict colour).
    this.ghostPathMat = B('ghost-path', 'intent', { opacity: 0.85 });
    this.ghostPath = flat(new THREE.Mesh(new THREE.BufferGeometry(), this.ghostPathMat), RO.path);
    this.ghostMarker = this.makeMarker(this.ghostPathMat);
    this.root.add(this.ghostPath, this.ghostMarker);

    // Lab aim.
    this.aimLine = flat(new THREE.Mesh(new THREE.BufferGeometry(), B('aim', 'intent', { opacity: 0.9 })), RO.path);
    this.reticle = new THREE.Group();
    const retMat = B('reticle', 'focus');
    this.reticle.add(flat(new THREE.Mesh(geo('ret-ring', () => new THREE.RingGeometry(0.14, 0.18, 32).rotateX(-Math.PI / 2)), retMat), RO.mark));
    for (const [x, z] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      const b = flat(new THREE.Mesh(geo('ret-bar', () => new THREE.PlaneGeometry(0.16, 0.03).rotateX(-Math.PI / 2)), retMat), RO.mark);
      b.position.set(x * 0.3, 0, z * 0.3);
      if (z) b.rotation.y = Math.PI / 2;
      this.reticle.add(b);
    }
    this.grabRing = flat(new THREE.Mesh(geo('grab-ring', () => new THREE.RingGeometry(0.34, 0.4, 32).rotateX(-Math.PI / 2)), retMat), RO.mark);
    this.root.add(this.aimLine, this.reticle, this.grabRing);

    // Rule rings (pool of 6).
    this.rings = [];
    for (let i = 0; i < 6; i++) {
      const g = new THREE.Group();
      const dash = flat(new THREE.Mesh(new THREE.BufferGeometry(), B(`ring-dash${i}`, 'bul-fg')), RO.ring + 1);
      const disc = flat(new THREE.Mesh(geo('unit-disc', () => new THREE.CircleGeometry(1, 48).rotateX(-Math.PI / 2)), B(`ring-disc${i}`, 'd3-bul', { opacity: 0.08 })), RO.ring);
      const band = flat(new THREE.Mesh(geo('unit-band', () => new THREE.CylinderGeometry(1, 1, 0.14, 48, 1, true).translate(0, 0.07, 0)),
        B(`ring-band${i}`, 'bul-fg', { opacity: 0.18, alphaMap: ctx.tex.bandFade })), RO.ring + 2);
      const lbl = ctx.makeLabel('', 'lbl-chip', 5);
      g.add(dash, disc, band, lbl);
      g.visible = false;
      g.userData = { dash, disc, band, lbl, r: 0, key: '' };
      this.rings.push(g);
      this.root.add(g);
    }

    // Verdict effects.
    this.hexMat = B('barrier', 'd3-bul', { opacity: 0.38, alphaMap: ctx.tex.hex });
    this.hexTex = ctx.tex.hex;
    this.solidBul = pal.mat('barrier-solid', 'd3-bul', 'basic', { transparent: true });
    this.barrier = this.makeBarrier(this.hexMat, this.solidBul);
    this.trailHex = B('barrier-trail', 'd3-bul', { opacity: 0.1, alphaMap: ctx.tex.hex });
    this.trailSolid = B('barrier-trail-solid', 'd3-bul', { opacity: 0.25 });
    this.trails = [0, 1, 2].map(() => this.makeBarrier(this.trailHex, this.trailSolid));
    this.impactMat = B('impact', 'd3-bul', { opacity: 0.6 });
    this.impact = flat(new THREE.Mesh(geo('impact', () => new THREE.RingGeometry(0.9, 1, 48).rotateX(-Math.PI / 2)), this.impactMat), RO.fx);
    this.dome = flat(new THREE.Mesh(geo('dome', () => new THREE.SphereGeometry(0.62, 28, 12, 0, Math.PI * 2, 0, Math.PI / 2)), this.hexMat), RO.fx);
    this.arch = this.makeArch();
    this.archChip = ctx.makeLabel('', 'lbl-chip v-jeol', 1);
    this.archChip.position.set(0, 0.95, 0);
    this.arch.add(this.archChip);
    this.speedRing = this.makeSpeedRing();
    this.glowMat = B('glow', 'd3-yun', { opacity: 0, alphaMap: ctx.tex.glow });
    this.glow = flat(new THREE.Mesh(geo('glow', () => new THREE.CircleGeometry(0.75, 40).rotateX(-Math.PI / 2)), this.glowMat), RO.mark);
    this.pulseMat = B('glow-pulse', 'd3-yun', { opacity: 0 });
    this.pulse = flat(new THREE.Mesh(geo('pulse', () => new THREE.RingGeometry(0.92, 1, 48).rotateX(-Math.PI / 2)), this.pulseMat), RO.mark);
    this.root.add(this.barrier, ...this.trails, this.impact, this.dome, this.arch, this.speedRing, this.glow, this.pulse);
    this.hideVerdict();
    for (const t of this.trails) t.visible = false;

    this.cache = { intent: null, crack: null, ghost: null, aim: '', fx: null };
    this.v = null; // configured verdict effect
  }

  makeMarker(mat) {
    const g = new THREE.Group();
    g.add(flat(new THREE.Mesh(geo('mark-ring', () => new THREE.RingGeometry(0.13, 0.19, 32).rotateX(-Math.PI / 2)), mat), RO.mark));
    const pin = flat(new THREE.Mesh(geo('pin', () => new THREE.CylinderGeometry(0.012, 0.012, 0.3, 6).translate(0, 0.15, 0)), mat), RO.mark);
    const ball = flat(new THREE.Mesh(geo('pin-ball', () => new THREE.SphereGeometry(0.045, 12, 8).translate(0, 0.31, 0)), mat), RO.mark);
    g.add(pin, ball);
    g.visible = false;
    return g;
  }

  /**
   * The barrier: a shallow 120-degree arc of lattice (radius BARRIER.r), its
   * middle at the anchor and bowing toward the target (local +Z = path
   * direction), so it keeps frontal area from any camera azimuth.
   */
  makeBarrier(hexMat, solidMat) {
    const { r, h, arc } = BARRIER;
    const g = new THREE.Group();
    const inner = new THREE.Group();
    g.add(inner);
    const shell = flat(new THREE.Mesh(geo('barrier-arc', () => new THREE.CylinderGeometry(r, r, h, 24, 1, true, -arc / 2, arc).translate(0, h / 2, -r)), hexMat), RO.fx);
    const bar = flat(new THREE.Mesh(geo('barrier-arc-bar', () => new THREE.TorusGeometry(r, 0.02, 6, 24, arc)
      .rotateZ(Math.PI / 2 - arc / 2).rotateX(Math.PI / 2).translate(0, h, -r)), solidMat), RO.fx + 1);
    const post = geo('barrier-post', () => new THREE.BoxGeometry(0.035, h, 0.035).translate(0, h / 2, 0));
    const p1 = flat(new THREE.Mesh(post, solidMat), RO.fx + 1);
    const p2 = flat(new THREE.Mesh(post, solidMat), RO.fx + 1);
    const ex = r * Math.sin(arc / 2);
    const ez = -r + r * Math.cos(arc / 2);
    p1.position.set(-ex, 0, ez);
    p2.position.set(ex, 0, ez);
    inner.add(shell, bar, p1, p2);
    g.userData.inner = inner;
    return g;
  }

  /** World (three) points bounding a configured verdict effect at full size, or null. */
  effectPoints(kind, pose) {
    const v = this.v;
    if (!v || v.kind !== kind) return null;
    const pts = this.effPts ??= Array.from({ length: 14 }, () => new THREE.Vector3());
    let n = 0;
    if (kind === 'barrier') {
      const { r, arc } = BARRIER;
      this.barrier.updateMatrixWorld(true);
      for (const th of [-arc / 2, -arc / 4, 0, arc / 4, arc / 2]) {
        for (const y of [0, 1.0]) pts[n++].set(r * Math.sin(th), y, -r + r * Math.cos(th)).applyMatrix4(this.barrier.matrixWorld);
      }
    } else if (kind === 'dome') {
      const c = w2v(pose, FLOOR_Y, this.tmpC ??= new THREE.Vector3());
      for (const [dx, dz] of [[-0.62, 0], [0.62, 0], [0, -0.62], [0, 0.62]]) pts[n++].set(c.x + dx, c.y, c.z + dz);
      pts[n++].set(c.x, c.y + 0.62, c.z);
    } else if (kind === 'arch') {
      this.arch.updateMatrixWorld(true);
      for (const x of [-0.6, 0.6]) for (const y of [0, 0.8]) pts[n++].set(x, y, 0).applyMatrix4(this.arch.matrixWorld);
      if (this.archChip.element.textContent) pts[n++].set(0, 1.05, 0).applyMatrix4(this.arch.matrixWorld);
    }
    return pts.slice(0, n);
  }

  makeArch() {
    const g = new THREE.Group();
    const inner = new THREE.Group();
    g.add(inner);
    const m = this.pal.mat('arch', 'd3-jeol', 'basic', { transparent: true });
    const post = geo('arch-post', () => roundedBox(0.07, 0.7, 0.07, 0.02).clone().translate(0, 0.35, 0));
    const a = flat(new THREE.Mesh(post, m), RO.fx);
    const b = a.clone();
    a.position.x = -0.55;
    b.position.x = 0.55;
    const bar = flat(new THREE.Mesh(geo('arch-bar', () => new THREE.BoxGeometry(1.22, 0.08, 0.08).translate(0, 0.72, 0)), m), RO.fx);
    inner.add(a, b, bar);
    g.userData.inner = inner;
    return g;
  }

  makeSpeedRing() {
    const g = new THREE.Group();
    const m = this.pal.mat('speed-ring', 'd3-jeol', 'basic', { transparent: true });
    g.add(flat(new THREE.Mesh(geo('speed-torus', () => new THREE.RingGeometry(0.42, 0.47, 48).rotateX(-Math.PI / 2)), m), RO.mark));
    const notches = new THREE.Group();
    for (let i = 0; i < 8; i++) {
      const n = flat(new THREE.Mesh(geo('notch', () => new THREE.PlaneGeometry(0.1, 0.035).rotateX(-Math.PI / 2)), m), RO.mark);
      const a = (i / 8) * Math.PI * 2;
      n.position.set(Math.cos(a) * 0.52, 0, Math.sin(a) * 0.52);
      n.rotation.y = -a;
      notches.add(n);
    }
    g.add(notches);
    g.userData.notches = notches;
    return g;
  }

  // ───────────────────────── Zones (from the loaded policy) ─────────────────────────

  setPolicy(policy) {
    for (const z of this.zones.values()) {
      z.g.traverse((o) => { if (o.isMesh && o.userData.own) o.geometry.dispose(); });
      z.chip.element.remove();
      this.zoneGroup.remove(z.g);
    }
    this.zones.clear();
    if (this.ws) { this.ws.traverse((o) => { if (o.isMesh) o.geometry.dispose(); }); this.zoneGroup.remove(this.ws); }
    const pal = this.pal;
    const B = (key, token, o = {}) => pal.mat(key, token, 'basic', { transparent: true, depthWrite: false, side: THREE.DoubleSide, ...o });
    const clampR = (r) => ({
      x0: Math.max(-0.9, Math.min(10.9, r.min.x)), x1: Math.max(-0.9, Math.min(10.9, r.max.x)),
      y0: Math.max(-1.6, Math.min(10.9, r.min.y)), y1: Math.max(-1.6, Math.min(10.9, r.max.y)),
    });
    (policy.zones ?? []).forEach((z, i) => {
      const r = clampR(z.area);
      const w = r.x1 - r.x0;
      const d = r.y1 - r.y0;
      if (!(w > 0 && d > 0)) return;
      const g = new THREE.Group();
      w2v({ x: (r.x0 + r.x1) / 2, y: (r.y0 + r.y1) / 2 }, Y.zone, g.position);
      const deny = !!z.no_entry;
      const tok = deny ? 'zone-deny' : 'zone-slow';
      const base = deny ? 0.12 : 0.16;
      const hatchMat = B(`zone-hatch${i}:${tok}`, tok, { opacity: base, alphaMap: deny ? this.ctx.tex.crosshatch : this.ctx.tex.hatch });
      const hatch = flat(new THREE.Mesh(floorPlane(w, d, 0.5), hatchMat), RO.zone);
      hatch.userData.own = true;
      g.add(hatch);
      const item = { id: z.id, deny, g, hatchMat, base, center: { x: (r.x0 + r.x1) / 2, y: (r.y0 + r.y1) / 2 } };
      if (deny) {
        // Dashed border of dash quads.
        const bm = B(`zone-border${i}:${tok}`, tok, { opacity: 0.9 });
        const corners = [{ x: r.x0, y: r.y0 }, { x: r.x1, y: r.y0 }, { x: r.x1, y: r.y1 }, { x: r.x0, y: r.y1 }];
        for (let k = 0; k < 4; k++) {
          const a = corners[k];
          const b = corners[(k + 1) % 4];
          const rb = ribbon(a, b, 0.04, 0.2, 0.12);
          const m = flat(new THREE.Mesh(rb.geometry, bm), RO.zone + 0.5);
          m.userData.own = true;
          w2v(a, 0.001, m.position).sub(g.position).setY(0.001);
          g.add(m);
        }
        // Lock pictogram floating over the zone.
        const lock = new THREE.Group();
        const lm = pal.mat(`zone-lock${i}:${tok}`, tok, 'lambert');
        const body = new THREE.Mesh(roundedBox(0.34, 0.28, 0.12, 0.04), lm);
        const shackle = new THREE.Mesh(geo('shackle', () => new THREE.TorusGeometry(0.11, 0.028, 8, 20, Math.PI)), lm);
        shackle.position.y = 0.14;
        body.castShadow = true;
        lock.add(body, shackle);
        lock.position.y = 0.9;
        g.add(lock);
        item.lock = lock;
        item.chipText = `${zoneKo(z.id)} · 출입 금지`;
      } else if (z.speed_limit != null) {
        const rm = pal.mat(`zone-roundel${i}:${tok}`, tok, 'basic', { transparent: true, side: THREE.DoubleSide });
        const bg = B(`zone-roundel-bg${i}`, 'surface', { opacity: 0.9 });
        const roundel = new THREE.Group();
        roundel.add(flat(new THREE.Mesh(geo('roundel-bg', () => new THREE.CircleGeometry(0.42, 36).rotateX(-Math.PI / 2)), bg), RO.zone + 0.6));
        roundel.add(flat(new THREE.Mesh(geo('roundel-ring', () => new THREE.RingGeometry(0.34, 0.44, 36).rotateX(-Math.PI / 2)), rm), RO.zone + 0.7));
        const ry = Math.min(r.y1, 10) - 1.1;
        w2v({ x: item.center.x, y: ry }, 0.002, roundel.position).sub(g.position).setY(0.002);
        g.add(roundel);
        item.roundel = roundel;
        item.chipText = `${zoneKo(z.id)} · ${fmt(z.speed_limit)} m/s`;
        item.chipAt = { x: item.center.x, y: ry };
      } else {
        item.chipText = zoneKo(z.id);
      }
      item.chip = this.ctx.makeLabel(item.chipText, `lbl-zone ${deny ? 'lbl-deny' : 'lbl-slow'}`, 6);
      if (item.chipAt) w2v(item.chipAt, 0.05, item.chip.position).sub(g.position);
      else item.chip.position.set(0, deny ? 0.55 : 0.05, 0);
      g.add(item.chip);
      this.zoneGroup.add(g);
      this.zones.set(z.id, item);
    });
    // Workspace inset line.
    const ws = policy.envelope?.workspace;
    if (ws) {
      const g = new THREE.Group();
      const c = [{ x: ws.min.x, y: ws.min.y }, { x: ws.max.x, y: ws.min.y }, { x: ws.max.x, y: ws.max.y }, { x: ws.min.x, y: ws.max.y }];
      for (let k = 0; k < 4; k++) {
        const rb = ribbon(c[k], c[(k + 1) % 4], 0.035);
        const m = flat(new THREE.Mesh(rb.geometry, this.wsMat), RO.ws);
        w2v(c[k], Y.ws, m.position);
        g.add(m);
      }
      this.ws = g;
      this.zoneGroup.add(g);
    }
  }

  // ───────────────────────── Verdict effects ─────────────────────────

  hideVerdict() {
    this.barrier.visible = false;
    this.impact.visible = false;
    this.dome.visible = false;
    this.arch.visible = false;
    this.speedRing.visible = false;
    this.glow.visible = false;
    this.pulse.visible = false;
  }

  /**
   * The only entry that creates verdict effects. Requires the frozen Decision.
   * anchor: sealAnchor() placement. ctx: { from, target, head, policy, t0 }.
   */
  verdict(decision, anchor, ctx) {
    this.hideVerdict();
    this.v = null;
    if (!decision || !decision.verdict) return;
    const v = decision.verdict;
    const from = ctx.from;
    const target = ctx.target;
    const cfg = { kind: null, t0: ctx.t0, verdict: v };
    if (v === 'bul') {
      const spatial = anchor?.type === 'world' && target && ctx.head !== 'envelope:pose';
      if (spatial) {
        cfg.kind = 'barrier';
        w2v(anchor.p, FLOOR_Y, this.barrier.position);
        // Across the path, turned up to 45 degrees toward the camera so it never reads edge-on.
        const yaw = yawZ(anchor.dir.x, anchor.dir.y);
        const cam = this.ctx.camera.position;
        const toCam = Math.atan2(cam.x - this.barrier.position.x, cam.z - this.barrier.position.z);
        let off = toCam - yaw;
        off = Math.atan2(Math.sin(off), Math.cos(off));
        if (off > Math.PI / 2) off -= Math.PI; // the lattice is two-sided: face the camera or away
        if (off < -Math.PI / 2) off += Math.PI;
        const lim = 40 * Math.PI / 180;
        const turn = Math.sign(off) * Math.min(Math.PI / 4, Math.max(0, Math.abs(off) - lim));
        this.barrier.rotation.y = yaw + turn;
        w2v(anchor.p, Y.mark, this.impact.position);
        cfg.at = { ...anchor.p };
      } else {
        cfg.kind = 'dome';
      }
    } else if (v === 'jeol') {
      const a = decision.action;
      const goal = a?.type === 'move_to' ? a.goal : (a?.at ?? null);
      if (goal && dist(from, goal) > 1e-6) {
        let p = lerp(from, goal, 0.5);
        const hz = ctx.head?.startsWith('zone:') ? ctx.policy?.zones?.find((z) => z.id === ctx.head.slice(5)) : null;
        if (hz?.speed_limit != null) {
          const t = segEnterT(from, goal, hz.area);
          if (t != null && t > 0.02) p = lerp(from, goal, t);
        }
        cfg.kind = 'arch';
        w2v(p, FLOOR_Y, this.arch.position);
        this.arch.rotation.y = yawZ(goal.x - from.x, goal.y - from.y);
        this.archChip.element.textContent = decision.speed_cap != null ? `≤ ${fmt(decision.speed_cap)} m/s` : '';
        this.archChip.visible = decision.speed_cap != null;
        cfg.at = p;
      }
      cfg.speed = a?.speed ?? 0;
    } else {
      cfg.kind = 'glow';
      const p = target ?? from;
      w2v(p, Y.mark, this.glow.position);
      w2v(p, Y.mark, this.pulse.position);
      cfg.at = { ...p };
    }
    this.v = cfg;
  }

  // ───────────────────────── Per frame ─────────────────────────

  /** Verdict effects follow scene.fx (a reference to the frozen Decision). */
  syncVerdict(sc) {
    const fx = sc.fx ?? null;
    if (fx === this.cache.fx) return;
    this.cache.fx = fx;
    if (fx) this.verdict(fx.decision, fx.anchor, { from: fx.from, target: fx.target, head: fx.head, policy: sc.policy, t0: fx.t0 });
    else { this.hideVerdict(); this.v = null; }
  }

  /**
   * Returns true while something timed by the scene clock is still changing.
   * Everything here is timed by the clock, so a frozen clock (paused) never
   * asks for frames.
   */
  update(sc, clock, real, dt, clockMoving = true) {
    const reduced = !!sc.reduced;
    let anim = false;
    const pose = robotPose(sc, clock, real);
    this.syncVerdict(sc);
    anim = this.updateVerdict(sc, clock, real, reduced, pose) || anim;
    this.updateTrails(sc);
    anim = this.updateIntent(sc, clock, reduced) || anim;
    anim = this.updateExec(sc, clock, pose) || anim;
    this.updateGhost(sc);
    this.updateLab(sc, pose);
    anim = this.updateRings(sc, clock, reduced, pose) || anim;
    anim = this.updateZones(sc, clock, real, reduced) || anim;
    // Lattice scroll: advanced by whatever frames run (idle ticks are capped); frozen under reduced motion.
    if (!reduced && clockMoving && (this.barrier.visible || this.dome.visible)) {
      this.hexTex.offset.y = (this.hexTex.offset.y + dt * 0.00008) % 1;
    }
    return anim && clockMoving;
  }

  updateVerdict(sc, clock, real, reduced, pose) {
    const v = this.v;
    if (!v) return false;
    const t = clock - v.t0;
    if (t < 0) { this.hideVerdict(); return true; }
    const alpha = sc.realAlpha ?? 1;
    let anim = false;
    const fade = reduced ? clamp01(t / 120) : 1;
    if (v.kind === 'barrier') {
      this.barrier.visible = true;
      const k = reduced ? 1 : clamp01(t / 260);
      const s = reduced ? 1 : Math.min(1.06, easeOutBack(k, 0.9));
      this.barrier.userData.inner.scale.set(1, Math.max(0.001, s), 1);
      this.hexMat.opacity = 0.38 * fade * alpha;
      this.solidBul.opacity = fade * alpha;
      anim = k < 1 || fade < 1;
      if (!reduced && t < 420) {
        this.impact.visible = true;
        const q = t / 420;
        this.impact.scale.setScalar(0.2 + 1.4 * easeOut(q));
        this.impactMat.opacity = 0.6 * (1 - q);
        anim = true;
      } else {
        this.impact.visible = false;
      }
    } else if (v.kind === 'dome') {
      this.dome.visible = true;
      w2v(pose, FLOOR_Y, this.dome.position);
      const k = reduced ? 1 : clamp01(t / 260);
      this.dome.scale.setScalar(Math.max(0.001, easeOut(k)));
      this.hexMat.opacity = 0.38 * fade * alpha;
      anim = k < 1 || fade < 1;
    } else if (v.kind === 'arch') {
      this.arch.visible = true;
      const k = reduced ? 1 : clamp01(t / 220);
      this.arch.userData.inner.scale.set(1, Math.max(0.001, easeOut(k)), 1);
      this.pal.entry('arch').mat.opacity = fade;
      this.archChip.visible = k >= 1 && this.archChip.element.textContent !== '';
      anim = k < 1 || fade < 1;
    } else if (v.kind === 'glow') {
      this.glow.visible = true;
      const k = reduced ? 1 : clamp01(t / 300);
      this.glowMat.opacity = reduced ? 0.25 * fade : (k < 0.5 ? 0.9 * k : 0.45 - 0.5 * (k - 0.5));
      if (!reduced && t < 600) {
        this.pulse.visible = true;
        const q = t / 600;
        this.pulse.scale.setScalar(0.3 + 0.8 * easeOut(q));
        this.pulseMat.opacity = 0.7 * (1 - q);
        anim = true;
      } else this.pulse.visible = false;
      anim = anim || k < 1;
    }
    // Speed ring under the robot during the executed motion (jeol).
    const e = sc.exec;
    const moving = v.verdict === 'jeol' && e && e.kind === 'move' && clock >= e.t0 && prog(e, clock) < 1;
    this.speedRing.visible = !!moving;
    if (moving) {
      w2v(pose, Y.mark, this.speedRing.position);
      if (!reduced) this.speedRing.userData.notches.rotation.y -= 0.02 * (v.speed || 0.1) * 6;
    }
    return anim;
  }

  updateTrails(sc) {
    const trail = sc.trail ?? [];
    this.trails.forEach((g, i) => {
      const tr = trail[i];
      g.visible = !!tr;
      if (!tr) return;
      w2v(tr.at, FLOOR_Y, g.position);
      g.rotation.y = yawZ(tr.dir.x, tr.dir.y);
      g.userData.inner.scale.set(1, 0.25, 1);
    });
  }

  updateIntent(sc, clock, reduced) {
    const it = sc.intent;
    const on = it && clock >= it.t0;
    if (!on) {
      this.intent.visible = this.intentBack.visible = this.head.visible = this.marker.visible = this.chip.visible = false;
      for (const c of this.crack) c.visible = false;
      return !!it;
    }
    const crack = it.crack && clock >= it.crack.t0 ? it.crack : null;
    if (this.cache.intent !== it || this.cache.crack !== crack) {
      this.cache.intent = it;
      this.cache.crack = crack;
      const end = crack ? crack.at : it.to;
      const rb = ribbon(it.from, end, 0.07, 0.25, 0.2);
      this.intent.geometry.dispose();
      this.intent.geometry = rb.geometry;
      this.intent.userData.quads = rb.quads;
      const bb = ribbon(it.from, end, 0.13);
      this.intentBack.geometry.dispose();
      this.intentBack.geometry = bb.geometry;
      w2v(it.from, Y.path, this.intent.position);
      w2v(it.from, Y.back, this.intentBack.position);
      // The part beyond the barrier: four pieces.
      this.crack.forEach((m, i) => {
        m.visible = !!crack;
        if (!crack) return;
        const a = lerp(crack.at, it.to, i / 4 + 0.04);
        const b = lerp(crack.at, it.to, (i + 1) / 4 - 0.04);
        const r = ribbon(a, b, 0.07, 0.25, 0.2);
        m.geometry.dispose();
        m.geometry = r.geometry;
        w2v(a, Y.path, m.position);
        m.userData.base = m.position.y;
      });
      w2v(it.to, Y.mark, this.marker.position);
      const ang = yawOf(it.to.x - it.from.x, it.to.y - it.from.y);
      this.head.rotation.y = ang;
      w2v(it.to, Y.path, this.head.position);
    }
    let alpha = it.alpha ?? 1;
    if (it.fadeT0 != null && clock >= it.fadeT0) alpha *= 1 - clamp01((clock - it.fadeT0) / 400);
    alpha *= sc.realAlpha ?? 1;
    const p = prog(it, clock);
    const vis = alpha > 0.01;
    this.intent.visible = this.intentBack.visible = vis;
    const q = this.intent.userData.quads ?? 0;
    const shown = crack ? q : Math.ceil(q * p);
    this.intent.geometry.setDrawRange(0, shown * 6);
    this.intentBack.visible = vis && p >= 1;
    this.intentMat.opacity = alpha;
    this.backMat.opacity = 0.85 * alpha;
    this.head.visible = vis && p >= 1 && !crack;
    this.marker.visible = vis && p >= 1;
    let anim = p < 1 || (it.fadeT0 != null && alpha > 0.01);
    if (crack) {
      const k = reduced ? 1 : clamp01((clock - crack.t0) / 400);
      this.crack.forEach((m, i) => {
        m.visible = vis;
        m.material.opacity = alpha * (1 - 0.7 * k);
        m.position.y = m.userData.base - k * 0.04 - (i % 2) * 0.004 * k;
      });
      anim = anim || k < 1;
      this.head.visible = false;
      if (vis) { this.head.visible = false; }
    }
    // Midpoint chip: the requested speed from the proposal.
    const showChip = vis && p >= 1 && it.speed != null && !it.noPill && dist(it.from, it.to) > 0.8;
    this.chip.visible = showChip;
    if (showChip) {
      const m = lerp(it.from, it.to, it.pillT ?? 0.35);
      w2v(m, 0.12, this.chip.position);
      const text = `요청 ${fmt(it.speed)} m/s`;
      if (this.chip.element.textContent !== text) this.chip.element.textContent = text;
    }
    return anim;
  }

  updateExec(sc, clock, pose) {
    const e = sc.exec;
    const on = e && e.kind === 'move' && clock >= e.t0 && dist(e.from, e.to) > 1e-6;
    this.execBack.visible = this.execFull.visible = this.execDone.visible = !!on;
    for (const c of this.chevrons) c.visible = false;
    if (!on) return false;
    const L = dist(e.from, e.to);
    const yaw = yawOf(e.to.x - e.from.x, e.to.y - e.from.y);
    const tok = `d3-${e.verdict}`;
    this.pal.setToken('exec', tok);
    this.pal.setToken('exec-full', tok);
    for (const [m, y] of [[this.execBack, Y.back], [this.execFull, Y.exec], [this.execDone, Y.exec + 0.002]]) {
      w2v(e.from, y, m.position);
      m.rotation.y = yaw;
    }
    this.execBack.scale.x = L;
    this.execFull.scale.x = L;
    // Comparison replay: the real robot's recorded effects are dimmed with it.
    const a = sc.realAlpha ?? 1;
    if (this.execMat.opacity !== a) {
      this.execMat.opacity = a;
      this.execFullMat.opacity = 0.45 * a;
      this.chevMat.opacity = a;
    }
    const p = prog(e, clock);
    this.execDone.scale.x = Math.max(0.001, L * p);
    if (e.chevrons) {
      [0.3, 0.5, 0.7].forEach((f, i) => {
        const c = this.chevrons[i];
        c.visible = L > 0.8;
        w2v(lerp(e.from, e.to, f), Y.mark, c.position);
        c.rotation.y = yaw;
      });
    }
    return p < 1;
  }

  updateGhost(sc) {
    const g = sc.ghost;
    const on = g && g.kind !== 'none';
    this.ghostPath.visible = this.ghostMarker.visible = !!on;
    if (!on) { this.cache.ghost = null; return; }
    if (this.cache.ghost !== g) {
      this.cache.ghost = g;
      const to = g.kind === 'move' ? g.to : g.at;
      const rb = ribbon(g.from, to, 0.07, 0.25, 0.2);
      this.ghostPath.geometry.dispose();
      this.ghostPath.geometry = rb.geometry;
      w2v(g.from, Y.path + 0.001, this.ghostPath.position);
      w2v(to, Y.mark, this.ghostMarker.position);
    }
  }

  updateLab(sc, pose) {
    const lab = sc.lab;
    const aimAt = lab?.preview ?? null;
    const cur = lab?.preview ?? lab?.cursor ?? null;
    this.reticle.visible = !!cur;
    if (cur) w2v(cur, Y.mark, this.reticle.position);
    const key = aimAt ? `${pose.x},${pose.y},${aimAt.x},${aimAt.y}` : '';
    if (key !== this.cache.aim) {
      this.cache.aim = key;
      if (aimAt) {
        const rb = ribbon(pose, aimAt, 0.035, 0.16, 0.12);
        this.aimLine.geometry.dispose();
        this.aimLine.geometry = rb.geometry;
        w2v(pose, Y.path, this.aimLine.position);
      }
    }
    this.aimLine.visible = !!aimAt;
    const gh = lab?.grabbed ? sc.world?.humans?.find((h) => h.id === lab.grabbed) : null;
    this.grabRing.visible = !!gh;
    if (gh) w2v(gh.pos, Y.mark, this.grabRing.position);
  }

  updateRings(sc, clock, reduced, pose) {
    const list = (sc.hl ?? []).filter((h) => h.kind === 'ring' && clock >= h.t0);
    let anim = false;
    const cam = this.ctx.camera;
    this.rings.forEach((g, i) => {
      const h = list[i];
      g.visible = !!h;
      if (!h) return;
      const u = g.userData;
      const key = `${h.r}`;
      if (u.key !== key) {
        u.key = key;
        u.dash.geometry.dispose();
        u.dash.geometry = dashedRing(h.r, 0.05, 36, 0.58);
      }
      w2v(h.center, Y.ring, g.position);
      u.disc.scale.setScalar(h.r);
      u.band.scale.set(h.r, 1, h.r);
      const tone = h.tone === 'bul' ? 'bul' : 'jeol';
      this.pal.setToken(`ring-dash${i}`, `${tone}-fg`);
      this.pal.setToken(`ring-disc${i}`, `d3-${tone}`);
      this.pal.setToken(`ring-band${i}`, `${tone}-fg`);
      const a = reduced ? 1 : clamp01((clock - h.t0) / 250);
      u.dash.material.opacity = a * (sc.realAlpha ?? 1);
      u.disc.material.opacity = 0.08 * a;
      u.band.material.opacity = 0.18 * a;
      anim = anim || a < 1;
      // Radius label: 80 degrees (+25 per further ring) away from where the
      // robot comes from / the barrier stands, on the side facing the camera.
      const cx = cam.position.x - g.position.x;
      const cz = cam.position.z - g.position.z;
      const cL = Math.hypot(cx, cz) || 1;
      const from = this.barrier.visible ? this.barrier.position : w2v(pose, 0, this.tmpP ??= new THREE.Vector3());
      let ox = from.x - g.position.x;
      let oz = from.z - g.position.z;
      const oL = Math.hypot(ox, oz);
      if (oL < 1e-3) { ox = cx / cL; oz = cz / cL; } else { ox /= oL; oz /= oL; }
      const ang = (80 + 25 * i) * Math.PI / 180;
      const rot = (s) => ({ x: ox * Math.cos(s * ang) - oz * Math.sin(s * ang), z: ox * Math.sin(s * ang) + oz * Math.cos(s * ang) });
      const A = rot(1);
      const Bq = rot(-1);
      const q = (A.x * cx + A.z * cz) >= (Bq.x * cx + Bq.z * cz) ? A : Bq;
      u.lbl.position.set(q.x * h.r, 0.2, q.z * h.r);
      const text = `${fmt(h.r)} m`;
      if (u.lbl.element.textContent !== text) u.lbl.element.textContent = text;
      if (u.tone !== tone) {
        // Only the tone class changes; lbl-hide / lbl-behind belong to the label pass.
        u.lbl.element.classList.remove('v-yun', 'v-jeol', 'v-bul');
        u.lbl.element.classList.add(`v-${tone}`);
        u.tone = tone;
      }
      u.lbl.visible = a >= 1;
    });
    return anim;
  }

  updateZones(sc, clock, real, reduced) {
    let anim = false;
    const hls = sc.hl ?? [];
    for (const z of this.zones.values()) {
      const hl = hls.find((h) => h.kind === 'zone' && h.id === z.id && clock >= h.t0);
      let op = z.base;
      let lockS = 1;
      let roundS = 1;
      if (hl) {
        const t = clock - hl.t0;
        if (z.deny) {
          if (reduced) op = 0.3;
          else if (t < 600) { op = z.base + (0.4 - z.base) * Math.abs(Math.sin((t / 300) * Math.PI)); anim = true; } else op = 0.24;
          if (!reduced && t < 600) lockS = 1 + 0.3 * Math.sin((t / 600) * Math.PI);
        } else {
          op = reduced ? 0.3 : z.base + 0.16 * (t < 600 ? Math.sin((t / 600) * Math.PI) : 0.6);
          if (!reduced && t < 600) { roundS = 1 + 0.2 * Math.sin((t / 600) * Math.PI); anim = true; }
        }
      }
      z.hatchMat.opacity = op;
      if (z.lock) {
        z.lock.scale.setScalar(lockS);
        z.lock.position.y = 0.9 + (reduced ? 0 : Math.sin(real / 700) * 0.04);
        z.lock.rotation.y = reduced ? 0.3 : 0.3 + Math.sin(real / 2100) * 0.25;
      }
      if (z.roundel) z.roundel.scale.setScalar(roundS);
    }
    const ws = hls.find((h) => h.kind === 'workspace' && clock >= h.t0);
    this.pal.setToken('ws', ws ? 'bul-fg' : 'ink-2');
    this.wsMat.opacity = ws ? (reduced ? 0.9 : 0.5 + 0.5 * Math.abs(Math.sin((clock - ws.t0) / 150))) : 0.3;
    if (ws && !reduced) anim = true;
    return anim;
  }

  dispose() {
    this.root.traverse((o) => { if (o.isMesh && !o.geometry.userData?.shared) o.geometry.dispose?.(); });
  }
}

const BARRIER = { r: 0.8, h: 0.9, arc: (120 * Math.PI) / 180 };

const ZONE_KO = { hall: '복도', 'child-room': '아이 방' };
const zoneKo = (id) => ZONE_KO[id] ?? id;

function fmt(v) {
  return typeof v === 'number' && Number.isFinite(v) ? String(Math.round(v * 100) / 100) : String(v);
}
