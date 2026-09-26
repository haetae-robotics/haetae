// Words for what the gate returned. Presentation only: these functions select
// among decision.fired and read numbers from the policy, the inputs or the
// decision's speed_cap. They never compute, change or drop a verdict.

import { num, MODE_KO, pt, describeRule, explainFired } from './explain.js';

export const VERDICT_UI = {
  yun: { glyph: '允', word: '통과', ko: '통과' },
  jeol: { glyph: '節', word: '줄임', ko: '줄임' },
  bul: { glyph: '不', word: '막음', ko: '막음' },
};

export const ZONE_KO = { hall: '복도', 'child-room': '아이 방' };

export const SOURCE_NAME = {
  vla: 'AI 모델', // a VLA (vision-language-action) model; 'VLA' is explained in 도움말 and the slip tooltip
  planner: '플래너',
  teleop: '원격 조작',
  peer: '옆집 로봇',
};

export const OBJECT_KO = { knife: '칼', cup: '컵', toy: '장난감' };

export const GENERIC_OUTRO = {
  bul: '해태가 이 공격을 막았습니다.',
  jeol: '해태가 이 공격을 허용하되 속도를 줄였습니다.',
  yun: '지금 정책에서는 이 공격이 통과했습니다. ⚙ 실험실의 정책을 확인해 보세요.',
};

export const KIND_BADGE = { builtin: '내장 검사', zone: '구역', rule: '정책 규칙', unknown: '기타' };

export const zoneName = (id) => ZONE_KO[id] ?? id;
export const sourceName = (s) => SOURCE_NAME[s] ?? String(s);
export const objectName = (o) => OBJECT_KO[o] ?? String(o);

/** 을/를 etc. picked from the last Hangul syllable's final consonant. */
export function josa(word, withBatchim, without) {
  const ch = String(word).trim().slice(-1);
  const code = ch.charCodeAt(0) - 0xac00;
  if (code < 0 || code > 11171) return `${withBatchim}(${without})`;
  return code % 28 ? withBatchim : without;
}

/** Glyph + word, the hanja hidden from screen readers. */
export function verdictHtml(v) {
  const u = VERDICT_UI[v];
  if (!u) return '';
  return `<span aria-hidden="true">${u.glyph}</span> ${u.word}`;
}

const rule = (policy, id) => policy?.rules?.find((r) => r.id === id);
const zone = (policy, id) => policy?.zones?.find((z) => z.id === id);
const fresh = (policy) => ({
  world: policy?.freshness?.world_max_age_ms ?? 500,
  proposal: policy?.freshness?.proposal_max_age_ms ?? 2000,
  future: policy?.freshness?.future_tolerance_ms ?? 100,
});

/** deny | cap | info | unknown — for ordering and headline choice only. */
export function kindOf(name, policy) {
  if (/^(invalid|stale):/.test(name)) return 'deny';
  if (name === 'source:not-allowed' || name === 'envelope:pose' || name === 'envelope:workspace') return 'deny';
  if (name === 'mode:hold' || name === 'mode:safe_park' || name === 'mode:estop') return 'deny';
  if (name === 'mode:caution' || name === 'envelope:max_speed') return 'cap';
  if (name === 'stop:unvetted-source') return 'info';
  if (name.startsWith('zone:')) {
    const z = zone(policy, name.slice(5));
    if (z?.no_entry) return 'deny';
    if (z?.speed_limit != null) return 'cap';
    return 'unknown';
  }
  const r = rule(policy, name);
  if (r) {
    if (r.then === 'bul') return 'deny';
    if (r.then?.jeol) return 'cap';
  }
  return 'unknown';
}

/** The policy limit a cap-kind name stands for (used only to pick a label). */
export function capLimit(name, policy) {
  if (name === 'envelope:max_speed') return policy?.envelope?.max_speed ?? null;
  if (name === 'mode:caution') return policy?.envelope?.max_speed != null ? policy.envelope.max_speed * 0.5 : null;
  if (name.startsWith('zone:')) return zone(policy, name.slice(5))?.speed_limit ?? null;
  return rule(policy, name)?.then?.jeol?.max_speed ?? null;
}

/**
 * The fired name the caption leads with. Returns a fired name, or the pseudo
 * names '·stop' / '·none' for a yun with nothing notable fired.
 */
export function headline(decision, policy) {
  const f = decision.fired ?? [];
  if (decision.verdict === 'bul') {
    return f.find((n) => kindOf(n, policy) === 'deny') ?? f[0] ?? '·none';
  }
  if (decision.verdict === 'jeol') {
    const cap = decision.speed_cap;
    const exact = f.find((n) => kindOf(n, policy) === 'cap' && cap != null && Math.abs((capLimit(n, policy) ?? NaN) - cap) < 1e-9);
    return exact ?? f.find((n) => kindOf(n, policy) === 'cap') ?? f[0] ?? '·none';
  }
  if (f.includes('stop:unvetted-source')) return 'stop:unvetted-source';
  if (decision.action?.type === 'stop') return '·stop';
  return f[0] ?? '·none';
}

const ORDER = { deny: 0, cap: 1, info: 2, unknown: 2 };
/** Every fired name, deny → cap → info/unknown, gate order kept within a class. */
export function sortedFired(decision, policy) {
  return (decision.fired ?? [])
    .map((n, i) => ({ n, i, k: ORDER[kindOf(n, policy)] }))
    .sort((a, b) => a.k - b.k || a.i - b.i)
    .map((x) => x.n);
}

function allowedList(policy) {
  return (policy?.allowed_sources ?? []).map(sourceName).join('·') || '없음';
}

const secs = (ms) => (Math.round(ms / 100) / 10).toFixed(1);

/** Short map label (about 12 characters). */
export function label(name, ctx) {
  const { policy } = ctx;
  if (name === '·none') return '문제 없음';
  if (name === '·stop') return '멈춤';
  const r = rule(policy, name);
  if (r) {
    if (name === 'knife-near-child' && r.when?.human_within) return '칼 들고 아이 곁 불가';
    if (name === 'slow-near-human' && r.when?.human_within) return '사람 곁에선 천천히';
    if (name === 'low-confidence' && r.when?.confidence_below != null) return '잘 안 보여 천천히';
    return `정책 규칙 ${name}`;
  }
  if (name.startsWith('zone:')) {
    const id = name.slice(5);
    const z = zone(policy, id);
    if (z?.no_entry) return `${zoneName(id)} 출입 금지`;
    if (z?.speed_limit != null) return `${zoneName(id)} ${num(z.speed_limit)} m/s 제한`;
    return `구역 '${zoneName(id)}'`;
  }
  const mode = name.startsWith('mode:') ? name.slice(5) : null;
  if (mode === 'caution') return '주의 모드: 절반 속도';
  if (mode) return `${MODE_KO[mode] ?? mode}: 멈춤만 가능`;
  const table = {
    'envelope:max_speed': `최고 속도 ${num(policy?.envelope?.max_speed)} m/s`,
    'envelope:workspace': '집 밖 목표 불가',
    'envelope:pose': '로봇이 공간 밖',
    'source:not-allowed': '미등록 출처',
    'stop:unvetted-source': '멈춤은 언제나 통과',
    'stale:world': '멈춘 화면 판단 거부',
    'stale:proposal': '오래된 명령 차단',
    'invalid:timestamp': '미래 시각 위조 의심',
    'invalid:proposal': '명령 값 오류',
    'invalid:world': '센서 값 오류',
  };
  return table[name] ?? '내장 검사';
}

/**
 * How a cap-kind sentence ends, keyed by what the gate returned: on 不 nothing
 * ran, so nothing was slowed; on 節 a lower limit may be the one applied.
 */
function capEnd(limit, decision) {
  if (decision?.verdict === 'bul') return `${num(limit)} m/s 제한에도 걸렸지만, 다른 조건 때문에 명령 자체가 막혔어요.`;
  const cap = decision?.speed_cap;
  if (decision?.verdict === 'jeol' && cap != null && limit != null && cap < limit - 1e-9) {
    return `${num(limit)} m/s 제한에 걸렸고, 더 낮은 한도 ${num(cap)} m/s가 적용됐어요.`;
  }
  return `${num(limit)} m/s 이하로 줄였어요.`;
}

/** Full sentence for the caption and the detail sheet. */
export function sentence(name, ctx) {
  const { policy, proposal, world, now, decision } = ctx;
  if (name === '·none') return '걸린 조건이 없어 그대로 실행해요. 해태는 필요할 때만 막습니다.';
  if (name === '·stop') return "'멈춤'은 모드와 상관없이 항상 통과해요.";
  const r = rule(policy, name);
  if (r) {
    const hw = r.when?.human_within;
    if (name === 'knife-near-child' && hw && r.then === 'bul') {
      return `칼을 든 채 아이 ${num(hw.distance)} m 안으로는 갈 수 없어요.`;
    }
    if (name === 'slow-near-human' && hw && r.then?.jeol) {
      return `사람 ${num(hw.distance)} m 안을 지나요. ${capEnd(r.then.jeol.max_speed, decision)}`;
    }
    if (name === 'low-confidence' && r.when?.confidence_below != null && r.then?.jeol) {
      const c = Math.round((world?.confidence ?? 0) * 100);
      const t = Math.round(r.when.confidence_below * 100);
      return `인식 신뢰도가 ${c}%로 기준 ${t}%보다 낮아요. ${capEnd(r.then.jeol.max_speed, decision)}`;
    }
    return `정책 규칙: ${describeRule(r)}`;
  }
  if (name.startsWith('zone:')) {
    const id = name.slice(5);
    const z = zone(policy, id);
    const zn = zoneName(id);
    if (z?.no_entry) return `경로가 출입 금지 구역 '${zn}'에 들어가요.`;
    if (z?.speed_limit != null) {
      return `속도 제한 구역 '${zn}'${josa(zn, '을', '를')} 지나요. ${capEnd(z.speed_limit, decision)}`;
    }
    return explainFired(name, policy).text;
  }
  if (name === 'mode:caution') {
    const lim = capLimit('mode:caution', policy);
    if (decision?.verdict === 'bul') return `센서 이상으로 주의 모드예요(절반 속도 ${num(lim)} m/s). 명령은 다른 조건 때문에 막혔어요.`;
    return `센서 이상으로 주의 모드예요. 최고 속도의 절반인 ${num(lim)} m/s까지만 허용해요.${decision?.speed_cap != null && lim != null && decision.speed_cap < lim - 1e-9 ? ` 더 낮은 한도 ${num(decision.speed_cap)} m/s가 적용됐어요.` : ''}`;
  }
  if (name.startsWith('mode:')) {
    const m = MODE_KO[name.slice(5)] ?? name.slice(5);
    return `${m} 모드예요. '멈춤' 말고는 아무 동작도 하지 않아요.`;
  }
  const f = fresh(policy);
  switch (name) {
    case 'envelope:max_speed':
      if (decision?.verdict === 'bul') return `이 로봇의 최고 속도는 ${num(policy?.envelope?.max_speed)} m/s라 요청한 ${num(proposal?.action?.speed)} m/s는 너무 빨라요. 명령은 다른 조건 때문에 막혔어요.`;
      {
        const max = policy?.envelope?.max_speed;
        const cap = decision?.speed_cap;
        const lower = cap != null && max != null && cap < max - 1e-9;
        return `이 로봇의 최고 속도는 ${num(max)} m/s예요. 요청한 ${num(proposal?.action?.speed)} m/s는 ${lower ? `그보다 빠르고, 더 낮은 한도 ${num(cap)} m/s가 적용됐어요.` : `${num(max)} m/s로 줄였어요.`}`;
      }
    case 'envelope:workspace':
      return '목표 지점이 로봇이 일하는 공간 밖이에요.';
    case 'envelope:pose':
      return '로봇의 현재 위치가 작업 공간 밖이라 움직이지 않아요.';
    case 'source:not-allowed': {
      const s = sourceName(proposal?.source);
      return `허가 목록(${allowedList(policy)})에 없는 '${s}'의 명령은 따르지 않아요.`;
    }
    case 'stop:unvetted-source':
      return "출처는 미등록이지만 '멈춤'은 가장 안전한 명령이라 따르고, 기록을 남겨요.";
    case 'stale:world':
      return `인식 정보가 ${secs(now - world.stamp_ms)}초 전 것이라 기준 ${num(f.world / 1000)}초를 넘었어요. 낡은 화면으로는 움직이지 않아요.`;
    case 'stale:proposal':
      return `${secs(now - proposal.timestamp_ms)}초 전에 만든 명령이에요(기준 ${num(f.proposal / 1000)}초). 녹화된 명령을 다시 보낸 것일 수 있어요.`;
    case 'invalid:timestamp': {
      const pAhead = proposal.timestamp_ms - now;
      const which = pAhead > f.future ? '명령' : '인식';
      const ahead = which === '명령' ? pAhead : world.stamp_ms - now;
      return `${which}에 신뢰 시계보다 ${secs(ahead)}초 뒤 시각이 찍혀 있어요. 위조로 보고 막았어요.`;
    }
    case 'invalid:proposal':
      return '명령 값이 잘못됐어요 (숫자가 아니거나 음수 속도).';
    case 'invalid:world':
      return '센서 데이터가 잘못됐어요 (신뢰도가 0~1 밖 등).';
    default:
      return explainFired(name, policy).text;
  }
}

/** Korean text for an action (null → not executed). */
export function actionText(a) {
  if (!a) return '실행 안 함';
  switch (a.type) {
    case 'move_to': return `${pt(a.goal)} 이동 ${num(a.speed)} m/s`;
    case 'grasp': return `${objectName(a.object)} 집기 ${pt(a.at)}`;
    case 'place': return `놓기 ${pt(a.at)}`;
    case 'stop': return '정지';
    default: return JSON.stringify(a);
  }
}

/** Spoken form of the command, e.g. "AI 모델이 (2.5, 5.2)로 0.8 m/s 이동을 요청". */
export function commandSpoken(p) {
  const s = sourceName(p.source);
  const who = `${s}${josa(s, '이', '가')}`;
  const a = p.action;
  switch (a?.type) {
    case 'move_to': return `${who} ${pt(a.goal)}로 ${num(a.speed)} m/s 이동을 요청`;
    case 'grasp': return `${who} ${pt(a.at)}의 ${objectName(a.object)} 집기를 요청`;
    case 'place': return `${who} ${pt(a.at)}에 놓기를 요청`;
    case 'stop': return `${who} 정지를 요청`;
    default: return `${who} 알 수 없는 동작을 요청`;
  }
}

/** Target of an action on the map (null for stop). */
export function actionTarget(a) {
  if (!a) return null;
  if (a.type === 'move_to') return a.goal;
  if (a.type === 'grasp' || a.type === 'place') return a.at;
  return null;
}

/** Everything the caption and live region say after a verdict. */
export function verdictCopy(decision, ctx) {
  const h = headline(decision, ctx.policy);
  const others = (decision.fired ?? []).filter((n) => n !== h).length;
  return {
    head: h,
    label: label(h, ctx),
    sentence: sentence(h, ctx),
    others,
  };
}

/** Kind of a fired name for the detail-sheet badge (builtin / zone / rule / unknown). */
export function badgeKind(name, policy) {
  if (name.startsWith('zone:')) return 'zone';
  if (name.includes(':')) return 'builtin';
  return rule(policy, name) ? 'rule' : 'unknown';
}

/** Short "name limit" for the brake stack chip, e.g. "복도 0.3". Null when not a cap. */
export function capShort(name, policy) {
  if (kindOf(name, policy) !== 'cap') return null;
  const lim = capLimit(name, policy);
  const n = lim == null ? '' : ` ${num(lim)}`;
  if (name === 'envelope:max_speed') return `최고 속도${n}`;
  if (name === 'mode:caution') return `주의 모드${n}`;
  if (name.startsWith('zone:')) return `${zoneName(name.slice(5))}${n}`;
  if (name === 'slow-near-human') return `사람 곁${n}`;
  if (name === 'low-confidence') return `흐린 인식${n}`;
  return `${name}${n}`;
}

/** Sentence for a yun/jeol/bul outro, picked only from the recorded verdict. */
export function outroFor(card, verdict) {
  return card.outro?.[verdict] ?? GENERIC_OUTRO[verdict];
}

export const STRICT = { bul: 3, jeol: 2, yun: 1 };
