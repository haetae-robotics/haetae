//! Ed25519 keypair handling for seals (contract §2.2).
//!
//! Keys are plain software keys kept in process memory.
// TODO(W4): TPM / secure-element backing; split-key encryption at rest.

use ed25519_dalek::{Signature, Signer, SigningKey, VerifyingKey};
use sha2::{Digest, Sha256};

use crate::error::Error;
use crate::Result;

/// An Ed25519 keypair that signs seal entries.
///
/// Derive persistence formats with [`Keypair::seed_hex`] (secret) and
/// [`Keypair::verifying_key_hex`] (public, handed to [`crate::verify`]).
pub struct Keypair {
    signing: SigningKey,
}

impl Keypair {
    /// Fresh keypair from a 32-byte seed drawn from the OS RNG.
    pub fn generate() -> Result<Self> {
        let mut seed = [0u8; 32];
        getrandom::fill(&mut seed).map_err(Error::Random)?;
        Ok(Self::from_seed(seed))
    }

    /// Rebuild a keypair from its raw 32-byte seed.
    pub fn from_seed(seed: [u8; 32]) -> Self {
        Keypair {
            signing: SigningKey::from_bytes(&seed),
        }
    }

    /// Rebuild a keypair from the hex encoding of its 32-byte seed.
    pub fn from_seed_hex(s: &str) -> Result<Self> {
        let raw = hex::decode(s).map_err(|e| Error::BadSeed(format!("not hex: {e}")))?;
        let seed: [u8; 32] = raw
            .as_slice()
            .try_into()
            .map_err(|_| Error::BadSeed(format!("expected 32 bytes, got {}", raw.len())))?;
        Ok(Self::from_seed(seed))
    }

    /// Hex-encoded 32-byte seed. Keep it secret.
    pub fn seed_hex(&self) -> String {
        hex::encode(self.signing.to_bytes())
    }

    /// Hex-encoded 32-byte verifying key — the string [`crate::verify`] takes.
    pub fn verifying_key_hex(&self) -> String {
        hex::encode(self.signing.verifying_key().to_bytes())
    }

    /// First 8 bytes of SHA-256(verifying key) as 16 hex chars; written into
    /// every seal so a verifier can spot a wrong key before checking the sig.
    pub fn key_id(&self) -> String {
        key_id_of(&self.signing.verifying_key())
    }

    /// Sign `msg` (for seals, the domain-separated seal message — contract
    /// §2.2).
    pub(crate) fn sign(&self, msg: &[u8]) -> Signature {
        self.signing.sign(msg)
    }
}

/// [`Keypair::key_id`] for a bare verifying key.
pub(crate) fn key_id_of(key: &VerifyingKey) -> String {
    let digest = Sha256::digest(key.to_bytes());
    hex::encode(&digest[..8])
}
