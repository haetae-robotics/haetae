const $ = (id) => document.getElementById(id);
const canvas = $('visual');
const ctx = canvas.getContext('2d');
const fmt = (n, digits = 3) => Number(n).toFixed(digits);

try {
  const [logResponse, resultResponse] = await Promise.all([
    fetch('./evidence/gazebo-snapshot.jsonl'),
    fetch('./evidence/gazebo-result.json'),
  ]);
  if (!logResponse.ok || !resultResponse.ok) throw new Error('기록 파일을 읽지 못했습니다.');
  const rows = (await logResponse.text()).trim().split('\n').map((line) => JSON.parse(line));
  const result = await resultResponse.json();
  if (!result.ok || !result.sillok_incident_snapshot_fully_sealed ||
      rows.at(-1)?.kind !== 'seal') throw new Error('완료된 기록의 형식이 아닙니다. 서명 검증은 CLI로 실행하세요.');

  const worlds = rows.filter((row) => row.kind === 'world');
  const event = (predicate, name) => {
    const row = rows.find(predicate);
    if (!row) throw new Error(`${name} 사건이 기록에 없습니다.`);
    return row;
  };
  const baseAllowed = event((r) => r.kind === 'decision' &&
    r.payload.action?.type === 'velocity' && r.payload.verdict === 'yun', '바퀴 허용');
  const baseRevoked = event((r) => r.kind === 'revoke' &&
    r.payload.fired?.includes('person'), '사람 등장 후 바퀴 차단');
  const baseStopped = worlds.find((r) => r.ts_ms > baseRevoked.ts_ms &&
    Math.abs(r.payload.robot.twist.linear) < 0.03);
  if (!baseStopped) throw new Error('Gazebo의 바퀴 정지 측정값이 없습니다.');
  const armDenied = event((r) => r.kind === 'decision' &&
    r.payload.fired?.includes('envelope:arm-position'), '팔 범위 초과 차단');
  const armAllowed = event((r) => r.kind === 'decision' &&
    r.payload.action?.type === 'joint_trajectory' && r.payload.verdict === 'yun', '팔 허용');
  const armRevoked = event((r) => r.kind === 'revoke' &&
    r.payload.reason === 'arm:world-changed', '팔 취소');

  const jointName = result.arm_joints?.[0] ?? worlds[0].payload.robot.joints?.[0]?.name;
  const measuredJoint = (world) => world.robot.joints.find((j) => j.name === jointName)?.position;
  const armTarget = armAllowed.payload.action.points.at(-1).positions[0];
  const scenes = {
    base: {
      start: baseAllowed.ts_ms - 60, end: baseStopped.ts_ms + 110, rate: 0.2,
      steps: [
        { at: baseAllowed.ts_ms, title: '이동 허용', detail: `기록의 ${fmt(baseAllowed.payload.action.linear)} m/s 명령을 해태가 통과시켰습니다.` },
        { at: baseRevoked.ts_ms, title: '사람 등장 → 명령 차단', detail: '해태가 이동 허가를 취소하고 ROS에 0속도 명령을 냈습니다.' },
        { at: baseStopped.ts_ms, title: 'Gazebo 바퀴 정지', detail: 'Gazebo가 측정한 바퀴 속도가 0.03 m/s 아래로 떨어졌습니다.' },
      ],
    },
    'arm-deny': {
      start: armDenied.ts_ms - 70, end: armDenied.ts_ms + 140, rate: 0.075,
      steps: [
        { at: armDenied.ts_ms, title: '팔 명령 거부', detail: '기록의 팔 명령이 관절 위치 정책을 벗어나 실행되지 않았습니다.' },
      ],
    },
    'arm-cancel': {
      start: armAllowed.ts_ms - 40, end: worlds.at(-1).ts_ms, rate: 0.16,
      steps: [
        { at: armAllowed.ts_ms, title: '팔 이동 허용', detail: `${jointName}의 ${fmt(armTarget)} rad 목표 궤적이 전달됐습니다.` },
        { at: armRevoked.ts_ms, title: '사람 등장 → 팔 취소', detail: '해태가 실행 중인 팔 동작을 취소했습니다. 관절은 관성으로 조금 더 움직인 뒤 멈춥니다.' },
      ],
    },
  };

  let selected = 'base';
  let time = scenes.base.start;
  let playing = true;
  let previousFrame = null;

  function worldAt(at) {
    for (let i = worlds.length - 1; i >= 0; i--) {
      if (worlds[i].ts_ms <= at) return worlds[i].payload;
    }
    return worlds[0].payload;
  }

  function draw(at, world) {
    const { width: w, height: h } = canvas;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = '#f8fbf8';
    ctx.fillRect(0, 0, w, h);
    const roadY = 155;
    const xFrom = 5.0, xTo = 5.18;
    const mapX = (x) => 88 + Math.max(0, Math.min(1, (x - xFrom) / (xTo - xFrom))) * (w - 176);
    ctx.strokeStyle = '#d8e1db'; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(58, roadY); ctx.lineTo(w - 58, roadY); ctx.stroke();
    ctx.font = '15px system-ui, sans-serif';
    ctx.fillStyle = '#637571';
    ctx.fillText('Gazebo 바퀴 위치 · x축 확대', 32, 38);
    ctx.fillText('출발 5.000 m', 58, roadY + 62);

    const human = world.humans[0];
    if (human) {
      const hx = selected === 'base' ? mapX(human.pos.x) : w - 110;
      ctx.fillStyle = '#bf543d';
      ctx.beginPath(); ctx.arc(hx, roadY - 43, 13, 0, Math.PI * 2); ctx.fill();
      ctx.fillRect(hx - 13, roadY - 29, 26, 36);
      ctx.fillStyle = '#883f30';
      ctx.fillText('사람 보고', Math.min(hx - 27, w - 100), roadY - 68);
    }

    const robotX = mapX(world.robot.pose.x);
    ctx.fillStyle = '#d2dcd5';
    ctx.fillRect(robotX - 22, roadY - 26, 12, 52);
    ctx.fillRect(robotX + 10, roadY - 26, 12, 52);
    ctx.fillStyle = '#246d60';
    ctx.fillRect(robotX - 28, roadY - 20, 56, 40);
    ctx.fillStyle = '#fff';
    ctx.fillText('로봇', robotX - 18, roadY + 5);
    ctx.fillStyle = '#29453f';
    ctx.fillText(`x = ${fmt(world.robot.pose.x)} m`, Math.max(35, robotX - 50), roadY + 88);

    ctx.fillStyle = '#637571';
    ctx.fillText('Gazebo 바퀴 속도', 32, 272);
    ctx.fillStyle = '#e6eee9';
    ctx.fillRect(215, 253, w - 280, 25);
    const speed = Math.abs(world.robot.twist.linear);
    ctx.fillStyle = at >= baseRevoked.ts_ms && selected === 'base' ? '#bd543b' : '#2b8068';
    ctx.fillRect(215, 253, Math.min(1, speed / 0.2) * (w - 280), 25);
    ctx.fillStyle = '#29453f';
    ctx.fillText(`${fmt(speed)} m/s`, 215, 307);

    ctx.fillStyle = '#637571';
    ctx.fillText('Gazebo 팔 관절', 32, 355);
    const joint = measuredJoint(world) ?? 0;
    ctx.fillStyle = '#e6eee9';
    ctx.fillRect(215, 336, w - 280, 20);
    ctx.fillStyle = '#5481a6';
    ctx.fillRect(215, 336, Math.min(1, Math.abs(joint) / Math.max(.01, Math.abs(armTarget))) * (w - 280), 20);
    ctx.fillStyle = '#29453f';
    ctx.fillText(`${fmt(joint, 4)} rad`, 215, 384);
  }

  function render() {
    const scene = scenes[selected];
    const world = worldAt(time);
    draw(time, world);
    $('speed').textContent = `${fmt(Math.abs(world.robot.twist.linear))} m/s`;
    $('joint').textContent = `${fmt(measuredJoint(world) ?? 0, 4)} rad`;
    $('human').textContent = world.humans.length ? '있음' : '없음';
    const current = [...scene.steps].reverse().find((step) => time >= step.at);
    $('status-title').textContent = current?.title ?? 'AI 명령 대기';
    $('status-detail').textContent = current?.detail ?? '시간을 재생하면 명령과 판정이 표시됩니다.';
    $('seek').value = String(Math.round(1000 * (time - scene.start) / (scene.end - scene.start)));
    $('clock').textContent = `${fmt((time - scene.start) / 1000, 2)} / ${fmt((scene.end - scene.start) / 1000, 2)} s`;
    $('events').replaceChildren(...scene.steps.map((step) => {
      const item = document.createElement('li');
      if (time >= step.at) item.classList.add('past');
      if (current === step) item.classList.add('active');
      const marker = document.createElement('time');
      marker.textContent = `+${fmt((step.at - scene.start) / 1000, 2)} s`;
      item.append(marker, document.createTextNode(step.title));
      return item;
    }));
    $('play').textContent = playing ? '일시정지' : time >= scene.end ? '다시 보기' : '재생';
    $('play').setAttribute('aria-label', $('play').textContent);
  }

  function selectScene(key) {
    selected = key;
    time = scenes[key].start;
    playing = true;
    previousFrame = null;
    document.querySelectorAll('.scene').forEach((button) => {
      button.setAttribute('aria-pressed', String(button.dataset.scene === key));
    });
    render();
  }

  document.querySelectorAll('.scene').forEach((button) => {
    button.addEventListener('click', () => selectScene(button.dataset.scene));
  });
  $('play').addEventListener('click', () => {
    if (time >= scenes[selected].end) {
      time = scenes[selected].start;
      playing = true;
    } else {
      playing = !playing;
    }
    previousFrame = null;
    render();
  });
  $('seek').addEventListener('input', (e) => {
    const scene = scenes[selected];
    time = scene.start + (scene.end - scene.start) * Number(e.target.value) / 1000;
    playing = false;
    render();
  });
  function tick(now) {
    if (playing && previousFrame !== null) {
      const scene = scenes[selected];
      time = Math.min(scene.end, time + Math.min(now - previousFrame, 100) * scene.rate);
      if (time >= scene.end) playing = false;
      render();
    }
    previousFrame = now;
    requestAnimationFrame(tick);
  }
  render();
  requestAnimationFrame(tick);
} catch (error) {
  $('status-title').textContent = '기록을 열지 못했습니다';
  $('status-detail').textContent = String(error.message || error);
  $('play').disabled = true;
  $('seek').disabled = true;
}
