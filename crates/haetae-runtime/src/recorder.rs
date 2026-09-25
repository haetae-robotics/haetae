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
pub(crate) struct Recorder {
    path: PathBuf,
    /// Held until the first incident creates the log; `None` afterwards.
    key: Option<Keypair>,
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
            key: Some(cfg.key),
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
    pub(crate) fn after_step(
        &mut self,
        incident: bool,
        counts: bool,
        sacho: &mut Sacho,
    ) -> sillok::Result<()> {
        if incident {
            if let Some(key) = self.key.take() {
                self.writer = Some(SillokWriter::create(&self.path, key, self.seal_every)?);
            }
            self.post_remaining = self.post_window;
        } else if self.post_remaining == 0 {
            return Ok(());
        } else if counts {
            self.post_remaining -= 1;
        }
        if let Some(w) = self.writer.as_mut() {
            sacho.flush_into(w)?;
            if incident || self.post_remaining == 0 {
                w.seal()?;
            }
        }
        Ok(())
    }

    /// Final seal, flush and fsync — but only when a log was ever created.
    pub(crate) fn close(self) -> sillok::Result<()> {
        if let Some(w) = self.writer {
            w.close()?;
        }
        Ok(())
    }
}
