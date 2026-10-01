# UNO R4 LED bench protocol H1

Trusted direct USB on macOS/Linux, official UNO R4 Minima, built-in LED only.
`hardware/uno_r4_bench/guard.h` is shared by the sketch and native tests.
No authenticated USB, motor controller, ROS input transport or cross-reset replay protection.

115200 8N1, ASCII, exactly one space between fields, LF ending (no CR).
At most 95 printable bytes before LF; partial lines expire at 100ms.
Counters are unsigned decimal u32; session is 16 lowercase hex digits.

```text
H1 <HELLO|ARM|RUN|STOP|STATUS> <session> <sequence> <challenge>\n
H1 <OK|ERR> <session> <sequence> <next_challenge> <LOCKED|ARMED|ON> <reason>\n
```

HELLO uses sequence/challenge zero and a new random host session, removes
output and offers challenge 1. ARM consumes it once, enabling a 200ms lease
with output OFF. RUN turns ON and renews it. Subsequent commands require exactly
the next sequence and preceding response's challenge; accepted commands rotate
the challenge. ARM/RUN require challenge age **<100ms**. STOP and STATUS accept
an older challenge while still enforcing session/sequence/token. STATUS only
observes state and rotates the challenge; it never arms or renews output.
Any syntax/counter error also removes output.
Counter exhaustion rejects rather than wrapping.

Lease expires at **>=200ms**, including before processing a queued RUN.
STATUS never renews it. STOP, parser/partial/oversize errors, replay, stale
challenge, timeout and TX failure clear output and arming. That session cannot
ARM or RUN again. A new explicit operator invocation opens a NEW HELLO session;
the host never retries HELLO or auto-reconnects. HELLO is not authentication.
A malicious USB writer can create a fresh session or replay one after MCU reset.

Host responses must match session, sequence, next challenge and expected state;
trailing/batched replies are rejected. USB uses the remaining **50ms aggregate
gate-to-ACK budget**. The host rechecks proposal expiry and 200ms world freshness
before RUN and after ACK. Error/denial sends best-effort STOP and exits.
A late ACK is not success; RUN may already have reached the device, so the
independent lease remains essential.

Before each positive cycle the host polls STATUS to acquire a fresh challenge
and refuses a LOCKED response. Inter-cycle scheduling can age the previous
challenge without making the new gate decision stale. Polling never extends
the existing lease: a resumed host still encounters LOCKED after expiry.

Sketch deadlines use the board clock. It reads at most 32 bytes per loop,
applies LED state before replying and removes output if TX space is insufficient.
Hardware WDT is requested at 500ms and refreshed after each completed loop.
Initialization failure leaves LED OFF and processes no commands. Actual WDT
interval, GPIO, USB backpressure and reset/bootloader behavior remain unmeasured.
Native tests prove guard/POSIX process behavior only, not electrical timing.

Each run uses fresh random ephemeral Ed25519 keys, a root-signed policy-bound
trust bundle, owner-only seed files, the actual `haetae enforce --stdio` process,
session-local persisted state and incident recording. Signed fresh world plus
signed zero arm the Rust source. Only a positive normal, recorder/state-healthy,
fresh result maps to RUN. A new run intentionally creates a new normal test
fixture; it is unsuitable for preserving robot incident mode/auth counters
across restarts. Secured ROS deployment identity/state rules remain separate.

Process/trust/state initialization has a bounded 500ms RPC budget while no
USB session has been armed. Only world updates and signed zero are sent then.
A fresh world/zero completes bootstrap, and the IPC timeout becomes 50ms
before any positive cycle or USB ARM. Slow-start tests verify that separation.
