//! Trusted semantic observations must not roll back or silently mutate facts.
mod common;
use common::{policy, world};
use haetae_core::{SemanticSnapshot, WorldSnapshot};
use haetae_runtime::{Inbound, Outcome, Runtime, RuntimeConfig};
use serde_json::json;

fn semantic(t: u64, revision: u64, task_revision: u64) -> SemanticSnapshot {
    serde_json::from_value(json!({
        "schema_version":1,"revision":revision,"task_revision":task_revision,
        "task_id":"task-1","step_id":"step-1","robot_id":"robot",
        "model_sha256":"a".repeat(64),"tool_id":"tool","item_id":"item",
        "observed_ms":t,"confidence":1,"coverage_known":true,"item":"inert",
        "regions":[],"base_pose":{"x":1,"y":1},"base_yaw":0
    }))
    .unwrap()
}
fn snapshot(t: u64, revision: u64, task_revision: u64) -> WorldSnapshot {
    let mut w = world();
    w.stamp_ms = t;
    w.semantic = Some(semantic(t, revision, task_revision));
    w
}
fn accepted(rt: &mut Runtime, w: WorldSnapshot, t: u64) {
    assert!(matches!(
        rt.handle(Inbound::World(w), t).unwrap(),
        Outcome::WorldUpdated { .. }
    ));
}
fn rejected(rt: &mut Runtime, w: WorldSnapshot, t: u64) {
    assert!(matches!(
        rt.handle(Inbound::World(w), t).unwrap(),
        Outcome::Rejected { .. }
    ));
}

#[test]
fn observation_refresh_does_not_authorize_changed_facts_at_same_revision() {
    let mut rt = Runtime::new(policy(), None, RuntimeConfig::default()).unwrap();
    accepted(&mut rt, snapshot(10000, 3, 2), 10000);
    let mut refreshed = snapshot(10010, 3, 2);
    refreshed.semantic.as_mut().unwrap().confidence = 0.95;
    accepted(&mut rt, refreshed, 10010);
    for change in 0..6 {
        let mut altered = snapshot(10020, 3, 2);
        let scene = altered.semantic.as_mut().unwrap();
        match change {
            0 => scene.item = haetae_core::hazard::ItemKind::Pressurized,
            1 => scene.tool_id = "other".into(),
            2 => scene.coverage_known = false,
            3 => scene.base_pose.x += 0.001,
            4 => scene.item_id = "other".into(),
            _ => scene.task_revision += 1,
        }
        rejected(&mut rt, altered, 10020);
        assert_eq!(rt.world().unwrap().stamp_ms, 10010);
    }
}

#[test]
fn missing_observations_do_not_erase_semantic_revision_floor() {
    let mut rt = Runtime::new(policy(), None, RuntimeConfig::default()).unwrap();
    accepted(&mut rt, snapshot(10000, 3, 2), 10000);
    let mut missing = world();
    missing.stamp_ms = 10010;
    accepted(&mut rt, missing, 10010);
    for (revision, task_revision) in [(2, 2), (3, 1)] {
        rejected(&mut rt, snapshot(10020, revision, task_revision), 10020);
        assert!(rt.world().unwrap().semantic.is_none());
    }
    let mut old_observation = snapshot(10020, 4, 2);
    old_observation.semantic.as_mut().unwrap().observed_ms = 9999;
    rejected(&mut rt, old_observation, 10020);
    accepted(&mut rt, snapshot(10020, 4, 2), 10020);
}

#[test]
fn task_context_change_requires_advancing_task_and_scene_revisions() {
    let initial = snapshot(10000, 3, 2);
    let mut rt = Runtime::new(policy(), Some(initial), RuntimeConfig::default()).unwrap();
    let mut changed = snapshot(10000, 4, 2);
    changed.semantic.as_mut().unwrap().step_id = "step-2".into();
    rejected(&mut rt, changed.clone(), 10000);
    changed.semantic.as_mut().unwrap().task_revision = 3;
    accepted(&mut rt, changed, 10000);
    // Keep-last QoS with the same world stamp cannot roll either revision back.
    rejected(&mut rt, snapshot(10000, 3, 2), 10000);
}
