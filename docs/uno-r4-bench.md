# UNO R4 도착 전 준비와 첫 실물 시험

대상: **정식 Arduino UNO R4 Minima (ABX00080)**. USB와 내장 LED만 사용한다.
USB를 신뢰하는 H1 시험이다. 보드가 인증된 실행 허가를 검사하는 후속 구현은
[H2 controller permits](controller-permits.md)를 따른다.
외부 모터·릴레이·로봇 제어선은 연결하지 않는다. LED ON은 가상 이동 명령이
Rust 판정 엔진을 통과했다는 표시다. 로봇 정지 검증은 아직 남아 있다.

## 지금 실행하기

저장소에서 실행한다. Python 3.9 이상, Rust/Cargo, C++17 컴파일러가 필요하다.
Mac은 Xcode Command Line Tools, Linux는 C++ 컴파일러를 사용한다.

```bash
python3 -m pip install cryptography
./haetae-bench verify
./haetae-bench compile
```

`run`은 깨끗한 체크아웃에서 완료된 `verify`의 커밋, 실행 파일 해시, 펌웨어 해시를 확인합니다. 코드나 바이너리가 바뀌면 다시 `verify`하세요.

`verify`는 실제 서명 검증 Rust 엔진과 펌웨어와 동일한 C++ guard를 별도
프로세스로 실행해 가상 직렬 포트(PTY)로 연결한다. 먼저 ON을 관측한 뒤,
사람 접근·세계 입력 단절·서명 위조·재전송·Rust 종료·호스트 강제 종료와
일시 정지·오래된 USB 명령·과대/미완성 프레임·상태 조회만 지속하는 상황을 시험한다.
상태 조회는 출력 임대를 갱신하지 않으며 장애 후 자동 ON도 금지한다.

`artifacts/bench/report.json`의 `physical_hardware_tested`,
`electrical_output_measured`, `usb_and_watchdog_tested`는 **false**다.
핵심 guard의 정확한 200ms 경계는 단위 시험에서 확인하며, 프로세스 관측은
OS 지연을 포함해 300ms 이내를 확인한다. 실제 GPIO 응답 측정이나 기존
Gazebo 정지 기준을 대신하지 않는다.

`compile`은 Arduino CLI **1.5.1**, core **arduino:renesas_uno 1.6.0**을
`artifacts/`에 설치해 `arduino:renesas_uno:minima`용 스케치를 빌드한다.
CLI 공식 SHA256 목록과 대조하고 core/tool은 Arduino CLI 패키지 검증을 사용한다.
전역 Arduino 설정은 변경하지 않는다.

Apple Silicon에서 Intel 컴파일러가 실행되지 않으면 Linux amd64 Docker로
컴파일한다. Docker가 실행 중이어야 한다. **USB 업로드는 Mac에서 직접 한다.**
해당 core의 Mac 업로더도 Intel용이므로 Rosetta가 필요하다. `flash`는 이를
검사하고 설명하며 Rosetta를 자동 설치하거나 라이선스에 동의하지 않는다.
[Apple Rosetta 안내](https://support.apple.com/en-us/102527)와
[Arduino core 1.6.0](https://github.com/arduino/ArduinoCore-renesas/tree/1.6.0)을 참고한다.

## 보드 도착 후

USB-C **데이터 케이블**로 연결하고 Serial Monitor 등 포트를 사용하는 앱을 닫는다.
`ports`에서 UNO R4 Minima와 포트를 확인해 아래 `PORT`를 `/dev/cu.usbmodem…`로 바꾼다.
Linux는 `/dev/ttyACM…`의 접근 권한도 필요하다.

```bash
./haetae-bench ports
./haetae-bench flash --port PORT
./haetae-bench run --port PORT --scenario person
```

`flash`는 감지된 UNO R4 Minima만 허용하고 기존 스케치를 새 LED 펌웨어로 덮어쓴다.
기대 동작은 **LED ON → 약 3초 뒤 가상 사람 접근 → LED OFF**다.
터미널에도 `허용`, `차단`이 표시된다. 사람 입력은 합성 시험 데이터이며,
실제 사람을 감지하는 센서 기능은 포함하지 않았다. 새 `run` 명령으로만 다시 켤 수 있다.
`--scenario world-loss`, `replay`, `invalid-signature`도 시험할 수 있다.

## 첫 독립 정지 시험

```bash
./haetae-bench run --port PORT --scenario allow --seconds 30
```

먼저 LED ON을 확인한다. 다른 터미널에서 `ps`로 이 `run`의 PID를 확인한 뒤
`kill -STOP PID` 또는 `kill -KILL PID`를 실행한다. **USB 전원은 유지한다.**
LED가 스스로 꺼지고 `kill -CONT PID`로 복구해도 자동 ON이 없어야 한다.
일반 Ctrl-C는 STOP 전송 경로 시험이다. USB를 뽑으면 전원도 사라져 LED OFF만으로
timeout을 증명할 수 없다. ON이 관측되지 않은 정지 시험은 유효하지 않다.

육안 확인 후 계측기로 GPIO timeout, USB 버퍼 정체, reset 중 핀 상태와
MCU watchdog reset을 별도 측정한다. WDT 요청값은 500ms이며 라이브러리가
선택하는 실제 interval과 reset 동작은 실물 검증 전이다. 로봇 정지 시간으로 쓰지 않는다.
업로드 실패 시 Arduino 공식 복구 절차에 따라 RESET을 빠르게 두 번 누르고
포트를 다시 확인한다. 재연결/재부팅 후에도 새 `run`이 필요하다.

## 경계

호스트는 세계/VLA/로그 시험 키를 모두 가지며 직접 USB 작성자도 신뢰한다.
서명 위조 시험은 Rust 입력 경계를 검증한다. USB의 session/sequence/challenge는
유효 기간·중복 검사이며 **인증·암호화가 아니다**. 악성 USB 작성자는 새
HELLO/ARM/RUN을 만들 수 있고 재부팅 전후 전체 transcript 재전송도 막지 않는다.
새 run은 새로운 임시 normal 상태/키로 시작한다. 실물 로봇의 incident 상태나
인증 카운터를 재시작 사이에 유지하는 운영 구성으로 사용할 수 없다.
기존 Gazebo의 ROS 역할 격리 시험은 별도다.

도착 후 USB/LED와 강제 종료·복구 잠금을 확인하고, 다음으로 계측과 저전력
모터 드라이버의 독립 enable 차단을 설계한다. 이 bench 통과는 실물 침투 방어,
모터 정지, 안전 인증의 통과가 아니다.
[통신 규약](uno-r4-protocol.md)과 [보호용 공개 조건](security-release.md)을 참고한다.
## Software runner scheduling loss

The board-free bench records a sustained allow run separately from measured
`host-scheduling-loss`. A non-real-time host can miss the independent 200ms
device lease. That path passes only the fail-closed property: prior measured ON,
lease OFF within the 200..300ms process observation band, a LOCKED status and
host gap of at least 200ms, failing host exit, and no subsequent ON/rearm.
It is not a normal availability success or a physical timing qualification.
The aggregate report sets `availability_degraded: true` whenever a case has
this classification, even when its fail-closed checks pass.
The Rust-kill case requires an observed `stop` event; a lease timeout cannot
pass that STOP-delivery check. The signed-controller socket uses bounded bulk
reads and complete writes against each frame's original monotonic deadline.
Failures retain the last four request/gate/sign/response stage timestamps.
Its operational request/acknowledgment budget remains 50 ms.

### Timing-inconclusive attempts

Shared CI runners can stretch the host's 25 ms wait between renewals (macOS
timer coalescing) or pause the whole VM. In the H1 bench and the signed H2
bench, a scenario attempt is timing-inconclusive only when all of these hold:

- The host or relay exited with one of the bounded deadline or lease messages
  (a 50 ms gate, IPC or USB deadline, or a locked controller after its 200 ms
  lease).
- No fault had been injected, no stop decision had reached the host or relay,
  and the H2 authorizer had logged no terminal. A STOP that fails after the H1
  host's gate stop or allow completion therefore does not count.
- Neither H2 process crashed with a Python traceback, and the H2 authorizer
  exited by itself, so its log is complete.
- If the H2 relay received the authorizer's reply to its last request but the
  controller never acknowledged that request (the relay's last stage has
  `response_received_ns` but no `controller_ack_ns`), the authorizer's
  failure trace holds the stage that read that request (the first stage
  whose `request_first_byte_ns` is at or after the relay's
  `request_send_started_ns`), and that stage has `response_sent_ns`. The
  authorizer prints a terminal only after its send returns, and the send
  checks its deadline once more after writing the reply, so a terminal can
  reach the relay with no terminal line in the log. A completed send means
  any terminal it carried was logged.
- The device stopped fail-closed, and no ON follows. Its first OFF is the
  lease, or a RUN refused because the lease had ended, at 200 ms or more after
  the last renewal. After a deadline it may instead be the host's or relay's
  STOP or a stale refusal, but only within 300 ms of the last renewal, the
  bound every passing attempt meets. The emulator writes at most one row per
  loop pass, so a lease that ends in the same pass as a STOP can show only as
  the STOP. A later STOP or stale refusal with no lease before it fails at
  once.

The harness sees decisions that reached the host or relay, and every terminal
the H2 authorizer logged. A decision made after the deadline that the harness
cannot see does not prevent a restart. For H2 this is an authorizer terminal
(a gate denial, gate expiry, authorizer failure or allow completion) that the
authorizer could not finish sending, so its log has no terminal line, and that
the relay did not receive before its own deadline. A terminal the authorizer
logged fails the attempt even if the relay never read it. For H1 it is a gate
reply that came after the host's 50 ms wait had ended. A denial before the
fault that coincides with such a delay is therefore restarted rather than
failed.

An inconclusive attempt never counts as a pass. The scenario restarts with a
fresh device emulator and fresh processes, at most three attempts, and three
inconclusive attempts fail it. Every other failure, any loss after the fault
was injected or after a stop decision reached the host or relay, and any ON
after a stop fail at once. The interruption cases (host kill, host pause, Rust
kill, relay pause, authorizer kill) restart only for a loss before positive
control; after the interruption they run once. A passing attempt keeps every
existing check, including the 300 ms observation band. `report.json` records
each attempt's renewal gaps, ARM-to-first-RUN time and STATUS-to-RUN-verified
time, and the evidence of each inconclusive attempt, as a non-blocking
measurement. The bench prints one GitHub Actions warning per inconclusive
attempt. The 50 ms and 200 ms limits are unchanged.
