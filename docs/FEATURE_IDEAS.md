# FEATURE IDEAS — Post-Phase 14.2 Brainstorm

Status: **PROPOSAL ONLY — not committed, not numbered as a phase yet.**
These are suggestions from the independent reviewer (Claude, chat), offered
after Phase 0–14.2 was fully verified DONE. None of this has a source spec
yet — if any idea is picked, it needs its own mini-spec + gated build prompt
before going to the coding AI, following the same process used for
Phases 11–14.

Each idea below notes which existing system it builds on, so implementation
can reuse current code rather than forking a parallel path (per
BUILD_ORDER.md's "extend, don't fork" rule).

---

## 1. Give Companion Approval (14.2) actual consequences

**Builds on:** `companions_approval: Dict[companion_id, {approval, ...}]`
(already exists, Phase 14.2).

Right now approval is tracked but nothing reads it back. Add threshold
effects:
- Very low approval → companion threatens to leave the party, or steals an
  item and departs.
- Very high approval → unlocks a unique camp dialogue line, or a small
  combat bonus when fighting alongside the player (e.g. advantage on their
  next attack once per rest).

No new data model needed — just new read sites for the existing
`approval` field.

---

## 2. Let Surfaces (11.1) persist outside combat, room-by-room

**Builds on:** `combat_state["room_surface"]` (Phase 11.1) and
`dungeon_data.json` room schema.

Surfaces currently only exist inside `combat_state` and vanish when combat
ends. Add an optional static `surface` field to specific rooms in
`dungeon_data.json` (e.g. a flooded room) so pre-fight preparation matters —
player throws oil down a corridor before pulling a monster into it, BG3-style.
This is the direct "Module A" tactical layer the original roadmap named the
project after.

---

## 3. Faction/Reputation system, layered on `is_wanted`/`bounty` (13.0)

**Builds on:** `is_wanted`, `bounty` (Phase 13.0) and the
`world_event_flags: Dict[location_id, List[str]]` pattern already used by
Phase 10.

Split the current single wanted/bounty flag into per-faction reputation
(e.g. helping a bandit camp lowers reputation with town guards). Reuse the
same `Dict`-keyed-by-entity pattern already proven in Phase 10's world
events — no new architecture, just a new dict shape:
`faction_reputation: Dict[faction_id, int]`.

---

## 4. Lightweight weather/season tied to `advance_time()`

**Builds on:** the existing `advance_time()` hook (Phase 7, extended by
Phase 12.3 for food spoilage).

Cheap to add since the hook point already exists and is proven safe to
extend:
- Rain → automatically clears active `fire` surfaces in outdoor rooms.
- Winter → `freshness_days` on food ticks down faster.

Low implementation cost, meaningfully increases the feeling of a "living
world."

---

## 5. Multi-phase boss fight using existing combat systems

**Builds on:** surfaces (11.1), conditions, weapon-action cooldowns (11.3).

A single boss encounter that changes behavior at HP thresholds — e.g. phase
2 starts self-applying surfaces every round, forcing the player to actually
use the tactical toolkit (shove, surfaces, weapon actions) built across
Phases 11.1–11.3 rather than just trading basic attacks. No new systems
needed, just a scripted monster with phase-triggered Python logic in
`combat_manager.py`.

---

## 6. Light crafting from loot, reusing the `cook_meal()` pattern

**Builds on:** lazy item generation (Phase 9) and `cook_meal()`'s
success/failure-roll structure (Phase 12.3).

Combine two collected materials into upgraded gear via a Survival/
Investigation check, mirroring `cook_meal(player_state, ingredient_1,
ingredient_2)`'s exact shape but with equipment output instead of food.
Same code pattern, different catalog.

---

## 7. Persuasion-based haggling in shops

**Builds on:** `resolve_check(skill="Persuasion")` (already used elsewhere,
e.g. the pending-inspiration-reroll flow) and `_get_shop_catalog()`
(Phase — shop system).

Shop prices are currently fixed; there is no negotiation path at all.
Add a "ต่อรองราคา" (haggle) button before purchase that rolls a Persuasion
check and maps margin-of-success to a small discount (or markup on a
fumble, for flavor). No new data model — just a new call site for the
existing check resolver, feeding its result into the existing price
calculation at point of sale.

---

## 8. NPCs on a schedule, tied to `game_time`

**Builds on:** `game_time: {day, period}` (already read into system prompt
Tier 1 every turn) and each room's static `npcs` list in
`dungeon_data.json`.

NPCs are currently present in their room regardless of time of day. Add an
optional `npcs_by_period` mapping (or a simple per-NPC `active_periods`
field) so a shopkeeper isn't there at night, a guard's post changes at
shift change, and a tavern only fills up in the evening. Reuses the same
room/NPC schema and the game_time value that's already tracked — no new
architecture, just a filter applied when a room's NPC list is resolved.

---

## 9. Companion personal quests, unlocked by approval

**Builds on:** `companions_approval` (Phase 14.2, see item 1 in Part 1) and
`add_quest()` / `quest_log["side"]`, whose collision-safe slug logic
already exists in `state_manager.py`.

Once a companion's approval crosses a threshold, fire a one-time personal
side-quest into `quest_log["side"]` via the existing quest-add path (the
single code path that's allowed to write quest_log — no bypass needed).
Gives approval tracking a concrete payoff beyond a number going up, and
reuses two systems that already work independently.

---

## 10. Room traps/hazards, using the existing condition system

**Builds on:** `combat_manager.py`'s 9-condition set (prone, poisoned,
stunned, restrained, frightened, exhausted, burning, dazed, slowed) and
`apply_condition_to_state()`, currently only exercised inside combat.

Add an optional static `trap` field to specific rooms (same shape of
change as item 2's persistent-surface idea in Part 1) so walking into a
room can require a Perception/Investigation check to notice it and a
Dexterity check to avoid it, applying one of the existing conditions on a
failure. No new condition logic needed — just a new entry point into code
that already exists and is already tested for combat.

---

## 11. A real "rest menu" for short/long rest downtime

**Builds on:** `_maybe_rest_ambush()` (already gates rest with an ambush
roll) and `cook_meal()` (Phase 12.3).

Resting is currently a single button: time passes, HP recovers, ambush
check runs. Add a small menu of optional downtime activities during a
rest — practice (small bonus next encounter), talk with a companion
(nudges approval), cook a meal (calls the existing `cook_meal()`) — so the
rest beat becomes a real choice point instead of a pass-through, while
reusing systems that already work in isolation rather than inventing new
ones.

---

## Next step

Pick one (or a few) of the above, and this reviewer will write:
1. A short mini-spec (values, data model, combo/threshold rules).
2. A gated build prompt for the coding AI, in the same format used for
   Phase 11.1 onward — including explicit DoD tests and a note on any
   assumption that needs to be logged as a deviation in `PROGRESS.md`.

