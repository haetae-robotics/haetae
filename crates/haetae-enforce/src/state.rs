use std::collections::BTreeMap;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use crate::auth::Role;
use haetae_core::Mode;
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
}

pub struct StateStore {
    path: PathBuf,
    _lock: File,
    current: Mode,
    auth_epoch: u64,
    counters: BTreeMap<Role, u64>,
}

impl StateStore {
    pub fn open(path: PathBuf, now_ms: u64) -> io::Result<Self> {
        let lock = lock(&path)?;
        let (mode, reason, auth_epoch, counters) = match fs::read(&path) {
            Ok(bytes) => match serde_json::from_slice::<StateFile>(&bytes) {
                Ok(s) if s.v == 1 && !s.running => (s.mode, "restart", s.auth_epoch, s.counters),
                Ok(s) if s.v == 1 => (
                    s.mode.max(Mode::Hold),
                    "unclean-restart",
                    s.auth_epoch,
                    s.counters,
                ),
                _ => (Mode::Hold, "state:untrusted", 0, BTreeMap::new()),
            },
            Err(e) if e.kind() == io::ErrorKind::NotFound => {
                (Mode::Hold, "first-boot", 0, BTreeMap::new())
            }
            Err(_) => (Mode::Hold, "state:untrusted", 0, BTreeMap::new()),
        };
        let store = StateStore {
            path,
            _lock: lock,
            current: mode,
            auth_epoch,
            counters,
        };
        // Persist the running marker before any motion can be admitted. A
        // SIGKILL between a mode raise and commit then restarts in Hold.
        store.write(mode, true, reason, "haetae", now_ms)?;
        Ok(store)
    }

    pub fn mode(&self) -> Mode {
        self.current
    }

    pub fn checkpoint(&self) -> (u64, BTreeMap<Role, u64>) {
        (self.auth_epoch, self.counters.clone())
    }

    pub fn persist(&mut self, mode: Mode, now_ms: u64) -> io::Result<()> {
        if mode != self.current {
            self.write(mode, true, "mode-raised", "haetae", now_ms)?;
            self.current = mode;
        }
        Ok(())
    }

    pub fn persist_auth(
        &mut self,
        epoch: u64,
        counters: &BTreeMap<Role, u64>,
        now_ms: u64,
    ) -> io::Result<()> {
        if epoch < self.auth_epoch {
            return Err(io::Error::other("auth epoch rollback"));
        }
        if epoch != self.auth_epoch || counters != &self.counters {
            self.auth_epoch = epoch;
            self.counters = counters.clone();
            self.write(self.current, true, "auth-checkpoint", "haetae", now_ms)?;
        }
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
            v: 1,
            mode,
            running,
            reason: reason.into(),
            set_by: set_by.into(),
            ts_ms,
            auth_epoch: self.auth_epoch,
            counters: self.counters.clone(),
        };
        let bytes = serde_json::to_vec(&state).map_err(io::Error::other)?;
        write_atomic(&self.path, &bytes)
    }
}

pub fn show(path: &Path) -> io::Result<serde_json::Value> {
    let bytes = fs::read(path)?;
    let state: StateFile = serde_json::from_slice(&bytes).map_err(io::Error::other)?;
    if state.v != 1 {
        return Err(io::Error::other("unsupported state version"));
    }
    serde_json::to_value(state).map_err(io::Error::other)
}

/// Offline operator operation. Fails while an enforcer holds the state lock.
pub fn set(path: &Path, mode: Mode, by: &str, reason: &str, now_ms: u64) -> io::Result<()> {
    if by.trim().is_empty() || reason.trim().is_empty() {
        return Err(io::Error::other("operator and reason are required"));
    }
    let _guard = lock(path)?;
    let (auth_epoch, counters) = match fs::read(path) {
        Ok(bytes) => match serde_json::from_slice::<StateFile>(&bytes) {
            Ok(s) if s.v == 1 => (s.auth_epoch, s.counters),
            _ => return Err(io::Error::other("cannot reset an untrusted state file")),
        },
        Err(e) if e.kind() == io::ErrorKind::NotFound => (0, BTreeMap::new()),
        Err(e) => return Err(e),
    };
    let state = StateFile {
        v: 1,
        mode,
        running: false,
        reason: reason.into(),
        set_by: by.into(),
        ts_ms: now_ms,
        auth_epoch,
        counters,
    };
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
