# W2 contract: haetae-runtime

Week 2 lifts the gate loop out of the `haetae` CLI into `haetae-runtime`, a
transport-agnostic library. A JSONL transport can drive it today; the W3 ROS
2 node drives the same API (§7). The crate closes the W1 review gaps that
belong to the loop: trusted receive time (M6), staleness enforcement (M5),
per-message isolation, replay detection, and no unauthenticated reset.

```
crates/
  haetae-runtime/   # world in / proposals judged / faults raise the mode / incidents recorded
```

It depends on `haetae-core` (the gate) and `sillok` (the recorder);
transports depend on `haetae-runtime` and nothing else in the stack needs to.

## 1. Time

There is deliberately no `Clock` trait. Every entry point takes
`recv_ms: u64` — the transport's **trusted receive time**.

- The gate is judged at `now_ms = recv_ms`.
- Every sillok entry's `ts_ms` is `recv_ms`. A proposal's claimed
  `timestamp_ms` and a fault's claimed `timestamp_ms` live only inside the
  entry payload — the audit timeline can no longer be chosen by the
  untrusted side (W1 gap M6).
- `recv_ms` and `WorldSnapshot::stamp_ms` must be the **same clock domain**:
  `judge_at` compares them directly for `stale:world` /
  `invalid:timestamp`.
  - ROS 2: the node clock (`node.get_clock().now()`), sampled at callback
    entry. Under `use_sim_time` that is sim time — correct, as long as the
    perception pipeline stamps `stamp_ms` from the same clock.
  - File/bag replay: the stream's message time, not wall clock. The `haetae`
    CLI advances `recv_ms` only from world `stamp_ms` (the trusted perception
    path). Proposal and fault timestamps are untrusted claims and never move
    the clock; otherwise one forged far-future line would make every later
    line stale.
  - A transport that cannot timestamp must not guess: stamp messages at
    receive upstream and pass that.

## 2. API (as implemented)

```rust
pub enum Inbound { World(WorldSnapshot), Fault(Fault), Proposal(ActionProposal) }
impl Inbound { pub fn from_json(bytes: &[u8]) -> Result<Inbound, String>; }

pub struct Fault {                          // deny_unknown_fields
    pub code: String,
    pub timestamp_ms: u64,
    pub raise_to: Mode,
}

pub enum Outcome {                          // Serialize; snake_case externally tagged
    Decision(Decision),
    WorldUpdated { stamp_ms: u64 },
    ModeChanged { before: Mode, after: Mode },
    Rejected { error: String },
}

pub struct RuntimeConfig {                  // Debug; Default = 256 / 4096 / None
    pub sacho_capacity: usize,
    pub dedup_capacity: usize,
    pub recorder: Option<RecorderConfig>,
}

pub struct RecorderConfig {                 // RecorderConfig::new(path, key): seal_every 64, post_window 8
    pub path: PathBuf,
    pub key: sillok::Keypair,
    pub seal_every: usize,
    pub post_window: usize,
}

pub enum RuntimeError {                     // std::error::Error, non_exhaustive
    Policy(PolicyError),
    Sillok(sillok::Error),
    InvalidConfig(String),
}

pub struct Runtime;
impl Runtime {
    pub fn new(policy: Policy, initial_world: Option<WorldSnapshot>, cfg: RuntimeConfig)
        -> Result<Runtime, RuntimeError>;
    pub fn handle_bytes(&mut self, bytes: &[u8], recv_ms: u64) -> Result<Outcome, RuntimeError>;
    pub fn handle(&mut self, msg: Inbound, recv_ms: u64) -> Result<Outcome, RuntimeError>;
    pub fn mode(&self) -> Mode;
    pub fn incidents(&self) -> usize;
    pub fn close(self) -> Result<(), RuntimeError>;
}
```

- `new` validates the policy (`Gate::new`) and the capacities:
  `sacho_capacity`, `dedup_capacity` and `recorder.seal_every` must all be
  >= 1. `post_window` may be 0 (record the incident and its backlog only).
- `initial_world` seeds the trusted world, e.g. a snapshot that arrived
  before the loop started. With `None`, non-stop proposals are denied
  `missing:world` until the first `Inbound::World`.
- `handle_bytes` never fails on bad input: a parse failure returns
  `Outcome::Rejected` and adds a `"reject"` record to the sacho (§5). `Err`
  is reserved for recorder failures (policy/config failures happen in
  `new`). Per-message isolation: one bad message never stops the loop.
- `incidents()` counts incident triggers whether or not a recorder is
  configured.
- `close()` writes the final seal, flushes and fsyncs **if** a log was
  created; otherwise it is a no-op.

## 3. Inbound JSON

One message per call; the transport does the framing (a JSONL line, a ROS
message string). Dispatch is on the top-level key, explicitly — the same
rule the W1 CLI's `Step::parse` used:

- `{"world": <WorldSnapshot>}` — `world` must be the only key. The snapshot
  is **rejected** at ingest, and the current world stays in force, if:
  - it is invalid (non-finite values, confidence outside 0..=1);
  - its `stamp_ms` is more than `future_tolerance_ms` past `recv_ms`
    (`"future world"`). Stored, it would make every later judgement
    `invalid:timestamp`;
  - its `stamp_ms` is older than the current world's (`"out-of-order world"`).
    Out-of-order delivery (best-effort QoS) must not roll perception back to
    an older, possibly less restrictive snapshot.

  Equal stamps are accepted (last write wins, as with keep-last QoS). Every
  reject records the same payload: `{"error", "input"}`, where `input` is a
  truncated lossy preview.
- `{"fault": <Fault>}` — `fault` must be the only key.
- anything else is parsed as an `ActionProposal`
  (`{"id", "source", "timestamp_ms", "action"}`).

A message carrying `world` or `fault` **plus** other keys is rejected, not
partially parsed: an untagged serde enum would pick the first variant that
fits and silently drop the rest, and a message mixing `fault` and `world`
could lose the fault.

`Fault = {"code": string, "timestamp_ms": u64, "raise_to": <Mode>}`.
`raise_to` can only move the mode **up** (`Gate::raise_mode` ignores lower
targets); a fault naming a lower mode is recorded and reported as
`ModeChanged{before == after}`.

**There is no reset variant.** Nothing on any transport may move the mode
down the ladder; an operator reset will arrive as a signed command (W3+),
never as an `Inbound`. This closes the W1 gap where `reset_mode` could have
been exposed as a plain topic.

## 4. Proposal pipeline

For `Inbound::Proposal(p)` at `recv_ms`, in order:

1. `p.action == stop` → allowed. With no world yet, the runtime returns a
   synthetic Yun itself — there is no snapshot to call `judge_at` with —
   replicating the gate's own stop path including `stop:unvetted-source`.
   Stop never touches dedup, in either direction.
2. No world seen yet → synthetic `Bul`, `fired: ["missing:world"]`. The
   gate is not called and the id is **not** claimed: the proposal may be
   legitimately retried once perception is up.
3. `(p.source, p.id)` already in the dedup set → synthetic `Bul`,
   `fired: ["replay:proposal"]`; the gate is not called. Otherwise the pair
   is inserted **before** judging, so a denied proposal still claims its id.
   Ids are only unique per source — `(vla, 7)` and `(planner, 7)` coexist.
   The set holds `dedup_capacity` keys and evicts the oldest beyond that
   (FIFO). An evicted id becomes its source's **floor**: any later id at or
   below the highest evicted id of that source is also `replay:proposal`.
   **Producer contract: ids are single-use whatever the verdict, and strictly
   increase per source.**
   Without the floor, an attacker could flush the set with fresh ids and then
   replay an old proposal with a fresh, attacker-chosen `timestamp_ms`, which
   `stale:proposal` would not catch. Ids therefore must increase per source;
   an out-of-order id older than the window is denied (fail-closed).
4. Otherwise `gate.judge_at(&p, &world, recv_ms)`.

Synthetic decisions set `action: None`, `speed_cap: None`, and `mode` from
the gate; the pre-world stop keeps `action: Some(stop)`.

## 5. Recording (dashcam)

Same semantics as the W1 CLI `Recorder`: every inbound record goes into the
sacho ring (capacity `sacho_capacity`); nothing is persisted until the first
incident.

| kind        | pushed when          | payload                                                |
| ----------- | -------------------- | ------------------------------------------------------ |
| `"world"`   | `Inbound::World`     | the `WorldSnapshot` itself                             |
| `"fault"`   | `Inbound::Fault`     | `{"fault", "mode_before", "mode_after"}`               |
| `"proposal"`| `Inbound::Proposal`  | `{"proposal", "world"}` (`world` is null until first)  |
| `"decision"`| after judging        | the `Decision` itself                                  |
| `"reject"`  | unparseable input    | `{"error", "input"}` — `input` is a lossy UTF-8 preview truncated to 4 KiB on a char boundary |

All entry `ts_ms` are `recv_ms` (§1). The W1 CLI stamped entries with the
untrusted `timestamp_ms`; that was gap M6.

An **incident** is: any `Bul` decision — including the synthetic
`missing:world` and `replay:proposal` — or a fault that raises the mode into
stop-only (`Hold` and above). On an incident the recorder lazily creates
the log (`SillokWriter::create`, never overwrites), drains the sacho backlog
oldest-first, seals, and opens the post window.

The **post window** covers the next `post_window` proposals and faults.
World updates and rejects inside an open window are still recorded but do
not consume it. The counting message that empties the window seals it. A
second incident inside an open window re-arms it to `post_window` and seals
again. Sealing at every boundary means a crash mid-incident still leaves a
signed log.

Rejects never trigger an incident — garbage must not be able to force log
creation — and once the post window is closed they wait in the sacho for the
next incident like everything else.

`sillok` §2.1's known-kind list should now read `"proposal"`, `"decision"`,
`"fault"`, `"world"`, `"reject"`, `"mode"`, `"note"`, plus the reserved
`"seal"`. (`"world"` and `"reject"` are the W2 additions.)

## 6. Built-in fired names added in W2

Runtime-level (no gate call): `missing:world`, `replay:proposal`.

Gate-level, now reachable through the runtime (`judge_at` landed on this
branch): `stale:world`, `stale:proposal`, `invalid:timestamp`.

Existing names are unchanged: `invalid:proposal`, `invalid:world`,
`source:not-allowed`, `stop:unvetted-source`, `mode:<m>`, `envelope:pose`,
`envelope:workspace`, `envelope:max_speed`, `zone:<id>`, and rule ids (which
still cannot contain `:`).

## 7. ROS 2 adapter contract (W3 — no code)

Node name: `haetae_gate`. One `Runtime` per node. All payloads are
`std_msgs/String` carrying the §3 JSON documents (`~/decision` carries a
serialised `Decision`; `~/outcome` a serialised `Outcome`).

| topic        | dir | payload           | QoS                          |
| ------------ | --- | ----------------- | ---------------------------- |
| `~/proposal` | sub | proposal JSON     | reliable, keep-last(16)      |
| `~/world`    | sub | `{"world": …}`    | best_effort, keep-last(1)    |
| `~/fault`    | sub | `{"fault": …}`    | reliable, keep-last(8)       |
| `~/decision` | pub | `Decision` JSON   | reliable, keep-last(16)      |
| `~/outcome`  | pub | `Outcome` JSON    | reliable, keep-last(16)      |

Rationale:

- `~/world` is keep-last(1): snapshots are periodic state, only the newest
  matters, and a queued stale one is worse than a dropped one — the gate's
  `world_max_age_ms` already bounds the damage of a gap. `best_effort` also
  maximises compatibility: a best-effort subscription matches both
  best-effort and reliable publishers, while a reliable subscription cannot
  match a best-effort publisher.
- proposals, faults, decisions and outcomes are reliable: a silently lost
  proposal starves the executor, a lost fault hides a mode raise, and a lost
  decision desyncs the actuator from the gate. Depths stay modest — these
  are low-rate topics — and keep-last bounds backlog under a stalled
  subscriber.
- `recv_ms = node.get_clock().now()` sampled **at callback entry** (§1: sim
  time under `use_sim_time`).
- Every `Outcome::Decision` is published on `~/decision`;
  `Outcome::ModeChanged` and `Outcome::Rejected` go to `~/outcome`.
  `WorldUpdated` stays internal — it is observable through later decisions.
  (Open for W3: publish world acks on `~/outcome` too, for diagnostics.)
- Malformed payloads produce `Outcome::Rejected` on `~/outcome`; the node
  must never let a bad message take it down.
- **No reset service or topic.** Reset moves the mode down; it arrives as a
  signed operator command (W3+), not through this node.

## 8. Notes / deviations from the agreed design

- `Outcome::ModeChanged` is returned for **every** fault, with
  `after == before` when the request did not ascend the ladder — there is no
  separate no-op outcome.
- `stop` before the first world replicates the gate's stop verdict locally —
  including `stop:unvetted-source` for unvetted sources — rather than
  judging against a placeholder snapshot, per the design note.
- `incidents()` counts triggers even when `recorder` is `None`.
- A serialisation failure on the record path surfaces as
  `RuntimeError::Sillok(sillok::Error::Json(..))` — the spec fixes the
  error enum at three variants, and serialising a `Decision`/`WorldSnapshot`
  is a recorder-path failure, not config.
- The reject preview truncates at 4 KiB on a UTF-8 boundary
  (`lossy_preview`), so the stored `input` is always valid UTF-8.

## 9. W2 review outcome and carry-over to W3

Fixed in W2 after Devin's review:

- The file adapter's clock moves only on world stamps (H2).
- Worlds are validated at ingest: invalid, far-future, and out-of-order snapshots are rejected (M1).
- Freshness budgets have a 60 s ceiling, and `future_tolerance_ms <= world_max_age_ms` (M4).
  A huge budget would saturate the clock arithmetic and turn the checks off.
- Every allowed non-stop decision carries `speed_cap` (the envelope maximum, or lower). The executor never gets "unbounded" for arm motion (M5).
- Reject payloads have one shape.

Accepted limitations, to be solved in W3:

- **Replay protection needs authentication (H1).** Proposals are unsigned.
  - An attacker who can replay can also forge a proposal with a fresh id. So dedup only stops duplicate deliveries and naive replays.
  - The eviction floor keeps a flush-then-replay from reopening old ids. The cost: a far-ahead id (e.g. `u64::MAX`) that later leaves the window denies that source until restart. This fails closed and is pinned by `a_poisoned_floor_fails_closed`.
  - W3: signed proposals carrying a per-source `(epoch, counter)`.
- **Restarts (M2, M3).**
  - A perception restart with a reset clock is rejected as out-of-order until the runtime restarts too.
  - A runtime restart forgets the dedup state and the mode (an `estop` is lost).
  - W3: an `epoch` on `WorldSnapshot`, and persisting the mode and per-source high-water marks (e.g. replayed from sillok on start).
- **`recv_ms` is not forced to be monotonic.** A backwards jump makes judgements fail closed (`invalid:timestamp`), but the log timeline steps back.
- **Seal entries still take wall-clock time** inside sillok, not `recv_ms`.
