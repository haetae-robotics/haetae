// Korean labels and human-readable explanations. Pure formatting: nothing
// here decides anything; it only describes what the gate returned.

export const VERDICTS = {
  yun: { glyph: '允', name: 'yun', ko: '허용' },
  jeol: { glyph: '節', name: 'jeol', ko: '제한 허용' },
  bul: { glyph: '不', name: 'bul', ko: '거부' },
};

// Mode names as the gate serializes them (EStop → "estop").
// Note the fired name for that mode is still "mode:estop" (see gate.rs mode_name).
export const MODES = ['normal', 'caution', 'hold', 'safe_park', 'estop'];

export const MODE_KO = {
  normal: '정상',
  caution: '주의',
  hold: '홀드',
  safe_park: '안전 주차',
  estop: '비상 정지',
};

export const OBJECTS = {
  knife: { emoji: '🔪', ko: '칼' },
  cup: { emoji: '☕', ko: '컵' },
  toy: { emoji: '🧸', ko: '장난감' },
};

export const SOURCE_KO = {
  vla: 'AI 모델',
  planner: '플래너',
  teleop: '원격 조작',
  peer: '다른 로봇',
};

export function objectEmoji(name) {
  return OBJECTS[name]?.emoji ?? '📦';
}

export function objectLabel(name) {
  const o = OBJECTS[name];
  return o ? `${o.emoji} ${o.ko}(${name})` : `📦 ${name}`;
}

/** Up to two decimals, no trailing zeros. */
export function num(v) {
  if (typeof v !== 'number' || !Number.isFinite(v)) return String(v);
  return String(Math.round(v * 100) / 100);
}

export function pt(p) {
  return `(${num(p.x)}, ${num(p.y)})`;
}

export function verdictLabel(v) {
  const d = VERDICTS[v];
  return d ? `${d.glyph} ${d.name} (${d.ko})` : String(v);
}

/** Sim-time as HH:MM:SS.mmm (UTC). */
export function simTime(ms) {
  const d = new Date(Math.floor(ms));
  const p = (n, w = 2) => String(n).padStart(w, '0');
  return `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}.${p(d.getUTCMilliseconds(), 3)}`;
}

/** Korean text for an ActionKind (null → "없음"). */
export function actionText(a) {
  if (!a) return '없음 (실행하지 않음)';
  switch (a.type) {
    case 'move_to': return `이동 → ${pt(a.goal)} @ ${num(a.speed)} m/s`;
    case 'grasp': return `집기 ${objectLabel(a.object)} @ ${pt(a.at)}`;
    case 'place': return `놓기 @ ${pt(a.at)}`;
    case 'stop': return '정지 (stop)';
    default: return JSON.stringify(a);
  }
}

/** Where an action points on the map (null for stop). */
export function actionTarget(a) {
  if (!a) return null;
  if (a.type === 'move_to') return a.goal;
  if (a.type === 'grasp' || a.type === 'place') return a.at;
  return null;
}

function freshness(policy) {
  const f = policy?.freshness ?? {};
  return {
    world: f.world_max_age_ms ?? 500,
    proposal: f.proposal_max_age_ms ?? 2000,
    future: f.future_tolerance_ms ?? 100,
  };
}

/** Explanations for the gate's built-in (namespaced) checks. */
function builtinText(name, policy) {
  const f = freshness(policy);
  const maxSpeed = policy?.envelope?.max_speed;
  const table = {
    'invalid:proposal': '제안 값이 잘못됨 (유한하지 않은 좌표나 음수 속도 등)',
    'invalid:world': '월드 스냅샷이 잘못됨 (신뢰도가 0~1 밖이거나 좌표가 유한하지 않음)',
    'invalid:timestamp': `타임스탬프가 신뢰 시계보다 ${f.future} ms 넘게 미래 — 위조 의심`,
    'stale:world': `인식 데이터가 ${f.world} ms보다 오래됨 — 낡은 인식으로는 판단하지 않음`,
    'stale:proposal': `제안이 ${f.proposal} ms보다 오래됨 — 재전송(리플레이) 방지`,
    'source:not-allowed': '허용 목록(allowed_sources)에 없는 출처',
    // No number here: the cap actually applied is the gate's speed_cap (shown above).
    'mode:caution': '주의 모드: 속도 상한을 낮춤 (실제 적용 상한은 위의 “속도 상한” = 게이트 speed_cap)',
    'mode:hold': '홀드 모드: 정지 외 모든 동작 거부',
    'mode:safe_park': '안전 주차 모드: 정지 외 모든 동작 거부',
    'mode:estop': '비상 정지 모드: 정지 외 모든 동작 거부',
    'envelope:pose': '로봇의 현재 위치가 작업 공간 밖',
    'envelope:workspace': '목표 지점이 작업 공간 밖',
    'envelope:max_speed': `요청 속도가 최대 속도 ${num(maxSpeed)} m/s를 초과 → 감속`,
    'stop:unvetted-source': '허용 목록 밖 출처의 정지 요청 — 정지는 항상 허용되며 기록만 남김',
  };
  return table[name];
}

/** Human description of a policy rule, derived from its condition and effect. */
export function describeRule(rule) {
  const w = rule.when ?? {};
  const parts = [];
  if (w.object_any) parts.push(`${w.object_any.map(objectLabel).join('·')}을(를) 들고 있거나 집으려 하고`);
  if (w.human_within) {
    const who = { adult: '어른이', child: '아이가' }[w.human_within.class] ?? '사람이';
    parts.push(`${who} 경로에서 ${num(w.human_within.distance)} m 이내이고`);
  }
  if (w.confidence_below != null) parts.push(`인식 신뢰도가 ${num(w.confidence_below)} 미만이고`);
  if (w.source_in) parts.push(`출처가 ${w.source_in.join('/')} 중 하나이고`);
  let cond = parts.join(' ');
  cond = cond.replace(/이고$/, '일 때');
  const effect = rule.then === 'bul'
    ? '거부'
    : rule.then?.jeol ? `속도 ≤ ${num(rule.then.jeol.max_speed)} m/s로 제한` : JSON.stringify(rule.then);
  return `${cond} → ${effect}`;
}

/**
 * Explain one fired name. Returns { name, kind, text } where kind is
 * 'builtin' | 'zone' | 'rule' | 'unknown'.
 */
export function explainFired(name, policy) {
  if (name.startsWith('zone:')) {
    const id = name.slice(5);
    const zone = policy?.zones?.find((z) => z.id === id);
    if (zone?.no_entry) return { name, kind: 'zone', text: `경로가 진입 금지 구역 '${id}'을(를) 지남` };
    if (zone?.speed_limit != null) return { name, kind: 'zone', text: `속도 제한 구역 '${id}' 통과 → ≤ ${num(zone.speed_limit)} m/s` };
    return { name, kind: 'zone', text: `구역 '${id}'` };
  }
  const builtin = builtinText(name, policy);
  if (builtin) return { name, kind: 'builtin', text: builtin };
  if (name.includes(':')) return { name, kind: 'builtin', text: '내장 검사' };
  const rule = policy?.rules?.find((r) => r.id === name);
  if (rule) return { name, kind: 'rule', text: describeRule(rule) };
  return { name, kind: 'unknown', text: '정책 규칙' };
}

export const KIND_KO = { builtin: '내장 검사', zone: '구역', rule: '정책 규칙', unknown: '' };
