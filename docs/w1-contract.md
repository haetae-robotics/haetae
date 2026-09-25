# W1 contract: haetae-core × sillok

Week 1 builds the ROS-independent core. ROS 2 / Gazebo bindings come in W2 and wrap
these crates; nothing here may depend on ROS.

```
crates/
  haetae-core/   # gate: ActionProposal + WorldSnapshot + Policy -> Decision   (owner: Claude)
  sillok/        # tamper-evident recorder: sacho ring buffer + signed hash chain (owner: Devin)
  haetae/        # umbrella crate + `haetae` CLI, wires the two together           (owner: Claude)
```

`haetae-core` and `sillok` must not depend on each other. `sillok` stores opaque
`serde_json::Value` payloads; the CLI decides what goes in them.

---

## 1. haetae-core (gate)

Coordinates are 2D metres in the robot's map frame. Speeds are m/s.

- `ActionProposal { id, source, timestamp_ms, action }` comes from the **untrusted** side (VLA, planner, teleop, peers).
  - `action` is one of `move_to { goal, speed }`, `grasp { object, at }`, `place { at }`, `stop`.
  - Every non-stop action sweeps the segment **robot pose → target**. Zones, workspace and human-distance checks use that segment, so grasp and place cannot skip a zone on the way.
- `WorldSnapshot` comes from the **trusted** safety-perception path: robot pose, what the gripper holds, nearby humans, and perception confidence. The gate never reads world facts from a proposal.
- `Policy` (JSON, unknown fields rejected):
  - `allowed_sources` (**required**; forgetting it must not mean "allow everyone")
  - hard `envelope` (max speed + workspace rect)
  - `zones` (no-entry and/or speed limit)
  - semantic `rules`
  - Validation rejects rules or zones that can never have an effect: empty lists, zones with no restriction, and jeol caps not below the envelope.
  - Ids must not contain `:`, because `:` is reserved for built-in check names.
- `Gate::judge(&self, &ActionProposal, &WorldSnapshot) -> Decision`
  - `Decision { proposal_id, verdict, fired, action, speed_cap, mode }`
  - `Yun`: allow as proposed. `Bul`: deny, and `action = None`.
  - `Jeol`: allow within limits. `speed_cap` is the limit the executor must apply to **all** motion, arm included.
    - For `move_to`, the speed is also clamped in `action`.
    - For `grasp` and `place`, `action` is unchanged and `speed_cap` alone carries the limit.
  - Every applicable check runs. The strictest verdict wins (tighten-only), and several caps combine as their minimum.
  - `stop` is always `Yun`. If the source is not allowed, the decision records `stop:unvetted-source`.
- Built-in checks: `invalid:proposal`, `invalid:world`, `source:not-allowed`, `mode:<m>`, `envelope:pose`, `envelope:workspace`, `envelope:max_speed`, `zone:<id>`.
- The mode ladder is `Normal < Caution < Hold < SafePark < EStop`.
  - `raise_mode` only goes up; `reset_mode` is an explicit operator call.
  - `Caution` caps speed at half the envelope maximum.
  - `Hold` and above deny everything except `stop`.

### 1.1 Known gaps, planned for W2 (from Devin's W1 review)

- **Freshness (M5)**: `WorldSnapshot` has no stamp and `timestamp_ms` is unused, so a replayed proposal or stale perception cannot be detected. W2 adds `stamp_ms` and a staleness budget to `judge`.
- **Trusted time (M6)**: the CLI writes the proposal's own `timestamp_ms` as the sillok `ts_ms`, which lets an attacker choose the audit timeline. W2 records receive time in `ts_ms` and keeps the claimed time in the payload.
- **Per-message isolation**: the W1 CLI aborts the whole run on one malformed line. The ROS 2 node must reject that message and keep running.
- **`reset_mode` is unauthenticated**: the W2 wrapper must not expose it as a plain topic or service. Signed resets come in W3.
- **Escape from a no-entry zone**: a robot already inside one can only `stop`. This is intentionally fail-closed for now; a slow exit manoeuvre is a W3 design question.
- **One bad human position fails the whole world**: `invalid:world` is intentionally fail-closed, at a cost to availability.

---

## 2. sillok (recorder)

### 2.1 Entry format (JSON Lines, one entry per line)

```json
{"seq":0,"ts_ms":1727241787212,"kind":"decision","payload":{...},"prev":"<64 hex>","hash":"<64 hex>"}
```

- `seq` starts at 0 and increments by 1 with no gaps.
- `prev` of entry 0 is 64 zeros. Otherwise it is the `hash` of the previous entry.
- `hash = SHA-256( canonical_json({"seq","ts_ms","kind","payload","prev"}) )`, lowercase hex.
  - Canonical JSON uses `serde_json::Value` with default (BTreeMap, sorted-key) maps, serialised with `serde_json::to_vec` (no whitespace). Do **not** enable serde_json's `preserve_order` feature.
- `kind` is a free string. Known values: `"proposal"`, `"decision"`, `"fault"`, `"mode"`, `"note"`, and the reserved `"seal"`.

### 2.2 Seals (signatures)

- A **seal** is an ordinary entry with `kind: "seal"` and payload `{"sig":"<128 hex>","key_id":"<16 hex>"}`.
- `sig` = Ed25519 signature over the domain-separated message

  `b"haetae/sillok/seal/v1" || 0x00 || seq.to_be_bytes() || ts_ms.to_be_bytes() || prev_raw`

  where `seq` and `ts_ms` are the seal entry's **own** fields and `prev_raw` is the raw
  32 bytes of the **previous** entry's hash. The tag keeps the signature from being
  replayed into any other protocol that shares the key, and covering `seq`/`ts_ms`
  binds the seal's own fields — the last seal in a log is covered by no later entry
  or signature, so without this it could be re-dated and re-hashed undetectably.
- `key_id` = the first 8 bytes (16 hex) of SHA-256(verifying key bytes).
- The writer seals automatically every `seal_every` entries (default 64), on `seal()`,
  and on `close()` — except that `close()` skips the final seal when nothing was
  appended since the last seal. A completely empty log is still sealed, so every
  closed log ends in a signature.
- Entries after the last seal form the **unsealed tail**. The verifier reports them,
  but they are not an error. `VerifyReport::fully_sealed()` is
  `seals > 0 && unsealed_tail == 0` — a log with zero seals verifies on hashes alone
  and proves nothing about who wrote it.

### 2.3 API (as implemented; lib name `sillok`)

```rust
pub struct Keypair;            // wraps ed25519 SigningKey
impl Keypair {
    pub fn generate() -> Result<Self>;                 // 32-byte seed from getrandom
    pub fn from_seed(seed: [u8; 32]) -> Self;
    pub fn from_seed_hex(s: &str) -> Result<Self>;
    pub fn seed_hex(&self) -> String;
    pub fn verifying_key_hex(&self) -> String;
    pub fn key_id(&self) -> String;                    // the 16-hex id written into seals
}

pub struct Entry;              // one log line; pub fields seq/ts_ms/kind/payload/prev/hash
impl Entry {
    pub fn new(seq: u64, ts_ms: u64, kind: &str, payload: Value, prev: &str) -> Self; // computes hash
    pub fn computed_hash(&self) -> String;
    pub fn is_seal(&self) -> bool;
}
pub const SEAL_KIND: &str = "seal";
pub const ZERO_HASH: &str = "0000…"; // 64 zeros, prev of seq 0
pub fn hash_body(seq, ts_ms, kind, payload, prev) -> String;  // the §2.1 hash

pub struct SillokWriter;       // append-only JSONL file
impl SillokWriter {
    pub fn create(path, Keypair, seal_every: usize) -> Result<Self>;   // fails if file exists; seal_every==0 errors
    pub fn append(&mut self, ts_ms: u64, kind: &str, payload: Value) -> Result<Entry>; // rejects kind "seal"
    pub fn seal(&mut self) -> Result<()>;
    pub fn close(self) -> Result<()>;   // final seal unless the last entry already is one; then fsyncs
    pub fn next_seq(&self) -> u64;      // total lines written so far
    pub fn last_hash(&self) -> String;  // chain tip hex (ZERO_HASH when empty)
    pub fn unsealed(&self) -> usize;    // entries written since the last seal
}

pub struct Sacho;              // in-memory ring buffer (the pre-trigger window)
impl Sacho {
    pub fn new(capacity: usize) -> Self;            // capacity 0 drops everything
    pub fn push(&mut self, ts_ms: u64, kind: &str, payload: Value);   // drops oldest when full
    pub fn flush_into(&mut self, w: &mut SillokWriter) -> Result<usize>; // drains in order, returns count
    pub fn len(&self) -> usize;
    pub fn is_empty(&self) -> bool;
    pub fn capacity(&self) -> usize;
}

pub fn verify(path, verifying_key_hex: &str) -> Result<VerifyReport, VerifyError>;
pub struct VerifyReport { pub entries: u64, pub seals: u64, pub unsealed_tail: u64, pub last_hash: String }
impl VerifyReport { pub fn fully_sealed(&self) -> bool; }  // seals > 0 && unsealed_tail == 0
```

- `Error` is the single fallible-op enum (`Io`, `AlreadyExists`, `Random`, `BadSeed`, `InvalidSealInterval`, `ReservedKind`, `Json`); `sillok::Result<T>` aliases it.
- `VerifyError` variants: `Io`, `BadKey`, `Malformed{seq,detail}`, `SeqGap{expected,found}`, `PrevMismatch{seq,expected,found}`, `HashMismatch{seq,expected,found}`, `BadSeal{seq,detail}`.
- Entry lines are parsed strictly: unknown top-level fields are rejected, since the hash does not cover them.
- An empty-but-existing file verifies: `{entries:0, seals:0, unsealed_tail:0, last_hash:ZERO_HASH}`.

### 2.4 `verify` must detect (each one needs a test)

1. A modified payload in any entry (hash mismatch)
2. A deleted entry in the middle (seq gap or prev mismatch)
3. Reordered entries
4. A seal signed with a different key (bad signature or key_id mismatch)
5. A forged seal: an entry `kind:"seal"` whose signature does not verify
6. Truncation *after* a seal is reported via `unsealed_tail`. Truncation that removes a seal is undetectable locally; this is what external anchoring (W4+) is for. Document this, don't test it.
7. A malformed line (not JSON, missing fields)
8. A re-dated last seal: editing a seal's own `ts_ms` and recomputing its `hash` still fails, because the signature covers `seq`/`ts_ms` (§2.2). A seal signed over the bare prev hash (no domain tag) also fails.

`VerifyError` must say **which seq** failed and **why**.

### 2.5 Out of scope for W1

TPM/secure element keys, split-key encryption, external anchoring (RFC 3161 / Sigsum), privacy masking. Leave `// TODO(W4)` markers where they will plug in.
