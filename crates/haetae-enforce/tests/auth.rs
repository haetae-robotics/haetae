use std::collections::BTreeMap;
use std::fs;

use ed25519_dalek::SigningKey;
use haetae_enforce::auth::{sign_bundle, sign_input, AuthVerifier, Role, SignedInput, TrustBody};
use haetae_runtime::Inbound;
use sha2::{Digest, Sha256};

fn setup() -> (tempfile::TempDir, String, String, String) {
    let dir = tempfile::tempdir().unwrap();
    let root_seed = hex::encode([1u8; 32]);
    let input_seed = hex::encode([2u8; 32]);
    let root_pubkey = hex::encode(
        SigningKey::from_bytes(&[1u8; 32])
            .verifying_key()
            .to_bytes(),
    );
    let input_pubkey = hex::encode(
        SigningKey::from_bytes(&[2u8; 32])
            .verifying_key()
            .to_bytes(),
    );
    let world_pubkey = hex::encode(
        SigningKey::from_bytes(&[3u8; 32])
            .verifying_key()
            .to_bytes(),
    );
    let fault_pubkey = hex::encode(
        SigningKey::from_bytes(&[4u8; 32])
            .verifying_key()
            .to_bytes(),
    );
    let policy = b"policy bytes";
    let body = TrustBody {
        v: 1,
        audience: "robot-7".into(),
        epoch: 42,
        policy_sha256: hex::encode(Sha256::digest(policy)),
        keys: BTreeMap::from([
            (Role::World, world_pubkey),
            (Role::Fault, fault_pubkey),
            (Role::Vla, input_pubkey),
        ]),
    };
    let bundle = sign_bundle(&serde_json::to_string(&body).unwrap(), &root_seed).unwrap();
    fs::write(
        dir.path().join("trust.json"),
        serde_json::to_vec(&bundle).unwrap(),
    )
    .unwrap();
    (dir, root_pubkey, input_seed, root_seed)
}

fn signed(seed: &str, counter: u64, role: Role, payload: &str) -> SignedInput {
    sign_input(
        SignedInput {
            v: 1,
            audience: "robot-7".into(),
            epoch: 42,
            counter,
            role,
            payload: payload.into(),
            signature: String::new(),
        },
        seed,
    )
    .unwrap()
}

#[test]
fn signature_role_replay_and_epoch_are_bound() {
    let (dir, root, seed, _) = setup();
    let mut v = AuthVerifier::load(&dir.path().join("trust.json"), &root, b"policy bytes").unwrap();
    let payload = r#"{"id":9,"source":"vla","timestamp_ms":1000,"action":{"type":"stop"}}"#;
    let good = signed(&seed, 1, Role::Vla, payload);
    let raw = serde_json::to_vec(&good).unwrap();
    assert!(matches!(v.verify(&raw), Ok(Inbound::Proposal(_))));
    assert!(v.verify(&raw).unwrap_err().contains("replayed"));
    let mut forged = signed(&seed, 2, Role::Vla, payload);
    forged.payload.push(' ');
    assert!(v
        .verify(&serde_json::to_vec(&forged).unwrap())
        .unwrap_err()
        .contains("signature"));
    let wrong_role = signed(&seed, 2, Role::Planner, payload);
    assert!(v.verify(&serde_json::to_vec(&wrong_role).unwrap()).is_err());
    let wrong_epoch = sign_input(
        SignedInput {
            epoch: 41,
            ..signed(&seed, 2, Role::Vla, payload)
        },
        &seed,
    )
    .unwrap();
    assert!(v
        .verify(&serde_json::to_vec(&wrong_epoch).unwrap())
        .is_err());
}

#[test]
fn bundle_and_policy_tampering_fail_before_processing() {
    let (dir, root, _, _) = setup();
    let path = dir.path().join("trust.json");
    assert!(AuthVerifier::load(&path, &root, b"changed policy").is_err());
    let mut bundle: serde_json::Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    bundle["body"] = serde_json::Value::String(
        bundle["body"]
            .as_str()
            .unwrap()
            .replace("robot-7", "robot-8"),
    );
    fs::write(&path, serde_json::to_vec(&bundle).unwrap()).unwrap();
    assert!(AuthVerifier::load(&path, &root, b"policy bytes").is_err());
}

#[test]
fn restored_counter_rejects_old_input_and_epoch_rollback() {
    let (dir, root, seed, _) = setup();
    let mut v = AuthVerifier::load(&dir.path().join("trust.json"), &root, b"policy bytes").unwrap();
    v.restore(42, BTreeMap::from([(Role::Vla, 11)])).unwrap();
    let payload = r#"{"id":9,"source":"vla","timestamp_ms":1000,"action":{"type":"stop"}}"#;
    assert!(v
        .verify(&serde_json::to_vec(&signed(&seed, 11, Role::Vla, payload)).unwrap())
        .is_err());
    assert!(v.restore(43, BTreeMap::new()).is_err());
}

#[test]
fn root_signed_key_rotation_accepts_new_epoch_and_rejects_old_key() {
    let (dir, root, old_seed, root_seed) = setup();
    let path = dir.path().join("trust.json");
    let old_bundle: haetae_enforce::auth::SignedBundle =
        serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    let mut body: TrustBody = serde_json::from_str(&old_bundle.body).unwrap();
    body.epoch = 43;
    let new_seed = hex::encode([5u8; 32]);
    body.keys.insert(
        Role::Vla,
        hex::encode(
            SigningKey::from_bytes(&[5u8; 32])
                .verifying_key()
                .to_bytes(),
        ),
    );
    let rotated = sign_bundle(&serde_json::to_string(&body).unwrap(), &root_seed).unwrap();
    fs::write(&path, serde_json::to_vec(&rotated).unwrap()).unwrap();
    let mut verifier = AuthVerifier::load(&path, &root, b"policy bytes").unwrap();
    verifier
        .restore(42, BTreeMap::from([(Role::Vla, 11)]))
        .unwrap();
    let payload = r#"{"id":9,"source":"vla","timestamp_ms":1000,"action":{"type":"stop"}}"#;
    let mut old = signed(&old_seed, 12, Role::Vla, payload);
    old.epoch = 43;
    let old = sign_input(old, &old_seed).unwrap();
    assert!(verifier.verify(&serde_json::to_vec(&old).unwrap()).is_err());
    let mut next = signed(&new_seed, 1, Role::Vla, payload);
    next.epoch = 43;
    let next = sign_input(next, &new_seed).unwrap();
    assert!(matches!(
        verifier.verify(&serde_json::to_vec(&next).unwrap()),
        Ok(Inbound::Proposal(_))
    ));
    fs::write(&path, serde_json::to_vec(&old_bundle).unwrap()).unwrap();
    let mut rolled_back = AuthVerifier::load(&path, &root, b"policy bytes").unwrap();
    assert!(rolled_back.restore(43, BTreeMap::new()).is_err());
}
