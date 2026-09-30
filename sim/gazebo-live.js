import { createGazeboScene } from './gazebo-scene.js';

const $ = (id) => document.getElementById(id);
const canvas = $('visual');
let sceneView = null;
let renderError = '';
try {
  sceneView = createGazeboScene(canvas);
  sceneView.ready?.catch(() => {
    renderError = '로봇 모델을 불러오지 못했습니다';
    $('scene-label').textContent = renderError;
    $('scene-note').textContent = '페이지를 새로고침하거나 Gazebo 원본을 확인해 주세요.';
  });
}
catch { renderError = '이 브라우저에서 3D 렌더링을 시작하지 못했습니다'; $('scene-label').textContent = renderError; }
let telemetry = null;
let lastCommand = null;
let armCancelling = false;
let complete = false;
let failed = false;
let streamConnected = false;
let lastTelemetryAt = 0;
let ready = false;
let started = false;
let worldAclBlocked = null;
let currentCheckpoint = null;
let stepThrough = false;
let manualStart = false;
let stage = 0;
let stageOrder = [1, 2, 3, 4, 5, 6];
const stageNames = ['준비', '사람 접근', '명령 제한', '팔 동작 취소', '접근 권한', '연결 종료', '명령 재전송'];
function showStage(number) {
  stage = number;
  $('stage-track').style.gridTemplateColumns = `repeat(${stageOrder.length}, minmax(0, 1fr))`;
  const position = stageOrder.indexOf(number);
  $('stage-label').textContent = failed ? '실험 중단' : complete ? '실험 완료' : number ?
    `${position + 1} / ${stageOrder.length} · ${stageNames[number]}` : `준비 / ${stageOrder.length}개 장면`;
  for (let index = 1; index <= 6; index++) {
    const order = stageOrder.indexOf(index);
    $('stage-' + index).hidden = order < 0;
    $('stage-' + index).style.order = order;
    $('stage-number-' + index).textContent = String(order + 1).padStart(2, '0');
    $('stage-' + index).dataset.state = complete || order < position ? 'visited' : index === number ? 'active' : 'pending';
    $('stage-' + index).setAttribute('aria-current', !complete && index === number ? 'step' : 'false');
  }
}

const phaseCopy = {
  '실험 준비 완료': ['로봇 준비 완료', '시뮬레이터가 명령을 기다리고 있습니다.', '화면이 뜨면 시작 버튼을 눌러 주세요.'],
  '3D 화면 준비 · 시작 버튼을 누르세요': ['시작할 준비 완료', '로봇이 보이면 시작해 주세요.', '시작 후 로봇이 움직이고, 위험 보고가 들어오면 해태가 반응합니다.'],
  'AI 바퀴 이동 명령': ['로봇이 움직입니다', 'AI가 바퀴에 이동 명령을 제안했습니다.', '잠시 뒤 사람이 옆에서 걸어 접근합니다. 가까워질 때 정지하는지 확인하세요.'],
  '사람이 걸어 접근합니다': ['사람이 걸어 접근합니다', '멀리 있는 사람의 위치 보고가 계속 갱신됩니다.', '로봇은 허용된 이동을 이어가다가 근접 조건에 걸리면 멈춥니다.'],
  '사람 등장': ['사람 근접 보고', '테스트에서 로봇 앞에 사람이 있다고 보고했습니다.', '해태의 판정과 바퀴의 정지 명령을 지켜보세요.'],
  '해태가 바퀴 0속도 명령': ['정지 명령 전달', '해태가 바퀴 컨트롤러에 속도 0을 보냈습니다.', '이제 실제 Gazebo 바퀴 속도가 0으로 줄어드는지 확인합니다.'],
  'Gazebo 바퀴 정지': ['바퀴가 멈췄습니다', 'Gazebo 측정 속도가 정지 기준 아래로 내려갔습니다.', '다음은 팔에 허용 범위를 넘는 명령을 보내 봅니다.'],
  'AI 팔 범위 초과 명령': ['위험한 팔 명령', 'AI가 허용 범위를 넘는 팔 동작을 제안했습니다.', '해태가 팔 명령을 거부하는지 확인합니다.'],
  '해태가 팔 명령 거부': ['팔 명령 거부', '팔 관절이 움직이지 않았습니다.', '다음은 정상 팔 동작 중 위험 보고가 들어오는 경우입니다.'],
  'AI 정상 팔 이동 명령': ['팔이 움직입니다', '허용 범위의 팔 동작이 시작됐습니다.', '사람이 감지되는 순간 팔을 중단하는 정책을 확인합니다.'],
  '사람 접근 · 팔 중단 시험': ['사람이 접근합니다', '팔 정책은 거리에 관계없이 사람 보고가 있으면 동작을 중단합니다.', '사람의 첫 위치 보고에 팔이 취소되는지 확인하세요.'],
  '사람 감지, 팔 취소 요청': ['사람 감지 · 팔 중단', '사람 보고가 들어와 진행 중인 팔 동작을 취소했습니다.', '사람은 계속 걸어 접근하며, 팔은 정지 상태를 유지해야 합니다.'],
  '사람이 걸어 나갑니다': ['사람이 걸어 나갑니다', '확인을 마친 사람이 로봇에서 멀어집니다.', '이동이 끝나면 사람 보고를 해제하고 다음 시험을 준비합니다.'],
  '사람 등장, 팔 취소 요청': ['팔 동작 취소 요청', '해태가 진행 중인 팔 동작을 취소하도록 요청했습니다.', '팔 관절이 더 움직이지 않는지 확인합니다.'],
  'Gazebo 팔 관절 정지': ['팔이 멈췄습니다', 'Gazebo 관절 측정값이 더 움직이지 않았습니다.', '정지한 팔 관절과 취소 판정을 확인하세요.'],
  '두 번째 바퀴 이동': ['로봇이 다시 움직입니다', '연결 끊김 시험을 위해 바퀴를 다시 움직입니다.', '해태 프로세스가 꺼진 뒤 컨트롤러가 멈추는지 확인합니다.'],
  '해태 프로세스 강제 종료': ['해태 연결 끊김 시험', '게이트 프로세스를 강제로 종료했습니다.', '바퀴 컨트롤러 자체의 정지 기능을 확인합니다.'],
  '컨트롤러 데드맨으로 바퀴 정지': ['연결이 끊겨도 정지', '명령이 끊기자 컨트롤러가 바퀴를 멈췄습니다.', '컨트롤러의 정지 결과를 확인하세요.'],
  '외부 노드 바퀴 명령 공격': ['바퀴 우회 공격 시험', '공격자 권한의 ROS 노드가 제어 명령을 보냅니다.', '공격 명령이 제어기에 도달했는지 확인합니다.'],
  '서명된 명령 재전송 공격': ['재전송 공격 시험', '실제 해태 엔진에 이미 승인된 명령을 다시 보냅니다.', '엔진이 명령을 거부하고 출력 속도를 0으로 만드는지 확인합니다.']
};

function renderRunControl() {
  const button = $('start-simulation');
  button.hidden = !manualStart && !stepThrough;
  button.disabled = failed || complete || !streamConnected || (!currentCheckpoint && (!ready || started));
  button.textContent = failed ? '시뮬레이션 중단' : complete ? '시뮬레이션 완료' : currentCheckpoint ?
    '다음 단계 → ' + currentCheckpoint.next_label : started ? '장면 진행 중…' :
      ready ? '▶ 시뮬레이션 시작' : '로봇 준비 중…';
  $('scene-hold').hidden = failed || !currentCheckpoint;
}

function fmt(value, digits = 3) {
  return Number(value).toFixed(digits);
}

function badge(state, label) {
  $('connection').dataset.state = state;
  $('connection').textContent = label;
}

function verdict(kind, label) {
  $('verdict').dataset.kind = kind;
  $('verdict').textContent = label;
}

function addEvent(label, simMs) {
  const item = document.createElement('li');
  const stamp = document.createElement('time');
  stamp.textContent = simMs ? `${fmt(simMs / 1000, 2)} s` : '—';
  item.append(stamp, document.createTextNode(label));
  $('events').prepend(item);
  while ($('events').children.length > 6) $('events').lastChild.remove();
}

function sceneAlert(label, kind = 'block') {
  $('scene-alert').textContent = label || '';
  $('scene-alert').dataset.kind = kind;
  $('scene-alert').hidden = !label;
}

function attackStatus(name, state, label, detail) {
  $('attack-' + name).dataset.state = state;
  $('attack-' + name + '-state').textContent = label;
  $('attack-' + name + '-detail').textContent = detail;
}

function chooseViewer(mode) {
  const original = mode === 'gazebo';
  canvas.hidden = original;
  $('gazebo-gui').hidden = !original;
  $('show-gazebo').setAttribute('aria-pressed', String(original));
  $('show-reconstruction').setAttribute('aria-pressed', String(!original));
  $('scene-label').textContent = original
    ? '실제 Gazebo 시뮬레이터 창'
    : renderError || 'Gazebo 측정값 · 3D 재구성';
  $('scene-note').textContent = original
    ? '실제 Gazebo 창입니다. 사람 보고 표시는 라이브 3D에서 확인할 수 있습니다.'
    : 'Gazebo 위치·관절 측정값을 재구성합니다. 사람은 테스트 보고 표시입니다. 드래그로 회전 · 스크롤로 확대';
}

$('show-gazebo').addEventListener('click', () => chooseViewer('gazebo'));
$('show-reconstruction').addEventListener('click', () => chooseViewer('reconstruction'));
$('start-simulation').addEventListener('click', async () => {
  if (failed || !streamConnected) return;
  const button = $('start-simulation');
  const checkpoint = currentCheckpoint;
  button.disabled = true;
  button.textContent = checkpoint ? '다음 단계 요청 중…' : '시작 요청 중…';
  try {
    const response = await fetch(checkpoint ? '/advance' : '/start', {
      method: 'POST',
      headers: checkpoint ? { 'X-Checkpoint-Token': checkpoint.token } : {}
    });
    if (!response.ok) throw new Error('start failed');
    started = true;
    if (checkpoint && currentCheckpoint?.token === checkpoint.token) currentCheckpoint = null;
    renderRunControl();
    if (!failed && !currentCheckpoint) $('start-help').textContent = stepThrough ?
      '이 장면이 끝나면 기다립니다. 확인 후 다음 단계로 넘어가세요.' :
      '로봇의 움직임과 오른쪽 설명을 함께 보세요.';
  } catch {
    renderRunControl();
    if (!failed) $('start-help').textContent = '진행 요청이 실패했습니다. 연결을 확인한 뒤 다시 눌러 주세요.';
  }
});
fetch('/viewer-config', { cache: 'no-store' })
  .then((response) => response.json())
  .then((config) => {
    stepThrough = Boolean(config.step_through);
    manualStart = Boolean(config.manual_start);
    stageOrder = config.attack_probes ? config.secured_gazebo ? [1, 2, 3, 4, 5, 6] :
      [1, 2, 3, 5, 4, 6] : [1, 2, 3, 5];
    showStage(stage);
    $('attack-lab').hidden = !config.attack_probes;
    $('attack-scope').textContent = config.secured_gazebo ?
      '①·②는 화면 속 Gazebo ROS 그래프에서 제한된 공격자 권한으로 시험합니다. Gazebo 내부 통신과 호스트 계정은 신뢰합니다. ③과 서명 변조 시험은 별도 해태 엔진에서 실행합니다.' :
      '①·②는 별도 보안 ROS 그래프의 테스트 제어기로 시험합니다. ③은 별도 해태 엔진에서 실행합니다. 화면 속 Gazebo 그래프 전체의 권한 검증은 아닙니다.';
    renderRunControl();
    if (stepThrough && !failed) {
      $('step-three').textContent = '장면 확인 후 다음 단계 누르기';
      $('start-help').textContent = currentCheckpoint ? '천천히 확인하세요. 다음 단계는 버튼을 누르면 시작됩니다.' :
        '한 장면씩 진행합니다. 결과를 확인한 뒤 다음 단계로 넘어가세요.';
    }
    if (config.manual_start) {
      if (!ready && !started && !complete && !failed) $('detail').textContent = 'Gazebo 로봇을 준비하고 있습니다.';
    } else {
      $('step-two').textContent = '연결되면 자동 시작';
      $('start-help').textContent = '브라우저가 연결되면 자동으로 시작합니다.';
    }
    if (!config.gazebo_gui) return;
    $('viewer-tabs').hidden = false;
    $('gazebo-gui').src = 'http://' + window.location.hostname +
      ':6080/vnc.html?autoconnect=true&resize=scale&view_only=true';
    chooseViewer(renderError ? 'gazebo' : 'reconstruction');
  })
  .catch(() => {});

const stream = new EventSource('/events');
stream.onopen = () => {
  streamConnected = true;
  renderRunControl();
  if (!complete && !failed) badge('live', '연결됨 · Gazebo 준비 중');
};
stream.onerror = () => {
  streamConnected = false;
  renderRunControl();
  if (!complete && !failed) {
    badge('offline', '연결 끊김 · 재연결 중');
    $('detail').textContent = '실험 프로세스와의 연결을 확인하고 있습니다.';
    $('next-step').textContent = '터미널에 오류가 있는지 확인해 주세요. 실행이 끝났다면 명령을 다시 시작하면 됩니다.';
  }
};
stream.onmessage = (event) => {
  let row;
  try { row = JSON.parse(event.data); } catch { return; }
  if (failed) return;
  if (row.kind === 'telemetry') {
    telemetry = row;
    lastTelemetryAt = Date.now();
    badge('live', '실시간 연결');
    $('sim-time').textContent = `${fmt(row.sim_ms / 1000, 2)} s`;
    $('speed').textContent = `${fmt(Math.abs(row.speed))} m/s`;
    $('joint').textContent = `${fmt(row.joint, 4)} rad`;
    const human = row.humans[0];
    $('human').textContent = human ? `${fmt(Math.hypot(human.pos.x - row.x, human.pos.y - row.y), 2)} m` : '없음';
    sceneView?.update(row);
  } else if (row.kind === 'base_command') {
    $('command').textContent = `${fmt(Math.abs(row.linear))} m/s`;
    if (lastCommand > 0.01 && Math.abs(row.linear) < 0.001) {
      addEvent('바퀴에 정지 명령 전달', row.sim_ms);
      sceneAlert('해태 → 바퀴 정지 명령', 'command');
    }
    lastCommand = Math.abs(row.linear);
  } else if (row.kind === 'phase') {
    const phaseStages = { 'AI 바퀴 이동 명령': 1, 'AI 팔 범위 초과 명령': 2,
      'AI 정상 팔 이동 명령': 3, '외부 노드 바퀴 명령 공격': 4,
      '두 번째 바퀴 이동': 5, '서명된 명령 재전송 공격': 6 };
    if (phaseStages[row.label]) showStage(phaseStages[row.label]);
    const copy = phaseCopy[row.label] || [row.label, 'Gazebo에서 실험을 진행하고 있습니다.', '로봇 장면과 판정을 함께 확인해 주세요.'];
    $('phase').textContent = copy[0];
    $('detail').textContent = row.detail || copy[1];
    $('next-step').textContent = copy[2];
    addEvent(copy[0], row.sim_ms);
    if (row.label === '3D 화면 준비 · 시작 버튼을 누르세요') {
      ready = true;
      renderRunControl();
    }
    if (row.label.startsWith('AI ') || row.label === '두 번째 바퀴 이동') {
      started = true;
      renderRunControl();
      sceneAlert(null);
      sceneView?.setBlocked(false);
      verdict('waiting', '판정 관찰 중');
    }
    if (row.label === '사람이 걸어 접근합니다' || row.label === '사람 접근 · 팔 중단 시험')
      sceneAlert('테스트 사람 접근 중', 'report');
    if (row.label === '사람이 걸어 나갑니다') sceneAlert('테스트 사람이 이동합니다', 'report');
    if (row.label === '사람 등장' || row.label === '사람 등장, 팔 취소 요청')
      sceneAlert('사람 근접 보고', 'report');
    if (row.label === 'Gazebo 바퀴 정지' || row.label === 'Gazebo 팔 관절 정지')
      sceneAlert(copy[0], 'measured');
    if (row.label === '해태 프로세스 강제 종료') {
      sceneAlert('해태 연결 끊김 시험', 'report');
      sceneView?.setBlocked(false);
      verdict('waiting', '컨트롤러 정지 관찰 중');
    }
    if (row.label === '컨트롤러 데드맨으로 바퀴 정지')
      sceneAlert(copy[0], 'measured');
    if (row.label === '사람 보고 해제') sceneAlert('사람 근접 보고 해제', 'report');
    if (row.label === '다음 장면 준비') sceneAlert(null);
  } else if (row.kind === 'checkpoint') {
    if (row.waiting) {
      const checkpointStages = { '바퀴 정지 장면': 1, '팔 명령 거부 장면': 2,
        '팔 정지 장면': 3, '외부 노드 공격 결과': 4, '연결 끊김 뒤 정지 장면': 5 };
      if (checkpointStages[row.label]) showStage(checkpointStages[row.label]);
      currentCheckpoint = row;
      started = true;
      $('phase').textContent = row.label;
      $('detail').textContent = row.detail;
      $('next-step').textContent = '현재 장면을 유지하고 있습니다. 다음: ' + row.next_label;
      $('start-help').textContent = '천천히 확인하세요. 다음 단계는 버튼을 누르면 시작됩니다.';
      addEvent('장면 확인 대기', row.sim_ms);
    } else if (currentCheckpoint?.token === row.token) {
      currentCheckpoint = null;
    }
    renderRunControl();
  } else if (row.kind === 'decision') {
    if (row.verdict === 'bul') {
      addEvent('해태가 위험한 명령 차단', row.sim_ms);
      sceneAlert('해태가 명령 차단');
      verdict('block', '차단 · 위험한 명령 중단');
      sceneView?.setBlocked(true);
    }
  } else if (row.kind === 'attack_result') {
    const state = row.blocked ? 'blocked' : 'failed';
    if (row.attack === 'direct') {
      attackStatus('direct', state, row.blocked ? '접근 차단 확인' : '검증 실패',
        row.blocked ? row.scope === 'gazebo_sros2_graph' ?
          '공격자 명령이 화면 속 Gazebo 바퀴 제어기에 도달하지 않았습니다.' :
          '공격자 명령이 별도 ROS 테스트 제어기에 도달하지 않았습니다.' :
          '권한 시험에 실패했습니다. 결과 파일을 확인해 주세요.');
    } else if (row.attack === 'world') {
      worldAclBlocked = Boolean(row.blocked);
      attackStatus('world', state, row.blocked ? 'ROS 접근 차단 확인' : '검증 실패',
        row.blocked ? row.scope === 'gazebo_sros2_graph' ?
          '공격자의 가짜 사람 정보가 화면 속 Gazebo의 신뢰된 수신기에 도달하지 않았습니다. 서명 검사도 진행합니다.' :
          '공격자의 가짜 사람 정보가 별도 ROS 테스트 수신기에 도달하지 않았습니다. 서명 검사도 진행합니다.' :
          '가짜 정보 접근 권한 시험에 실패했습니다.');
    } else if (row.attack === 'signature') {
      const blocked = worldAclBlocked && row.blocked;
      attackStatus('world', blocked ? 'blocked' : 'failed',
        blocked ? '접근·서명 모두 차단' : '검증 실패',
        blocked ? 'ROS 접근 권한이 위조 게시를 막았고, 별도 해태 엔진은 변조된 서명을 거부했습니다.' :
          '접근 권한 또는 서명 검증에 실패했습니다.');
    } else if (row.attack === 'replay') {
      attackStatus('replay', state, row.blocked ? '재전송 거부 확인' : '검증 실패',
        row.blocked ? '같은 서명 명령을 다시 보냈을 때 해태 엔진의 출력 속도는 0이었습니다.' :
          '재전송 검증에 실패했습니다. 결과 파일을 확인해 주세요.');
    }
    if (!row.blocked) verdict('waiting', '공격 검증 실패');
    addEvent(row.blocked ? '공격 차단 확인' : '공격 검증 실패', row.sim_ms);
  } else if (row.kind === 'state') {
    if (row.arm_cancelling && !armCancelling) {
      addEvent('팔 동작 취소 요청', row.sim_ms);
      sceneAlert('해태 → 팔 동작 취소');
      verdict('block', '취소 · 팔 동작 중단');
      sceneView?.setBlocked(true);
    }
    armCancelling = Boolean(row.arm_cancelling);
  } else if (row.kind === 'error') {
    failed = true;
    currentCheckpoint = null;
    badge('offline', '시뮬레이션 중단');
    $('phase').textContent = row.label;
    $('detail').textContent = row.detail;
    $('next-step').textContent = '실행 명령을 다시 시작한 뒤 이 페이지를 새로고침해 주세요.';
    $('start-help').textContent = '실행이 종료되어 다음 단계로 진행할 수 없습니다.';
    sceneAlert('시뮬레이션 중단', 'report');
    addEvent(row.label, row.sim_ms);
    showStage(stage);
    renderRunControl();
    stream.close();
  } else if (row.kind === 'result') {
    complete = true;
    currentCheckpoint = null;
    renderRunControl();
    showStage(stage);
    const passed = row.result?.ok && row.result?.arm_out_of_bounds_denied &&
      row.result?.sillok_incident_snapshot_fully_sealed;
    badge(passed ? 'complete' : 'offline', passed ? '실험 완료' : '실험 결과 확인');
    $('phase').textContent = passed ? '실시간 실험 완료' : '실험이 끝났습니다';
    $('detail').textContent = passed ?
      Object.keys(row.result.attack_probes || {}).length ?
        'Gazebo 정지 동작과 공격 검증이 완료됐습니다. 아래 적용 범위를 확인해 주세요.' :
        '바퀴 정지, 팔 거부·취소, 게이트 종료 후 데드맨 정지가 검증됐습니다.' :
      '결과 파일에서 세부 검증 상태를 확인해 주세요.';
    addEvent(passed ? '검증 결과 통과' : '검증 결과 확인 필요', row.sim_ms);
    verdict(passed ? 'done' : 'waiting', passed ? '검증 통과' : '결과 확인 필요');
    $('next-step').textContent = '다시 보려면 터미널에서 실행 명령을 다시 시작하세요.';
    stream.close();
  }
};

setInterval(() => {
  if (!complete && !failed && streamConnected && telemetry && Date.now() - lastTelemetryAt > 3000) {
    badge('offline', 'Gazebo 측정 지연');
  }
}, 1000);
