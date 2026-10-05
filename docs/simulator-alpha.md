# 시뮬레이터 평가용 알파

Haetae가 위험한 명령을 거부하고 가상 로봇을 멈추는 과정을 확인하는 공개 평가용 실행입니다. **실물 로봇 보호용 배포나 안전 인증 버전은 아닙니다.** 검증된 범위는 제한된 Linux 컨테이너 계정의 ROS/Gazebo 명령 경로입니다.

## 처음 실행하기

macOS에서는 Docker Desktop, Linux에서는 Docker Engine 또는 Docker Desktop을 설치하고 켜세요. 터미널의 `docker`, `curl`이 필요합니다. ROS나 Gazebo를 직접 설치할 필요는 없습니다. 현재 Windows 실행은 지원 범위에 포함하지 않습니다.

```bash
git clone https://github.com/haetae-robotics/haetae.git
cd haetae
./haetae-demo doctor
./haetae-demo start
```

첫 빌드는 몇 분 걸릴 수 있습니다. 준비가 끝나면 표시되는 <http://127.0.0.1:8765/>를 여세요. **시뮬레이션 시작 → 움직임·정지 확인 → 다음 단계** 순서로 진행합니다. 시작 명령은 백그라운드 실행을 준비한 뒤 종료합니다. 터미널을 계속 열어둘 필요가 없습니다.

`라이브 3D`는 실제 위치·관절·사람 형상 측정값의 재구성입니다. `Gazebo 원본`은 시뮬레이터 자체 화면입니다. 청록 점은 실제 가상 라이다의 장애물 표면 측정입니다.

| 할 일 | 명령 |
| --- | --- |
| 별도 생활 위험 실행 전 검사 실험 | `./haetae-demo hazards` |
| 실행 상태 확인 | `./haetae-demo status` |
| 새 코드로 다시 실행 | `./haetae-demo restart` |
| 원인 확인 | `./haetae-demo logs` |
| 종료 | `./haetae-demo stop` |
| 화면 없이 전체 시험·복합 장애 6회 | `./haetae-demo verify` |
| 완료·실패 리포트 저장 | `./haetae-demo report` |
| 원하는 파일에 리포트 저장 | `./haetae-demo report ./my-report.json` |

완료 또는 실패 뒤 웹 오른쪽 **검증 리포트 → 결과 보기 / JSON 저장**도 사용할 수 있습니다. 리포트는 정상 이동, 라이다 기반 정지, 명령 제한, 권한/서명 공격, 센서 이상, 팔 독립 정지, 복합 장애 반복과 사건 기록 확인을 구분합니다. 미실행 항목은 통과로 표시하지 않습니다. 실패 리포트는 미완료 항목을 남깁니다.

출력은 매 실행마다 새 `artifacts/simulator-alpha/<실행>/` 폴더에 저장됩니다. 종료해도 삭제하지 않습니다. `verification-report.json`은 공유용 요약이며 키나 원본 world, 파일 경로를 포함하지 않습니다. 원본 진단/사건 로그는 별도 파일입니다. **리포트는 서명되지 않은 로컬 요약**이므로 독립 검증이나 인증서로 취급하지 마세요.

포트가 사용 중이면 기존 실행을 종료하거나 다음처럼 바꾸세요. Gazebo 원본 포트도 함께 적용됩니다.

```bash
HAETAE_DEMO_PORT=8766 HAETAE_GUI_PORT=6081 ./haetae-demo start
```

이 도구는 같은 프로젝트가 생성한 `haetae-demo` 컨테이너만 종료합니다. 동명 다른 컨테이너는 조작하지 않습니다. 다른 프로젝트의 포트 점유도 자동으로 종료하지 않습니다. 기존 `ros/gazebo/run_docker.sh`는 이 실행 명령으로 연결됩니다.

`hazards`는 사람 보호 공간, 열원, 전기, 물, 세정제 이력, 추락 공간의 6개 위험한 서명 명령을 중앙 Rust 게이트에서 차단하고 정상 이동을 대조하는 별도 프로필입니다. 검사 참조 누락·잘못된 버전·물체 참조도 실제 서명 경로에서 거부합니다. 물체·기기 상태는 신뢰된 시험 입력입니다. 최종 제어기의 승인 증명 검증·실물 보호·실제 인지·파지·화학 반응·사람 밀기 방지는 미검증입니다. [생활 위험 실험의 범위](../examples/household-hazards/README.md)와 [필수 검사 계약](household-gate.md)을 확인하세요.

## 다운로드 가능한 검증 후보

[CI](https://github.com/haetae-robotics/haetae/actions/workflows/ci.yml)의 성공한 실행에서 `simulator-alpha-candidate` 아티팩트를 받으세요. 소스 압축 파일, 기존 보안 시험의 `verification-report.json`, 생활 위험 실험의 `household-verification-report.json`, 소스 커밋/트리와 증거 해시를 기록한 `manifest.json`, `SHA256SUMS`가 들어 있습니다. GitHub 로그인 여부에 따라 아티팩트 다운로드가 제한될 수 있습니다.

```bash
# macOS
shasum -a 256 -c SHA256SUMS
# Linux
sha256sum -c SHA256SUMS

tar -xzf haetae-simulator-alpha.tar.gz
cd haetae-simulator-alpha
./haetae-demo start
```

패키지는 두 프로필의 리포트가 모두 통과하고, 모든 필수 시험과 복합 장애 시험 6회(40·80·100Hz 각각 2회 이상)가 통과하고 증거의 소스 커밋이 패키지와 일치할 때만 생성합니다. 동일한 커밋과 증거 입력의 소스 압축 파일은 같은 바이트를 만듭니다. Docker 이미지·Ubuntu/ROS 의존성 해석은 고정하지 않았으며, 아티팩트 서명과 바이너리 재현성은 보호용 릴리스의 남은 조건입니다. 추출한 소스를 수정했다면 `REVISION`을 지우고 버전이 `unknown`인 개발 실행으로 취급하세요.

## 검증 범위

- 제한된 제안 노드·서명 노드는 바퀴/팔 제어와 사람 정보 위조 권한을 얻을 수 없습니다. 네 역할 및 외부 공격자 계정의 Gazebo 직접 통신도 제한합니다.
- root 제공자, 호스트, Gazebo/컨트롤러, 신뢰된 인지 입력은 신뢰합니다. 게이트웨이가 이미 가진 정당한 제어 권한을 악용하는 경우도 보호 범위 밖입니다.
- 복합 시험은 초당 40·80·100개 요청과 센서 전달 끊김·서명 처리 지연을 겹칩니다. 센서 신선도 200ms, 양의 응답 제한 50ms, 팔 독립 임대 250ms는 유지합니다. 일반 서비스 거부 공격이나 최악 지연 보장은 아닙니다.
- 라이다 관측 불가는 confidence 0인 서명 관측으로 전달해 root 정책의 `perception-unknown` 규칙으로 즉시 거부·정지합니다. 표적 소실 시험은 해당 관측과 Rust 취소 기록, disarm, 후속 0출력을 함께 요구합니다. 관측·서명 경로 자체가 사라지면 기존 신선도 만료 정지를 적용합니다. 요청부터 0출력까지 400ms 제한과 회복 후 자동 재가동 금지는 유지합니다.
- 라이다는 한 높이의 보수적 장애물 측정입니다. 아동·기어가는 사람·복잡한 가림·실물 센서 공격을 검증하지 않았습니다. 3D 팔 충돌 방지와 실물 정지 성능도 검증하지 않았습니다.

상세 근거와 보호용 배포 조건: [Gazebo runbook](gazebo-reference.md), [security release gate](security-release.md), [취약점 비공개 신고](../SECURITY.md).
