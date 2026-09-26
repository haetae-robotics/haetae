// ?selftest — re-checks every attack card and the dinner-party replay against
// the REAL gate (WASM). This is the only module that reads `expect`.
// It never influences what the simulator shows: it only compares and reports.

import { Gate } from './engine.js';
import { CARDS } from './cards.js';
import { parseStream } from './scenario.js';

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

/** Run every card beat on a fresh Gate(defaultPolicy); returns rows. */
export function checkCards(policyText) {
  const rows = [];
  for (const card of CARDS) {
    if (card.stream) continue;
    const gate = new Gate(policyText);
    card.beats.forEach((beat, i) => {
      if (beat.fault) gate.raise(beat.fault.raise_to);
      const row = { card: card.num, beat: i + 1, expect: beat.expect.verdict, verdict: '', fired: '', cap: '', speed: '', ok: false };
      try {
        const d = gate.judge(beat.proposal, beat.world, beat.now);
        row.verdict = d.verdict;
        row.fired = d.fired.join(',');
        row.cap = d.speed_cap;
        row.speed = d.action?.speed ?? null;
        const e = beat.expect;
        row.ok = d.verdict === e.verdict && same(d.fired, e.fired)
          && (!('speed_cap' in e) || d.speed_cap === e.speed_cap)
          && (!('speed' in e) || d.action?.speed === e.speed);
      } catch (err) {
        row.verdict = `throw: ${err.message}`;
      }
      rows.push(row);
    });
    gate.free();
  }
  return rows;
}

/** Dinner party: now = latest world stamp seen; older worlds ignored; faults raise the mode. */
export function checkDinner(policyText, worldText, streamText) {
  const gate = new Gate(policyText);
  let world = JSON.parse(worldText);
  let now = world.stamp_ms;
  const verdicts = [];
  for (const s of parseStream(streamText)) {
    if (s.kind === 'world') {
      now = Math.max(now, s.world.stamp_ms);
      if (s.world.stamp_ms >= world.stamp_ms) world = s.world;
    } else if (s.kind === 'fault') {
      gate.raise(s.fault.raise_to);
    } else if (s.kind === 'proposal') {
      try { verdicts.push(gate.judge(s.proposal, world, now).verdict); } catch { verdicts.push('throw'); }
    }
  }
  gate.free();
  const want = CARDS.find((c) => c.stream).expect.verdicts;
  return { verdicts, want, ok: same(verdicts, want) };
}

export async function runSelftest(policyText) {
  const get = (p) => fetch(p, { cache: 'no-store' }).then((r) => r.text());
  const [w, s] = await Promise.all([get('./examples/world.json'), get('./examples/proposals.jsonl')]);
  const rows = checkCards(policyText);
  const dinner = checkDinner(policyText, w, s);
  console.table(rows);
  console.log('dinner party', dinner.verdicts.join(','), dinner.ok ? 'OK' : `MISMATCH (want ${dinner.want.join(',')})`);
  for (const r of rows) if (!r.ok) console.error('selftest mismatch', r);
  if (!dinner.ok) console.error('selftest dinner mismatch', dinner);
  const pass = rows.filter((r) => r.ok).length + (dinner.ok ? 1 : 0);
  const total = rows.length + 1;
  const panel = document.createElement('div');
  panel.className = 'selftest-panel';
  panel.dataset.ok = String(pass === total);
  panel.setAttribute('role', 'status');
  panel.textContent = `selftest ${pass}/${total}`;
  document.body.append(panel);
  window.__haetaeSelftest = { rows, dinner, pass, total };
  return { rows, dinner, pass, total };
}
