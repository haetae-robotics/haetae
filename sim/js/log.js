// 사초 (sacho) event log: newest first, capped, sim-time stamps.
// Every judge() call is logged with its Decision exactly as returned.
// Unfiltered comparison runs are logged as `comparison`, never as decisions.

import { simTime, VERDICTS } from './explain.js';

const MAX_ENTRIES = 300;
const MAX_RECORDS = 2000; // judge-call JSONL kept for "JSONL 복사" (kiosk loops forever)

const KIND_KO = {
  proposal: '판정',
  comparison: '비교 재생',
  fault: '고장',
  mode: '모드',
  world: '인식',
  policy: '정책',
  scenario: '시나리오',
  error: '오류',
  exec: '실행기',
};

export class EventLog {
  constructor(listEl) {
    this.list = listEl;
    this.records = []; // judge calls, oldest first, for "JSONL 복사"
  }

  /**
   * Add an entry.
   * @param {object} e { t: sim ms, kind, text, detail?, verdict?, json? }
   *   json: an object shown in an expandable <details> (e.g. the Decision).
   */
  add({ t, kind, text, detail, verdict, json }) {
    const li = document.createElement('li');
    li.className = `log-${kind}`;

    const time = document.createElement('span');
    time.className = 'log-time';
    time.textContent = simTime(t);
    li.append(time);

    const tag = document.createElement('span');
    tag.className = 'log-kind';
    tag.textContent = kind === 'comparison' ? 'comparison:unfiltered' : (KIND_KO[kind] ?? kind);
    li.append(tag);

    if (verdict && VERDICTS[verdict]) {
      const chip = document.createElement('span');
      chip.className = `vchip vchip-${verdict}`;
      chip.textContent = `${VERDICTS[verdict].sym} ${VERDICTS[verdict].ko} · ${verdict}`;
      li.append(chip, ' ');
    }

    li.append(document.createTextNode(text));

    if (detail) {
      const d = document.createElement('span');
      d.className = 'log-detail';
      d.textContent = detail;
      li.append(d);
    }

    if (json) {
      const det = document.createElement('details');
      const sum = document.createElement('summary');
      sum.textContent = 'JSON';
      const pre = document.createElement('pre');
      pre.textContent = JSON.stringify(json, null, 2);
      det.append(sum, pre);
      li.append(det);
      if (kind === 'proposal') {
        this.records.push(json);
        if (this.records.length > MAX_RECORDS) this.records.splice(0, this.records.length - MAX_RECORDS);
      }
    }

    this.list.prepend(li);
    while (this.list.children.length > MAX_ENTRIES) this.list.lastElementChild.remove();
  }

  /** Logged judge calls as JSON Lines. */
  jsonl() {
    return this.records.map((r) => JSON.stringify(r)).join('\n');
  }

  clear() {
    this.list.replaceChildren();
    this.records = [];
  }
}
