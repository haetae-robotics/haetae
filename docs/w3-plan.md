# W3 plan: make one mobile base actually stop

One week, Claude + Devin. Dev machine is macOS with no ROS 2 or Docker; ROS 2 runs
only in GitHub Actions (`ros:jazzy`). Subagents run no git commands; Claude commits,
pushes and iterates on CI.

## 0. Entry gate (before W3 starts)

PR #2 (`w2-runtime`) carries the audit fixes, and CI is green on every job:

| id | fix | pinned by a test |
|---|---|---|
| S1 | `seal()` flushes and fsyncs; `create()` fsyncs the file and its parent directory | seal-then-drop leaves a verifiable log |
| S2 | the CLI's `verify` exits non-zero (not `"ok": true`) for an empty log or an unsealed tail | CLI exit-code tests |
| S3 | a torn last line is reported as a torn tail, and every sealed entry before it still verifies | truncated-mid-line fixture |
| S4 | the log is created `0o600` on unix | mode check |
| R1 | `Runtime::handle` always returns the Decision; recorder failures are latched; the key is kept and creation is retried (w2 §5.1) | failed-create-then-retry test |
| C1 | `sillok replay` escapes control characters in untrusted strings | ESC/CSI fixture |
| D1 | README says only speed is clamped, lists sillok as shipped, and has a "what it is not" section | review |

The three cargo gates (`test`, `clippy -D warnings`, `fmt --check`) pass locally,
and CI is green after the push. W3 bumps the version to 0.0.3.

## 1. Direction

**Enforcement first.** The audit's top blockers are (1) nothing applies decisions to
motors and (6) approvals are never revoked. Until both are fixed, signing the inputs
protects a gate that a model can simply bypass. So W3 turns Haetae into the only
writer of `/cmd_vel` for a differential base: it re-judges continuously and fails
to zero velocity.

Two items come over from the trust-first draft because they are cheap and affect
safety:

- **Mode persistence with a safe start.** An E-stop survives a restart. A missing
  or corrupt state file starts the gate in `Hold`.
- **A bridge that runs all the logic in Rust.** The ROS node is a thin rclpy
  process around `haetae enforce --stdio`. Everything that decides can then be
  tested on macOS. r2r and its libclang, codegen and lockfile risk move to W4.
  (This reverses the enforcement-first draft: its Plan B becomes Plan A.)

Signed envelopes, the signed bundle, signed online reset and sillok anchoring move
to W4 as a whole.

Blocker coverage at the end of W3:

| blocker | W3 result |
|---|---|
| (1) no enforcement point | **Closed for a diff-drive base.** Haetae is the only `/cmd_vel` writer by topology; it outputs zero on any doubt, and the base's deadman covers a dead Haetae. Detection only, not prevention, against a rogue extra publisher (SROS2 in W4). |
| (2) no ROS 2 node | **Closed.** An rclpy node, tested end to end in CI. Not an ament or colcon package yet. |
| (3) no certification path | **Positioning.** Documented as a non-safety supervisory layer; its stop is a control stop, not a safety-rated stop. |
| (4) unauthenticated inputs | **Partly.** Each source is bound to its input topic rather than self-declared. Signatures come in W4. |
| (5) geometry and action representation | **Partly.** Velocity commands, a disc footprint, and a swept arc with braking. Arms, joint deltas and chunks stay out. |
| (6) no continuous monitoring | **Closed.** The active command is re-judged on every world update and every tick, and can only tighten. |
| Major: mode lost, reset unauthenticated | **Mode persisted.** Mode is lowered only by an offline, local, lock-guarded CLI. Signed online reset comes in W4. |

## 2. Goals and non-goals

**Must (P0)**

- **G1** `ActionKind::Velocity` in core: disc footprint, a horizon sweep that includes
  latency, TTL and braking, and a clamp that keeps the curvature.
- **G2** `Runtime::rejudge` with `revoke` and `stop` records.
- **G3** A new crate, `haetae-enforce`: a pure-Rust state machine (watchdog, re-arm
  latch, tighten-only, 20 Hz heartbeat) with no ROS dependency.
- **G4** Mode persistence: the state file is `0o600` and replaced atomically, and a
  missing or corrupt file means `Hold`. Lowering the mode goes through
  `haetae state set`, which is refused while the gate runs.
- **G5** `haetae enforce --stdio`: the JSONL process protocol (§4.5).
- **G6** `ros/haetae_gate`: the rclpy node (the only `/cmd_vel` publisher, never judges)
  plus `bridge.py`, a pure-Python core.
- **G7** `ros/haetae_sim`: a kinematic diff-drive simulator (`/clock`, deadman,
  deceleration limit, scripted humans, oracle), with a pure-Python core.
- **G8** CI: a `ros-e2e` scenario matrix that runs sillok verify for each scenario and
  uploads artifacts.

**Should (P1)**

- Footprint inflation for `MoveTo`/`Grasp`/`Place` segments when `base` is set.
- Rogue-publisher detection: `count_publishers("/cmd_vel") > 1` raises a `Hold` fault.
- Scenarios 9–10.

**Could (P2)**

- An SROS2 permissions file limiting `rt/cmd_vel` to the gate enclave, as config only.
- Replaying a trajectory in the `sim/` browser simulator.

**Non-goals (stated in `docs/w3-contract.md` §Carry-over)**

- Safety certification or a safety-rated stop.
- Arms, 3D, joint deltas, EE twists and action chunks.
- Gazebo, real hardware and a perception adapter. The world is built from the
  simulator's ground truth.
- Signed proposals, worlds and faults, the signed policy bundle, signed online
  reset, key rotation and log anchoring.
- r2r or rclrs nodes, ament/colcon packaging, custom msgs.
- Polygon footprints. The disc means an in-place rotation sweeps nothing new; this
  is documented.
- nav2 integration beyond documenting the remap.
- Semantic rules beyond exact string matches, and publishing the crates.

## 3. Design decisions

1. **Logic in Rust, transport in Python.** Every decision is made in `haetae-enforce`
   and tested on macOS. The rclpy node stays under about 150 lines: it converts
   callbacks, stamps `recv_ms` at callback entry, forwards each input over stdio, and
   publishes exactly what comes back.
2. **Stop first, then record.** The stdio loop writes and flushes the output line
   *before* `Enforcer::commit()` seals and fsyncs the log and persists the state
   file, so an fsync never delays a zero.
   - This deviates from w2 §5, which fsyncs before `handle` returns. W3 still
     guarantees durability before the next input is read.
3. **Latched stops need a re-arm.** Any stop caused by the world, a denial or the
   mode clears the arming of every source. After that, a source's non-zero command
   is not judged until that source sends one zero twist.
   - Without this, a streaming upstream would resume motion on its own the moment
     the world came back.
   - The gate starts disarmed.
4. **Sim time everywhere.** The simulator owns `/clock`, and every node runs with
   `use_sim_time:=true`. Pass thresholds come from physics plus a margin, never from
   observed runs.
5. **No new crates.io dependencies.** Locking uses std `File::try_lock` (stable
   Rust), so `--locked` keeps working and Devin never touches `Cargo.lock`.

## 4. Contract sketches (fixed in `docs/w3-contract.md` on Day 1)

### 4.1 haetae-core (backward compatible)

```rust
// proposal.rs
pub enum ActionKind {
    MoveTo { .. }, Grasp { .. }, Place { .. }, Stop,
    /// Body-frame twist for a differential base, valid for `ttl_ms` after admission.
    Velocity { linear: f64, angular: f64, ttl_ms: u64 },
}
// world.rs, RobotState
#[serde(default)] pub yaw: Option<f64>,       // rad; Velocity without yaw -> bul invalid:world
#[serde(default)] pub twist: Option<Twist2>,  // measured; world-age compensation
pub struct Twist2 { pub linear: f64, pub angular: f64 }
// policy.rs, optional section; Velocity without it -> bul policy:no-base
pub struct Base {
    pub footprint_radius: f64, // m, circumscribed disc
    pub max_decel: f64,        // m/s^2, MUST equal the base controller's limit
    pub max_angular: f64,      // rad/s
    pub latency_ms: u64,       // command -> motor
    pub max_ttl_ms: u64,       // validate: <= freshness.proposal_max_age_ms
    #[serde(default)] pub allow_reverse: bool,
}
// gate.rs, Decision
#[serde(default, skip_serializing_if = "Option::is_none")]
pub expires_ms: Option<u64>,   // recv_ms + ttl_ms for Velocity
```

**Velocity judgment (`sweep.rs`).**

- The sweep speed is `v = min(|linear|, envelope.max_speed)`.
- The horizon is `T = world_age + latency + ttl + v/max_decel`, assuming constant
  velocity over all of it, which is conservative.
- The closed-form unicycle arc is sampled with a spacing of `Δs ≤ r/2`, at most
  64 samples.
- The inflation is `r + |twist_meas|·world_age + sagitta(Δs, R)`.

Checks, in order:

| fired | check |
|---|---|
| `envelope:ttl` | `ttl_ms` is 0 or greater than `max_ttl_ms` |
| `envelope:reverse` | `linear < 0` while `allow_reverse` is false |
| `envelope:workspace` | a sample falls outside the workspace shrunk by the inflation |
| `zone:<id>` | the inflated zone rectangle meets the polyline |
| `human_within` | polyline-to-human distance minus the inflation, against the existing rule |
| `envelope:max_speed` / `envelope:max_angular` | jeol: v and ω are scaled by the same factor, which keeps the curvature |

A zero twist is treated as `Stop` and is always `yun`.

### 4.2 haetae-runtime

```rust
pub struct RuntimeConfig { ..., #[doc = "initial mode; W2 default Normal"] pub start_mode: Mode }
impl Runtime {
    /// Re-judge an admitted proposal against the current world. Bypasses dedup.
    /// A bul records "revoke" (an incident). A lower cap records "rejudge".
    pub fn rejudge(&mut self, p: &ActionProposal, recv_ms: u64) -> Decision;
    pub fn world_age_ms(&self, now_ms: u64) -> Option<u64>;
    /// Seal and fsync records deferred by `defer_seal: true` (see §3.2).
    pub fn commit(&mut self) -> Result<(), RuntimeError>;
}
```

New sillok kinds, added to the w1 §2.1 list:

- `revoke`: an incident.
- `rejudge`: pushed to the sacho only.
- `stop`: recorded only when the stop reason changes.

### 4.3 haetae-enforce (new crate, no ROS)

```rust
pub struct EnforcerConfig { pub runtime: RuntimeConfig, pub state_path: Option<PathBuf> /* None = ephemeral */, pub tick_ms: u64 /* 50 */ }
pub struct TwistCmd { pub source: Source, pub seq: u64, pub twist: Twist2, pub ttl_ms: u64, pub claimed_stamp_ms: Option<u64> }
pub enum Input<'a> { Proposal(&'a [u8]), World(&'a [u8]), Fault(&'a [u8]), Twist(TwistCmd) }
pub enum StopReason { Startup, NoWorld, StaleWorld, NoCommand, Expired, Denied, Revoked, Mode(Mode) }
pub struct Step { pub cmd: Twist2, pub publish_now: bool, pub stop: Option<StopReason>, pub outcome: Option<Outcome> }
pub struct Status { pub mode: Mode, pub stop: Option<StopReason>, pub armed: BTreeSet<Source>,
                    pub active: Option<ActiveCommand>, pub suppressed: u64, pub world_age_ms: Option<u64>,
                    pub recorder_ok: bool, pub state_ok: bool }
impl Enforcer {
    pub fn open(policy: Policy, cfg: EnforcerConfig) -> Result<Self, EnforceError>; // reads state, takes lock
    pub fn handle(&mut self, input: Input<'_>, recv_ms: u64) -> Step;
    pub fn tick(&mut self, now_ms: u64) -> Step;   // 20 Hz; always yields a command
    pub fn commit(&mut self) -> Result<(), EnforceError>; // seal+fsync, persist state; failures also latched in Status
    pub fn status(&self) -> &Status;
    pub fn close(self) -> Result<(), EnforceError>;
}
```

Each twist is wrapped internally as an `ActionProposal`. Its `id` is the
node-assigned `seq`, which strictly increases per topic, and its `source` comes from
the topic binding.

**State machine.** Devin derives `tests/stop_matrix.rs` from this table. Every row
gets a fake-clock test.

Output rules, evaluated on every `handle` and `tick` in this order:

| # | condition | cmd | stop | effect |
|---|---|---|---|---|
| 1 | mode ≥ Hold | 0 | `Mode(m)` | drop active, disarm all |
| 2 | no world, or world age > `world_max_age_ms` | 0 | `NoWorld` / `StaleWorld` | drop active, disarm all |
| 3 | `now ≥ expires_ms` | 0 | `Expired` | drop active (no disarm) |
| 4 | no active command | 0 | `NoCommand` (`Startup` before the first arm) | none |
| 5 | otherwise | active (capped) | none | none |

Events:

| event | effect |
|---|---|
| zero twist from source s | arm s, clear active; recorded as a `Stop` proposal (yun) |
| non-zero twist from an unarmed source | not judged; `suppressed += 1`; output per rules 1–5 |
| twist yun/jeol from an armed source | replaces active (latest wins); `expires_ms = recv + ttl` |
| twist bul | 0, `Denied`, drop active, disarm all |
| world update → `rejudge` bul | 0 with `publish_now`, `Revoked`, disarm all, `revoke` incident |
| world update → lower cap | scale down, `rejudge` record; a higher cap is ignored (tighten-only) |
| fault raising ≥ Hold | 0 with `publish_now`; the state file is persisted on `commit` |
| recorder or state write failure | latched in `Status` (w2 §5.1); mode unchanged |

Every tick publishes something, zeros included. That output is the heartbeat the
base's deadman watches.

### 4.4 State file and offline reset

- The file is `state.json`, mode `0o600`, containing
  `{"v":1,"mode","reason","set_by","ts_ms"}`.
- It is replaced atomically: write a temp file, fsync it, rename it over the old
  one, fsync the directory.
- `open` takes an exclusive `try_lock` on `state.json.lock` and holds it for the
  whole run.

| on open | start mode |
|---|---|
| file missing | `Hold` (`first-boot`) |
| unreadable, unknown `v`, or unknown mode | `Hold` (`state:untrusted`), plus a sacho `note` |
| valid | the persisted mode |

- On a raise: apply in memory, publish zero, persist in `commit`.
- `haetae state show|set --state P --mode <m> --by <name> --reason <text>` is the
  only way to lower the mode.
  - It fails if the lock is held, meaning the gate is running.
  - It needs filesystem access on the robot, which is W3's interim "authentication".
    Signed online reset comes in W4.
- `haetae enforce` refuses to run without `--state` unless `--ephemeral`.

### 4.5 `haetae enforce --stdio` (the process protocol)

The child's arguments are `--policy`, `--state`, `--sillok` and `--key`. Every input
line gets exactly one output line, then the child calls `commit()`.

```text
in : {"t":u64,"k":"world"|"proposal"|"fault","data":"<raw JSON string>"}
     {"t":u64,"k":"twist","source":"vla","seq":u64,"linear":f64,"angular":f64,"ttl_ms":u64,"stamp_ms":u64|null}
     {"t":u64,"k":"tick"}
out: {"cmd":{"linear":f64,"angular":f64},"now":bool,"stop":"stale_world"|null,"outcome":<Outcome>|null,"status":<Status>|null}
```

- `status` is sent on ticks only.
- A malformed input line produces an `Outcome::Rejected` line, never a crash.

### 4.6 ROS 2 surface (`haetae_gate`, extends w2 §7)

| topic | dir | type | QoS |
|---|---|---|---|
| `inputs[i].topic` (e.g. `/vla/cmd_vel`) | sub | `geometry_msgs/TwistStamped` | reliable, keep-last 1 |
| `~/proposal`, `~/fault` | sub | `std_msgs/String` | w2 §7 |
| `~/world` | sub | `std_msgs/String` | best_effort, keep-last 1 |
| `/cmd_vel` (remappable) | pub | `TwistStamped`; `Twist` when `output_stamped:=false` | reliable, keep-last 1 |
| `~/decision`, `~/outcome` | pub | `std_msgs/String` | w2 §7 |
| `~/state` | pub | `std_msgs/String` (Status JSON), 10 Hz and on change | reliable, transient_local, depth 1 |

Mapping and trust:

- `linear.x` maps to `linear` and `angular.z` to `angular`. Any other non-zero
  component gives `invalid:proposal`.
- `header.stamp` is only an untrusted claim.

Parameters: `haetae_bin`, `policy_path`, `state_path`, `sillok_path`, `key_path`,
`tick_hz=20`, `inputs=[{topic,source,ttl_ms}]`, `output_stamped=true`,
`response_timeout_ms=20`.

Node failure rules:

- A child response later than the timeout, or child EOF: publish zero once, kill
  the child, exit with code 2.
- The node never judges.
- There is no reset service.

The node runs as a plain script sourced from `/opt/ros/jazzy/setup.bash`, the same
way as `ros/smoke`. It needs no colcon.

### 4.7 Simulator (`ros/haetae_sim`)

The pure core has no dependencies and its unittests run with `python3` on macOS:

- `kinematics.py`: unicycle at 100 Hz with acceleration and `max_decel` limits.
- `oracle.py`: minimum disc-to-disc gap, zone entry, and trigger-to-v=0 time.

The node (`sim_node.py`):

- publishes `/clock`, `/odom`, and the world on `/haetae_gate/world` at 20 Hz, with
  dropout and delay injection;
- subscribes to `/cmd_vel`;
- has a deadman: no command for 250 ms means v → 0 at `max_decel`.

`run_scenario.py` does the rest:

- plays the YAML timeline: inputs, humans, faults, `kill -9`, restarts;
- writes `result.json`, `trajectory.csv` and a stdlib-only SVG.

## 5. Deliverables and acceptance

| # | deliverable | acceptance |
|---|---|---|
| D1 | `docs/w3-contract.md` | Fixes §4, the timing budget and the ROS facts from the probe job. Devin has reviewed it. |
| D2 | core `Velocity`, `Base`, `sweep.rs` | Test vectors (straight, left and right arcs, in place, reverse). A thin zone is never skipped between samples. The clamp keeps curvature. Every `Base` validate error has a case. All W1/W2 tests are unchanged and green. |
| D3 | runtime `rejudge`, `start_mode`, deferred `commit` | `rejudge` bypasses dedup; a bul is a `revoke` incident; the log verifies `fully_sealed` after `commit`. |
| D4 | `haetae-enforce` | Every row of §4.3 has a test. Also tested: tighten-only; nothing is judged while unarmed; `publish_now` before `commit`; missing or corrupt state gives Hold; E-stop survives reopen; the state file is `0o600`; a second `open` fails on the lock. |
| D5 | CLI `enforce --stdio`, `state show/set` | A macOS integration test: spawn, raise to estop, SIGKILL, respawn, estop is reported, twists get 0. `state set` fails while the gate runs and succeeds after. |
| D6 | `ros/haetae_gate` (`bridge.py` + `node.py`) | `bridge.py` passes its unittests against a fake child (timeout, EOF, malformed line). The node survives a flood of malformed messages. |
| D7 | `ros/haetae_sim` | The pure core's unittests pass on macOS `python3`. |
| D8 | CI `ros-e2e` | Scenarios 1–8 green, zero flakes over 5 repeats. Artifacts and a step summary. |
| D9 | docs | README "what it does and does not do" updated; w2 §5 deviation noted; w1 §2.1 kinds; threat model (enforced / detected / unprotected); W3 review outcome. |

**E2E scenarios.** Parameters: v = 0.8 m/s, `max_decel` = 1.0 m/s², r = 0.25 m,
`world_max_age_ms` = 200, TTL = 200 ms, deadman = 250 ms. After every scenario,
`haetae sillok verify` reports `fully_sealed: true`, except scenario 1, which leaves
no log.

| # | scenario | pass condition |
|---|---|---|
| 1 | happy: the planner crosses the room at 0.5 m/s | reaches the goal with no stop; shows the gate is not a brick |
| 2 | deny: the VLA sends 2 m/s toward `child-room` | clamped, then bul on the disc sweep; the disc never enters the zone |
| 3 | revoke: a child steps into the path at 0.8 m/s | zero within ≤ 60 ms of the first world showing the child; stops with a gap ≥ 0.3 m |
| 4 | hold: fault `raise_to: hold` while driving | zero within 1 tick; stays stopped to the end |
| 5 | stale: perception drops out | zero within ≤ 250 ms; no motion after the world returns while the VLA keeps streaming |
| 6 | TTL: upstream goes silent | zero within ≤ ttl + 50 ms |
| 7 | `kill -9` of the node (a) or of the child (b) | (a) the deadman stops the base within ≤ 250 ms; (b) the node publishes zero and exits 2 |
| 8 | estop, `kill -9`, restart | comes back in estop; every twist gets 0 |
| 9 (P1) | a second `/cmd_vel` publisher | detected, then Hold; documented as detection, not prevention |
| 10 (P1) | malformed input flood | no crash; tick jitter < 20 ms |

## 6. Work split and schedule

Ground rules:

- Devin writes new files only and runs no builds. That covers tests derived from
  the contract, Python, scenarios, CI YAML and reviews.
- Claude edits the existing crates, implements the critical path, runs every build
  and test, and iterates on CI.
- The two never edit the same file on the same day.
- On the safety-critical path, Claude implements and Devin writes the tests, on
  purpose, so each checks the other.

| day | Claude | Devin |
|---|---|---|
| Mon | `w3-contract.md`; core types plus validate; `start_mode`; deferred `commit`. `ros-probe` CI job for the first push (see §7). | Contract review; `sweep` test vectors against the fixed signature; `kinematics.py`, `oracle.py` and their unittests |
| Tue | `sweep.rs` and gate integration; `rejudge`, `revoke` and `stop` records; compile and fix Devin's tests | `haetae-enforce/tests/stop_matrix.rs`; `bridge.py` and its tests against a fake child; scenario YAMLs 1–8 |
| Wed | `haetae-enforce` (matrix green), state store and lock, `haetae enforce --stdio`, `haetae state`, SIGKILL integration test | `node.py`, `sim_node.py`, `run_scenario.py`, SVG plotter, `ros-e2e` YAML |
| Thu | Push and iterate `ros-e2e` to green on 1–8 | Review Claude's diffs against a contract checklist; scenarios 9–10; publisher detection; threat model |
| Fri | 5× repeat, remove flakes, step-summary metrics; README, contracts, W3 review outcome. Afternoon is buffer. | Independent final review, demo script, W4 issue list |

**Cut check, Wednesday evening.** If D4 and D5 are not green, drop in this order:
scenarios 9–10 and publisher detection, then segment footprint inflation, then P2.

Never cut: the re-arm latch, the watchdog, rejudge, persistence, and scenarios 3,
5, 7 and 8.

## 7. CI plan

| job | runner | what it does |
|---|---|---|
| `rust` (existing) | ubuntu, macOS | fmt, clippy, test; now includes `haetae-enforce` and the CLI integration tests |
| `dinner-party`, `python`, `ros-smoke` (existing) | as today | unchanged; W2 behaviour must not regress |
| `sim-unit` (new) | ubuntu-latest | `python3 -m unittest discover ros/` covering `bridge`, `kinematics` and `oracle` (no rclpy) |
| `build-linux` (new) | ubuntu-24.04 | `cargo build --release --locked`; uploads the `haetae` binary (noble, same glibc as `ros:jazzy`) |
| `ros-probe` (Mon, then folded into e2e) | `ros:jazzy` | Checks `Node.count_publishers` in rclpy, `ros2 interface show geometry_msgs/msg/TwistStamped`, and sim-time timer cadence. Records the Jazzy `diff_drive_controller` stamped-input default. |
| `ros-e2e` (new) | `ros:jazzy`, needs `build-linux` | Matrix over scenarios. Each runs `run_scenario.py`, asserts on `result.json` and runs `haetae sillok verify`, uploads the log, CSV, SVG and result, and writes a step-summary row (stop latency, minimum gap). A `workflow_dispatch` input `repeat` (5 on Friday) re-runs each scenario. Target: under 10 minutes. |

Push budget: Monday for the probe; Wednesday evening for the first full e2e;
Thursday for as many pushes as needed; Friday for the repeat run.

## 8. Timing budget (normative in the contract)

| item | value |
|---|---|
| processing, world in to zero out (`publish_now`) | < 5 ms, child included |
| E2E revoke | ≤ world period (50 ms) + 10 ms |
| tick | 50 ms |
| world stale | 200 ms |
| default TTL | 200 ms (≤ `max_ttl_ms` ≤ `proposal_max_age_ms`) |
| node response timeout | 20 ms |
| base deadman | 250 ms, about 5 missed ticks |

The policy's `max_decel` and `latency_ms` must equal the real base controller's
settings. If they are wrong, the sweep means nothing. This is stated in the README
and the contract.

## 9. Risks

| risk | mitigation |
|---|---|
| Slow CI loops: no local ROS, so every ROS check needs a push and a CI run | All logic in Rust plus pure-Python cores, all tested locally; the rclpy glue stays small; the push budget in §7 |
| Timing flakes on shared runners | Sim time; thresholds from physics plus a margin; 5× repeat on Friday |
| stdio hop latency or a hung child | 20 ms response timeout, then zero, then exit; the deadman is the backstop; latency measured in the step summary. A native r2r node is W4. |
| Devin's unbuilt code fails to compile | Signatures fixed in the contract; no new dependencies; Claude compiles it the same day |
| Arc sampling skips a thin zone | `Δs ≤ r/2`, sagitta inflation, a dedicated thin-zone test |
| The re-arm rule surprises integrators whose upstream never sends zero | Documented in the contract and README; the scenario planners send one zero first; the rule fails closed |
| fsync on an incident delays the tick | Output first, then `commit` (§3.2); jitter measured; a recorder thread is W4 |
| Overclaiming "sole writer" | Claim topology plus detection only; prevention needs SROS2 or a base-side input filter (W4) |
| Wrong ROS facts (TwistStamped default, `count_publishers`) | The Monday `ros-probe` job settles them before the contract is frozen; the output type is a parameter |
| Lost or corrupt state file | Starts in `Hold`; lowering the mode is offline only; an unsigned state file is documented (anyone with filesystem access already controls the robot) |
| "How is this different from nav2 `collision_monitor`?" | It assumes the AI is untrusted, has the semantic rules and mode ladder, and keeps a signed evidence log. Positioned as complementary, to run together. |
| Scope creep (Gazebo, polygons, signing) | Listed as non-goals and moved to W4 |

## 10. End-of-week demo

Reproduced by green PR CI. Artifacts: SVG trajectories, `result.json`, sillok logs.

1. A "compromised VLA" drives the base with TwistStamped, and a child steps into the
   path. Haetae revokes the command it already approved and outputs zero within
   60 ms. The base stops at least 0.3 m away.
2. The same VLA sends 2 m/s: it is clamped, and the path toward `child-room` is
   denied based on the disc footprint.
3. Perception dropout, upstream silence and a Hold fault each stop the base. It does
   not resume while the VLA keeps streaming.
4. `kill -9` of the gate: the base deadman stops it within 250 ms. After an estop and
   a restart, the gate comes back in estop. `haetae state set` is refused while the
   gate runs.
5. `haetae sillok replay` shows the sealed `revoke` and `stop` evidence.

The demo does not show safety certification, real hardware, authenticated inputs,
or physics-level dynamics.

## 11. Moves to W4

- The trust layer: signed proposal, world and fault envelopes with `(aud, epoch,
  counter)`; the signed policy/trust bundle with anti-rollback; signed online
  operator reset; a signed state file.
- SROS2 Enforce with enclaves, and a base-side input filter.
- sillok: per-boot genesis, checkpoints and `verify --anchors`; recorder on its own
  thread.
- A native r2r or rclrs node, ament packaging, custom msgs.
- A perception adapter (odom plus person tracker), polygon footprints, arms and
  VLA action chunks.
- Gazebo or TurtleBot 4, then a real base; nav2 integration.
- Publishing the crates (`publish = true`).
