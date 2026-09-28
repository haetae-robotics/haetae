//! Detached signatures bind each command to a configured role and epoch.
//! The signed payload is the exact UTF-8 JSON string in the envelope.

use std::collections::BTreeMap;
use std::fs;
use std::path::Path;

use ed25519_dalek::{Signature, Signer, SigningKey, Verifier as _, VerifyingKey};
use haetae_core::{ActionProposal, Policy, Source, WorldSnapshot};
use haetae_runtime::{Fault, Inbound};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Role {
    World,
    Fault,
    Vla,
    Planner,
    Teleop,
    Peer,
}

impl Role {
    fn tag(self) -> u8 {
        match self {
            Self::World => 1,
            Self::Fault => 2,
            Self::Vla => 3,
            Self::Planner => 4,
            Self::Teleop => 5,
            Self::Peer => 6,
        }
    }
    fn source(self) -> Option<Source> {
        match self {
            Self::World | Self::Fault => None,
            Self::Vla => Some(Source::Vla),
            Self::Planner => Some(Source::Planner),
            Self::Teleop => Some(Source::Teleop),
            Self::Peer => Some(Source::Peer),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TrustBody {
    pub v: u8,
    pub audience: String,
    pub epoch: u64,
    pub policy_sha256: String,
    pub keys: BTreeMap<Role, String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SignedBundle {
    pub body: String,
    pub signature: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SignedInput {
    pub v: u8,
    pub audience: String,
    pub epoch: u64,
    pub counter: u64,
    pub role: Role,
    pub payload: String,
    pub signature: String,
}

pub struct AuthVerifier {
    audience: String,
    epoch: u64,
    keys: BTreeMap<Role, VerifyingKey>,
    counters: BTreeMap<Role, u64>,
}

fn key(hex_key: &str) -> Result<VerifyingKey, String> {
    let raw = hex::decode(hex_key).map_err(|_| "public key is not hex")?;
    let bytes: [u8; 32] = raw.try_into().map_err(|_| "public key must be 32 bytes")?;
    VerifyingKey::from_bytes(&bytes).map_err(|_| "invalid public key".into())
}

fn signature(hex_sig: &str) -> Result<Signature, String> {
    let raw = hex::decode(hex_sig).map_err(|_| "signature is not hex")?;
    let bytes: [u8; 64] = raw.try_into().map_err(|_| "signature must be 64 bytes")?;
    Ok(Signature::from_bytes(&bytes))
}

fn seed(hex_seed: &str) -> Result<SigningKey, String> {
    let raw = hex::decode(hex_seed).map_err(|_| "seed is not hex")?;
    let bytes: [u8; 32] = raw.try_into().map_err(|_| "seed must be 32 bytes")?;
    Ok(SigningKey::from_bytes(&bytes))
}

fn bundle_message(body: &str) -> Vec<u8> {
    let mut msg = b"haetae-trust-bundle-v1\0".to_vec();
    msg.extend_from_slice(body.as_bytes());
    msg
}

pub fn sign_bundle(body: &str, seed_hex: &str) -> Result<SignedBundle, String> {
    // Parse before signing so a signer never issues a valid but unusable bundle.
    let _: TrustBody = serde_json::from_str(body).map_err(|e| e.to_string())?;
    let signing = seed(seed_hex)?;
    Ok(SignedBundle {
        body: body.into(),
        signature: hex::encode(signing.sign(&bundle_message(body)).to_bytes()),
    })
}

impl AuthVerifier {
    pub fn load(path: &Path, root_key_hex: &str, policy_bytes: &[u8]) -> Result<Self, String> {
        let bytes = fs::read(path).map_err(|e| e.to_string())?;
        let bundle: SignedBundle = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
        key(root_key_hex)?
            .verify(
                &bundle_message(&bundle.body),
                &signature(&bundle.signature)?,
            )
            .map_err(|_| "trust bundle signature failed")?;
        let body: TrustBody = serde_json::from_str(&bundle.body).map_err(|e| e.to_string())?;
        if body.v != 1 || body.audience.is_empty() || body.epoch == 0 {
            return Err("invalid trust bundle version, audience or epoch".into());
        }
        if body.policy_sha256 != hex::encode(Sha256::digest(policy_bytes)) {
            return Err("policy hash does not match trust bundle".into());
        }
        if !body.keys.contains_key(&Role::World) || !body.keys.contains_key(&Role::Fault) {
            return Err("world and fault keys are required".into());
        }
        let mut keys = BTreeMap::new();
        let mut distinct = std::collections::BTreeSet::new();
        for (role, pubkey) in body.keys {
            let parsed = key(&pubkey)?;
            if !distinct.insert(parsed.to_bytes()) {
                return Err("each input role needs a distinct key".into());
            }
            keys.insert(role, parsed);
        }
        Ok(Self {
            audience: body.audience,
            epoch: body.epoch,
            keys,
            counters: BTreeMap::new(),
        })
    }

    pub fn verify(&mut self, raw: &[u8]) -> Result<Inbound, String> {
        if raw.len() > 1024 * 1024 {
            return Err("signed input too large".into());
        }
        let signed: SignedInput = serde_json::from_slice(raw).map_err(|e| e.to_string())?;
        if signed.v != 1 || signed.audience != self.audience || signed.epoch != self.epoch {
            return Err("wrong input version, audience or epoch".into());
        }
        if signed.counter == 0
            || self
                .counters
                .get(&signed.role)
                .is_some_and(|last| signed.counter <= *last)
        {
            return Err("replayed or zero counter".into());
        }
        let key = self.keys.get(&signed.role).ok_or("untrusted input role")?;
        key.verify(&input_message(&signed)?, &signature(&signed.signature)?)
            .map_err(|_| "input signature failed")?;
        let input = match signed.role {
            Role::World => Inbound::World(
                serde_json::from_str::<WorldSnapshot>(&signed.payload)
                    .map_err(|e| e.to_string())?,
            ),
            Role::Fault => Inbound::Fault(
                serde_json::from_str::<Fault>(&signed.payload).map_err(|e| e.to_string())?,
            ),
            role => {
                let p: ActionProposal =
                    serde_json::from_str(&signed.payload).map_err(|e| e.to_string())?;
                if Some(p.source) != role.source() {
                    return Err("proposal source differs from signing role".into());
                }
                Inbound::Proposal(p)
            }
        };
        self.counters.insert(signed.role, signed.counter);
        Ok(input)
    }

    pub fn epoch(&self) -> u64 {
        self.epoch
    }

    pub fn counters(&self) -> &BTreeMap<Role, u64> {
        &self.counters
    }

    pub fn restore(&mut self, epoch: u64, counters: BTreeMap<Role, u64>) -> Result<(), String> {
        if self.epoch < epoch {
            return Err("trust bundle epoch rolled back".into());
        }
        if self.epoch == epoch {
            self.counters = counters;
        }
        Ok(())
    }
}

fn input_message(s: &SignedInput) -> Result<Vec<u8>, String> {
    let audience = s.audience.as_bytes();
    let payload = s.payload.as_bytes();
    let audience_len: u32 = audience.len().try_into().map_err(|_| "audience too long")?;
    let payload_len: u32 = payload.len().try_into().map_err(|_| "payload too long")?;
    let mut msg = b"haetae-input-v1\0".to_vec();
    msg.extend_from_slice(&audience_len.to_be_bytes());
    msg.extend_from_slice(audience);
    msg.extend_from_slice(&s.epoch.to_be_bytes());
    msg.extend_from_slice(&s.counter.to_be_bytes());
    msg.push(s.role.tag());
    msg.extend_from_slice(&payload_len.to_be_bytes());
    msg.extend_from_slice(payload);
    Ok(msg)
}

pub fn sign_input(mut input: SignedInput, seed_hex: &str) -> Result<SignedInput, String> {
    input.signature.clear();
    let signing = seed(seed_hex)?;
    input.signature = hex::encode(signing.sign(&input_message(&input)?).to_bytes());
    Ok(input)
}

pub fn policy_hash(policy: &Policy) -> Result<String, String> {
    serde_json::to_vec(policy)
        .map(|bytes| hex::encode(Sha256::digest(bytes)))
        .map_err(|e| e.to_string())
}
