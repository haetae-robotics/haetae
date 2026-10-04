// Actors of the 3D diorama: the robot (badge, eyes, gripper, lidar, wheels,
// flinch), people, props, the charging dock, the fake tag, the peer robot and
// its signal orbs, and the unfiltered ghost robot.
//
// Everything here only DRAWS the scene object the director built. Verdict
// colours appear only where the scene carries a frozen Decision (scene.fx)
// or a reaction the director started from one. The ghost uses --intent only.

import * as THREE from 'three';
import {
  FLOOR_Y, w2v, yawOf, geo, roundedBox, shieldShape, boltShape, dashedRing,
} from './geom3d.js';
import {
  FURN3D, robotPose, displayHolding, ghostHolding, ghostPose, prog, clamp01, dist,
} from './model.js';

const D2R = Math.PI / 180;
const TAU = Math.PI * 2;

function angleLerp(a, b, t) {
  let d = ((b - a + Math.PI) % TAU + TAU) % TAU - Math.PI;
  return a + d * t;
}

function mesh(g, m, { cast = true, receive = false } = {}) {
  const x = new THREE.Mesh(g, m);
  x.castShadow = cast;
  x.receiveShadow = receive;
  return x;
}

const G = {
  cyl: (rt, rb, h, s = 18) => geo(`cyl:${rt}:${rb}:${h}:${s}`, () => new THREE.CylinderGeometry(rt, rb, h, s)),
  sph: (r, w = 16, h = 12) => geo(`sph:${r}:${w}:${h}`, () => new THREE.SphereGeometry(r, w, h)),
  box: (w, h, d) => geo(`box:${w}:${h}:${d}`, () => new THREE.BoxGeometry(w, h, d)),
  cap: (r, l) => geo(`cap:${r}:${l}`, () => new THREE.CapsuleGeometry(r, l, 4, 12)),
  torus: (r, t, s = 32) => geo(`tor:${r}:${t}:${s}`, () => new THREE.TorusGeometry(r, t, 6, s)),
};

// ───────────────────────── Props ─────────────────────────

/** Knife along local +X: handle at the origin (the grasp point), blade toward +X. 1.5x toy scale. */
function makeKnife(M, outline) {
  const g = new THREE.Group();
  const handle = mesh(G.cyl(0.018, 0.018, 0.16, 10), M('d3-wood-dark'));
  handle.rotation.z = Math.PI / 2;
  handle.position.x = 0.08;
  const blade = mesh(G.box(0.3, 0.015, 0.06), M('knife-blade'));
  blade.position.x = 0.16 + 0.15;
  g.add(handle, blade);
  if (outline) {
    const hull = new THREE.Mesh(G.box(0.3, 0.015, 0.06), outline);
    hull.scale.set(1.12, 1.9, 1.3);
    hull.position.copy(blade.position);
    g.add(hull);
  }
  g.userData.blade = blade;
  return g;
}

function makeCup(M) {
  const g = new THREE.Group();
  const c = mesh(geo('cup', () => new THREE.CylinderGeometry(0.05, 0.045, 0.09, 16, 1, true)), M('surface-double'));
  c.position.y = 0.045;
  const inner = mesh(G.cyl(0.046, 0.046, 0.005, 16), M('d3-metal'));
  inner.position.y = 0.01;
  g.add(c, inner);
  return g;
}

/** Teddy (3 spheres), in wood brown: never a verdict colour. */
function makeToy(M, s = 1) {
  const g = new THREE.Group();
  const body = mesh(G.sph(0.05 * s, 12, 8), M('d3-wood'));
  body.position.y = 0.05 * s;
  const head = mesh(G.sph(0.035 * s, 12, 8), M('d3-wood'));
  head.position.y = 0.12 * s;
  const e1 = mesh(G.sph(0.013 * s, 8, 6), M('d3-wood'));
  e1.position.set(0, 0.155 * s, 0.025 * s);
  const e2 = e1.clone();
  e2.position.z = -0.025 * s;
  g.add(body, head, e1, e2);
  return g;
}

function makeHeld(M, outline) {
  const slots = { knife: makeKnife(M, outline), cup: makeCup(M), toy: makeToy(M) };
  slots.knife.position.set(-0.02, 0, 0);
  const g = new THREE.Group();
  for (const s of Object.values(slots)) { s.visible = false; g.add(s); }
  g.userData.slots = slots;
  return g;
}

function showHeld(held, what) {
  const slots = held.userData.slots;
  for (const [k, s] of Object.entries(slots)) s.visible = k === what;
  if (what && !slots[what]) slots.toy.visible = true; // unknown object: a generic toy
}

// ───────────────────────── Robot ─────────────────────────

/** Build a robot. M(token) gives a material; ghost=true skips the badge and outline. */
function buildRobot(M, { ghost = false, outline = null } = {}) {
  const root = new THREE.Group();
  const yaw = new THREE.Group();
  const tilt = new THREE.Group();
  tilt.position.y = 0.09;
  root.add(yaw);
  yaw.add(tilt);
  const P = new THREE.Group(); // parts, in the frame whose origin is the floor under the axle
  P.position.y = -0.09;
  tilt.add(P);

  const baseM = M('robot-base');
  const bodyM = M('robot-body');
  const faceM = M('robot-face');
  const metalM = M('d3-metal');

  const base = mesh(G.cyl(0.28, 0.28, 0.08, 20), baseM);
  base.position.y = 0.13;
  const wheels = [];
  for (const z of [0.26, -0.26]) {
    const w = mesh(G.cyl(0.09, 0.09, 0.05, 16), baseM);
    w.rotation.x = Math.PI / 2;
    w.position.set(0, 0.09, z);
    const hub = mesh(G.box(0.1, 0.02, 0.052), metalM, { cast: false });
    hub.position.copy(w.position);
    wheels.push({ w, hub });
    P.add(w, hub);
  }
  const body = mesh(roundedBox(0.46, 0.4, 0.48, 0.08), bodyM);
  body.position.y = 0.37;
  const neck = mesh(G.cyl(0.06, 0.07, 0.06, 12), metalM);
  neck.position.y = 0.6;
  P.add(base, body, neck);

  const head = new THREE.Group();
  head.position.y = 0.62;
  const skull = mesh(roundedBox(0.3, 0.26, 0.36, 0.07), bodyM);
  skull.position.y = 0.13;
  const face = mesh(roundedBox(0.02, 0.18, 0.28, 0.008), faceM, { cast: false });
  face.position.set(0.152, 0.13, 0);
  const eyeM = M('robot-eye-basic');
  const eyes = [];
  for (const z of [0.065, -0.065]) {
    const e = mesh(G.cap(0.022, 0.04), eyeM, { cast: false });
    e.position.set(0.165, 0.135, z);
    eyes.push(e);
    head.add(e);
  }
  const lidar = mesh(G.cyl(0.07, 0.075, 0.05, 16), metalM);
  lidar.position.y = 0.285;
  const lidarRing = mesh(G.torus(0.078, 0.008, 20), faceM, { cast: false });
  lidarRing.rotation.x = Math.PI / 2;
  lidarRing.position.y = 0.3;
  const mast = mesh(G.cyl(0.008, 0.008, 0.13, 6), metalM, { cast: false });
  mast.position.set(-0.08, 0.32, 0.1);
  const tip = mesh(G.sph(0.024, 10, 8), M('robot-tip'), { cast: false });
  tip.position.set(-0.08, 0.395, 0.1);
  head.add(skull, face, lidar, lidarRing, mast, tip);
  P.add(head);

  // Arm on the front right (+Z is the robot's right when facing +X).
  const arm = new THREE.Group();
  arm.position.set(0.2, 0.46, 0.2);
  const upper = mesh(G.box(0.18, 0.05, 0.05), metalM);
  upper.position.x = 0.09;
  const elbow = new THREE.Group();
  elbow.position.x = 0.18;
  const fore = mesh(G.box(1, 0.045, 0.045), bodyM);
  fore.scale.x = 0.16;
  fore.position.x = 0.08;
  const grip = new THREE.Group();
  grip.position.x = 0.16;
  const f1 = mesh(G.box(0.06, 0.02, 0.012), metalM, { cast: false });
  f1.position.set(0.03, 0, 0.022);
  const f2 = f1.clone();
  f2.position.z = -0.022;
  grip.add(f1, f2);
  const held = makeHeld(M, ghost ? null : outline);
  held.position.set(0.04, -0.01, 0);
  grip.add(held);
  elbow.add(fore, grip);
  arm.add(upper, elbow);
  arm.rotation.z = -35 * D2R;
  P.add(arm);

  let badge = null;
  if (!ghost) {
    badge = new THREE.Group();
    badge.position.set(-0.245, 0.4, 0);
    badge.rotation.y = -Math.PI / 2;
    const sh = shieldShape(0.16, 0.19);
    const plateG = geo('badge-plate', () => new THREE.ExtrudeGeometry(sh, { depth: 0.015, bevelEnabled: false }));
    const rimG = geo('badge-rim', () => new THREE.ExtrudeGeometry(shieldShape(0.19, 0.225), { depth: 0.01, bevelEnabled: false }));
    const plate = mesh(plateG, M('badge-plate'), { cast: false });
    plate.position.z = 0.006;
    const rim = mesh(rimG, M('badge-rim'), { cast: false });
    const horn = mesh(G.cyl(0, 0.025, 0.06, 10), M('badge-ink'), { cast: false });
    horn.position.set(0, 0.095 + 0.03, 0.012);
    const d1 = mesh(G.sph(0.012, 8, 6), M('badge-ink'), { cast: false });
    d1.position.set(-0.03, 0.02, 0.024);
    const d2 = d1.clone();
    d2.position.x = 0.03;
    badge.add(rim, plate, horn, d1, d2);
    P.add(badge);
  }

  return { root, yaw, tilt, head, eyes, tip, lidarRing, wheels, arm, elbow, fore, grip, held, badge, body };
}

// ───────────────────────── People ─────────────────────────

function buildPerson(M, cls) {
  const child = cls === 'child';
  const g = new THREE.Group();
  const inner = new THREE.Group();
  g.add(inner);
  const bodyTok = child ? 'child' : 'adult';
  const parts = [];
  if (child) {
    const body = mesh(G.cap(0.11, 0.26), M(bodyTok));
    body.position.y = 0.28;
    const head = mesh(G.sph(0.12, 16, 12), M('d3-skin'));
    head.position.y = 0.62;
    const hair = mesh(geo('hair', () => new THREE.SphereGeometry(0.125, 16, 8, 0, TAU, 0, Math.PI / 2)), M('d3-hair'));
    hair.position.y = 0.63;
    hair.rotation.x = -0.25;
    const armL = mesh(G.cap(0.035, 0.18), M(bodyTok));
    armL.position.set(0.1, 0.34, 0.12);
    armL.rotation.z = 0.9;
    const toy = makeToy(M, 0.9);
    toy.position.set(0.2, 0.2, 0.1);
    inner.add(body, head, hair, armL, toy);
    parts.push(body, head, hair, armL, ...toy.children);
  } else {
    const body = mesh(G.cap(0.15, 0.5), M(bodyTok));
    body.position.y = 0.45;
    const head = mesh(G.sph(0.13, 16, 12), M('d3-skin'));
    head.position.y = 1.05;
    const hair = mesh(geo('hairA', () => new THREE.SphereGeometry(0.135, 16, 8, 0, TAU, 0, Math.PI / 2.3)), M('d3-hair'));
    hair.position.y = 1.07;
    const aL = mesh(G.cap(0.045, 0.34), M(bodyTok));
    aL.position.set(0, 0.55, 0.2);
    const aR = aL.clone();
    aR.position.z = -0.2;
    inner.add(body, head, hair, aL, aR);
    parts.push(body, head, hair, aL, aR);
  }
  for (const p of parts) { p.layers.enable(1); p.userData.human = true; }
  return { g, inner, parts, child, height: child ? 0.8 : 1.25 };
}

// ───────────────────────── Actors ─────────────────────────

export class Actors {
  /**
   * @param {{scene: THREE.Scene, pal: import('./geom3d.js').Palette, tex: object,
   *          makeLabel: (text: string, cls: string, prio: number) => any}} ctx
   */
  constructor(ctx) {
    this.ctx = ctx;
    const { pal, scene } = ctx;
    this.pal = pal;
    this.group = new THREE.Group();
    scene.add(this.group);

    const outline = pal.mat('knife-outline', 'bul-fg', 'basic', { side: THREE.BackSide });
    this.robotMats = new Map();
    const RM = (t) => this.robotMat(t);
    this.robot = buildRobot(RM, { outline });
    this.group.add(this.robot.root);
    this.robotLabel = ctx.makeLabel('로봇', 'lbl-robot', 4);
    this.robotLabel.position.set(0, 1.12, 0);
    this.robot.root.add(this.robotLabel);
    this.speedLabel = ctx.makeLabel('', 'lbl-speed', 5);
    this.speedLabel.position.set(0, -0.05, 0);
    this.speedLabel.center.set(0.5, -0.4);
    this.robot.root.add(this.speedLabel);
    this.speedLabel.visible = false;

    // Glitch sparks (card 4).
    const sp = new Float32Array(12 * 3);
    this.sparkGeo = new THREE.BufferGeometry();
    this.sparkGeo.setAttribute('position', new THREE.BufferAttribute(sp, 3));
    this.sparks = new THREE.Points(this.sparkGeo, new THREE.PointsMaterial({ size: 0.05, color: pal.color('zone-slow'), toneMapped: false }));
    this.sparkMat = this.sparks.material;
    this.sparks.visible = false;
    this.robot.head.add(this.sparks);
    this.sparks.position.y = 0.3;

    // Mode ring (from sim.mode(); gate state, not a verdict): amber dashes / red ring.
    this.modeRing = new THREE.Group();
    const dashes = new THREE.Mesh(dashedRing(0.55, 0.05, 16, 0.6), pal.mat('mode-caution', 'zone-slow', 'basic', { side: THREE.DoubleSide }));
    const solid = new THREE.Mesh(geo('mode-solid', () => new THREE.RingGeometry(0.52, 0.58, 40).rotateX(-Math.PI / 2)), pal.mat('mode-hold', 'zone-deny', 'basic', { side: THREE.DoubleSide }));
    this.modeRing.add(dashes, solid);
    this.modeRing.userData = { dashes, solid };
    this.modeRing.position.y = FLOOR_Y + 0.02;
    this.group.add(this.modeRing);
    this.pauseBars = new THREE.Group();
    for (const z of [-0.035, 0.035]) {
      const b = new THREE.Mesh(G.box(0.03, 0.1, 0.03), pal.mat('mode-hold-bars', 'zone-deny', 'basic'));
      b.position.z = z;
      this.pauseBars.add(b);
    }
    this.pauseBars.position.y = 1.3;
    this.robot.root.add(this.pauseBars);

    // Ghost robot: one unlit --intent material with a stripe alpha map.
    this.ghostMat = pal.mat('ghost', 'intent', 'basic', {
      transparent: true, opacity: 0.55, depthWrite: false, alphaMap: ctx.tex.stripes, side: THREE.DoubleSide,
    });
    this.ghost = buildRobot(() => this.ghostMat, { ghost: true });
    this.ghost.root.traverse((o) => { o.castShadow = false; o.receiveShadow = false; });
    this.ghost.root.visible = false;
    this.group.add(this.ghost.root);
    this.ghostLabel = ctx.makeLabel('필터 없음 · 모델 명령 원본', 'lbl-ghost', 1);
    this.ghostLabel.position.set(0, 1.2, 0);
    this.ghost.root.add(this.ghostLabel);
    this.ghostSpeed = ctx.makeLabel('', 'lbl-ghost-speed', 5);
    this.ghostSpeed.center.set(0.5, -0.4);
    this.ghost.root.add(this.ghostSpeed);

    // Knife on the table (grasp point = handle end at (3.2, 3.0)).
    this.tableKnife = makeKnife((t) => this.decoMat(t, 'knife'), null);
    w2v(FURN3D.knife, FURN3D.table.h + FLOOR_Y + 0.012, this.tableKnife.position);
    this.group.add(this.tableKnife);

    this.buildDock();
    this.buildFakeTag();
    this.buildPeer();

    this.people = new Map(); // occurrence key -> person
    this.state = {
      bodyYaw: Math.PI * 0.6, headYaw: 0, lastPose: null, wheel: 0, lidar: 0,
      blinkAt: 0, ghostYaw: 0, lastPeople: '',
    };
  }

  robotMat(token) {
    let m = this.robotMats.get(token);
    if (m) return m;
    const pal = this.pal;
    const key = `robot:${token}`;
    switch (token) {
      case 'robot-base': m = pal.mat(key, 'robot-body', 'lambert'); pal.entry(key).mul = 0.8; pal.apply(pal.entry(key)); break;
      case 'robot-eye-basic': m = pal.mat(key, 'robot-eye', 'basic'); break;
      case 'robot-tip': m = pal.mat(key, 'd3-metal', 'basic'); break;
      case 'badge-plate': m = pal.mat(key, 'd3-badge', 'lambert', { emissive: 0x000000 }); break;
      case 'badge-rim': m = pal.mat(key, 'ink', 'basic'); break;
      case 'badge-ink': m = pal.mat(key, 'ink', 'basic'); break;
      case 'knife-blade': m = pal.mat(key, 'd3-metal', 'lambert', { emissive: 0x000000 }); break;
      case 'surface-double': m = pal.mat(key, 'surface', 'lambert', { side: THREE.DoubleSide }); break;
      default: m = pal.mat(key, token, 'lambert');
    }
    this.robotMats.set(token, m);
    return m;
  }

  /** Decor material (dimmable per group). */
  decoMat(token, group) {
    const key = `deco:${group}:${token}`;
    const pal = this.pal;
    if (pal.entry(key)) return pal.entry(key).mat;
    let m;
    if (token === 'knife-blade') m = pal.mat(key, 'd3-metal', 'lambert', { emissive: 0x000000 });
    else if (token === 'surface-double') m = pal.mat(key, 'surface', 'lambert', { side: THREE.DoubleSide });
    else m = pal.mat(key, token, 'lambert');
    pal.entry(key).group = group;
    return m;
  }

  buildDock() {
    const M = (t) => this.decoMat(t, 'dock');
    const g = new THREE.Group();
    const plate = mesh(roundedBox(0.7, 0.03, 0.5, 0.012), M('d3-metal'), { receive: true });
    plate.position.y = 0.015;
    const back = mesh(roundedBox(0.5, 0.45, 0.08, 0.02), M('d3-wall-cap'));
    back.position.set(0, 0.225, -0.3);
    const boltG = geo('bolt', () => new THREE.ExtrudeGeometry(boltShape(), { depth: 0.02, bevelEnabled: false }));
    const bolt = mesh(boltG, this.pal.mat('dock-bolt', 'robot-eye', 'basic'), { cast: false });
    bolt.scale.setScalar(0.28);
    bolt.position.set(0, 0.24, -0.255);
    g.add(plate, back, bolt);
    w2v(FURN3D.dock, FLOOR_Y, g.position);
    this.group.add(g);
    this.dock = g;
  }

  buildFakeTag() {
    const g = new THREE.Group(); // pivot on the sticker's top edge
    const top = 0.45 + 0.11;
    w2v(FURN3D.fakeTag, FLOOR_Y + top, g.position);
    g.position.z += 0.006;
    const sticker = new THREE.Mesh(geo('tag', () => new THREE.PlaneGeometry(0.22, 0.22)),
      new THREE.MeshLambertMaterial({ map: this.ctx.tex.qr, side: THREE.DoubleSide }));
    this.tagMat = sticker.material;
    sticker.position.y = -0.11;
    sticker.castShadow = true;
    g.add(sticker);
    const lbl = this.ctx.makeLabel("'충전소' 태그", 'lbl-tag', 2);
    lbl.position.set(0, 0.12, 0.02);
    g.add(lbl);
    this.tagLabel = lbl;
    g.visible = false;
    this.group.add(g);
    this.fakeTag = g;
  }

  buildPeer() {
    const pal = this.pal;
    const M = (t) => this.decoMat(t, 'peer');
    const g = new THREE.Group();
    const body = mesh(roundedBox(0.55, 0.4, 0.45, 0.06), M('d3-wall-cap'));
    body.position.y = 0.3;
    const lid = mesh(roundedBox(0.5, 0.05, 0.4, 0.02), M('d3-metal'));
    lid.position.y = 0.525;
    g.add(body, lid);
    for (const x of [-0.18, 0, 0.18]) {
      for (const z of [0.23, -0.23]) {
        const w = mesh(G.cyl(0.06, 0.06, 0.04, 12), M('robot-body'));
        w.rotation.x = Math.PI / 2;
        w.position.set(x, 0.06, z);
        g.add(w);
      }
    }
    const mast = mesh(G.cyl(0.01, 0.01, 0.22, 6), M('d3-metal'), { cast: false });
    mast.position.set(-0.18, 0.66, 0.12);
    const ball = mesh(G.sph(0.035, 10, 8), pal.mat('peer-ball', 'intent', 'basic'), { cast: false });
    ball.position.set(-0.18, 0.78, 0.12);
    g.add(mast, ball);
    // Violet-outlined warning triangle made of shapes (the untrusted source).
    const tri = new THREE.Shape();
    tri.moveTo(0, 0.14); tri.lineTo(0.13, -0.08); tri.lineTo(-0.13, -0.08); tri.lineTo(0, 0.14);
    const hole = new THREE.Path();
    hole.moveTo(0, 0.09); hole.lineTo(-0.085, -0.055); hole.lineTo(0.085, -0.055); hole.lineTo(0, 0.09);
    tri.holes.push(hole);
    const triM = mesh(geo('peer-tri', () => new THREE.ShapeGeometry(tri).rotateX(-Math.PI / 2)), pal.mat('peer-tri', 'intent', 'basic', { side: THREE.DoubleSide }), { cast: false });
    triM.position.y = 0.553;
    triM.rotation.y = -Math.PI / 2;
    const bar = mesh(G.box(0.015, 0.004, 0.06), pal.mat('peer-tri', 'intent', 'basic'), { cast: false });
    bar.position.set(0.01, 0.553, 0);
    const dot = mesh(G.box(0.015, 0.004, 0.015), pal.mat('peer-tri', 'intent', 'basic'), { cast: false });
    dot.position.set(-0.045, 0.553, 0);
    g.add(triM, bar, dot);
    w2v(FURN3D.peer, FLOOR_Y - 0.04, g.position);
    g.rotation.y = yawOf(0, 1);
    g.visible = false;
    this.group.add(g);
    this.peer = g;
    this.peerTip = ball;
    // Signal orbs.
    this.orbs = [];
    for (let i = 0; i < 5; i++) {
      const o = new THREE.Mesh(G.sph(0.045, 10, 8), pal.mat('orb', 'intent', 'basic', { transparent: true }));
      o.visible = false;
      this.group.add(o);
      this.orbs.push(o);
    }
  }

  // ── People ──

  syncPeople(humans) {
    const sig = humans.map((h) => `${h.id}:${h.class}`).join('|');
    if (sig === this.state.lastPeople) return;
    this.state.lastPeople = sig;
    const entries = humans.map((h, i) => ({ ...h, key: `${i}:${h.id}` }));
    const keep = new Set(entries.map((h) => h.key));
    for (const [id, p] of this.people) {
      if (!keep.has(id) || p.cls !== entries.find((h) => h.key === id)?.class) {
        this.group.remove(p.g);
        p.tag.element.remove();
        p.q.element.remove();
        this.people.delete(id);
      }
    }
    for (const h of entries) {
      if (this.people.has(h.key)) continue;
      const M = (t) => this.robotMat(t);
      const p = buildPerson(M, h.class);
      p.cls = h.class;
      p.id = h.id;
      p.tag = this.ctx.makeLabel(h.class === 'child' ? '아이' : '어른', `lbl-person lbl-${h.class === 'child' ? 'child' : 'adult'}`, 4);
      p.tag.position.set(0, p.height + 0.15, 0);
      p.g.add(p.tag);
      p.q = this.ctx.makeLabel('?', 'lbl-q', 3);
      p.q.position.set(0, p.height * 0.6, 0);
      p.q.visible = false;
      p.g.add(p.q);
      p.bubbles = [];
      for (let i = 0; i < 3; i++) {
        const b = new THREE.Mesh(G.sph(0.03 + i * 0.008, 8, 6), this.pal.mat('bubble', 'surface', 'basic', { transparent: true, opacity: 0.8 }));
        b.visible = false;
        p.g.add(b);
        p.bubbles.push(b);
      }
      p.phase = Math.random() * TAU;
      this.group.add(p.g);
      this.people.set(h.key, p);
    }
  }

  /** Human meshes (for raycasts / screen boxes). */
  personOf(id) { return [...this.people.values()].find((p) => p.id === id); }

  // ───────────────────────── Per-frame update ─────────────────────────

  /**
   * Set transforms from the scene. Returns { casters: bool } telling the
   * stage whether a shadow caster moved.
   */
  update(sc, clock, real, dt) {
    const st = this.state;
    const reduced = !!sc.reduced;
    const r = this.robot;
    let casters = false;
    const pose = robotPose(sc, clock, real);
    const holding = displayHolding(sc, clock);
    const e = sc.exec;

    // Position + wheel spin from actual travel.
    if (!st.lastPose || st.lastPose.x !== pose.x || st.lastPose.y !== pose.y) {
      const moved = st.lastPose ? dist(st.lastPose, pose) : 0;
      if (!reduced && moved < 1) st.wheel += moved / 0.09;
      st.lastPose = { x: pose.x, y: pose.y };
      casters = true;
    }
    w2v(pose, FLOOR_Y, r.root.position);
    for (const w of r.wheels) { w.w.rotation.y = -st.wheel; w.hub.rotation.z = -st.wheel; }

    // Body yaw follows executed motion only; the head looks at the intent target.
    let bodyTarget = st.bodyYaw;
    if (e && clock >= e.t0) {
      if (e.kind === 'move' && dist(e.from, e.to) > 1e-6) bodyTarget = yawOf(e.to.x - e.from.x, e.to.y - e.from.y);
      if (e.kind === 'reach') bodyTarget = yawOf(e.at.x - e.from.x, e.at.y - e.from.y);
    } else if (sc.bodyYaw != null) {
      bodyTarget = sc.bodyYaw;
    }
    const prevYaw = st.bodyYaw;
    st.bodyYaw = reduced ? bodyTarget : angleLerp(st.bodyYaw, bodyTarget, Math.min(1, dt / 70));
    if (Math.abs(prevYaw - st.bodyYaw) > 1e-4) casters = true;
    r.yaw.rotation.y = st.bodyYaw;

    let headTarget = 0;
    const it = sc.intent;
    const denied = sc.fx?.decision?.verdict === 'bul';
    if (it && !e && !denied && clock >= it.t0) {
      const want = yawOf(it.to.x - pose.x, it.to.y - pose.y);
      let d = ((want - st.bodyYaw + Math.PI) % TAU + TAU) % TAU - Math.PI;
      headTarget = Math.max(-45 * D2R, Math.min(45 * D2R, d));
    }
    st.headYaw = reduced ? headTarget : angleLerp(st.headYaw, headTarget, Math.min(1, dt / 120));

    // Flinch on a denial (reaction started by the director from a Decision).
    const react = sc.robot.reactT != null ? real - sc.robot.reactT : -1;
    let shake = 0;
    let pitch = 0;
    let eyeRed = false;
    if (react >= 0 && react < 900) {
      eyeRed = true;
      if (!reduced && react < 450) {
        const k = react / 450;
        pitch = Math.sin(k * Math.PI) * 7 * D2R;
        shake = Math.sin(k * Math.PI * 4) * 14 * D2R * (1 - k * 0.5);
        eyeRed = Math.floor(react / 110) % 2 === 0;
      }
    }
    // Reduced motion keeps the eye colour as a static state while the denial is shown.
    if (reduced && denied) eyeRed = true;
    r.tilt.rotation.z = pitch;
    r.head.rotation.y = st.headYaw + shake;
    this.pal.setToken('robot:robot-eye-basic', eyeRed ? 'bul-fill' : 'robot-eye');

    // Blink every ~4 s.
    let blink = 1;
    if (!reduced) {
      if (real > st.blinkAt + 4000) st.blinkAt = real + Math.random() * 1200;
      if (real > st.blinkAt && real < st.blinkAt + 120) blink = 0.1;
    }
    for (const eye of r.eyes) eye.scale.y = blink;

    // Lidar ring and antenna tip (violet when the command slip arrives: input, not a verdict).
    if (!reduced) st.lidar += dt * 0.002;
    r.lidarRing.rotation.z = st.lidar;
    const slip = sc.robot.slipT != null ? real - sc.robot.slipT : -1;
    this.pal.setToken('robot:robot-tip', slip >= 350 && slip < 1100 ? 'intent' : 'd3-metal');

    // Badge: neutral glow at judge(); the rim takes the verdict fill from scene.fx.
    const badgeGlow = sc.robot.badgeT != null && real - sc.robot.badgeT < 300;
    const plate = this.pal.entry('robot:badge-plate')?.mat;
    if (plate) plate.emissive.copy(badgeGlow ? this.pal.color('robot-eye') : this.pal.tmp.set(0)).multiplyScalar(badgeGlow ? 0.6 : 0);
    const v = sc.fx?.decision?.verdict;
    this.pal.setToken('robot:badge-rim', v ? `${v}-fill` : 'ink');

    // Arm reach from decision.action (grasp / place), holding.
    let ext = 0;
    let reachD = 0;
    if (e && e.kind === 'reach' && clock >= e.t0) {
      const p = prog(e, clock);
      ext = reduced ? 0 : (p < 0.5 ? p * 2 : (1 - p) * 2);
      reachD = dist(e.from, e.at);
    }
    this.pose3Arm(r, ext, reachD);
    showHeld(r.held, holding);

    // Glitch sparks after a fault.
    const gl = sc.robot.glitchT != null ? real - sc.robot.glitchT : -1;
    if (!reduced && gl >= 0 && gl < 300) {
      this.sparks.visible = true;
      const a = this.sparkGeo.attributes.position;
      for (let i = 0; i < 12; i++) {
        const ang = i * 0.52 + gl * 0.01;
        const rr = 0.08 + (gl / 300) * 0.2;
        a.setXYZ(i, Math.cos(ang) * rr, (i % 3) * 0.04 + (gl / 300) * 0.1, Math.sin(ang) * rr);
      }
      a.needsUpdate = true;
    } else {
      this.sparks.visible = false;
    }

    // Mode ring from sim.mode().
    const mode = sc.mode ?? 'normal';
    this.modeRing.visible = mode !== 'normal';
    this.modeRing.userData.dashes.visible = mode === 'caution';
    this.modeRing.userData.solid.visible = mode !== 'normal' && mode !== 'caution';
    this.modeRing.position.x = r.root.position.x;
    this.modeRing.position.z = r.root.position.z;
    if (!reduced && mode === 'caution') this.modeRing.rotation.y = real * 0.0003;
    this.pauseBars.visible = mode !== 'normal' && mode !== 'caution';
    this.pauseBars.position.y = 1.3 + (reduced ? 0 : Math.sin(real / 400) * 0.02);

    // Executed speed chip while moving (decision.action.speed).
    const moving = e && e.kind === 'move' && clock >= e.t0 && prog(e, clock) < 1;
    this.speedLabel.visible = !!moving;
    // Dimmed with the real robot during the comparison replay.
    if (moving) this.setLabel(this.speedLabel, `${fmt(e.speed)} m/s`, `lbl-speed v-${e.verdict}${(sc.realAlpha ?? 1) < 1 ? ' lbl-dim' : ''}`);

    // Real robot dims during the unfiltered comparison.
    this.setRobotAlpha(sc.realAlpha ?? 1);

    // Table knife: hidden while anyone holds it.
    const gh = sc.ghost ? ghostHolding(sc.ghost, clock) : null;
    this.tableKnife.visible = holding !== 'knife' && gh !== 'knife';
    const glint = (sc.hl ?? []).find((h) => h.kind === 'glint' && clock >= h.t0);
    const blade = this.pal.entry('robot:knife-blade')?.mat;
    if (blade) {
      let k = 0;
      if (glint) {
        const q = (clock - glint.t0) / 500;
        k = reduced ? 0.35 : (q < 2 ? Math.sin((q % 1) * Math.PI) : 0);
      }
      blade.emissive.setScalar(k * 0.9);
    }

    this.updateGhost(sc, clock, real, dt);
    casters = this.updatePeople(sc, real, reduced) || casters;
    casters = this.updateTag(sc, real, reduced) || casters;
    this.updatePeer(sc, clock, pose);
    return { casters };
  }

  pose3Arm(r, ext, reachD) {
    r.arm.rotation.z = (-35 + 38 * ext) * D2R;
    const need = Math.max(0, Math.min(1.1, reachD - 0.45));
    const len = 0.16 + need * ext;
    r.fore.scale.x = len;
    r.fore.position.x = len / 2;
    r.grip.position.x = len;
  }

  /** Text and base class only; lbl-hide / lbl-behind belong to the label pass. */
  setLabel(obj, text, cls) {
    const el = obj.element;
    if (el.textContent !== text) el.textContent = text;
    if (obj.userData.cls === cls) return;
    const keep = ['lbl-hide', 'lbl-behind'].filter((c) => el.classList.contains(c));
    el.className = `lbl ${cls}`;
    for (const c of keep) el.classList.add(c);
    obj.userData.cls = cls;
  }

  setRobotAlpha(a) {
    if (this.robotAlpha === a) return;
    this.robotAlpha = a;
    for (const [tok, m] of this.robotMats) {
      if (tok === 'd3-skin' || tok === 'd3-hair' || tok === 'child' || tok === 'adult' || tok === 'd3-wood' || tok === 'surface-double') continue;
      m.transparent = a < 1;
      m.opacity = a;
      m.depthWrite = a >= 1;
      m.needsUpdate = true;
    }
  }

  updateGhost(sc, clock, real, dt) {
    const g = sc.ghost;
    const gr = this.ghost;
    gr.root.visible = !!g;
    if (!g) { this.ghostSpeed.visible = false; return; }
    const pose = ghostPose(g, clock);
    w2v(pose, FLOOR_Y, gr.root.position);
    let yaw = this.state.bodyYaw;
    if (g.kind === 'move' && dist(g.from, g.to) > 1e-6) yaw = yawOf(g.to.x - g.from.x, g.to.y - g.from.y);
    if (g.kind === 'reach') yaw = yawOf(g.at.x - g.from.x, g.at.y - g.from.y);
    gr.yaw.rotation.y = yaw;
    let ext = 0;
    let reachD = 0;
    if (g.kind === 'reach' && clock >= g.t0) {
      const p = prog(g, clock);
      ext = sc.reduced ? 0 : (p < 0.5 ? p * 2 : (1 - p) * 2);
      reachD = dist(g.from, g.at);
    }
    this.pose3Arm(gr, ext, reachD);
    showHeld(gr.held, ghostHolding(g, clock));
    const moving = g.kind === 'move' && clock >= g.t0 && prog(g, clock) < 1;
    this.ghostSpeed.visible = moving;
    if (moving) this.setLabel(this.ghostSpeed, `${fmt(g.speed)} m/s`, 'lbl-speed lbl-intent');
    // Offset a little when the ghost stands exactly on the real robot, so both read.
    if (!moving && g.kind === 'none') gr.root.position.x += 0.02;
  }

  updatePeople(sc, real, reduced) {
    const humans = sc.world?.humans ?? [];
    this.syncPeople(humans);
    let casters = false;
    const unknown = !!sc.decor?.childUnknown;
    for (const [i, h] of humans.entries()) {
      const p = this.people.get(`${i}:${h.id}`);
      if (!p) continue;
      const key = `${h.pos.x},${h.pos.y}`;
      if (p.lastKey !== key) { p.lastKey = key; casters = true; }
      w2v(h.pos, FLOOR_Y, p.g.position);
      const m = FURN3D.mat;
      const sleeping = p.child && h.pos.x >= m.x0 && h.pos.x <= m.x1 && h.pos.y >= m.y0 && h.pos.y <= m.y1;
      p.inner.rotation.set(0, 0, 0);
      p.inner.position.set(0, 0, 0);
      if (sleeping) {
        p.inner.rotation.z = Math.PI / 2;
        p.inner.position.set(0.35, 0.12, 0);
        p.tag.position.y = 0.55;
      } else {
        p.tag.position.y = p.height + 0.15;
        if (!reduced) {
          if (p.child) p.inner.position.y = Math.sin(real / 318 + p.phase) * 0.03;
          else p.inner.rotation.z = Math.sin(real / 637 + p.phase) * 2 * D2R;
        }
      }
      for (let i = 0; i < p.bubbles.length; i++) {
        const b = p.bubbles[i];
        b.visible = sleeping && !reduced;
        if (b.visible) {
          const k = ((real / 2400 + i / 3) % 1);
          b.position.set(0.1 + k * 0.1, 0.35 + k * 0.5, 0);
          b.material.opacity = 0.8 * (1 - k);
        }
      }
      const unk = unknown && p.child;
      if (p.unknown !== unk) {
        p.unknown = unk;
        const wire = this.pal.mat('unknown-wire', 'ink-2', 'basic', { wireframe: true, transparent: true, opacity: 0.5 });
        for (const part of p.parts) {
          part.userData.mat ??= part.material;
          part.material = unk ? wire : part.userData.mat;
          part.castShadow = !unk;
        }
        p.q.visible = unk;
        casters = true;
      }
    }
    return casters;
  }

  updateTag(sc, real, reduced) {
    const d = sc.decor ?? {};
    const t = this.fakeTag;
    const was = t.visible;
    const focus = sc.focus ?? [];
    this.tagLabel.visible = focus.includes('fakeTag');
    if (d.fakeTag && !d.peelT) {
      t.visible = true;
      t.rotation.x = 0;
      t.position.y = FLOOR_Y + 0.56;
      this.tagMat.opacity = 1;
      return !was;
    }
    if (d.peelT) {
      const k = reduced ? 1 : clamp01((real - d.peelT) / 400);
      t.visible = k < 1;
      t.rotation.x = k * 100 * D2R;
      t.position.y = FLOOR_Y + 0.56 - k * 0.35;
      this.tagLabel.visible = false;
      return true;
    }
    t.visible = false;
    return was;
  }

  updatePeer(sc, clock, pose) {
    const on = !!sc.peer;
    this.peer.visible = on;
    const sig = sc.signal;
    for (const o of this.orbs) o.visible = false;
    if (!on || !sig || clock < sig.t0 || sc.reduced) return;
    // Zig-zag polyline: peer antenna -> over the south wall -> our antenna.
    const a = this.peerTip.getWorldPosition(this.tmpA ??= new THREE.Vector3());
    const b = w2v(pose, FLOOR_Y + 1.0, this.tmpB ??= new THREE.Vector3());
    const mid = this.tmpC ??= new THREE.Vector3();
    const k = (clock - sig.t0) / 450;
    const bounce = sig.bounceT != null && clock >= sig.bounceT ? (clock - sig.bounceT) / 450 : -1;
    for (let i = 0; i < this.orbs.length; i++) {
      let t = k - i * 0.12;
      let back = false;
      if (bounce >= 0) { t = 1 - Math.min(1, bounce - i * 0.08) * 0.7; back = true; }
      if (t < 0 || (t > 1 && !back) || (back && bounce - i * 0.08 > 1.2)) continue;
      t = Math.min(1, Math.max(0, t));
      const o = this.orbs[i];
      o.visible = true;
      mid.lerpVectors(a, b, t);
      mid.y += Math.sin(t * Math.PI) * 1.0 + Math.sin(t * Math.PI * 6) * 0.08;
      o.position.copy(mid);
      o.material.opacity = back ? 0.6 : 1;
    }
  }

  /** Screen-space helpers use these world heights. */
  static get ROBOT_H() { return 0.95; }

  dispose() {
    this.sparkGeo.dispose();
    this.sparkMat.dispose();
    this.tagMat.dispose();
    for (const p of this.people.values()) { p.tag.element.remove(); p.q.element.remove(); }
    this.people.clear();
  }
}

function fmt(v) {
  return typeof v === 'number' && Number.isFinite(v) ? String(Math.round(v * 100) / 100) : String(v);
}
