//! The gate loop as a spec: message parsing, missing world, replay
//! detection, freshness passthrough, faults and config validation.

mod common;

use haetae_core::{ActionKind, Mode, Policy, Source, Verdict};
use haetae_runtime::{Fault, Inbound, Outcome, Runtime, RuntimeConfig, RuntimeError};
use serde_json::json;

use common::{decide, policy, proposal, proposal_bytes, stop, world, NOW};

fn runtime() -> Runtime {
    Runtime::new(policy(), Some(world()), RuntimeConfig::default()).unwrap()
}

// --- inbound parsing ---------------------------------------------------------

#[test]
fn world_and_fault_must_be_the_only_key() {
    let mut rt = runtime();

    // a `world` key mixed with other keys is rejected, not partially parsed
    let mixed = json!({ "world": world(), "id": 9 }).to_string();
    assert!(matches!(
        rt.handle_bytes(mixed.as_bytes(), NOW).unwrap(),
        Outcome::Rejected { .. }
    ));

    // same for `fault`
    let mixed = json!({
        "fault": {"code": "x", "timestamp_ms": NOW, "raise_to": "hold"},
        "id": 9
    })
    .to_string();
    assert!(matches!(
        rt.handle_bytes(mixed.as_bytes(), NOW).unwrap(),
        Outcome::Rejected { .. }
    ));

    // the clean forms work
    let w = json!({ "world": world() }).to_string();
    assert_eq!(
        rt.handle_bytes(w.as_bytes(), NOW).unwrap(),
        Outcome::WorldUpdated { stamp_ms: NOW }
    );
    let f = json!({
        "fault": {"code": "x", "timestamp_ms": NOW, "raise_to": "caution"}
    })
    .to_string();
    assert_eq!(
        rt.handle_bytes(f.as_bytes(), NOW).unwrap(),
        Outcome::ModeChanged {
            before: Mode::Normal,
            after: Mode::Caution
        }
    );
}

#[test]
fn malformed_input_is_rejected_and_the_loop_continues() {
    let mut rt = runtime();

    for bad in [
        b"not json".as_slice(),
        b"".as_slice(),
        b"[1,2,3]".as_slice(), // valid JSON, not an object
        b"{\"world\":}".as_slice(),
    ] {
        assert!(
            matches!(rt.handle_bytes(bad, NOW).unwrap(), Outcome::Rejected { .. }),
            "{bad:?} should be rejected"
        );
    }

    // garbage is not fatal: the next real message is handled
    let d = decide(
        rt.handle_bytes(&proposal_bytes(1, "vla", 3.0, 1.0, 0.5), NOW)
            .unwrap(),
    );
    assert_eq!(d.verdict, Verdict::Yun);
}

// --- missing world -----------------------------------------------------------

#[test]
fn proposals_before_any_world_are_missing_world() {
    let mut rt = Runtime::new(policy(), None, RuntimeConfig::default()).unwrap();

    let d = decide(
        rt.handle(proposal(1, Source::Vla, 3.0, 1.0, 0.5), NOW)
            .unwrap(),
    );
    assert_eq!(d.verdict, Verdict::Bul);
    assert_eq!(d.fired, ["missing:world"]);
    assert_eq!(d.action, None);

    // the denial does not claim the id: once a world exists the same
    // proposal is judged fresh, not flagged as a replay
    rt.handle(Inbound::World(world()), NOW).unwrap();
    let d = decide(
        rt.handle(proposal(1, Source::Vla, 3.0, 1.0, 0.5), NOW)
            .unwrap(),
    );
    assert_eq!(d.verdict, Verdict::Yun);
}

#[test]
fn stop_needs_no_world() {
    let mut rt = Runtime::new(policy(), None, RuntimeConfig::default()).unwrap();

    let d = decide(rt.handle(stop(1, Source::Vla), NOW).unwrap());
    assert_eq!(d.verdict, Verdict::Yun);
    assert_eq!(d.action, Some(ActionKind::Stop));

    // an unvetted source can still stop, and it is noted like the gate does
    let d = decide(rt.handle(stop(2, Source::Peer), NOW).unwrap());
    assert_eq!(d.verdict, Verdict::Yun);
    assert_eq!(d.fired, ["stop:unvetted-source"]);
}

// --- replay detection --------------------------------------------------------

#[test]
fn repeated_source_and_id_is_replay() {
    let mut rt = runtime();

    let d = decide(
        rt.handle(proposal(1, Source::Vla, 3.0, 1.0, 0.5), NOW)
            .unwrap(),
    );
    assert_eq!(d.verdict, Verdict::Yun);

    let d = decide(
        rt.handle(proposal(1, Source::Vla, 3.0, 1.0, 0.5), NOW)
            .unwrap(),
    );
    assert_eq!(d.verdict, Verdict::Bul);
    assert_eq!(d.fired, ["replay:proposal"]);

    // ids are only unique per source: (planner, 1) is a different proposal
    let d = decide(
        rt.handle(proposal(1, Source::Planner, 3.0, 1.0, 0.5), NOW)
            .unwrap(),
    );
    assert_eq!(d.verdict, Verdict::Yun);
}

#[test]
fn denied_proposals_still_claim_their_id() {
    let mut rt = runtime();

    // goal outside the workspace: the gate denies it
    let d = decide(
        rt.handle(proposal(7, Source::Vla, 20.0, 1.0, 0.5), NOW)
            .unwrap(),
    );
    assert_eq!(d.verdict, Verdict::Bul);
    assert_eq!(d.fired, ["envelope:workspace"]);

    // retrying the same id is a replay, not a fresh denial
    let d = decide(
        rt.handle(proposal(7, Source::Vla, 20.0, 1.0, 0.5), NOW)
            .unwrap(),
    );
    assert_eq!(d.fired, ["replay:proposal"]);
}

#[test]
fn stop_is_exempt_from_dedup() {
    let mut rt = runtime();

    for _ in 0..3 {
        assert_eq!(
            decide(rt.handle(stop(5, Source::Vla), NOW).unwrap()).verdict,
            Verdict::Yun
        );
    }
    // a stop never claims its id either
    let d = decide(
        rt.handle(proposal(5, Source::Vla, 3.0, 1.0, 0.5), NOW)
            .unwrap(),
    );
    assert_eq!(d.verdict, Verdict::Yun);
}

#[test]
fn evicted_ids_stay_replays() {
    let cfg = RuntimeConfig {
        dedup_capacity: 4,
        ..RuntimeConfig::default()
    };
    let mut rt = Runtime::new(policy(), Some(world()), cfg).unwrap();

    for id in 1..=5u64 {
        let d = decide(
            rt.handle(proposal(id, Source::Vla, 3.0, 1.0, 0.5), NOW)
                .unwrap(),
        );
        assert_eq!(d.verdict, Verdict::Yun, "id {id}");
    }
    // The set holds {2,3,4,5}: id 2 is a replay...
    let d = decide(
        rt.handle(proposal(2, Source::Vla, 3.0, 1.0, 0.5), NOW)
            .unwrap(),
    );
    assert_eq!(d.fired, ["replay:proposal"]);
    // ...and so is the evicted id 1, because it is below vla's floor.
    // (Review R1: flushing the set with fresh ids must not reopen old ones.)
    let d = decide(
        rt.handle(proposal(1, Source::Vla, 3.0, 1.0, 0.5), NOW)
            .unwrap(),
    );
    assert_eq!(d.fired, ["replay:proposal"]);
    // A new, higher id is fine.
    let d = decide(
        rt.handle(proposal(6, Source::Vla, 3.0, 1.0, 0.5), NOW)
            .unwrap(),
    );
    assert_eq!(d.verdict, Verdict::Yun);
}

/// W2 review: a far-future or invalid snapshot is rejected at ingest instead
/// of being stored and bricking every later judgement.
#[test]
fn future_or_invalid_world_is_rejected_at_ingest() {
    let mut rt = runtime();
    let mut future = world();
    future.stamp_ms = u64::MAX;
    match rt.handle(Inbound::World(future), NOW).unwrap() {
        Outcome::Rejected { error } => assert!(error.contains("future world"), "{error}"),
        other => panic!("expected Rejected, got {other:?}"),
    }
    let mut bad = world();
    bad.confidence = 7.0;
    assert!(matches!(
        rt.handle(Inbound::World(bad), NOW).unwrap(),
        Outcome::Rejected { .. }
    ));

    // The loop is not bricked: the current world is still used.
    let d = decide(
        rt.handle(proposal(1, Source::Vla, 3.0, 1.0, 0.5), NOW)
            .unwrap(),
    );
    assert_eq!(d.verdict, Verdict::Yun);
}

/// W2 review H1, pinned: ids are single-use and must increase per source.
/// A far-ahead id that later falls out of the window becomes the source's
/// floor and denies that source's later proposals. This fails closed (the
/// robot stops, and every denial is an incident) and is accepted until W3
/// adds signed proposals with per-source (epoch, counter).
#[test]
fn a_poisoned_floor_fails_closed() {
    let cfg = RuntimeConfig {
        dedup_capacity: 2,
        ..RuntimeConfig::default()
    };
    let mut rt = Runtime::new(policy(), Some(world()), cfg).unwrap();
    for id in [u64::MAX, 1, 2] {
        rt.handle(proposal(id, Source::Vla, 3.0, 1.0, 0.5), NOW)
            .unwrap();
    }
    let d = decide(
        rt.handle(proposal(3, Source::Vla, 3.0, 1.0, 0.5), NOW)
            .unwrap(),
    );
    assert_eq!(d.fired, ["replay:proposal"]);
    // Stop still works, and other sources are unaffected.
    assert_eq!(
        decide(rt.handle(stop(4, Source::Vla), NOW).unwrap()).verdict,
        Verdict::Yun
    );
    let d = decide(
        rt.handle(proposal(3, Source::Planner, 3.0, 1.0, 0.5), NOW)
            .unwrap(),
    );
    assert_eq!(d.verdict, Verdict::Yun);
}

/// Review R2: an older snapshot arriving late must not replace a newer one.
#[test]
fn out_of_order_world_is_rejected() {
    let mut rt = runtime();
    let mut older = world();
    older.stamp_ms = NOW - 10;
    match rt.handle(Inbound::World(older), NOW).unwrap() {
        Outcome::Rejected { error } => assert!(error.contains("out-of-order"), "{error}"),
        other => panic!("expected Rejected, got {other:?}"),
    }
    // The newer world is still in force.
    let d = decide(
        rt.handle(proposal(1, Source::Vla, 3.0, 1.0, 0.5), NOW)
            .unwrap(),
    );
    assert_eq!(d.verdict, Verdict::Yun);

    let mut newer = world();
    newer.stamp_ms = NOW + 5;
    assert!(matches!(
        rt.handle(Inbound::World(newer), NOW + 5).unwrap(),
        Outcome::WorldUpdated { stamp_ms } if stamp_ms == NOW + 5
    ));
}

// --- freshness passthrough ---------------------------------------------------

#[test]
fn recv_time_drives_the_freshness_checks() {
    let mut rt = runtime(); // world stamped at NOW

    // No new perception for 501 ms of receive time: world_max_age_ms is 500.
    let later = NOW + 501;
    let d = decide(
        rt.handle(proposal(1, Source::Vla, 3.0, 1.0, 0.5), later)
            .unwrap(),
    );
    assert_eq!(d.verdict, Verdict::Bul);
    assert_eq!(d.fired, ["stale:world"]);

    // A fresh snapshot recovers the loop.
    let mut fresh = world();
    fresh.stamp_ms = later;
    rt.handle(Inbound::World(fresh), later).unwrap();
    let d = decide(
        rt.handle(proposal(2, Source::Vla, 3.0, 1.0, 0.5), later)
            .unwrap(),
    );
    assert_eq!(d.verdict, Verdict::Yun);
}

// --- faults ------------------------------------------------------------------

#[test]
fn fault_raises_the_mode_and_hold_denies_motion() {
    let mut rt = runtime();

    let out = rt
        .handle(
            Inbound::Fault(Fault {
                code: "maek:thermal".into(),
                timestamp_ms: NOW,
                raise_to: Mode::Caution,
            }),
            NOW,
        )
        .unwrap();
    assert_eq!(
        out,
        Outcome::ModeChanged {
            before: Mode::Normal,
            after: Mode::Caution
        }
    );
    assert_eq!(rt.mode(), Mode::Caution);
    assert_eq!(rt.incidents(), 0); // caution is not stop-only

    let out = rt
        .handle(
            Inbound::Fault(Fault {
                code: "maek:vibration".into(),
                timestamp_ms: NOW,
                raise_to: Mode::Hold,
            }),
            NOW,
        )
        .unwrap();
    assert_eq!(
        out,
        Outcome::ModeChanged {
            before: Mode::Caution,
            after: Mode::Hold
        }
    );
    assert_eq!(rt.incidents(), 1); // climbing into stop-only is an incident

    let d = decide(
        rt.handle(proposal(1, Source::Vla, 3.0, 1.0, 0.5), NOW)
            .unwrap(),
    );
    assert_eq!(d.verdict, Verdict::Bul);
    assert_eq!(d.fired, ["mode:hold"]);
    assert_eq!(rt.incidents(), 2); // a bul decision is an incident too

    // stop is still allowed at Hold
    assert_eq!(
        decide(rt.handle(stop(2, Source::Vla), NOW).unwrap()).verdict,
        Verdict::Yun
    );

    // a fault below the current mode changes nothing
    let out = rt
        .handle(
            Inbound::Fault(Fault {
                code: "maek:late".into(),
                timestamp_ms: NOW,
                raise_to: Mode::Caution,
            }),
            NOW,
        )
        .unwrap();
    assert_eq!(
        out,
        Outcome::ModeChanged {
            before: Mode::Hold,
            after: Mode::Hold
        }
    );
}

// --- config validation -------------------------------------------------------

#[test]
fn zero_capacities_are_rejected() {
    let cfg = RuntimeConfig {
        sacho_capacity: 0,
        ..RuntimeConfig::default()
    };
    assert!(matches!(
        Runtime::new(policy(), Some(world()), cfg),
        Err(RuntimeError::InvalidConfig(_))
    ));

    let cfg = RuntimeConfig {
        dedup_capacity: 0,
        ..RuntimeConfig::default()
    };
    assert!(matches!(
        Runtime::new(policy(), Some(world()), cfg),
        Err(RuntimeError::InvalidConfig(_))
    ));
}

#[test]
fn invalid_policy_is_a_runtime_error() {
    // parses, but fails validation (negative envelope speed)
    let p: Policy = serde_json::from_str(
        r#"{
            "envelope": {"max_speed": -1.0, "workspace": {"min": {"x":0,"y":0}, "max": {"x":10,"y":10}}},
            "allowed_sources": ["vla"]
        }"#,
    )
    .unwrap();
    assert!(matches!(
        Runtime::new(p, None, RuntimeConfig::default()),
        Err(RuntimeError::Policy(_))
    ));
}

#[test]
fn close_without_a_recorder_is_a_no_op() {
    runtime().close().unwrap();
}
