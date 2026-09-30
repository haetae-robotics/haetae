// URDF link/joint reconstruction from the pinned manufacturer asset.
export function createProductRig(THREE, asset) {
  const root = new THREE.Group();
  const measuredJoints = new Map();
  const jointObjects = new Map();
  function applyOrigin(group, origin) {
    group.position.fromArray(origin.xyz);
    group.quaternion.setFromEuler(new THREE.Euler(...origin.rpy, 'ZYX'));
  }
  function applyJoints() {
    for (const [name, item] of jointObjects) {
      const spec = item.spec;
      const value = measuredJoints.has(name) ? measuredJoints.get(name) : spec.mimic
        ? (measuredJoints.get(spec.mimic.joint) ?? jointObjects.get(spec.mimic.joint)?.spec.initial ?? 0) * spec.mimic.multiplier + spec.mimic.offset
        : (measuredJoints.get(name) ?? spec.initial);
      if (spec.type === 'revolute' || spec.type === 'continuous') {
        item.motion.quaternion.setFromAxisAngle(item.axis, value);
      } else if (spec.type === 'prismatic') {
        item.motion.position.copy(item.axis).multiplyScalar(value);
      }
    }
  }
  const links = new Map(asset.links.map((link) => [link.name, new THREE.Group()]));
  const geometries = new Map();
  const materialCache = new Map();
  function material(color) {
    const key = color.join(',');
    if (!materialCache.has(key)) materialCache.set(key, new THREE.MeshStandardMaterial({
      color: new THREE.Color(...color.slice(0, 3)), roughness: 0.65, metalness: 0.15,
      opacity: color[3], transparent: color[3] < 1, envMapIntensity: 0.6
    }));
    return materialCache.get(key);
  }
  for (const [uri, source] of Object.entries(asset.meshes)) {
    geometries.set(uri, source.parts.map((part) => {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(part.positions, 3));
      geometry.setAttribute('normal', new THREE.Float32BufferAttribute(part.normals, 3));
      geometry.setIndex(part.indices); geometry.computeBoundingSphere();
      return { geometry, color: part.color };
    }));
  }
  for (const link of asset.links) {
    const group = links.get(link.name); group.name = link.name;
    for (const visual of link.visuals) {
      const visualGroup = new THREE.Group(); applyOrigin(visualGroup, visual.origin);
      group.add(visualGroup);
      let parts;
      if (visual.mesh) {
        visualGroup.scale.fromArray(visual.scale);
        parts = geometries.get(visual.mesh);
      } else {
        const d = visual.dimensions;
        const geometry = visual.shape === 'cylinder'
          ? new THREE.CylinderGeometry(d.radius[0], d.radius[0], d.length[0], 32)
          : visual.shape === 'sphere' ? new THREE.SphereGeometry(d.radius[0], 24, 16)
          : new THREE.BoxGeometry(...d.size);
        if (visual.shape === 'cylinder') geometry.rotateX(Math.PI / 2);
        parts = [{ geometry, color: visual.color ?? [0.2, 0.2, 0.2, 1] }];
      }
      for (const part of parts) {
        const mesh = new THREE.Mesh(part.geometry, material(visual.color ?? part.color));
        mesh.castShadow = true; mesh.receiveShadow = true; visualGroup.add(mesh);
      }
    }
  }
  for (const spec of asset.joints) {
    const pivot = new THREE.Group(); applyOrigin(pivot, spec.origin);
    const motion = new THREE.Group(); pivot.add(motion);
    links.get(spec.parent).add(pivot); motion.add(links.get(spec.child));
    jointObjects.set(spec.name, { spec, motion, axis: new THREE.Vector3(...spec.axis).normalize() });
  }
  root.add(links.get(asset.root)); applyJoints();
  return { root, joints: jointObjects, update(rows) {
    for (const joint of rows) measuredJoints.set(joint.name, joint.position);
    applyJoints();
  } };
}
