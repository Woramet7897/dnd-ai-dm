## 22. [v2 NEW] Time & Supply System (Day/Night Cycle, Rations)
*(Insert this section before the current "22. Manual Verification Plan", which becomes Section 23.)*

**Core rule, same as everywhere else in this spec: Python owns the clock, the LLM only narrates around it.**
Time never advances because the LLM decided narratively that time passed — it advances only through a fixed,
deterministic trigger that Python counts. The extraction call has no field for time and never should.

### 22a. `game_time` — new field on `world_saves/<n>_world.json`
```json
{
  "game_time": {
    "day": 1,
    "period": "morning",
    "steps_since_period_start": 0
  }
}
```
`period` is a fixed vocabulary, same pattern as `CONDITIONS` in Section 20a — never LLM-decided, never
free text:
```python
DAY_PERIODS = ["morning", "afternoon", "evening", "night"]
STEPS_PER_PERIOD = 4   # tune by playtesting; not a magic constant the LLM ever sees or reasons about
```

### 22b. Deterministic time advance — `dungeon_manager.py`
Add `advance_time(world_state, steps=1) -> dict` (returns the updated `game_time` dict, and a
`period_changed: bool` flag so callers know whether to trigger 22c/22d effects):
- Called from inside `move_player()` on every successful move (one call = one step). Not called on failed
  moves (blocked exit, invalid direction) — no time cost for a no-op.
- Increments `steps_since_period_start`. When it reaches `STEPS_PER_PERIOD`, reset to 0 and advance
  `period` to the next entry in `DAY_PERIODS`, wrapping `night → morning` and incrementing `day` by 1 on
  that wrap.
- Combat does **not** advance time per round — a fight is assumed to resolve within the same period it
  started in. (If a future phase wants multi-period sieges, that's an explicit extension, not implied here.)

### 22c. Long rest = deterministic jump, not simulated
When the player rests in town (existing rest/shop flow, Section 19), Python sets `game_time` directly:
`period = "morning"`, `day += 1`, `steps_since_period_start = 0`. This is a hard overwrite, not N calls to
`advance_time()` — resting doesn't "walk through the night," it skips to the next morning outright.

### 22d. Rations — new consumable subtype, `item_catalog.json`
Add a `"ration"` type alongside the existing `"consumable"`/`"wearable"`/`"weapon"`/`"tool"` types (Section 7a):
```json
"trail_rations": {
  "name": "Trail Rations",
  "type": "ration",
  "value_gold": 2,
  "description": "Dried meat, hardtack, and a waterskin — enough to keep you going for a day."
}
```
- Consumption is automatic and Python-driven, **not** an LLM or player action: on every `period_changed == True`
  event from `advance_time()`, **while the player is outside town** (check `is_room_safe`/room type per
  Section 6), `state_manager` decrements one `"ration"`-type item from inventory.
- If none are available to consume: apply the new `"exhausted"` condition (see 22e) instead. No HP damage,
  no death spiral — this is meant to be a soft push back toward town, not a punishing survival mechanic.
- Rations are never consumed while `current_location` is a `"town"`-type room (Section 6) — assume the
  player eats normally in town, no bookkeeping needed there.

### 22e. New condition — extends Section 20a, does not replace it
```python
CONDITIONS = ["prone", "poisoned", "stunned", "restrained", "frightened", "exhausted"]

CONDITION_EFFECTS = {
    # ...existing four unchanged...
    "exhausted": {"attack_rolls_disadvantage": True, "ability_checks_disadvantage": True},
}
```
Applied by `state_manager` directly when ration consumption fails (22d) — same mechanism as a monster's
`applies_condition` field (Section 9a), just triggered by supply state instead of a hit. Cleared automatically
the next time the player successfully eats (ration consumed, or returns to town and rests per 22c).

### 22f. Shop hours — extends Section 19a, does not replace it
Add an optional `"open_periods"` array to `shop_catalog.json` entries (omit the field entirely on a shop to
keep it always-open, so existing catalog entries need no migration):
```json
"blacksmith": {
  "...": "unchanged",
  "open_periods": ["morning", "afternoon", "evening"]
}
```
If `world_state["game_time"]["period"]` is not in a shop's `open_periods`, the shop's buy/sell functions
(Section 19b) return a rejection reason ("The smithy is closed for the night.") instead of processing the
transaction — same validation-gate pattern as everything else the LLM might attempt.

### 22g. What the LLM actually sees and does
- The narrative system prompt's context injection (Section 12a/12c pattern) gets one more line each turn:
  `Time: Day {day}, {period}.` That's it — pure read, same as it already reads HP/location.
  Also inject `steps_since_period_start` is NOT surfaced to the LLM — it's an internal counter, not narrative-
  relevant.
- The LLM's only actual job related to this system: describe the scene consistently with the given period
  (torchlight vs. daylight, a closed shop's shutters, feeling hungry when `"exhausted"` is in
  `active_conditions`). It never sets, increments, or reasons about `game_time` itself, and the extraction
  call schema (Section 12b) gets no new field for it — there is nothing here for the LLM to get wrong,
  by construction.

### 22h. Known limitation, accepted for v1 (matches the framing of Section 21b)
`STEPS_PER_PERIOD` is a single global constant — travel doesn't take longer through wilderness than through
a dungeon corridor, and there's no weather/seasons layer. This is intentionally simple; the goal is "the
world has a pulse and a reason to carry rations," not a full survival simulation.
