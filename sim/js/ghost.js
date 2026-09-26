// "해태 없이 보기" — the honest counterfactual.
//
// This module executes the model's RAW proposal exactly as sent, as if there
// were no gate between the model and the motors. It deliberately imports
// nothing from engine.js, copy.js or explain.js, never receives a Decision,
// and produces no verdict, seal, fired name or verdict colour. The stage
// draws what it returns in the intent (violet) style only.

const REACH_MS = 1400; // grasp/place carry no speed; a fixed arm reach at 1×

/**
 * @param {{world: object, proposal: object, k: number}} run
 *   world: the beat's scripted world; proposal: the model's raw proposal;
 *   k: the time-compression factor shared with the gated run.
 * @param {{clock(): number, setGhost(g: object|null): void}} layer the stage's intent layer
 * @returns {{duration: number, facts: object}} ms until the ghost is done, and
 *   plain facts taken from the proposal (requested speed, destination, object).
 */
export function playUnfiltered({ world, proposal, k }, layer) {
  const a = proposal.action ?? {};
  const from = { x: world.robot.pose.x, y: world.robot.pose.y };
  const holding = world.robot.holding ?? null;
  const t0 = layer.clock() + 250;
  const kk = Math.max(1, Number(k) || 1);
  let g;
  switch (a.type) {
    case 'move_to': {
      const d = Math.hypot(a.goal.x - from.x, a.goal.y - from.y);
      const dur = a.speed > 0 ? (d / a.speed / kk) * 1000 : 0;
      g = { kind: 'move', from, to: { x: a.goal.x, y: a.goal.y }, speed: a.speed, holding, t0, dur };
      break;
    }
    case 'grasp':
      g = { kind: 'reach', from, at: { x: a.at.x, y: a.at.y }, holdBefore: holding, holdAfter: a.object, t0, dur: REACH_MS };
      break;
    case 'place':
      g = { kind: 'reach', from, at: { x: a.at.x, y: a.at.y }, holdBefore: holding, holdAfter: null, t0, dur: REACH_MS };
      break;
    default:
      g = { kind: 'none', from, holding, t0, dur: 0 };
  }
  layer.setGhost(g);
  return {
    duration: g.t0 - layer.clock() + g.dur,
    facts: { speed: a.speed ?? null, to: a.goal ?? a.at ?? null, object: a.object ?? holding },
  };
}
