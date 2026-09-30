# HANDOFF — haetae

마지막 갱신: 2026-10-01. 작업 브랜치: `feat/secure-enforcement`.

## 현재 상태

Haetae는 신뢰하지 않는 AI 명령을 컨트롤러 앞에서 검사하는 감독 게이트다.
Rust 강제 실행기, 서명된 입력, ROS 2 Jazzy 브리지, SROS2 정책과 Gazebo
Harmonic 실시간 시뮬레이터가 구현되어 있다. 보호용 배포 준비도와 남은
조건은 [docs/security-release.md](docs/security-release.md)가 기준이다.
실제 로봇, 하드웨어 정지 동작 및 안전 인증은 아직 검증하지 않았다.

## 바로 실행하기

저장소 루트에서 Docker Desktop을 켠 뒤:

```bash
ros/gazebo/run_docker.sh
```

`Live view:`가 나오면 <http://127.0.0.1:8765/>를 열고 **시뮬레이션 시작**을
누른다. 장면이 끝나면 정지 상태로 기다리며 **다음 단계** 버튼으로 진행한다.
Gazebo 원본 창은 로컬 noVNC 포트 6080으로 제공한다. 이미 같은 포트를 쓰는
시뮬레이터가 있으면 그 컨테이너를 먼저 종료해야 한다.
실행 방법, 모델 출처와 시험 범위는 [docs/gazebo-reference.md](docs/gazebo-reference.md)에 있다.

## 이번 개선

- 기본 로봇은 Husarion ROSbot XL 표준 바퀴 + ROBOTIS OpenMANIPULATOR-X다.
  공식 URDF/메시와 라이선스를 `ros/gazebo/vendor/`에 커밋별로 고정했다.
  `manifest.json`과 모델 테스트가 출처 및 파일 해시를 확인한다.
- 라이브 3D는 실제 Gazebo 위치와 관절 측정을 사용한다. 제품의 네 팔 관절과
  바퀴 피드백을 표시하며, 원본 Gazebo 창과 전환할 수 있다.
- 테스트 사람 보고는 시뮬레이션 시간에 따라 걸어 접근하고, 정지 장면에서
  대기한 뒤 다음 단계 요청에 걸어 나간다. 표시 좌표와 게이트 입력이 같다.
  사람은 인식 센서가 검출한 대상이나 Gazebo의 물리적 human actor가 아니다.
- 기본 시험 순서는 사람 접근 → 팔 범위 거부 → 팔 동작 취소 → 외부 노드
  접근 권한 → 게이트 연결 종료 → 서명 명령 재전송이다.
- 제한된 VLA 자격 증명만 가진 별도 OS 사용자로 실제 Gazebo ROS 그래프의
  직접 바퀴 명령과 world 위조를 시험한다. 재전송과 서명 변조는 별도 실제
  Rust 실행기에서 검증한다. Gazebo Transport와 관리자 계정은 신뢰 경계 안이다.
- 사람 감지 후 다음 단계가 멈추던 오류를 수정했다. world 갱신으로 철회되면
  proposal Decision 이벤트가 없을 수 있다. `stop_evidence.py`는 실제 엔진
  로그의 사람 판정과 해당 world 보고 이후의 0속도 명령을 확인한다.
- 실행 실패 시 화면이 **시뮬레이션 중단**을 유지하고 진행 버튼을 비활성화한다.
  `error.json`에 진단을 저장한다. 연결 끊김과 측정 지연도 구분한다.

## 검증

2026-10-01 로컬 Docker에서 원본 Gazebo GUI를 함께 실행하여 전체 6개 장면과
5개 수동 대기 지점을 끝까지 검증했다. 네 공격 시험, 팔 네 관절 취소 및
봉인된 사고 로그 확인이 통과했다. 별도 실패 주입으로 오류 이벤트 보존,
HTTP 진행 요청 거부와 `error.json` 저장을 확인했다.
CI와 같은 GUI 없는 라이브 실행도 `check_live_stream.py`로 끝까지 확인했고,
Python/JavaScript 단위 검사 32개가 통과했다.
로컬 증거는 `artifacts/gazebo-walking-gui-check/`와
`artifacts/gazebo-error-check/`에 있으며 Git에서는 제외한다.

빠른 검사:

```bash
python3 -m unittest discover -s ros/gazebo -p 'test_*.py' -v
node --test sim/gazebo-live.test.mjs sim/person-rig.test.mjs sim/product-rig.test.mjs
python3 -m unittest discover -s ros/haetae_gate -p 'test_*.py' -v
python3 -m unittest discover -s ros/security -p 'test_*.py' -v
python3 -m unittest discover -s ros/haetae_sim -p 'test_*.py' -v
```

제품 자산을 다시 만들려면 `xacro`가 설치된 환경에서
`python3 tools/build_product_visuals.py`를 실행한다. Docker 이미지 빌드도 이를
수행한다. 생성된 JSON과 gzip, 모델 어댑터 및 변환기 해시가 일치해야 한다.
기존 가상 로봇 메시와 변환기는 이전 기록 및 회귀 검사에 남겨 두었다.

## 다음 작업의 기준

- 브랜치 push는 사용자 승인으로 진행한다. `main` 머지나 배포, 레지스트리
  게시, 원격 브랜치 삭제는 이번 요청에 포함되지 않는다.
- UI 기준은 루트 [DESIGN.md](DESIGN.md), 기존 웹 데모 기준은
  [sim/DESIGN.md](sim/DESIGN.md)다.
- 소스 신선도 200 ms, 양의 실행 응답 제한 50 ms, 팔 추적 오차 0.05 rad 및
  최대 청크 1초를 화면 진행 때문에 완화하지 않는다.
- 선택된 실물 로봇은 없다. 다음 보호용 배포 작업은 실제 제어기의 독립 정지,
  센서 신뢰 경로, OS/키 격리 및 로봇별 공격 경계 검증이 필요하다.
- 실행 로그, 시험 키, `.omx/` 검토 증거와 `.playwright-cli/`는 로컬에만 둔다.
