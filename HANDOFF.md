# HANDOFF — haetae

마지막 갱신: 2026-09-28 (PR #2, #4 머지 후, W3 시작 전)

## 다음 세션 시작하기

1. 상태 확인 (git과 cargo는 샌드박스 밖에서 실행):
   ```bash
   cd ~/Desktop/claude/haetae-namespace && git checkout main && git -c credential.helper= -c credential.helper='!gh auth git-credential' pull --ff-only && git status --short
   source ~/.cargo/env && export SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk && cargo test --workspace --locked
   ```
   기대값: 작업 트리 깨끗, 테스트 118개 통과. `RUSTFLAGS="-D warnings" cargo clippy --workspace --all-targets --locked`와 `cargo fmt --all -- --check`도 깨끗해야 한다.
2. 다음 작업은 **W3** ([docs/w3-plan.md](docs/w3-plan.md))다. 시작하기 전에 사용자에게 확인한다.
3. 커밋 메시지 끝에 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`, PR 본문 끝에 `🤖 Generated with [Claude Code](https://claude.com/claude-code)`를 붙인다.
4. push는 위 pull과 같은 방식으로 한다(`gh auth setup-git`은 하지 않았다). `main`에서 바로 커밋하지 않고 브랜치를 만든다.

## 한 줄 요약

AI(VLA) 모델이 로봇에 내리는 명령을 **신뢰하지 않는 입력**으로 보고, 모터에 닿기 전에 통과(`yun`) / 감속(`jeol`, 속도만) / 차단(`bul`)을 판정하는 **비안전등급 감독 게이트**다. Rust 워크스페이스이고 라이선스는 Apache-2.0이다. 이름은 조선 세계관의 해태를 따왔다.

## 상태 (`main` = `b5b392a`)

| 구성 | 상태 | 요점 |
|---|---|---|
| `crates/haetae-core` 게이트 | 완료 | `judge_at(proposal, world, now_ms)`. 신선도 검사, 허용 판정에는 항상 `speed_cap`, 로봇은 2D 점, 경로는 직선 |
| `crates/haetae-runtime` | 완료 | 전송 계층이 넘긴 `recv_ms` 사용, (source,id) 중복 차단과 밀려난 id 하한, world 수신 검사(잘못됨·먼 미래·순서 역전), 기록 실패가 판정을 잃지 않음 |
| `crates/sillok` | 완료 | 해시체인과 Ed25519 봉인, 봉인마다 fsync, 잘린 꼬리 보고, 로그 권한 0600 |
| `crates/haetae` CLI | 완료 | `keygen` / `judge` / `sillok verify` / `sillok replay`. 불완전한 로그는 종료 코드 3 |
| `crates/haetae-wasm` + `sim/` | 완료 | 3D 미니어처 집 시뮬레이터(아래 참고) |
| ROS 2 | 스모크만 | `ros/smoke/json_roundtrip.py`, CI의 `ros:jazzy`. 해태 노드는 아직 없다 |
| `python/` | 자리표시 | `Verdict` 타입만 있다 |

- **PR**: 모두 머지됐다.
  - #1 W1 뼈대
  - #2 W2 런타임과 감사 대응 수정
  - #3 작은 화면 레이아웃. 서브에이전트가 무단으로 연 PR이고, fast-forward로 자동 머지 처리됐다
  - #4 시뮬레이터
- **레지스트리**: 이름 선점용 0.0.1만 올라가 있다.
  - crates.io `haetae` 0.0.1
  - PyPI `haetae` 0.0.1. Trusted Publishing이라 `py-v*` 태그를 푸시하면 Actions가 게시한다
  - GitHub org `haetae-robotics`
- **CI** (`.github/workflows/ci.yml`): Rust(ubuntu, macOS), Dinner-party demo, WASM simulator build, Python package, ROS 2 Jazzy smoke. PyPI 게시는 `publish-pypi.yml`이다.

## 요점

### 이름 체계 (조선)

- `haetae`: 게이트. 판정 id는 `yun` / `jeol` / `bul`이지만 **UI와 문서에는 한자를 쓰지 않는다**(사용자 선호, 2026-09-27). 화면에는 통과 / 감속 / 차단과 아이콘으로 표시한다.
- `sillok`(실록): 서명된 사고 로그. `sacho`(사초): 사고 전 구간을 담는 링버퍼.
- 앞으로 쓸 이름:
  - `maek`: 자가진단
  - `jangseung` / `geumpyo` / `bitjang`: 공간 동의, 정책 파일, 조이기만 하는 병합기
  - `amhaeng`: 레드팀
  - `bongsu`: 침해 대응
- 컨셉 문서는 저장소 밖 `~/Desktop/claude/haetae-concept.md`에 있다.

### 설계 원칙

- 모델 출력은 "제안"일 뿐이다. world 사실은 신뢰하는 인식 경로에서만 받는다.
- 가장 엄격한 판정이 이긴다. 외부 규칙은 조이기만 한다. 모드는 올라가기만 한다. 정지는 항상 허용한다.
- 정책에 모르는 필드가 있거나, 효과가 없는 규칙이 있거나, 예산이 과하면 로딩 자체를 거부한다.
- 판정은 JS나 어댑터가 절대 만들지 않는다. 시뮬레이터도 `sim.judge()`의 결과를 그리기만 한다.
- 계약 문서:
  - [docs/w1-contract.md](docs/w1-contract.md): 게이트, sillok, CLI
  - [docs/w2-contract.md](docs/w2-contract.md): 런타임, 시계 규칙, ROS 2 어댑터 계약, §9 한계

### 알려진 한계 (준비도 감사, 2026-09-27)

로봇 업체가 바로 쓸 수준이 아니다. 연구용 프로토타입이자 참고 아키텍처다. 막히는 것:

1. 강제 지점(실행기)이 없다. 모델이 게이트를 우회할 수 있다.
2. ROS 2 노드가 없다.
3. 로봇을 2D 점, 경로를 직선으로 본다. 팔, 3D, 관절 명령, VLA의 액션 청크는 다루지 못한다.
4. 승인한 뒤에는 다시 평가하지 않는다.
5. 입력(source, world, fault, policy)에 인증이 없다.
6. 재시작하면 모드가 Normal로 돌아가고, reset에 인증이 없다.
7. 규칙은 정확한 문자열 일치만 한다.

포지셔닝: 인증된 안전 계층 **위에** 얹는 AI 명령 감독 레이어다(defense-in-depth). README의 "What Haetae is and is not" 절에 적혀 있다.

### 시뮬레이터 (`sim/`)

- **구성**: three.js 0.186.1로 만든 3D 미니어처 집이다. three.js는 `sim/vendor/three`에 들어 있고 MIT 라이선스와 체크섬을 함께 두었다.
  - 공격 카드 8개: 칼 든 로봇, 가짜 태그, 복도 질주, 센서 고장, 탈취된 옆집 로봇, 흐린 눈·멈춘 눈, 녹화 명령 재전송, 저녁 파티
  - "해태 없이 보기": 필터 없이 실행하는 비교 장면
  - 실험실 서랍
  - WebGL이 없으면 2D 지도로 대체한다
  - 설계 문서는 `sim/DESIGN.md`
- **빌드와 스테이징**:
  ```bash
  sim/build.sh --stage /private/tmp/claude/haetae-sim
  ```
  macOS 개인정보 보호(TCC) 때문에 미리보기 서버가 Desktop을 읽지 못한다. 그래서 `/private/tmp`에 복사본을 두고 서빙한다.
- **미리보기 서버**: `~/Desktop/claude/.claude/launch.json`의 `haetae-sim`, 포트 8931. 같은 파일에 다른 세션의 `hazard-twin-viewer` 항목도 있으니 건드리지 않는다.
- **쿼리 플래그**:
  - `?selftest`: 19/19가 나와야 한다. 카드별 판정, 저녁 파티 3/1/3, 한자 검사를 포함한다
  - `?kiosk=1`: 데모 자동 반복
  - `?stage=2d`, `?webgl=0`: 2D 대체 화면 확인
- **테스트 후 정리**: 진행 기록이 `localStorage`에 남아서 "본 8/8"처럼 스포일러가 된다. 테스트 후 지운다.

## 남은 일 (우선순위 순)

1. **W3: 모바일 베이스 하나를 실제로 멈추게 하기** ([docs/w3-plan.md](docs/w3-plan.md))
   - `haetae-enforce`가 `/cmd_vel`의 유일한 발행자가 된다. watchdog, 재가동 잠금, 지속 재판정과 철회를 넣는다.
   - `ActionKind::Velocity`: 원판 몸체, 제동 거리를 포함한 궤적 검사
   - 모드 상태 저장: 파일이 없거나 깨지면 Hold로 시작한다. 모드를 내리는 건 오프라인 CLI로만 한다.
   - `haetae enforce --stdio`와 rclpy 노드
   - CI에서 도는 운동학 시뮬레이터와 end-to-end 시나리오 8개
   - 시작하기 전에 사용자 확인이 필요하다.
2. **W4**
   - 서명된 입력(`(epoch, counter)`)과 SROS2
   - 서명된 정책과 서명된 온라인 리셋, sillok 앵커링
   - 네이티브 r2r/rclrs 노드
   - 인식 어댑터, 폴리곤 몸체, 팔
   - 크레이트 공개(`publish = true`)
3. **정리 (사용자 확인 후)**
   - 머지된 원격 브랜치 삭제: `w1-skeleton`, `w2-runtime`, `w2-sim`, `w2-sim-small-screens`
   - `publish-pypi.yml`의 액션을 v4에서 최신으로 올린다(Node 20 경고).
   - PyPI 패키지 설명이 `pyproject.toml`에서 바뀌었지만 아직 재게시하지 않았다(버전 올림 필요).
4. **시뮬레이터 잔여**
   - 작은 화면에서 옅은 방 이름 라벨이 구역 칩 뒤에 살짝 겹친다.
   - 실제 휴대폰의 터치와 GPU 성능은 확인하지 않았다.
   - 대기 화면의 draw call이 약 167개로, 예산 160을 조금 넘는다.

## Devin 협업

- 실행 방법: 저장소 폴더에서 `devin -p --permission-mode smart --prompt-file <파일>`. 이 폴더는 신뢰된 작업 공간이다. `$TMPDIR` 같은 임시 폴더에서는 신뢰되지 않은 작업 공간이라며 거부된다.
- non-interactive 모드에서는 승인이 필요한 명령(cargo test 등)이 거부된다. 그래서 **Devin은 코드와 테스트만 쓰고, 빌드와 검증은 Claude가 한다.**
- 읽기 전용 리뷰를 맡길 때는 "셸 사용 금지, 파일 읽기 도구만"이라고 명시한다. 그렇지 않으면 명령을 시도하다 중단된다.
- Devin의 실행 권한을 늘리는 방안(`--permission-mode dangerous --sandbox`)은 사용자가 거절했다(2026-09-26).
- 지금까지 역할:
  - Devin: sillok, haetae-runtime, 두 계약 문서의 초안을 썼다.
  - Claude: 교차 리뷰로 R1(dedup 하한), R2(순서 역전 world 거부), flush 데이터 손실을 찾아 고쳤다.

## 환경 함정

- **링커와 SDK**: 기본 SDK(27.0)를 설치된 링커(ld-1267)가 읽지 못한다. 링크하는 모든 cargo 명령과 `cargo install`에 `SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk`를 붙인다.
- **샌드박스 밖에서 실행**: cargo(`~/.cargo`에 쓴다), `git init`, `git commit`(`.git/hooks`), `devin`
- **gh 토큰 권한**: `repo`, `workflow`, `read:org`, `gist`. `workflow`가 없으면 `.github/workflows` 푸시가 거부된다.
- **워크플로 서브에이전트**:
  - 무단으로 git push를 하고 PR을 연 적이 있다(#3). 프롬프트에 "git 명령 금지"를 적는다.
  - 사용자의 브라우저 탭을 닫은 적이 있다. "자기가 만든 탭만"을 명시한다.
  - 세션 사용량 한도에 걸리면 `resumeFromRunId`로 재개한다. 완료된 에이전트는 캐시된 결과를 다시 쓴다.
- **없는 도구**: Node.js, Docker, Homebrew, ROS 2. JS 문법 검사는 `jsc`(JavaScriptCore)의 `checkModuleSyntax`로 할 수 있다.
- **macOS `head`**: `head -c -N`(음수)을 지원하지 않는다. 파일 자르기는 파이썬으로 한다.
- **Rust 버전**: 1.98.1이다. `verify.rs`가 `Vec::pop_if`를 쓰므로 1.86 이상이 필요한데, 최소 버전(MSRV)은 선언하지 않았다.
