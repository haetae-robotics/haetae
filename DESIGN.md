# Design

## Source of truth
- Status: Active
- Last refreshed: 2026-10-01
- Primary product surfaces: live Gazebo reference at `sim/gazebo-live.html` and the scripted demo at `sim/index.html`.
- Evidence reviewed: `sim/DESIGN.md`, `sim/index.html`, `sim/gazebo-live.html`, `sim/gazebo-scene.js`, `sim/person-rig.js`, `ros/gazebo/scene_layout.py`, `ros/gazebo/rosbot_xl.urdf.xacro`, the Gazebo runbook, and Claude's source-based consultation in `.omx/artifacts/claude-design-20260930/` (not a rendered visual review).
- Scope: This file guides the live Gazebo reference. `sim/DESIGN.md` remains the detailed contract for the separate scripted demo.

## Brand
- Personality: calm, capable, and direct.
- Trust signals: label the actual Gazebo window, the telemetry reconstruction, and injected test reports accurately.
- Avoid: raw robotics jargon in the first screen, toy-like block geometry, and claims of physical robot safety.

## Product goals
- Goals: a first-time visitor understands how to start, sees a robot move and stop, and can tell what Haetae decided.
- Non-goals: a general-purpose Gazebo editor or hardware safety certification.
- Success signals: the first action is obvious without a manual; the stop is visible in both the scene and plain-language status.

## Personas and jobs
- Primary personas: founders, security engineers, and people without ROS experience.
- User jobs: launch one fixed safety scenario, recognize the moving robot and the stop, inspect the original simulator if desired.
- Key contexts of use: a laptop browser connected to local Docker Desktop or Docker Engine.

## Information architecture
- Primary navigation: one live run with an optional view switch; deeper measurements stay in a disclosure.
- Core routes/screens: `/` live run and `/gazebo-replay.html` recorded evidence.
- Content hierarchy: compact identity bar → large test bay with current verdict inspector → persistent bottom start/next control. Attack results are compact inspector rows, not a second dashboard below the scene.

## Design principles
- One prominent action starts the run only when the server is ready.
- The Docker live run advances one scene at a time. After measured stops or completed checks, keep the scene and explanation until the viewer presses “다음 단계”. Never delay safety commands to slow the presentation.
- Keep injected person reports visible throughout their stopped scene. After an explicit next action, walk the person out before clearing the report; announce both departure and clearance. Fade only at the far endpoint without changing safety-report timing.
- Person report coordinates must stand outside the base and arm sweep, including braking travel. Construct entry/target coordinates in the robot frame and sample the resulting world path on the Gazebo clock. Drive native person geometry and anchor the silhouette to its observed pose. The gate receives independent lidar surface measurements.
- People enter from the side at 0.24 m/s on the simulator clock. The base keeps receiving proposals until the existing person rule denies motion; freeze the person beside the stopped robot. The arm still cancels on the first human report at any distance, while the person continues approaching the stopped arm. Explain this difference in plain text. The walk, feet and arm swing are illustrative test-report presentation, kinematic native Gazebo geometry, not measured human skeletons or validated human physics.
- Show cause and effect in the scene; repeat it in text for accessibility.
- Present the reconstructed scene as measured data and the Gazebo tab as the original application.
- Preserve the existing simulator's green/amber/red verdict meaning, but use the colours only after corresponding events.

## Visual language
- Color: graphite frame `#111418`, panel `#181c21`, line `#2b323a`, text `#e7ebee`, secondary `#a6afb8`; light gray studio; cyan for active controls, amber for injected reports, red for actual block decisions, green for passed checks.
- Typography: local system Korean font stack; primary text 12–28 px, 28 px desktop verdict title; small secondary instrument metadata may use 10–11 px. Monospace is for numeric telemetry and equipment identifiers.
- Spacing/layout rhythm: 56 px top bar, large scene, 340 px inspector (304–360 px at responsive breakpoints), persistent bottom transport; 4 px spacing scale. The scene takes priority over marketing copy.
- Shape/radius/elevation: 8 px panels, 6 px controls, fine separators. Robot uses the manufacturer ROSbot XL and OpenMANIPULATOR-X geometry and material colours. The floor ring is a Haetae verdict overlay, not a robot indicator.
- Motion: robot pose follows Gazebo telemetry. Human gait follows reported travel distance, uses floor-planted feet, settles into a standing pose after stopping, and stops striding when telemetry stalls. Reduced motion keeps the marker position updates and suppresses the gait. No decorative movement is evidence of motor actuation.
- Imagery/iconography: pinned manufacturer URDF, DAE and STL assets converted into the browser link hierarchy. Four measured arm joints and four measured wheel joints; gripper held closed. No tested grasping or functional perception sensors. Adult-sized report silhouette stands on the ground, clear of the product arm reach.

## Components
- Existing components to reuse: live SSE feed, scene canvas, noVNC frame, verdict overlay, start endpoint.
- New/changed components: studio viewport, compact source switch, inset telemetry strip, current verdict inspector with attack rows, bottom transport and current stage. Live 3D is the default so report markers stay visible; label it as a reconstruction. Original Gazebo stays available and shares the mesh assets.
- Variants and states: connecting, ready, running, stopped, complete, error, and offline.
- Token/component ownership: this live page's local CSS; the separate scripted demo keeps its own styles.

## Accessibility
- Target standard: WCAG 2.2 AA where feasible.
- Keyboard/focus behavior: start, view switch, and details are native controls with visible focus.
- Contrast/readability: verdicts have words as well as colour; text remains legible over video.
- Screen-reader semantics: live state is announced in text; the scene is supplementary.
- Reduced motion and sensory considerations: no flashing effects; respect reduced motion for scene interpolation.

## Responsive behavior
- Supported breakpoints/devices: laptop, tablet, and phone widths down to 360 px.
- Layout adaptations: scene and status stack below 930 px; transport remains at the bottom with a full-width primary control on phones. No clipped horizontally scrolling toolbar.
- Touch/hover differences: controls remain at least 44 px high and work without hover.

## Interaction states
- Loading: explain that Docker and Gazebo are starting.
- Empty: show the robot as a static preview and mark live measurements as pending.
- Error: say that the simulator connection failed and where to check terminal output.
- Success: confirm completion only from the result event.
- Disabled: start action waits for the server's ready phase.
- Between scenes: the same primary control becomes “다음 단계”; the robot is already stopped, while physics, telemetry and safety monitoring continue. Refreshing restores the current waiting step.
- Offline/slow network: retain the last status but mark the feed as delayed or disconnected.
- Runner failure: show “시뮬레이션 중단”, disable progression, and retain the failure on refresh. A stale telemetry timer must not replace a disconnection or terminal error label.

## Content voice
- Tone: everyday Korean first, precise technical names in expandable details.
- Terminology: “로봇”, “위험 보고”, “해태가 정지 명령”, “실제 Gazebo 화면”.
- Microcopy rules: describe observed commands and feedback, never promise a physical safety outcome.

## Implementation constraints
- Framework/styling system: static HTML/CSS/JavaScript with vendored Three.js; Python SSE server.
- Design-token constraints: local page styles must not change the scripted demo's token system.
- Performance constraints: avoid external visual assets and keep the robot geometry lightweight.
- Compatibility constraints: Docker Gazebo GUI remains available through the local noVNC relay.
- Mesh constraints: `tools/build_product_visuals.py` derives browser meshes and joint hierarchy from `ros/gazebo/rosbot_xl.urdf.xacro` and the pinned, unmodified manufacturer sources in `ros/gazebo/vendor/`. Preserve geometry, inertia, physical limits and mounting pose. Record all Haetae controller/servo adapters separately. Validate source hashes, URDF kinematics, full arm reach clearance, and actual Gazebo controller behavior. The adult silhouette follows the observed native torso; person reports are independent lidar surface measurements. The old authored reference remains only for historical fixtures.
- Test/screenshot expectations: syntax and XML checks; verify a real Docker run and browser scene when the environment permits.

## Open questions
- Product model selected: ROSbot XL + OpenMANIPULATOR-X, standard wheels. Real hardware remains unselected; importing its model is not hardware verification.

## Simulator alpha operator flow
- Entry: `./haetae-demo start` prepares the background container and reports readiness; `doctor`, `status`, `restart`, `stop`, `verify` and `report` use plain explanations.
- One primary start/next action stays in the bottom transport. Report view/download are secondary inspector links enabled only after a terminal result or failure.
- A report separates passed, failed and unrun cases; partial checks never imply full success. Label this as simulator evaluation alpha, with unsigned local evidence and no physical protection/certification claim.
- Native transport closure is scoped to sandboxed non-root container roles; root/host/gateway actuation trust stays visible in the disclosure/report.
