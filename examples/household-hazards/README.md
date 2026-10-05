# Household hazard lab

`./haetae-demo hazards` starts a separate, local Gazebo evaluation profile.
Open http://127.0.0.1:8765/, press Start, inspect the stopped scene, then
press Next. Every category pairs rejection with measured allowed arm motion.
`./haetae-demo start` starts the existing security reference; stop the lab first
with `./haetae-demo stop` when switching back. `./haetae-demo report` downloads
the completed profile's unsigned report. Never use real hazardous materials.

| Fixture case | Rejected action | Allowed control |
| --- | --- | --- |
| Human volume | Tool sphere intersects a fixture labelled human | Same item moves away |
| Heat | Pressurized item contacts active heat fixture | Same item moves away |
| Electricity | Conductive tool contacts energized fixture | Same item moves away |
| Water | Battery enters water fixture | Same item moves away |
| Contents | The same bottle fixture is relabelled as a second incompatible cleaner, approaching a vessel retaining the first cleaner | Relabelled item moves away |
| Fall volume | End effector enters an excluded volume | End effector moves away |

The human/fall cases are end-effector keepouts. They do not demonstrate human
recognition, knife-tip orientation checks, whole-arm collision prevention,
edge detection, base stability, pushing, dropping or throwing prevention.
The chemical fixture retains a bleach/ammonia pair; no general chemical
compatibility database or fluid simulation is implemented.
The chemical sequence deliberately reuses one visible bottle: its trusted
test label changes from bleach to ammonia after measured transfer and retreat.
The scene pauses and displays this change; no second physical bottle or liquid
transfer is claimed. Each transfer/retreat uses two separately checked slow
one-second chunks with measured tracking evidence.
In this profile only, the trusted VLA signer is configured with a fixed
1000 ms arm lease. Each trajectory finishes at 900 ms, leaving time for the
action result before lease expiry; ramp motion ends at 800 ms. The legacy
signer still derives the lease from the final waypoint. Core limits and the
independent 250 ms controller watchdog remain unchanged.

## Trust and execution

The fixed root-owned Python adapter, ROS/Gazebo observation, semantic fixture
labels, and existing signed arm gateway/controller are trusted. There is no
public household proposal endpoint. `hazard-judge` is a trusted-adapter JSONL
interface, not an authenticated interlock: other clients of the existing arm
gate do **not** gain these checks. It must not be exposed directly to a model.

The adapter derives the path from immutable joint waypoints and the generated
manufacturer URDF. It sends those same waypoints through the signed arm gate
only after Rust allows the derived path. A SHA-256 identifies the local plan
for audit comparison; it is not a gate-side semantic authorization token.
Controls require accepted signed arm outcomes with identical waypoints,
measured motion and at least ten joint tracking samples within 0.05 rad.
After ordinary completed motion, the next fixed test explicitly sends signed
zero commands to acquire a new lease. Faulted, stale, revoked or denied gate
states are not automatically rearmed.
Rejected plans are not submitted; measured stationary joints are secondary
evidence. The semantics are an execution-before check for this fixed lab,
not continuous semantic perception or a production robot safety function.

Both simulation and wall-clock observation freshness are required. Odometry,
joint and target observations contribute their original stamps. Immediately
after judging, the adapter checks a 50 ms dispatch budget, 200 ms original
observation age, unchanged target/base pose and measured joint start. During
allowed motion, target/base observation failure stops refreshing the existing
world stream; its original 200 ms expiry and independent 250 ms controller
lease remain in force. This is fixture-pose/base monitoring, not evolving
material/device-state inference.

Paths sample linear joint interpolation at at most 0.002 rad. The 0.15 m
end-effector sphere covers the miniature item's <0.06 m bounding radius,
<0.07 m displacement from four 0.05 rad joint tracking errors (sum of pinned
chain lever lengths), and a 0.02 m allowance for sampling, base drift, and
hand envelope. This excludes arm links; it deliberately overblocks some
near-misses. The axis-expanded region box is a conservative sphere sweep
superset. Device fixtures fit a 0.12 × 0.12 × 0.18 m volume.

The lab starts with joint positions (0, 0.4, -0.4, 0) so the tool is clear of
the base. Only simulator initial position parameters change; manufacturer
geometry, inertia, mounting, limits and the legacy Home posture stay intact.
The fixtures have original locally authored miniature geometry, no collision
or grasp/fluid dynamics. Native props follow **measured** FK. Their observed
Gazebo poses drive the browser. Device power, material kind and contents are
injected known test states. After measured first transfer completion, the
trusted fixture records retained contents; this is not observed pouring.

## Verification

Headless, inside the existing Jazzy/Harmonic image:

```sh
python3 ros/gazebo/run_reference.py bin/haetae --secure-graph \
  --household-hazards --live-hold-seconds 0 --out /out/household-hazards
```

The report requires all six rejected cases, all six measured allowed controls,
accepted signed arm outcomes, measured tracking, the contents transfer and
retreat sequence and stale/unknown/missing
coverage controls. Missing/duplicate cases cannot pass. The legacy transport,
fault/independent-stop and intrusion results are reported separately.

General household hazard categories were discussed after viewing
[RoboHarm](https://github.com/robocurve/roboharm). No benchmark code, assets or
instruction strings are incorporated. This lab is original Apache-2.0 code.
