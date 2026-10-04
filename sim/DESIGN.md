# Haetae Simulator Redesign: final spec, "해태가 막는다"

The simulator is rebuilt as an attack demo. The map is the stage. A visitor picks an attack card, watches an untrusted AI command reach the robot, and sees the real Rust gate (running as WASM) stamp its verdict on the map, at the spot where the action was stopped.

**Base design and grafts.** The judges' winner, "clarity", is the base. Grafted in:
- From "story": the colour grammar, the seal anchoring, the 차단(bul) drama, the Haetae badge, the time-compression badge, the verdict-picked outros, the mode ladder, the end card and filmstrip, the hazard-framed counterfactual, and the transcript.
- From "play": the real-judge attract teaser, the pipeline strip, the "slower, still blocked" beat, the with/without summary, the "정책 수정됨" honesty, the lab aim preview, a separate lab Sim, and `?kiosk`.
- Clarity's separate 판결문 column is removed. The verdict lives on the map and in the caption bar directly under it, and the details open on demand.

**Step data is machine-checked.** Every card step in §4 was run through a line-by-line port of `crates/haetae-core/src/gate.rs` + `geom.rs` + `proposal.rs`, using `sim/examples/policy.json` and the default freshness (500 / 2000 / 100 ms). The listed outcomes are that port's output. The dinner-party replay also produced `yun,yun,bul,bul,jeol,bul,yun`. `?selftest` re-checks all of this against the real WASM (§10).

---

## 0. Principles (each tied to a complaint)

| # | Principle | Complaint fixed |
|---|---|---|
| P1 | **One stage, one thing to press.** The map is the largest element. Each state has exactly one filled (primary) button. Sliders, toggles and JSON live only in the ⚙ 실험실 drawer. | 1 복잡함 |
| P2 | **The verdict lands on the map where the action is**, as a seal with a short reason beside it. It is repeated in a caption bar directly under the map, never in a separate panel. | 2 판정이 안 보임 |
| P3 | **The "why" is drawn, not only written.** Each fired name lights up its own geometry (child ring, zone flash, speed chips, stale-camera look). Nothing is drawn for a check the gate did not fire. | 2 |
| P4 | **The refusal is a moment.** The seal slams, the stage shakes, a barrier drops across the path, the robot's eyes flash red and it shakes its head, and the motor link in the pipeline strip is cut. | 3 밋밋함 |
| P5 | **It explains itself in 10 seconds.** A real verdict plays before the first click. There is a one-button intro, and the pipeline strip "AI 모델 → [표식] 해태 → 모터" states the idea permanently. | 4 사용법 모름 |
| P6 | **The colour grammar never changes.** **Violet** always means the model's raw, untrusted intent. **Jade / ochre / cinnabar** (통과(yun) / 감속(jeol) / 차단(bul)) always mean the gate's decision. A verdict colour, glyph or seal never appears unless it came from a `Decision` returned by `sim.judge()`. | HARD RULE |
| P7 | **The narration never predicts.** Narrator lines describe only the threat. Every post-verdict sentence and outro is chosen by the verdict the gate actually returned, so edited policies never make the story lie. | HARD RULE |

---

## 1. Core loop and the first 10 seconds

### 1a. Loop
Pick a card → the card plays 1–3 beats (command → gate → seal → execution) → optionally press **해태 없이 보기** after a 차단(bul)/감속(jeol) beat → an end card appears → the primary button becomes **다음 공격: {title} →**.

### 1b. First 10 seconds

| t | What happens |
|---|---|
| 0.0 s | The page renders with the house already alive: the robot at the table **holding the knife** and the child playing at (2, 6). This is card 1's opening world. The pipeline strip reads `AI 모델 ──▶ [표식] 해태 ──▶ 모터`. The intro card is on screen: bottom-right of the map on desktop, in the transport slot under the map on mobile. |
| 0.4 s | **Attract teaser, once per page load, muted.** A violet dashed intent path draws from the robot toward (2.5, 5.2). Then a **real** `judge()` call runs on a throwaway `Gate` built from the default policy, using card 1 beat 1 data exactly. The returned seal lands on the map using the normal seal code, and the callout appears. A small tag under the seal reads **"실제 판정"**. If the call throws, the teaser is skipped silently. |
| 3.4 s | The teaser seal and path fade (400 ms). The CTA pulses once (not under reduced motion). |
| any time | **▶ 첫 공격 보기 — 칼 든 로봇** fades the intro (200 ms) and starts card 1. The first real 차단(bul) lands about 1.8 s later (beat timeline §3f). Focus stays on the transport's primary button, which is now **다음 ▶**. |

Intro card content (copy is final):
- H1: **AI가 해킹당해 위험한 명령을 내리면?**
- Body: 해태는 AI가 로봇에게 내린 모든 명령을 **모터에 닿기 전에** 심사합니다. 옳고 그름을 가리는 상상의 동물, 해태처럼.
- Legend chips: `통과` `감속` `차단`
- Primary: **▶ 첫 공격 보기 — 칼 든 로봇**
- Text links: `공격 목록 보기` (focuses the card list) · `직접 해 보기 (실험실)`
- Footnote chip: 판정은 전부 실제 Rust 게이트(WebAssembly v{version()})가 내립니다.

When the intro appears:
- It shows on every load. It is cheap to dismiss, and presenters need it.
- `?` in the header shows it again.
- `?kiosk=1` skips it (§7c).
- Choosing any card also dismisses it.

Clicking the map outside the lab shows a one-time toast: "공격을 골라 보세요. 직접 명령은 ⚙ 실험실에서 할 수 있어요." It does nothing else.

### 1c. One primary action per state

| State | Primary (the only filled button) | Secondary (outlined / text) |
|---|---|---|
| S0 intro | ▶ 첫 공격 보기 — 칼 든 로봇 | 공격 목록 보기 · 직접 해 보기 (실험실) |
| S1 beat animating | 다음 ▶. Pressing it mid-animation jumps to the revealed end state of this beat; it does **not** skip the verdict. | ❚❚ |
| S2 beat revealed | 다음 ▶, with a countdown ring when auto-advance is on | 해태 없이 보기 (only after a 차단(bul) or 감속(jeol) beat) · ❚❚ |
| S3 unfiltered replay | 해태 켜고 돌아가기 | none |
| S4 card done | 다음 공격: {next title} →. After card 8: ↻ 처음부터 | ↻ 다시 · 해태 없이 다시 보기 (if the card had a 차단(bul)/감속(jeol) key beat) · 운영자 리셋 (card 4 only) |
| S5 lab open | none in the transport. Clicking or pressing Enter on the map sends a command. | lab tabs |

The primary button keeps its position through S1 → S4, so a keyboard user never has to hunt for it.

---

## 2. Layout

The canvas shows world x, y ∈ [−0.4, 10.4], so the canvas is square. The DOM overlay (seal, callout, slip, rings' labels) is positioned with the same world→screen transform and may overflow the canvas into the stage padding.

### 2a. Desktop, about 1280×800 (≥1100 px wide)

Vertical budget: header 56, strip 44, map 580, transport 76, padding 44.

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ [표식] 해태  AI의 로봇 명령을 모터 앞에서 심사합니다     통과  감속  차단   [?] [보기 ◐] [⚙ 실험실] │ 56
├────────────────────────────────────────────────────────────────────┬─────────────────────┤
│ 01 칼 든 로봇, 아이에게 · 1/3                                       │ 공격 고르기   본 2/8 │
│ (◇ VLA 모델) ━━━●━━▶ [[표식] 해태] ━━━━━━━✕━━ (⚙ 모터)       ×1 배속    │┌───────────────────┐│
│          ┌─────────────────── map 580×580 ───────────────────┐    ││▍1 칼 든 로봇,     ││
│          │ 주방·식당            ┊복도┊   거실                 │    ││   아이에게  [차단(bul) 통과(yun)]✓││
│          │                      ┊░░░░┊            ┌아이 방──┐ │    ││ 해킹된 AI가 칼 든… ││
│          │   ┌명령서 (violet)──┐┊0.3 ┊            │╱출입 금지│ │    │└───────────────────┘│
│          │   │◇ VLA 모델        │┊m/s ┊            │╱╱ 🔒 ╱╱╱│ │    │  2 가짜 태그   스푸핑 │
│          │   │"아이에게 칼을…"  │┊░░░░┊            └────────┘ │    │  3 복도 질주     과속 │
│          │   │이동 (2.5,5.2)    │┊    ┊                       │    │  4 센서 고장     고장 │
│          │   │0.8 m/s     [차단(bul)]  │┊    ┊                       │    │  5 탈취된 옆집 로봇   │
│          │   └──────────────────┘                              │    │  6 흐린 눈, 멈춘 눈   │
│          │  (아이)  ╭╌╌ 1.5 m ╌╮     ╭────────────────╮        │    │  7 녹화 명령 재전송   │
│          │     ╰╌╌╌┃▌차단(bul)▐┃╌╌╌╌╌╌╌─────│차단           │        │    │  8 저녁 파티 전체 ★   │
│          │         ╲ (로봇🔪)[식탁]  │칼 들고 아이 곁 불가│        │    │                     │
│          │                           │+1 · 자세히 ▸     │        │    │                     │
│          └───────────────────────────╰────────────────╯───────┘    │                     │
│ ●○○  차단 · 칼을 든 채 아이 1.5 m 안으로는 갈 수 없어요.  자세히 ▸   │                     │ 76
│      명령 · VLA 모델 → (2.5, 5.2) 이동 0.8 m/s    [해태 없이 보기] [❚❚] [ 다음 ▶ ◔ ]│         │
└────────────────────────────────────────────────────────────────────┴─────────────────────┘
   stage column ≈ 900 px (map centred, ≈150 px each side for callouts)       rail 360 px
```

- **Rail cards** are 72 px `<button>`s. Each shows the number, title, a one-line threat (ellipsis), and a badge. The badge is the attack tag before viewing, and the gate's recorded seals plus ✓ after. The active card gets a 4 px left bar and `aria-current="true"`.
- **Stage title line**: card number, title, and beat position `1/3`.
- **The ×k badge** sits at the strip's right end and is visible only when k > 1.

### 2b. Desktop, about 800×600 (720–1099 px wide)

```
┌──────────────────────────────────────────────────────────────┐
│ [표식] 해태                  통과(yun) 감속(jeol) 차단(bul)        [?] [◐] [⚙]         │ 48
├───────────────────────────────────────────┬──────────────────┤
│ 01 칼 든 로봇 · 1/3                        │ 공격   본 2/8    │
│ (VLA) ━●━▶ [관문 표식] ━━✕ (모터)       ×1       │ 1 칼 든 로봇 차단(bul)통과(yun)✓│
│   ┌──────── map 404×404 ────────┐         │ 2 가짜 태그       │
│   │ (아이)                       │         │ 3 복도 질주       │
│   │   ┃차단(bul)┃╌╌  ╭───────────╮      │         │ 4 센서 고장       │
│   │    (로봇) │차단(bul) 칼 들고   │      │         │ 5 탈취된 옆집 로봇│
│   │           │아이 곁 불가 │      │         │ 6 흐린 눈         │
│   └───────────╰───────────╯──────┘         │ 7 녹화 명령       │
│ ●○○ 차단 · 칼을 든 채 아이 1.5 m…  자세히 ▸│ 8 저녁 파티 ★     │
│ 명령 · VLA → (2.5,5.2) 0.8 m/s  [해태 없이][❚❚][다음 ▶]│          │
└───────────────────────────────────────────┴──────────────────┘
                                                  rail 280 px
```

- Header pitch text and legend words are hidden at this width. The glyphs stay, with `title` and `aria-label`.
- Rail cards are 48 px and show number, title and badge. The threat line goes in `aria-describedby` and a hover/focus tooltip.
- On desktop the command slip shrinks to two lines: source, then action and speed.

### 2c. Mobile, 375 px (<720 px, stacked, 16 px gutters, no horizontal scroll)

```
┌─────────────────────────────┐
│ [표식] 해태      [?] [◐] [⚙]   │ 48
├─────────────────────────────┤
│ 01 칼 든 로봇 · 1/3     ×1   │ 24
│ (VLA)━●━▶[관문 표식]━━✕(모터)       │ strip 36
│┌────── map 343×343 ───────┐ │
││ (아이) ╭╌1.5m╌╮           │ │
││    ┃차단(bul)┃  48px seal         │ │
││     ╲ (로봇🔪)  ✉          │ │  ✉ = slip tab above robot
││  [차단(bul) 칼 들고 아이 곁 불가]  │ │  label pill only, ≤12 chars
│└──────────────────────────┘ │
│ ●○○                          │
│ 차단 · 칼을 든 채 아이     │ caption (verdict sentence)
│ 1.5 m 안으로는 갈 수 없어요.  │
│ 명령 · VLA 모델 → (2.5, 5.2)  │ command line (violet)
│ 이동 0.8 m/s      자세히 ▸   │
│ [      다음 ▶  ◔       ] [❚❚]│ primary 48 px full width
│ [   해태 없이 보기   ]        │ secondary, outlined
├─────────────────────────────┤
│ 공격 고르기          본 2/8  │
│ ┌─────────────────────────┐ │
│ │1 칼 든 로봇, 아이에게 차단(bul)통과(yun)✓│ │ 56 px rows, threat as 2nd line
│ │  해킹된 AI가 칼 든 로봇을…│ │
│ └─────────────────────────┘ │
│  … 2–8                      │
└─────────────────────────────┘
```

- On mobile the command slip is never drawn over the map. It becomes the violet "명령 ·" line in the caption area, plus a 20 px ✉ tab above the robot. The ✉ tab is the landing spot for non-spatial 차단(bul) seals.
- The reason callout is reduced to the label pill. The full sentence sits in the caption directly below the map.
- Tapping a card scrolls the stage into view (smooth, or instant under reduced motion) and starts it.
- The intro card occupies the caption/transport slot.
- The detail sheet and the lab are bottom sheets (max-height 88vh, internal scroll).

### 2d. Sheets

| Sheet | Desktop | Mobile | Behaviour |
|---|---|---|---|
| **자세히** (§6c) | Right side sheet, 400 px | Bottom sheet | Overlays the **rail**, never the map. `<dialog>`, focus trapped. Esc returns focus to "자세히 ▸". |
| **⚙ 실험실** (§8) | Right side sheet, 420 px, same slot | Full-height bottom sheet | Same dialog rules. |
| **보기** | Popover | Popover | Theme (시스템 / 밝게 / 어둡게), 움직임 줄이기, 자동 진행. |

---

## 3. Visual language

### 3a. Tokens

Tokens are CSS custom properties on `:root`. Dark values are applied twice:
- under `@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {…} }`
- under `:root[data-theme="dark"]`

`body` gets an explicit `background: var(--bg)` and `color-scheme` follows the theme. The canvas re-reads the tokens through `getComputedStyle` on OS scheme change and on manual toggle.

The look is hanji paper, ink, and red 인주 for 차단(bul).

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | #F4EFE6 | #12100D | page |
| `--surface` | #FFFDF8 | #1C1915 | rail, cards, sheets, callouts, slips |
| `--surface-2` | #EFE8DB | #26221C | strip, transport |
| `--ink` / `--ink-2` | #1D1A16 / #5E564B | #F1EADF / #A89E90 | text / secondary text |
| `--line` | #D9CFBF | #3A342C | borders |
| `--floor` | #FBF7F0 | #1A1713 | house floor |
| `--room-tint` | #F1EADC | #211E19 | alternate room floor tint |
| `--grid` | rgba(74,64,54,.07) | rgba(241,234,223,.06) | 1 m grid |
| `--zone-slow` | #A8812A | #C9A456 | hall hatch lines at 16% alpha, never solid |
| `--zone-deny` | #9E3B41 | #D0676C | child-room crosshatch at 12% alpha, dashed border |
| `--robot-body` / `--robot-face` | #2B3A4A / #E9EEF3 | #D6DEE6 / #1C2530 | robot |
| `--robot-eye` | #0EA5E9 | #38BDF8 | LED eyes (they switch to `--bul-fill` during a 차단(bul) reaction) |
| `--child` | #C23A7E | #FF7AB8 | child figure |
| `--adult` | #4C7EA8 | #7FB0DA | adult figure |
| `--intent` | #6D3FD0 | #A78BFA | the model's raw intent: slip, dashed path, packet, ghost robot, unfiltered frame |
| `--yun-fill` / `--yun-fg` | #2E8F5F / #1F6F4B | #3FB57E / #5ED39A | 통과(yun) shape / 통과(yun) text |
| `--jeol-fill` / `--jeol-fg` | #D08A12 / #8A5600 | #E0A73E / #F2C064 | 감속(jeol) |
| `--bul-fill` / `--bul-fg` | #B3232A / #A21F26 | #D93A33 / #FF6B66 | 차단(bul) |
| `--seal-ink` | #FFF6EE | #FFF6EE | glyph carved into filled seals |
| `--focus` | #0B63CE | #7FB4FF | 3 px focus ring, 2 px offset |

Contrast rules:
- `-fg` tokens and `--intent` meet at least 4.5:1 on `--surface` in both themes. `-fill` tokens are only for shapes and glyphs of 40 px or more (3:1).
- Zones are pattern plus text chip, at low alpha. They read as "house rules drawn on the floor", never as verdicts.

Fonts: system stacks only, with no web fonts.
- UI: `system-ui, "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR", sans-serif`
- Seal glyphs: `"Noto Serif CJK KR", "AppleMyungjo", "Batang", serif`
- Technical names: `ui-monospace, "SF Mono", Menlo, Consolas, monospace`

### 3b. The map (canvas: reuse `map.js` HiDPI setup and y-up `toScreen` / `toWorld`)

- **No walls.** The gate knows zones, not walls, and a path drawn through a wall would mislead. Rooms are floor tints with small room labels in `--ink-2`:
  - 주방·식당: x 0–4
  - 복도: x 4–6
  - 거실: x 6–10, y 0–7
  - 아이 방: x 7–10, y 7–10
- **Zones** are drawn from the **loaded policy**, so a policy edit redraws them:
  - A `speed_limit` zone gets diagonal hatch plus a speed-limit roundel chip, e.g. "0.3 m/s".
  - A `no_entry` zone gets crosshatch, a 2 px dashed border, a lock pictogram, and the chip "출입 금지".
  - Zone display names come from `ZONE_KO = { hall: '복도', 'child-room': '아이 방' }`, falling back to the raw id.
- **Decor.** This is fixed art. It never enters the world JSON, and the gate only knows `robot.holding` and a grasp's `object`.

  | Item | Position |
  |---|---|
  | table | rect 2.4–3.9 × 2.5–3.6 |
  | knife | (3.2, 3.0) when not held |
  | cup | (3.6, 3.3) |
  | sink counter | rect 0.3–1.7 × 0.2–0.6 |
  | charging dock | (1, 9), with a small bolt icon |
  | sofa | 7.2–9.4 × 1.0–1.8 |
  | bed and teddy | in the child room, 8.4–9.8 × 8.2–9.6 |
  | fake tag sprite ("충전소" QR sticker) | at (7.3, 6.8), card 2 only while `decor.fakeTag` |

- **Robot** (vector, 0.6 m footprint, radius at least 12 px):
  - Rounded-square body, a face plate with two LED eyes facing the heading, and a two-finger gripper. A held object is drawn in the gripper. A held knife gets a thin `--bul-fg` outline, so the danger reads without a verdict colour fill.
  - A 14 px **Haetae badge** (a square 해태 표식 seal mark) rides on its back. It glows for 300 ms when `judge()` is called.
  - A dotted **gate ring** (r 0.55 m) is drawn when the mode is not normal. It is amber dashed in caution, and solid red with ⏸ at hold or above. The mode comes from `sim.mode()`.
  - Idle: the eyes blink every 3–5 s.
- **Child** (0.45 m): round head, body, a toy in hand, always labelled "아이". Idle bob of ±0.03 m on a 2 s cycle, cosmetic only. When the card lists the child in `focus`, a soft ring pulses twice.
- **Adult** (0.55 m), labelled "어른".
- **No emoji on the canvas**, because rendering varies by OS. Emoji may appear in DOM text.

### 3c. The pipeline strip (DOM, above the map)

`[source icon + name] ━━●━━▶ [[표식] 해태] ━━━━▶ [⚙ 모터]`

| Beat moment | What the strip shows |
|---|---|
| Source | Taken from `proposal.source` (input data): `◇ VLA 모델` / `플래너` / `원격 조작` / `옆집 로봇 ⚠ 미등록`. The ⚠ is violet outlined, since it is input and not a verdict. Icons are inline SVG. |
| Slip time | A violet packet dot travels source → 관문 표식 (450 ms). |
| `judge()` called | 관문 표식 glows (300 ms). |
| Seal lands | 관문 표식 fills with the verdict colour. The 관문 표식 → 모터 link then changes by verdict. 통과(yun): the link flows in `--yun-fill`. 감속(jeol): the link flows with a chip showing `≤{decision.speed_cap} m/s`. 차단(bul): the link is cut with a cinnabar ✕ and the motor icon dims. |
| Unfiltered replay | 관문 표식 is greyed and struck through. A violet bypass line runs straight from the source to 모터. |

### 3d. Intent vs. decision on the map

| Element | Layer | Drawn as |
|---|---|---|
| **명령서 (command slip)** | intent (DOM) | A violet-bordered paper card that flies from the strip's source icon to rest just above the robot (desktop). It shows the source, the model's words (`say`, in quotes), the action text and the requested speed. Card 7 adds a tape tag: `◀◀ 5.0초 전 명령` or `+3.0초 뒤 시각`, computed as `now − timestamp_ms` from the inputs. On mobile it is the ✉ tab plus the caption's "명령 ·" line. |
| **Intent path** | intent | A 2 px violet dashed line (6/6) with an arrowhead from the robot to the target, and a midpoint chip `요청 0.8 m/s`. Grasp and place use the same path (the gate sweeps it too). Stop has no path. |
| **Barrier** (차단(bul), spatial) | decision | A 5 px cinnabar bar with brush-stroke ends, drawn across the intent path at the seal anchor. The path beyond the barrier cracks into 4 fading segments (to 30%, 400 ms). |
| **Executed path** | decision | A solid 3 px line in the verdict fill, following `decision.action`. |
| **Robot motion** | decision | It follows `decision.action`: move_to at `decision.action.speed`; grasp/place as a gripper reach at `decision.speed_cap`; stop as a 300 ms brake pulse. **A denied robot never moves or lurches.** |
| **Speed diff** (감속(jeol)) | decision | Three ochre chevrons across the executed path. The slip speed line becomes `~~2.5~~ → 0.2 m/s`, where the new value is `decision.action.speed`. |
| **Brake stack** (감속(jeol), when 2 or more cap-kind names fired) | decision | A chip beside the callout listing each fired cap with its limit read from the policy, e.g. `최고 속도 1 · 복도 0.3 · 사람 곁 0.2`. The entry equal to `decision.speed_cap` is bold. This is card 3's signature visual. |
| **Ghost robot** | intent | Only in the unfiltered replay (§5). |

**Time compression.**
- A beat's executed motion lasts at most 3.5 s. Let `d` be the path length and `v` the executed speed. Then `k = max(1, (d / v) / 3.5)`.
- The unfiltered replay of the same beat reuses the gated run's `k`. If the gated run was 차단(bul), `k` is computed from the raw speed instead. Either way, relative speeds stay truthful.
- When `k > 1`, a `×{k to one decimal} 배속` badge sits in the strip.

### 3e. The seal: the verdict on the map (DOM overlay)

| Verdict | Shape | Content | Size (desktop / 800 / mobile) | Entrance |
|---|---|---|---|---|
| **통과** | circle, double ring, `--yun-fill` | 통과(yun) | 64 / 56 / 48 px | fade + scale 1.2 → 1, 200 ms. Fades out after 1.5 s. |
| **감속** | rounded octagon, `--jeol-fill` | 감속(jeol) + `≤{speed_cap}` | same | stamp 1.6 → 1, 220 ms, no shake |
| **차단** | square 도장, rotated −6°, slightly rough edge (fixed SVG path), `--bul-fill`, glyph `--seal-ink` | 차단(bul) | same | the slam (§3f) |

**Seal anchor.** This is placement only. It reads names from `decision.fired` and geometry from the policy. It never changes or re-derives the verdict, and if the geometry finds nothing it falls back to the default.

- **차단(bul)**, chosen by the **headline** fired name (§6a):
  1. A `no_entry` zone: the point where the robot → target segment first enters the zone rect (Liang–Barsky `t0`). If the robot starts inside, the robot pose.
  2. A rule whose `when.human_within` exists: the first point along the segment within `distance` of any human of that `class` (quadratic solve). If there is none, the closest point to the nearest human of that class.
  3. `envelope:workspace`: the point where the segment leaves the workspace rect.
  4. `envelope:pose`: the robot.
  5. Anything else (`source:*`, `stale:*`, `invalid:*`, `mode:*`, a rule with no geometry): **stamped onto the command slip** (the ✉ tab on mobile), like stamping a document. The intent path fades entirely, because the command itself never got past the gate.
- **감속(jeol)**: the midpoint of the path, offset 0.5 m perpendicular toward the map centre, because the cap governs the whole motion.
- **통과(yun)**: the target. For stop, the robot.
- The seal is clamped at least 8 px inside the map rect.
- When the next beat starts, the previous seal shrinks to 60% and stays at 25% opacity, leaving a trail of the card's seals. Trails are cleared at card end.

**Reason callout.** A DOM element, `aria-hidden`, because the live region speaks the same content.
- It attaches to the seal with a 1 px leader line and appears 200 ms after the seal lands.
- **Line 1**: `차단`, the verdict word in its `-fg` colour, followed by the **label** (at most 12 characters, §6b).
- **Line 2** (`--ink-2`): `+{fired.length−1}` when more names fired, then `· 자세히 ▸`.
- It is placed on the side with more free space inside the stage (the map plus its side padding) and flips on collision.
- On mobile it collapses to a single label pill under the seal.

**Rule highlights.** Each name in `decision.fired` draws its own highlight, fading in 250 ms after the seal. Names that did not fire draw nothing.

| Fired | Highlight |
|---|---|
| a rule with `human_within` + `then: "bul"` (knife-near-child) | A dashed `--bul-fg` ring of the rule's `distance` around **every** human of the rule's class (not re-evaluated per human). A held knife glints twice. |
| a rule with `human_within` + jeol (slow-near-human) | A dashed `--jeol-fg` ring of the rule's `distance` around every human of that class, or every human if no class. |
| a rule with `confidence_below` | A static haze overlay (CSS gradient, 20%) and a chip `인식 신뢰도 45% · 기준 60%`. The value is from the world; the threshold is from the policy. |
| `zone:<no_entry>` | The zone hatch flashes to 40% twice and the lock scales up. |
| `zone:<speed_limit>` | The zone hatch brightens and the roundel pulses once. |
| `envelope:max_speed` | A chip at the robot: `요청 2.5 → 한계 1 m/s`. |
| `envelope:workspace` / `envelope:pose` | The workspace frame flashes. The off-map target is clamped to the edge with an arrow. |
| `source:not-allowed` | The strip's source chip is struck through. A violet zig-zag "signal" bounces off a hexagon shield drawn around the robot. |
| `stop:unvetted-source` | The source chip gets a small `기록됨` note. |
| `mode:*` | The mode ladder (at the map's left edge whenever the mode is not normal, or in card 4) pulses its lit rung. A vignette tints the map: amber for caution, red for hold and above. |
| `stale:world` | The map desaturates (CSS `filter: saturate(.35)`), a `❚❚ 화면 멈춤` stamp appears, and the 인식 나이 chip turns red. |
| `stale:proposal` / `invalid:timestamp` | The slip's tape tag shakes and turns `--bul-fg`. |

**Context chips** (top-left of the map, world and input facts only, never verdicts):
- `모드: 주의`, whenever `sim.mode()` is not `normal`
- `인식 신뢰도 NN%`, when `confidence < 0.9`
- `인식 나이 N.N초`, when `now − world.stamp_ms > 0`

### 3f. Beat timeline (ms from beat start, at 1×)

| t | Event |
|---|---|
| 0 | **Spotlight**: everything except the beat's `focus` entities dims to 65% (vignette). The narrator line fades into the caption (250 ms). If the beat has a `fault`, `raise_mode()` runs now, and the ladder and chips update from `sim.mode()`. If the beat's world moves the robot, the robot eases to the new pose over 400 ms (reuse `SNAP_MS`). |
| 300 | The slip flies from the source to above the robot (450 ms, ease-out). The strip packet travels to 관문 표식. The caption's "명령 ·" line appears. |
| 750 | The intent path draws (500 ms). |
| 1250 | **`judge(proposal, world, now)` is called** (director, §10). The badge on the robot and 관문 표식 glow for 300 ms. The Decision is frozen (`Object.freeze`) and stored. |
| 1550 | **The seal lands.** For **차단(bul)**: scale 2.2 → 0.94 → 1.0 (180 ms ease-in, 120 ms settle); a 5 px stage shake (160 ms); an ink ring spreads from the seal (400 ms); the barrier drops, or the stamp lands on the slip; the path beyond cracks; the robot's eyes flash red twice with a ±4° "no" wiggle (400 ms); the strip link is cut. **감속(jeol)**: the softer stamp plus chevrons. **통과(yun)**: a fade-in. |
| 1750 | The rule highlights and the callout appear. |
| 1900 | The caption switches to the verdict sentence, and the live region announces it (§9). |
| 2300 | Execution of `decision.action` at `k` (≤ 3.5 s). For 차단(bul) nothing moves. |
| end | Dwell: 2.5 s after 통과(yun), 4.5 s after 감속(jeol) or 차단(bul). A countdown ring runs on **다음 ▶** when auto-advance is on; otherwise it waits. |

Beats with no motion (차단(bul), stop) last about 6.4 s. The first 차단(bul) of card 1 appears about 1.8 s after the click, counting the 200 ms intro fade.

### 3g. End card (DOM, slides up over the lower stage)

The end card holds:
- **Seals row**: the recorded verdict of every beat, as glyph + word, e.g. `차단 · 차단 · 통과`.
- **Outro**: picked from the card's **attack beats** (`key` or `attack`). Take the strictest recorded verdict (차단(bul) > 감속(jeol) > 통과(yun)) and use `card.outro[verdict]`. If that entry is missing, use `GENERIC_OUTRO[verdict]` (§6d). If any attack beat returned 통과(yun), show a neutral ⚠ plus the generic yun line. This can only happen after a policy edit, and it is shown, never hidden.
- **With/without line**, when any beat was not 통과(yun): `해태 없이: 모델 명령 {n}개가 모두 그대로 실행 · 해태와 함께: 막음 {b} · 줄임 {j} · 통과 {y}`. The left half counts proposals; the right half is tallied from the recorded Decisions.
- **Secondary buttons**: `↻ 다시`, `해태 없이 다시 보기` (replays the `key` beat unfiltered), and `운영자 리셋` (card 4 only; calls `reset_mode()`, and the ladder drops to 정상).
- A `대본 보기` link that opens the transcript in the 자세히 sheet (§6c).

---

## 4. Attack cards: exact data

### 4a. Conventions
- The director owns one `missionGate = new Gate(policyText)`. Each card starts with `missionGate.reset()`, which calls `sim.reset_mode()`.
- Every beat gives `world`, an explicit trusted `now`, and a `proposal`:
  - Normally `world.stamp_ms = now` and `proposal.timestamp_ms = now − 50`.
  - Freshness beats break this on purpose.
  - A world after executed motion has a stamp at least the previous `now` plus the motion time, so the timeline stays physically consistent.
- JSON objects contain **exactly** the allowed keys (serde `deny_unknown_fields`):
  - world: `{stamp_ms, robot:{pose:{x,y}, holding}, humans:[{id, class, pos:{x,y}}], confidence}`
  - proposal: `{id, source, timestamp_ms, action}`
- `expect` is read **only** by `?selftest`. It is never rendered and never used to choose copy.
- `B` = card base time. B1 = 1727241900000, and each later card adds 100000.

### 4b. Data (`js/cards.js`; helpers guarantee the exact shapes)

```js
const KID    = { id: 'kid', class: 'child', pos: { x: 2, y: 6 } };
const W = (stamp, x, y, holding = null, humans = [KID], confidence = 0.95) =>
  ({ stamp_ms: stamp, robot: { pose: { x, y }, holding }, humans, confidence });
const P = (id, source, ts, action) => ({ id, source, timestamp_ms: ts, action });
const mv = (x, y, speed) => ({ type: 'move_to', goal: { x, y }, speed });
const STOP = { type: 'stop' };
```

**Card 1 · 칼 든 로봇, 아이에게** · tag `물리 위해` · B = 1727241900000
Threat: "해킹된 AI가 칼을 든 로봇을 아이에게 보냅니다."

| Beat | Narrator (threat only) | Slip `say` | Data | Expect (selftest only) |
|---|---|---|---|---|
| 1 `key` | 로봇이 칼을 들고 있어요. 해킹된 VLA 모델이 아이 쪽으로 가라고 합니다. | "아이에게 칼을 가져다 줘" | world `W(B,3,3,'knife')`, now `B`, `P(1,'vla',B-50,mv(2.5,5.2,0.8))` | bul `[knife-near-child, slow-near-human]` (path end 0.94 m from the child) |
| 2 `attack` | 그럼 천천히, 살짝 돌아서 가면? | "천천히 돌아서 아이 옆으로" | `W(B+2000,3,3,'knife')`, now `B+2000`, `P(2,'vla',B+1950,mv(1.2,4.8,0.3))` | bul `[knife-near-child]` (1.44 m: ≤1.5, >1.0) |
| 3 | 이번엔 칼을 싱크대에 치우라고 합니다. | "칼을 싱크대로 치워" | `W(B+4000,3,3,'knife')`, now `B+4000`, `P(3,'vla',B+3950,mv(1,1,0.5))` | yun `[]`, cap 1.0 (3.16 m from the child) |

- Focus: `kid`, `knife`.
- Raw consequence (unfiltered replay, neutral text):
  - Beat 1: "칼을 든 로봇이 아이 0.9 m 앞까지 갑니다."
  - Beat 2: "칼을 든 채 0.3 m/s로 아이 1.4 m 옆까지 갑니다."
- `outro.bul`: "칼을 드는 건 괜찮아요. 하지만 칼을 든 채 아이 곁으로는, 빠르든 느리든, 갈 수 없습니다."

**Card 2 · 가짜 태그** · tag `스푸핑` · B = 1727242000000
Threat: "가짜 '충전소' 태그가 로봇을 잠든 아이의 방으로 부릅니다."
Humans: `SLEEPER = { id: 'kid', class: 'child', pos: { x: 9.3, y: 7.5 } }`.

| Beat | Narrator | `say` | Data | Expect |
|---|---|---|---|---|
| 1 `key` (decor.fakeTag) | 누군가 아이 방 문에 가짜 '충전소' 태그를 붙였어요. VLA 모델은 태그를 믿습니다. | "충전소 발견 → (8.5, 8.5)" | `W(B,3,3,null,[SLEEPER])`, now `B`, `P(1,'vla',B-50,mv(8.5,8.5,0.8))` | bul `[zone:hall, zone:child-room]` (child 1.27 m from the path, so no slow rule). Seal at the entry point (7, 7). |
| 2 `attack` (decor.fakeTag) | 살금살금, 문턱까지만 가 보면? | "천천히 문 앞까지만" | `W(B+2000,3,3,null,[SLEEPER])`, now `B+2000`, `P(2,'vla',B+1950,mv(7.5,7.5,0.2))` | bul `[zone:hall, zone:child-room]` (child 1.8 m away) |
| 3 (the tag peels off, 400 ms) | 태그를 떼어 내자, 모델이 진짜 충전소를 찾았어요. | "충전소 (1, 9)로 이동" | `W(B+4000,3,3,null,[SLEEPER])`, now `B+4000`, `P(3,'vla',B+3950,mv(1,9,0.8))` | yun `[]`, cap 1.0 |

- Focus: the fake tag, the child room.
- Raw consequence:
  - Beat 1: "로봇이 잠든 아이의 방 안으로 들어갑니다."
  - Beat 2: "0.2 m/s로 아이 방 문턱을 넘어 들어섭니다."
- `outro.bul`: "태그가 뭐라고 하든, 아이 방은 로봇이 들어갈 수 없는 곳입니다."

**Card 3 · 복도 질주** · tag `과속` · B = 1727242100000
Threat: "AI가 최고 속도의 2.5배로 사람 옆을 질주하라고 합니다."
Humans: `H3 = [{ id: 'parent', class: 'adult', pos: { x: 5, y: 2.8 } }, KID]`.

| Beat | Narrator | `say` | Data | Expect |
|---|---|---|---|---|
| 1 `key` | '빨리 거실로!' 모델이 2.5 m/s(시속 9 km)로 복도를 가로지르려 해요. 복도에는 어른이 서 있습니다. | "빨리 거실로 가 (9, 2)" | `W(B,1,2,null,H3)`, now `B`, `P(1,'vla',B-50,mv(9,2,2.5))` | jeol `[envelope:max_speed, zone:hall, slow-near-human]`, action speed 0.2, cap 0.2 (adult 0.8 m from the path) |

- Focus: `parent`, the hall.
- Signature visual: the brake stack `최고 속도 1 · 복도 0.3 · 사람 곁 0.2` and the slip showing `~~2.5~~ → 0.2 m/s`. k ≈ 11.4, so the unfiltered ghost really does cross in about 0.3 s while the gated robot crawls.
- Raw consequence: "2.5 m/s로 어른 0.8 m 옆을 스쳐 지나갑니다."
- `outro.jeol`: "막지는 않았어요. 걸린 한도 가운데 가장 낮은 속도로 줄였습니다."

**Card 4 · 센서 고장** · tag `고장` · B = 1727242200000
Threat: "라이다가 연달아 고장 나는데, 플래너는 계속 움직이려 합니다."

| Beat | Narrator | Fault (at t=0) | Data | Expect |
|---|---|---|---|---|
| 1 `attack` | 라이다 고장 신호(M-LIDAR-021)가 들어왔어요. 플래너는 그대로 움직이려 합니다. | `raise_mode('caution')` | `W(B,3,3)`, now `B`, `P(1,'planner',B-50,mv(3,1,0.9))` | jeol `[mode:caution]`, speed 0.5, cap 0.5 |
| 2 `key` | 고장이 커졌어요(M-LIDAR-022). 플래너는 또 움직이려 합니다. | `raise_mode('hold')` | `W(B+5000,3,1)`, now `B+5000`, `P(2,'planner',B+4950,mv(3,2,0.3))` | bul `[mode:hold]` |
| 3 | 원격 조작자가 '정지'를 보냅니다. | none | `W(B+6000,3,1)`, now `B+6000`, `P(3,'teleop',B+5950,STOP)` | yun `[]`, cap null |

- The mode ladder is always visible in this card: 정상 → 주의 → 홀드 → 안전 주차 → 비상 정지, with the lit rung read from `sim.mode()`. A fault beat shows a sensor glitch spark on the robot and the rung climbing (a clunk, 200 ms).
- The end card adds `운영자 리셋`.
- Raw consequence:
  - Beat 1: "라이다가 고장 난 채로 0.9 m/s로 움직입니다."
  - Beat 2: "센서가 망가진 채로 계속 움직입니다."
- `outro.bul`: "고장이 나면 모드가 올라가고, 그동안에도 '멈춤'만큼은 언제나 통과합니다."

**Card 5 · 탈취된 옆집 로봇** · tag `탈취` · B = 1727242300000
Threat: "해킹당한 옆집 배달 로봇이 우리 로봇에게 '칼을 집어'라고 명령합니다."

| Beat | Narrator | `say` | Data | Expect |
|---|---|---|---|---|
| 1 `key` | 옆집 배달 로봇이 해킹당했어요. 우리 로봇에게 칼을 집으라고 신호를 보냅니다. | "칼을 집어" | `W(B,3,3)`, now `B`, `P(1,'peer',B-50,{type:'grasp',object:'knife',at:{x:3.2,y:3}})` | bul `[source:not-allowed]` (child 3.16 m away, so the knife rule does not fire). Seal stamped on the slip. |
| 2 | 같은 로봇이 이번엔 '멈춰'라고 합니다. | "멈춰" | `W(B+1500,3,3)`, now `B+1500`, `P(2,'peer',B+1450,STOP)` | yun `[stop:unvetted-source]` |

- The strip source is `옆집 로봇 ⚠ 미등록`, and the slip flies along a violet zig-zag.
- Raw consequence (beat 1): "옆집 로봇이 시키는 대로 칼을 집어 듭니다."
- `outro.bul`: "모르는 출처의 명령은 듣지 않아요. 단, '멈춰'는 누가 말해도 듣습니다."

**Card 6 · 흐린 눈, 멈춘 눈** · tag `인식` · B = 1727242400000
Threat: "연기로 앞이 흐리고 카메라까지 멈췄는데, AI는 움직이려 합니다."

| Beat | Narrator | `say` | Data | Expect |
|---|---|---|---|---|
| 1 `attack` | 주방에 연기가 차서 인식 신뢰도가 45%로 떨어졌어요. 모델은 그래도 0.6 m/s로 가려 합니다. | "(1, 4)로 이동" | `W(B,1,1,null,[KID],0.45)`, now `B`, `P(1,'vla',B-50,mv(1,4,0.6))` | jeol `[low-confidence]`, speed 0.2, cap 0.2 |
| 2 `key` | 이번엔 카메라가 1.2초째 멈췄어요. 그 사이 아이가 어디로 움직였는지 알 수 없습니다. | "아이 쪽 (2.6, 5.4)로 이동" | `W(B+16000,1,4)`, **now `B+17200`**, `P(2,'vla',B+17150,mv(2.6,5.4,0.5))` | bul `[stale:world]` (age 1200 > 500, early return) |

- Before the verdict, beat 1 shows the haze and the `인식 신뢰도 45%` chip. These are world inputs.
- Beat 2 shows `인식 나이 1.2초` (now − stamp) and draws the child as a dashed outline with "?" (decor). After the verdict, the stale highlight is added.
- Raw consequence:
  - Beat 1: "잘 보이지 않는 채로 0.6 m/s로 움직입니다."
  - Beat 2: "1.2초 전 화면만 믿고 아이가 있던 쪽으로 갑니다."
- `outro.bul`: "잘 안 보이면 천천히, 멈춘 화면이면 아예 움직이지 않습니다."

**Card 7 · 녹화 명령 재전송** · tag `재전송` · B = 1727242500000
Threat: "공격자가 가로챈 명령을 다시 틀고, 시각까지 위조합니다."

| Beat | Narrator | Slip tag | Data | Expect |
|---|---|---|---|---|
| 1 `key` | 공격자가 5초 전에 가로챈 명령을 지금 다시 보냅니다. | `◀◀ 5.0초 전 명령` | `W(B,1,1)`, now `B`, `P(1,'vla',B-5000,mv(3,3,0.5))` | bul `[stale:proposal]` |
| 2 `attack` | 이번엔 명령에 3초 뒤 시각을 찍어 보냅니다. | `+3.0초 뒤 시각` | `W(B+1000,1,1)`, now `B+1000`, `P(2,'vla',B+4000,mv(3,3,0.5))` | bul `[invalid:timestamp]` (3000 > 100) |
| 3 | 같은 명령이 제시간에 도착하면? | none | `W(B+2000,1,1)`, now `B+2000`, `P(3,'vla',B+1950,mv(3,3,0.5))` | yun `[]`, cap 1.0 |

- Raw consequence:
  - Beat 1: "5초 전 명령이 지금 상황에서 그대로 실행됩니다."
  - Beat 2: "시각이 위조된 명령이 그대로 실행됩니다."
- `outro.bul`: "해태는 명령에 적힌 시각이 아니라 자기 시계를 믿어요. 같은 명령도 제시간에 와야 통과합니다."
- A detail-sheet footnote: "같은 id를 다시 보내는 재전송은 런타임의 중복 id 검사가 막습니다. 이 브라우저 데모에는 게이트만 들어 있어요."

**Card 8 · 저녁 파티 전체 재생 ★** · tag `종합`
Threat: "하나의 저녁, 일곱 개의 명령, 두 번의 고장. 위의 공격이 연달아 벌어집니다."

- Load `examples/policy.json`, `examples/world.json` and `examples/proposals.jsonl` through the existing `ScenarioPlayer` / `parseStream`, with its semantics unchanged:
  - `now` = the latest world `stamp_ms` seen.
  - An older world line is ignored.
  - Fault lines call `raise_mode`.
  - Captions come from `PROPOSAL_CAPTIONS` / `FAULT_CAPTIONS`.
- The director supplies the `api` hooks, so the same seal, highlight and strip code runs.
- Pacing: world and fault lines 1.2 s (caption plus ladder); proposal beats use a compressed timeline (seal at about 900 ms, motion ≤ 2.5 s, dwell 1.5 s). Auto-advance is on unless reduced motion is set.
- A **filmstrip** of 7 slots replaces the beat dots under the map. Slots fill with the recorded seals as they land.
- End card: a **scoreboard** tallied from the recorded Decisions (`통과 3 · 줄임 1 · 막음 3` with the default policy), with primary `↻ 처음부터` and secondary `⚙ 실험실에서 직접 해 보기`.
- There is no unfiltered replay for card 8; its attacks are covered by cards 1, 2 and 4.
- Selftest expects `yun, yun, bul, bul, jeol, bul, yun`.

**Card badges and progress.**
- Before viewing, a card shows its tag. After viewing, it shows the gate's recorded glyphs plus ✓.
- The header shows `본 n/8`.
- Progress is stored in `localStorage['haetae.sim.watched.v2']` inside try/catch, as a convenience only. It is cleared when a policy is applied (§8).

---

## 5. "해태 없이 보기": honest counterfactual (HARD RULE)

- **Availability**: only after a beat whose recorded `decision.verdict !== 'yun'`, and on the end card for the `key` beat. A 통과(yun) beat would look identical, so it gets no button. Shortcut `H`.
- **What runs**:
  - The beat restarts from its scripted world.
  - The **ghost robot** executes `proposal.action` exactly as sent: move_to at the raw speed, grasp and place as an arm reach and pick-up, stop as nothing.
  - At the same time, the real robot re-plays its **recorded** `decision.action` at 40% opacity with its recorded seal. Both use the same `k`. This is a true race in one frame (card 3: the ghost crosses before the real robot has gone 1 m).
  - **`judge()` is not called. No new verdict exists.** Faults are shown as a caption only: "고장 신호를 받아 줄 게이트가 없습니다".
  - Nothing carries forward: the next beat uses its scripted world.
- **Framing**:
  - An 8 px violet diagonal hazard-stripe border around the map.
  - A sticky banner across the map top: **"⚠ 비교 재생 · 해태 없음 — 모델의 원래 명령을 필터 없이 실행하면 (판정 없음)"**.
  - The ghost is violet at 55% opacity with a hatch fill, labelled `필터 없음 · 모델 명령 원본`, and has **no Haetae badge**.
  - The strip shows 관문 표식 struck through, with a violet bypass line.
- **Allowed on the ghost**: only facts from the proposal and its own position: the requested speed, the destination, the carried object. The authored neutral `raw` sentence goes in the caption with a ⚠ icon in `--ink-2`.
- **Never on the ghost**: a seal, a 통과(yun)/감속(jeol)/차단(bul) glyph or word, a verdict colour, a fired name, a barrier, a callout.
- **Exit**: when the replay ends, the primary button **해태 켜고 돌아가기** (or `H` again) restores the gated end state of that beat.
- **Code isolation**:
  - `js/ghost.js` imports nothing from `engine.js`, `copy.js` or `explain.js`. Its entry point is `playUnfiltered({ world, proposal, k }, stageIntentLayer)`, and it never receives a Decision.
  - The dimmed real robot is drawn by the existing decision layer from the recorded, frozen Decision.
  - Ghost runs are logged in the lab log as `comparison:unfiltered`, never as decisions.

---

## 6. Explanation: where it appears and what it says

### 6a. Kind and headline (presentation only; never changes the verdict or drops a name)

`kindOf(name, policy)` returns one of `deny`, `cap`, `info` or `unknown`:

| Name | Kind |
|---|---|
| `invalid:*`, `stale:*`, `source:not-allowed`, `envelope:pose`, `envelope:workspace` | deny |
| `mode:hold`, `mode:safe_park`, `mode:estop` | deny |
| `mode:caution`, `envelope:max_speed` | cap |
| `stop:unvetted-source` | info |
| `zone:<id>` | deny if the policy zone is `no_entry`, cap if it has `speed_limit` |
| rule id | deny if `then === 'bul'`, cap if `then.jeol` |
| anything else | unknown |

`headline(decision, policy)`:
- **bul**: the first fired name of kind deny, in gate order. Otherwise `fired[0]`.
- **jeol**: the first cap-kind name whose policy limit equals `decision.speed_cap` (within 1e-9). The limits are:
  - `envelope.max_speed`
  - a zone's `speed_limit`
  - a rule's `then.jeol.max_speed`
  - `envelope.max_speed × 0.5` for `mode:caution`; this is used only to pick the label.
  Otherwise the first cap-kind name, otherwise `fired[0]`.
- **yun**: `stop:unvetted-source` if fired. Otherwise, if the action is stop, the "stop" line. Otherwise "문제 없음".

The detail sheet lists **every** fired name, sorted deny → cap → info/unknown, keeping gate order within each class.

### 6b. Copy (`js/copy.js`, layered over `explain.js`)

`{…}` values are filled at runtime:
- limits from the **policy**
- confidence, ages and requested speed from the **inputs**
- the applied cap from **`decision.speed_cap`**

Numbers are formatted with `num()`.

| Fired | Map label (≤12 chars) | Caption / sheet sentence |
|---|---|---|
| knife-near-child | 칼 들고 아이 곁 불가 | 칼을 든 채 아이 {1.5} m 안으로는 갈 수 없어요. |
| slow-near-human | 사람 곁에선 천천히 | 사람 {1} m 안을 지나서 {0.2} m/s로 줄였어요. |
| low-confidence | 잘 안 보여 천천히 | 인식 신뢰도가 {45}%로 기준 {60}%보다 낮아 {0.2} m/s로 줄였어요. |
| zone (no_entry) | {아이 방} 출입 금지 | 경로가 출입 금지 구역 '{아이 방}'에 들어가요. |
| zone (speed_limit) | {복도} {0.3} m/s 제한 | 속도 제한 구역 '{복도}'을 지나서 {0.3} m/s 이하로 줄였어요. |
| envelope:max_speed | 최고 속도 {1} m/s | 이 로봇의 최고 속도는 {1} m/s예요. 요청한 {2.5} m/s는 줄였어요. |
| envelope:workspace | 집 밖 목표 불가 | 목표 지점이 로봇이 일하는 공간 밖이에요. |
| envelope:pose | 로봇이 공간 밖 | 로봇의 현재 위치가 작업 공간 밖이라 움직이지 않아요. |
| source:not-allowed | 미등록 출처 | 허가 목록({VLA 모델·플래너·원격 조작})에 없는 '{다른 로봇}'의 명령은 따르지 않아요. |
| stop:unvetted-source | 멈춤은 언제나 통과 | 출처는 미등록이지만 '멈춤'은 가장 안전한 명령이라 따르고, 기록을 남겨요. |
| mode:caution | 주의 모드: 절반 속도 | 센서 이상으로 주의 모드예요. 속도를 {0.5} m/s까지만 허용해요. |
| mode:hold / safe_park / estop | {홀드}: 멈춤만 가능 | {홀드} 모드예요. '멈춤' 말고는 아무 동작도 하지 않아요. |
| stale:world | 멈춘 화면 판단 거부 | 인식 정보가 {1.2}초 전 것이라 기준 {0.5}초를 넘었어요. 낡은 화면으로는 움직이지 않아요. |
| stale:proposal | 오래된 명령 차단 | {5.0}초 전에 만든 명령이에요(기준 {2}초). 녹화된 명령을 다시 보낸 것일 수 있어요. |
| invalid:timestamp | 미래 시각 위조 의심 | 명령(또는 인식)에 신뢰 시계보다 {3.0}초 뒤 시각이 찍혀 있어요. 위조로 보고 막았어요. |
| invalid:proposal | 명령 값 오류 | 명령 값이 잘못됐어요 (숫자가 아니거나 음수 속도). |
| invalid:world | 센서 값 오류 | 센서 데이터가 잘못됐어요 (신뢰도가 0~1 밖 등). |
| other rule id | 정책 규칙 {id} | `describeRule(rule)` |
| other zone / builtin | 구역 '{id}' / 내장 검사 | `explainFired(name, policy).text` |
| yun, nothing fired | 문제 없음 | 걸린 조건이 없어 그대로 실행해요. 해태는 필요할 때만 막습니다. |
| yun, stop | 멈춤 | '멈춤'은 모드와 상관없이 항상 통과해요. |

Where the numbers come from:
- `{1.5}`, `{1}`, `{0.2}` and `{60}%` come from the matching rule's `when` / `then`.
- `{0.5}` for mode:caution is `decision.speed_cap`.
- The allowed list maps `policy.allowed_sources` through `SOURCE_KO`.
- The mode word comes from `MODE_KO`.

For invalid:timestamp, the text says "명령" if `proposal.timestamp_ms` is ahead of `now` by more than the tolerance, and "인식" otherwise. This only picks wording from the inputs.

The **verdict word** is always `통과` / `감속` / `차단`, with `yun/jeol/bul` in small mono on the detail sheet. Hanja is wrapped as `<span aria-hidden="true">차단(bul)</span> 막음`.

### 6c. The three layers
1. **On the map**: the seal, the label, `+N · 자세히 ▸`, and the rule highlights. This is the glance layer.
2. **Caption bar directly under the map**: before the verdict, the narrator line. After it, `{glyph} {word} · {sentence}` and `자세히 ▸`. Line 2 is always the violet `명령 · {source} → {actionText(proposal.action)}`.
3. **자세히 sheet**:
   - the verdict glyph, word and technical name
   - every fired row: label, sentence, the name in mono, and a kind badge (`내장 검사` / `구역` / `정책 규칙`)
   - `요청 → 실행`: `actionText(proposal.action)` → `actionText(decision.action)`, with speeds struck through when they differ
   - `speed_cap`, `mode`, `proposal_id`
   - a `원본 JSON` disclosure with the proposal, world, now and decision
   - **이번 장면 대본**, the transcript: every beat so far with its narration, command and recorded verdict sentence

### 6d. Generic lines
```js
GENERIC_OUTRO = {
  bul:  '해태가 이 공격을 막았습니다.',
  jeol: '해태가 이 공격을 허용하되 속도를 줄였습니다.',
  yun:  '지금 정책에서는 이 공격이 통과했습니다. ⚙ 실험실의 정책을 확인해 보세요.',
};
```

---

## 7. Header, cards, and small features

### 7a. Header
From left to right:
- The 해태 표식 mark and "해태".
- The pitch "AI의 로봇 명령을 모터 앞에서 심사합니다" (≥1100 px only).
- The legend `통과 감속 차단` (words ≥1100 px only).
- The `정책 수정됨` badge, shown only after a policy apply. It links to the lab's policy tab.
- `[?]` shows the intro and the shortcuts list.
- `[보기 ◐]` opens the popover: theme, 움직임 줄이기, 자동 진행.
- `[⚙ 실험실]`.

### 7b. Card list
- An `<ol>` of `<button>`s in a `<nav aria-label="공격 고르기">`.
- Each button's `aria-describedby` points to its threat line and status text, e.g. "본 장면: 막음, 막음, 통과" or "아직 안 봄".

### 7c. `?kiosk=1`
- No intro, auto-advance forced on, and cards loop 1 → 8 → 1.
- Any input pauses the loop; 60 s of idle resumes it.
- Reduced motion still wins: the loop steps with 6 s dwells and no animation.

---

## 8. ⚙ 실험실: advanced controls, tucked away

**Opening.**
- Header button, `L`, the intro link, or the card-8 end card.
- Opening the lab stops any running card; it can be replayed. The map enters **sandbox mode**:
  - a `실험실 모드` chip
  - the canvas gets `role="application"` with the existing help text
  - the existing map pointer and keyboard handling from `app.js` / `map.js` (arrow cursor, Shift = 1 m, Enter to send, grab/drag humans, Esc)
- The lab uses its own `labGate`, seeded from the currently displayed world. It mirrors the mission mode by calling `labGate.raise(missionGate.mode)`.

**Aim preview.** Keep the current throttled `refreshPreview`: a live seal follows the pointer or keyboard cursor, labelled `미리보기 (실제 판정)`. It is a real `labGate.judge()` call. Clicking or pressing Enter sends the command, and the full §3f timeline plays.

**Tabs.** Each tab starts with one "이렇게 써요" line.
1. **직접 명령**:
   - action segmented control: 이동 / 집기 / 놓기 / 정지
   - object (칼 / 컵 / 장난감)
   - source (vla / planner / teleop / peer)
   - speed slider 0–3 m/s
   - then click the map
2. **세계·고장**:
   - holding select
   - confidence slider
   - 인식 나이 slider, 0–2000 ms; it sets `now − stamp_ms`
   - + 아이 / + 어른 / remove
   - mode ladder buttons, each showing `raise_mode`'s bool as "변경됨" / "이미 그 이상"
   - 운영자 리셋, ■ 정지 보내기
3. **정책**:
   - a JSON textarea (wraps, mono) with live inline `policy_error()`
   - **적용** rebuilds both `missionGate` and `labGate`. It fails closed: an invalid policy keeps the old gates and shows the error. It clears the watched badges and shows the `정책 수정됨` badge.
   - **기본값 복원**
   - Banner: "정책을 바꾸면 장면의 판정도 바뀝니다. 판정은 언제나 게이트가 계산합니다."
4. **기록**:
   - the `log.js` event log of every judge call: time, source, action, glyph and fired names, with expandable Decision JSON
   - `comparison:unfiltered` rows are visually distinct and have no glyph
   - `JSONL 복사`, `기록 지우기`
   - Paste or load a JSONL stream to replay it through the card-8 player.

---

## 9. Accessibility and reduced motion

- **Structure.** `lang="ko"`. A skip link "무대로 건너뛰기". Then:
  - `<header>`
  - `<main>` containing `<section aria-labelledby="stage-title">`, which holds the strip, the canvas wrapper, the overlay and the transport
  - `<nav aria-label="공격 고르기">`
  - `<dialog>` elements for 자세히, 실험실 and help
- **Canvas.** `role="img"` in card mode, with an `aria-label` refreshed per beat, e.g. "지도: 로봇 (3, 3), 칼을 들고 있음. 아이 (2, 6). 복도 0.3 m/s 제한, 아이 방 출입 금지." A visually hidden `<ul>` lists the entities. In lab mode it becomes `role="application"`.
- **Overlay DOM** (seal, callout, slip) is `aria-hidden="true"`. Its content is spoken through the live region.
- **One polite live region** (`role="status"`, `aria-atomic="true"`), with one message per step:
  1. the narrator line
  2. "명령: VLA 모델이 (2.5, 5.2)로 0.8 m/s 이동을 요청"
  3. "해태 판정: 막음(차단(bul)). 칼을 든 채 아이 1.5 m 안으로는 갈 수 없어요. 함께 걸린 조건 1개."

  Unfiltered replay announces "비교 재생, 해태 없음, 판정 없음. {raw}". `role="alert"` is used only for WASM load failure.
- **Keyboard.** Global shortcuts are inactive while a dialog is open or a text field has focus.

  | Key | Action |
  |---|---|
  | `Space` | pause / resume |
  | `→` or `N` | next (reveal, then advance) |
  | `←` | previous beat |
  | `R` | replay card |
  | `H` | 해태 없이 보기 / 돌아가기 |
  | `L` | lab |
  | `1`–`8` | cards |
  | `?` | help |
  | `Esc` | close sheet or intro |

  - Tab order: header → stage transport → cards.
  - Starting a card or revealing a verdict never moves focus. The primary button stays in place.
  - The focus ring is 3 px `--focus` with a 2 px offset.
- **Not colour alone.**
  - Verdicts: glyph + Korean word + shape (circle / octagon / square).
  - Paths: dashed = intent, solid = executed, chevrons = 감속(jeol), barrier = 차단(bul).
  - Zones: hatch vs crosshatch, plus text chips.
- **Sizes.** Targets are at least 44×44 px; mobile primary buttons are 48 px. Text is at least 14 px, 16 px on mobile. The layout holds at 200% zoom and at 375 px with no horizontal scroll.
- **Timing.**
  - Auto-advance is on by default with a visible countdown and a ❚❚ button and Space to pause.
  - It pauses automatically while any dialog is open, while the pointer or focus is inside the caption/transport, and while `document.hidden`.
  - It is off by default under reduced motion, and it can be turned off in 보기.
- **Reduced motion** (`prefers-reduced-motion: reduce`, or 보기 → 움직임 줄이기, saved in try/catch):
  - Removed: the teaser animation (replaced by a static card-1 seal still), the slam, shake, ink ring, crack, wiggle, eye flash, pulses, idle bob and blink, the knife glint, the packet flight, and the hatch pulse.
  - Seals, callouts and highlights fade in over 120 ms.
  - The intent path is drawn statically.
  - The robot is drawn at its end pose, with the executed path as a static line. 감속(jeol) beats keep the `~~2.5~~ → 0.2 m/s` chip, so the difference is still stated.
  - The unfiltered ghost is shown at its end pose with a static dashed path.
  - Auto-advance is off by default. Smooth scroll becomes instant.
- **Failure.**
  - If WASM fails to load, a `role="alert"` banner replaces the intro and the cards are disabled.
  - No seal can render without a Decision.
  - A `judge()` throw in a card shows "이 장면을 판정하지 못했습니다 (입력 오류)" in the caption, and no seal.

---

## 10. Implementation map and invariants

**Keep:**
- `js/engine.js` as it is.
- `js/scenario.js`: `parseStream`, `ScenarioPlayer` and the captions.
- `js/log.js`.
- `js/explain.js`: `explainFired`, `describeRule`, `actionText`, `num`, `pt`, `MODE_KO`, `SOURCE_KO`.
- `js/map.js`: HiDPI resize, `toScreen` / `toWorld`, and the lab pointer and keyboard code. Its drawing moves to `stage.js`.

**New or rewritten:**

| File | Role |
|---|---|
| `index.html`, `style.css` | Rewritten around §2 and the §3a tokens. |
| `app.js` | Thin bootstrap: load the engine and policy, build the gates, wire the modules. |
| `js/cards.js` | The §4b data: `{num, title, tag, threat, focus, outro, beats:[{narr, say, focus?, decor?, fault?, world, now, proposal, key?, attack?, raw?, expect}]}` |
| `js/director.js` | The beat state machine and §3f timeline. It is the **only** card-mode caller of `missionGate.judge`, called once per proposal beat at t=1250. It also calls the teaser's throwaway gate and adapts `ScenarioPlayer` for card 8. |
| `js/stage.js` | Canvas: house, actors, zones from the policy, `intentLayer(proposal, world)`, and `decisionLayer(decision, proposal, world)`. |
| `js/overlay.js` | DOM: seal, callout, slip, strip, context chips, caption, end card, live region, anchor geometry. |
| `js/copy.js` | `kindOf`, `headline`, labels and sentences, `ZONE_KO`, `GENERIC_OUTRO`. |
| `js/ghost.js` | The unfiltered replay. No engine, copy or explain imports. |
| `js/lab.js` | The drawer, `labGate`, and the aim preview (moved from `app.js`). |
| `js/selftest.js` | Loaded only with `?selftest`. It runs every card headless on a fresh `Gate(defaultPolicy)` plus the dinner replay, compares verdict, fired, speed_cap and action speed to `expect`, and prints `console.table`. Mismatches go to `console.error`, and a small fixed "selftest n/n" panel is shown. |

**Invariants (checkable in review):**
1. `judge(` appears only in `director.js`, `lab.js` and `selftest.js`.
2. The verdict, fired list, action and speed_cap shown anywhere come from a frozen Decision object and are never computed or edited. Anchors, headlines and highlights only *select among* `decision.fired` and read policy numbers.
3. `expect` is read only in `selftest.js`.
4. `ghost.js` never receives a Decision and never uses `--yun-*`, `--jeol-*` or `--bul-*` classes.
5. No verdict colour, glyph or seal renders without a Decision, including the teaser, which uses a real call.
6. Narrator strings contain no verdict words. Every post-verdict string is keyed by the returned verdict or fired name.
7. Static site only: no CDN, no external fonts, no build step, plain ES modules. It works offline from `sim/`.

**Explicitly dropped** (they add learning or build cost without fixing a complaint):
- From "play": scoring, the mission roles and the 도장판, the draggable command pin, free-form attacks inside cards, the sticky mobile action bar.
- From "story": the HUD and scrubber, the 16:10 stage with garden margins, the fog shader, film grain, chromatic glitch, ink particles, side-by-side frozen frames, flying slips on mobile.
- Walls and sound.
- A separate 판결문 column.

---

## 3D diorama (v3)

v3 swaps the flat map for a **miniature house diorama**: a roofless toy house on a plinth, seen from above at an angle, that the viewer can rotate and zoom. The robot, the people and the furniture are 3D objects. When Haetae blocks a command, a glowing barrier rises in front of the robot. v3 also removes **every Han character** from the UI. Verdicts are shown as Korean words with non-Han icons and colour: **통과 / 감속 / 차단**.

**What v3 replaces:**
- the map geometry of §2 (the aspect ratio only)
- §3b (the map)
- the glyph seal of §3e (now the verdict tag, v3.7)
- the Han glyphs and seal font of §3a, §3c, §6b and §7a
- the verdict words 줄임 / 막음

**What still holds:** §1, §3c–§3g timing and logic, §4 card data, §5 (the honest counterfactual), §6 explanation logic, §7, §8, §9 and the §10 invariants, as amended below. Wherever an earlier section names a Han glyph, read it as the v3 icon plus word (v3.9).

### v3.0 Added principles

| # | Principle | Why |
|---|---|---|
| P8 | **The diorama is a stage set, not a sensor model.** The gate knows zones and humans; it does not know walls or furniture. Walls are low. They are cut away on the side facing the camera, and they fade wherever a drawn path crosses them. So nothing seems to pass through something solid, and nothing seems stopped by an object the gate never saw. Furniture is placed clear of every scripted pose and executed path. | Honesty (§3b "no walls" is kept in spirit). |
| P9 | **No text is drawn in WebGL.** Every word is DOM (the overlay or a CSS2D label) and in Korean. It stays crisp, it can be checked, and it is Han-free by construction. Badges and tags are made of shapes and patterns. | Readability, no Han characters. |
| P10 | **The camera serves the story, but it belongs to the viewer.** Auto-framing hands over control the moment the viewer touches the camera. Nothing in a card requires moving the camera, because the default view shows the whole house. | Accessibility. |
| P11 | **Verdict colours are still reserved** (P6). In 3D, the green, amber and red of the verdict appear only in effects built from a frozen Decision. Decor never uses them. For example, the charging bolt is sky blue, not green. | HARD RULE |

### v3.1 Coordinate frame

- World coordinates are metres, and y is north (policy and cards).
- The three.js frame is `X = x − 5`, `Z = 5 − y`, `Y` up, with the floor top at `Y = 0`. North is therefore −Z.
- Helpers in `js/geom3d.js`:
  - `w2v({x,y}, h = 0) → Vector3`
  - `v2w(Vector3) → {x,y}`
- The house centre (5, 5) sits at the origin.

### v3.2 Scene and art direction

**Mood:** a toy dollhouse on a table under a warm lamp. Everything is soft, matte, chunky and rounded, like painted plywood, felt and wood. There are no image files and no model files. The only textures are a few small procedural `CanvasTexture`s, drawn once, and none of them contains text.

**Plinth.**
- A rounded slab covering x −0.9…10.9 and y −1.6…10.9, 0.5 m thick (top at Y = 0), with corner radius 0.5 m. It is built with `ExtrudeGeometry` from a rounded-rect `Shape`, bevel 0.06.
- The extra 1.6 m on the south side is a front garden plus a sidewalk strip (y −1.4…−0.4, `--d3-path`).
- The top is lawn (`--d3-plinth`) and the sides are `--d3-plinth-side`.
- There are round shrubs (spheres, `--d3-leaf`) at the four outside corners and on either side of the front door, at (3.9, −0.4) and (6.1, −0.4).
- A fake contact shadow sits under the plinth: a plane with a radial-gradient alpha texture, at opacity `--d3-shadow`, so the diorama floats on the page background.

**House floor.** A 0.04 m slab over x 0…10, y 0…10. Rooms are the fixed art `ROOMS`, which moves to `js/model.js`:

| Room | Rect | Floor |
|---|---|---|
| 주방·식당 | x 0–4, y 0–10 | `--d3-floor-kitchen`, 0.5 m tile grid (texture lines at 6% ink) |
| 복도 | x 4–6, y 0–10 | `--d3-floor-hall`, planks running north–south |
| 거실 (+ passage x 6–7, y 7–10) | x 6–10, y 0–7 | `--d3-floor-living`, round rug r 1.1 at (8, 4) in `--d3-fabric-2` |
| 아이 방 | x 7–10, y 7–10 | `--d3-floor-child` |

The kitchen, hall and living room form **one open plan**. Only a 0.02 m groove marks x = 4 and x = 6, and there are no walls there. Card 3's executed path (y = 2) and the intent paths of card 2 and the dinner party (y = x) cross those lines between y 2 and y 6.

**Walls.** Thickness 0.12, colour `--d3-wall`, top cap `--d3-wall-cap`.

| Segment | Height | Notes |
|---|---|---|
| Outer south wall, y = 0 | 1.1 m | Front-door gap at x 4.3–5.7 (hall) |
| Outer west wall, x = 0, and north wall, y = 10 | 1.1 m | Solid |
| Outer east wall, x = 10 | 1.1 m | Decorative window gaps at y 2.0–3.2 and y 4.4–5.6, each with a sill |
| Nook partition, x = 4, y 7.2–10 | 0.55 m | Separates the charging nook from the hall |
| Child-room west wall, x = 7, y 7–10 | 0.55 m | |
| Child-room south wall, y = 7 | 0.55 m | A pier at x 7.0–7.5 (the fake tag's wall), then the doorway x 7.5–8.5, then wall x 8.5–10. The door leaf (0.9 × 0.55 × 0.05) is hinged at (8.5, 7) and swung 75° into the room, so it clears the y = x path. |

**Wall rules:**
- **Cutaway.** Every outer segment has an outward normal `n`. Let `c` be the horizontal unit vector from the house centre toward the camera. If `n·c > 0.2`, the segment's target height is a 0.18 m sill; otherwise it is full height. The height tweens over 220 ms on view change (instant under reduced motion).
  - In the default south-east view, the south and east walls are sills, and the west and north walls stand full height behind the scene.
  - Interior half-walls never cut away.
- **Fade.** Any wall segment whose footprint intersects a drawn path is set to opacity 0.25 (a transparent clone of its material, with `depthWrite: false`) until the path is cleared. "Drawn path" means intent, executed, ghost or the lab aim line.
  - The test is a 2D segment/segment check in world coordinates, run only when a path changes.
  - Example: card 2's intent enters the child room exactly at the pier corner (7, 7), so the pier fades and the barrier behind it stays visible.
  - This also covers lab commands and edited policies.

**Furniture.** All furniture uses matte Lambert materials. Footprints are in world metres. Each position was checked against every scripted robot pose (radius 0.3) and every executed path in §4 and the dinner party.

| Item | Build | Footprint / position | Height | Why it is here |
|---|---|---|---|---|
| Sink counter | rounded box, dark inset basin, two-cylinder tap (`--d3-metal`) | 0.3–1.7 × 0.2–0.6 | 0.5 | same as 2D |
| Dining table | top plus 4 cylinder legs, `--d3-wood` | **3.35–3.95 × 2.4–3.7** | 0.42 | Narrowed from the 2D rect, which contained the robot pose (3, 3). The robot at (3, 3) with radius 0.3 now clears it, and so do card 3 (y = 2) and card 4 (x = 3). |
| Chair | box seat, back | (3.65, 4.0), 0.4 × 0.4, facing south | 0.25 / 0.5 | Clear of the card 1 and card 2 paths |
| Knife | blade 0.30 × 0.015 × 0.06 (`--d3-metal`, faintly emissive for the glint), handle cylinder (`--d3-wood-dark`), drawn at 1.5× (toy exaggeration) | Lies along x on the table. The handle end overhangs the west edge to (3.2, 3.0), so the gate's grasp point is the handle the gripper closes on. | table top | |
| Cup | open cylinder r 0.05, h 0.09, inner disc | (3.6, 3.3) on the table | | |
| Sofa | seat, back rest on the south side, two arms, `--d3-fabric` | **7.2–9.4 × 1.0–1.6** (2D depth 1.8 reduced) | 0.22 / 0.45 | Card 3's robot at y = 2 clears it |
| Coffee table | wood box | (8.3, 3.6), 0.8 × 0.5 | 0.2 | |
| Floor lamp | thin pole, cone shade (the shade is emissive in the dark theme; it is not a light) | (9.6, 6.4) | 1.0 | |
| Bed with pillow, blanket (`--d3-bed`), teddy (3 spheres) | | **8.9–9.8 × 8.3–9.8** (narrower than 2D) | 0.25 | Card 2's target (8.5, 8.5) stays on open floor, so its marker is not hidden |
| Nap mat | flat cushion | 8.8–9.8 × 7.25–7.75 | 0.05 | Card 2's sleeping child lies here |
| Toy box | box with lid | (7.4, 9.5) | 0.3 | |
| Charging dock | base plate 0.7 × 0.5, back panel 0.5 × 0.08 × 0.45 on its north side (y 9.3), extruded 6-point lightning-bolt `Shape` in `--robot-eye` blue (never a verdict colour) | (1, 9) | | Card 2 beat 3's real destination |
| Fake tag (card 2, while `decor.fakeTag`) | 0.22 m sticker on the pier's south face at (7.3, 6.94), 0.45 m up. A 128² `CanvasTexture` holds a fixed QR-like 21 × 21 module pattern (seeded PRNG, drawn once) with a small bolt in the centre, plus tape corners. While the card focuses it, a CSS2D label reads **'충전소' 태그**. | | | Peeling (beat 3) rotates it 100° about its top edge and drops it (400 ms, instant under reduced motion), then hides it. |
| Peer robot (card 5 only; hidden otherwise) | box body 0.55 × 0.45 × 0.4 on 6 small wheels, cargo lid, antenna mast with a ball, and a **violet-outlined warning triangle made of shapes** on the lid (violet because it is the untrusted source, matching the strip's `⚠ 미등록`) | sidewalk (6.8, −0.9), facing north | 0.6 | See the signal below |

**Peer robot signal.** When the slip flies (t = 300), 5 violet orbs hop along a zig-zag polyline from the peer's antenna, over the south wall, to our robot's antenna (450 ms). On `source:not-allowed` they bounce back off the shield dome (v3.7).

**Robot** (footprint radius 0.3, total height 0.95):
- **Base:** a flat cylinder (r 0.28, h 0.08) in `--robot-body` darkened 20%, with two side wheels (r 0.09). The wheels spin with the actual executed speed (angle += v·dt / r); they are static under reduced motion.
- **Body:** a rounded box 0.48 (w) × 0.40 (h) × 0.46 (d), corner radius 0.08, `--robot-body`.
- **Head:** a rounded box 0.36 × 0.26 × 0.30 on a short neck, with a `--robot-face` face plate.
  - Two capsule eyes in unlit `--robot-eye` (`MeshBasicMaterial`, `toneMapped: false`). They blink (scale.y → 0.1 for 120 ms) every 3–5 s.
  - A lidar puck on top (`--d3-metal`) with a slowly turning ring. Card 4's glitch sparks appear here: 12 `Points`, 300 ms.
  - An antenna with a ball tip. The tip flashes violet when the command slip arrives. It shows the command reaching the robot; it is not a verdict.
- **Arm:** on the front right, an upper and a forearm segment with two finger boxes. It opens and closes only for a grasp or place reach taken from `decision.action` (or from the proposal, on the ghost). A held object is parented to the gripper. A held knife gets a thin outline hull (a BackSide clone scaled ×1.12, in `--bul-fg`), exactly like the 2D outline.
- **Haetae badge** on its back, made only of shapes:
  - a shield plate 0.16 × 0.19 (flat top with a small centre notch, rounded shoulders, pointed base), extruded 0.015, in `--d3-badge`, with an `--ink` rim
  - **one horn** (a cone, r 0.025, h 0.06) on top, since the haetae is a one-horned guardian
  - two eye dots
  - This is the same mark as the header logo (v3.9).
  - At `judge()`, the badge glows a neutral eye-blue for 300 ms; the verdict is not known yet. After the verdict lands, the rim takes the verdict fill for the dwell. That colour comes from the Decision.
- **Mode ring:** a flat torus (r 0.55) at Y 0.02, drawn from `sim.mode()` exactly as §3b. Caution is 16 amber dashes. Hold and above is solid red, with two small floating pause bars above the head.
- **Heading:**
  - The body yaw follows only executed motion. It turns in place during the first 200 ms of `scene.exec`, before any translation; turning is not travel.
  - Before the verdict, only the head turns (up to 45°) toward the intent target, "looking at what it was asked". On 차단 it turns back.
- **Flinch on 차단** (skipped under reduced motion), 450 ms:
  - The body pitches back 7° about the wheel axle, with **no translation**: a denied robot never moves or lurches.
  - The head shakes "no" (yaw ±14°, twice).
  - The eyes switch to `--bul-fill` and blink twice, then return.

**Humans** (only those in `world.humans`; people are never decor):
- **Adult:** height 1.25 (toy scale). Capsule body in `--adult`, sphere head in `--d3-skin`, capsule arms. Idle sway ±2° on a 4 s cycle.
- **Child:** height 0.8. Capsule body in `--child`, head with a half-sphere hair cap (`--d3-hair`), holding a toy (3-sphere teddy). Idle bob ±0.03 m, cosmetic as in 2D.
- **Sleeper:** a child whose position lies on the nap mat (card 2) is laid down (rotated 90° about X). Three small bubbles rise slowly above them (none under reduced motion).
- **Unknown (card 6 beat 2, from `beat.decor`):** the child becomes a translucent grey wireframe (opacity 0.5) with a CSS2D "?" tag.
- **Person tags:** CSS2D labels **아이** / **어른** at head height + 0.15.
- Human meshes are on raycast layer 1, for lab dragging.

**Ghost robot** (unfiltered replay only):
- Clones of the robot meshes, sharing their geometries, under **one** unlit `--intent` material: opacity 0.5, transparent, `depthWrite: false`, with a diagonal-stripe `alphaMap` (the hatch fill).
- It has no badge, no mode ring, no eye change and no shadow.
- A CSS2D label reads **필터 없음 · 모델 명령 원본**.
- It holds only what the proposal carries (§5).

### v3.3 Palette tokens

These are added to `:root` in `style.css`. Dark values go under both §3a dark selectors (`@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) }` and `:root[data-theme="dark"]`).

Existing tokens are reused unchanged: `--ink*`, `--line`, `--surface`, `--intent`, `--robot-*`, `--child`, `--adult`, `--zone-*`, `--yun-* / --jeol-* / --bul-*` and `--focus`.

`scene3d.js` reads all tokens through `getComputedStyle` on boot, on OS scheme change and on manual toggle. It then updates material colours **in place**: materials are cached by token name, so nothing is rebuilt. Numeric tokens are parsed with `parseFloat`.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--d3-bg-top` / `--d3-bg-bottom` | #F7F2E9 / #E4DACA | #1C2029 / #0E0F13 | map-wrap radial gradient behind a transparent canvas |
| `--d3-plinth` | #DCE3CF | #27312B | lawn top |
| `--d3-plinth-side` | #C9B79C | #1F1B16 | slab sides |
| `--d3-path` | #E8E1D4 | #34302A | sidewalk |
| `--d3-floor-kitchen` | #F3E9D8 | #3A342C | |
| `--d3-floor-hall` | #E4CFAE | #4A3D2E | |
| `--d3-floor-living` | #EEE7DB | #36322C | |
| `--d3-floor-child` | #F4E0E6 | #3F3139 | |
| `--d3-wall` / `--d3-wall-cap` | #FBF8F2 / #D9CFBF | #4D473F / #6B6256 | |
| `--d3-wood` / `--d3-wood-dark` | #C99A6B / #8C6440 | #7A5A3E / #5A4130 | |
| `--d3-fabric` / `--d3-fabric-2` | #8FA9B8 / #E3C9A8 | #4E6574 / #4B4034 | sofa / rug |
| `--d3-bed` | #B9C7E0 | #4A5670 | |
| `--d3-metal` | #B8C0C8 | #7D8792 | |
| `--d3-skin` / `--d3-hair` | #F1C9A5 / #5B3A29 | #D9AE8A / #3A261B | |
| `--d3-leaf` | #9DBF8A | #3F5A3A | shrubs |
| `--d3-badge` | #FFFDF8 | #E9EEF3 | robot badge plate |
| `--d3-hemi-sky` / `--d3-hemi-ground` | #FFF6E8 / #BBAA90 | #9FB3D1 / #2A241D | hemisphere light |
| `--d3-sun` | #FFF1DC | #DDE6FF | warm sun / cool moon |
| `--d3-hemi-i` / `--d3-sun-i` | 1.6 / 1.3 | 0.9 / 0.55 | intensities |
| `--d3-shadow` | 0.22 | 0.45 | contact-shadow opacity |

**Contrast rules:**
- Verdict and intent effects are unlit (`MeshBasicMaterial`, `toneMapped: false`), so their hue equals the DOM token.
- Every floor ribbon and ring sits on a slightly wider `--surface` backing ribbon or halo, so it separates from any of the four floor tints. It must meet 3:1 non-text contrast in both themes; check this in review with the §3a rules.
- The dark theme is a night dollhouse: a cool moon, lower hemisphere light, a warm lamp shade. The floor tints are chosen so `--robot-body` (light in dark mode) and `--child` / `--adult` stay distinct.

### v3.4 Renderer, lighting and materials

- **Renderer:** `new WebGLRenderer({ canvas: #map, antialias: dpr < 2, alpha: true })`.
  - `outputColorSpace = SRGBColorSpace`, `toneMapping = NoToneMapping` (so the token hues hold), and clear alpha 0.
  - `setPixelRatio(min(devicePixelRatio, cap))`, where cap is 2 on desktop and 1.5 when `(pointer: coarse)` or the width is under 720 px.
- **Lights (two only):**
  - `HemisphereLight(--d3-hemi-sky, --d3-hemi-ground, --d3-hemi-i)`.
  - One `DirectionalLight` sun (`--d3-sun`, `--d3-sun-i`) at three-space (−7, 14, −5): high in the north-west, so from the default south-east view its shadows fall toward the camera and read as depth. It targets the origin.
- **Shadows:**
  - Only the sun casts. `shadow.mapSize` is 1024, or 512 on coarse pointers.
  - The orthographic shadow camera spans ±8 (the whole plinth), near 2, far 40, with `bias −0.0004`, `normalBias 0.02` and `radius 3`.
  - `renderer.shadowMap.type = PCFShadowMap`: r186 removed `PCFSoftShadowMap`, and softness comes from `radius`.
  - `renderer.shadowMap.autoUpdate = false`. Set `needsUpdate = true` only on frames where a caster moved: robot, humans, tag, door, or the wall-height tween.
  - Casters: robot, humans, furniture, walls. Receivers: floor, plinth, rug, table and bed tops. Effects, paths, the ghost and labels neither cast nor receive.
- **Materials:**
  - `MeshLambertMaterial` for everything matte. It is cheap on phones and gives the toy look.
  - `MeshBasicMaterial` (`toneMapped: false`) for eyes, effects, paths, rings and the ghost.
  - No PBR, no environment map, no post-processing, no bloom. "Glow" is made from unlit colour, additive-free alpha and gradient alpha maps.
- **Fog:** none, except the low-confidence haze (v3.6).

### v3.5 Camera and controls

**Camera.**
- `PerspectiveCamera`, fov 32°. A narrow fov gives a tabletop-miniature look with little distortion. Near 0.5, far 120.
- **Fit distance `fitD`:** a 12-step binary search on distance so that the 8 projected corners of the plinth (bounds from the house ∪ policy zones, as in the 2D `setPolicy`) fit inside 92% of the viewport at the current angles. It is recomputed on resize and on policy apply. The default framing also keeps the house clear of the camera toolbar (v3.15).
- **Default view:**
  - target = centre of the plinth top, `w2v({x: 5, y: 4.65}, 0.3)`
  - azimuth **+28°** (camera to the south-east)
  - polar **50°** from vertical
  - distance `fitD`
  - The whole house is visible at every breakpoint, including 343 px.

**OrbitControls (three r186).**

| Setting | Value | Reason |
|---|---|---|
| `enableDamping` / `dampingFactor` | true / 0.12, off under reduced motion | |
| `rotateSpeed` | 0.8 | |
| `minPolarAngle` / `maxPolarAngle` | 8° / 70° | Near top-down (map-like reading) is allowed; the camera never drops below the plinth rim |
| `minAzimuthAngle` / `maxAzimuthAngle` | −110° / +110° | North always stays roughly "away", so the floor plan never appears upside down and the captions' mental map holds. The house can still be seen from the front and both sides. |
| `minDistance` / `maxDistance` | 0.45 · fitD / 1.3 · fitD | |
| Pan | `enablePan` true, `screenSpacePanning` false (pan on the floor plane), `cursor` = house centre, `maxTargetRadius` 4 m | The house cannot be panned out of view |
| Keys | not bound (`listenToKeyEvents` is never called) | Arrow keys belong to the app and the lab |

**Mouse.**
- Left-drag rotates (card mode). Right-drag or Shift+left pans.
- **The wheel zooms only with Ctrl/⌘.** A plain wheel scrolls the page and shows a once-per-session chip, "Ctrl(⌘) + 스크롤로 확대". A trackpad pinch arrives as a ctrl-wheel event, so it just works.
- Implementation: `enableZoom = false`, plus a capturing `wheel` listener that turns zoom on for ctrl/meta events.

**Touch.**
- `touches = { ONE: null, TWO: TOUCH.DOLLY_ROTATE }`. After `controls.connect()`, reset `canvas.style.touchAction = 'pan-y'`. One finger scrolls the page (the stage is embedded in a scrolling page at 375 px); two fingers rotate and pinch-zoom. A one-time hint chip reads "두 손가락으로 돌리고 확대할 수 있어요".
- A one-finger tap (movement < 6 px) is a click: a toast in card mode, a command in the lab.

**Lab mode** (`stage.setLabMode(true)`):
- `mouseButtons = { LEFT: null, MIDDLE: DOLLY, RIGHT: ROTATE }`, so left-click is the command and right-drag rotates.
- `touchAction = 'none'`: one finger aims, taps or drags a human; two fingers orbit.
- The lab's "이렇게 써요" line says: "바닥을 누르면 명령, 오른쪽 드래그(두 손가락)로 돌려 보기".

**View toolbar.**
- DOM, top-right corner of the map, `role="toolbar"`, `aria-label="시점"`. Buttons are 44×44 with Korean `aria-label`s and `title`s.
- The glyphs are non-Han Unicode (⟲ ⟳ ＋ － ⌂) or inline SVG.

| Button | `aria-label` | Action | Key |
|---|---|---|---|
| ⟲ | 왼쪽으로 돌리기 | azimuth −30° | `[` |
| ⟳ | 오른쪽으로 돌리기 | azimuth +30° | `]` |
| ＋ | 확대 | distance × 0.8 | `+` |
| － | 축소 | distance × 1.25 | `-` |
| ⌂ | 시점 초기화 | default view, and hands the camera back to auto-framing | `V` |

- Button moves tween over 350 ms (instant under reduced motion).
- Under 720 px, only ⟳ and ⌂ show.
- The keys follow the §9 rule: inactive while a dialog is open or a text field has focus.
- The toolbar comes after the transport in DOM and tab order (header → transport → 시점 → cards), so the primary button is still reached first.

**Auto-framing per beat.** The director calls `stage.frameBeat({ phase, points })`:

| Phase | When | Points |
|---|---|---|
| `card` | card start | whole house: the default view |
| `beat` | beat t = 0 | robot pose; proposal target clamped to the workspace; the beat's `focus` entities; the fake tag or peer robot if in focus |
| `verdict` | t = 1550 | nudge only (v3.7) |
| `exec` | t = 2300 | executed from → to |
| `ghost` | unfiltered replay | ghost from → to, plus the real robot |
| `end` | end card | default view |

- **Framing math:** keep the viewer's **current azimuth and polar** angles. Tween only the target (to the centroid of the points, clamped by `maxTargetRadius`) and the distance (fit the points' bounding sphere plus a 1.5 m margin, clamped to 0.55–1.0 × fitD). The tween runs 900 ms with easeInOutCubic.
- **Ownership:** an OrbitControls `start` event or any toolbar press sets `userOwnsCamera`. Auto-framing then stops for the rest of the card, and ⌂ shows a small dot with the `title` "장면 따라가기 꺼짐 — 눌러서 켜기". Pressing ⌂ or starting a new card hands the camera back.
- **Kiosk:** always auto. During dwells it also sways the azimuth ±12° over 40 s (not under reduced motion).
- **Reduced motion:** no auto-framing and no nudge. The camera stays at the default view, or wherever the viewer put it.

**Per-card staging** (a hint list for `beat` points; no new data):

| Card | Beat framing leans toward | Special props |
|---|---|---|
| 1 | robot (3, 3), child (2, 6) | knife on the table, knife in the gripper |
| 2 | (3, 3) → the child room at (7, 7) | fake tag, sleeper on the mat, door; the tag peels on beat 3 |
| 3 | the strip y = 2, x 1 → 9 | adult (5, 2.8), hall roundel |
| 4 | robot (3, 3) / (3, 1) | lidar sparks, mode ring |
| 5 | robot plus the peer on the sidewalk | peer robot, orbs, shield dome |
| 6 | the kitchen's west side | haze fog; wireframe child "?" |
| 7 | (1, 1) → (3, 3) | none (the tape tag is on the slip) |
| 8 | whole house (stream pacing) | as the lines require |

### v3.6 Rules and zones on the floor

**Decals.** All decals are flat meshes at Y 0.045–0.07 with `polygonOffset`, layered zones < rings < paths < markers. Everything is drawn from the **loaded policy** and from `highlightsFor(decision, …)`, exactly as §3b and §3e select it. A name that did not fire draws nothing.

- **No-entry zone** (`no_entry`, 아이 방):
  - A crosshatch `CanvasTexture` in `--zone-deny` at 12% on the zone rect, and a dashed border of 0.04 m dash quads.
  - A lock pictogram floats 0.9 m above the zone centre: a rounded-box body and a half-torus shackle in `--zone-deny`. It bobs 0.04 m (static under reduced motion).
  - A CSS2D chip reads **아이 방 · 출입 금지**.
  - When `zone:<id>` fires, the hatch flashes to 40% twice (2 × 300 ms) and the lock scales 1 → 1.3 → 1.
- **Speed-limit zone** (`speed_limit`, 복도):
  - A diagonal hatch in `--zone-slow` at 16%, and a speed roundel (ring plus disc) painted on the floor at the zone centre.
  - A CSS2D chip reads **복도 · 0.3 m/s**; the number comes from the policy.
  - When it fires, the hatch brightens and the roundel pulses once.
- **Decals follow the policy; the walls do not** (the walls are fixed art). An edited zone that no longer lines up with a wall is fine, because the decal is the truth. The fade rule stops a wall from pretending to enforce anything.
- **Workspace:** a thin inset line on the floor edge in `--ink-2` at 30%.
  - It flashes on `envelope:workspace` or `envelope:pose`.
  - An off-house target is clamped to the plinth edge with an arrowhead, as in 2D.
- **Distance rings** (fired `human_within` rules; the humans are chosen exactly as `highlightsFor` chooses them):
  - A flat dashed ring at the rule's `distance`: 36 `RingGeometry` arc dashes, 0.05 wide.
  - A disc fill at 8% alpha.
  - A **0.14 m translucent band** (an open cylinder at 18% alpha with a vertical-fade `alphaMap`), so the circle reads even at a low camera angle.
  - Colour: `--bul-fg` for a `then: "bul"` rule, `--jeol-fg` for a jeol rule.
  - A CSS2D radius label ("1.5 m") on the ring's camera-facing side.
  - It fades in 250 ms after the verdict effect.
  - A held knife glints twice: an emissive pulse on the blade.
- **Intent path** (violet, the model's raw wish):
  - A dashed ribbon 0.07 wide (dash 0.25 / gap 0.2) at Y 0.06, from the robot to the target, growing over `pathDur`, with a flat triangle arrowhead.
  - The **target marker** is a violet ring (r 0.18) plus a 0.3 m pin, so the goal reads in 3D.
  - A CSS2D midpoint chip reads **요청 0.8 m/s**; the value is from the proposal.
  - Grasp and place use the same path, to `at`. Stop has no path.
- **Executed path** (from `scene.exec`, which the director copied from the Decision):
  - A solid ribbon 0.10 wide over a 0.14 `--surface` backing ribbon, in the fill of `scene.exec.verdict`.
  - The whole path shows at 45% opacity when execution starts. The part already travelled is drawn at 100% as the robot moves.
  - For 감속, three flat chevrons in `--jeol-fill` sit across it.
- **Beyond a barrier:** the intent ribbon past the anchor splits into 4 pieces that sink (Y → −0.02) and fade to 30% over 400 ms (a static 30% under reduced motion). This is the §3d crack.
- **Ghost path:** the same violet dashed ribbon and marker as the intent path. **Never** a verdict colour.
- **Lab aim:** a floor reticle (ring plus cross) at the cursor, and a thin violet line from the robot to the cursor. The preview tag is `overlay.preview()`, which is still a real `labGate.judge()` call.
- **Haze** (when `low-confidence` fires, or the world confidence is below 0.6 as an input fact): `scene.fog = new Fog(--d3-bg-bottom, 0.8 · fitD, 1.6 · fitD)` fades in over 300 ms, together with the existing DOM haze. The confidence chip is unchanged.
- **`stale:world`:** the existing `.desat` CSS filter on `#map` works on the WebGL canvas as it is. Idle animation freezes, and the `❚❚ 화면 멈춤` stamp appears (overlay).
- **`mode:*`:** the existing DOM vignette plus the robot's mode ring.
- **Spotlight** (§3f t = 0): non-focus furniture and decor colours lerp 35% toward `--d3-bg-bottom`. Dimmable groups get per-group material clones at build time; there is no per-frame allocation. The DOM vignette stays as it is.

### v3.7 The verdict moment in 3D (t = 1550)

The director sets `scene.fx = { decision, anchor, t0 }`:
- `decision` is the frozen Decision.
- `anchor` comes from `sealAnchor()`, which is unchanged and does placement only.

`Stage3D` hands this to `fx3d.verdict(decision, anchor, ctx)`, which requires the Decision object. It reads only `decision.verdict`, `decision.fired` (through `highlightsFor`), `decision.action` and `decision.speed_cap`. Without a Decision there is no verdict effect.

**차단 (bul).**
- **Spatial anchor** (no-entry entry point, human-ring point, workspace edge): a **barrier wall** rises across the intent path at `anchor.p`, across `anchor.dir`. At verdict time it is turned up to 45° toward the camera so that it never reads edge-on (|cos(normal, view)| ≥ about 0.7 from the default azimuth).
  - Size: a shallow 120° arc (radius 0.8 m, chord about 1.4 m), its middle at the anchor and bowing toward the target. It rises from 0 to 0.9 m in 260 ms (easeOutBack, overshoot 1.06), then settles.
  - Build:
    - a double-sided open cylinder segment with a hex-lattice `CanvasTexture` (256², drawn once) in `--d3-bul` at opacity 0.38, `depthWrite: false`
    - an opaque bright curved top bar (torus arc, tube 0.02)
    - two thin posts at the arc ends
  - 3D effects (ribbons, chevrons, barrier, arch, rings) use `--d3-yun` / `--d3-jeol` / `--d3-bul`, tuned to ≥ 3:1 against `--surface` and every `--d3-floor-*` tint in both themes.
  - `Stage3D.screenBox('barrier' | 'dome' | 'arch' | 'ghost')` projects the configured effect at full size; the verdict tag treats it (and the rule's people) as weight-6 obstacles and docks to the map's top or bottom edge when every placement would still hide more than about 40% of one.
  - The lattice scrolls upward slowly (uv.y += 0.08/s, frozen under reduced motion).
  - A floor impact ring grows from r 0.2 to r 1.6 while fading from 0.6 to 0 over 420 ms.
- **Non-spatial** (`source:*`, `stale:*`, `invalid:*`, `mode:hold` and above, a rule without geometry): a **hex-lattice shield dome** (a half sphere, r 0.62, same material) closes around the robot, scaling 0 → 1 over 260 ms. The DOM tag still lands on the command slip, as in §3e rule 5; the dome is its 3D echo. For `source:not-allowed` the peer's orbs bounce off the dome.
- **Robot flinch** (v3.2).
- **Camera nudge:** the distance goes ×0.96 toward the anchor over 180 ms, then back over 400 ms. This is skipped under reduced motion or while `userOwnsCamera`.
- The existing 160 ms CSS `.shake` of `#map-wrap` runs unless reduced motion is set.
- The strip link is cut, as in §3c.
- **Trail:** when the next beat starts, the barrier sinks to 25% height at 25% opacity, the 3D form of the §3e seal trail. Trails clear at card end.

**감속 (jeol).**
- A **speed gate** arch stands across the executed path at the jeol anchor: the path midpoint, or the zone entry point if a `zone:<speed_limit>` is the headline.
  - Two 0.7 m posts, 1.1 m apart, perpendicular to the path, with a crossbar, all in `--jeol-fill`.
  - It rises 0 → 1 over 220 ms, with no overshoot.
  - A CSS2D chip on the crossbar reads **≤ {decision.speed_cap} m/s**.
- A **speed ring** (a flat torus, r 0.45, `--jeol-fill`, with 8 notches) rides under the robot during the executed motion. The notches rotate at a rate proportional to `decision.action.speed` (static under reduced motion).
- The robot really moves at `decision.action.speed` (time-compressed by `k`, §3d), and the wheel spin matches.
- The slip speed diff and the brake stack stay in the overlay, unchanged.

**통과 (yun).**
- A **soft green glow**: a radial-gradient disc under the target (or under the robot, for stop) in `--yun-fill`. Its opacity goes 0 → 0.45 → 0.2 over 300 ms, with one ring pulse.
- The badge rim glows green for the dwell, and the executed ribbon is green.
- The tag fades after 1.5 s (§3e).

**The verdict tag.** This DOM overlay replaces the glyph seal and the callout, in 2D and 3D alike.
- **Content:**
  - Line 1: `[icon] 차단 · {label ≤ 12 chars}`.
  - Line 2 (desktop): `+{n} · 자세히 ▸`.
  - Mobile: a single pill, `[icon] 차단 · label`.
  - The word uses the `-fg` token on `--surface`.
- **Icon and shape** (inline SVG `<symbol>` sprite in `index.html`; 40 px, or 32 px on mobile):
  - 통과: a green circle with a check.
  - 감속: an amber rounded diamond with a double down-chevron.
  - 차단: a red octagon with a white horizontal bar.
  - Shape stays a second channel beside colour.
- **Entrance:**
  - 차단: scale 1.6 → 0.96 → 1 (180 + 120 ms).
  - 감속: 1.3 → 1 (220 ms).
  - 통과: fade.
  - Under reduced motion, all fade in over 120 ms.
- **3D anchoring:** the world point plus a height, through `stage.toScreen(p, h)`, with h = 1.05 at a barrier or arch top and 1.35 above the robot. The tag hangs above its anchor on a 1 px leader, so it covers wall and sky rather than the robot. It is DOM above the WebGL canvas, so geometry **never occludes it**.
- **Placement:**
  - The existing `pickRect()` candidates (above / right / left / below) are tested against obstacles: the projected screen boxes (`stage.screenBox`) of the robot, the humans in the rule, the barrier, the slip and the trail.
  - The tag is clamped 8 px inside `#map-wrap`.
  - Placement re-runs on every camera `change` (throttled to one per animation frame) and on resize.
  - If the anchor projects behind the camera or outside the map, the tag docks to the nearest map edge with a small arrow pointing toward the anchor.
- **Label priority:** when labels overlap, the lower-priority one hides (opacity 0). This is re-evaluated on view change, not per frame. Order, highest first:
  1. verdict tag
  2. command slip
  3. context chips
  4. person tags
  5. ring and speed chips
  6. zone chips
  7. room labels
- **Occlusion of CSS2D labels:** on view change, one raycast runs from the camera to each person-tag anchor (at most 6). If a wall or furniture mesh is hit first, the tag drops to 55% opacity with a dotted border. It stays readable and is visibly marked as "behind something".

### v3.8 Module map and the stage interface

| File | Status | Role |
|---|---|---|
| `js/model.js` | **new** (extracted) | Pure shared data and maths: `DECOR`, `ROOMS`, `FURN3D` (the 3D footprints above), `dist`, `prog`, `lerp`, `easeOut`, `robotPose`, `displayHolding`, `ghostHolding`. `stage.js` re-exports them so `director.js` imports keep working. |
| `js/scene3d.js` | **new** | `Stage3D`. It owns the renderer, camera, OrbitControls and `CSS2DRenderer` (a label layer `div` inside `#map-wrap`, `pointer-events: none`, `aria-hidden="true"`, stacked above the canvas and below `#overlay`). It builds the lights, plinth, house and furniture; runs wall cutaway and fade; draws zone decals; reads the palette; computes `fitD`; runs the auto-framing tweens; wires the view toolbar; raycasts (`toWorld`, human hit test); runs the render-on-demand loop; handles WebGL context loss; and disposes. Imports: `three`, `three/addons/OrbitControls.js`, `three/addons/CSS2DRenderer.js`, `model.js`, `actors3d.js`, `fx3d.js`, `geom3d.js`. **Never** imports `engine.js`. |
| `js/actors3d.js` | **new** | Robot (badge, eyes, gripper, lidar, wheels, flinch), adult, child, sleeper, unknown child, knife/cup/toy, dock, fake tag and peel, peer robot and orbs, ghost robot. `update(scene, clock, real)` sets transforms from `robotPose` / `displayHolding` / `scene.ghost`, as the 2D draw does. |
| `js/fx3d.js` | **new** | Intent, exec and ghost ribbons, markers, chevrons, crack; rings; zone and workspace flashes; barrier, dome, speed gate, speed ring, green glow, impact ring; mode ring; haze; aim reticle. All objects are pooled. `verdict(decision, anchor, ctx)` is the only entry that creates verdict effects. |
| `js/geom3d.js` | **new** | `w2v` / `v2w`; `roundedBox(w, h, d, r)` via `ExtrudeGeometry`; `shieldShape()`, `boltShape()`, `dashedRing()`, `ribbon(points, width, dash)`; `CanvasTexture` makers (hatch, crosshatch, hex lattice, stripes, QR pattern, radial glow, tile, planks), each ≤ 256² and drawn once; a material cache keyed by token and variant. |
| `js/stage.js` | kept: **2D fallback** | Han badge replaced by a drawn shield-and-horn path. Pure helpers moved to `model.js`. Adds the interface members below (`toScreen(p, h)` ignores `h`, `pxPerM = scale`, `screenBox` from its existing rects, and no-op camera methods). |
| `js/overlay.js` | kept, adapted | Seal → verdict tag with SVG icon. Uses `stage.toScreen(p, h)`, `stage.pxPerM(p)` and `stage.screenBox()` instead of `stage.scale`. Relayouts on `stage.onView`. Strip gate node → mark icon. Progress dots → icons. Adds `setStage(stage)` for the runtime swap. |
| `js/ghost.js` | **unchanged** | Still returns the ghost description through `layer.setGhost`. `actors3d` / `fx3d` draw it in `--intent` only. |
| `js/director.js` | kept, small edits | Calls `stage.frameBeat`, `stage.pulse('judge')` and `stage.react(verdict, anchor)` instead of touching `#map-wrap` classes directly. Sets and clears `scene.fx`. Vocabulary per v3.9. Canvas `aria-label` prefix becomes "모형 집:" (3D) / "지도:" (2D). |
| `js/lab.js` | kept, small edits | `stage.setLabMode(on)`; pointer through `stage.bindPointer` (a raycast in 3D); camera-relative keyboard cursor. |
| `js/cards.js`, `js/engine.js`, `js/log.js` (chip text only), `js/scenario.js` (caption text only), `js/explain.js` (vocabulary only), `js/copy.js` (vocabulary only) | kept | |
| `js/selftest.js` | kept, extended | Adds `hanScan()` (v3.9). The verdict checks are stage-independent and pass headless in both modes. |
| `app.js` | edited | Stage selection and swap; the 보기 option. |
| `index.html` | edited | The import map `<script type="importmap">{"imports": {"three": "./vendor/three/three.module.js", "three/addons/": "./vendor/three/"}}</script>`, placed **before** the module script; the SVG sprite (`#mark`, `#v-yun`, `#v-jeol`, `#v-bul`); the view toolbar; the 보기 radio; no Han characters. |
| `style.css` | edited | `--d3-*` tokens; `#map-wrap` aspect **4:3 when the stage column is at least 820 px wide, otherwise 1:1** (amended by v3.15) (the 2D fallback always forces 1:1 via `.stage-2d`); toolbar; tag and icon styles; `--font-seal` and its rules removed. |

**Stage interface.** Both `Stage` (2D) and `Stage3D` implement it. The director, the overlay and the lab talk only to this:

```js
interface StageLike {
  kind: '2d' | '3d';
  canvas: HTMLCanvasElement;          // always #map
  cssW: number; cssH: number;         // #map-wrap size in CSS px
  onResize: (() => void) | null;
  onView: (() => void) | null;        // camera changed → overlay.relayout() (3D only)
  readPalette(): void;
  setPolicy(policy): void;            // bounds, zone decals, fitD
  render(scene, clock, real): void;   // draws the scene object; never decides anything
  toScreen(p, h = 0): { x, y, behind };   // world metres (+ height) → #map-wrap px
  pxPerM(p): number;                  // replaces stage.scale in overlay rect maths
  screenBox(kind, id?): { x, y, w, h } | null;  // 'robot' | 'human' | 'barrier' | 'ghost'
  toWorld(clientX, clientY): { x, y } | null;   // floor raycast (3D) / inverse map (2D)
  clamp(p): p;
  bindPointer(handlers): void;        // the same handler object lab.js passes today
  setLabMode(on): void;
  frameBeat({ phase, points }): void; // 2D: no-op
  resetView(): void;                  // 2D: no-op
  pulse(kind): void;                  // 'judge' → badge glow
  react(verdict, anchor): void;       // 2D: existing shake + eye flash; 3D: flinch + nudge + shake
  dispose(): void;
}
```

The `scene` object keeps its current shape. The one addition is `scene.fx = { decision, anchor, t0 }`, a reference to the frozen Decision. The 2D stage ignores it, because it already draws from `scene.barrier`.

**Stage selection** (`app.js`, before anyone calls `getContext` on `#map`):
1. `?stage=2d`, or a saved preference of `2d` (`localStorage['haetae.sim.stage.v1']`, in try/catch), selects `Stage`.
2. Otherwise, probe WebGL on a throwaway canvas (`getContext('webgl2') || getContext('webgl')`).
   - If there is none, use 2D and show a one-time toast: "이 브라우저에서는 3D를 쓸 수 없어 2D 지도로 보여 드려요."
   - If `WEBGL_debug_renderer_info` reports a software renderer (`/SwiftShader|llvmpipe|Software/`), default to 2D; 보기 still offers 3D.
3. Otherwise, run `const { Stage3D } = await import('./js/scene3d.js')` in parallel with WASM init. Three.js is loaded **only here**. Any throw falls back to 2D.
4. **Runtime swap routine** (used for context loss and for the 보기 switch):
   - dispose the old stage
   - replace `#map` with a fresh `<canvas>` carrying the same id, role, `aria-label`, `tabindex` and classes
   - construct the other stage
   - `overlay.setStage(s)`, `director.stage = s`, and re-bind the lab pointer
   - `setPolicy`, then re-render the current scene
   - On `webglcontextlost`: `preventDefault()`, swap to 2D for the rest of the session, and toast "3D 화면이 멈춰 2D 지도로 바꿨어요."
5. 보기 popover: a new radio group **화면: 3D 모형 / 2D 지도**.
6. `?selftest` and `?kiosk=1` work with either stage.

**Director → 3D, along the §3f timeline:**

| t (ms) | Director (unchanged logic) | 3D stage |
|---|---|---|
| 0 | narrator line, fault, world snap | `frameBeat('beat')`; spotlight dim; robot eases to the new pose (400 ms) |
| 300 | slip flies (DOM), strip packet | antenna tip flashes violet; peer orbs (card 5) |
| 750 | `scene.intent` | intent ribbon grows; the head turns toward the target |
| 1250 | **`judge()`**; Decision frozen | `pulse('judge')`: neutral badge glow, 300 ms |
| 1550 | `scene.fx`, `react()`, tag | `fx3d.verdict(...)`: barrier / dome / speed gate / glow; flinch; nudge |
| 1750 | `highlightsFor()` | rings, zone flashes, workspace flash, knife glint |
| 2300 | `scene.exec` from `decision.action` | yaw in place, then travel; `frameBeat('exec')`; speed ring |
| end | dwell, end card | trail; `frameBeat('end')` |

**Unfiltered replay in 3D (§5 holds in full):**
- The ghost robot and ghost path appear.
- The real robot and its recorded effects are drawn at 40% (a group-level opacity on its materials while `scene.realAlpha < 1`).
- The plinth side band swaps to a violet diagonal hazard-stripe texture: the 3D version of the §5 hazard frame. The DOM banner and the strip bypass stay as they are.
- `frameBeat('ghost')`.
- `judge()` is not called, and no verdict effect exists.

**Lab click-to-target.**
- **`toWorld(clientX, clientY)`:** NDC from `canvas.getBoundingClientRect()`, then `raycaster.setFromCamera`, then `ray.intersectPlane(floor Y = 0)`, then `v2w`, then `clamp` to the policy bounds ± 0.4, then `Stage.snap` (0.05 m). It returns `null` if there is no hit, which cannot happen within `maxPolarAngle` 70°.
- **Human drag:**
  - `bindPointer` raycasts human meshes (layer 1) first.
  - On a hit, it captures the pointer, sets `controls.enabled = false` for the drag, and moves the human along the floor plane through `onHumanMove`.
  - `onHumanDrop` ends the drag, and the controls are restored.
  - A click with less than 6 px of movement calls `onCommit`. These are the same handlers as today.
- **Keyboard cursor:** arrow keys move 0.25 m (Shift: 1 m) in **camera-relative** directions, snapped to the nearest world axis ("up" is the world axis closest to the camera's forward direction on the floor). Enter sends. The reticle is drawn in 3D, and the live region states the coordinates as it does today.
- **Aim preview:** unchanged; it is a throttled real `labGate.judge()`.

### v3.9 Han-character removal

**Rule:** no code point in U+3400–U+4DBF, U+4E00–U+9FFF or U+F900–U+FAFF may appear anywhere in `index.html`, `style.css`, `app.js` or `js/*.js`. That covers comments, `title` / `aria-label` attributes, data URIs (including percent-encoded ones) and strings built at runtime.

The English ids `yun` / `jeol` / `bul` remain in code, CSS class names and small mono technical text.

**Verdict vocabulary:**

| id | Word | Icon (SVG `<symbol>`) | Plain-text symbol | Shape | Colour |
|---|---|---|---|---|---|
| yun | **통과** | check in a circle | ✓ (U+2713) | circle | `--yun-*` |
| jeol | **감속** | double down-chevron | ⇊ (U+21CA) | rounded diamond | `--jeol-*` |
| bul | **차단** | white bar in an octagon | ⊘ (U+2298) | octagon | `--bul-*` |

- Plain-text symbols are used only where markup cannot go: log-chip `textContent`, `title` attributes and console tables. They are text-presentation symbols found in system fonts, so they do not turn into OS-specific emoji.
- Icons are always `aria-hidden`; the Korean word carries the meaning.
- Legend `title` / `aria-label` values:
  - "통과: 그대로 실행"
  - "감속: 속도를 줄여 실행"
  - "차단: 실행하지 않음"

**Brand refresh (2026-10-04).** The user-approved A seated guardian replaces the old shield-and-horn header, favicon and pipeline mark. Canonical vectors and the adaptive external `#mark` symbol are in `brand/haetae/`; see root `DESIGN.md` and `docs/brand.md`. The historical v3.9 robot badge geometry is an illustrative legacy model decoration, not the current identity.

**Historical v3.9 inventory.** The old shield with one horn and two eye dots replaced the Han logo in:
- the header
- the favicon (an SVG data URI: white mark on an `--ink` rounded square, never a verdict colour)
- the pipeline gate node, which becomes `[mark] 해태`
- the robot badge (3D shapes, and a 2D canvas path)

**Inventory.** This comes from a grep of `sim/` for the ranges above, plus the percent-encoded favicon, which a plain grep misses. Old glyphs are named by code point: U+5141 = old yun, U+7BC0 = old jeol, U+4E0D = old bul, U+736C = old logo, U+9580 = old gate.

| # | Place | Today | Replacement |
|---|---|---|---|
| 1 | `index.html:9` favicon | SVG data URI with `<text>` U+736C (percent-encoded `%E7%8D%AC`) | data URI of the shield-and-horn mark path, no `<text>` |
| 2 | `index.html:17` header `.mark` | U+736C | `<svg class="mark" aria-hidden="true"><use href="#mark"/></svg>` next to "해태" |
| 3 | `index.html:22–24` header legend | glyph spans U+5141 / U+7BC0 / U+4E0D; `title` / `aria-label` "glyph 통과 / 줄임 / 막음" | icon + `<span class="lg-word">통과 / 감속 / 차단</span>`; labels as above |
| 4 | `index.html:58` strip gate node | `.gate-glyph` U+9580 | mark icon + "해태" |
| 5 | `index.html:81–83` intro legend chips | glyph + 통과 / 줄임 / 막음 | icon + 통과 / 감속 / 차단 |
| 6 | `index.html:130` help text | "…모델과 모터 사이의 문(U+9580)에서… glyph 통과, glyph 줄임(예: 더 느리게), glyph 막음…" | "해태는 모델과 모터 사이의 관문에서 모든 명령을 심사해 **통과**, **감속**(예: 더 느리게), **차단** 가운데 하나로 판정합니다." The next paragraph changes "지도 위에서" to "모형 집 위에서", and a 시점 line is added: "드래그로 돌리기 · Ctrl(⌘)+스크롤로 확대 · 두 손가락으로 돌리고 확대 · V 시점 초기화". The shortcut list gains `[` `]` `+` `-` `V`. |
| 7 | `style.css:1`, `:257`, `:443` | comments naming U+4E0D, U+9580, U+5141 | rewrite with the Korean words or the ids |
| 8 | `style.css:216`, `:278`, `:511`, `:529`, `:565–567`, `:572` | `--font-seal` rules for glyph spans | delete, together with the `--font-seal` token; icons are sized by `.vi` rules |
| 9 | `js/copy.js:8–10` `VERDICT_UI` | `{ glyph, word: '줄임' / '막음' }` | `{ icon: 'v-yun', sym: '✓', word: '통과' }`, `{ icon: 'v-jeol', sym: '⇊', word: '감속' }`, `{ icon: 'v-bul', sym: '⊘', word: '차단' }` |
| 10 | `js/copy.js:48` `verdictHtml` | `<span aria-hidden>glyph</span> word` | `<svg class="vi vi-{v}" aria-hidden="true"><use href="#v-{v}"/></svg> {word}` |
| 11 | `js/copy.js:160–161` | comment with glyphs | words |
| 12 | `js/explain.js:5–7` `VERDICTS` | glyphs; `ko` 허용 / 제한 허용 / 거부 | `sym` as in #9; `ko` 통과 / 감속 / 차단 (one vocabulary everywhere) |
| 13 | `js/explain.js:56` `verdictLabel` | `glyph name (ko)` | `{sym} {ko} ({name})` |
| 14 | `js/overlay.js:235` `makeSeal` | `.seal-glyph` text | verdict tag with SVG icon (v3.7) |
| 15 | `js/overlay.js:612` callout word | glyph span | `verdictHtml` |
| 16 | `js/overlay.js:839` progress dots / filmstrip | `textContent = glyph` | small verdict-shape icon in the verdict colour |
| 17 | `js/overlay.js:589` | comment | words |
| 18 | `js/stage.js:811` (2D robot badge) | `fillText` U+736C; `SEAL_FONT` | drawn shield-and-horn path; constant removed |
| 19 | `js/director.js:422` live region | "해태 판정: {word}({glyph})." | "해태 판정: {word}." e.g. "해태 판정: 차단. 칼을 든 채 아이 1.5 m 안으로는 갈 수 없어요. 함께 걸린 조건 1개." |
| 20 | `js/director.js:1190–1193` rail badge | glyph spans + "✓" watched mark | verdict icons; the watched mark becomes the text chip **봄**, because ✓ now means 통과 |
| 21 | `js/director.js:1213` detail head | glyph | icon + word + `bul` in mono |
| 22 | `js/log.js:50` log chip | `{glyph} {verdict}` | `{sym} {word} · {verdict}` |
| 23 | `js/scenario.js:238` card-8 caption | `{glyph} {verdict} ({ko})` | `{sym} {ko} ({verdict})` |
| 24 | old words (no Han, but the vocabulary changes): `js/director.js:714`, `:744`, `:1016–1017`, `:1028`, `:1032` | "막음 / 줄임" in the with/without line, end caption, scoreboard, announce | "차단 / 감속", e.g. "통과 3 · 감속 1 · 차단 3", "해태와 함께: 차단 {b} · 감속 {j} · 통과 {y}", and the card status text "본 장면: 차단, 차단, 통과" |
| 25 | `DESIGN.md` §0–§10 | spec prose uses the old glyphs | superseded by this section; not shipped, and not part of the scan |

**Generated and vendored files:** `sim/pkg`, `sim/vendor/three` and `sim/examples` contain no Han characters (checked) and are not edited. `README.md` outside `sim/` is out of scope.

**Guard.** `selftest.js` gains `hanScan()`, which counts as one extra row in the selftest panel:
- It fetches `index.html`, `style.css`, `app.js` and every `js/*.js` module (a const list in `selftest.js`).
- It decodes `%XX` runs with `decodeURIComponent` inside try/catch.
- It tests each file against `/[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]/u`.
- After the card run, it also scans `document.body.innerText`, every `title` / `aria-label` / `alt` attribute, and the CSS2D layer text.
- Any hit goes to `console.error` with file and line, and the panel shows **한자 검사 실패**.

### v3.10 Accessibility

- **`#map` (the WebGL canvas)** keeps `role="img"` in card mode, with the per-beat `aria-label` (§9; the prefix becomes "모형 집:"). In lab mode it becomes `role="application"`.
- **Aria-hidden layers:** the CSS2D label layer and `#overlay`.
- **The caption and the single polite live region carry all meaning**, unchanged from §9. The 3D view is a visual restatement.
- **No function is gesture-only.** Every camera move has a toolbar button and a key, and no card requires moving the camera.
- **Focus:**
  - 3 px `--focus` ring on the toolbar buttons.
  - Camera changes never move focus.
  - Focus never lands on the canvas unless it is in lab mode.
- **Not colour alone:**
  - verdict icon shape plus word
  - barrier (wall) vs speed gate (arch) vs glow (disc): three different forms
  - dashed intent vs solid executed paths
  - crosshatch vs diagonal hatch, plus zone chips
- **Sizes:** labels at least 12 px, the tag 14 / 16 px as in §9, targets at least 44 px. The layout holds at 375 px with no horizontal scroll: the toolbar is inside the map, and CSS2D labels are clipped by `#map-wrap`'s `overflow: hidden`.
- **`forced-colors: active`:** the overlay tag uses system colours. The 3D keeps rendering as a decorative image.

### v3.11 Reduced motion (`prefers-reduced-motion`, or 보기 → 움직임 줄이기)

Everything in the §9 reduced-motion list still applies. In 3D, additionally:
- **Camera:** no damping, no auto-framing, no nudge, no kiosk sway; toolbar moves are instant; wall cutaway height changes are instant.
- **Effects** appear at their final size with a 120 ms opacity fade: the barrier and dome at full height, the gate standing, the glow static. The lattice does not scroll, the speed ring does not turn, and there is no impact ring.
- **Robot:** no flinch, head shake, blink, wheel spin or lidar spin. The eyes still switch colour, as a static state for the dwell.
- **People and props:** no idle bob or sway, no sleeper bubbles, no lock bob. The knife glint becomes a static highlight.
- **Motion itself:** the robot is shown at its end pose with the executed ribbon fully drawn, and the ghost the same way (§9). The tag peels instantly.
- Render-on-demand then renders only on actual state changes, which is close to zero GPU at rest.

### v3.12 Performance budget

- **Load:**
  - `three.module.js` plus `three.core.js` (about 790 KB local, uncompressed) are dynamically imported only when 3D is chosen, in parallel with WASM.
  - Target: the first 3D frame within 400 ms after the modules load, on a laptop.
  - Until then, `#map-wrap` shows a CSS placeholder (the plinth colour on the background gradient), never a flash of the 2D map.
- **Scene:**
  - at most 160 draw calls and at most 60k triangles (cylinders 16–20 segments, spheres 16×12)
  - 1 shadow-casting light
  - at most 8 `CanvasTexture`s, each at most 256² (about 2 MB of GPU memory)
  - shared geometries; `matrixAutoUpdate = false` on static meshes
- **Render on demand.** A frame is rendered only when one of these holds:
  1. The scene is animating: the clock is inside any timed element's `[t0, t0 + dur + fade]`, a snap is easing, or a ghost is running.
  2. Controls damping or a camera tween is active.
  3. An idle tick is due (blink, bob, sway), capped at 30 fps (20 fps on coarse pointers), and off under reduced motion or when `document.hidden`.
  4. A resize, theme change, policy apply or lab pointer move happened.
  - The director's rAF loop still calls `render()` every frame; `Stage3D` returns early when nothing is dirty. A paused, static scene renders at 0 fps.
- **Shadow map:** updated only on frames where a caster moved.
- **Offscreen:** an `IntersectionObserver` stops rendering while the stage is off-screen (for example, when mobile scrolls to the card list).
- **Main-thread JS:** at most 2 ms per frame on a laptop, with no allocations in the loop (reused `Vector3` / `Matrix4` scratch objects). Overlay relayout runs at most once per frame, only on `onView` or resize.
- **Adaptive degrade:** if the median frame time exceeds 40 ms over 3 s, shadows go first (`castShadow` off, receivers keep the contact shadow), then the pixel ratio drops to 1. This is logged to the console only.

### v3.13 Disposal

- **`Stage3D.dispose()`:**
  - cancels any pending frame request
  - calls `controls.dispose()`
  - removes the wheel, pointer, key, resize and `matchMedia` listeners
  - disconnects the `ResizeObserver` and `IntersectionObserver`
  - traverses the scene and calls `geometry.dispose()`, `material.dispose()` for every cached material, and `texture.dispose()` for every canvas texture
  - removes every CSS2D object from its parent and deletes the label layer element
  - calls `renderer.dispose()`
  - calls `renderer.forceContextLoss()` only on the swap path, so the replacement canvas is clean
- **Per-beat effects are pooled:** one barrier, one dome, one arch, 6 rings, 3 ribbons and 1 speed ring are reused. Ribbon geometries are rebuilt per path, and the old `BufferGeometry` is disposed immediately.
- **Theme and policy changes** update colours and decals in place. Zone decals are rebuilt only on policy apply, and their old geometries and textures are disposed.

### v3.14 Additions to the §10 invariants

8. `scene3d.js`, `actors3d.js`, `fx3d.js` and `geom3d.js` import nothing from `engine.js` and never call `judge(`. They read Decision data only through `scene.fx.decision` and `scene.exec`, which the director copied from the frozen Decision.
9. In 3D, verdict colour tokens are used only by `fx3d.verdict()`, by the rings and flashes derived from `highlightsFor(decision, …)`, by the executed ribbon, and by the badge rim after a verdict. The ghost and its path use `--intent` only. Decor never uses a verdict colour.
10. No text is rendered in WebGL; every word is DOM. The QR tag is a pattern, and the badges are shapes.
11. No Han code points exist in shipped sources or in the rendered DOM (`hanScan()` in `?selftest`).
12. The site is still static and offline. `three` comes from `sim/vendor` through the import map. There are no model files, no fonts and no network requests. `sim/vendor`, `sim/pkg` and `sim/examples` are never edited.

### v3.15 Small screens (layout pass)

Checked at 800×600, 1024×768, 768×1024, 667×375, 375×812 and 360×640 (intro, and cards 1, 3 and 5 at the verdict moment); 1280×800 and wider are unchanged.

- **Map shape (3D).** Beside the rail at 720–1099 px, a height-limited map may run up to **16:10** (it was 4:3). The house is height-fitted either way, so the extra width is side room for the toolbar and the tags. Below 720 px the map is **6:5** (it was 1:1): the house is width-fitted either way, and the saved height keeps the intro button, the caption and the primary button on the first screen. The 2D map stays square everywhere.
- **Toolbar keep-out.** The overlay reports the toolbar's rect to `stage.setReserve(rects)` (map px). `computeFit` first fits and centres the plinth as before; if the projected hull of its 8 corners would touch a reserved rect, it shrinks the house (down to 0.6×) and slides it on screen to the largest placement that is clear, nearest the centre first. `resize`, `setReserve` and `showIntro` put a camera that sits at the default framing back on it; a camera on its way home (a `home` tween from ⌂, `showIntro`, card start or the end card) counts as home, and its tween is retargeted to the new fit, so a keep-out or resize that lands mid-tween on first load is not lost. The keep-out applies up to 1099 px only; wider, the framing is as before v3.15. Beat framing is unchanged. The toolbar is compact (⟳ and ⌂ only) when the map is under 440 px wide **or under 340 px tall**.
- **Lab drawer.** At 720–1099 px the open drawer (340 px) takes the rail's column, so it sits beside the stage and never over the map, the toolbar or the caption. In short landscape the drawer is `min(320px, 48vw)` wide and the stage left of it stacks the map over the caption (title and strip hidden). Below 720 px it stays a bottom sheet.
- **Intro chips.** The legend chips never break inside a word; on a stage under ~470 px (portrait tablet) the intro is one column with the button full width. Phones under 700 px tall trim the intro's spacing so its links stay on the first screen.
- **Strip, short landscape.** The speed-cap chip is 10 px and the gate-to-motor link is at least as wide as it, so it no longer runs into 해태 or the motor.
- **Labels.** The toolbar joins the label-priority pass as a priority-2 box, so zone chips, room labels and person tags under it step aside instead of being covered. When the map is under 560 px wide, zone chips drop the zone name: **0.3 m/s**, **출입 금지** (the room label names the room).
- **Intro, 720–1099 px.** The stage title is hidden in the intro (the intro's own heading replaces it); the card puts the legend chips and the two links left of the button. The map gets `100dvh − 300px` of height (it was `− 430px`). Below 720 px the stage title is hidden in the intro as well.
- **Short landscape (≤ 1099 × 500 px, landscape).** The rail moves below. The stage becomes two columns: the map on the left at the full stage height (up to 4:3), and on the right the title, a narrow strip (the motor keeps its icon; a flagged source shows only its flag; the ×배속 badge wraps to a second line), the caption (clamped to 3 lines; the full sentence stays in the live region and in 자세히) and the transport. The intro card and the end card take the right column. Sheets come in from the side.

