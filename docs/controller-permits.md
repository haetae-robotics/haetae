# Controller-authenticated LED permits (H2)

H2 is a controller-side authorization reference for the official **UNO R4
Minima built-in LED**. It does not implement base/arm trajectory permits or
complete household milestone M3. H1 remains a separately labelled trusted-USB
bench. Real perception, motors, electrical timing, secure boot and physical
tamper resistance are outside this LED result.

## Quick start

```bash
python3 -m pip install cryptography
./haetae-permit verify
./haetae-permit provision --directory artifacts/my-controller
./haetae-permit compile --deployment artifacts/my-controller/deployment.json \
  --controller-key artifacts/my-controller/controller.seed
./haetae-bench ports
./haetae-permit flash --port PORT --deployment artifacts/my-controller/deployment.json \
  --controller-key artifacts/my-controller/controller.seed
./haetae-permit verify-usb --port PORT --device-directory artifacts/my-controller
./haetae-permit run --port PORT --device-directory artifacts/my-controller
```

`run` starts one operator session: fresh signed Rust decisions light the LED;
after three seconds synthetic person input causes a stop. Other scenarios:
`world-loss`, `replay`, `invalid-signature`, `allow`. Only LED_BUILTIN is driven.
The same-UID developer launcher trusts the host: process separation alone does
**not** protect keys from another program with that UID.

Use a clean checkout. `run` requires successful software and physical USB
reports for the current commit, matching gate binary, firmware sources, build
manifest and private image. Repeat verification after a source/build change.
These local records are operator evidence, not authenticated on-device firmware
attestation; root and the developer host remain trusted.

Provisioning never overwrites a directory. Keep `authorizer.seed` with the
trusted authorizer and `controller.seed` with the device provisioner. Public
`deployment.json` binds installation, authorizer Ed25519 public key and MCU
X25519 public key. Private directories are 0700 and seeds are owner-only.
Generated headers and device firmware contain MCU secrets: **do not publish
images, build directories or seeds**.

## Authority and timing

```mermaid
flowchart LR
  W[Trusted synthetic observer] --> G[Actual signed Rust gate]
  G --> A[Isolated authorizer and private key]
  A --> R[Untrusted packet relay]
  R --> C[MCU verifies authenticated permit]
  C --> L[Built-in LED only]
```

The authorizer owns `BenchGate`, its signed inputs and scenario clock. Requests
contain controller context, never relay decisions/worlds/arbitrary bytes to
sign. Each ARM/RUN needs a fresh positive actual Rust result with healthy
state/recording and the existing freshness recheck. Denial/context/transport
failure ends the invocation. Explicit restarts create new synthetic fixtures,
not durable real robot incident/effect history.

The response contains a permit or a terminal envelope. A relay can observe
authorization metadata and denial reasons, but cannot create valid authority.
The MCU independently checks the signature/MAC; an IPC field never authorizes
output by itself.

Per-command Ed25519 took 65.398 ms on the development board's USB reply and was
correctly rejected under 50 ms. O3 also failed. Therefore **Ed25519-signed BIND
and X25519 key agreement run under a 500 ms OFF-only initialization bound**;
BIND cannot arm or renew output. Every exact ARM/RUN then carries a
**BLAKE2b-256 MAC** under its secret RAM connection key, never disclosed to the
relay. These are authenticated permits, not per-action Ed25519 signatures.

Positive gate-to-ACK remains **<50 ms**. MCU permit age is **<50 ms**, checked
before and after verification. The independent lease is **at most 200 ms from
the original challenge issue time**, never receipt. STATUS cannot renew it.
Failures clear output, arming and connection key. Permits bind domain,
installation, durable boot epoch, MCU generation, exact action, sequence,
one-use challenge, original stamp, duration and an unpredictable 128-bit MCU
nonce. The private keyed nonce prevents pre-staging future permits while the
world is safe; no host randomness is trusted.

Unchanged [Monocypher 4.0.3 sources/hashes](../hardware/uno_r4_permit/src/vendor/upstream.json)
are shared by sketch and native tests; [upstream manual](https://monocypher.org/manual/ed25519).

## Protocol

115200 8N1 ASCII, LF, no CR, at most 255 printable bytes. Partial frames expire
at 100 ms. Decimal u32 is canonical (no leading zeros except `0`); hex is
lowercase. RX consumes at most 32 bytes/loop. Counters never wrap.

```text
H2 HELLO
H2 STATUS
H2 STOP
H2 BIND <generation> <next_sequence> <challenge> <issued> 0 <ephemeral_hex64> <signature_hex128>
H2 ARM <generation> <next_sequence> <challenge> <issued> <duration> <mac_hex64>
H2 RUN <generation> <next_sequence> <challenge> <issued> <duration> <mac_hex64>
H2 <OK|ERR> <install_hex32> <epoch> <generation> <sequence> <challenge> <issued> <nonce_hex32> <LOCKED|BOUND|ARMED|ON> <reason>
```

HELLO removes output/key, increments generation, sets sequence 0/challenge 1.
BIND establishes BOUND, still OFF. ARM creates an OFF lease; RUN turns ON.
Each consumes sequence/challenge and issues a new nonce. STATUS refreshes
challenge/nonce/issue stamp, never the lease. STOP always removes output. Protocol/replay/expiry/crypto/TX
errors latch OFF; HELLO still needs new trusted authorization. Replies are
**not authenticated telemetry**, electrical measurements or motor evidence.

ARM/RUN canonical MAC payload (76 bytes):

| Bytes | Contents |
| --- | --- |
| 0..15 | `HAETAE-LED-H2` plus three zero bytes |
| 16..31 | installation ID |
| 32..59 | seven LE u32: epoch, generation, next sequence, challenge, issue stamp, duration, action (ARM=0/RUN=1) |
| 60..75 | nonce |

BIND uses 140 bytes: same layout with `HAETAE-KEX-H2` plus three zeros,
duration 0/action 2, then ephemeral public key (32 bytes), controller public
key (32 bytes). MCU X25519 private key is keyed BLAKE2b-256 of
`HAETAE-X25519-H2` under its private master. Reject all-zero shared secrets.
Connection key is keyed BLAKE2b-256 of the complete BIND payload under the
shared secret. Challenge PRF uses the LED domain with action 2 under the separate
MCU master key; it does not use the RAM session key or KEX certificate domain.

## Reboot, update and deployment boundaries

Before any USB challenge, startup reserves an epoch in a 4 KiB append-only
data-flash journal. The hardware BlankCheck API determines blank cells because
[Renesas documents](https://renesas.github.io/fsp/group___f_l_a_s_h___l_p.html)
that erased data-flash reads are not guaranteed to return FF. Each contiguous non-erased four-byte word consumes an
epoch, including torn reservations. Holes followed by data, write/readback
failure or 1024-slot exhaustion lock the MCU. **No runtime erase/reset command**.
Trusted physical reprovisioning needs new identity **and keys**. Power-cut
behavior is not physically qualified. Do not combine the reserved flash region
with another EEPROM/data-flash application.

The build adds a distinct local board mapping, verifies the CLI actually
resolves its hardened core, checks pinned Arduino 1.6.0 source hashes, omits
runtime DFU descriptor, makes DFU-detach reboot inert and disables CDC1200
reset. Stock core is untouched. [Upstream USB descriptors](https://raw.githubusercontent.com/arduino/ArduinoCore-renesas/1.6.0/cores/arduino/usb/USB.cpp)
and [reset callbacks](https://raw.githubusercontent.com/arduino/ArduinoCore-renesas/1.6.0/cores/arduino/usb/SerialUSB.cpp)
are the specific paths changed. A manifest binds stock/modified source and
private image hashes. Subsequent maintenance requires double-press RESET,
detect the DFU port and upload. Stock watchdog USB re-enumeration can fail;
physical entry is the supported recovery. No unauthenticated remote escape
hatch is supplied. This removes two runtime programming entry paths;
**it is not secure boot or defense against physical reset/debugger/host root**.

`verify` checks portable crypto, epoch model, protocol attacks, actual Rust
authorizer and relay failures. Hazards require positive ON and intended fault
evidence; early failure cannot pass. Software/physical USB reports are separate.

Linux root may run `sudo -E env PATH="$PATH" python3 haetae-permit verify-isolation`.
Authorizer UID2001, gateway2003 and AI2004 test key read/write/signal denials,
legitimate ON and actual Rust synthetic-person denial. Fixtures use `/dev/shm`
when available. This is software isolation, not physical USB/kernel proof.
Root, provisioner, observer, Rust gate, MCU firmware and USB stack stay trusted.
Deploy authorizer/relay under distinct identities; expose only the socket to
relay's group and withhold seeds/private images/programming/admin access from
AI/relay. File permissions cannot defend against root. Real robots still need
exact command integration, measured perception, durable incidents/effects,
independent electrical stop and robot-specific qualification.

## First USB board result (2026-10-05)

The official USB-only Minima passed 13 H2 checks: CDC-only runtime descriptors;
five forged/replayed/context-mismatched permit cases; STATUS without lease
renewal; four actual Rust synthetic fault cases; relay pause/resume without
automatic rearm; and 1200-baud DTR without changing boot/generation.
Every actuating case first requires a positive USB ON control. Native software
verification also passed 17 cases, and Linux UID2001/2003/2004 separation
denied key read/write/signalling while allowing the authenticated reference.
The exact reviewed commit and final CI evidence are recorded in
[PR19](https://github.com/haetae-robotics/haetae/pull/19).

These results use MCU USB replies and trusted synthetic inputs. They do not
measure GPIO/electrical output, watchdog reset, flash power-cut behavior,
motors, real perception, or resistance to physical reset/debugger/host root.
