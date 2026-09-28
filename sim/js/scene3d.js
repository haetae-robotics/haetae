// Stage3D: the miniature house diorama. A roofless toy house on a plinth,
// seen from above at an angle; the viewer can rotate and zoom it.
//
// Like the 2D stage, this only DRAWS the scene object the director built. It
// never imports engine.js and never calls judge(). Decision data reaches it
// only through scene.fx (a reference to the frozen Decision), scene.exec and
// scene.hl, which the director copied from that Decision. No text is drawn in
// WebGL: every word is DOM (CSS2D labels or the overlay).

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/OrbitControls.js';
import { CSS2DRenderer, CSS2DObject } from 'three/addons/CSS2DRenderer.js';
import { FURN3D, ROOMS, robotPose, ghostPose, segSegDist, easeInOut, clamp01 } from './model.js';
import { Actors } from './actors3d.js';
import { Fx } from './fx3d.js';
import {
  FLOOR_Y, w2v, v2w, yawOf, geo, roundedBox, roundedRectShape, TEX, Palette, disposeGeoCache,
} from './geom3d.js';

const D2R = Math.PI / 180;
const DEFAULT = { az: 28 * D2R, polar: 50 * D2R, target: { x: 5, y: 4.65 }, ty: 0.3 };
const TOKENS = [
  'bg', 'surface', 'surface-2', 'ink', 'ink-2', 'line', 'focus', 'intent',
  'zone-slow', 'zone-deny', 'robot-body', 'robot-face', 'robot-eye', 'child', 'adult',
  'yun-fill', 'jeol-fill', 'bul-fill', 'yun-fg', 'jeol-fg', 'bul-fg',
  'd3-yun', 'd3-jeol', 'd3-bul',
  'd3-bg-top', 'd3-bg-bottom', 'd3-plinth', 'd3-plinth-side', 'd3-path',
  'd3-floor-kitchen', 'd3-floor-hall', 'd3-floor-living', 'd3-floor-child',
  'd3-wall', 'd3-wall-cap', 'd3-wood', 'd3-wood-dark', 'd3-fabric', 'd3-fabric-2', 'd3-bed',
  'd3-metal', 'd3-skin', 'd3-hair', 'd3-leaf', 'd3-badge', 'd3-hemi-sky', 'd3-hemi-ground',
  'd3-sun', 'd3-hemi-i', 'd3-sun-i', 'd3-shadow',
];
const DRAG_THRESHOLD = 6;

/** Walls: fixed art. Outer segments cut away on the camera side. */
const WALLS = [
  { a: { x: -0.06, y: -0.06 }, b: { x: 4.3, y: -0.06 }, h: 1.1, n: { x: 0, y: -1 } },
  { a: { x: 5.7, y: -0.06 }, b: { x: 10.06, y: -0.06 }, h: 1.1, n: { x: 0, y: -1 } },
  { a: { x: -0.06, y: -0.06 }, b: { x: -0.06, y: 10.06 }, h: 1.1, n: { x: -1, y: 0 } },
  { a: { x: -0.06, y: 10.06 }, b: { x: 10.06, y: 10.06 }, h: 1.1, n: { x: 0, y: 1 } },
  { a: { x: 10.06, y: -0.06 }, b: { x: 10.06, y: 2.0 }, h: 1.1, n: { x: 1, y: 0 } },
  { a: { x: 10.06, y: 2.0 }, b: { x: 10.06, y: 3.2 }, h: 0.45, n: { x: 1, y: 0 }, sill: true },
  { a: { x: 10.06, y: 3.2 }, b: { x: 10.06, y: 4.4 }, h: 1.1, n: { x: 1, y: 0 } },
  { a: { x: 10.06, y: 4.4 }, b: { x: 10.06, y: 5.6 }, h: 0.45, n: { x: 1, y: 0 }, sill: true },
  { a: { x: 10.06, y: 5.6 }, b: { x: 10.06, y: 10.06 }, h: 1.1, n: { x: 1, y: 0 } },
  { a: { x: 4, y: 7.2 }, b: { x: 4, y: 10 }, h: 0.55 },
  { a: { x: 7, y: 7 }, b: { x: 7, y: 10 }, h: 0.55, group: 'child-room' },
  { a: { x: 7, y: 7 }, b: { x: 7.5, y: 7 }, h: 0.55, group: 'child-room' },
  { a: { x: 8.5, y: 7 }, b: { x: 10, y: 7 }, h: 0.55, group: 'child-room' },
];
const SILL_H = 0.18;

export class Stage3D {
  /**
   * @param {HTMLCanvasElement} canvas #map
   * @param {HTMLElement} wrap #map-wrap
   * @param {{kiosk?: boolean, onContextLost?: Function, hint?: (key: string, text: string) => void}} opts
   */
  constructor(canvas, wrap, opts = {}) {
    this.kind = '3d';
    this.canvas = canvas;
    this.wrap = wrap;
    this.opts = opts;
    this.cssW = 0;
    this.cssH = 0;
    this.onResize = null;
    this.onView = null;
    this.userOwnsCamera = false;
    this.labMode = false;
    this.reduced = false;
    this.dirty = true;
    this.visible = true;
    this.lastRender = 0;
    this.listeners = [];
    this.labels = [];
    this.tween = null;
    this.frameTimes = [];
    this.degrade = 0;
    this.coarse = matchMedia('(pointer: coarse)').matches;
    this.bounds = { minX: -0.4, minY: -0.4, maxX: 10.4, maxY: 10.4 };

    const dpr = window.devicePixelRatio || 1;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: dpr < 2, alpha: true, powerPreference: 'default' });
    const r = this.renderer;
    r.outputColorSpace = THREE.SRGBColorSpace;
    r.toneMapping = THREE.NoToneMapping;
    r.setClearColor(0x000000, 0);
    r.shadowMap.enabled = true;
    r.shadowMap.type = THREE.PCFShadowMap;
    r.shadowMap.autoUpdate = false;
    r.shadowMap.needsUpdate = true;
    this.pixelRatio();

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(32, 1, 0.5, 120);

    // Label layer (CSS2D), above the canvas and below the overlay.
    this.labelLayer = document.createElement('div');
    this.labelLayer.className = 'label-layer';
    this.labelLayer.setAttribute('aria-hidden', 'true');
    canvas.after(this.labelLayer);
    this.css2d = new CSS2DRenderer({ element: this.labelLayer });

    this.pal = new Palette();
    this.pal.read(TOKENS);
    this.tex = {};
    for (const [k, make] of Object.entries(TEX)) this.tex[k] = make();
    this.tex.bandFade = this.tex.glow;
    this.tex.tile.repeat.set(1, 1);

    const makeLabel = (text, cls, prio) => this.makeLabel(text, cls, prio);
    this.buildLights();
    this.buildHouse();
    this.actors = new Actors({ scene: this.scene, pal: this.pal, tex: this.tex, makeLabel });
    this.fx = new Fx({ scene: this.scene, pal: this.pal, tex: this.tex, makeLabel, camera: this.camera });
    this.occluders = [];
    this.scene.traverse((o) => { if (o.isMesh && o.userData.occluder) this.occluders.push(o); });

    // Controls.
    const c = (this.controls = new OrbitControls(this.camera, canvas));
    c.enableDamping = true;
    c.dampingFactor = 0.12;
    c.rotateSpeed = 0.8;
    c.minPolarAngle = 8 * D2R;
    c.maxPolarAngle = 70 * D2R;
    c.minAzimuthAngle = -110 * D2R;
    c.maxAzimuthAngle = 110 * D2R;
    c.enablePan = true;
    c.screenSpacePanning = false;
    c.cursor.set(0, 0, 0);
    c.maxTargetRadius = 4;
    c.touches = { ONE: null, TWO: THREE.TOUCH.DOLLY_ROTATE };
    // The viewer owns the camera only once the view really changes: a plain
    // click (OrbitControls sends 'start' on every pointerdown) keeps scene-following.
    this.startSig = new Float64Array(6);
    c.addEventListener('start', () => {
      this.pendingTake = true;
      this.readCamSig(this.startSig);
    });
    c.addEventListener('change', () => {
      if (!this.pendingTake || this.selfMove) return;
      const s = this.readCamSig(this.tmpSig);
      for (let i = 0; i < 6; i++) {
        if (Math.abs(s[i] - this.startSig[i]) > 1e-3) { this.pendingTake = false; this.takeCamera(); return; }
      }
    });
    c.addEventListener('end', () => { this.pendingTake = false; });
    this.tmpSig = new Float64Array(6);
    this.setLabMode(false);

    this.raycaster = new THREE.Raycaster();
    this.floorPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), -FLOOR_Y);
    this.tmpV = new THREE.Vector3();
    this.tmpV2 = new THREE.Vector3();
    this.tmpN = new THREE.Vector2();
    this.camSig = new Float64Array(6);

    // A plain wheel scrolls the page; Ctrl/Cmd + wheel (and trackpad pinch) zooms.
    this.on(wrap, 'wheel', (e) => {
      if (e.target !== canvas) return;
      if (!e.ctrlKey && !e.metaKey) {
        e.stopPropagation();
        this.opts.hint?.('wheel', 'Ctrl(⌘) + 스크롤로 확대할 수 있어요.');
      }
    }, { capture: true, passive: true });
    this.on(canvas, 'pointerdown', (e) => {
      if (e.pointerType === 'touch' && !this.labMode) this.opts.hint?.('touch', '두 손가락으로 돌리고 확대할 수 있어요.');
    });
    this.on(canvas, 'webglcontextlost', (e) => {
      e.preventDefault();
      this.lost = true;
      this.opts.onContextLost?.();
    });

    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(wrap);
    this.io = new IntersectionObserver((ents) => {
      for (const en of ents) this.visible = en.isIntersecting;
      if (this.visible) this.dirty = true;
    });
    this.io.observe(wrap);
    this.on(window, 'resize', () => this.resize(true));
    this.bindToolbar();
    this.resize(true);
  }

  on(target, type, fn, opts) {
    target.addEventListener(type, fn, opts);
    this.listeners.push([target, type, fn, opts]);
  }

  pixelRatio() {
    const dpr = window.devicePixelRatio || 1;
    const cap = this.degrade >= 2 ? 1 : (this.coarse || window.innerWidth < 720 ? 1.5 : 2);
    this.renderer.setPixelRatio(Math.min(dpr, cap));
  }

  makeLabel(text, cls, prio) {
    const el = document.createElement('div');
    el.className = `lbl ${cls}`;
    el.textContent = text;
    const o = new CSS2DObject(el);
    o.userData.prio = prio;
    this.labels.push(o);
    return o;
  }

  // ───────────────────────── Build ─────────────────────────

  buildLights() {
    const pal = this.pal;
    this.hemi = new THREE.HemisphereLight(0xffffff, 0x888888, 1);
    this.sun = new THREE.DirectionalLight(0xffffff, 1);
    this.sun.position.set(-7, 14, -5);
    this.sun.target.position.set(0, 0, 0);
    this.sun.castShadow = true;
    const s = this.sun.shadow;
    const size = this.coarse ? 512 : 1024;
    s.mapSize.set(size, size);
    const cam = s.camera;
    cam.left = -8; cam.right = 8; cam.top = 8; cam.bottom = -8; cam.near = 2; cam.far = 40;
    s.bias = -0.0004;
    s.normalBias = 0.02;
    s.radius = 3;
    this.scene.add(this.hemi, this.sun, this.sun.target);
    this.applyLights(pal);
  }

  applyLights(pal) {
    this.hemi.color.copy(pal.color('d3-hemi-sky'));
    this.hemi.groundColor.copy(pal.color('d3-hemi-ground'));
    this.hemi.intensity = pal.num('d3-hemi-i', 1.4);
    this.sun.color.copy(pal.color('d3-sun'));
    this.sun.intensity = pal.num('d3-sun-i', 1.2);
    if (this.contact) this.contact.material.opacity = pal.num('d3-shadow', 0.25);
    const dark = getComputedStyle(document.documentElement).colorScheme.includes('dark');
    if (this.lampShade) this.lampShade.material.emissive.set(dark ? 0xffc27a : 0x000000).multiplyScalar(dark ? 0.7 : 0);
  }

  /** Static mesh helper: placed once, matrix frozen. */
  put(g, m, { x, y, h = 0 }, { cast = true, receive = true, occ = false, rot = 0 } = {}) {
    const o = new THREE.Mesh(g, m);
    w2v({ x, y }, h, o.position);
    o.rotation.y = rot;
    o.castShadow = cast;
    o.receiveShadow = receive;
    o.userData.occluder = occ;
    o.updateMatrix();
    o.matrixAutoUpdate = false;
    this.house.add(o);
    return o;
  }

  hm(token, group) {
    const key = group ? `deco:${group}:${token}` : `house:${token}`;
    const e = this.pal.entry(key);
    if (e) return e.mat;
    const m = this.pal.mat(key, token, 'lambert');
    if (group) this.pal.entry(key).group = group;
    return m;
  }

  buildHouse() {
    const pal = this.pal;
    this.house = new THREE.Group();
    this.scene.add(this.house);

    // Plinth: a rounded slab, lawn top, wooden sides.
    const shape = roundedRectShape(11.8, 12.5, 0.5);
    const pg = new THREE.ExtrudeGeometry(shape, { depth: 0.38, bevelEnabled: true, bevelThickness: 0.06, bevelSize: 0.06, bevelSegments: 2, curveSegments: 6 });
    pg.rotateX(-Math.PI / 2);
    this.plinthSide = this.hm('d3-plinth-side');
    this.hazardSide = pal.mat('hazard-side', 'intent', 'lambert', { map: this.tex.stripes });
    const plinth = new THREE.Mesh(pg, [this.hm('d3-plinth'), this.plinthSide]);
    plinth.position.set(0, -0.44, 0.35);
    plinth.receiveShadow = true;
    this.house.add(plinth);
    this.plinth = plinth;
    // Contact shadow so the diorama floats on the page.
    const cm = new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.25, alphaMap: this.tex.glow, depthWrite: false });
    this.contact = new THREE.Mesh(geo('contact', () => new THREE.PlaneGeometry(17, 17).rotateX(-Math.PI / 2)), cm);
    this.contact.position.set(0, -0.62, 0.5);
    this.contact.renderOrder = -1;
    this.house.add(this.contact);
    // Sidewalk and shrubs.
    this.put(new THREE.BoxGeometry(11.4, 0.02, 1.0), this.hm('d3-path'), { x: 5, y: -0.9, h: 0.01 }, { cast: false });
    const leaf = this.hm('d3-leaf');
    const shrub = geo('shrub', () => new THREE.SphereGeometry(0.3, 16, 12).scale(1, 0.8, 1));
    for (const p of [{ x: -0.5, y: 10.45 }, { x: 10.5, y: 10.45 }, { x: -0.5, y: -0.25 }, { x: 10.5, y: -0.25 }, { x: 3.9, y: -0.3 }, { x: 6.1, y: -0.3 }]) {
      this.put(shrub, leaf, { ...p, h: 0.2 }, { receive: false });
    }

    // House floor: rooms as slabs (top at FLOOR_Y), grooves at x = 4 and 6.
    const floorMat = {
      kitchen: pal.mat('floor-kitchen', 'd3-floor-kitchen', 'lambert', { map: this.tex.tile }),
      hall: pal.mat('floor-hall', 'd3-floor-hall', 'lambert', { map: this.tex.planks }),
      living: this.hm('d3-floor-living'),
      child: this.hm('d3-floor-child'),
    };
    this.tex.tile.repeat.set(8, 20);
    this.tex.planks.repeat.set(1, 5);
    for (const rm of ROOMS) {
      const w = rm.x1 - rm.x0;
      const d = rm.y1 - rm.y0;
      this.put(new THREE.BoxGeometry(w, FLOOR_Y, d), floorMat[rm.floor], { x: (rm.x0 + rm.x1) / 2, y: (rm.y0 + rm.y1) / 2, h: FLOOR_Y / 2 }, { cast: false });
    }
    const groove = pal.mat('groove', 'line', 'basic');
    for (const x of [4, 6]) this.put(geo('groove', () => new THREE.PlaneGeometry(0.02, 10).rotateX(-Math.PI / 2)), groove, { x, y: 5, h: FLOOR_Y + 0.001 }, { cast: false, receive: false });
    // Rug.
    this.put(new THREE.CylinderGeometry(FURN3D.rug.r, FURN3D.rug.r, 0.012, 48), this.hm('d3-fabric-2', 'sofa'), { x: FURN3D.rug.x, y: FURN3D.rug.y, h: FLOOR_Y + 0.006 }, { cast: false });

    // Walls.
    this.walls = [];
    const wallGeo = geo('wall-unit', () => new THREE.BoxGeometry(1, 1, 0.12).translate(0, 0.5, 0));
    const capGeo = geo('wall-cap', () => new THREE.BoxGeometry(1, 0.03, 0.14));
    this.wallMat = this.hm('d3-wall');
    this.capMat = this.hm('d3-wall-cap');
    this.wallFade = pal.mat('wall-fade', 'd3-wall', 'lambert', { transparent: true, opacity: 0.25, depthWrite: false });
    this.capFade = pal.mat('cap-fade', 'd3-wall-cap', 'lambert', { transparent: true, opacity: 0.25, depthWrite: false });
    for (const w of WALLS) {
      const len = Math.hypot(w.b.x - w.a.x, w.b.y - w.a.y);
      const mid = { x: (w.a.x + w.b.x) / 2, y: (w.a.y + w.b.y) / 2 };
      const rot = yawOf(w.b.x - w.a.x, w.b.y - w.a.y);
      const wall = new THREE.Mesh(wallGeo, this.wallMat);
      w2v(mid, FLOOR_Y, wall.position);
      wall.rotation.y = rot;
      wall.scale.set(len, w.h, 1);
      wall.castShadow = true;
      wall.receiveShadow = true;
      wall.userData.occluder = true;
      const cap = new THREE.Mesh(capGeo, this.capMat);
      cap.rotation.y = rot;
      cap.scale.x = len + 0.02;
      cap.castShadow = false;
      w2v(mid, FLOOR_Y + w.h, cap.position);
      this.house.add(wall, cap);
      this.walls.push({ ...w, wall, cap, cur: w.h, target: w.h, faded: false, outer: !!w.n });
    }
    // Child-room door, swung 75 degrees into the room.
    const door = new THREE.Group();
    w2v({ x: 8.5, y: 7 }, FLOOR_Y, door.position);
    door.rotation.y = yawOf(Math.cos(105 * D2R), Math.sin(105 * D2R));
    const leafM = new THREE.Mesh(roundedBox(0.9, 0.55, 0.05, 0.015), this.hm('d3-wood', 'child-room'));
    leafM.position.set(0.45, 0.275, 0);
    leafM.castShadow = true;
    door.add(leafM);
    this.house.add(door);

    this.buildFurniture();

    // Room labels (low priority).
    for (const rm of ROOMS) {
      if (!rm.name) continue;
      const l = this.makeLabel(rm.name, 'lbl-room', 7);
      w2v({ x: rm.x0 + 0.25, y: rm.y1 - 0.3 }, 0.05, l.position);
      l.center.set(0, 0.5);
      this.house.add(l);
    }
  }

  buildFurniture() {
    const F = FURN3D;
    const M = (t, g) => this.hm(t, g);
    // Sink counter.
    const sw = F.sink.x1 - F.sink.x0;
    const sd = F.sink.y1 - F.sink.y0;
    const sc = { x: (F.sink.x0 + F.sink.x1) / 2, y: (F.sink.y0 + F.sink.y1) / 2 };
    this.put(roundedBox(sw, F.sink.h, sd, 0.04), M('d3-wall-cap', 'sink'), { ...sc, h: FLOOR_Y + F.sink.h / 2 }, { occ: true });
    this.put(new THREE.BoxGeometry(0.5, 0.012, 0.26), M('d3-metal', 'sink'), { ...sc, h: FLOOR_Y + F.sink.h + 0.002 }, { cast: false });
    this.put(new THREE.CylinderGeometry(0.02, 0.02, 0.16, 8), M('d3-metal', 'sink'), { x: sc.x, y: F.sink.y0 + 0.07, h: FLOOR_Y + F.sink.h + 0.08 }, { cast: false });
    this.put(new THREE.BoxGeometry(0.03, 0.03, 0.14), M('d3-metal', 'sink'), { x: sc.x, y: F.sink.y0 + 0.13, h: FLOOR_Y + F.sink.h + 0.16 }, { cast: false });

    // Dining table (narrowed so the robot at (3, 3) clears it).
    const t = F.table;
    const tc = { x: (t.x0 + t.x1) / 2, y: (t.y0 + t.y1) / 2 };
    this.put(roundedBox(t.x1 - t.x0, 0.05, t.y1 - t.y0, 0.02), M('d3-wood', 'table'), { ...tc, h: FLOOR_Y + t.h - 0.025 }, { occ: true });
    const leg = geo('leg', () => new THREE.CylinderGeometry(0.025, 0.02, t.h - 0.05, 10));
    for (const [x, y] of [[t.x0 + 0.06, t.y0 + 0.08], [t.x1 - 0.06, t.y0 + 0.08], [t.x0 + 0.06, t.y1 - 0.08], [t.x1 - 0.06, t.y1 - 0.08]]) {
      this.put(leg, M('d3-wood-dark', 'table'), { x, y, h: FLOOR_Y + (t.h - 0.05) / 2 }, { cast: false });
    }
    // Chair facing south (back rest on the north side).
    const ch = F.chair;
    this.put(roundedBox(ch.w, 0.04, ch.d, 0.015), M('d3-wood-dark', 'table'), { x: ch.x, y: ch.y, h: FLOOR_Y + 0.25 });
    this.put(roundedBox(ch.w, 0.26, 0.04, 0.015), M('d3-wood-dark', 'table'), { x: ch.x, y: ch.y + ch.d / 2 - 0.02, h: FLOOR_Y + 0.25 + 0.15 });
    const cleg = geo('cleg', () => new THREE.CylinderGeometry(0.015, 0.015, 0.25, 6));
    for (const [dx, dy] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) {
      this.put(cleg, M('d3-wood-dark', 'table'), { x: ch.x + dx * 0.16, y: ch.y + dy * 0.16, h: FLOOR_Y + 0.125 }, { cast: false });
    }
    // Cup on the table.
    const cupM = this.pal.mat('deco:table:cup', 'surface', 'lambert', { side: THREE.DoubleSide });
    this.pal.entry('deco:table:cup').group = 'table';
    this.put(geo('cup', () => new THREE.CylinderGeometry(0.05, 0.045, 0.09, 16, 1, true)), cupM, { ...F.cup, h: FLOOR_Y + t.h + 0.045 }, { cast: false });

    // Sofa with the back rest on the south side.
    const so = F.sofa;
    const sow = so.x1 - so.x0;
    const sod = so.y1 - so.y0;
    const soc = { x: (so.x0 + so.x1) / 2, y: (so.y0 + so.y1) / 2 };
    this.put(roundedBox(sow, 0.22, sod, 0.06), M('d3-fabric', 'sofa'), { ...soc, h: FLOOR_Y + 0.11 }, { occ: true });
    this.put(roundedBox(sow, 0.25, 0.16, 0.06), M('d3-fabric', 'sofa'), { x: soc.x, y: so.y0 + 0.08, h: FLOOR_Y + 0.22 + 0.11 }, { occ: true });
    for (const x of [so.x0 + 0.08, so.x1 - 0.08]) this.put(roundedBox(0.16, 0.14, sod, 0.05), M('d3-fabric', 'sofa'), { x, y: soc.y, h: FLOOR_Y + 0.29 });
    // Coffee table.
    const co = F.coffee;
    this.put(roundedBox(co.w, 0.04, co.d, 0.015), M('d3-wood', 'sofa'), { x: co.x, y: co.y, h: FLOOR_Y + co.h - 0.02 });
    this.put(new THREE.BoxGeometry(co.w - 0.1, co.h - 0.04, co.d - 0.1), M('d3-wood-dark', 'sofa'), { x: co.x, y: co.y, h: FLOOR_Y + (co.h - 0.04) / 2 });
    // Floor lamp (the shade glows in the dark theme; it is not a light).
    const la = F.lamp;
    this.put(new THREE.CylinderGeometry(0.14, 0.16, 0.03, 16), M('d3-metal', 'lamp'), { ...la, h: FLOOR_Y + 0.015 });
    this.put(new THREE.CylinderGeometry(0.012, 0.012, 0.85, 6), M('d3-metal', 'lamp'), { ...la, h: FLOOR_Y + 0.44 });
    const shadeM = new THREE.MeshLambertMaterial({ color: 0xf6e7c8, emissive: 0x000000, side: THREE.DoubleSide });
    this.lampShade = this.put(new THREE.CylinderGeometry(0.1, 0.2, 0.2, 18, 1, true), shadeM, { ...la, h: FLOOR_Y + 0.9 });

    // Child room: bed, nap mat, toy box.
    const bd = F.bed;
    const bw = bd.x1 - bd.x0;
    const bdd = bd.y1 - bd.y0;
    const bc = { x: (bd.x0 + bd.x1) / 2, y: (bd.y0 + bd.y1) / 2 };
    this.put(roundedBox(bw, 0.15, bdd, 0.03), M('d3-wood', 'child-room'), { ...bc, h: FLOOR_Y + 0.075 }, { occ: true });
    this.put(roundedBox(bw - 0.06, 0.1, bdd - 0.3, 0.04), M('d3-bed', 'child-room'), { x: bc.x, y: bc.y - 0.12, h: FLOOR_Y + 0.2 });
    this.put(roundedBox(bw - 0.2, 0.08, 0.25, 0.04), M('surface', 'child-room'), { x: bc.x, y: bd.y1 - 0.2, h: FLOOR_Y + 0.21 });
    const teddy = new THREE.Group();
    const tb = new THREE.Mesh(geo('sph:0.07', () => new THREE.SphereGeometry(0.07, 12, 8)), M('d3-wood', 'child-room'));
    const th = new THREE.Mesh(geo('sph:0.05', () => new THREE.SphereGeometry(0.05, 12, 8)), M('d3-wood', 'child-room'));
    th.position.y = 0.1;
    teddy.add(tb, th);
    w2v({ x: bc.x - 0.2, y: bd.y1 - 0.45 }, FLOOR_Y + 0.32, teddy.position);
    tb.castShadow = th.castShadow = false; // small props cast no shadow (draw-call budget)
    this.house.add(teddy);
    const mt = F.mat;
    this.put(roundedBox(mt.x1 - mt.x0, 0.05, mt.y1 - mt.y0, 0.02), M('d3-fabric-2', 'child-room'), { x: (mt.x0 + mt.x1) / 2, y: (mt.y0 + mt.y1) / 2, h: FLOOR_Y + 0.025 }, { cast: false });
    const tbx = F.toybox;
    this.put(roundedBox(0.45, 0.28, 0.35, 0.03), M('d3-wood-dark', 'child-room'), { ...tbx, h: FLOOR_Y + 0.14 });
    this.put(roundedBox(0.48, 0.04, 0.38, 0.015), M('d3-wood', 'child-room'), { ...tbx, h: FLOOR_Y + 0.3 });
  }

  // ───────────────────────── Palette / policy / size ─────────────────────────

  readPalette() {
    this.pal.read(TOKENS);
    this.applyLights(this.pal);
    this.dirty = true;
    this.renderer.shadowMap.needsUpdate = true;
  }

  setPolicy(policy) {
    this.policy = policy;
    const rects = [policy.envelope.workspace, ...(policy.zones ?? []).map((z) => z.area)];
    const fin = (v, d) => (Number.isFinite(v) && Math.abs(v) < 1e5 ? v : d);
    this.bounds = {
      minX: fin(Math.min(...rects.map((q) => q.min.x)), 0) - 0.4,
      minY: fin(Math.min(...rects.map((q) => q.min.y)), 0) - 0.4,
      maxX: fin(Math.max(...rects.map((q) => q.max.x)), 10) + 0.4,
      maxY: fin(Math.max(...rects.map((q) => q.max.y)), 10) + 0.4,
    };
    this.fx.setPolicy(policy);
    const home = this.atHome();
    this.computeFit();
    if (home) this.rehome();
    else if (!this.userOwnsCamera && !this.tween) this.setView(this.defTarget, this.az(), this.polar(), this.fitD);
    this.dirty = true;
  }

  resize(force = false) {
    const W = Math.round(this.wrap.clientWidth);
    const H = Math.round(this.wrap.clientHeight);
    if (!W || !H || (!force && W === this.cssW && H === this.cssH)) return;
    const first = !this.cssW;
    this.cssW = W;
    this.cssH = H;
    this.pixelRatio();
    this.renderer.setSize(W, H, false);
    this.css2d.setSize(W, H);
    this.camera.aspect = W / H;
    this.camera.updateProjectionMatrix();
    const home = this.atHome();
    this.labelLayer.classList.toggle('narrow', W < 560);
    this.computeFit();
    if (first) this.setView(this.defTarget, DEFAULT.az, DEFAULT.polar, this.fitD);
    else if (home) this.rehome();
    else if (!this.userOwnsCamera && !this.tween) this.setView(this.targetW(), this.az(), this.polar(), this.fitD * (this.lastDistK ?? 1));
    this.controls.minDistance = 0.45 * this.fitD;
    this.controls.maxDistance = 1.3 * this.fitD;
    this.dirty = true;
    this.onResize?.();
  }

  /**
   * Keep-out rects over the map, in map px ({x, y, w, h}): chrome such as the
   * camera toolbar. The default framing fits the house around them.
   */
  setReserve(rects = []) {
    const key = JSON.stringify(rects);
    if (key === this.reserveKey) return;
    this.reserveKey = key;
    this.reserve = rects;
    if (!this.cssW) return;
    const home = this.atHome();
    this.computeFit();
    if (home) this.rehome();
    this.dirty = true;
  }

  /**
   * Put a home camera back on the (re)computed default framing. A homing tween
   * keeps running but now ends on the new fit, so a resize or keep-out that lands
   * mid-tween (the first frames after load) is not lost.
   */
  rehome() {
    if (this.tween?.home) {
      this.tween.to.target = { ...this.defTarget };
      this.tween.to.d = this.fitD;
    } else {
      this.setView(this.defTarget, this.az(), this.polar(), this.fitD);
    }
  }

  reserveNdc() {
    const W = this.cssW;
    const H = this.cssH;
    if (!W || !H) return [];
    return (this.reserve ?? []).map((r) => ({ x0: (2 * r.x) / W - 1, x1: (2 * (r.x + r.w)) / W - 1, y0: 1 - (2 * (r.y + r.h)) / H, y1: 1 - (2 * r.y) / H }));
  }

  /**
   * True while the camera sits at the default framing, or is on its way there
   * (a homing tween: resetView, card start, end card). Not while the viewer owns it.
   */
  atHome() {
    if (!this.defTarget || this.userOwnsCamera) return false;
    if (this.tween) return !!this.tween.home;
    const t = this.targetW();
    return Math.hypot(t.x - this.defTarget.x, t.y - this.defTarget.y) < 1e-3 && Math.abs((this.lastDistK ?? 0) - 1) < 1e-3;
  }

  /** Distance at which the whole plinth (plus policy zones) fits 92% of the view, clear of the reserve. */
  computeFit(az = DEFAULT.az, polar = DEFAULT.polar) {
    const b = this.bounds;
    const x0 = Math.min(-0.9, b.minX) - 5;
    const x1 = Math.max(10.9, b.maxX) - 5;
    const z0 = 5 - Math.max(10.9, b.maxY);
    const z1 = 5 - Math.min(-1.6, b.minY);
    const corners = [];
    for (const x of [x0, x1]) for (const y of [-0.5, 1.15]) for (const z of [z0, z1]) corners.push(new THREE.Vector3(x, y, z));
    const cam = this.camera.clone();
    const tw = { ...DEFAULT.target }; // refined below
    const target = new THREE.Vector3();
    const p = new THREE.Vector3();
    const box = (d) => {
      w2v(tw, DEFAULT.ty, target);
      this.placeCam(cam, target, az, polar, d);
      cam.updateMatrixWorld();
      let x0 = Infinity; let x1 = -Infinity; let y0 = Infinity; let y1 = -Infinity; let front = true;
      const pts = [];
      for (const c of corners) {
        p.copy(c).project(cam);
        if (p.z >= 1) front = false;
        pts.push({ x: p.x, y: p.y });
        x0 = Math.min(x0, p.x); x1 = Math.max(x1, p.x); y0 = Math.min(y0, p.y); y1 = Math.max(y1, p.y);
      }
      return { x0, x1, y0, y1, front, pts };
    };
    const search = () => {
      let lo = 4;
      let hi = 90;
      for (let i = 0; i < 12; i++) {
        const m = (lo + hi) / 2;
        const b = box(m);
        if (b.front && Math.max(-b.x0, b.x1, -b.y0, b.y1) <= 0.92) hi = m; else lo = m;
      }
      return hi;
    };
    // Move the target along the floor (forward / sideways) so the plinth's box centre lands on `want` (NDC).
    const fwd = { x: -Math.sin(az), y: Math.cos(az) };
    const side = { x: fwd.y, y: -fwd.x };
    const centre = (dist, want) => {
      const b0 = box(dist);
      const c0 = { x: (b0.x0 + b0.x1) / 2 - want.x, y: (b0.y0 + b0.y1) / 2 - want.y };
      const base = { ...tw };
      tw.x = base.x + fwd.x; tw.y = base.y + fwd.y;
      const bf = box(dist);
      const dyF = (bf.y0 + bf.y1) / 2 - want.y - c0.y;
      tw.x = base.x + side.x; tw.y = base.y + side.y;
      const bs = box(dist);
      const dxS = (bs.x0 + bs.x1) / 2 - want.x - c0.x;
      const mf = Math.abs(dyF) > 1e-4 ? -c0.y / dyF : 0;
      const ms = Math.abs(dxS) > 1e-4 ? -c0.x / dxS : 0;
      tw.x = base.x + fwd.x * mf + side.x * ms;
      tw.y = base.y + fwd.y * mf + side.y * ms;
    };
    // Centre the plinth on screen, then fit again.
    let d = search();
    for (let k = 0; k < 2; k++) {
      centre(d, { x: 0, y: 0 });
      d = search();
    }
    // Chrome drawn over the map (the camera toolbar): when the fitted house would
    // run under it, shrink and slide the house to the largest placement that is clear.
    const keep = this.reserveNdc();
    const hits = (P) => keep.some((r) => polyHitsRect(P, r));
    if (keep.length && hits(hull2(box(d).pts))) {
      const b = box(d);
      const c = { x: (b.x0 + b.x1) / 2, y: (b.y0 + b.y1) / 2 };
      const Q = hull2(b.pts).map((q) => ({ x: q.x - c.x, y: q.y - c.y }));
      const at = (s, o) => Q.map((q) => ({ x: s * q.x + o.x, y: s * q.y + o.y }));
      const place = (s) => FIT_OFFSETS.find((o) => at(s, o).every((q) => Math.abs(q.x) <= 0.92 && Math.abs(q.y) <= 0.92) && !hits(at(s, o)));
      let lo = 0.6;
      let hi = 1;
      let best = place(lo) ? { s: lo, o: place(lo) } : null;
      for (let i = 0; best && i < 9; i++) {
        const m = (lo + hi) / 2;
        const o = place(m);
        if (o) { lo = m; best = { s: m, o }; } else hi = m;
      }
      if (best) {
        // Size scales ~1/distance at this narrow fov; a final check absorbs the perspective error.
        const d0 = d;
        for (let s = best.s; s > 0.55; s *= 0.97) {
          d = d0 / s;
          centre(d, best.o);
          centre(d, best.o);
          if (!hits(hull2(box(d).pts))) break;
        }
      }
    }
    this.defTarget = { ...tw };
    const hi = d;
    this.fitD = hi;
    this.controls.minDistance = 0.45 * hi;
    this.controls.maxDistance = 1.3 * hi;
  }

  placeCam(cam, target, az, polar, d) {
    cam.position.set(
      target.x + d * Math.sin(polar) * Math.sin(az),
      target.y + d * Math.cos(polar),
      target.z + d * Math.sin(polar) * Math.cos(az),
    );
    cam.lookAt(target);
  }

  az() { return this.controls.getAzimuthalAngle(); }
  polar() { return this.controls.getPolarAngle(); }
  dist() { return this.camera.position.distanceTo(this.controls.target); }
  targetW() { return v2w(this.controls.target); }

  setView(targetW, az, polar, d) {
    const t = w2v(targetW, DEFAULT.ty, this.controls.target);
    this.placeCam(this.camera, t, az, polar, d);
    this.selfMove = true;
    try { this.controls.update(); } finally { this.selfMove = false; }
    this.camera.updateMatrixWorld();
    this.lastDistK = d / this.fitD;
    this.dirty = true;
  }

  // ───────────────────────── Camera: framing and toolbar ─────────────────────────

  takeCamera() {
    this.userOwnsCamera = true;
    this.tween = null;
    this.homeBtn?.classList.add('owned');
    this.homeBtn?.setAttribute('title', '장면 따라가기 꺼짐 — 눌러서 켜기');
  }

  releaseCamera() {
    this.userOwnsCamera = false;
    this.homeBtn?.classList.remove('owned');
    this.homeBtn?.setAttribute('title', '시점 초기화');
  }

  startTween(to, dur) {
    const from = { target: this.targetW(), az: this.az(), polar: this.polar(), d: this.dist() };
    const end = { target: to.target ?? from.target, az: to.az ?? from.az, polar: to.polar ?? from.polar, d: to.d ?? from.d };
    if (this.reduced || dur <= 0) { this.tween = null; this.setView(end.target, end.az, end.polar, end.d); return; }
    this.tween = { from, to: end, t0: performance.now(), dur, then: to.then ?? null, home: !!to.home };
    this.dirty = true;
  }

  stepTween(real) {
    const tw = this.tween;
    if (!tw) return false;
    const k = clamp01((real - tw.t0) / tw.dur);
    const e = easeInOut(k);
    const L = (a, b) => a + (b - a) * e;
    this.setView(
      { x: L(tw.from.target.x, tw.to.target.x), y: L(tw.from.target.y, tw.to.target.y) },
      L(tw.from.az, tw.to.az), L(tw.from.polar, tw.to.polar), L(tw.from.d, tw.to.d),
    );
    if (k >= 1) {
      this.tween = null;
      tw.then?.();
    }
    return true;
  }

  /** Auto-framing along the beat timeline. Keeps the viewer's angles. */
  frameBeat({ phase, points = [] }) {
    if (this.reduced || this.userOwnsCamera || this.disposed) return;
    if (phase === 'card' || phase === 'end') {
      this.startTween({ target: this.defTarget, d: this.fitD, home: true }, 900);
      return;
    }
    const pts = points.filter((p) => p && Number.isFinite(p.x) && Number.isFinite(p.y))
      .map((p) => ({ x: Math.max(this.bounds.minX, Math.min(this.bounds.maxX, p.x)), y: Math.max(this.bounds.minY, Math.min(this.bounds.maxY, p.y)) }));
    if (!pts.length) return;
    let cx = pts.reduce((s, p) => s + p.x, 0) / pts.length;
    let cy = pts.reduce((s, p) => s + p.y, 0) / pts.length;
    // Clamp the target within maxTargetRadius (4 m) of the house centre.
    const dx = cx - 5;
    const dy = cy - 5;
    const L = Math.hypot(dx, dy);
    if (L > 3.8) { cx = 5 + (dx / L) * 3.8; cy = 5 + (dy / L) * 3.8; }
    const R = Math.max(...pts.map((p) => Math.hypot(p.x - cx, p.y - cy))) + 1.5;
    const fov = (this.camera.fov * D2R) / 2;
    const vfit = R / Math.sin(fov);
    const hfit = R / Math.sin(Math.atan(Math.tan(fov) * this.camera.aspect));
    const d = Math.max(0.55 * this.fitD, Math.min(this.fitD, Math.max(vfit, hfit)));
    this.startTween({ target: { x: cx, y: cy }, d }, 900);
  }

  resetView() {
    this.releaseCamera();
    this.startTween({ target: this.defTarget, az: DEFAULT.az, polar: DEFAULT.polar, d: this.fitD, home: true }, 350);
  }

  viewCmd(cmd) {
    if (cmd === 'home') { this.resetView(); return; }
    this.takeCamera();
    const c = this.controls;
    if (cmd === 'left' || cmd === 'right') {
      const az = Math.max(c.minAzimuthAngle, Math.min(c.maxAzimuthAngle, this.az() + (cmd === 'left' ? -30 : 30) * D2R));
      this.startTween({ az }, 350);
    } else if (cmd === 'in' || cmd === 'out') {
      const d = Math.max(c.minDistance, Math.min(c.maxDistance, this.dist() * (cmd === 'in' ? 0.8 : 1.25)));
      this.startTween({ d }, 350);
    }
  }

  bindToolbar() {
    const bar = document.getElementById('view-tools');
    if (!bar) return;
    this.homeBtn = bar.querySelector('[data-view="home"]');
    for (const b of bar.querySelectorAll('[data-view]')) this.on(b, 'click', () => this.viewCmd(b.dataset.view));
  }

  /** Nudge toward a denial (skipped under reduced motion or when the viewer owns the camera). */
  react(verdict, anchor, reduced = false) {
    if (verdict !== 'bul' || this.reduced || reduced) return;
    const wrap = this.wrap;
    wrap.classList.remove('shake');
    void wrap.offsetWidth;
    wrap.classList.add('shake');
    if (this.userOwnsCamera || this.tween) return;
    const d = this.dist();
    this.startTween({ d: d * 0.96, then: () => { if (!this.userOwnsCamera) this.startTween({ d }, 400); } }, 180);
  }

  pulse() { this.dirty = true; }

  /** World direction (unit, snapped to an axis) for a screen arrow key, camera-relative. */
  axisFor(key) {
    const az = this.az();
    // Camera forward on the floor, in world coordinates (y north).
    const fwd = { x: -Math.sin(az), y: Math.cos(az) };
    const snap = (v) => (Math.abs(v.x) >= Math.abs(v.y) ? { x: Math.sign(v.x), y: 0 } : { x: 0, y: Math.sign(v.y) });
    const up = snap(fwd);
    const right = { x: up.y, y: -up.x };
    switch (key) {
      case 'ArrowUp': return up;
      case 'ArrowDown': return { x: -up.x, y: -up.y };
      case 'ArrowRight': return right;
      case 'ArrowLeft': return { x: -right.x, y: -right.y };
      default: return null;
    }
  }

  // ───────────────────────── Projection helpers ─────────────────────────

  toScreen(p, h = 0) {
    const v = w2v(p, FLOOR_Y + h, this.tmpV).project(this.camera);
    return { x: (v.x + 1) / 2 * this.cssW, y: (1 - v.y) / 2 * this.cssH, behind: v.z > 1 };
  }

  pxPerM(p) {
    const a = this.toScreen(p, 0);
    const right = this.tmpV2.set(1, 0, 0).applyQuaternion(this.camera.quaternion);
    const b = this.toScreen({ x: p.x + right.x, y: p.y - right.z }, 0);
    return Math.max(4, Math.hypot(b.x - a.x, b.y - a.y));
  }

  /** Screen box of a vertical cylinder (radius r, height h) standing at world p. */
  projBox(p, r, h) {
    const right = this.tmpV2.set(1, 0, 0).applyQuaternion(this.camera.quaternion);
    const rx = right.x * r;
    const ry = -right.z * r;
    const pts = [
      this.toScreen({ x: p.x - rx, y: p.y - ry }, 0), this.toScreen({ x: p.x + rx, y: p.y + ry }, 0),
      this.toScreen({ x: p.x - rx, y: p.y - ry }, h), this.toScreen({ x: p.x + rx, y: p.y + ry }, h),
      this.toScreen(p, h + 0.25),
    ];
    const xs = pts.map((q) => q.x);
    const ys = pts.map((q) => q.y);
    const x = Math.min(...xs);
    const y = Math.min(...ys);
    return { x, y, w: Math.max(...xs) - x, h: Math.max(...ys) - y };
  }

  screenBox(kind, id) {
    const sc = this.lastScene;
    if (!sc) return null;
    if (kind === 'robot') return this.projBox(robotPose(sc, this.lastClock ?? 0, performance.now()), 0.32, 0.95);
    if (kind === 'human') {
      const h = sc.world?.humans?.find((q) => q.id === id);
      return h ? this.projBox(h.pos, 0.2, h.class === 'child' ? 0.8 : 1.25) : null;
    }
    if (kind === 'ghost') {
      const g = sc.ghost;
      if (!g || g.kind === 'none') return null;
      return this.projBox(ghostPose(g, this.lastClock ?? 0), 0.32, 1.35);
    }
    if (kind === 'barrier' || kind === 'dome' || kind === 'arch') {
      // The effect configured from scene.fx (the frozen Decision), at full size.
      this.fx.syncVerdict(sc);
      const pts = this.fx.effectPoints(kind, robotPose(sc, this.lastClock ?? 0, performance.now()));
      if (!pts) return null;
      let x0 = Infinity; let y0 = Infinity; let x1 = -Infinity; let y1 = -Infinity;
      const v = this.tmpV;
      for (const p of pts) {
        v.copy(p).project(this.camera);
        if (v.z > 1) continue;
        const sx = (v.x + 1) / 2 * this.cssW;
        const sy = (1 - v.y) / 2 * this.cssH;
        x0 = Math.min(x0, sx); x1 = Math.max(x1, sx); y0 = Math.min(y0, sy); y1 = Math.max(y1, sy);
      }
      return x1 > x0 ? { x: x0, y: y0, w: x1 - x0, h: y1 - y0 } : null;
    }
    return null;
  }

  /** Heights for overlay anchoring. */
  heightOf(kind) {
    return { robot: 0.95, slipRobot: 1.05, tag: 1.35, barrier: 1.05, person: 1.3 }[kind] ?? 0;
  }

  /**
   * Floor point under the pointer. Null when the pointer is over the
   * background (more than 0.3 m outside the bounds), unless `loose` (a person
   * being dragged keeps following, clamped to the bounds).
   */
  toWorld(clientX, clientY, { loose = false } = {}) {
    const r = this.canvas.getBoundingClientRect();
    this.tmpN.set(((clientX - r.left) / r.width) * 2 - 1, -((clientY - r.top) / r.height) * 2 + 1);
    this.raycaster.setFromCamera(this.tmpN, this.camera);
    const hit = this.raycaster.ray.intersectPlane(this.floorPlane, this.tmpV);
    if (!hit) return null;
    const p = v2w(hit);
    const b = this.bounds;
    const M = 0.3;
    if (!loose && (p.x < b.minX - M || p.x > b.maxX + M || p.y < b.minY - M || p.y > b.maxY + M)) return null;
    return this.clamp(p);
  }

  clamp(p) {
    const b = this.bounds;
    return { x: Math.min(b.maxX, Math.max(b.minX, p.x)), y: Math.min(b.maxY, Math.max(b.minY, p.y)) };
  }

  static snap(p) {
    return { x: Math.round(p.x * 20) / 20, y: Math.round(p.y * 20) / 20 };
  }

  setLabMode(on) {
    this.labMode = on;
    const c = this.controls;
    c.mouseButtons = on
      ? { LEFT: null, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.ROTATE }
      : { LEFT: THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN };
    this.canvas.style.touchAction = on ? 'none' : 'pan-y';
    this.dirty = true;
  }

  hitHuman(e, humans) {
    const r = this.canvas.getBoundingClientRect();
    const sx = e.clientX - r.left;
    const sy = e.clientY - r.top;
    for (let i = humans.length - 1; i >= 0; i--) {
      const b = this.projBox(humans[i].pos, 0.22, humans[i].class === 'child' ? 0.8 : 1.25);
      if (sx >= b.x - 4 && sx <= b.x + b.w + 4 && sy >= b.y - 4 && sy <= b.y + b.h + 4) return humans[i];
    }
    return null;
  }

  /** Same handler object the lab passes to the 2D stage. */
  bindPointer(h) {
    const c = this.canvas;
    let drag = null;
    this.on(c, 'pointerdown', (e) => {
      const primary = e.button === 0 || e.pointerType === 'touch';
      if (!primary) return;
      drag = { id: e.pointerId, x0: e.clientX, y0: e.clientY, moved: false, human: null, lab: h.active() };
      if (drag.lab) {
        const human = this.hitHuman(e, h.getHumans());
        if (human) {
          drag.human = human.id;
          this.controls.enabled = false;
          c.setPointerCapture(e.pointerId);
          c.classList.add('grabbing');
        }
      }
    });
    this.on(c, 'pointermove', (e) => {
      if (drag && drag.id === e.pointerId && !drag.moved && Math.hypot(e.clientX - drag.x0, e.clientY - drag.y0) > DRAG_THRESHOLD) drag.moved = true;
      if (!h.active()) return;
      if (drag && drag.id === e.pointerId && drag.human && drag.moved) {
        const q = this.toWorld(e.clientX, e.clientY, { loose: true });
        if (q) h.onHumanMove(drag.human, Stage3D.snap(q));
        this.dirty = true;
        return;
      }
      if (e.pointerType !== 'touch' && !(drag && drag.moved)) {
        const p = this.toWorld(e.clientX, e.clientY);
        c.classList.toggle('grab', !drag && !!this.hitHuman(e, h.getHumans()));
        h.onPointer(p ? Stage3D.snap(p) : null);
        this.dirty = true;
      }
    });
    const end = (e, cancelled) => {
      if (!drag || drag.id !== e.pointerId) return;
      const d = drag;
      drag = null;
      c.classList.remove('grabbing');
      this.controls.enabled = true;
      if (cancelled) return;
      if (!d.lab || !h.active()) {
        if (!d.moved && !h.active()) h.onInactiveClick?.();
        return;
      }
      if (d.human && d.moved) h.onHumanDrop(d.human);
      else if (!d.moved) {
        const p = this.toWorld(e.clientX, e.clientY);
        if (p) h.onCommit(Stage3D.snap(p));
      }
      if (e.pointerType === 'touch') h.onPointer(null);
      this.dirty = true;
    };
    this.on(c, 'pointerup', (e) => end(e, false));
    this.on(c, 'pointercancel', (e) => end(e, true));
    this.on(c, 'pointerleave', (e) => { if (!drag && e.pointerType !== 'touch' && h.active()) h.onPointer(null); });
  }

  // ───────────────────────── Per frame ─────────────────────────

  readCamSig(out) {
    const p = this.camera.position;
    const t = this.controls.target;
    out[0] = p.x; out[1] = p.y; out[2] = p.z; out[3] = t.x; out[4] = t.y; out[5] = t.z;
    return out;
  }

  camChanged() {
    const p = this.camera.position;
    const t = this.controls.target;
    const s = this.camSig;
    const vals = [p.x, p.y, p.z, t.x, t.y, t.z];
    let ch = false;
    for (let i = 0; i < 6; i++) {
      if (Math.abs(s[i] - vals[i]) > 1e-5) { ch = true; s[i] = vals[i]; }
    }
    return ch;
  }

  updateCutaway() {
    const t = this.controls.target;
    const cx = this.camera.position.x - t.x;
    const cz = this.camera.position.z - t.z;
    const L = Math.hypot(cx, cz) || 1;
    for (const w of this.walls) {
      if (!w.outer) continue;
      // Outward normal in three space: (n.x, -n.y).
      const dot = (w.n.x * cx - w.n.y * cz) / L;
      w.target = dot > 0.2 ? Math.min(SILL_H, w.h) : w.h;
    }
  }

  stepWalls(dt) {
    let moving = false;
    const k = this.reduced ? 1 : Math.min(1, dt / 220 * 3);
    for (const w of this.walls) {
      if (Math.abs(w.cur - w.target) < 1e-3) { if (w.cur !== w.target) { w.cur = w.target; moving = true; } else continue; }
      w.cur += (w.target - w.cur) * k;
      if (Math.abs(w.cur - w.target) < 1e-3) w.cur = w.target;
      w.wall.scale.y = w.cur;
      w.cap.position.y = FLOOR_Y + w.cur;
      moving = true;
    }
    return moving;
  }

  syncWallFade(sc, pose) {
    const segs = [];
    const it = sc.intent;
    if (it) segs.push([it.from, it.to]);
    const e = sc.exec;
    if (e?.kind === 'move') segs.push([e.from, e.to]);
    if (e?.kind === 'reach') segs.push([e.from, e.at]);
    const g = sc.ghost;
    if (g && g.kind !== 'none') segs.push([g.from, g.kind === 'move' ? g.to : g.at]);
    if (sc.lab?.preview) segs.push([pose, sc.lab.preview]);
    const key = segs.map((s) => `${s[0].x},${s[0].y},${s[1].x},${s[1].y}`).join('|');
    if (key === this.fadeKey) return;
    this.fadeKey = key;
    for (const w of this.walls) {
      const f = segs.some(([a, b]) => segSegDist(a, b, w.a, w.b) < 0.1);
      if (f !== w.faded) {
        w.faded = f;
        w.wall.material = f ? this.wallFade : this.wallMat;
        w.cap.material = f ? this.capFade : this.capMat;
        w.wall.castShadow = !f;
        this.renderer.shadowMap.needsUpdate = true;
        this.dirty = true;
      }
    }
  }

  syncFocus(sc) {
    const key = (sc.focus ?? []).join(',');
    if (key === this.focusKey) return;
    this.focusKey = key;
    const focus = sc.focus?.length ? new Set(sc.focus) : null;
    for (const [k, e] of this.pal.mats) {
      if (!e.group) continue;
      this.pal.setDim(k, !!focus && !focus.has(e.group));
    }
    this.dirty = true;
  }

  syncHaze(sc, real) {
    const on = (sc.world?.confidence ?? 1) < 0.6 && !sc.ghost;
    if (on !== this.hazeOn) { this.hazeOn = on; this.hazeT = real; }
    const k = this.reduced ? 1 : clamp01((real - (this.hazeT ?? 0)) / 300);
    if (!on && k >= 1) { if (this.scene.fog) { this.scene.fog = null; this.dirty = true; } return false; }
    const f = (this.scene.fog ??= new THREE.Fog(0xffffff, 100, 200));
    f.color.copy(this.pal.color('d3-bg-bottom'));
    const dd = this.dist();
    const near = 0.75 * dd;
    const far = 1.45 * dd;
    const a = on ? k : 1 - k;
    f.near = near + (1 - a) * 4 * this.fitD;
    f.far = far + (1 - a) * 4 * this.fitD;
    return k < 1;
  }

  sigOf(sc) {
    const lab = sc.lab;
    const hs = sc.world?.humans ?? [];
    let hsum = hs.length;
    for (const h of hs) hsum += h.pos.x * 7.1 + h.pos.y * 3.3;
    return [sc.intent, sc.exec, sc.fx, sc.ghost, sc.hl, sc.trail?.length, sc.robot.pose.x, sc.robot.pose.y,
      sc.robot.holding, sc.mode, sc.decor, sc.focus, lab?.cursor, lab?.preview, lab?.grabbed, hsum, sc.realAlpha, sc.peer, sc.signal, sc.world];
  }

  render(sc, clock, real) {
    if (this.disposed || this.lost || !this.cssW || !sc.policy) return;
    // A frozen clock (paused, modal, hidden) animates nothing that is timed by it.
    const clockMoving = clock !== this.lastClock;
    this.lastScene = sc;
    this.lastClock = clock;
    const dt = Math.min(100, Math.max(0, real - (this.lastReal ?? real)));
    this.lastReal = real;
    if (this.reduced !== !!sc.reduced) {
      this.reduced = !!sc.reduced;
      this.controls.enableDamping = !this.reduced;
      if (this.reduced && this.tween) {
        // Finish any camera move at once (no motion under reduced motion).
        const tw = this.tween;
        this.tween = null;
        this.setView(tw.to.target, tw.to.az, tw.to.polar, tw.to.d);
      }
    }

    // Camera.
    const tweening = this.stepTween(real);
    if (this.opts.kiosk && !this.reduced && !this.userOwnsCamera && !this.tween) {
      const az = DEFAULT.az + 12 * D2R * Math.sin((real / 40000) * Math.PI * 2);
      this.setView(this.targetW(), az, this.polar(), this.dist());
    }
    const moved = this.controls.update();
    this.camera.updateMatrixWorld();
    const view = this.camChanged();
    if (view) {
      this.updateCutaway();
      this.labelsDue = true;
      this.onView?.();
    }

    const sig = this.sigOf(sc);
    let sigCh = !this.lastSig || sig.length !== this.lastSig.length;
    if (!sigCh) for (let i = 0; i < sig.length; i++) if (sig[i] !== this.lastSig[i]) { sigCh = true; break; }
    this.lastSig = sig;
    if (sigCh) this.labelsDue = true;

    const pose = robotPose(sc, clock, real);
    this.syncFocus(sc);
    this.syncWallFade(sc, pose);
    const band = sc.ghost ? this.hazardSide : this.plinthSide;
    if (this.plinth.material[1] !== band) { this.plinth.material[1] = band; this.dirty = true; }
    const haze = this.syncHaze(sc, real);
    const act = this.actors.update(sc, clock, real, dt);
    const anim = this.fx.update(sc, clock, real, dt, clockMoving);
    const walls = this.stepWalls(dt);

    if (!this.visible) return;
    // Moving things carry labels: re-run the label pass (throttled below).
    if (anim || act.casters || tweening) this.labelsDue = true;
    const busy = this.dirty || tweening || moved || view || anim || act.casters || walls || haze || sigCh
      || (sc.robot.reactT != null && real - sc.robot.reactT < 1000)
      || (sc.robot.badgeT != null && real - sc.robot.badgeT < 400)
      || (sc.robot.slipT != null && real - sc.robot.slipT < 1200)
      || (sc.robot.glitchT != null && real - sc.robot.glitchT < 400)
      || (sc.decor?.peelT != null && real - sc.decor.peelT < 500)
      || (clockMoving && sc.ghost && clock <= sc.ghost.t0 + sc.ghost.dur + 100)
      || (sc.robot.snap && real - sc.robot.snap.t0 < 450)
      || (clockMoving && sc.signal && clock - sc.signal.t0 < 1400);
    const idleMs = this.coarse ? 50 : 33;
    // Idle ticks (blink, bob, lattice scroll): capped, off under reduced motion and while paused.
    const idle = !this.reduced && !document.hidden && clockMoving && real - this.lastRender >= idleMs;
    if (!busy && !idle) { this.prevBusy = false; this.maybeLabels(real); return; }

    if (act.casters || walls || this.dirty) this.renderer.shadowMap.needsUpdate = true;
    this.dirty = false;
    // Adaptive degrade: shadows first, then the pixel ratio.
    // Sample only runs of consecutive busy frames (idle frames are capped on purpose).
    if (busy && this.prevBusy && !document.hidden && real - this.lastRender < 250) {
      this.frameTimes.push(real - this.lastRender);
      if (this.frameTimes.length > 90) {
        const s = [...this.frameTimes].sort((a, b) => a - b);
        const med = s[45];
        this.frameTimes.length = 0;
        if (med > 40 && this.degrade < 2) {
          this.degrade++;
          if (this.degrade === 1) { this.renderer.shadowMap.enabled = false; console.info('Haetae 3D: shadows off (slow frames)'); }
          if (this.degrade === 2) { this.pixelRatio(); this.renderer.setSize(this.cssW, this.cssH, false); console.info('Haetae 3D: pixel ratio 1 (slow frames)'); }
        }
      }
    }
    this.prevBusy = busy;
    this.lastRender = real;
    this.renderer.render(this.scene, this.camera);
    this.css2d.render(this.scene, this.camera);
    this.maybeLabels(real);
  }

  /**
   * The label pass: when due (throttled to 150 ms), and every 500 ms anyway,
   * because overlay boxes (tag, slip) can move without a frame being drawn.
   * DOM measuring only; nothing is rendered.
   */
  maybeLabels(real) {
    const since = real - (this.lastLabels ?? 0);
    if (!(this.labelsDue ? since > 150 : since > 500)) return;
    this.lastLabels = real;
    this.labelsDue = false;
    this.layoutLabels();
  }

  /**
   * Hide labels that overlap an equal- or higher-priority one (earlier created
   * wins a tie) or that are cut by the map edge; zone chips first try a small
   * vertical nudge. Mark person tags behind walls.
   */
  layoutLabels() {
    // Drop labels whose object left the scene (removed people, rebuilt zone chips).
    this.labels = this.labels.filter((o) => {
      let p = o;
      while (p.parent) p = p.parent;
      if (p === this.scene) return true;
      o.element.remove();
      return false;
    });
    const wrapR = this.wrap.getBoundingClientRect();
    const boxes = [];
    for (const sel of ['.vt-box', '.slip, .slip-tab', '#ctx-chips .ctx-chip']) {
      document.querySelectorAll(sel).forEach((e) => {
        const r = e.getBoundingClientRect();
        if (!r.width) return;
        // The dimmed recorded tag of a comparison replay yields to the ghost's own label.
        const prio = sel === '.vt-box' ? (e.closest('.ghost-real, .trail') ? 1.5 : 1) : sel === '.slip, .slip-tab' ? 2 : 3;
        boxes.push({ r, prio });
      });
    }
    // The camera toolbar is drawn over the map: labels under it (tags and below) step aside.
    const bar = document.getElementById('view-tools');
    const barR = bar?.getBoundingClientRect();
    if (barR?.width) boxes.push({ r: barR, prio: 2 });
    const live = this.labels.filter((o) => o.element.isConnected && o.element.style.display !== 'none' && o.visible && isVisibleUp(o));
    live.sort((a, b) => a.userData.prio - b.userData.prio); // stable: creation order breaks ties
    const E = 3;
    const cut = (r) => r.left < wrapR.left + E || r.right > wrapR.right - E || r.top < wrapR.top + E || r.bottom > wrapR.bottom - E;
    const gone = (r) => r.right < wrapR.left || r.left > wrapR.right || r.bottom < wrapR.top || r.top > wrapR.bottom;
    for (const o of live) {
      const el = o.element;
      el.classList.remove('lbl-hide');
      if (el.style.translate) el.style.translate = '';
      let r = el.getBoundingClientRect();
      if (gone(r)) continue;
      const clashes = (q) => cut(q) || boxes.some((b) => b.prio <= o.userData.prio && overlaps(q, b.r, 2));
      let clash = clashes(r);
      if (clash && el.classList.contains('lbl-zone')) {
        for (const dy of [-(r.height + 3), r.height + 3]) {
          const q = { left: r.left, right: r.right, top: r.top + dy, bottom: r.bottom + dy };
          if (!clashes(q)) {
            el.style.translate = `0 ${Math.round(dy)}px`;
            r = q;
            clash = false;
            break;
          }
        }
      }
      el.classList.toggle('lbl-hide', clash);
      if (!clash) boxes.push({ r, prio: o.userData.prio });
    }
    // Person tags behind a wall or furniture: dimmed and dotted, still readable.
    const cam = this.camera.position;
    for (const p of this.actors.people.values()) {
      const wp = p.tag.getWorldPosition(this.tmpV);
      const dir = this.tmpV2.copy(wp).sub(cam);
      const L = dir.length();
      this.raycaster.set(cam, dir.normalize());
      this.raycaster.far = L - 0.05;
      const hit = this.raycaster.intersectObjects(this.occluders, false).some((h) => !(h.object.material?.transparent));
      p.tag.element.classList.toggle('lbl-behind', hit);
      this.raycaster.far = Infinity;
    }
  }

  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    for (const [t, type, fn, o] of this.listeners) t.removeEventListener(type, fn, o);
    this.listeners = [];
    this.ro.disconnect();
    this.io.disconnect();
    this.controls.dispose();
    this.actors.dispose();
    this.fx.dispose();
    this.scene.traverse((o) => {
      if (o.isMesh || o.isPoints) {
        o.geometry?.dispose();
        const ms = Array.isArray(o.material) ? o.material : [o.material];
        ms.forEach((m) => m?.dispose());
      }
    });
    disposeGeoCache();
    this.pal.dispose();
    for (const t of Object.values(this.tex)) t.dispose();
    for (const l of this.labels) { l.element.remove(); l.removeFromParent(); }
    this.labels = [];
    this.labelLayer.remove();
    this.renderer.dispose();
  }

  /** Swap path only: make sure the canvas's context is released. */
  forceLoss() {
    try { this.renderer.forceContextLoss(); } catch { /* ignore */ }
  }
}

/** Screen offsets (NDC) tried by the keep-out fit, nearest the centre first. */
const FIT_OFFSETS = (() => {
  const out = [];
  for (let i = -20; i <= 20; i++) for (let j = -20; j <= 20; j++) out.push({ x: i * 0.025, y: j * 0.025 });
  return out.sort((a, b) => Math.hypot(a.x, a.y) - Math.hypot(b.x, b.y));
})();

/** Convex hull (monotone chain) of 2D points. */
function hull2(pts) {
  const s = pts.slice().sort((a, b) => a.x - b.x || a.y - b.y);
  const cr = (o, a, b) => (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
  const half = (list) => {
    const h = [];
    for (const p of list) {
      while (h.length >= 2 && cr(h[h.length - 2], h[h.length - 1], p) <= 0) h.pop();
      h.push(p);
    }
    h.pop();
    return h;
  };
  return half(s).concat(half(s.slice().reverse()));
}

/** Does a convex polygon touch an axis-aligned rect {x0, x1, y0, y1}? (separating axes) */
function polyHitsRect(P, r) {
  const xs = P.map((p) => p.x);
  const ys = P.map((p) => p.y);
  if (Math.max(...xs) <= r.x0 || Math.min(...xs) >= r.x1 || Math.max(...ys) <= r.y0 || Math.min(...ys) >= r.y1) return false;
  const R = [{ x: r.x0, y: r.y0 }, { x: r.x1, y: r.y0 }, { x: r.x1, y: r.y1 }, { x: r.x0, y: r.y1 }];
  for (let i = 0; i < P.length; i++) {
    const a = P[i];
    const b = P[(i + 1) % P.length];
    const n = { x: b.y - a.y, y: a.x - b.x };
    const proj = (q) => q.x * n.x + q.y * n.y;
    const pp = P.map(proj);
    const rp = R.map(proj);
    if (Math.max(...pp) <= Math.min(...rp) || Math.max(...rp) <= Math.min(...pp)) return false;
  }
  return true;
}

function overlaps(a, b, pad) {
  return a.left < b.right - pad && a.right > b.left + pad && a.top < b.bottom - pad && a.bottom > b.top + pad;
}

function isVisibleUp(o) {
  for (let p = o; p; p = p.parent) if (!p.visible) return false;
  return true;
}
