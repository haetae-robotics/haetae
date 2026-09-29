const $ = (id) => document.getElementById(id);
const canvas = $('visual');
const ctx = canvas.getContext('2d');
let telemetry = null;
let lastCommand = null;
let armCancelling = false;
let complete = false;
let lastTelemetryAt = 0;

function fmt(value, digits = 3) {
  return Number(value).toFixed(digits);
}

function badge(state, label) {
  $('connection').dataset.state = state;
  $('connection').textContent = label;
}

function addEvent(label, simMs) {
  const item = document.createElement('li');
  const stamp = document.createElement('time');
  stamp.textContent = simMs ? `${fmt(simMs / 1000, 2)} s` : '—';
  item.append(stamp, document.createTextNode(label));
  $('events').prepend(item);
  while ($('events').children.length > 8) $('events').lastChild.remove();
}

function draw() {
  const { width: w, height: h } = canvas;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = '#f8fbf8';
  ctx.fillRect(0, 0, w, h);
  ctx.font = '16px system-ui, sans-serif';
  if (!telemetry) {
    ctx.fillStyle = '#74847b';
    ctx.fillText('Gazebo 측정값을 기다리는 중입니다', 50, 190);
    return;
  }
  const x = Math.max(0, Math.min(1, (telemetry.x - 5.0) / 0.18));
  const robotX = 86 + x * (w - 172);
  const roadY = 150;
  ctx.strokeStyle = '#d1dfd5'; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(48, roadY); ctx.lineTo(w - 48, roadY); ctx.stroke();
  ctx.fillStyle = '#637770';
  ctx.fillText('Gazebo 바퀴 위치 · x축 확대', 32, 40);
  ctx.fillText('출발 x = 5.000 m', 48, roadY + 62);
  if (telemetry.humans.length) {
    const human = telemetry.humans[0];
    const hx = human.pos.x <= 5.18 ?
      86 + Math.max(0, (human.pos.x - 5.0) / 0.18) * (w - 172) : w - 95;
    ctx.fillStyle = '#ba503b';
    ctx.beginPath(); ctx.arc(hx, roadY - 43, 13, 0, Math.PI * 2); ctx.fill();
    ctx.fillRect(hx - 12, roadY - 30, 24, 38);
    ctx.fillStyle = '#8b4232';
    ctx.fillText('사람 보고', Math.min(hx - 30, w - 110), roadY - 68);
  }
  ctx.fillStyle = '#cad9cf';
  ctx.fillRect(robotX - 23, roadY - 26, 12, 52);
  ctx.fillRect(robotX + 11, roadY - 26, 12, 52);
  ctx.fillStyle = '#206d5b';
  ctx.fillRect(robotX - 29, roadY - 20, 58, 40);
  ctx.fillStyle = '#fff';
  ctx.fillText('로봇', robotX - 18, roadY + 6);
  ctx.fillStyle = '#28443a';
  ctx.fillText(`x = ${fmt(telemetry.x)} m`, Math.max(36, robotX - 55), roadY + 88);

  ctx.fillStyle = '#637770';
  ctx.fillText('실제 바퀴 속도', 32, 272);
  ctx.fillStyle = '#e5eee8';
  ctx.fillRect(210, 251, w - 280, 27);
  ctx.fillStyle = '#2a8164';
  ctx.fillRect(210, 251, Math.min(1, Math.abs(telemetry.speed) / 0.2) * (w - 280), 27);
  ctx.fillStyle = '#28443a';
  ctx.fillText(`${fmt(Math.abs(telemetry.speed))} m/s`, 210, 308);

  ctx.fillStyle = '#637770';
  ctx.fillText('실제 팔 관절', 32, 355);
  ctx.fillStyle = '#e5eee8';
  ctx.fillRect(210, 337, w - 280, 20);
  ctx.fillStyle = '#638aaf';
  ctx.fillRect(210, 337, Math.min(1, Math.abs(telemetry.joint) / 0.12) * (w - 280), 20);
  ctx.fillStyle = '#28443a';
  ctx.fillText(`${fmt(telemetry.joint, 4)} rad`, 210, 385);
}

draw();
const stream = new EventSource('/events');
stream.onopen = () => {
  if (!complete) badge('live', '연결됨 · Gazebo 준비 중');
};
stream.onerror = () => {
  if (!complete) {
    badge('offline', '연결 끊김 · 재연결 중');
    $('detail').textContent = '실험 프로세스와의 연결을 확인하고 있습니다.';
  }
};
stream.onmessage = (event) => {
  let row;
  try { row = JSON.parse(event.data); } catch { return; }
  if (row.kind === 'telemetry') {
    telemetry = row;
    lastTelemetryAt = Date.now();
    badge('live', '● 실시간');
    $('sim-time').textContent = `${fmt(row.sim_ms / 1000, 2)} s`;
    $('speed').textContent = `${fmt(Math.abs(row.speed))} m/s`;
    $('joint').textContent = `${fmt(row.joint, 4)} rad`;
    $('human').textContent = row.humans.length ? '있음' : '없음';
    draw();
  } else if (row.kind === 'base_command') {
    $('command').textContent = `${fmt(Math.abs(row.linear))} m/s`;
    if (lastCommand > 0.01 && Math.abs(row.linear) < 0.001) {
      addEvent('ROS 컨트롤러에 바퀴 0속도 전달', row.sim_ms);
    }
    lastCommand = Math.abs(row.linear);
  } else if (row.kind === 'phase') {
    $('phase').textContent = row.label;
    $('detail').textContent = 'Gazebo와 ROS가 현재 이 장면을 실행하고 있습니다.';
    addEvent(row.label, row.sim_ms);
  } else if (row.kind === 'decision') {
    if (row.verdict === 'bul') {
      const reason = row.fired?.join(', ') || '안전 정책';
      addEvent(`해태 차단: ${reason}`, row.sim_ms);
    }
  } else if (row.kind === 'state') {
    if (row.arm_cancelling && !armCancelling) {
      addEvent('ROS 팔 동작 취소 요청', row.sim_ms);
    }
    armCancelling = Boolean(row.arm_cancelling);
  } else if (row.kind === 'result') {
    complete = true;
    const passed = row.result?.ok && row.result?.arm_out_of_bounds_denied &&
      row.result?.sillok_incident_snapshot_fully_sealed;
    badge(passed ? 'complete' : 'offline', passed ? '실험 완료' : '실험 결과 확인');
    $('phase').textContent = passed ? '실시간 실험 완료' : '실험이 끝났습니다';
    $('detail').textContent = passed ?
      '바퀴 정지, 팔 거부·취소, 게이트 종료 후 데드맨 정지가 검증됐습니다.' :
      '결과 파일에서 세부 검증 상태를 확인해 주세요.';
    addEvent(passed ? '검증 결과 통과' : '검증 결과 확인 필요', row.sim_ms);
    stream.close();
  }
};

setInterval(() => {
  if (!complete && telemetry && Date.now() - lastTelemetryAt > 3000) {
    badge('offline', 'Gazebo 측정 지연');
  }
}, 1000);
