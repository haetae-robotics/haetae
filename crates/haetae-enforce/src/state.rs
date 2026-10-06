use std::collections::BTreeMap;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use crate::auth::Role;
use crate::history::History;
use haetae_core::{Mode, Policy};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct StateFile {
    v: u8,
    mode: Mode,
    running: bool,
    reason: String,
    set_by: String,
    ts_ms: u64,
    #[serde(default)]
    auth_epoch: u64,
    #[serde(default)]
    counters: BTreeMap<Role, u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    history: Option<History>,
}

pub struct StateStore {
    path: PathBuf,
    _lock: File,
    current: Mode,
    auth_epoch: u64,
    counters: BTreeMap<Role, u64>,
    history: Option<History>,
}

impl StateStore {
    pub fn open(path: PathBuf, now_ms: u64, policy: &Policy) -> io::Result<Self> {
        let lock = lock(&path)?;
        let protected = policy.household.is_some();
        let (loaded, startup_reason) = match read_state(&path) {
            Ok(state) => {
                let reason = if state.running {
                    "unclean-restart"
                } else {
                    "restart"
                };
                (Some(state), reason)
            }
            Err(e) if e.kind() == io::ErrorKind::NotFound => (None, "first-boot"),
            Err(e) if protected => return Err(e),
            Err(_) => (None, "state:untrusted"),
        };
        let mut history = loaded.as_ref().and_then(|s| s.history.clone());
        if loaded
            .as_ref()
            .is_some_and(|s| s.v == 2 && s.history.is_none())
        {
            return Err(io::Error::other("v2 history is missing"));
        }
        let history_initialized = protected && history.is_none();
        if history_initialized {
            history = History::new(policy);
        }
        if let Some(h) = &mut history {
            h.validate(policy).map_err(io::Error::other)?;
            h.restart();
        }
        let store = StateStore {
            path,
            _lock: lock,
            current: loaded.as_ref().map_or(Mode::Hold, |s| {
                if s.running {
                    s.mode.max(Mode::Hold)
                } else {
                    s.mode
                }
            }),
            auth_epoch: loaded.as_ref().map_or(0, |s| s.auth_epoch),
            counters: loaded.map_or_else(BTreeMap::new, |s| s.counters),
            history,
        };
        // Running marker and interrupted reservation are one atomic write.
        store.write(
            store.current,
            true,
            if history_initialized {
                "history-initialized"
            } else {
                startup_reason
            },
            "haetae",
            now_ms,
        )?;
        Ok(store)
    }

    pub fn history(&self) -> Option<History> {
        self.history.clone()
    }

    pub fn mode(&self) -> Mode {
        self.current
    }

    pub fn checkpoint(&self) -> (u64, BTreeMap<Role, u64>) {
        (self.auth_epoch, self.counters.clone())
    }

    /// Mode, authentication counters and motion reservation share one commit.
    pub fn persist_checkpoint(
        &mut self,
        mode: Mode,
        auth: Option<(u64, BTreeMap<Role, u64>)>,
        history: &Option<History>,
        now_ms: u64,
    ) -> io::Result<()> {
        let (epoch, counters) = auth.unwrap_or((self.auth_epoch, self.counters.clone()));
        if epoch < self.auth_epoch {
            return Err(io::Error::other("auth epoch rollback"));
        }
        if mode == self.current
            && epoch == self.auth_epoch
            && counters == self.counters
            && history == &self.history
        {
            return Ok(());
        }
        let state = StateFile {
            v: if history.is_some() { 2 } else { 1 },
            mode,
            running: true,
            reason: "execution-checkpoint".into(),
            set_by: "haetae".into(),
            ts_ms: now_ms,
            auth_epoch: epoch,
            counters: counters.clone(),
            history: history.clone(),
        };
        write_atomic(
            &self.path,
            &serde_json::to_vec(&state).map_err(io::Error::other)?,
        )?;
        self.current = mode;
        self.auth_epoch = epoch;
        self.counters = counters;
        self.history = history.clone();
        Ok(())
    }

    pub fn close(self, now_ms: u64) -> io::Result<()> {
        self.write(self.current, false, "clean-stop", "haetae", now_ms)
    }

    fn write(
        &self,
        mode: Mode,
        running: bool,
        reason: &str,
        set_by: &str,
        ts_ms: u64,
    ) -> io::Result<()> {
        let state = StateFile {
            v: if self.history.is_some() { 2 } else { 1 },
            mode,
            running,
            reason: reason.into(),
            set_by: set_by.into(),
            ts_ms,
            auth_epoch: self.auth_epoch,
            counters: self.counters.clone(),
            history: self.history.clone(),
        };
        let bytes = serde_json::to_vec(&state).map_err(io::Error::other)?;
        write_atomic(&self.path, &bytes)
    }
}

const MAX_STATE_BYTES: u64 = 1024 * 1024;

fn read_state(path: &Path) -> io::Result<StateFile> {
    let mut bytes = Vec::new();
    File::open(path)?
        .take(MAX_STATE_BYTES + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() as u64 > MAX_STATE_BYTES {
        return Err(io::Error::other("state exceeds 1 MiB"));
    }
    let state: StateFile = serde_json::from_slice(&bytes).map_err(io::Error::other)?;
    if !matches!(state.v, 1 | 2)
        || (state.v == 1 && state.history.is_some())
        || (state.v == 2 && state.history.is_none())
    {
        return Err(io::Error::other("unsupported state version"));
    }
    Ok(state)
}

pub fn show(path: &Path) -> io::Result<serde_json::Value> {
    serde_json::to_value(read_state(path)?).map_err(io::Error::other)
}

/// Offline mode reset preserves object facts, consumed work and pending stops.
pub fn set(path: &Path, mode: Mode, by: &str, reason: &str, now_ms: u64) -> io::Result<()> {
    if by.trim().is_empty() || reason.trim().is_empty() {
        return Err(io::Error::other("operator and reason are required"));
    }
    let _guard = lock(path)?;
    let mut state = match read_state(path) {
        Ok(s) => s,
        Err(e) if e.kind() == io::ErrorKind::NotFound => StateFile {
            v: 1,
            mode: Mode::Hold,
            running: false,
            reason: String::new(),
            set_by: String::new(),
            ts_ms: now_ms,
            auth_epoch: 0,
            counters: BTreeMap::new(),
            history: None,
        },
        Err(e) => return Err(e),
    };
    state.mode = mode;
    state.running = false;
    state.reason = reason.into();
    state.set_by = by.into();
    state.ts_ms = now_ms;
    write_atomic(path, &serde_json::to_vec(&state).map_err(io::Error::other)?)
}

fn lock(path: &Path) -> io::Result<File> {
    let name = path
        .file_name()
        .ok_or_else(|| io::Error::other("state path has no file name"))?;
    let lock_path = path.with_file_name(format!("{}.lock", name.to_string_lossy()));
    let mut options = OpenOptions::new();
    options.create(true).read(true).write(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let f = options.open(lock_path)?;
    f.try_lock()?;
    Ok(f)
}

fn write_atomic(path: &Path, bytes: &[u8]) -> io::Result<()> {
    if bytes.len() as u64 > MAX_STATE_BYTES {
        return Err(io::Error::other("state exceeds 1 MiB"));
    }
    static NONCE: AtomicU64 = AtomicU64::new(0);
    let parent = path
        .parent()
        .ok_or_else(|| io::Error::other("state path has no parent"))?;
    let name = path
        .file_name()
        .ok_or_else(|| io::Error::other("state path has no file name"))?;
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |duration| duration.as_nanos());
    let tmp = parent.join(format!(
        ".{}.{}.{}.{}.tmp",
        name.to_string_lossy(),
        std::process::id(),
        nanos,
        NONCE.fetch_add(1, Ordering::Relaxed)
    ));
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let result = (|| -> io::Result<()> {
        let mut f = options.open(&tmp)?;
        f.write_all(bytes)?;
        f.sync_all()?;
        fs::rename(&tmp, path)?;
        File::open(parent)?.sync_all()?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(tmp);
    }
    result
}
