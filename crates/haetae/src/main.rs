use std::error::Error;
use std::fs;
use std::io::{self, BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::ExitCode;

use clap::{Parser, Subcommand};
use haetae::runtime::{Inbound, Outcome, RecorderConfig, Runtime, RuntimeConfig};
use haetae::sillok::{self, Keypair, VerifyReport};
use haetae::{ActionKind, Policy, Verdict, WorldSnapshot};
use serde_json::{json, Value};

type Result<T> = std::result::Result<T, Box<dyn Error>>;

/// Exit code of `sillok verify` / `sillok replay` for a log whose chain is
/// intact but which is not signed through its end: empty, an unsealed
/// tail, or a torn final line. (1 is any error, 2 a usage error.)
const EXIT_INCOMPLETE: u8 = 3;

#[derive(Parser)]
#[command(
    name = "haetae",
    version,
    about = "Supervisory policy gate for AI-driven robots (non-safety-rated, pre-alpha)"
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
    ///
    /// Exits 0 when the log is intact and complete, 3 when it is intact but
    /// incomplete (empty, unsealed tail, or torn final line), 1 on failure.
    Verify {
        #[arg(long)]
        log: PathBuf,
        #[arg(long)]
        pubkey: String,
    },
    /// Print a human-readable timeline of a log (verifies it first).
    ///
    /// Exit codes as for `verify`. Text from the log is escaped before it
    /// reaches the terminal.
    Replay {
        #[arg(long)]
        log: PathBuf,
        #[arg(long)]
        pubkey: String,
    },
}

fn main() -> ExitCode {
    match run(Cli::parse()) {
        Ok(code) => code,
        Err(e) => {
            // Errors can quote log or input bytes (e.g. an unknown field
            // name, a claimed key_id), so they are escaped too.
            eprintln!("error: {}", sanitize(&e.to_string()));
            ExitCode::FAILURE
        }
    }
}

fn run(cli: Cli) -> Result<ExitCode> {
    match cli.command {
        Command::Keygen { out } => keygen(&out).map(|()| ExitCode::SUCCESS),
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
            judge(&policy, &world, &proposals, cfg).map(|()| ExitCode::SUCCESS)
        }
        Command::Sillok {
            command: SillokCommand::Verify { log, pubkey },
        } => {
            let report = sillok::verify(&log, &pubkey)?;
            println!("{}", serde_json::to_string_pretty(&report_json(&report))?);
            Ok(completeness(&report))
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
///
/// A recorder failure is reported on stderr as it happens, every decision
/// is still printed, and the run then fails (exit 1) once all lines are
/// judged: a run whose incidents were not recorded must not look clean.
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
        let failures = rt.recorder_failures();
        let outcome = match Inbound::from_json(line.as_bytes()) {
            Ok(msg) => {
                if let Inbound::World(w) = &msg {
                    recv_ms = recv_ms.max(w.stamp_ms);
                }
                rt.handle(msg, recv_ms)?
            }
            Err(_) => rt.handle_bytes(line.as_bytes(), recv_ms)?,
        };
        if rt.recorder_failures() > failures {
            if let Some(e) = rt.recorder_fault() {
                eprintln!(
                    "{}:{}: error: sillok recording failed: {}",
                    proposals.display(),
                    lineno + 1,
                    sanitize(&e.to_string())
                );
            }
        }
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
                eprintln!(
                    "{}:{}: rejected: {}",
                    proposals.display(),
                    lineno + 1,
                    sanitize(&error)
                );
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
    // Fails with `RecordingIncomplete` if any recording failed.
    rt.close()?;
    Ok(())
}

/// `ok` is chain integrity (always true here: a broken chain is an
/// error); `complete` additionally requires every byte to be signed.
fn report_json(r: &VerifyReport) -> Value {
    json!({
        "ok": true,
        "complete": r.is_complete(),
        "fully_sealed": r.fully_sealed(),
        "entries": r.entries,
        "seals": r.seals,
        "unsealed_tail": r.unsealed_tail,
        "torn_tail": r.torn_tail,
        "last_hash": r.last_hash,
    })
}

/// Warn on stderr about every way `r` falls short of complete, and pick
/// the exit code: success, or [`EXIT_INCOMPLETE`].
fn completeness(r: &VerifyReport) -> ExitCode {
    if r.is_complete() {
        return ExitCode::SUCCESS;
    }
    if r.entries == 0 {
        eprintln!("warning: the log holds no entries and no seal (the writer may have died right after creating it)");
    } else if r.seals == 0 {
        eprintln!("warning: the log has no seal; nothing in it is signed");
    } else if r.unsealed_tail > 0 {
        eprintln!(
            "warning: {} entries after the last seal (marked UNSEALED) are not covered by any signature",
            r.unsealed_tail
        );
    }
    if r.torn_tail {
        eprintln!("warning: the log ends in a torn, unverified partial line (an interrupted write, or truncation); it was ignored");
    }
    eprintln!("warning: chain intact but log INCOMPLETE (exit {EXIT_INCOMPLETE})");
    ExitCode::from(EXIT_INCOMPLETE)
}

/// Verify and print from a single read of the log, so every printed line
/// is one of the bytes that were verified — even if the file is replaced
/// or rewritten meanwhile.
fn replay(log: &Path, pubkey: &str) -> Result<ExitCode> {
    let bytes = fs::read(log)?;
    let report = sillok::verify_reader(&bytes[..], pubkey)?;
    println!(
        "sillok verified: {} entries, {} seals, {} unsealed{}",
        report.entries,
        report.seals,
        report.unsealed_tail,
        if report.torn_tail { ", torn tail" } else { "" }
    );
    let code = completeness(&report);
    println!();
    let sealed_through = report.entries - report.unsealed_tail;
    // Only the verified entries; a torn tail is marked below, never parsed.
    let lines = bytes.split(|&b| b == b'\n');
    for (seq, raw) in (0..report.entries).zip(lines) {
        let entry: Value = serde_json::from_slice(raw)?;
        let mark = if seq >= sealed_through {
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
        println!("{}", sanitize(&format!("{mark}{ts}  {line}")));
    }
    if report.torn_tail {
        println!("TORN     unverified partial final line (interrupted write, or truncation) — not an entry, ignored");
    }
    Ok(code)
}

/// Make untrusted text (from a log or an input file) safe for a terminal.
///
/// C0 controls except tab, DEL, C1 controls (all of Unicode `Cc`), every
/// Unicode `Bidi_Control` character (ALM, LRM/RLM, the embedding/override
/// and isolate controls) and the line/paragraph separators U+2028/U+2029
/// are replaced by their `\u{..}` escapes. Escaping ESC (and the 8-bit CSI
/// U+009B) makes every escape sequence — colours, cursor moves, OSC title
/// or clipboard writes — inert text; escaping bidi marks keeps ids and
/// digits from being visually reordered.
fn sanitize(s: &str) -> String {
    let unsafe_char = |c: char| {
        (c.is_control() && c != '\t')
            || matches!(
                c,
                '\u{061C}'
                    | '\u{200E}'
                    | '\u{200F}'
                    | '\u{2028}'
                    | '\u{2029}'
                    | '\u{202A}'..='\u{202E}'
                    | '\u{2066}'..='\u{2069}'
            )
    };
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        if unsafe_char(c) {
            out.extend(c.escape_unicode());
        } else {
            out.push(c);
        }
    }
    out
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

#[cfg(test)]
mod tests {
    use super::sanitize;

    #[test]
    fn sanitize_escapes_terminal_controls() {
        assert_eq!(sanitize("a\tb → ok"), "a\tb → ok");
        assert_eq!(sanitize("\x1b[2J"), "\\u{1b}[2J");
        assert_eq!(sanitize("x\ny\r\x07\x7f"), "x\\u{a}y\\u{d}\\u{7}\\u{7f}");
        assert_eq!(sanitize("\u{9b}31m"), "\\u{9b}31m");
        assert_eq!(sanitize("\u{202e}txt"), "\\u{202e}txt");
        for (raw, escaped) in [
            ("\u{61c}", "\\u{61c}"),
            ("\u{200e}", "\\u{200e}"),
            ("\u{200f}bul", "\\u{200f}bul"),
            ("\u{2028}", "\\u{2028}"),
            ("\u{2029}", "\\u{2029}"),
            ("\u{2066}", "\\u{2066}"),
        ] {
            assert_eq!(sanitize(raw), escaped);
        }
    }
}
