use std::io::Write;
use std::process::{Command, Stdio};

fn run(input: &[u8]) -> std::process::Output {
    let mut child = Command::new(env!("CARGO_BIN_EXE_haetae"))
        .arg("hazard-judge")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child.stdin.take().unwrap().write_all(input).unwrap();
    child.wait_with_output().unwrap()
}

fn request() -> serde_json::Value {
    serde_json::json!({"now_ms":1000,"observed_ms":1000,"confidence":1.0,"coverage_known":true,
        "item":"inert","observed_start":{"x":0,"y":0,"z":0},"swept_radius_m":0.15,
        "path":[{"x":0,"y":0,"z":0},{"x":1,"y":0,"z":0}],"regions":[]})
}

#[test]
fn valid_and_unknown_inputs_have_one_explicit_decision() {
    let mut r = request();
    let output = run(serde_json::to_string(&r).unwrap().as_bytes());
    assert!(output.status.success());
    assert_eq!(
        serde_json::from_slice::<serde_json::Value>(&output.stdout).unwrap()["allowed"],
        true
    );
    r["coverage_known"] = false.into();
    let output = run(serde_json::to_string(&r).unwrap().as_bytes());
    assert!(output.status.success());
    assert_eq!(
        serde_json::from_slice::<serde_json::Value>(&output.stdout).unwrap()["allowed"],
        false
    );
}

#[test]
fn malformed_unknown_fields_and_oversized_records_never_allow() {
    let mut r = request();
    r["model_claimed_safe"] = true.into();
    for input in [
        serde_json::to_vec(&r).unwrap(),
        b"{malformed}\n".to_vec(),
        vec![b' '; 262145],
    ] {
        let output = run(&input);
        assert!(!output.status.success());
        assert!(output.stdout.is_empty());
    }
}
