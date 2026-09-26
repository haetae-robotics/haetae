# Haetae Simulator Redesign: final spec, "해태가 막는다"

The simulator is rebuilt as an attack demo. The map is the stage. A visitor picks an attack card, watches an untrusted AI command reach the robot, and sees the real Rust gate (running as WASM) stamp its verdict on the map, at the spot where the action was stopped.

**Base design and grafts.** The judges' winner, "clarity", is the base. Grafted in:
- From "story": the colour grammar, the seal anchoring, the 不 drama, the Haetae badge, the time-compression badge, the verdict-picked outros, the mode ladder, the end card and filmstrip, the hazard-framed counterfactual, and the transcript.
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
| P5 | **It explains itself in 10 seconds.** A real verdict plays before the first click. There is a one-button intro, and the pipeline strip "AI 모델 → 門 해태 → 모터" states the idea permanently. | 4 사용법 모름 |
| P6 | **The colour grammar never changes.** **Violet** always means the model's raw, untrusted intent. **Jade / ochre / cinnabar** (允 / 節 / 不) always mean the gate's decision. A verdict colour, glyph or seal never appears unless it came from a `Decision` returned by `sim.judge()`. | HARD RULE |
| P7 | **The narration never predicts.** Narrator lines describe only the threat. Every post-verdict sentence and outro is chosen by the verdict the gate actually returned, so edited policies never make the story lie. | HARD RULE |

---

## 1. Core loop and the first 10 seconds

### 1a. Loop
Pick a card → the card plays 1–3 beats (command → gate → seal → execution) → optionally press **해태 없이 보기** after a 不/節 beat → an end card appears → the primary button becomes **다음 공격: {title} →**.

### 1b. First 10 seconds

| t | What happens |
|---|---|
| 0.0 s | The page renders with the house already alive: the robot at the table **holding the knife** and the child playing at (2, 6). This is card 1's opening world. The pipeline strip reads `AI 모델 ──▶ 門 해태 ──▶ 모터`. The intro card is on screen: bottom-right of the map on desktop, in the transport slot under the map on mobile. |
| 0.4 s | **Attract teaser, once per page load, muted.** A violet dashed intent path draws from the robot toward (2.5, 5.2). Then a **real** `judge()` call runs on a throwaway `Gate` built from the default policy, using card 1 beat 1 data exactly. The returned seal lands on the map using the normal seal code, and the callout appears. A small tag under the seal reads **"실제 판정"**. If the call throws, the teaser is skipped silently. |
| 3.4 s | The teaser seal and path fade (400 ms). The CTA pulses once (not under reduced motion). |
| any time | **▶ 첫 공격 보기 — 칼 든 로봇** fades the intro (200 ms) and starts card 1. The first real 不 lands about 1.8 s later (beat timeline §3f). Focus stays on the transport's primary button, which is now **다음 ▶**. |

Intro card content (copy is final):
- H1: **AI가 해킹당해 위험한 명령을 내리면?**
- Body: 해태는 AI가 로봇에게 내린 모든 명령을 **모터에 닿기 전에** 심사합니다. 옳고 그름을 가리는 상상의 동물, 해태처럼.
- Legend chips: `允 통과` `節 줄임` `不 막음`
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
| S2 beat revealed | 다음 ▶, with a countdown ring when auto-advance is on | 해태 없이 보기 (only after a 不 or 節 beat) · ❚❚ |
| S3 unfiltered replay | 해태 켜고 돌아가기 | none |
| S4 card done | 다음 공격: {next title} →. After card 8: ↻ 처음부터 | ↻ 다시 · 해태 없이 다시 보기 (if the card had a 不/節 key beat) · 운영자 리셋 (card 4 only) |
| S5 lab open | none in the transport. Clicking or pressing Enter on the map sends a command. | lab tabs |

The primary button keeps its position through S1 → S4, so a keyboard user never has to hunt for it.

---

## 2. Layout

The canvas shows world x, y ∈ [−0.4, 10.4], so the canvas is square. The DOM overlay (seal, callout, slip, rings' labels) is positioned with the same world→screen transform and may overflow the canvas into the stage padding.

### 2a. Desktop, about 1280×800 (≥1100 px wide)

Vertical budget: header 56, strip 44, map 580, transport 76, padding 44.

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ [獬] 해태  AI의 로봇 명령을 모터 앞에서 심사합니다     允 통과  節 줄임  不 막음   [?] [보기 ◐] [⚙ 실험실] │ 56
├────────────────────────────────────────────────────────────────────┬─────────────────────┤
│ 01 칼 든 로봇, 아이에게 · 1/3                                       │ 공격 고르기   본 2/8 │
│ (◇ VLA 모델) ━━━●━━▶ [門 해태] ━━━━━━━✕━━ (⚙ 모터)       ×1 배속    │┌───────────────────┐│
│          ┌─────────────────── map 580×580 ───────────────────┐    ││▍1 칼 든 로봇,     ││
│          │ 주방·식당            ┊복도┊   거실                 │    ││   아이에게  [不 允]✓││
│          │                      ┊░░░░┊            ┌아이 방──┐ │    ││ 해킹된 AI가 칼 든… ││
│          │   ┌명령서 (violet)──┐┊0.3 ┊            │╱출입 금지│ │    │└───────────────────┘│
│          │   │◇ VLA 모델        │┊m/s ┊            │╱╱ 🔒 ╱╱╱│ │    │  2 가짜 태그   스푸핑 │
│          │   │"아이에게 칼을…"  │┊░░░░┊            └────────┘ │    │  3 복도 질주     과속 │
│          │   │이동 (2.5,5.2)    │┊    ┊                       │    │  4 센서 고장     고장 │
│          │   │0.8 m/s     [不]  │┊    ┊                       │    │  5 탈취된 옆집 로봇   │
│          │   └──────────────────┘                              │    │  6 흐린 눈, 멈춘 눈   │
│          │  (아이)  ╭╌╌ 1.5 m ╌╮     ╭────────────────╮        │    │  7 녹화 명령 재전송   │
│          │     ╰╌╌╌┃▌不▐┃╌╌╌╌╌╌╌─────│不 막음           │        │    │  8 저녁 파티 전체 ★   │
│          │         ╲ (로봇🔪)[식탁]  │칼 들고 아이 곁 불가│        │    │                     │
│          │                           │+1 · 자세히 ▸     │        │    │                     │
│          └───────────────────────────╰────────────────╯───────┘    │                     │
│ ●○○  不 막음 · 칼을 든 채 아이 1.5 m 안으로는 갈 수 없어요.  자세히 ▸   │                     │ 76
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
│ [獬] 해태                  允 節 不        [?] [◐] [⚙]         │ 48
├───────────────────────────────────────────┬──────────────────┤
│ 01 칼 든 로봇 · 1/3                        │ 공격   본 2/8    │
│ (VLA) ━●━▶ [門] ━━✕ (모터)       ×1       │ 1 칼 든 로봇 不允✓│
│   ┌──────── map 404×404 ────────┐         │ 2 가짜 태그       │
│   │ (아이)                       │         │ 3 복도 질주       │
│   │   ┃不┃╌╌  ╭───────────╮      │         │ 4 센서 고장       │
│   │    (로봇) │不 칼 들고   │      │         │ 5 탈취된 옆집 로봇│
│   │           │아이 곁 불가 │      │         │ 6 흐린 눈         │
│   └───────────╰───────────╯──────┘         │ 7 녹화 명령       │
│ ●○○ 不 막음 · 칼을 든 채 아이 1.5 m…  자세히 ▸│ 8 저녁 파티 ★     │
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
│ [獬] 해태      [?] [◐] [⚙]   │ 48
├─────────────────────────────┤
│ 01 칼 든 로봇 · 1/3     ×1   │ 24
│ (VLA)━●━▶[門]━━✕(모터)       │ strip 36
│┌────── map 343×343 ───────┐ │
││ (아이) ╭╌1.5m╌╮           │ │
││    ┃不┃  48px seal         │ │
││     ╲ (로봇🔪)  ✉          │ │  ✉ = slip tab above robot
││  [不 칼 들고 아이 곁 불가]  │ │  label pill only, ≤12 chars
│└──────────────────────────┘ │
│ ●○○                          │
│ 不 막음 · 칼을 든 채 아이     │ caption (verdict sentence)
│ 1.5 m 안으로는 갈 수 없어요.  │
│ 명령 · VLA 모델 → (2.5, 5.2)  │ command line (violet)
│ 이동 0.8 m/s      자세히 ▸   │
│ [      다음 ▶  ◔       ] [❚❚]│ primary 48 px full width
│ [   해태 없이 보기   ]        │ secondary, outlined
├─────────────────────────────┤
│ 공격 고르기          본 2/8  │
│ ┌─────────────────────────┐ │
│ │1 칼 든 로봇, 아이에게 不允✓│ │ 56 px rows, threat as 2nd line
│ │  해킹된 AI가 칼 든 로봇을…│ │
│ └─────────────────────────┘ │
│  … 2–8                      │
└─────────────────────────────┘
```

- On mobile the command slip is never drawn over the map. It becomes the violet "명령 ·" line in the caption area, plus a 20 px ✉ tab above the robot. The ✉ tab is the landing spot for non-spatial 不 seals.
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

The look is hanji paper, ink, and red 인주 for 不.

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
| `--robot-eye` | #0EA5E9 | #38BDF8 | LED eyes (they switch to `--bul-fill` during a 不 reaction) |
| `--child` | #C23A7E | #FF7AB8 | child figure |
| `--adult` | #4C7EA8 | #7FB0DA | adult figure |
| `--intent` | #6D3FD0 | #A78BFA | the model's raw intent: slip, dashed path, packet, ghost robot, unfiltered frame |
| `--yun-fill` / `--yun-fg` | #2E8F5F / #1F6F4B | #3FB57E / #5ED39A | 允 shape / 允 text |
| `--jeol-fill` / `--jeol-fg` | #D08A12 / #8A5600 | #E0A73E / #F2C064 | 節 |
| `--bul-fill` / `--bul-fg` | #B3232A / #A21F26 | #D93A33 / #FF6B66 | 不 |
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
  - A 14 px **Haetae badge** (a square 獬 seal mark) rides on its back. It glows for 300 ms when `judge()` is called.
  - A dotted **gate ring** (r 0.55 m) is drawn when the mode is not normal. It is amber dashed in caution, and solid red with ⏸ at hold or above. The mode comes from `sim.mode()`.
  - Idle: the eyes blink every 3–5 s.
- **Child** (0.45 m): round head, body, a toy in hand, always labelled "아이". Idle bob of ±0.03 m on a 2 s cycle, cosmetic only. When the card lists the child in `focus`, a soft ring pulses twice.
- **Adult** (0.55 m), labelled "어른".
- **No emoji on the canvas**, because rendering varies by OS. Emoji may appear in DOM text.

### 3c. The pipeline strip (DOM, above the map)

`[source icon + name] ━━●━━▶ [門 해태] ━━━━▶ [⚙ 모터]`

| Beat moment | What the strip shows |
|---|---|
| Source | Taken from `proposal.source` (input data): `◇ VLA 모델` / `플래너` / `원격 조작` / `옆집 로봇 ⚠ 미등록`. The ⚠ is violet outlined, since it is input and not a verdict. Icons are inline SVG. |
| Slip time | A violet packet dot travels source → 門 (450 ms). |
| `judge()` called | 門 glows (300 ms). |
| Seal lands | 門 fills with the verdict colour. The 門 → 모터 link then changes by verdict. 允: the link flows in `--yun-fill`. 節: the link flows with a chip showing `≤{decision.speed_cap} m/s`. 不: the link is cut with a cinnabar ✕ and the motor icon dims. |
| Unfiltered replay | 門 is greyed and struck through. A violet bypass line runs straight from the source to 모터. |

### 3d. Intent vs. decision on the map

| Element | Layer | Drawn as |
|---|---|---|
| **명령서 (command slip)** | intent (DOM) | A violet-bordered paper card that flies from the strip's source icon to rest just above the robot (desktop). It shows the source, the model's words (`say`, in quotes), the action text and the requested speed. Card 7 adds a tape tag: `◀◀ 5.0초 전 명령` or `+3.0초 뒤 시각`, computed as `now − timestamp_ms` from the inputs. On mobile it is the ✉ tab plus the caption's "명령 ·" line. |
| **Intent path** | intent | A 2 px violet dashed line (6/6) with an arrowhead from the robot to the target, and a midpoint chip `요청 0.8 m/s`. Grasp and place use the same path (the gate sweeps it too). Stop has no path. |
| **Barrier** (不, spatial) | decision | A 5 px cinnabar bar with brush-stroke ends, drawn across the intent path at the seal anchor. The path beyond the barrier cracks into 4 fading segments (to 30%, 400 ms). |
| **Executed path** | decision | A solid 3 px line in the verdict fill, following `decision.action`. |
| **Robot motion** | decision | It follows `decision.action`: move_to at `decision.action.speed`; grasp/place as a gripper reach at `decision.speed_cap`; stop as a 300 ms brake pulse. **A denied robot never moves or lurches.** |
| **Speed diff** (節) | decision | Three ochre chevrons across the executed path. The slip speed line becomes `~~2.5~~ → 0.2 m/s`, where the new value is `decision.action.speed`. |
| **Brake stack** (節, when 2 or more cap-kind names fired) | decision | A chip beside the callout listing each fired cap with its limit read from the policy, e.g. `최고 속도 1 · 복도 0.3 · 사람 곁 0.2`. The entry equal to `decision.speed_cap` is bold. This is card 3's signature visual. |
| **Ghost robot** | intent | Only in the unfiltered replay (§5). |

**Time compression.**
- A beat's executed motion lasts at most 3.5 s. Let `d` be the path length and `v` the executed speed. Then `k = max(1, (d / v) / 3.5)`.
- The unfiltered replay of the same beat reuses the gated run's `k`. If the gated run was 不, `k` is computed from the raw speed instead. Either way, relative speeds stay truthful.
- When `k > 1`, a `×{k to one decimal} 배속` badge sits in the strip.

### 3e. The seal: the verdict on the map (DOM overlay)

| Verdict | Shape | Content | Size (desktop / 800 / mobile) | Entrance |
|---|---|---|---|---|
| **允 통과** | circle, double ring, `--yun-fill` | 允 | 64 / 56 / 48 px | fade + scale 1.2 → 1, 200 ms. Fades out after 1.5 s. |
| **節 줄임** | rounded octagon, `--jeol-fill` | 節 + `≤{speed_cap}` | same | stamp 1.6 → 1, 220 ms, no shake |
| **不 막음** | square 도장, rotated −6°, slightly rough edge (fixed SVG path), `--bul-fill`, glyph `--seal-ink` | 不 | same | the slam (§3f) |

**Seal anchor.** This is placement only. It reads names from `decision.fired` and geometry from the policy. It never changes or re-derives the verdict, and if the geometry finds nothing it falls back to the default.

- **不**, chosen by the **headline** fired name (§6a):
  1. A `no_entry` zone: the point where the robot → target segment first enters the zone rect (Liang–Barsky `t0`). If the robot starts inside, the robot pose.
  2. A rule whose `when.human_within` exists: the first point along the segment within `distance` of any human of that `class` (quadratic solve). If there is none, the closest point to the nearest human of that class.
  3. `envelope:workspace`: the point where the segment leaves the workspace rect.
  4. `envelope:pose`: the robot.
  5. Anything else (`source:*`, `stale:*`, `invalid:*`, `mode:*`, a rule with no geometry): **stamped onto the command slip** (the ✉ tab on mobile), like stamping a document. The intent path fades entirely, because the command itself never got past the gate.
- **節**: the midpoint of the path, offset 0.5 m perpendicular toward the map centre, because the cap governs the whole motion.
- **允**: the target. For stop, the robot.
- The seal is clamped at least 8 px inside the map rect.
- When the next beat starts, the previous seal shrinks to 60% and stays at 25% opacity, leaving a trail of the card's seals. Trails are cleared at card end.

**Reason callout.** A DOM element, `aria-hidden`, because the live region speaks the same content.
- It attaches to the seal with a 1 px leader line and appears 200 ms after the seal lands.
- **Line 1**: `不 막음`, the verdict word in its `-fg` colour, followed by the **label** (at most 12 characters, §6b).
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
| 300 | The slip flies from the source to above the robot (450 ms, ease-out). The strip packet travels to 門. The caption's "명령 ·" line appears. |
| 750 | The intent path draws (500 ms). |
| 1250 | **`judge(proposal, world, now)` is called** (director, §10). The badge on the robot and 門 glow for 300 ms. The Decision is frozen (`Object.freeze`) and stored. |
| 1550 | **The seal lands.** For **不**: scale 2.2 → 0.94 → 1.0 (180 ms ease-in, 120 ms settle); a 5 px stage shake (160 ms); an ink ring spreads from the seal (400 ms); the barrier drops, or the stamp lands on the slip; the path beyond cracks; the robot's eyes flash red twice with a ±4° "no" wiggle (400 ms); the strip link is cut. **節**: the softer stamp plus chevrons. **允**: a fade-in. |
| 1750 | The rule highlights and the callout appear. |
| 1900 | The caption switches to the verdict sentence, and the live region announces it (§9). |
| 2300 | Execution of `decision.action` at `k` (≤ 3.5 s). For 不 nothing moves. |
| end | Dwell: 2.5 s after 允, 4.5 s after 節 or 不. A countdown ring runs on **다음 ▶** when auto-advance is on; otherwise it waits. |

Beats with no motion (不, stop) last about 6.4 s. The first 不 of card 1 appears about 1.8 s after the click, counting the 200 ms intro fade.

### 3g. End card (DOM, slides up over the lower stage)

The end card holds:
- **Seals row**: the recorded verdict of every beat, as glyph + word, e.g. `不 막음 · 不 막음 · 允 통과`.
- **Outro**: picked from the card's **attack beats** (`key` or `attack`). Take the strictest recorded verdict (不 > 節 > 允) and use `card.outro[verdict]`. If that entry is missing, use `GENERIC_OUTRO[verdict]` (§6d). If any attack beat returned 允, show a neutral ⚠ plus the generic yun line. This can only happen after a policy edit, and it is shown, never hidden.
- **With/without line**, when any beat was not 允: `해태 없이: 모델 명령 {n}개가 모두 그대로 실행 · 해태와 함께: 막음 {b} · 줄임 {j} · 통과 {y}`. The left half counts proposals; the right half is tallied from the recorded Decisions.
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

- **Availability**: only after a beat whose recorded `decision.verdict !== 'yun'`, and on the end card for the `key` beat. A 允 beat would look identical, so it gets no button. Shortcut `H`.
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
  - The strip shows 門 struck through, with a violet bypass line.
- **Allowed on the ghost**: only facts from the proposal and its own position: the requested speed, the destination, the carried object. The authored neutral `raw` sentence goes in the caption with a ⚠ icon in `--ink-2`.
- **Never on the ghost**: a seal, a 允/節/不 glyph or word, a verdict colour, a fired name, a barrier, a callout.
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

The **verdict word** is always `允 통과` / `節 줄임` / `不 막음`, with `yun/jeol/bul` in small mono on the detail sheet. Hanja is wrapped as `<span aria-hidden="true">不</span> 막음`.

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
- The 獬 mark and "해태".
- The pitch "AI의 로봇 명령을 모터 앞에서 심사합니다" (≥1100 px only).
- The legend `允 통과 節 줄임 不 막음` (words ≥1100 px only).
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
  3. "해태 판정: 막음(不). 칼을 든 채 아이 1.5 m 안으로는 갈 수 없어요. 함께 걸린 조건 1개."

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
  - Paths: dashed = intent, solid = executed, chevrons = 節, barrier = 不.
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
  - The robot is drawn at its end pose, with the executed path as a static line. 節 beats keep the `~~2.5~~ → 0.2 m/s` chip, so the difference is still stated.
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
