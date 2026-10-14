# HANDOFF — haetae

갱신: 2026-10-15. 이 문서는 구현 이력이다. 작업 순서·리뷰·push·머지 권한은 [AGENTS.md](AGENTS.md), 승인된 작업 범위는 [ROADMAP.md](ROADMAP.md)를 따른다. 현재 검증 상태는 해당 PR의 정확한 head와 CI에서 확인한다. 실행 설명은 [simulator alpha guide](docs/simulator-alpha.md), 배포 검증의 미완료 조건은 [security release gate](docs/security-release.md)를 따른다.

## 현재 증거의 범위

- Haetae는 실물 로봇에서 검증되지 않은 pre-alpha다. Gazebo 결과는 같은 호스트의 시뮬레이션 참조 시험이고, UNO R4 결과는 내장 LED 벤치다. 어느 결과도 사람이나 실물 팔을 보호하는 장치의 검증이 아니다.
- 생활위험 보고서의 `stale`, `unknown_item`, `missing_coverage`는 judge-only 진단이다. 판정만 기록하며 팔로 명령을 보내거나 이동을 측정하지 않는다. 6개 위험 사례는 모두 같은 도구 끝점 sphere sweep과 box 영역 기하를 쓴다. 실제 팔 전체 형상이나 충돌 검사는 아니다. [생활위험 실험](examples/household-hazards/README.md)과 [이력 문서](docs/household-history.md)를 따른다.
- H2 보드 시험은 승인 서비스와 전달자가 같은 UID에서 실행됐다. LED는 사람이 육안으로 확인했으며 독립 GPIO 계측은 없다. Linux 역할 격리 시험은 별도 소프트웨어 증거다. 아래 보드 세션 기록으로 현재 보드 상태나 다른 사용자 계정·실물 모터의 동작을 추정하지 않는다. [H2 증거 범위](docs/controller-permits.md#scope-of-the-h2-evidence)를 따른다.
- Gazebo의 공유 러너 타이밍은 비차단 측정이다. fail-closed 판정에는 같은 시험의 정상 이동 대조군이 필요하며, 타이밍 표본이나 과거 CI 통과를 새 head의 승인으로 쓰지 않는다. [Gazebo 검사 기준](docs/gazebo-reference.md)을 따른다.

## 2026-10-04 전체 리뷰 수정

- 전체 리뷰 기준 main `17b8931`에서 핵심7건 및 낮은 우선순위 표시·운영·증거 경계를 수정했다. [PR15](https://github.com/haetae-robotics/haetae/pull/15)의 정확한 head별 리뷰/CI/머지 상태가 기준이다.
- 로그 수집은 trusted root 기준 nofollow fd/owner/type/link/256MiB 제한; 역할 준비 폴더는 root0700에서 준비 후 권한 이전. private key0600/public diagnostic0644.
- nonzero velocity에 측정 twist 필수. Caution 팔 거부·취소 및 base 즉시 감속. reject 시각 이후 관절 피드백으로 settling 확인.
- ROS arm 수락·취소 absolute250ms, 취소/settling 동안 heartbeat 금지, resolved command topic의 extra publisher는 지속 Hold.
- 당시 로컬 unit·native bench와 리뷰 기록은 PR15의 이력이다. 현재 리뷰 방식과 머지 조건은 AGENTS.md를 따른다. 이전 리뷰·CI 성공은 승인으로 재사용하지 않는다. 실물 미검증.
- 진단 counter의 atomic replace 중 이미 unlink된 fd는 같은 pinned directory에서 다시 읽되, 첫 읽기를 포함해 최대3회 시도한다. live hardlink·symlink·owner/type/size 검사는 유지한다. export는 거부 파일 외 진단을 계속 수집하고 실패 시 이전 passing report를 무효화한다.
- CI software bench의 임시 서명 fixture는 Linux `/dev/shm`, macOS의 새128MiB RAM device를 사용한다. 공유 디스크 fsync 지연과 분리한 software 시험이며, 운영50ms/세계200ms/guard200ms 제한은 그대로다. 일반 로컬 verify/run은 기본 디스크를 사용한다. 디스크 전원 장애·실물 보호 검증이 아니다.

## 현재 구현

- Rust 강제 실행기와 서명된 역할 입력, ROS2 Jazzy/SROS2 권한, Gazebo Harmonic ROSbot XL + OpenMANIPULATOR-X.
- 가상 바퀴·팔 제어기는 원래 발급 시각에서 최대200ms인 독립 시뮬레이션/벽시계 허가를 검사한다. kill·stall·delay 뒤 실제 관절 위치를 유지하고 오래된 동작을 버린다.
- 승인 서비스/인지 서명/VLA 서명/제안/전달자의 UID2001..2005, 개인 키 저장소와 DDS 권한을 분리한다. root 제공자·인지 입력·컨트롤러·호스트 및 승인 서비스는 신뢰한다. 전달자는 승인 키와 Rust pipe를 갖지 않는다.
- 사람은 native Gazebo 기하 형상이다. 화면은 관측된 몸통 좌표를 따르고, 정지 판단은 실제 가상 GPU 라이다 표면 측정을 사용한다. 원하는 사람 좌표를 게이트 입력으로 직접 쓰지 않는다.
- root 소유 컨테이너 방화벽과 상속되는 seccomp/NNP 필터가 비-root 역할의 통신을 로컬 DDS UDP로 제한한다. 실제 Gazebo pose 서비스 및 UDP 수신기 시험은 root 정상 대조군과 함께 확인한다. NET_ADMIN/필터 지원이 없으면 실행을 거부한다.
- 40/80/100Hz 제안 압력과 센서 끊김·인지 서명 지연을 겹치는 복합 시험을 반복한다. 엔진의 실제 승인/거부, 400ms 이내 0출력, 실제 정지, 회복 뒤 자동 재가동 없음이 필수다. 서명 카운터만으로 수신 성공을 판단하지 않는다.
- 라이다 관측 불가는 confidence0 서명 world로 즉시 전달하고 root reference 정책의 `perception-unknown` 규칙으로 거부·취소한다. 표적 소실은 해당 world→명명된 Rust revoke→disarm→후속0출력의 인과 증거가 필수다. 기계 관측·서명 경로 소실은 기존200ms world expiry를 유지하며 household failure guard는 unknown world도 막는다. 실제 장애 요청부터0출력까지400ms 제한을 바꾸지 않는다.
- Gazebo scan/ROS clock 비동기 수신은 최근16개 단조 증가 frame 중 현재시각 이하의 최신 frame을 선택한다. 유효하지 않은 최신 frame을 과거 정상 frame으로 덮지 않고, 원래 source/수신벽시계200ms 제한을 유지한다. 미래 frame만 있거나 만료·eviction되면 unknown이다. 관측 시각·authority를 갱신하는 완충이 아니다.
- 시뮬레이터 평가용 알파 운영 명령, 공유용 허용 필드 리포트, CI 소스/검증 후보 패키지를 제공한다. 실물 보호나 인증 버전이 아니다.
- 공식 UNO R4 Minima를 USB만 연결했고 사용자 승인으로 Rosetta를 설치했다. main `1b8a904`의 H1 LED 펌웨어를 업로드해 USB 실물 6개 시험을 통과했다. 갱신 없는 독립 잠금 USB 관측은204.2ms이며 호스트 중지/복구 뒤 자동 재가동이 없었다. USB 소프트웨어 상태 증거이고 GPIO·WDT reset·모터 정지·실물 인지는 미검증이다. H1은 USB writer를 신뢰한다. 후속 [H2 controller permits](docs/controller-permits.md)는 서명된 OFF-only 연결과 매 LED 명령 인증을 보드에서 검사하고 별도 승인 서비스를 제공한다. 실제 ROS arm/base M3·실물 모터 보호와 동일시하지 않는다.

- `./haetae-demo hazards`는 root-bound household 정책을 적용한 별도 생활 위험 실험이다. 신뢰된 서명 관측의 종류·기기 상태·용기 이력과 정책 FK로 정확한 후보 관절 명령을 중앙 Rust 게이트가 재계산·검사한다. 주입한 6개 위험 사례와 검사 참조 누락·버전/물체 불일치를 거부하고, 정상 명령의 실제 가상 이동·0.05rad 추적을 대조한다. 비정지 base/미지원 작업은 이 고정 base 범위에서 거부한다. 기존 침투 방어 시험과 합산하지 않는다. 3개 judge-only 진단과 같은 기하를 쓰는 6개 사례의 한계는 위 증거 범위를 따른다. 영속 관측·동작 이력은 아래 M2a 범위까지 구현됐지만 material effect commit은 0이다. 전체 팔 geometry, 실물 보호·인지·파지·화학 반응·사람 밀기 방지는 미검증이다. [필수 검사 계약](docs/household-gate.md)과 [생활 위험 실험](examples/household-hazards/README.md)을 따른다.

- H2 UNO R4 Minima의 인증 허가·런타임 USB 경로 차단은 USB 실물 13개 시험, native 17개 시험과 Linux 역할 격리 시험을 통과했다. 최종 커밋별 리뷰·CI·머지 상태는 [PR19](https://github.com/haetae-robotics/haetae/pull/19)와 [H2 문서](docs/controller-permits.md)의 좁은 범위를 따른다. 보드에는 H2 강화 프로필을 올렸으며 이후 재업로드는 물리 RESET 진입이 필요하다. 개인 키·생성 헤더·장치 이미지는 로컬에만 있고 공개하지 않는다. GPIO·WDT reset·전원 차단·모터·실물 인지·secure boot는 미검증이다. 별도 [M3 Gazebo 허가](docs/gazebo-controller-permits.md)는 실제 가상 바퀴·팔의 최종 제어기에서 동작별 서명과 임대를 검사한다. 실물 MCU/모터 보호 결과가 아니다.

## Durable household history (M2a)

- [이력 문서](docs/household-history.md): household policy에서 관측된 내용물의 보수적 합집합, 물체/region kind와 관측 revision floor, 정확한 승인 동작의 예약을 기존 상태 파일 v2에 보존한다. 모드·서명 카운터·이력은 한 번의 atomic checkpoint로 저장하며 양의 명령 전에 완료한다.
- 재시작은 pending motion을 interrupted로 남기고 취소/실측 정지를 요구한다. 같은 observer task revision의 step 재사용은 거부한다. 새로운 trusted task revision만 새 epoch를 만들며 오래된 binding은 지속 floor로 거부한다. 유효한 새 VLA 카운터만으로 재실행할 수 없다.
- motion settling은 관절 측정의 증거이며 물질 이송/파지/작업 성공이 아니다. 모든 material effect commit은 0이다. M2 전체 effect ledger, 실물 identity/인지·clock epoch 재시작·전체 팔 geometry는 별도다.
- Gazebo household 보고서는13개 필수 항목이다. 새 이력 검사는 정상 이동·정지 이후 중복 step 차단과 raw 빈 용기 주장 뒤 실제 Rust의 내용물 이력 거부를 요구한다. 정확한 최종 head의 리뷰/CI/실행 상태를 별도로 확인해야 한다.

## 실행

```bash
./haetae-demo doctor
./haetae-demo start
./haetae-demo status
./haetae-demo verify
./haetae-demo report
./haetae-demo stop
```

<http://127.0.0.1:8765/>에서 시작하고 정지 장면을 본 뒤 다음 단계를 누른다. `restart`는 새 코드로 다시 실행한다. 원본 Gazebo는 로컬6080, 새 출력은 `artifacts/simulator-alpha/<run>/`에 보존한다. 포트 충돌과 동명 다른 컨테이너를 자동 삭제하지 않는다. ROS setup을 `set -u` 전에 source하는 순서는 유지한다.

## 검증·리뷰 기준

- PR8 팔 독립 정지, PR9 역할 격리, PR10 native 사람/라이다, PR11 native 통신 격리, PR12 복합 장애 반복이 반영됐다.
- PR11 exact-head CI36865368679 및 PR12 CI36866546349의13개 작업이 통과했다. 최신 알파 변경의 정확한 상태는 해당 PR/CI를 확인한다. 과거 성공을 새 커밋 승인으로 재사용하지 않는다.
- 현재 PR 전체를 모델 하나로 리뷰하고, 수정 후 같은 모델이 갱신된 head를 다시 리뷰하는 방식은 AGENTS.md를 따른다. 이 문서의 과거 작업 요청은 현재 push·머지·게시 권한을 부여하지 않는다.
- 200ms 세계 신선도, 50ms 양의 응답, 팔0.05rad 추적 오차와1초 청크 제한을 완화하지 않는다. 제때 도착했지만 입력이 만료된 응답은 0/팔 취소 후 엔진 목표·arming을 해제한다. 50ms 이상 지연, IPC/형식/역방향 시계 실패는 fatal 정지를 유지한다.
- 로컬 증거·실행 키·`.omx/`는 커밋하지 않는다. 다운로드 리포트는 unsigned 요약이며 원본 키/환경/인지 메시지를 포함하지 않는다.
- 실물 로봇 시험이나 다음 개발 단계는 ROADMAP.md의 승인 범위와 AGENTS.md를 따른다. 이 구현 이력만으로 NEXT·FROZEN 영역의 새 작업을 시작하지 않는다.

UI 기준: [DESIGN.md](DESIGN.md), 별도 scripted demo: [sim/DESIGN.md](sim/DESIGN.md).
