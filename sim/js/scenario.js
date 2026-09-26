// Dinner-party scenario player. Replays examples/proposals.jsonl the way the
// CLI file adapter does: every world line advances the clock to the latest
// world stamp_ms seen (now = max), a world line replaces the trusted world
// unless it is older than the current one (out-of-order, as the runtime
// rejects), fault lines raise the mode, and proposals are judged by the gate
// at that clock. Proposal and fault timestamps never move the clock.
//
// The player calls the Gate directly; the Runtime is not exposed through WASM,
// so runtime-only ingest checks (replay:proposal dedup, missing:world,
// invalid-world rejection by the runtime) are not rebuilt here. Only lines that
// are not structurally a world/fault/proposal message are skipped as invalid.

import { VERDICTS, pt, objectLabel, SOURCE_KO, actionText, MODE_KO } from './explain.js';

const DWELL_MS = 1500;

// Narration keyed by proposal id / fault code. Captions describe intent only;
// the verdict is appended from the gate's decision.
export const PROPOSAL_CAPTIONS = {
  1: '① 플래너가 식탁 쪽 (3, 3)으로 이동 제안',
  2: '② AI 모델이 식탁 위의 칼 🔪을 집으려 함',
  3: '③ 탈취된 AI 모델이 칼을 든 채 아이에게 접근 시도',
  4: '④ 가짜 태그: 아이 방으로 2 m/s 돌진',
  5: '⑤ 플래너가 출발점 쪽 (3, 1)로 복귀 이동 제안',
  6: '⑥ 홀드 중에 플래너가 다시 이동 제안',
  7: '⑦ 원격 조작자가 정지 명령',
};

export const FAULT_CAPTIONS = {
  'M-LIDAR-021': `라이다 고장 → ${MODE_KO.caution} 모드 요청`,
  'M-LIDAR-022': `라이다 추가 고장 → ${MODE_KO.hold} 모드 요청`,
};

function proposalCaption(p) {
  return PROPOSAL_CAPTIONS[p.id]
    ?? `제안 #${p.id} (${SOURCE_KO[p.source] ?? p.source}): ${actionText(p.action)}`;
}

function faultCaption(f) {
  return FAULT_CAPTIONS[f.code] ?? `고장 ${f.code ?? ''} → ${MODE_KO[f.raise_to] ?? f.raise_to} 모드 요청`;
}

function worldCaption(w) {
  const holding = w.robot.holding ? `, ${objectLabel(w.robot.holding)} 들고 있음` : '';
  const conf = typeof w.confidence === 'number' ? `, 신뢰도 ${Math.round(w.confidence * 100)}%` : '';
  return `인식 갱신 — 로봇 ${pt(w.robot.pose)}${holding}${conf}`;
}

const isObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
const isPoint = (p) => isObject(p) && typeof p.x === 'number' && typeof p.y === 'number';

/**
 * Split the JSONL stream into typed steps. A world/fault line must have that
 * key as its only key (docs/w2-contract.md §3). This is input parsing only:
 * a line that is not even shaped like a message is skipped, never judged.
 */
export function parseStream(text) {
  const steps = [];
  text.split('\n').forEach((raw, idx) => {
    const line = raw.trim();
    const n = idx + 1;
    if (!line) return;
    let obj;
    try {
      obj = JSON.parse(line);
    } catch (e) {
      steps.push({ kind: 'invalid', error: e.message, line: n });
      return;
    }
    const keys = isObject(obj) ? Object.keys(obj) : [];
    const only = (k) => keys.length === 1 && keys[0] === k;
    if (keys.includes('world') || keys.includes('fault')) {
      if (only('world')) {
        const w = obj.world;
        if (isObject(w) && typeof w.stamp_ms === 'number' && isObject(w.robot) && isPoint(w.robot.pose)) {
          steps.push({ kind: 'world', world: w, line: n });
        } else {
          steps.push({ kind: 'invalid', error: 'world 형식이 아님 (stamp_ms, robot.pose 필요)', line: n });
        }
      } else if (only('fault')) {
        const f = obj.fault;
        if (isObject(f) && typeof f.raise_to === 'string') steps.push({ kind: 'fault', fault: f, line: n });
        else steps.push({ kind: 'invalid', error: 'fault 형식이 아님 (raise_to 필요)', line: n });
      } else {
        steps.push({ kind: 'invalid', error: 'world/fault 줄에는 그 키 하나만 있어야 함', line: n });
      }
    } else if (isObject(obj)) {
      steps.push({ kind: 'proposal', proposal: obj, line: n }); // the gate validates the rest
    } else {
      steps.push({ kind: 'invalid', error: 'JSON 객체가 아님', line: n });
    }
  });
  return steps;
}

export class ScenarioPlayer {
  /**
   * @param {object} api app hooks:
   *   begin(policyText, world) → void          fresh gate + initial world
   *   world(world, nowMs)                       replace the trusted world
   *   rejectedWorld(world, nowMs, currentStamp) an out-of-order world line was skipped
   *   fault(fault, nowMs)                       raise the mode
   *   run(proposal, nowMs) → { decision, done } judge + execute (done: animation promise)
   *   fastForward()                             finish the current animation now
   *   caption(text, index, total)
   *   controls({ running, paused, stoppable? })  stoppable: ⏹ usable while not running
   *   end(summary | null, nowMs)                 leave scenario mode
   *   error(message)                             leave scenario mode with an error
   */
  constructor(api) {
    this.api = api;
    this.token = 0;
    this.running = false;
    this.active = false;   // app is in scenario mode (between api.begin and api.end/error)
    this.paused = false;
    this.animating = false;
    this.wake = null;
    this.stepRequested = false;
    this.dwellMs = DWELL_MS;
  }

  /**
   * @param {object} [src] optional overrides { policy, world, stream } (texts);
   *   any part left out is fetched from ./examples/ as before.
   */
  async start(src = {}) {
    this.stop(true);
    // ⏸/⏭ have nothing to act on while the files load. ⏹ stays usable when a
    // restart interrupts playback, so the user can still leave scenario mode.
    this.api.controls({ running: false, paused: false, stoppable: this.active });
    const token = ++this.token;
    let policyText, world, steps;
    try {
      const get = async (path) => {
        const res = await fetch(path, { cache: 'no-store' });
        if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
        return res.text();
      };
      const [p, w, s] = await Promise.all([
        src.policy ?? get('./examples/policy.json'),
        src.world ?? get('./examples/world.json'),
        src.stream ?? get('./examples/proposals.jsonl'),
      ]);
      policyText = p;
      world = JSON.parse(w);
      steps = parseStream(s);
    } catch (e) {
      if (token === this.token) this.fail(`시나리오 파일을 불러오지 못했습니다: ${e.message}`);
      return;
    }
    if (token !== this.token) return;

    try {
      this.active = true;
      this.api.begin(policyText, world);
    } catch (e) {
      this.fail(`시나리오 정책으로 엔진을 만들지 못했습니다: ${e.message}`);
      return;
    }
    this.steps = steps;
    this.i = 0;
    this.now = world.stamp_ms; // world.json is the initial world
    this.worldStamp = world.stamp_ms;
    this.decisions = [];
    this.running = true;
    this.paused = false;
    this.api.controls(this.status());
    this.api.caption(`시나리오 시작 — 초기 세계: 로봇 ${pt(world.robot.pose)}, 사람 ${(world.humans ?? []).length}명`, 0, steps.length);
    await this.sleep(this.dwellMs);
    this.loop(token).catch((e) => {
      if (token === this.token) this.fail(`시나리오 재생 중 오류: ${e?.message ?? e}`);
    });
  }

  /** Abort playback and hand control back to the app with an error message. */
  fail(message) {
    this.token++;
    this.running = false;
    this.paused = false;
    this.active = false;
    this.wake?.();
    this.api.error(message);
  }

  status() {
    return { running: this.running, paused: this.paused };
  }

  alive(token) {
    return this.running && token === this.token;
  }

  async loop(token) {
    while (this.alive(token) && this.i < this.steps.length) {
      if (this.paused && !this.stepRequested) {
        await this.sleep(null);
        continue;
      }
      this.stepRequested = false;
      await this.step();
      if (!this.alive(token)) return;
      if (!this.paused) await this.sleep(this.dwellMs);
    }
    if (this.alive(token)) this.finish();
  }

  async step() {
    const s = this.steps[this.i++];
    const n = this.i;
    const total = this.steps.length;
    switch (s.kind) {
      case 'world':
        // As the CLI: the clock only moves forward (latest world stamp seen)…
        this.now = Math.max(this.now, s.world.stamp_ms);
        // …and, as the runtime: an older world never replaces a newer one.
        if (s.world.stamp_ms < this.worldStamp) {
          this.api.caption(`out-of-order world 거부 — stamp_ms ${s.world.stamp_ms} < 현재 ${this.worldStamp}, 기존 월드 유지`, n, total);
          this.api.rejectedWorld(s.world, this.now, this.worldStamp);
          break;
        }
        this.worldStamp = s.world.stamp_ms;
        this.api.world(s.world, this.now);
        this.api.caption(worldCaption(s.world), n, total);
        break;
      case 'fault':
        this.api.caption(faultCaption(s.fault), n, total);
        this.api.fault(s.fault, this.now);
        break;
      case 'proposal': {
        // One caption per proposal, written after the gate answers, so the
        // live region speaks the verdict once.
        const base = proposalCaption(s.proposal);
        const { decision, done } = this.api.run(s.proposal, this.now);
        if (decision) {
          this.decisions.push(decision);
          const v = VERDICTS[decision.verdict];
          const why = decision.fired.length ? `: ${decision.fired.join(', ')}` : '';
          this.api.caption(`${base} → ${v.glyph} ${decision.verdict} (${v.ko})${why}`, n, total);
        } else {
          this.api.caption(`${base} → 엔진이 입력을 거부함 (로그 참조)`, n, total);
        }
        this.animating = true;
        await done;
        this.animating = false;
        break;
      }
      default:
        this.api.caption(`${s.line}번째 줄을 해석할 수 없어 건너뜀: ${s.error}`, n, total);
    }
  }

  finish() {
    const counts = { yun: 0, jeol: 0, bul: 0 };
    for (const d of this.decisions) counts[d.verdict] += 1;
    this.running = false;
    this.active = false;
    this.api.controls(this.status());
    this.api.end({ counts, decisions: this.decisions, now: this.now });
  }

  /** Wait `ms` (or until woken when ms is null). pause/next/stop wake it early. */
  sleep(ms) {
    return new Promise((resolve) => {
      const done = () => {
        clearTimeout(this.timer);
        this.wake = null;
        resolve();
      };
      this.wake = done;
      if (ms != null) this.timer = setTimeout(done, ms);
    });
  }

  pause() {
    if (!this.running) return;
    this.paused = true;
    this.api.controls(this.status());
  }

  resume() {
    if (!this.running) return;
    this.paused = false;
    this.api.controls(this.status());
    this.wake?.();
  }

  next() {
    if (!this.running) return;
    if (this.animating) {
      this.api.fastForward();
      return;
    }
    if (this.paused) this.stepRequested = true;
    this.wake?.();
  }

  /**
   * Stop playback. `silent` keeps the app in scenario mode (used when
   * restarting). Otherwise the app leaves scenario mode whenever it is in it,
   * even if playback was already interrupted by a restart that is still loading.
   */
  stop(silent = false) {
    const wasActive = this.active;
    this.token++;
    this.running = false;
    this.paused = false;
    this.wake?.();
    if (silent) return;
    this.active = false;
    this.api.controls(this.status());
    if (wasActive) this.api.end(null, this.now);
  }
}
