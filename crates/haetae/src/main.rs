use std::error::Error;
use std::fs;
use std::io::{self, BufRead, BufReader, Write};
use std::path::{Path, PathBuf};

use clap::{Parser, Subcommand};
use haetae::sillok::{self, Keypair, Sacho, SillokWriter};
use haetae::{ActionKind, ActionProposal, Gate, Mode, Policy, Verdict, WorldSnapshot};
use serde::Deserialize;
use serde_json::{json, Value};

type Result<T> = std::result::Result<T, Box<dyn Error>>;

#[derive(Parser)]
#[command(
    name = "haetae",
    version,
    about = "Robot safety & security stack for physical AI"
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Generate a sillok signing key. Writes the secret seed, prints the public key.
    Keygen {
        #[arg(long)]
        out: PathBuf,
    },
    /// Judge a stream of proposals and record incidents into a sillok log.
    Judge {
        #[arg(long)]
        policy: PathBuf,
        #[arg(long)]
        world: PathBuf,
        /// JSON Lines: a proposal, a `{"fault": {...}}` event, or a `{"world": {...}}` update per line.
        #[arg(long)]
        proposals: PathBuf,
        /// Where to write the sillok log. Created on the first incident.
        #[arg(long, requires = "key")]
        sillok: Option<PathBuf>,
        /// Signing key seed file from `haetae keygen`.
        #[arg(long, requires = "sillok")]
        key: Option<PathBuf>,
        /// How many recent records the sacho ring buffer keeps before an incident.
        #[arg(long, default_value_t = 256, value_parser = clap::value_parser!(u64).range(1..))]
        sacho: u64,
        /// How many steps after an incident are also recorded.
        #[arg(long, default_value_t = 8)]
        post: usize,
    },
    /// Work with sillok logs.
    Sillok {
        #[command(subcommand)]
        command: SillokCommand,
    },
}

#[derive(Subcommand)]
enum SillokCommand {
    /// Check the hash chain and seals of a log.
    Verify {
        #[arg(long)]
        log: PathBuf,
        #[arg(long)]
        pubkey: String,
    },
    /// Print a human-readable timeline of a log (verifies it first).
    Replay {
        #[arg(long)]
        log: PathBuf,
        #[arg(long)]
        pubkey: String,
    },
}

/// A line of the proposals stream.
enum Step {
    /// New facts from the trusted safety-perception path.
    World(WorldSnapshot),
    Fault(Fault),
    Proposal(ActionProposal),
}

impl Step {
    /// Dispatch on the top-level key explicitly. An untagged serde enum would
    /// pick the first variant that fits and silently drop extra keys, so a line
    /// mixing `fault` and `world` could lose the fault.
    fn parse(line: &str) -> std::result::Result<Step, String> {
        let value: Value = serde_json::from_str(line).map_err(|e| e.to_string())?;
        let obj = value.as_object().ok_or("expected a JSON object")?;
        let event = |key: &str| -> std::result::Result<Option<Value>, String> {
            match (obj.get(key), obj.len()) {
                (None, _) => Ok(None),
                (Some(v), 1) => Ok(Some(v.clone())),
                (Some(_), _) => Err(format!("`{key}` must be the only key on its line")),
            }
        };
        let err = |e: serde_json::Error| e.to_string();
        if let Some(w) = event("world")? {
            return serde_json::from_value(w).map(Step::World).map_err(err);
        }
        if let Some(f) = event("fault")? {
            return serde_json::from_value(f).map(Step::Fault).map_err(err);
        }
        serde_json::from_value(value)
            .map(Step::Proposal)
            .map_err(err)
    }
}

/// A fault reported by maek (self-diagnosis). W1 feeds these in by hand.
#[derive(Deserialize, serde::Serialize)]
#[serde(deny_unknown_fields)]
struct Fault {
    code: String,
    timestamp_ms: u64,
    raise_to: Mode,
}

fn main() {
    if let Err(e) = run(Cli::parse()) {
        eprintln!("error: {e}");
        std::process::exit(1);
    }
}

fn run(cli: Cli) -> Result<()> {
    match cli.command {
        Command::Keygen { out } => keygen(&out),
        Command::Judge {
            policy,
            world,
            proposals,
            sillok,
            key,
            sacho,
            post,
        } => {
            let recorder = match (sillok, key) {
                (Some(path), Some(key)) => Some(Recorder::new(path, read_key(&key)?, post)),
                _ => None,
            };
            judge(&policy, &world, &proposals, recorder, sacho as usize)
        }
        Command::Sillok {
            command: SillokCommand::Verify { log, pubkey },
        } => {
            let report = sillok::verify(&log, &pubkey)?;
            println!("{}", serde_json::to_string_pretty(&report_json(&report))?);
            Ok(())
        }
        Command::Sillok {
            command: SillokCommand::Replay { log, pubkey },
        } => replay(&log, &pubkey),
    }
}

fn keygen(out: &Path) -> Result<()> {
    let key = Keypair::generate()?;
    write_secret(out, &key.seed_hex())?;
    println!("{}", key.verifying_key_hex());
    Ok(())
}

#[cfg(unix)]
fn write_secret(path: &Path, contents: &str) -> Result<()> {
    use std::os::unix::fs::OpenOptionsExt;
    let mut f = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(path)?;
    writeln!(f, "{contents}")?;
    Ok(())
}

#[cfg(not(unix))]
fn write_secret(path: &Path, contents: &str) -> Result<()> {
    let mut f = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)?;
    writeln!(f, "{contents}")?;
    Ok(())
}

fn read_key(path: &Path) -> Result<Keypair> {
    Ok(Keypair::from_seed_hex(fs::read_to_string(path)?.trim())?)
}

/// Dashcam-style recorder: nothing is persisted until the first incident.
/// On an incident the sacho window is flushed and sealed, and the next
/// `post_window` steps are recorded too.
struct Recorder {
    path: PathBuf,
    key: Option<Keypair>,
    writer: Option<SillokWriter>,
    post_window: usize,
    post_remaining: usize,
    incidents: usize,
}

impl Recorder {
    fn new(path: PathBuf, key: Keypair, post_window: usize) -> Self {
        Recorder {
            path,
            key: Some(key),
            writer: None,
            post_window,
            post_remaining: 0,
            incidents: 0,
        }
    }

    /// Called after every step. `counts` is false for world updates, so the
    /// post-incident window covers the next `post_window` proposals and faults.
    fn after_step(&mut self, incident: bool, counts: bool, sacho: &mut Sacho) -> Result<()> {
        if incident {
            if let Some(key) = self.key.take() {
                self.writer = Some(SillokWriter::create(&self.path, key, 64)?);
            }
            self.incidents += 1;
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

    fn close(self) -> Result<()> {
        if let Some(w) = self.writer {
            w.close()?;
        }
        Ok(())
    }
}

fn judge(
    policy: &Path,
    world: &Path,
    proposals: &Path,
    mut recorder: Option<Recorder>,
    sacho_capacity: usize,
) -> Result<()> {
    let mut gate = Gate::new(Policy::from_json(&fs::read_to_string(policy)?)?)?;
    let mut world: WorldSnapshot = serde_json::from_str(&fs::read_to_string(world)?)?;
    let mut sacho = Sacho::new(sacho_capacity);
    let mut counts = [0usize; 3];
    let mut last_ts = 0u64;
    let stdout = io::stdout();
    let mut out = stdout.lock();

    for (lineno, line) in BufReader::new(fs::File::open(proposals)?)
        .lines()
        .enumerate()
    {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        let step = Step::parse(&line)
            .map_err(|e| format!("{}:{}: {e}", proposals.display(), lineno + 1))?;
        let counts_toward_post = !matches!(step, Step::World(_));
        let incident = match step {
            Step::World(w) => {
                world = w;
                sacho.push(last_ts, "world", serde_json::to_value(&world)?);
                false
            }
            Step::Fault(fault) => {
                last_ts = fault.timestamp_ms;
                let before = gate.mode();
                gate.raise_mode(fault.raise_to);
                sacho.push(
                    fault.timestamp_ms,
                    "fault",
                    json!({ "fault": fault, "mode_before": before, "mode_after": gate.mode() }),
                );
                gate.mode() > before && gate.mode().stop_only()
            }
            Step::Proposal(p) => {
                last_ts = p.timestamp_ms;
                sacho.push(
                    p.timestamp_ms,
                    "proposal",
                    json!({ "proposal": p, "world": world }),
                );
                let d = gate.judge(&p, &world);
                sacho.push(p.timestamp_ms, "decision", serde_json::to_value(&d)?);
                writeln!(out, "{}", serde_json::to_string(&d)?)?;
                counts[match d.verdict {
                    Verdict::Yun => 0,
                    Verdict::Jeol => 1,
                    Verdict::Bul => 2,
                }] += 1;
                d.verdict == Verdict::Bul
            }
        };
        if let Some(r) = recorder.as_mut() {
            r.after_step(incident, counts_toward_post, &mut sacho)?;
        }
    }

    let incidents = recorder.as_ref().map_or(0, |r| r.incidents);
    eprintln!(
        "yun={} jeol={} bul={} incidents_recorded={incidents}",
        counts[0], counts[1], counts[2]
    );
    if let Some(r) = recorder {
        r.close()?;
    }
    Ok(())
}

fn report_json(r: &sillok::VerifyReport) -> Value {
    json!({
        "ok": true,
        "fully_sealed": r.fully_sealed(),
        "entries": r.entries,
        "seals": r.seals,
        "unsealed_tail": r.unsealed_tail,
        "last_hash": r.last_hash,
    })
}

fn replay(log: &Path, pubkey: &str) -> Result<()> {
    let report = sillok::verify(log, pubkey)?;
    println!(
        "sillok verified: {} entries, {} seals, {} unsealed",
        report.entries, report.seals, report.unsealed_tail
    );
    if report.unsealed_tail > 0 {
        println!("warning: entries marked UNSEALED are not covered by any signature");
    }
    println!();
    let sealed_through = report.entries - report.unsealed_tail;
    for (seq, line) in BufReader::new(fs::File::open(log)?).lines().enumerate() {
        let entry: Value = serde_json::from_str(&line?)?;
        let mark = if seq as u64 >= sealed_through {
            "UNSEALED "
        } else {
            ""
        };
        let ts = clock(entry["ts_ms"].as_u64().unwrap_or(0));
        let p = &entry["payload"];
        let text = |v: &Value| v.as_str().unwrap_or("?").to_string();
        let line = match entry["kind"].as_str().unwrap_or("") {
            "proposal" => format!(
                "proposal#{} from {}: {}",
                p["proposal"]["id"],
                text(&p["proposal"]["source"]),
                describe(&p["proposal"]["action"])
            ),
            "decision" => {
                let cap = match p["speed_cap"].as_f64() {
                    Some(v) => format!("  cap={v} m/s"),
                    None => String::new(),
                };
                format!(
                    "  → {}  fired={}  exec={}{cap}",
                    text(&p["verdict"]),
                    p["fired"],
                    describe(&p["action"])
                )
            }
            "world" => format!(
                "world  robot@({}, {}) holding={}  humans={}",
                p["robot"]["pose"]["x"],
                p["robot"]["pose"]["y"],
                p["robot"]["holding"],
                p["humans"].as_array().map_or(0, Vec::len)
            ),
            "fault" => format!(
                "maek {}  mode {} → {}",
                text(&p["fault"]["code"]),
                text(&p["mode_before"]),
                text(&p["mode_after"])
            ),
            "seal" => format!("── seal (key {}) ──", text(&p["key_id"])),
            other => format!("{other} {p}"),
        };
        println!("{mark}{ts}  {line}");
    }
    Ok(())
}

/// Human-readable action, or `-` when there is none.
fn describe(action: &Value) -> String {
    if action.is_null() {
        return "-".into();
    }
    match serde_json::from_value::<ActionKind>(action.clone()) {
        Ok(a) => a.to_string(),
        Err(_) => action.to_string(),
    }
}

/// `HH:MM:SS.mmm` (UTC) from a unix timestamp in milliseconds.
fn clock(ts_ms: u64) -> String {
    let ms = ts_ms % 86_400_000;
    format!(
        "{:02}:{:02}:{:02}.{:03}",
        ms / 3_600_000,
        ms / 60_000 % 60,
        ms / 1000 % 60,
        ms % 1000
    )
}
