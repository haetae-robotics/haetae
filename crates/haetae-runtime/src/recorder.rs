//! Dashcam recorder: nothing is persisted until the first incident.

use std::path::PathBuf;

use sillok::{Keypair, Sacho, SillokWriter};

use crate::runtime::RecorderConfig;

/// Owns the sillok log once an incident creates it.
///
/// Every record lands in the sacho ring first (that happens in
/// [`crate::Runtime`]); on an incident the log file is created lazily — a
/// run with no incidents leaves no log — the backlog is drained and sealed,
/// and the next `post_window` proposals/faults are recorded too before the
/// window is sealed shut.
///
/// Failures never lose the key. If creating the log fails, the next
/// incident tries again (creation still refuses to overwrite an existing
/// file). If a write to an open log fails, that writer is discarded — its
/// file may end in a partial line — and is never written again; a later
/// incident's creation attempt then fails on the existing file and is
/// reported like any other failure.
pub(crate) struct Recorder {
    path: PathBuf,
    /// Kept for the whole run so creation can be retried after a failure.
    key: Keypair,
    seal_every: usize,
    post_window: usize,
    /// Counting messages (proposals and faults) left in the open
    /// post-incident window. 0 means no window is open.
    post_remaining: usize,
    writer: Option<SillokWriter>,
}

impl Recorder {
    pub(crate) fn new(cfg: RecorderConfig) -> Self {
        Recorder {
            path: cfg.path,
            key: cfg.key,
            seal_every: cfg.seal_every,
            post_window: cfg.post_window,
            post_remaining: 0,
            writer: None,
        }
    }

    /// Called once per handled message, after its records are in the sacho.
    ///
    /// `incident` (re)opens the post window and lazily creates the log.
    /// `counts` marks a message that consumes the window — proposals and
    /// faults count; world updates and rejects are recorded while a window
    /// is open but never shorten it. The window and every incident end in a
    /// seal, so a crash mid-incident still leaves a signed log.
    ///
    /// On `Err` nothing is lost from the sacho that was not written, and
    /// the recorder stays usable: see the type docs for retry rules.
    pub(crate) fn after_step(
        &mut self,
        incident: bool,
        counts: bool,
        sacho: &mut Sacho,
    ) -> sillok::Result<()> {
        if incident {
            self.post_remaining = self.post_window;
            if self.writer.is_none() {
                self.writer = Some(SillokWriter::create(
                    &self.path,
                    self.key.clone(),
                    self.seal_every,
                )?);
            }
        } else if self.post_remaining == 0 {
            return Ok(());
        } else if counts {
            self.post_remaining -= 1;
        }
        let seal = incident || self.post_remaining == 0;
        if let Some(w) = self.writer.as_mut() {
            let written = sacho
                .flush_into(w)
                .and_then(|_| if seal { w.seal() } else { Ok(()) });
            if written.is_err() {
                // The file may now end mid-line; never append after that.
                self.writer = None;
            }
            written?;
        }
        Ok(())
    }

    /// Final seal, flush and fsync — but only when a log is open.
    pub(crate) fn close(self) -> sillok::Result<()> {
        if let Some(w) = self.writer {
            w.close()?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn recorder(path: PathBuf, key: Keypair) -> Recorder {
        let mut cfg = RecorderConfig::new(path, key);
        cfg.post_window = 0;
        Recorder::new(cfg)
    }

    /// R1, later-failure path: a write that fails on an already-open log
    /// discards the writer. The file is never appended to again; the next
    /// incident's creation attempt fails on the existing file instead.
    #[test]
    fn a_failed_write_discards_the_writer_for_good() {
        let dir = tempfile::TempDir::new().expect("tempdir");
        let path = dir.path().join("log.jsonl");
        let key = Keypair::generate().expect("key");
        let vk = key.verifying_key_hex();
        let mut rec = recorder(path.clone(), key);
        let mut sacho = Sacho::new(16);

        sacho.push(1, "note", json!({"i": 1}));
        rec.after_step(true, true, &mut sacho)
            .expect("first incident");
        let before = std::fs::read(&path).expect("log exists");

        // Inject a write-path failure: a reserved kind makes `append` fail.
        sacho.push(2, "seal", json!({}));
        assert!(matches!(
            rec.after_step(true, true, &mut sacho),
            Err(sillok::Error::ReservedKind)
        ));
        assert!(rec.writer.is_none(), "the failed writer is discarded");

        let mut sacho = Sacho::new(16);
        sacho.push(3, "note", json!({"i": 3}));
        assert!(matches!(
            rec.after_step(true, true, &mut sacho),
            Err(sillok::Error::AlreadyExists(_))
        ));
        assert_eq!(sacho.len(), 1, "unwritten records stay in the sacho");
        rec.close().expect("no open writer to close");

        let after = std::fs::read(&path).expect("log still there");
        assert_eq!(after, before, "nothing appended after the failure");
        assert!(sillok::verify(&path, &vk).expect("intact").is_complete());
    }
}
