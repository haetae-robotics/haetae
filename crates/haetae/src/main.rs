use std::error::Error;
use std::fs;
use std::io::{self, BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::ExitCode;

use clap::{Parser, Subcommand};
use haetae::runtime::{Inbound, Outcome, RecorderConfig, Runtime, RuntimeConfig};
use haetae::sillok::{self, Keypair, VerifyReport};
use haetae::{ActionKind, ActionProposal, Mode, Policy, Source, Verdict, WorldSnapshot};
use haetae_enforce::auth::{self, AuthVerifier, SignedInput};
use haetae_enforce::{Enforcer, EnforcerConfig};
use serde::Deserialize;
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
    /// Build signed trust and input fixtures for authenticated enforcement.
    Auth {
        #[command(subcommand)]
        command: AuthCommand,
    },
    /// Run the monitored command gate over a JSONL subprocess protocol.
    Enforce {
        #[arg(long)]
        policy: PathBuf,
        #[arg(long, conflicts_with = "ephemeral")]
        state: Option<PathBuf>,
        #[arg(long)]
        ephemeral: bool,
        #[arg(long, requires = "key")]
        sillok: Option<PathBuf>,
        #[arg(long, requires = "sillok")]
        key: Option<PathBuf>,
        #[arg(long)]
        stdio: bool,
        #[arg(long, requires = "root_pubkey")]
        trust: Option<PathBuf>,
        #[arg(long, requires = "trust")]
        root_pubkey: Option<String>,
    },
    /// Inspect or change persistent mode while the gate is stopped.
    State {
        #[command(subcommand)]
        command: StateCommand,
    },
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
enum AuthCommand {
    PolicyHash {
        #[arg(long)]
        policy: PathBuf,
    },
    SignBundle {
        #[arg(long)]
        body: PathBuf,
        #[arg(long)]
        key: PathBuf,
        #[arg(long)]
        out: PathBuf,
    },
    SignInput {
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        key: PathBuf,
        #[arg(long)]
        out: PathBuf,
    },
}

#[derive(Subcommand)]
enum StateCommand {
    Show {
        #[arg(long)]
        state: PathBuf,
    },
    Set {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        mode: String,
        #[arg(long)]
        by: String,
        #[arg(long)]
        reason: String,
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
        Command::Auth { command } => {
            match command {
                AuthCommand::PolicyHash { policy } => {
                    use sha2::{Digest, Sha256};
                    println!("{}", hex::encode(Sha256::digest(fs::read(policy)?)));
                }
                AuthCommand::SignBundle { body, key, out } => {
                    let body = fs::read_to_string(body)?;
                    let signed = auth::sign_bundle(body.trim(), fs::read_to_string(key)?.trim())?;
                    create_json(&out, &signed)?;
                }
                AuthCommand::SignInput { input, key, out } => {
                    let input: SignedInput = serde_json::from_slice(&fs::read(input)?)?;
                    let signed = auth::sign_input(input, fs::read_to_string(key)?.trim())?;
                    create_json(&out, &signed)?;
                }
            }
            Ok(ExitCode::SUCCESS)
        }
        Command::Enforce {
            policy,
            state,
            ephemeral,
            sillok,
            key,
            stdio,
            trust,
            root_pubkey,
        } => {
            if !stdio || (!ephemeral && state.is_none()) {
                return Err("enforce needs --stdio and either --state or --ephemeral".into());
            }
            if state.is_some() && trust.is_none() {
                return Err("persistent enforcement requires --trust and --root-pubkey".into());
            }
            if state.is_some() && sillok.is_none() {
                return Err("persistent enforcement requires --sillok and --key".into());
            }
            let recorder = match (sillok, key) {
                (Some(path), Some(key)) => {
                    let mut cfg = RecorderConfig::new(path, read_key(&key)?);
                    // An unexpected bridge death may prevent close(). Each
                    // enforcement incident must already have a durable seal.
                    cfg.post_window = 0;
                    Some(cfg)
                }
                _ => None,
            };
            enforce_stdio(
                &policy,
                state,
                recorder,
                trust.as_deref(),
                root_pubkey.as_deref(),
            )?;
            Ok(ExitCode::SUCCESS)
        }
        Command::State { command } => {
            match command {
                StateCommand::Show { state } => println!(
                    "{}",
                    serde_json::to_string_pretty(&haetae_enforce::show_state(&state)?)?
                ),
                StateCommand::Set {
                    state,
                    mode,
                    by,
                    reason,
                } => {
                    let mode: Mode = serde_json::from_value(Value::String(mode))?;
                    haetae_enforce::set_state(&state, mode, &by, &reason, unix_ms())?;
                }
            }
            Ok(ExitCode::SUCCESS)
        }
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

fn create_json(path: &Path, value: &impl serde::Serialize) -> Result<()> {
    let mut f = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)?;
    serde_json::to_writer(&mut f, value)?;
    writeln!(f)?;
    f.sync_all()?;
    Ok(())
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

#[derive(Deserialize)]
#[serde(tag = "k", rename_all = "snake_case", deny_unknown_fields)]
enum EnforceLine {
    World {
        t: u64,
        data: String,
    },
    Fault {
        t: u64,
        data: String,
    },
    Proposal {
        t: u64,
        data: String,
    },
    Twist {
        t: u64,
        source: Source,
        seq: u64,
        linear: f64,
        angular: f64,
        ttl_ms: u64,
        #[serde(default)]
        stamp_ms: Option<u64>,
    },
    Tick {
        t: u64,
    },
    Signed {
        t: u64,
        data: String,
    },
    Reject {
        t: u64,
        reason: String,
    },
}

impl EnforceLine {
    fn time(&self) -> u64 {
        match self {
            Self::World { t, .. }
            | Self::Fault { t, .. }
            | Self::Proposal { t, .. }
            | Self::Twist { t, .. }
            | Self::Tick { t }
            | Self::Signed { t, .. }
            | Self::Reject { t, .. } => *t,
        }
    }
}

fn enforce_stdio(
    policy_path: &Path,
    state: Option<PathBuf>,
    recorder: Option<RecorderConfig>,
    trust: Option<&Path>,
    root_pubkey: Option<&str>,
) -> Result<()> {
    let policy_bytes = fs::read(policy_path)?;
    let policy = Policy::from_json(std::str::from_utf8(&policy_bytes)?)?;
    let mut verifier = match (trust, root_pubkey) {
        (Some(path), Some(key)) => Some(AuthVerifier::load(path, key, &policy_bytes)?),
        _ => None,
    };
    let cfg = EnforcerConfig {
        runtime: RuntimeConfig {
            recorder,
            ..RuntimeConfig::default()
        },
        state_path: state,
        tick_ms: 50,
    };
    let mut gate = Enforcer::open(policy, cfg, unix_ms())?;
    if let Some(v) = &mut verifier {
        let (epoch, counters) = gate.auth_checkpoint();
        v.restore(epoch, counters)?;
    }
    let stdin = io::stdin();
    let mut input = stdin.lock();
    let stdout = io::stdout();
    let mut output = stdout.lock();
    let mut last_t = 0;
    while let Some((line, oversized)) = read_bounded_line(&mut input)? {
        let parsed = if oversized {
            Err("input line exceeds 1 MiB".to_string())
        } else {
            serde_json::from_slice::<EnforceLine>(&line).map_err(|e| e.to_string())
        };
        let step = match parsed {
            Ok(msg) if msg.time() >= last_t => {
                let t = msg.time();
                last_t = t;
                if verifier.is_some()
                    && !matches!(
                        msg,
                        EnforceLine::Signed { .. }
                            | EnforceLine::Tick { .. }
                            | EnforceLine::Reject { .. }
                    )
                {
                    gate.reject(
                        "unsigned input refused in authenticated mode".into(),
                        &line,
                        t,
                    )
                } else {
                    match msg {
                        EnforceLine::Tick { .. } => gate.tick(t),
                        EnforceLine::Reject { reason, .. } => gate.reject(reason, &line, t),
                        EnforceLine::Signed { data, .. } => match &mut verifier {
                            Some(v) => match v.verify(data.as_bytes()) {
                                Ok(input) => {
                                    gate.set_auth_checkpoint(v.epoch(), v.counters().clone());
                                    gate.handle(input, t)
                                }
                                Err(error) => gate.reject(error, data.as_bytes(), t),
                            },
                            None => gate.reject("signed input requires --trust".into(), &line, t),
                        },
                        EnforceLine::World { data, .. } => {
                            let raw: std::result::Result<Value, _> = serde_json::from_str(&data);
                            match raw {
                                Ok(w) => gate.handle_bytes(
                                    serde_json::to_string(&json!({"world":w}))?.as_bytes(),
                                    t,
                                ),
                                Err(_) => gate.handle_bytes(data.as_bytes(), t),
                            }
                        }
                        EnforceLine::Fault { data, .. } => {
                            let raw: std::result::Result<Value, _> = serde_json::from_str(&data);
                            match raw {
                                Ok(f) => gate.handle_bytes(
                                    serde_json::to_string(&json!({"fault":f}))?.as_bytes(),
                                    t,
                                ),
                                Err(_) => gate.handle_bytes(data.as_bytes(), t),
                            }
                        }
                        EnforceLine::Proposal { data, .. } => gate.handle_bytes(data.as_bytes(), t),
                        EnforceLine::Twist {
                            source,
                            seq,
                            linear,
                            angular,
                            ttl_ms,
                            stamp_ms,
                            ..
                        } => {
                            let _ = stamp_ms; // untrusted claim, never the receive clock
                            gate.handle(
                                Inbound::Proposal(ActionProposal {
                                    id: seq,
                                    source,
                                    timestamp_ms: t,
                                    action: ActionKind::Velocity {
                                        linear,
                                        angular,
                                        ttl_ms,
                                    },
                                }),
                                t,
                            )
                        }
                    }
                }
            }
            Ok(_) => gate.reject("transport time moved backwards".into(), &line, last_t),
            Err(error) => gate.reject(error, &line, last_t),
        };
        writeln!(output, "{}", serde_json::to_string(&step)?)?;
        output.flush()?; // publish zero before any seal or state fsync
        gate.commit(last_t)?;
    }
    // The transport has no authenticated clean-shutdown command. EOF may mean
    // the ROS bridge was killed. Record and seal that stop while retaining the
    // running marker so restart still enters Hold.
    gate.reject("enforcement transport closed".into(), b"", last_t);
    gate.commit(last_t)?;
    Err("enforcement transport closed; offline reset required".into())
}

fn read_bounded_line(input: &mut impl BufRead) -> io::Result<Option<(Vec<u8>, bool)>> {
    const MAX: usize = 1024 * 1024;
    let mut line = Vec::new();
    let mut oversized = false;
    let mut read_any = false;
    loop {
        let buf = input.fill_buf()?;
        if buf.is_empty() {
            return if read_any {
                Ok(Some((line, oversized)))
            } else {
                Ok(None)
            };
        }
        read_any = true;
        let end = buf.iter().position(|&b| b == b'\n');
        let consumed = end.map_or(buf.len(), |i| i + 1);
        let data = &buf[..end.unwrap_or(buf.len())];
        if line.len() + data.len() > MAX {
            oversized = true;
        }
        if !oversized {
            line.extend_from_slice(data);
        }
        input.consume(consumed);
        if end.is_some() {
            if line.last() == Some(&b'\r') {
                line.pop();
            }
            return Ok(Some((line, oversized)));
        }
    }
}

fn unix_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_millis() as u64)
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
