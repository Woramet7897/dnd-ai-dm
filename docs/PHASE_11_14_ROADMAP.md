# PHASE 11–14 ROADMAP — Sandbox Expansion (BG3 Tactics / Elin Freedom / Kenshi Survival)

Source spec: `DND_SANDBOX_EXPANSION_SPEC.md` (Modules A–D).
Status tracking convention matches `PROGRESS.md`: NOT_STARTED / IN_PROGRESS / DONE.

**Ground rules carried over from Phase 0–10 review process — apply to every
sub-phase below, no exceptions:**
- Coding AI implements; Claude (chat) independently reviews by running real
  files, never rubber-stamping a transcript's reported pass/fail counts.
- Rule of Determinism First: all math/DC/damage/outcome logic is Python.
  The LLM only narrates.
- Context Budget Awareness: only inject prompt context relevant to the
  current room/situation (e.g. no surface-related prompt text if the room
  has no active surface).
- Graceful Failures: any Ollama/LLM failure falls back cleanly, never
  crashes the app.
- Every sub-phase needs a DoD that is actually run and verified, matching
  the rigor used in Phase 0–10 (real trial counts for probabilistic
  systems, call-count assertions not just return-value checks, full
  regression suite re-run after any shared-file edit).

---

## Sequencing decision (already made, see rationale below)

The original expansion spec grouped this work into four large phases
(11–14) matching its four modules (A–D). That grouping was assessed as too
large to hand to the coding AI in single chunks — comparable in total scope
to Phases 0–10 combined. Broken into smaller numbered sub-phases below,
sequenced so that:
- Independent, low-risk systems come first (11.1–12.3).
- The one sub-phase with a known collision risk against existing Phase 8
  work (13.0, Crime/Prison) is flagged and gated on an explicit
  reuse-vs-new-system decision before implementation starts.
- The LLM-orchestration module (D) is last, and its one real architecture
  risk — adding a third per-turn Ollama call on 4GB VRAM — was resolved by
  folding suggestions into the existing narrative call rather than adding a
  new one.

---

## Status

| # | Item | Files (per spec) | Status |
|---|---|---|---|
| 11.1 | Surface system (grease/fire/water/electrified_water combos) | `combat_manager.py`, `state_manager.py`, `spell_catalog.json` | **DONE** |
| 11.2 | Shove action + High Ground | `combat_manager.py` | NOT_STARTED |
| 11.3 | Weapon actions (per-damage-type, short-rest cooldown) | `combat_manager.py` | NOT_STARTED |
| 12.1 | Inspiration points (award/spend, reroll) | `state_manager.py` | NOT_STARTED |
| 12.2 | Cursed/unidentified items | `state_manager.py`, `item_catalog.json` | NOT_STARTED |
| 12.3 | Food spoilage + camp cooking | `state_manager.py` | NOT_STARTED |
| 13.0 | Crime, pickpocketing, prison/escape loop | `state_manager.py`, `dungeon_manager.py` | NOT_STARTED — **gated, see below** |
| 13.5 | Notice board generator | `dungeon_manager.py` | NOT_STARTED |
| 14.1 | Contextual action suggestions (3-button UI) | `llm_handler.py`, `app.py` | **DONE** |
| 14.2 | Camp companion dialogue (approval system) | `llm_handler.py`, `app.py` | NOT_STARTED |

---

## 11.1 — Surface System

**Spec source:** Module A §1.

- Values: `None`, `grease`, `water`, `fire`, `electrified_water`, `blood`.
- `apply_surface(room_id, surface_type, duration=3)`.
- Combos (all deterministic, Python-resolved):
  - `grease` + `fire` → `fire`; every combatant in room rolls DEX save DC 12,
    fail = `2d4` fire damage + `burning` condition.
  - `water` + `lightning` → `electrified_water`; every combatant rolls CON
    save DC 13, fail = `1d6` lightning damage + `shocked/dazed`.
  - `fire` + `water` → `None`, produces `smoke` (ranged attacks vs targets in
    the room get disadvantage).
- Surface duration ticks down by 1 at the end of each combat round; resets
  to `None` at 0.

**DoD:** unit test simulating igniting a `grease`-covered room confirms the
`2d4` combo damage fires correctly; confirm surface expiry after the
specified number of rounds via direct state inspection, not just reading
the code.

---

## 11.2 — Shove & High Ground

**Spec source:** Module A §2.

- `has_high_ground: bool` flag on combatants. While true: +2 attack bonus
  on ranged attacks/spells; enemies shooting back get disadvantage.
- `resolve_shove(attacker, target, room_hazards)`: attacker rolls Athletics
  vs target's higher of Athletics/Acrobatics.
  - Attacker loses: no effect.
  - Attacker wins: choice of (a) target prone (melee attacks vs target get
    advantage), or (b) push 5ft — if room is tagged `near_chasm` or
    `acid_pool`, target rolls DEX save DC 13 or falls (3d6 damage or instant
    death depending on monster size).

**DoD:** unit test confirming a successful shove into a `near_chasm`-tagged
room kills/damages the target as specified; confirm the losing-attacker
no-op path leaves state unchanged.

---

## 11.3 — Weapon Actions (Cooldown-Gated)

**Spec source:** Module A §3.

- Per equipped weapon damage type:
  - Bludgeoning → *Concussive Smash*: on hit, target CON save DC
    `8 + prof + str_mod`, fail = `dazed` (loses reaction, -1 AC, 1 round).
  - Slashing → *Cleave*: hits primary target + STR-mod damage to one
    adjacent enemy.
  - Piercing → *Hamstring*: on hit, target gets `slowed` (movement
    penalty/loses movement advantage).
- Cooldown: `player_state["weapon_actions_available"] = False` until next
  short rest.

**DoD:** unit test per weapon type confirming the correct on-hit effect
fires and that the action is unavailable again until `perform_short_rest()`
resets the flag (this sub-phase can be tested with a stubbed short-rest
flag flip if 12.3's real short-rest function isn't built yet — note the
dependency either way).

---

## 12.1 — Inspiration Points

**Spec source:** Module B §1.

- `award_inspiration(player_state, reason: str)`: cap at
  `max_inspiration` (4), no-op if already at cap.
- `spend_inspiration(player_state) -> bool`: consumes 1 point, enables a
  d20 reroll.
- Award triggers are background-specific (Criminal: successful
  theft/lockpick; Soldier: won a fight without losing a teammate, or a
  Shove assist; Sage: solved an Arcana check) — implementer's exact hook
  points into existing skill-check call sites, confirm each hook actually
  fires by testing it, not just by placing the call.

**DoD:** unit test confirming award respects the cap; confirms
spend/reroll actually changes a roll outcome in a controlled test (mock the
d20 to a known low value pre-reroll, high value post-reroll, confirm the
higher value is what's used).

---

## 12.2 — Cursed / Unidentified Items

**Spec source:** Module B §2.

- New item fields: `identified: bool`, `identify_dc`, `identify_skill`,
  `cursed: bool`, `curse_effect`, `cannot_unequip: bool`.
- `identify_item(player_state, item_id, roll_total) -> bool`: reveals true
  name/properties on success.
- `equip_item()` must be extended (not duplicated) so that equipping an
  unidentified cursed item locks `cannot_unequip = True` until *Remove
  Curse* is cast or the player pays 50 gold at a town cleric — check
  existing `equip_item()`/`unequip_item()` in `state_manager.py` before
  writing this, since Phase 9 already built and tested these functions;
  this must extend them, not fork a parallel equip path.

**DoD:** unit test confirming `unequip_item()` on a cursed+unidentified
item returns a clean rejection rather than unequipping; confirm identifying
it first clears the block; **re-run `phase_equipment_tests.py` in full**
since this directly touches Phase 9's equip/unequip functions.

---

## 12.3 — Food Spoilage & Camp Cooking

**Spec source:** Module B §5.

- `raw_food` items carry `freshness_days`; `advance_time()` (existing
  Phase 7 function) ticks this down and flips the item to `rotten_food`
  when it hits 0 — extend the existing `advance_time()` call site, don't
  add a second time-tick hook.
- `cook_meal(player_state, ingredient_1, ingredient_2)`: Survival check DC
  12 → success creates `Hearty Stew` (+5 temp HP, clears 1 exhaustion
  stack); failure burns the ingredients.
- Cooking with `rotten_food`: CON save DC 13 or `Poisoned` for 8 hours.

**DoD:** unit test advancing time 5 days on a raw-food item confirms it
becomes `rotten_food`; test both cook_meal branches (success/burn) and the
rotten-food poison path.

---

## 13.0 — Crime, Pickpocketing & Prison/Escape Loop — **GATED**

**Spec source:** Module B §3.

**⚠ Before any code is written:** this sub-phase's `imprisoned` /
`prison_days_left` / lockpick-then-stealth escape loop is structurally very
close to Phase 8's already-shipped, already-tested captive/escape system
(`status == "captive"`, single "Attempt Escape" action, DEX check vs DC 16,
wired into `app.py`'s movement UI). Do not let the coding AI build a second,
parallel captive system without an explicit decision here first:

- **Option A (reuse):** Extend Phase 8's existing `status: "captive"` /
  escape-check machinery to also represent imprisonment — e.g. a
  `captivity_reason: "combat_defeat" | "crime"` field distinguishing why the
  player is captive, sharing the same escape-attempt code path in `app.py`
  rather than a second one.
- **Option B (separate):** Build `imprisoned` as genuinely distinct from
  `captive` (different consequences — confiscated items into a specific
  recoverable chest, multi-step lockpick-then-stealth escape vs. Phase 8's
  single check) with a clearly documented reason why one shared system
  doesn't fit both cases.

Whichever option, this decision must be made and written into `PROGRESS.md`
*before* implementation, not discovered after the coding AI has already
built a second system.

- `attempt_pickpocket(player_state, target_npc, item_id)`: Sleight of Hand
  vs NPC passive perception (or DC 12–15). Failure sets
  `is_wanted = True`, `bounty += item_value * 2`, triggers guard
  confrontation (Persuasion DC 15 or combat).
- Escape loop (however Option A/B resolves): Serve Time (skip
  `prison_days_left` via `advance_time()`, pay fine, keep only
  non-stolen items) vs. Lockpick Escape (Sleight of Hand DC 14 → Stealth DC
  13 → recover confiscated items → flee).

**DoD:** unit test confirming a failed pickpocket sets `is_wanted`/`bounty`
correctly; confirm the chosen Option A/B captivity path against Phase 8's
existing `phase8_tests.py` suite — if Option A, `phase8_tests.py` must still
pass unmodified or with only additive changes; if Option B, explain in
`PROGRESS.md` why coexistence with Phase 8's system doesn't cause player
confusion or state conflicts (e.g. can a player somehow be both `captive`
and `imprisoned` at once — this must be explicitly prevented and tested).

---

## 13.5 — Notice Board Generator

**Spec source:** Module C §2.

- `refresh_notice_board(world_state)`: every in-game 3 days, generates
  quests in the tavern room:
  - Bounty (kill a monster in an uncleared room).
  - Item Delivery (deliver herbs/rations to an NPC, 1.5x payout).
  - Rumor (reveals a `hidden_cache: true` flag on a target room).

**DoD:** unit test advancing time past a 3-day boundary confirms new board
entries generate; confirm entries reference only real, existing room IDs
(reuse `dungeon_manager._all_known_room_ids()` from Phase 1/3, don't
hand-roll a second room-existence check).

---

## 14.1 — Contextual Action Suggestions — **DONE**

**Spec source:** Module D §1 (adapted).

**Architecture decision already made:** the original spec called for a
*separate* LLM call per turn to generate 3 suggested actions. Rejected —
this machine's 4GB VRAM already carries one narrative + one extraction
call per turn; a third call was assessed as adding real, avoidable latency.
Instead: suggestions are folded into the existing narrative call's output
as a trailing JSON block, parsed out before the existing JSON-leakage-strip
regex discards it.

- `generate_narrative_response()` prompt extended to request a trailing
  `{"suggestions": [...]}` block (always exactly 3 short strings).
- Malformed/missing block → falls back to spec's fixed defaults:
  `["โจมตีศัตรูที่ใกล้ที่สุด", "ตั้งท่าป้องกัน (Dodge)", "สำรวจหาจุดได้เปรียบ"]`.
- Suggestions rendered as `st.button()` elements beside/above the
  free-text input; clicking one reuses the exact same action-submission
  code path as typing text.
- Decisions implemented & verified:
  (a) Combat rounds: Enabled for both exploration turns and combat rounds
  (tactical suggestions displayed in combat view and refreshed every round).
  (b) Thai-mode handling: Generated directly in Thai by the model via user prompt directive
  (zero added latency, no extra translation call).

**Status:** DONE.
**DoD Verified:** `phase14_1_tests.py` (9/9 passed). Confirms well-formed parsing, zero narrative leakage, fallback to 3 spec defaults, exact-3 padding/truncation, combat round directive inclusion, exception resilience, and app session state initialization. Full regression suite passed (248/248 tests).

---

## 14.2 — Camp Companion Dialogue (Approval System)

**Spec source:** Module D §2.

- New world_state field: `companions_approval: Dict[companion_id, {approval,
  camp_dialogue_pending, last_discussion_topic}]`.
- `generate_camp_dialogue(companion_id, memory_manager, player_state)`:
  pulls recent `get_relevant_lore()` results (existing Phase 5 function,
  don't build a second retrieval path), generates a companion reaction to
  a recent notable event.
- Player given 3 response options (agree/disagree/neutral) → adjusts
  `approval` accordingly.

**DoD:** unit test with a mock LLM client confirming a camp dialogue
prompt is built from real `get_relevant_lore()` output (not hardcoded
filler); confirms each of the 3 response paths adjusts `approval` in the
correct direction; confirms this only triggers in a camp/rest context, not
mid-combat or mid-dungeon-crawl.

---

## Reference: full module-to-file map (for later use)

| Module | Files touched |
|---|---|
| A — Combat Tactics (11.1–11.3) | `combat_manager.py`, `dungeon_data.json` |
| B — Survival/Crime/Items (12.1–13.0) | `state_manager.py`, `item_catalog.json` |
| C — World/Board (13.5) | `dungeon_manager.py` |
| D — LLM Orchestration (14.1–14.2) | `llm_handler.py`, `app.py` |

System directives from the original expansion spec, still binding for every
sub-phase above:
1. **Determinism first** — all numeric outcomes in Python; LLM narrates only.
2. **Context budget awareness** — only inject prompt context relevant to
   the current room/situation.
3. **Graceful failures** — any LLM/Ollama failure falls back to safe
   defaults, never crashes the app.
