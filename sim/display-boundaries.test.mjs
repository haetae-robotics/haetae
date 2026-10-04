import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as THREE from './vendor/three/three.module.js';
import { actionText, actionTarget } from './js/explain.js';
import { slipAction } from './js/overlay.js';

const moduleUrl = (source) => 'data:text/javascript,' + encodeURIComponent(source);
const geometryUrl = moduleUrl(readFileSync(new URL('./js/geom3d.js', import.meta.url), 'utf8')
  .replace("'three'", JSON.stringify(new URL('./vendor/three/three.module.js', import.meta.url).href)));
const { ribbon, w2v } = await import(geometryUrl);
const actorSource = readFileSync(new URL('./js/actors3d.js', import.meta.url), 'utf8')
  .replace("'three'", JSON.stringify(new URL('./vendor/three/three.module.js', import.meta.url).href))
  .replace("'./geom3d.js'", JSON.stringify(geometryUrl))
  .replace("'./model.js'", JSON.stringify(new URL('./js/model.js', import.meta.url).href));
const { Actors } = await import(moduleUrl(actorSource));

test('huge or malformed raw intent produces finite bounded display geometry', () => {
  for (const goal of [{x: 1e300, y: -1e300}, {x: Infinity, y: NaN}, null]) {
    const result = ribbon({x: 5, y: 5}, goal, .1, .00000001, 0);
    assert.ok(result.quads <= 2000);
    assert.ok([...result.geometry.attributes.position.array].every(Number.isFinite));
    const point = w2v(goal);
    assert.ok([point.x, point.y, point.z].every(Number.isFinite));
    assert.ok(Math.abs(point.x) <= 105 && Math.abs(point.z) <= 105);
    result.geometry.dispose();
  }
});

test('duplicate human identifiers keep every occurrence visible', () => {
  const actors = Object.create(Actors.prototype);
  actors.people = new Map(); actors.state = {lastPeople: ''}; actors.group = new THREE.Group();
  actors.robotMat = () => new THREE.MeshBasicMaterial();
  actors.pal = {mat: () => new THREE.MeshBasicMaterial()};
  actors.ctx = {makeLabel: () => Object.assign(new THREE.Object3D(), {element: {remove() {}}})};
  const humans = [{id: 'same', class: 'adult'}, {id: 'same', class: 'adult'}];
  actors.syncPeople(humans);
  assert.equal(actors.people.size, 2);
  assert.notEqual(actors.people.get('0:same').g, actors.people.get('1:same').g);
  actors.syncPeople(humans.slice(0, 1));
  assert.equal(actors.people.size, 1);
});

test('malformed action captions remain readable and cannot yield invalid targets', () => {
  for (const action of [{type: 'move_to'}, {type: 'grasp', at: null},
                       {type: 'place', at: {x: null, y: 3}}]) {
    assert.doesNotThrow(() => actionText(action));
    assert.doesNotThrow(() => slipAction(action));
    assert.equal(actionTarget(action), null);
  }
});
