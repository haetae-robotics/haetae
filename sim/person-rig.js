// Articulated test-report silhouette. Travel comes from the runner; this
// illustrative gait is not a measured human skeleton or a Gazebo actor.
export function createPersonRig(THREE, material) {
  const root = new THREE.Group();
  const body = new THREE.Group(); root.add(body);
  const legs = [], arms = [], feet = [];
  const upperLength = 0.43, lowerLength = 0.42, hipHeight = 0.89;
  function part(parent, geometry, x, y, z) {
    const mesh = new THREE.Mesh(geometry, material);
    mesh.position.set(x, y, z); mesh.castShadow = true; parent.add(mesh);
    return mesh;
  }
  part(body, new THREE.SphereGeometry(0.095, 24, 16), 0, 1.625, 0);
  part(body, new THREE.CapsuleGeometry(0.145, 0.36, 8, 20), 0, 1.16, 0);
  for (const side of [-1, 1]) {
    const hip = new THREE.Group(); hip.position.set(side * 0.075, hipHeight, 0); body.add(hip);
    part(hip, new THREE.CapsuleGeometry(0.045, upperLength - 0.09, 6, 16), 0, -upperLength / 2, 0);
    const knee = new THREE.Group(); knee.position.y = -upperLength; hip.add(knee);
    part(knee, new THREE.CapsuleGeometry(0.038, lowerLength - 0.076, 6, 16), 0, -lowerLength / 2, 0);
    const ankle = new THREE.Group(); ankle.position.y = -lowerLength; knee.add(ankle);
    feet.push(part(ankle, new THREE.BoxGeometry(0.09, 0.055, 0.16), 0, -0.0225, 0.025));
    legs.push({ hip, knee, ankle });
    const shoulder = new THREE.Group(); shoulder.position.set(side * 0.205, 1.425, 0); body.add(shoulder);
    part(shoulder, new THREE.CapsuleGeometry(0.035, 0.18, 6, 16), 0, -0.125, 0);
    const elbow = new THREE.Group(); elbow.position.y = -0.25; shoulder.add(elbow);
    part(elbow, new THREE.CapsuleGeometry(0.03, 0.19, 6, 16), 0, -0.125, 0);
    arms.push({ shoulder, elbow });
  }
  function pose(distance, strideWeight = 1) {
    const phase = distance / 0.36 * Math.PI * 2;
    for (let i = 0; i < legs.length; i++) {
      const angle = phase + i * Math.PI;
      const cycle = ((distance / 0.36 + i * 0.5) % 1 + 1) % 1;
      const stance = cycle < 0.5;
      const swing = (cycle - 0.5) * 2;
      // During stance, local travel exactly cancels forward root travel.
      // During swing, lift and return the foot to the next contact point.
      const z = (stance ? 0.09 - 0.36 * cycle
        : -0.09 + 0.18 * swing * swing * (3 - 2 * swing)) * strideWeight;
      const y = 0.05 + (stance ? 0 : Math.sin(Math.PI * swing) * 0.065) * strideWeight;
      const down = hipHeight - y;
      const reach = Math.min(upperLength + lowerLength - 1e-6, Math.hypot(down, z));
      const hipAngle = Math.atan2(-z, down) - Math.acos(Math.max(-1, Math.min(1,
        (upperLength ** 2 + reach ** 2 - lowerLength ** 2) / (2 * upperLength * reach))));
      const kneeAngle = Math.PI - Math.acos(Math.max(-1, Math.min(1,
        (upperLength ** 2 + lowerLength ** 2 - reach ** 2) / (2 * upperLength * lowerLength))));
      legs[i].hip.rotation.x = hipAngle;
      legs[i].knee.rotation.x = kneeAngle;
      legs[i].ankle.rotation.x = -hipAngle - kneeAngle; // Sole stays horizontal.
      arms[i].shoulder.rotation.x = Math.cos(angle) * 0.16 * strideWeight;
      arms[i].elbow.rotation.x = 0.12 * strideWeight;
    }
  }
  pose(0, 0);
  return { root, feet, pose };
}
