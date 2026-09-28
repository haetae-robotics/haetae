// ?selftest — re-checks every attack card and the dinner-party replay against
// the REAL gate (WASM). This is the only module that reads `expect`.
// It never influences what the simulator shows: it only compares and reports.

import { Gate } from './engine.js';
import { CARDS } from './cards.js';
import { parseStream } from './scenario.js';

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// Shipped sources that must contain no Han code points (v3.9).
const SOURCES = [
  './index.html', './style.css', './app.js',
  ...['actors3d', 'cards', 'copy', 'director', 'engine', 'explain', 'fx3d', 'geom3d', 'ghost', 'lab', 'log',
    'model', 'overlay', 'scenario', 'scene3d', 'selftest', 'stage'].map((m) => `./js/${m}.js`),
];
const HAN = /[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]/u;

/** Decode %XX runs (e.g. a percent-encoded favicon) so they are scanned too. */
function decodeRuns(text) {
  return text.replace(/(?:%[0-9A-Fa-f]{2})+/g, (m) => { try { return decodeURIComponent(m); } catch { return m; } });
}

/** Scan the shipped files and the rendered DOM for Han characters. Returns a list of hits. */
export async function hanScan() {
  const hits = [];
  for (const f of SOURCES) {
    let text = '';
    try { text = await fetch(f, { cache: 'no-store' }).then((r) => r.text()); } catch { hits.push({ where: f, line: 0, text: 'fetch failed' }); continue; }
    text.split('\n').forEach((line, i) => {
      if (HAN.test(line) || HAN.test(decodeRuns(line))) hits.push({ where: f, line: i + 1, text: line.trim().slice(0, 120) });
    });
  }
  hits.push(...hanScanDom());
  return hits;
}

/** The rendered DOM: visible text, title / aria-label / alt attributes and the 3D label layer. */
export function hanScanDom() {
  const hits = [];
  if (HAN.test(document.body.innerText)) hits.push({ where: 'DOM innerText', line: 0, text: '' });
  for (const e of document.querySelectorAll('[title], [aria-label], [alt]')) {
    for (const a of ['title', 'aria-label', 'alt']) {
      const v = e.getAttribute(a);
      if (v && HAN.test(v)) hits.push({ where: `DOM @${a}`, line: 0, text: v });
    }
  }
  const layer = document.querySelector('.label-layer');
  if (layer && HAN.test(layer.textContent)) hits.push({ where: 'CSS2D labels', line: 0, text: layer.textContent.slice(0, 120) });
  const icon = document.querySelector('link[rel="icon"]')?.getAttribute('href') ?? '';
  if (HAN.test(decodeRuns(icon))) hits.push({ where: 'favicon', line: 0, text: '' });
  return hits;
}

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
  let han = await hanScan();
  const report = () => {
    for (const h of han) console.error('selftest: Han character found', h);
    const pass = rows.filter((r) => r.ok).length + (dinner.ok ? 1 : 0) + (han.length ? 0 : 1);
    const total = rows.length + 2;
    panel.dataset.ok = String(pass === total);
    panel.textContent = `selftest ${pass}/${total}${han.length ? ' · 한자 검사 실패' : ''}`;
    window.__haetaeSelftest = { rows, dinner, han, pass, total };
  };
  const panel = document.createElement('div');
  panel.className = 'selftest-panel';
  panel.setAttribute('role', 'status');
  document.body.append(panel);
  report();
  // Scan the DOM again once the attract teaser has put its tag and labels on screen.
  setTimeout(() => { han = [...han, ...hanScanDom()]; report(); }, 4000);
  return window.__haetaeSelftest;
}
