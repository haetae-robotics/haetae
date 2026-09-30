import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as THREE from './vendor/three/three.module.js';
import { createProductRig } from './product-rig.js';

const asset = JSON.parse(readFileSync(new URL('./assets/rosbot-xl.json', import.meta.url)));
const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-7, `${a} != ${b}`);

test('official Home pose and joint axes reconstruct the end effector', () => {
  const rig = createProductRig(THREE, asset);
  rig.root.updateMatrixWorld(true);
  const tip = rig.root.getObjectByName('end_effector_link').getWorldPosition(new THREE.Vector3());
  // Independent forward kinematics for the official Home angles (0,-1,.7,.3).
  const q2 = -1, q23 = -.3;
  const expectedX = -.11 + .024 * Math.cos(q2) + .128 * Math.sin(q2) + .124 * Math.cos(q23) + .126;
  const expectedZ = .048 + .08345 + .017 + .0595 - .024 * Math.sin(q2) + .128 * Math.cos(q2) - .124 * Math.sin(q23);
  near(tip.x, expectedX); near(tip.y, 0); near(tip.z, expectedZ);
  rig.update([{ name: 'joint1', position: Math.PI / 2 }]);
  rig.root.updateMatrixWorld(true);
  const rotated = rig.root.getObjectByName('end_effector_link').getWorldPosition(new THREE.Vector3());
  near(rotated.x, -.11); near(rotated.y, expectedX + .11); near(rotated.z, expectedZ);
});

test('wheel rotation uses measured joint positions and gripper honors measured mimic state', () => {
  const rig = createProductRig(THREE, asset);
  rig.update([{ name: 'fl_wheel_joint', position: .7 }, { name: 'rr_wheel_joint', position: -.4 },
    { name: 'gripper_left_joint', position: .006 }]);
  near(rig.joints.get('fl_wheel_joint').motion.quaternion.y, Math.sin(.35));
  near(rig.joints.get('rr_wheel_joint').motion.quaternion.y, Math.sin(-.2));
  near(rig.joints.get('gripper_left_joint').motion.position.y, .006);
  near(rig.joints.get('gripper_right_joint').motion.position.y, -.006);
  rig.update([{ name: 'gripper_right_joint', position: .004 }]);
  near(rig.joints.get('gripper_right_joint').motion.position.y, -.004);
});
