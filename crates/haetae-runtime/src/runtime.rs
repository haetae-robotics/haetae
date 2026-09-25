//! The transport-agnostic gate loop.

use std::fmt;
use std::path::PathBuf;

use haetae_core::{
    ActionKind, ActionProposal, Decision, Gate, Mode, Policy, Verdict, WorldSnapshot,
};
use serde_json::json;
use sillok::{Keypair, Sacho};

use crate::dedup::Dedup;
use crate::inbound::Inbound;
use crate::outcome::Outcome;
use crate::recorder::Recorder;
use crate::RuntimeError;

/// Rejected input is previewed in its `"reject"` record truncated to this
/// many bytes, cut on a UTF-8 boundary.
const REJECT_PREVIEW_MAX: usize = 4 * 1024;

/// Runtime knobs. The `Default` is a 256-deep sacho, a 4096-deep dedup set
/// and no recorder.
#[derive(Debug)]
pub struct RuntimeConfig {
    /// Pre-incident ring capacity: how many recent records the sacho keeps
    /// so an incident log shows what led up to it. Must be >= 1.
    pub sacho_capacity: usize,
    /// How many `(source, id)` pairs the replay detector remembers.
    /// Must be >= 1.
    pub dedup_capacity: usize,
    /// Incident recording; `None` disables the dashcam entirely.
    pub recorder: Option<RecorderConfig>,
}

impl Default for RuntimeConfig {
    fn default() -> Self {
        RuntimeConfig {
            sacho_capacity: 256,
            dedup_capacity: 4096,
            recorder: None,
        }
    }
}

/// Dashcam configuration. The log file is created lazily on the first
/// incident — a run with no incidents leaves no log at `path`.
pub struct RecorderConfig {
    /// Path of the sillok log. Must not already exist when the first
    /// incident fires — logs are born once and never overwritten.
    pub path: PathBuf,
    /// Ed25519 keypair that signs seals. The seed stays in process memory.
    pub key: Keypair,
    /// Automatic seal cadence: entries between seals. Must be >= 1.
    pub seal_every: usize,
    /// How many proposals/faults *after* an incident are also recorded
    /// before the window is sealed. World updates and rejects are recorded
    /// inside an open window but do not consume it. `0` records the incident
    /// itself (plus the sacho backlog) and nothing after.
    pub post_window: usize,
}

impl RecorderConfig {
    /// `seal_every` 64, `post_window` 8.
    pub fn new(path: impl Into<PathBuf>, key: Keypair) -> Self {
        RecorderConfig {
            path: path.into(),
            key,
            seal_every: 64,
            post_window: 8,
        }
    }
}

// The key's seed must never appear in logs; the public key id is safe.
impl fmt::Debug for RecorderConfig {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("RecorderConfig")
            .field("path", &self.path)
            .field("key_id", &self.key.key_id())
            .field("seal_every", &self.seal_every)
            .field("post_window", &self.post_window)
            .finish()
    }
}

/// One gate loop: world in, proposals judged, faults raise the mode,
/// incidents recorded.
///
/// There is no clock inside. Every entry point takes `recv_ms`, the
/// transport's trusted receive time, which must share a clock domain with
/// [`WorldSnapshot::stamp_ms`] — the gate compares them directly. Under ROS
/// 2 that is the node clock (sim time when `use_sim_time` is set); under
/// file replay it is the stream time. See `docs/w2-contract.md` §1.
pub struct Runtime {
    gate: Gate,
    /// Latest trusted world; `None` until the first snapshot arrives.
    world: Option<WorldSnapshot>,
    sacho: Sacho,
    dedup: Dedup,
    recorder: Option<Recorder>,
    /// Incident triggers so far, whether or not a recorder is configured.
    incidents: usize,
}

impl Runtime {
    /// Build the loop around a fresh [`Gate`].
    ///
    /// `initial_world` seeds the trusted world (e.g. a snapshot that arrived
    /// before the loop started); with `None`, non-stop proposals are denied
    /// `missing:world` until the first [`Inbound::World`]. Config capacities
    /// must be >= 1 and the policy must validate.
    pub fn new(
        policy: Policy,
        initial_world: Option<WorldSnapshot>,
        cfg: RuntimeConfig,
    ) -> Result<Runtime, RuntimeError> {
        if cfg.sacho_capacity == 0 {
            return Err(RuntimeError::InvalidConfig(
                "sacho_capacity must be at least 1".into(),
            ));
        }
        if cfg.dedup_capacity == 0 {
            return Err(RuntimeError::InvalidConfig(
                "dedup_capacity must be at least 1".into(),
            ));
        }
        if let Some(r) = &cfg.recorder {
            if r.seal_every == 0 {
                return Err(RuntimeError::InvalidConfig(
                    "recorder.seal_every must be at least 1".into(),
                ));
            }
        }
        Ok(Runtime {
            gate: Gate::new(policy)?,
            world: initial_world,
            sacho: Sacho::new(cfg.sacho_capacity),
            dedup: Dedup::new(cfg.dedup_capacity),
            recorder: cfg.recorder.map(Recorder::new),
            incidents: 0,
        })
    }

    /// Handle one raw transport message.
    ///
    /// Malformed input is **not** an error: it yields [`Outcome::Rejected`]
    /// and a `"reject"` sacho record holding the parse error plus a
    /// truncated lossy preview of the input. Rejects can never create the
    /// incident log — garbage must not force one. `Err` is reserved for
    /// recorder failures.
    pub fn handle_bytes(&mut self, bytes: &[u8], recv_ms: u64) -> Result<Outcome, RuntimeError> {
        match Inbound::from_json(bytes) {
            Ok(msg) => self.handle(msg, recv_ms),
            Err(error) => {
                self.reject(recv_ms, &error, bytes);
                self.after_step(false, false)?;
                Ok(Outcome::Rejected { error })
            }
        }
    }

    /// Handle one already-parsed message.
    ///
    /// Every message lands in the sacho ring first, then the recorder is
    /// told whether the message was an incident and whether it counts
    /// toward an open post-incident window (proposals and faults count;
    /// world updates do not).
    pub fn handle(&mut self, msg: Inbound, recv_ms: u64) -> Result<Outcome, RuntimeError> {
        let (outcome, incident, counts) = match msg {
            Inbound::World(w) => match self.check_world(&w, recv_ms) {
                Err(error) => {
                    self.reject(recv_ms, &error, &serde_json::to_vec(&w)?);
                    (Outcome::Rejected { error }, false, false)
                }
                Ok(()) => {
                    let stamp_ms = w.stamp_ms;
                    self.sacho.push(recv_ms, "world", serde_json::to_value(&w)?);
                    self.world = Some(w);
                    (Outcome::WorldUpdated { stamp_ms }, false, false)
                }
            },
            Inbound::Fault(f) => {
                let before = self.gate.mode();
                self.gate.raise_mode(f.raise_to);
                let after = self.gate.mode();
                self.sacho.push(
                    recv_ms,
                    "fault",
                    json!({ "fault": &f, "mode_before": before, "mode_after": after }),
                );
                // Only a climb into stop-only is an incident.
                let incident = after > before && after.stop_only();
                (Outcome::ModeChanged { before, after }, incident, true)
            }
            Inbound::Proposal(p) => {
                self.sacho.push(
                    recv_ms,
                    "proposal",
                    json!({ "proposal": &p, "world": &self.world }),
                );
                let d = self.judge(&p, recv_ms);
                self.sacho
                    .push(recv_ms, "decision", serde_json::to_value(&d)?);
                let incident = d.verdict == Verdict::Bul;
                (Outcome::Decision(d), incident, true)
            }
        };
        self.after_step(incident, counts)?;
        Ok(outcome)
    }

    /// The gate's position on the mode ladder.
    pub fn mode(&self) -> Mode {
        self.gate.mode()
    }

    /// Incident triggers so far — every `Bul` decision and every fault that
    /// raised the mode into stop-only — whether or not a recorder is
    /// configured.
    pub fn incidents(&self) -> usize {
        self.incidents
    }

    /// Write the final seal, flush and fsync the incident log **if** one was
    /// created; otherwise a no-op.
    pub fn close(self) -> Result<(), RuntimeError> {
        if let Some(r) = self.recorder {
            r.close()?;
        }
        Ok(())
    }

    /// Judgement order for a proposal: stop is always allowed (needs no
    /// world, exempt from dedup); without a world every other action is a
    /// synthetic `missing:world` Bul that does not claim the id; a repeated
    /// `(source, id)` is a synthetic `replay:proposal` Bul; anything left
    /// goes to the gate.
    fn judge(&mut self, p: &ActionProposal, recv_ms: u64) -> Decision {
        if p.action == ActionKind::Stop {
            return match &self.world {
                Some(w) => self.gate.judge_at(p, w, recv_ms),
                None => self.stop_without_world(p),
            };
        }
        let Some(world) = &self.world else {
            return self.synthetic_bul(p, "missing:world");
        };
        // Insert before judging, so even a denied proposal claims its id.
        if self.dedup.check_and_insert((p.source, p.id)) {
            return self.synthetic_bul(p, "replay:proposal");
        }
        self.gate.judge_at(p, world, recv_ms)
    }

    /// Whether a world snapshot may replace the current one. Rejected worlds
    /// leave the current world in force, so a single bad snapshot cannot
    /// brick the loop:
    /// - invalid values (non-finite pose, confidence outside 0..=1);
    /// - a stamp further in the future than the policy's skew tolerance,
    ///   which would otherwise make every later judgement `invalid:timestamp`;
    /// - a stamp older than the current world (out-of-order delivery must not
    ///   roll perception back to an older, possibly less restrictive snapshot).
    ///
    /// Equal stamps are accepted: last write wins, as with keep-last QoS.
    fn check_world(&self, w: &WorldSnapshot, recv_ms: u64) -> Result<(), String> {
        if !w.is_valid() {
            return Err("invalid world: non-finite value or confidence outside 0..=1".into());
        }
        let tolerance = self.gate.policy().freshness.future_tolerance_ms;
        if w.stamp_ms > recv_ms.saturating_add(tolerance) {
            return Err(format!(
                "future world: stamp_ms {} is more than {tolerance} ms past recv_ms {recv_ms}",
                w.stamp_ms
            ));
        }
        if let Some(current) = self.world.as_ref().filter(|c| w.stamp_ms < c.stamp_ms) {
            return Err(format!(
                "out-of-order world: stamp_ms {} < current {}",
                w.stamp_ms, current.stamp_ms
            ));
        }
        Ok(())
    }

    /// Record a rejected input. Every reject has the same payload shape:
    /// the error plus a truncated lossy preview of the input.
    fn reject(&mut self, recv_ms: u64, error: &str, input: &[u8]) {
        self.sacho.push(
            recv_ms,
            "reject",
            json!({ "error": error, "input": lossy_preview(input) }),
        );
    }

    /// A Bul for a proposal the gate never saw.
    fn synthetic_bul(&self, p: &ActionProposal, check: &str) -> Decision {
        Decision {
            proposal_id: p.id,
            verdict: Verdict::Bul,
            fired: vec![check.to_string()],
            action: None,
            speed_cap: None,
            mode: self.gate.mode(),
        }
    }

    /// Stop is always allowed — even before the first world snapshot. This
    /// mirrors the gate's own stop path (including `stop:unvetted-source`)
    /// because there is no world to call `judge_at` with.
    fn stop_without_world(&self, p: &ActionProposal) -> Decision {
        let vetted = self.gate.policy().allowed_sources.contains(&p.source);
        Decision {
            proposal_id: p.id,
            verdict: Verdict::Yun,
            fired: if vetted {
                Vec::new()
            } else {
                vec!["stop:unvetted-source".into()]
            },
            action: Some(ActionKind::Stop),
            speed_cap: None,
            mode: self.gate.mode(),
        }
    }

    /// Incident bookkeeping plus the recorder hook, once per message.
    fn after_step(&mut self, incident: bool, counts_toward_post: bool) -> Result<(), RuntimeError> {
        if incident {
            self.incidents += 1;
        }
        if let Some(r) = self.recorder.as_mut() {
            r.after_step(incident, counts_toward_post, &mut self.sacho)?;
        }
        Ok(())
    }
}

/// Lossy preview of rejected input for the log: at most
/// [`REJECT_PREVIEW_MAX`] bytes, never splitting a UTF-8 sequence.
fn lossy_preview(bytes: &[u8]) -> String {
    let mut end = bytes.len().min(REJECT_PREVIEW_MAX);
    // Continuation bytes match 0x80..0xC0; back off so the cut lands on a
    // char boundary and the preview stays valid UTF-8.
    while end > 0 && end < bytes.len() && (bytes[end] & 0xC0) == 0x80 {
        end -= 1;
    }
    String::from_utf8_lossy(&bytes[..end]).into_owned()
}
