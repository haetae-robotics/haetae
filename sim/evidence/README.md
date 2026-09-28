# Gazebo replay evidence

`gazebo-snapshot.jsonl` and `gazebo-result.json` are unmodified copies from the
`gazebo-reference-evidence` artifact of [GitHub Actions run 36457675543](https://github.com/haetae-robotics/haetae/actions/runs/36457675543),
which ran at commit `4a079346c6ae8376d6ddc4ab831a2cd861ef9be0`.
The snapshot is the fully sealed prefix containing the base stop, arm denial,
and arm cancellation. The browser page derives its animation from `world`
entries (Gazebo odometry and joint states) and `decision`/`revoke` entries.
It does not show a Gazebo GUI capture or a live simulator session. The base
controller's stop after gateway death happened later and is recorded in
`gazebo-result.json`, outside this sealed prefix.

SHA-256:

```text
017099489b4d7f42a441a09f3d00f01eaf482c7a55450e5d6fa15968195361f8  gazebo-snapshot.jsonl
0fb678b4cd64b0ad4b7f3b100664d48f34a2b8d5fe30d769f3dba8f261962f8f  gazebo-result.json
```

The incident snapshot can be checked against the reference log public key:

```bash
cargo run --locked -p haetae -- sillok verify \
  --log sim/evidence/gazebo-snapshot.jsonl \
  --pubkey fd1724385aa0c75b64fb78cd602fa1d991fdebf76b13c58ed702eac835e9f618
```

The page itself does not perform cryptographic verification. CI verifies the
snapshot before publishing its artifact. The fixture uses deterministic test
keys, which must never be used for a deployed robot.
