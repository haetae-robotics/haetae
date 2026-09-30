import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from './vendor/three/three.module.js';
import { createPersonRig } from './person-rig.js';

test('walking feet stay above the floor and the entire gait respects the clearance radius', () => {
  const rig = createPersonRig(THREE, new THREE.MeshBasicMaterial());
  for (let sample = 0; sample <= 72; sample++) {
    rig.pose(sample / 72 * .36, 1);
    rig.root.updateMatrixWorld(true);
    rig.root.traverse((mesh) => {
      if (!mesh.geometry) return;
      const positions = mesh.geometry.attributes.position;
      for (let i = 0; i < positions.count; i++) {
        const p = new THREE.Vector3().fromBufferAttribute(positions, i).applyMatrix4(mesh.matrixWorld);
        assert.ok(p.y >= -1e-6, `body penetrates floor at ${sample}: ${p.y}`);
        assert.ok(Math.hypot(p.x, p.z) <= .29, `gait exceeds report clearance at ${sample}`);
      }
    });
    const heights = rig.feet.map((foot) => foot.getWorldPosition(new THREE.Vector3()).y - .0275);
    assert.ok(Math.min(...heights) < .001, 'at least one sole stays planted');
  }
  rig.pose(.12, 0); rig.root.updateMatrixWorld(true);
  for (const foot of rig.feet) assert.ok(Math.abs(foot.getWorldPosition(new THREE.Vector3()).y - .0275) < 1e-6);
});

test('a foot in stance stays planted in world space while the body advances', () => {
  const rig = createPersonRig(THREE, new THREE.MeshBasicMaterial());
  const points = [.025, .045, .065].map((distance) => {
    rig.root.position.z = distance; rig.pose(distance, 1); rig.root.updateMatrixWorld(true);
    return rig.feet[0].getWorldPosition(new THREE.Vector3());
  });
  for (const point of points) assert.ok(point.distanceTo(points[0]) < 1e-7);
});
