# BUILD_ORDER.md — Phased Build Plan for Vibe Coding (resumable across sessions)

Give this file to the coding AI **together with** `dnd_upgrade_prompt_v2.md` and `DND_AI_DM_full_spec_v2.md`.
Its only job is to say **what order to build in** and **how to resume cleanly if a session ends mid-way**
(token limit hit, context reset, new chat). The full spec never changes — this file just sequences it.

---

## RULE 0 — Read this before doing anything else, every single session
1. Check if `PROGRESS.md` exists in the project root.
   - **If it doesn't exist:** this is session 1. Create it now using the template in "PROGRESS.md template"
     below, then start Phase 0.
   - **If it exists:** read it. It tells you exactly which phase is done, which is in-progress, and what the
     very last completed step was. **Resume from there — do not restart earlier phases, do not re-generate
     files marked `DONE`.**
2. Work on **exactly one phase at a time**. Do not start the next phase's files until the current phase's
   "Definition of Done" checklist is fully checked and `PROGRESS.md` is updated to reflect it.
3. **After finishing each phase** (not each file — each whole phase), update `PROGRESS.md` immediately:
   mark it `DONE`, note the exact filenames written, note any deviation from spec (see Rule 2 below), then
   stop and summarize briefly for the user before continuing to the next phase. This is the checkpoint a new
   session will resume from if this one ends here.
4. If a phase is only partially done when the session ends (mid-file, ran out of room), mark it
   `IN_PROGRESS` in `PROGRESS.md` with a note of exactly what's left, rather than leaving it unmarked. An
   unmarked phase is ambiguous to resume from; a marked in-progress one isn't.

---

## RULE 1 — One phase = one working, testable unit
Never write code for Phase N+1 before Phase N's Definition of Done passes. Each phase should leave the
project in a state that runs without crashing, even if incomplete overall — never leave things half-wired
between files at a phase boundary.

## RULE 2 — Deviations get logged, not silently made
If a phase can't be completed exactly as the spec describes (a function needs a different signature, a
schema field needs adjusting), write it anyway, but add a one-line note under that phase in `PROGRESS.md`
explaining what changed and why. A resuming session must never have to guess whether something was changed
on purpose.

## RULE 3 — Catalog files (JSON) are cheap; write them generously
Static JSON catalog files cost very little context to write and review. When in doubt in early phases, add
a few extra races/items/spells rather than the bare minimum — it's cheap now and expensive to come back to
later once code depends on the catalog shape.

---

## PHASE 0 — Project skeleton + all static catalogs
**No code logic yet, no Ollama calls needed to test this phase.**

Files: `requirements.txt`, folder structure (`saves/`, `world_saves/`, `save_backups/`, `db/`), and every
catalog file: `races_catalog.json`, `classes_catalog.json`, `backgrounds_catalog.json` (with `starting_gold`),
`item_catalog.json` (with `value_gold`), `spell_catalog.json`, `shop_catalog.json`, `monster_catalog.json`
(with `xp_value`, optional `gold_drop`, optional `applies_condition`), `dungeon_data.json` (hub, with `shops`
field referencing `shop_catalog.json` IDs).

**Definition of Done:**
- [ ] All JSON files are valid JSON (parse-test each one).
- [ ] Every cross-reference between catalogs resolves — e.g. every `shops` ID in `dungeon_data.json` exists
      in `shop_catalog.json`; every `sell_items` ID in `shop_catalog.json` exists in `item_catalog.json`;
      every class's starting spell (if any) exists in `spell_catalog.json`.
- [ ] At least 5 races, 4 classes, 4 backgrounds, 10 items, 8 spells (spread across at least 2 casting
      classes), 2 shops, 4 monsters exist.

*Why this phase first:* zero risk, zero LLM cost to verify, and every later phase depends on these shapes
existing and being internally consistent. Cheapest possible phase to get right before spending tokens on logic.

---

## PHASE 1 — `character_creator.py` + `validation.py`
Files: `character_creator.py` (point-buy, derived stats), `validation.py` (all sub-validators from PART 5b of
the upgrade prompt).

**Definition of Done:**
- [ ] `validate_point_buy()` correctly accepts a valid 27-point allocation and rejects an invalid one with a
      specific error message (test both cases manually).
- [ ] Each `validation.py` function tested standalone with deliberately malformed input (unknown item_id,
      out-of-vocabulary action_tag, absurd hp_change, invalid npc_id) — confirm each is dropped individually,
      not the whole payload.

*No Ollama call needed to test this phase — pure Python, run it directly.*

---

## PHASE 2 — `state_manager.py` core math (no XP/shop/spell yet)
Files: `state_manager.py` — but **only** `get_modifier`, `is_proficient`, `resolve_check` (with the
difficulty enum, not raw DC), `resolve_death_save`, `resolve_concentration_check`, `apply_state_updates`,
`log_roll`. Leave `award_xp`/`check_level_up`/`buy_item`/`sell_item`/`resolve_spell_save`/
`resolve_downed_outcome`/`dismiss_companion` as empty stub functions with a `# PHASE 6+` comment — don't
implement them yet, just make sure nothing else breaks importing this file.

**Definition of Done:**
- [ ] `resolve_check()` takes a difficulty string (`"easy"|"medium"|"hard"|"very_hard"`), maps it internally
      to a DC, and returns a dict with roll/modifier/total/success/critical/fumble — confirm nat 20 always
      succeeds, nat 1 always fails, regardless of modifiers.
- [ ] `apply_state_updates()` correctly routes an hp_change and confirms `resolve_concentration_check` fires
      automatically when `hp_change < 0` and `concentration` is set.
- [ ] All of this tested with plain Python calls — no Streamlit, no Ollama.

---

## PHASE 3 — `dungeon_manager.py`
Files: `dungeon_manager.py` — `get_current_room`, `move_player`, `register_new_location`,
`mark_room_cleared`, `get_room_loot`. Skip world-event flag injection (optional, Phase 10).

**Definition of Done:**
- [ ] Player can move between two hand-authored rooms in `dungeon_data.json` and `current_location` updates
      correctly in a test world-save dict.
- [ ] `register_new_location()` rejects a duplicate ID and rejects a `connects_to` pointing at a nonexistent
      location.

---

## PHASE 4 — `combat_manager.py` (round-based, no status effects yet)
Files: `combat_manager.py` — `start_combat` (with the idempotency guard as its first line), `roll_initiative`,
`resolve_attack`, `roll_damage`, `resolve_companion_turn`, `resolve_enemy_turn`, `advance_turn`,
`check_combat_end`, `classify_round_significance`. Skip `apply_condition`/`tick_conditions` (Phase 9).

**Definition of Done — this is the single most important test in the whole project:**
- [ ] Simulate a fight with 2 companions + 2 enemies for 3 rounds using dummy/scripted combatants (no LLM
      involved yet) and confirm `classify_round_significance()` correctly separates a forced critical hit
      and a forced kill into "significant," while normal hits go to "routine."
- [ ] Confirm calling `start_combat()` a second time while `combat_state` is already active is a no-op (the
      idempotency guard) — this is the fix from the last spec revision, test it explicitly.
- [ ] `check_combat_end()` correctly returns `"victory"`/`"defeat"`/`None` at the right points.

*Do not proceed to Phase 5 until this phase's round-batching behavior is verified — everything about combat
performance depends on getting this right before any LLM call touches it.*

---

## PHASE 5 — `memory_manager.py`
Files: `memory_manager.py` — ChromaDB client setup, short-term window, minor/major lore summarization,
`get_relevant_lore()`. Skip the optional session-recap feature (Phase 10).

**Definition of Done:**
- [ ] ChromaDB persistent client initializes without error and survives a script restart (data persists in `db/`).
- [ ] `get_relevant_lore()` never returns more than 3 entries total regardless of how many are stored.

---

## PHASE 6 — `llm_handler.py`
Files: `llm_handler.py` — system prompt tiers (0-4), narrative call, extraction call, token counting +
budget enforcement, result injection formats. **This is the first phase that needs Ollama running.**

**Before writing this phase:** confirm which model(s) to use per the model-swap decision — default to the
SAME model for both narrative and extraction calls unless a benchmark says otherwise (don't split by default).

**Definition of Done:**
- [ ] A single narrative call streams back plain text with no JSON leakage.
- [ ] A single extraction call reliably returns parseable JSON on a handful of test narratives — if it
      doesn't, note this in `PROGRESS.md` and consider adjusting the extraction prompt before moving on.
- [ ] Token counting logs how many tokens each tier (0-4) actually costs on a real system prompt — this
      number is needed to sanity-check the budget in Phase 7.
- [ ] Feed a deliberately-too-long assembled prompt through the budget function and confirm it drops tier 4
      (then 3, then 2) before ever touching tier 0/1.

---

## PHASE 7 — `app.py` core loop (creation + exploration + basic combat UI)
Wire everything from Phases 0-6 together: landing screen, character creation flow, playing mode with
sidebar, movement, the roll button, the always-available "⚔️ Attack" button, the combat panel showing one
narration per round, autosave with atomic writes + rolling backup rotation.

**Do NOT include yet:** shop panel, level-up popup, spell casting UI, status effect display, dismiss button.
These come in later phases. `state_manager.py`'s stub functions from Phase 2 can stay stubbed for now.

**Definition of Done — the real end-to-end test:**
- [ ] Full run: create a character → explore → trigger combat via the manual button → fight 2-3 rounds →
      confirm exactly one narrative call happens per round, not per action → win or get downed → confirm a
      downed result doesn't hard-crash (even a placeholder message is fine here; full downed-outcome logic
      is Phase 8) → close the app → reopen → load the save → confirm everything persisted correctly.
- [ ] Check the debug log: confirm `eval_duration` stays roughly stable turn to turn, not growing — this
      is the real proof the context budget is working, not just a number that was set once and forgotten.

**⚠️ This phase is the natural "MVP is playable" milestone.** If a session has to stop somewhere and not
come back for a while, this is the best possible stopping point — everything after this is additive.

---

## PHASE 8 — Death/downed-outcome system (Kenshi-lite)
Implement `resolve_downed_outcome()` and the captive/escape sub-loop in `state_manager.py`, wire it into the
combat flow from Phase 4/7 in place of the placeholder.

**Definition of Done:**
- [ ] Force a combatant to 0 HP + 3 failed death saves in a test → confirm one of the three outcomes fires,
      game state remains valid, and there is no game-over screen anywhere in the code path.
- [ ] If `captured` fires: confirm the escape check works and both success/failure are handled.

---

## PHASE 9 — Remaining optional systems, each independent, build in any order, skip any without risk
Each of these only touches its own files/functions plus `app.py` UI additions — none of them depend on each
other. Do them one at a time, one Definition of Done per system, in whatever order the user prefers:

- **XP/Leveling** (`award_xp`, `check_level_up`, `apply_level_up` + level-up popup UI). Done when: crossing
  an XP threshold in a test correctly increases HP max/proficiency/spell slots.
- **Shop/Economy** (`buy_item`, `sell_item` + shop panel UI). Done when: buying then selling the same item in
  a test nets a gold loss (never a gain) confirming the multipliers are applied correctly.
- **Spellcasting** (`resolve_spell_save` + spell UI). Done when: one attack-roll spell and one save-forcing
  spell both resolve correctly in a test combat round, and the save-forcing one's DC comes from the
  *caster's* stats while the *target* rolls.
- **Status effects** (`apply_condition`, `tick_conditions` + condition display UI). Done when: applying a
  condition in a test affects the very next roll as expected and expires exactly on schedule.
- **Companion dismissal** (`dismiss_companion` + dismiss button UI). Done when: a dismissed companion appears
  in `former_companions`, not deleted, and is recruitable again at their last location in a test.

---

## PHASE 10 — Optional polish (cut first if ever short on time/tokens)
- World event flags (Section 6d of the full spec).
- Session recap on load (Section 14a of the full spec).

---

## PROGRESS.md template
Create this file at the project root during Phase 0 if it doesn't exist yet. Update it after every phase.

```markdown
# PROGRESS.md — do not delete, read this first every session

Last updated: <date/time you finish updating this>

| Phase | Status | Files written | Notes/deviations |
|---|---|---|---|
| 0 - Skeleton + catalogs | NOT_STARTED | | |
| 1 - character_creator + validation | NOT_STARTED | | |
| 2 - state_manager core | NOT_STARTED | | |
| 3 - dungeon_manager | NOT_STARTED | | |
| 4 - combat_manager | NOT_STARTED | | |
| 5 - memory_manager | NOT_STARTED | | |
| 6 - llm_handler | NOT_STARTED | | |
| 7 - app.py core loop (MVP milestone) | NOT_STARTED | | |
| 8 - Death/downed outcome | NOT_STARTED | | |
| 9 - XP/Leveling | NOT_STARTED | | |
| 9 - Shop/Economy | NOT_STARTED | | |
| 9 - Spellcasting | NOT_STARTED | | |
| 9 - Status effects | NOT_STARTED | | |
| 9 - Companion dismissal | NOT_STARTED | | |
| 10 - World events (optional) | NOT_STARTED | | |
| 10 - Session recap (optional) | NOT_STARTED | | |

Status values to use: NOT_STARTED / IN_PROGRESS / DONE

## If IN_PROGRESS when a session ends, note exactly what's left here:
(overwrite this line with specifics, e.g. "Phase 6: narrative call works, extraction call prompt still
needs writing, token counting not started yet")
```

---

## Why this ordering specifically
Phases 0-6 build every foundation piece standalone and testable without Streamlit or a full game loop —
cheapest to verify, cheapest to fix if wrong. Phase 7 is the first point everything is wired together and is
a genuine "playable MVP" checkpoint — deliberately called out above as the safest place to pause for a long
time if needed. Phase 8 (death system) comes before Phase 9's optional systems because it's the
tone-defining feature discussed earlier, not because it's technically required before the others. Phase 9's
five systems are ordered by convenience only, not dependency — skip, reorder, or drop any of them freely.
Phase 10 is pure polish, cut without a second thought if short on time or tokens.
