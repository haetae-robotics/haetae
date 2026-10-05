import { validTelemetry } from './telemetry.js';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/OrbitControls.js';
import { createProductRig } from './product-rig.js';
import { createPersonRig } from './person-rig.js';

// The pinned manufacturer URDF and meshes are shared with Gazebo.
// Every moving joint, including wheel rotation, comes from joint-state telemetry.
export function createGazeboScene(canvas) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 0.95;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0xd8dce0);
  const camera = new THREE.PerspectiveCamera(35, 1, 0.02, 40);
  const controls = new OrbitControls(camera, canvas);
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  controls.enableDamping = !reducedMotion;
  controls.minDistance = 1.2;
  controls.maxDistance = 7;
  controls.maxPolarAngle = Math.PI / 2.1;
  function resetCamera() {
    camera.position.set(2.6, 2.0, 2.2);
    controls.target.set(0.35, 0.65, -0.2);
    controls.update();
  }
  resetCamera();

  scene.add(new THREE.HemisphereLight(0xe4edf5, 0x707879, 1.2));
  const key = new THREE.DirectionalLight(0xfff6e9, 3.0);
  key.position.set(-1.3, 3.2, 1.5);
  key.target.position.set(0.3, 0.15, 0);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  Object.assign(key.shadow.camera, { left: -1.7, right: 1.7, top: 1.7, bottom: -1.7, near: 0.1, far: 8 });
  key.shadow.bias = -0.00015;
  key.shadow.normalBias = 0.008;
  key.shadow.radius = 3;
  scene.add(key, key.target);
  const fill = new THREE.DirectionalLight(0xc9dfef, 0.8);
  fill.position.set(2, 1.5, -2);
  scene.add(fill);

  // A small procedural softbox environment adds material depth without a
  // network HDRI, bloom, fake sensor rays, or unmeasured robot motion.
  const environment = new THREE.Scene();
  environment.add(new THREE.Mesh(new THREE.BoxGeometry(8, 8, 8),
    new THREE.MeshBasicMaterial({ color: 0x777f88, side: THREE.BackSide })));
  for (const [x, y, z, width, height] of [[-3, 2, 0, 2, 3], [2, 3, -2, 3, 2], [0, 3.8, 1, 4, 1]]) {
    const panel = new THREE.Mesh(new THREE.PlaneGeometry(width, height),
      new THREE.MeshBasicMaterial({ color: 0xffffff, side: THREE.DoubleSide }));
    panel.position.set(x, y, z);
    panel.lookAt(0, 0, 0);
    environment.add(panel);
  }
  const pmrem = new THREE.PMREMGenerator(renderer);
  const environmentMap = pmrem.fromScene(environment, 0.08, 0.1, 15);
  scene.environment = environmentMap.texture;
  pmrem.dispose();
  environment.traverse((object) => { object.geometry?.dispose(); object.material?.dispose(); });

  const floor = new THREE.Mesh(new THREE.PlaneGeometry(30, 30),
    new THREE.MeshStandardMaterial({ color: 0xe3e6e9, roughness: 0.92, metalness: 0 }));
  floor.rotation.x = -Math.PI / 2;
  floor.receiveShadow = true;
  scene.add(floor);
  function floorLine(x1, z1, x2, z2, color, opacity = 1) {
    const geometry = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(x1, 0.003, z1), new THREE.Vector3(x2, 0.003, z2)
    ]);
    scene.add(new THREE.Line(geometry, new THREE.LineBasicMaterial({ color, transparent: true, opacity })));
  }
  for (let n = -4; n <= 4; n += 0.5) {
    floorLine(n, -4, n, 4, 0xb9c0c7, Number.isInteger(n) ? 0.35 : 0.18);
    floorLine(-4, n, 4, n, 0xb9c0c7, Number.isInteger(n) ? 0.35 : 0.18);
  }
  for (const z of [-0.85, 0.85]) floorLine(-0.5, z, 1.2, z, 0x84939e);
  for (const x of [-0.5, 1.2]) floorLine(x, -0.85, x, 0.85, 0x84939e);
  for (let x = -0.5; x <= 1.2; x += 0.1) floorLine(x, 0.85, x, 0.83, 0x84939e);
  floorLine(0, -0.04, 0, 0.04, 0x557783);
  const labelCanvas = document.createElement('canvas');
  labelCanvas.width = 512; labelCanvas.height = 96;
  const labelContext = labelCanvas.getContext('2d');
  labelContext.fillStyle = '#71818c';
  labelContext.font = '500 40px sans-serif';
  labelContext.fillText('ROSBOT XL  /  TEST BAY', 0, 58);
  const labelTexture = new THREE.CanvasTexture(labelCanvas);
  labelTexture.colorSpace = THREE.SRGBColorSpace;
  const floorLabel = new THREE.Mesh(new THREE.PlaneGeometry(0.72, 0.135),
    new THREE.MeshBasicMaterial({ map: labelTexture, transparent: true, depthWrite: false }));
  floorLabel.rotation.x = -Math.PI / 2;
  floorLabel.position.set(-0.23, 0.004, 0.95);
  scene.add(floorLabel);

  const robot = new THREE.Group();
  scene.add(robot);
  const rosFrame = new THREE.Group();
  rosFrame.rotation.x = -Math.PI / 2;
  robot.add(rosFrame);
  let productRig = null;
  let latestJoints = [];
  const gateRing = new THREE.Mesh(new THREE.RingGeometry(0.22, 0.225, 64),
    new THREE.MeshBasicMaterial({ color: 0x568e96, side: THREE.DoubleSide, transparent: true, opacity: 0.65 }));
  gateRing.rotation.x = -Math.PI / 2;
  gateRing.position.y = 0.005;
  robot.add(gateRing); // Haetae state overlay, not a product status light.
  canvas.dataset.modelState = "loading";
  const ready = fetch('./assets/rosbot-xl.json', { cache: 'no-store' })
    .then((response) => { if (!response.ok) throw new Error('Robot asset unavailable'); return response.json(); })
    .then((asset) => {
      productRig = createProductRig(THREE, asset);
      rosFrame.add(productRig.root); productRig.update(latestJoints);
      canvas.dataset.modelState = "loaded";
    });

  // Body root follows the native Gazebo torso pose; gait is an illustration.
  const personMaterial = new THREE.MeshStandardMaterial({ color: 0xd2a35d, roughness: 0.8, transparent: true, opacity: 0 });
  const personRig = createPersonRig(THREE, personMaterial);
  const person = personRig.root;
  const reportRing = new THREE.Mesh(new THREE.RingGeometry(0.28, 0.286, 64),
    new THREE.MeshBasicMaterial({ color: 0xb9832c, side: THREE.DoubleSide, transparent: true, opacity: 0 }));
  reportRing.rotation.x = -Math.PI / 2;
  reportRing.position.y = 0.006;
  person.add(reportRing);
  person.visible = false;
  scene.add(person);

  const measuredPoints = new THREE.Group(); scene.add(measuredPoints);
  const pointGeometry = new THREE.SphereGeometry(0.025, 8, 6);
  const pointMaterial = new THREE.MeshBasicMaterial({ color: 0x2ac6bb });
  let targetX = 0, targetZ = 0, targetYaw = 0;
  let first = true, personPresent = false, personOpacity = 0;
  let walkDistance = 0, walking = false, strideWeight = 0;
  let personHeading = 0;
  let lastPersonAt = 0;
  let lastFrameAt = performance.now();
  function update(row) {
    if (!validTelemetry(row)) return false;
    targetX = Number(row.x) - 5; targetZ = 5 - Number(row.y);
    latestJoints = row.joints ?? [];
    productRig?.update(latestJoints);
    if (Number.isFinite(row.yaw)) targetYaw = row.yaw;
    if (first || canvas.hidden) {
      robot.position.set(targetX, 0, targetZ);
      robot.rotation.y = targetYaw; first = false;
    }
    measuredPoints.clear();
    for (const detection of row.detections || []) {
      const point = new THREE.Mesh(pointGeometry, pointMaterial);
      point.position.set(Number(detection.pos.x) - 5, 1.16, 5 - Number(detection.pos.y));
      measuredPoints.add(point);
    }
    const human = row.humans?.[0];
    personPresent = Boolean(human);
    walking = Boolean(row.human_motion?.moving && human);
    lastPersonAt = performance.now();
    if (row.human_motion) {
      walkDistance = Number(row.human_motion.distance_m);
      personHeading = Number(row.human_motion.heading) + Math.PI / 2;
    }
    reportRing.visible = personPresent;
    if (human) person.position.set(Number(human.pos.x) - 5, 0, 5 - Number(human.pos.y));
    return true;
  }
  const hazardGroup = new THREE.Group(); scene.add(hazardGroup);
  let hazardFixtures = null;
  const hazardAssets = fetch('./assets/household-fixtures.json').then(r=>{if (!r.ok) throw new Error('Hazard fixtures unavailable'); return r.json();}).then(v=>{hazardFixtures=v;});
  let hazardCase = null, hazardItem = null, hazardTarget = null, hazardPath = null, pathKey = '';
  function setHazardScene(row) {
    const finitePoint = (p) => p && ['x','y','z'].every(k => Number.isFinite(p[k]) && Math.abs(p[k])<=100);
    if (!hazardFixtures || !hazardFixtures[row.case] || !finitePoint(row.item) || !finitePoint(row.target) || !Array.isArray(row.path) || !row.path.every(finitePoint)) return;
    if (hazardCase !== row.case) {
      for (const child of [...hazardGroup.children]) { child.traverse(c=>{c.geometry?.dispose();c.material?.dispose();}); hazardGroup.remove(child); }
      hazardCase = row.case; hazardPath = null; pathKey = '';
      function fixture(role) {
        const group=new THREE.Group();
        for (const part of hazardFixtures[row.case][role]) {
          const geometry=part.shape==='box' ? new THREE.BoxGeometry(...part.size) : part.shape==='sphere' ? new THREE.SphereGeometry(part.radius,20,14) : new THREE.CylinderGeometry(part.radius,part.radius,part.length,24);
          const mesh=new THREE.Mesh(geometry,new THREE.MeshStandardMaterial({color:part.color,roughness:.55}));
          if (part.shape==='cylinder') mesh.rotation.x=Math.PI/2;
          mesh.position.set(...part.pos); group.add(mesh);
        }
        group.rotation.x=-Math.PI/2; return group;
      }
      hazardItem=fixture('item'); hazardTarget=fixture('target');
      hazardGroup.add(hazardItem,hazardTarget);
    }
    const place=(mesh,p)=>mesh.position.set(p.x-5,p.z,5-p.y);
    place(hazardItem,row.item); place(hazardTarget,row.target);
    if (Array.isArray(row.item_quaternion) && row.item_quaternion.length===4 && row.item_quaternion.every(Number.isFinite)) {
      hazardItem.quaternion.setFromAxisAngle(new THREE.Vector3(1,0,0),-Math.PI/2).multiply(new THREE.Quaternion(...row.item_quaternion));
    }
    const key=JSON.stringify(row.path);
    if (key!==pathKey) {
      if (hazardPath) { hazardGroup.remove(hazardPath); hazardPath.geometry.dispose(); hazardPath.material.dispose(); }
      pathKey=key;
      if (row.path.length>1) {
        const geometry=new THREE.BufferGeometry().setFromPoints(row.path.map(p=>new THREE.Vector3(p.x-5,p.z,5-p.y)));
        hazardPath=new THREE.Line(geometry,new THREE.LineDashedMaterial({color:0xe1ab60,dashSize:.02,gapSize:.015}));
        hazardPath.computeLineDistances(); hazardGroup.add(hazardPath);
      }
    }
  }
  function setBlocked(value) {
    gateRing.material.color.setHex(value ? 0xe5484d : 0x568e96);
  }
  function frame() {
    const now = performance.now();
    const elapsed = Math.min((now - lastFrameAt) / 1000, 0.1); lastFrameAt = now;
    const opacityTarget = personPresent ? 1 : 0;
    personOpacity = reducedMotion ? opacityTarget : personOpacity + Math.sign(opacityTarget - personOpacity) *
      Math.min(Math.abs(opacityTarget - personOpacity), elapsed / 0.9);
    personMaterial.opacity = personOpacity; reportRing.material.opacity = personOpacity;
    person.visible = personOpacity > 0;
    // Stop the illustrative stride if telemetry stalls. The person's root
    // position follows the observed native pose without display-only drift.
    const strideTarget = walking && now - lastPersonAt < 400 && !reducedMotion ? 1 : 0;
    strideWeight += (strideTarget - strideWeight) * (1 - Math.exp(-12 * elapsed));
    personRig.pose(walkDistance, strideWeight);
    const turn = personHeading - person.rotation.y;
    person.rotation.y += Math.atan2(Math.sin(turn), Math.cos(turn)) * (1 - Math.exp(-10 * elapsed));
    const bounds = canvas.getBoundingClientRect();
    if (bounds.width > 0 && bounds.height > 0) {
      const width = Math.round(bounds.width), height = Math.round(bounds.height);
      if (canvas.width !== Math.round(width * renderer.getPixelRatio()) || canvas.height !== Math.round(height * renderer.getPixelRatio())) {
        renderer.setSize(width, height, false); camera.aspect = width / height; camera.updateProjectionMatrix();
      }
      const blend = reducedMotion ? 1 : 1 - Math.exp(-15 * elapsed);
      robot.position.x += (targetX - robot.position.x) * blend;
      robot.position.z += (targetZ - robot.position.z) * blend;
      const yawDelta = targetYaw - robot.rotation.y;
      robot.rotation.y += Math.atan2(Math.sin(yawDelta), Math.cos(yawDelta)) * blend;
      controls.update(); renderer.render(scene, camera);
    }
    requestAnimationFrame(frame);
  }
  frame();
  return { update, setBlocked, setHazardScene, resetCamera, ready: Promise.all([ready,hazardAssets]) };
}
