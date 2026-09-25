use std::error::Error;
use std::fs;
use std::io::{self, BufRead, BufReader, Write};
use std::path::{Path, PathBuf};

use clap::{Parser, Subcommand};
use haetae::runtime::{Inbound, Outcome, RecorderConfig, Runtime, RuntimeConfig};
use haetae::sillok::{self, Keypair};
use haetae::{ActionKind, Policy, Verdict, WorldSnapshot};
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
                (Some(path), Some(key)) => Some(RecorderConfig {
                    post_window: post,
                    ..RecorderConfig::new(path, read_key(&key)?)
                }),
                _ => None,
            };
            let cfg = RuntimeConfig {
                sacho_capacity: sacho as usize,
                recorder,
                ..RuntimeConfig::default()
            };
            judge(&policy, &world, &proposals, cfg)
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

/// File transport for the runtime: one JSON line in, one outcome out.
///
/// Receive time is stream time taken from the trusted perception path only:
/// the latest world `stamp_ms` seen so far. Proposal and fault timestamps are
/// untrusted claims and never move the clock, so one forged far-future line
/// cannot make everything after it stale. Malformed lines are rejected and
/// the run continues.
fn judge(policy: &Path, world: &Path, proposals: &Path, cfg: RuntimeConfig) -> Result<()> {
    let policy = Policy::from_json(&fs::read_to_string(policy)?)?;
    let world: WorldSnapshot = serde_json::from_str(&fs::read_to_string(world)?)?;
    let mut recv_ms = world.stamp_ms;
    let mut rt = Runtime::new(policy, Some(world), cfg)?;
    let mut counts = [0usize; 3];
    let mut rejected = 0usize;
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
        let outcome = match Inbound::from_json(line.as_bytes()) {
            Ok(msg) => {
                if let Inbound::World(w) = &msg {
                    recv_ms = recv_ms.max(w.stamp_ms);
                }
                rt.handle(msg, recv_ms)?
            }
            Err(_) => rt.handle_bytes(line.as_bytes(), recv_ms)?,
        };
        match outcome {
            Outcome::Decision(d) => {
                writeln!(out, "{}", serde_json::to_string(&d)?)?;
                counts[match d.verdict {
                    Verdict::Yun => 0,
                    Verdict::Jeol => 1,
                    Verdict::Bul => 2,
                }] += 1;
            }
            Outcome::Rejected { error } => {
                rejected += 1;
                eprintln!("{}:{}: rejected: {error}", proposals.display(), lineno + 1);
            }
            Outcome::WorldUpdated { .. } | Outcome::ModeChanged { .. } => {}
        }
    }

    eprintln!(
        "yun={} jeol={} bul={} rejected={rejected} incidents={}",
        counts[0],
        counts[1],
        counts[2],
        rt.incidents()
    );
    rt.close()?;
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
            "reject" => format!("reject  {}", text(&p["error"])),
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
