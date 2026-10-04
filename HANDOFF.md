# HANDOFF — haetae

갱신: 2026-10-04. 현재 기준은 `main`과 PR의 정확한 커밋별 CI/리뷰 증거다. 실행 설명은 [simulator alpha guide](docs/simulator-alpha.md), 보호용 배포 조건은 [security release gate](docs/security-release.md)를 따른다.

## 진행 중 리뷰 수정

- 전체 리뷰 기준 main `17b8931`에서 `fix/full-review-boundaries` 작업 중. 핵심7건 및 낮은 우선순위 표시·운영·증거 경계 수정.
- 로그 수집은 trusted root 기준 nofollow fd/owner/type/link/256MiB 제한; 역할 준비 폴더는 root0700에서 준비 후 권한 이전. private key0600/public diagnostic0644.
- nonzero velocity에 측정 twist 필수. Caution 팔 거부·취소 및 base 즉시 감속. reject 시각 이후 관절 피드백으로 settling 확인.
- ROS arm 수락·취소 absolute250ms, 취소/settling 동안 heartbeat 금지, resolved command topic의 extra publisher는 지속 Hold.
- 로컬 unit·native bench 검증 후 전체 변경의 독립 양쪽 리뷰/Claude/정확한 head CI를 확인해야 한다. 이전 리뷰·CI 성공은 승인으로 재사용하지 않는다. 실물 미검증.

## 현재 구현

- Rust 강제 실행기와 서명된 역할 입력, ROS2 Jazzy/SROS2 권한, Gazebo Harmonic ROSbot XL + OpenMANIPULATOR-X.
- 팔 제어기에 독립적인 250ms 시뮬레이션/벽시계 임대가 있다. kill·stall·delay 뒤 실제 관절 위치를 유지하고 오래된 동작을 버린다.
- 게이트/인지 서명/VLA 서명/제안의 UID2001..2004, 개인 키 저장소와 DDS 권한을 분리한다. root 제공자·인지 입력·컨트롤러·호스트 및 게이트의 정당한 제어 권한은 신뢰한다.
- 사람은 native Gazebo 기하 형상이다. 화면은 관측된 몸통 좌표를 따르고, 정지 판단은 실제 가상 GPU 라이다 표면 측정을 사용한다. 원하는 사람 좌표를 게이트 입력으로 직접 쓰지 않는다.
- root 소유 컨테이너 방화벽과 상속되는 seccomp/NNP 필터가 비-root 역할의 통신을 로컬 DDS UDP로 제한한다. 실제 Gazebo pose 서비스 및 UDP 수신기 시험은 root 정상 대조군과 함께 확인한다. NET_ADMIN/필터 지원이 없으면 실행을 거부한다.
- 40/80/100Hz 제안 압력과 센서 끊김·인지 서명 지연을 겹치는 복합 시험을 반복한다. 엔진의 실제 승인/거부, 400ms 이내 0출력, 실제 정지, 회복 뒤 자동 재가동 없음이 필수다. 서명 카운터만으로 수신 성공을 판단하지 않는다.
- 시뮬레이터 평가용 알파 운영 명령, 공유용 허용 필드 리포트, CI 소스/검증 후보 패키지를 제공한다. 실물 보호나 인증 버전이 아니다.
- 사용자가 UNO R4 Minima를 주문했다. [LED bench](docs/uno-r4-bench.md)의 서명된 실제 Rust 엔진→직렬 adapter→독립 200ms guard를 준비했다. `./haetae-bench verify`는 같은 guard의 native 프로세스 시험, `compile`은 Arduino용 빌드다. 실물 USB/GPIO/WDT/reset 및 모터 정지는 미검증이다. Apple Silicon의 Intel Arduino 도구는 compile만 Docker로 우회하며 실제 upload는 Rosetta가 필요하다.

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
- 독립 code-reviewer와 architect가 같은 base/head/패치 해시의 전체 변경을 검토한다. 행동이 바뀌면 양쪽 전체 재검토. READY와 승인 상태를 구분하며 정확한 head의 CI와 실제 실행을 확인한 뒤 머지한다.
- 이번 사용자 요청은 커밋·리뷰·CI·push·머지를 포함한다. 공개 레지스트리/보호용 릴리스 게시까지 승인했다고 추정하지 않는다.
- 200ms 세계 신선도, 50ms 양의 응답, 팔0.05rad 추적 오차와1초 청크 제한을 완화하지 않는다. 제때 도착했지만 입력이 만료된 응답은 0/팔 취소 후 엔진 목표·arming을 해제한다. 50ms 이상 지연, IPC/형식/역방향 시계 실패는 fatal 정지를 유지한다.
- 로컬 증거·실행 키·`.omx/`는 커밋하지 않는다. 다운로드 리포트는 unsigned 요약이며 원본 키/환경/인지 메시지를 포함하지 않는다.
- 실물 로봇은 정해지지 않았다. 다음 보호용 단계는 신뢰된 센서 경로, 실제 컨트롤러 독립 정지, 로봇별 공격 경계, 3D 팔 충돌 정책, stop/checkpoint 충돌 창과 서명된 재현 가능한 릴리스 증거다.

UI 기준: [DESIGN.md](DESIGN.md), 별도 scripted demo: [sim/DESIGN.md](sim/DESIGN.md).
