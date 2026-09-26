// 사초 (sacho) event log: newest first, capped, sim-time stamps.

import { simTime, VERDICTS } from './explain.js';

const MAX_ENTRIES = 300;

const KIND_KO = {
  proposal: '제안',
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
  }

  /**
   * Add an entry.
   * @param {object} e { t: sim ms, kind, text, detail?, verdict? }
   */
  add({ t, kind, text, detail, verdict }) {
    const li = document.createElement('li');

    const time = document.createElement('span');
    time.className = 'log-time';
    time.textContent = simTime(t);
    li.append(time);

    const tag = document.createElement('span');
    tag.className = 'log-kind';
    tag.textContent = KIND_KO[kind] ?? kind;
    li.append(tag);

    if (verdict && VERDICTS[verdict]) {
      const chip = document.createElement('span');
      chip.className = `vchip vchip-${verdict}`;
      chip.textContent = `${VERDICTS[verdict].glyph} ${verdict}`;
      li.append(chip, ' ');
    }

    li.append(document.createTextNode(text));

    if (detail) {
      const d = document.createElement('span');
      d.className = 'log-detail';
      d.textContent = detail;
      li.append(d);
    }

    this.list.prepend(li);
    while (this.list.children.length > MAX_ENTRIES) this.list.lastElementChild.remove();
  }

  clear() {
    this.list.replaceChildren();
  }
}
