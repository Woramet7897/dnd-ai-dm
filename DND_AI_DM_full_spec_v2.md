# Offline RAG D&D 5E AI Dungeon Master — Full Project Spec (v2, Consolidated)

Reference doc — read this while building/reviewing. Use `dnd_upgrade_prompt_v2.md` to instruct the coding AI.
This version keeps the entire v1 architecture intact and layers in: (1) four surgical performance/reliability
fixes that are required before build, and (2) five new optional systems (death penalty, XP/leveling,
spellcasting, economy, status effects, companion dismissal). Sections marked **[v2 NEW]** or **[v2 FIX]** are
changes from the original spec. Everything else is unchanged from v1 and already solid — don't rewrite it.

---

## 1. Goal & Target Hardware
A 100% offline, local, solo-player text-RPG Dungeon Master with:
- Persistent long-term memory (RAG via ChromaDB)
- Real D&D 5e mechanics resolved by Python, narrated by a local LLM (BG3-style tone, Kenshi-lite death stakes)
- Character creation, a party of AI companions, relationships, and a real combat engine
- A Streamlit web app UI (not a terminal — real buttons/widgets)

**Hardware:** Intel i7 12th gen, 16GB RAM, RTX 3050 (4GB VRAM). Zero external API calls — everything runs on
`localhost:11434` (Ollama) + local embeddings.

**Design philosophy for this build (not SillyTavern-style RP):** the point of difference from generic
chat-RP tools isn't prose quality — it's a state engine the player can trust completely: numbers never drift,
death never means "conversation over," and the world doesn't freeze waiting for the player. Every new system
in this spec is built to reinforce that, not to add narrative branching for its own sake.

**Token/context reality check:**
- No billing-based token limit — nothing to "run out of" locally.
- There IS a hard context window (`num_ctx`) per model. Overflow = old content silently dropped, not an error.
- Longer context = slower generation, worse on 4GB VRAM with partial CPU offload.
- **[v2 FIX] Section 13 now enforces a real token budget with priority tiers, not just a target number.**

---

## 2. Core Design Principle (the single most important rule)
**Strict division of labor:**
| Layer | Responsibility |
|---|---|
| **LLM (Ollama)** | Narrative text only. Decides *what kind* of check/attack is needed — never the math, never a raw DC, never a raw damage/HP number. |
| **Python** | Owns 100% of the math: dice rolls, modifiers, proficiency, advantage/disadvantage, crit/fumble, death saves, concentration DC, DC selection, combat resolution, item effects, quest state, relationship values, XP/leveling, shop pricing, status effect durations. |

The LLM never sees raw numbers to interpret — it receives already-resolved outcomes and only narrates them.
**[v2 FIX — closes a real gap in v1]:** this now explicitly includes DC. In v1 the LLM's `requires_roll.dc`
field let it pick an arbitrary difficulty number, which quietly violated this exact principle. Section 8 and
12b now remove that field entirely in favor of a fixed difficulty enum.

**Two separate LLM calls per turn outside combat, never combined:**
1. **Narrative call** (streamed) — pure story text, no JSON.
2. **Extraction call** (`format="json"`, not streamed, minimal prompt) — pulls all structured updates out of
   the narrative just generated.

Small local models (this hardware class) reliably break format if asked for prose + JSON together — never
combine them.

**[v2 FIX] Inside combat, this changes — see Section 9 for the new round-based narration model.** Extraction
calls are also **skipped entirely during combat** (v1 didn't specify this clearly): all combat state changes
come from deterministic Python resolution (`resolve_attack`, `roll_damage`), so there is nothing for an
extraction call to usefully pull out of combat narration. Extraction resumes as normal the turn after combat ends.

---

## 3. File Architecture
```
project/
├── setup.py
├── requirements.txt
├── saves/
│   └── <character_name>.json          # one player_state.json per save file
├── save_backups/                       # [v2 NEW] rolling backup, see 5d
│   └── <character_name>/
│       ├── backup_1.json
│       ├── backup_2.json
│       └── backup_3.json
├── world_saves/
│   └── <character_name>_world.json     # dynamic world state per save
├── races_catalog.json                  # static, hand-authored
├── classes_catalog.json                # static, hand-authored
├── backgrounds_catalog.json            # static, hand-authored
├── item_catalog.json                   # static, hand-authored
├── spell_catalog.json                  # [v2 NEW] static, hand-authored, see Section 18
├── shop_catalog.json                   # [v2 NEW] static, hand-authored, see Section 19
├── dungeon_data.json                   # static starting area (hand-authored hub)
├── monster_catalog.json                # static monster stat blocks (now includes xp_value, loot, conditions)
├── state_manager.py                    # ALL D&D math: checks, items, quests, relationships, XP, shops
├── dungeon_manager.py                  # room/town movement, loot, lazy-generated locations, world events
├── combat_manager.py                   # initiative, attack resolution, turn order, status effects
├── memory_manager.py                   # ChromaDB RAG: tiered short/long-term memory
├── llm_handler.py                      # Ollama calls: narrative + extraction, system prompt, token budget
├── character_creator.py                # point-buy validation, derived stat calculation
├── validation.py                       # [v2 NEW] extraction-output schema validation, see Section 13b
├── app.py                              # Streamlit: creation mode + playing mode + game loop
└── db/                                 # ChromaDB persistent storage (auto-created)
```

---

## 4. Character Creation System
Unchanged from v1. Runs entirely in `st.session_state["app_mode"] = "character_creation"` — **zero Ollama
calls** during this phase, pure Streamlit forms + Python validation.

### 4a. Flow (BG3-inspired scope, not full 5e SRD)
1. Name
2. Race (from `races_catalog.json` — Human, Elf, Dwarf, Halfling, Tiefling, Half-Orc, Dragonborn; each with
   stat bonuses + a trait)
3. Class (from `classes_catalog.json` — hit die, starting proficiencies, starting equipment; no subclass
   selection at level 1)
4. Background (from `backgrounds_catalog.json` — grants 2 skill proficiencies + `background_hook` +
   **[v2 NEW] `starting_gold`**, see Section 19)
5. Stat allocation via **Point Buy** (27 points, standard cost table) —
   `validate_point_buy(stats) -> (bool, error_message)`, reject invalid allocations with a specific error.
6. Campaign tone selection (`"serious" | "comedic" | "mature"`) — locked in for the whole save.
7. Confirm → derived stats computed → write to `saves/<n>.json` and `world_saves/<n>_world.json` → switch to
   `"playing"` mode.

### 4b. Point-buy cost table
```python
POINT_BUY_COST = {8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9}
POINT_BUY_BUDGET = 27
```

### 4c. Save file system
Unchanged from v1 — multiple save files, landing screen with "New Character" / "Load Save."

### 4d. Campaign tone
Unchanged from v1 — injected once into the system prompt, never re-guessed turn to turn.

### 4e. [v2 NEW] Level scope decision
**Explicitly scope this build to levels 1–5.** Full 5e goes to 20 with subclass features at nearly every
level — that's out of scope for a solo hand-built catalog. Level 5 is a natural stopping point (most classes
get their signature level-5 feature, e.g. Extra Attack for martial classes — **which is also explicitly
deferred, see Section 17c**). Document this scope decision in the save file so it's never ambiguous later.

---

## 5. Save File Schema

### 5a. `saves/<n>.json` — Character sheet
```json
{
  "schema_version": 4,
  "name": "Star",
  "race": "Half-Elf",
  "class": "Bard",
  "background": "Entertainer",
  "background_hook": "Once performed for a noble court before a scandal forced Star to flee.",
  "level": 1,
  "xp_current": 0,
  "campaign_tone": "comedic",
  "hp": {"current": 10, "max": 10},
  "ac": 12,
  "stats": {"STR": 10, "DEX": 14, "CON": 12, "INT": 10, "WIS": 10, "CHA": 16},
  "proficiency_bonus": 2,
  "proficient_skills": ["Persuasion", "Performance", "Deception"],
  "proficient_saves": ["DEX", "CHA"],
  "spell_slots": {"1": {"max": 2, "current": 2}},
  "known_spells": ["vicious_mockery", "healing_word"],
  "bardic_inspiration": {"max": 2, "current": 2},
  "concentration": null,
  "death_saves": {"success": 0, "fail": 0},
  "status": "normal",
  "active_conditions": [],
  "gold": 15,
  "inventory": [
    {"item_id": "cloak_of_protection", "equipped": true},
    {"item_id": "healing_potion", "equipped": false, "quantity": 1}
  ],
  "roll_log": []
}
```
Changes from v1 schema_version 3: added `xp_current`, `known_spells`, `status`, `active_conditions`, `gold`.
`schema_version` bumped to 4 — `state_manager.py` must refuse to load a mismatched version with a clear error
rather than crashing obscurely (unchanged principle from v1, still critical).

### 5b. `world_saves/<n>_world.json` — World, party, quests, relationships
```json
{
  "schema_version": 4,
  "current_location": "town_riverside",
  "visited_rooms": ["town_riverside"],
  "cleared_rooms": [],
  "locations": { "...": "see Section 6" },
  "quest_log": {"main": [], "side": []},
  "npc_relationships": {},
  "party": {"companions": [], "former_companions": []},
  "combat_state": null,
  "world_event_flags": []
}
```
Changes from v1: `party.former_companions` (Section 21), `world_event_flags` (Section 6d, optional).

### 5c. Save integrity (unchanged from v1)
- **Atomic writes**: temp file + `os.replace()`.
- **Autosave**: after every resolved turn.

### 5d. [v2 NEW] Rolling backup
Keep the last 3 autosaves per character in `save_backups/<name>/`, rotating oldest-out. Purpose: if an
extraction-call glitch corrupts state in a way validation didn't catch, or a schema migration goes wrong,
the player can manually restore from a backup instead of losing the whole save. This is a plain file-copy
operation on save, not a new system — no UI needed beyond an optional "restore backup" button on the landing
screen.

---

## 6. World & Dungeon System (hand-authored hub + lazy generation)
Unchanged from v1 — hand-authored `dungeon_data.json` hub, lazy-generated locations merged into the
per-save world file only, `register_new_location()` rejects duplicate IDs and requires `connects_to` to
reference an existing location.

### 6a–6c
Unchanged from v1. See original spec for full schema and required functions
(`get_current_room`, `move_player`, `register_new_location`, `mark_room_cleared`, `get_room_loot`).

### 6d. [v2 OPTIONAL] Lightweight world events
Not required for v1 — build only if time allows, cut without regret if not.
- On `move_player` or every N turns, small random chance (~10%) rolls a world event flag for a location the
  player is **not currently in** (e.g. `"goblin_camp_grew"`, `"merchant_route_reopened"`).
- Stored as plain strings in `world_event_flags` in the world save — no simulation loop, no faction ledger.
- When the player later visits that location, one line of context is injected into the narrative system
  prompt so the DM can reference it (e.g. "the forest smells of smoke now"). Python decides the flag exists;
  the LLM only narrates around it.
- Purpose: the world feels like it moves without the player, without building an actual simulation engine.

---

## 7. Item System
Unchanged from v1, plus one addition:

### 7a. `item_catalog.json`
```json
{
  "cloak_of_protection": {"name": "Cloak of Protection", "type": "wearable", "slot": "cloak", "effects": {"ac_bonus": 1, "saving_throw_bonus": 1}, "rarity": "uncommon", "value_gold": 25},
  "healing_potion": {"name": "Healing Potion", "type": "consumable", "effects": {"heal": "2d4+2"}, "rarity": "common", "value_gold": 15}
}
```
**[v2 NEW]** every item now has `value_gold` — required for the shop system (Section 19), harmless if unused.

### 7b. Required functions — unchanged from v1
`equip_item`, `unequip_item`, `get_active_effects`, `use_consumable`.

---

## 8. Core D&D Resolution — `state_manager.py`
All pure Python, zero LLM involvement:
- `get_modifier(stat_value) -> int`
- `is_proficient(skill_or_save, state) -> bool`
- `resolve_check(stat, dc, state, advantage=False, disadvantage=False, proficient=False, bonus_dice=None) -> dict`
- `resolve_death_save(state) -> dict`
- `resolve_concentration_check(damage_taken, state) -> dict` — auto-triggered inside `apply_state_updates`.
- `apply_state_updates(updates: dict)` — now validated first, see Section 13b.
- `log_roll(entry: dict)`.

### 8a. [v2 FIX] Difficulty enum replaces raw DC
The LLM never emits a raw DC number. `requires_roll` from the extraction call now only ever contains a
`difficulty` enum, and Python owns the mapping:
```python
DIFFICULTY_TO_DC = {
    "easy": 10,
    "medium": 13,
    "hard": 16,
    "very_hard": 19,
}
```
This mirrors the 5e DMG's own DC guideline table (Easy 10 / Medium 15 / Hard 20 — values adjusted slightly
here to sit better for a level 1–5 scope), so it isn't a deviation from real 5e difficulty *feel*, only a
guardrail against the model inventing arbitrary numbers. `resolve_check()` signature takes `difficulty: str`
instead of `dc: int` at the call site nearest the extraction output; internal dice math still works in raw
DC once translated.

### 8b. [v2 NEW] Save-target spell resolution
A second resolution function is needed for spells that force the *target* to save, rather than the caster
rolling to hit:
- `resolve_spell_save(caster, target, spell, state) -> dict` — DC = `8 + proficiency_bonus + casting_stat_modifier`.
  Target rolls the relevant save. Direction is reversed from a normal check (the *caster's* stats set the DC,
  the *target* rolls) — this is different enough from `resolve_check` that it should be its own function,
  not a parameter flip, to avoid subtle sign-flip bugs.

---

## 9. Combat Engine

### 9a. Combatant schema — unchanged from v1
```json
{
  "id": "goblin_scout_1",
  "name": "Goblin Scout",
  "side": "enemy",
  "hp": {"current": 7, "max": 7},
  "ac": 13,
  "stats": {"STR": 8, "DEX": 14, "CON": 10, "INT": 10, "WIS": 8, "CHA": 8},
  "attacks": [{"name": "Scimitar", "attack_bonus": 4, "damage": "1d6+2", "damage_type": "slashing", "applies_condition": null}],
  "initiative": null,
  "active_conditions": []
}
```
**[v2 NEW]** `active_conditions` added to the combatant shape (Section 20), and attacks may carry a static
`applies_condition` field defined per-monster in the catalog — never decided ad hoc by the LLM.

### 9b. [v2 FIX] Combat flow — round-based narration, not per-action
This is the most important performance fix in this spec. v1 called the narrative model after **every single
combatant's action** — with 3 companions + 3 enemies that's 6+ Ollama calls per round, which is too slow on
4GB VRAM with partial CPU offload.

**New flow:**
1. **Trigger:** unchanged — extraction call sets `combat_start`, or the player presses an always-available
   "Attack!" button as a manual fallback trigger (see 9b-iv below — this doesn't depend on the extraction
   call correctly detecting intent).
2. **Initiative:** unchanged — `roll_initiative(combatants)`.
3. **Whole-round resolution:** Python resolves **the entire round** first — player's action (from button
   press), then every companion's turn (`resolve_companion_turn`), then every enemy's turn
   (`resolve_enemy_turn`) — all pure Python, no LLM calls during resolution.
4. **Single narration pass per round:** after the whole round resolves, classify each individual result by
   significance:
   - **Significant** (always narrated in full): critical hit/fumble, a combatant downed or killed, combat
     start/end, any spell/special item use, HP dropping below 25% for any named combatant.
   - **Routine** (never sent to the LLM): a normal hit or miss with no special outcome.
   Python assembles **one combined result block** for the whole round (all significant events fully described,
   routine events summarized via canned templates — see 9b-ii) and sends **exactly one narrative call** to
   describe the round. This turns 6-8 calls/round into 1.
5. **Death/end conditions:** unchanged from v1 — `hp <= 0` → `"downed"` (Section 17 for what happens next,
   not v1's permadeath), monster removed from turn order. `check_combat_end()` unchanged.
6. **[v2 FIX] No extraction call during combat.** State changes are already fully known from
   `resolve_attack`/`roll_damage`/`resolve_companion_turn`/`resolve_enemy_turn` — there's nothing for an
   extraction call to usefully extract. Resume normal narrative+extraction cycle the turn after
   `check_combat_end()` returns non-null.

   **Accepted tradeoff, confirmed intentional — not a forgotten gap:** this means `action_tags`
   (Section 11's relationship system) cannot fire on events that happen *inside* a fight — e.g. sparing a
   downed enemy, or a companion witnessing cruelty mid-combat, generate no relationship change under this
   design, since there is no extraction pass to tag them. If this ever matters enough to fix, the cheapest
   fix is *not* re-adding extraction calls mid-combat — it's tagging a small fixed set of outcomes directly in
   Python from data already computed by `classify_round_significance()` (e.g. "enemy left alive when reduced
   to 0 HP" → `["mercy"]`), no LLM call needed either way. Out of scope for v1; note it here so it isn't
   reintroduced by accident later.

### 9b-ii. [v2 NEW] Routine-hit template bank
For routine hits/misses, use a small bank of Python-selected sentence templates (randomly picked to avoid
repetition), e.g.:
```
"{attacker} lands a solid hit on {target} for {damage}."
"{target} staggers as {attacker}'s attack connects."
"{attacker}'s strike goes wide, missing {target} entirely."
```
These require zero LLM involvement and keep pacing fast; only the single round-summary narrative call touches
the model.

### 9b-iii. [v2 FIX] Injected result format, updated
```
[System: Round Result]
Round 3:
- Star attacks Goblin Scout 1: hit, 7 damage, defeated.
- Gale casts Fire Bolt at Goblin Scout 2: hit, 6 damage.
- Goblin Scout 2 attacks Star: miss.
Narrate this round based strictly on these results. Do not invent additional actions or change any outcome.
```
One block per round, not one message per action.

### 9b-iv. [v2 FIX] Manual combat trigger as fallback
An "⚔️ Attack" button is always available during exploration, independent of whether the extraction call's
`combat_start` field fires correctly. Small local models can miss the signal that a scene has turned hostile;
relying solely on an LLM-inferred trigger is fragile. The button instantiates `combat_state` directly via
`start_combat()`, same as the extraction-triggered path.

**[v2 FIX] Idempotency guard — required since there are two trigger paths.** Both the extraction signal and
the manual button call the same `start_combat()`. If the player presses the button in the same turn the
extraction call also emits `combat_start`, they could otherwise fire twice and stomp each other's
initiative roll. `start_combat()` must check `if world_state.get("combat_state") is not None: return` as its
first line — a second trigger while combat is already active is simply a no-op.

### 9c. Required functions — `combat_manager.py`
Unchanged list from v1, plus:
- `classify_round_significance(round_results) -> dict` — **[v2 NEW]** splits round events into
  significant/routine per the rules in 9b(4).
- `apply_condition(combatant, condition, duration) -> None` — **[v2 NEW]**, see Section 20.
- `tick_conditions(combat_state) -> None` — **[v2 NEW]**, decrements duration each turn, removes expired ones.

### 9d. `app.py` combat UI additions
Unchanged from v1 (turn order panel, HP bars, action buttons instead of free text, auto-resolve companion/enemy
turns) — the UI experience doesn't change, only how many times the model gets called per round.

---

## 10. Party & Companion System
Unchanged from v1 (schema, two-context/two-mechanism design, 2–3 companion cap for narrative clarity). See
Section 21 for the new dismiss/former-companion mechanic.

---

## 11. Relationship System
Unchanged from v1: fixed `ACTION_TAGS` vocabulary, `apply_approval_tags`, `get_relationship_tier` with fixed
thresholds, `romance_available` flag. Companion romance/relationship depth is kept as an **optional layer**,
not the core loop — if you want it to sit better alongside the Kenshi-lite death/world tone, consider tying
some action tags to shared-hardship moments (protecting a downed companion, sharing scarce supplies) rather
than dialogue alone, but this is a style choice, not a required change.

---

## 12. `llm_handler.py` — The Brain

### 12a. System prompt structure — [v2 FIX] now tiered for budget enforcement
Built in priority order, highest priority first, so lower tiers can be dropped if the token budget is tight
(Section 13):
1. **Tier 0 (never dropped):** override block — Python owns all math, including DC and damage; treat
   `[System: Roll Result]` / `[System: Round Result]` as absolute truth.
2. **Tier 1 (never dropped):** campaign tone + `background_hook` — both short.
3. **Tier 2 (drop least-relevant first if needed):** present companions' `persona_seed` — only companions
   physically in the current scene, and only those who've spoken/acted recently if truly tight on space.
4. **Tier 3 (droppable, degrade gracefully):** RAG lore — normally 2 `major_lore` + 1 `minor_lore`, drop to 1
   `major_lore` only under pressure.
5. **Tier 4 (compressed, not full text every call):** ruleset — **[v2 FIX]** replace the full 16-section
   prose ruleset with a **condensed rules cheat-sheet** (bullet-point form, only the facts the model needs to
   narrate correctly — not the reasoning behind each rule). The full 16-section version becomes reference
   documentation for the developer, not something sent to the model every single call. This is the single
   biggest token saving available, since the full ruleset was likely the largest item in the v1 prompt.

### 12b. Extraction call — [v2 FIX] consolidated output schema
```json
{
  "state_updates": {"hp_change": -10, "add_item_id": "healing_potion"},
  "requires_roll": {"type": "skill_check", "stat": "DEX", "skill": "Stealth", "difficulty": "hard", "advantage": false},
  "combat_start": {"enemies": ["goblin_scout"]},
  "world_updates": {"new_location": {"...": "see Section 6"}},
  "quest_updates": {"new_quest": {"...": "..."}, "objective_update": {"quest_id": "q_main_01", "objective_index": 1, "done": true}},
  "action_tags": ["honesty", "flirtation"],
  "npc_relationship_change": {"npc_id": "npc_gale", "delta": 0}
}
```
Change from v1: `requires_roll.dc` → `requires_roll.difficulty` (enum only, see 8a). Every field remains
optional/nullable. **Skipped entirely during combat, see 9b(6).**

### 12c. Roll result injection — unchanged shape from v1, see 9b-iii for the new round-batched combat variant.

---

## 12d. [v2 FIX] Model-swap decision — required before build, not left open
The v1/early-v2 note "extraction can use a smaller model independently" is dangerous as written on 4GB VRAM.
Two different models (e.g. `llama3.1:8b` for narrative, `qwen2.5:3b` for extraction) cannot both stay resident
— `keep_alive` only prevents unloading an *idle* model, it does nothing to prevent the swap cost of switching
between two *different* models back-to-back every turn. If the narrative and extraction calls alternate models
every turn, expect real reload overhead on **every single turn**, not just occasionally.

**Default for v1 build: use the same model for both narrative and extraction calls.** Only split them if a
benchmark (via `setup.py` or a manual test script) shows the combined single-model round-trip is too slow —
and if so, treat it as an explicit, measured tradeoff, not a default assumption. Log this decision and its
benchmark result in the debug log described in Section 13.

---

## 13. Context Budget Management — [v2 FIX] now actually enforced

- **Set `num_ctx` explicitly** in every Ollama call. Test the actual ceiling on this hardware rather than
  assuming 4096 — with the condensed ruleset (12a tier 4) there may be room for 6144–8192; benchmark both
  generation speed and quality before locking a number.
- **Real token counting before every narrative call**, not an assumed budget:
  1. Count tokens for tiers 0–4 in priority order (12a).
  2. If total exceeds ~70% of `num_ctx` (leaving room for conversation history + model output), drop tiers
     from the bottom (tier 4 compressed further → tier 3 reduced → tier 2 trimmed) until it fits. Tier 0/1
     are never dropped.
  3. Log the final token count and which tiers were trimmed, to the debug log (not shown to the player).
- **Two separate history buffers**, unchanged from v1: `chat_display` (unbounded, never sent to the LLM) and
  `llm_context_window` (fixed 6-turn sliding window, actually sent).
- **Log `eval_count` / `prompt_eval_count` / `eval_duration`** from every response — unchanged from v1, this
  is how context bloat gets caught during testing.
- Extraction call prompt stays minimal — narrative text + short instruction only, no system prompt, no
  history, no lore, unchanged from v1.

---

## 13b. [v2 NEW] `validation.py` — Extraction Output Validation
This module sits between the extraction call's raw JSON output and `apply_state_updates()`. **Nothing from
the extraction call is trusted or applied without passing through here first.**

- `validate_action_tags(tags: list) -> list` — drops any tag not in the fixed `ACTION_TAGS` vocabulary
  (Section 11); keeps the valid ones. Field-level filtering, not all-or-nothing rejection.
- `validate_item_id(item_id: str) -> bool` — checks against `item_catalog.json` keys; unknown IDs are logged
  and dropped, never applied (an LLM narrating "a Ring of Fire" that doesn't exist in the catalog must not
  be able to grant it).
- `validate_monster_ids(enemy_ids: list) -> list` — same pattern against `monster_catalog.json`, used for
  `combat_start.enemies`.
- `validate_npc_id(npc_id: str, world_state: dict) -> bool` — checks against `npc_relationships` and
  `party.companions`.
- `validate_numeric_range(field_name: str, value: int, min_val: int, max_val: int) -> int | None` — sanity
  bounds (e.g. `hp_change` clamped to a reasonable range) to catch hallucinated extreme values; out-of-range
  values are dropped, not clamped silently into the update (dropping is safer than guessing a "corrected"
  number).
- `validate_extraction_output(raw: dict) -> dict` — top-level entry point: runs every sub-validator field by
  field, returns a cleaned dict containing only what passed. Logs every dropped field with the reason, to
  the debug log, so extraction-prompt quality can be tuned over time by seeing what gets rejected most.
- **Retry policy:** if the raw JSON fails to parse at all (not a validation failure, a syntax failure), retry
  the extraction call once with a stricter "return ONLY valid JSON" reminder appended. If it still fails,
  fall back to no-op — never crash the game loop, matching v1's original intent, just with one retry added
  first.

---

## 14. `memory_manager.py` — Tiered RAG Memory
Unchanged from v1: ChromaDB persistent client, `all-MiniLM-L6-v2` embeddings, 6-turn short-term window,
auto-summarize into `minor_lore` past 6 turns, consolidate into `major_lore` every 20 minor entries,
`get_relevant_lore()` capped at 3 total regardless of campaign length. `st.cache_resource` for both clients.

### 14a. [v2 OPTIONAL] Session recap on load
When loading a save, pull the 2–3 most recent `major_lore` entries and generate a short "previously, in your
story..." paragraph once (single lightweight-model call at load time, not per turn) before the player's first
action. Nice-to-have for re-immersion after a break; skip if time-constrained, doesn't affect anything else.

---

## 15. `app.py` — Full UI / Game Loop Checklist

### App modes — unchanged
- [ ] Landing screen: "New Character" / "Load Save" dropdown / **[v2 NEW]** "Restore from backup" option.
- [ ] `app_mode` switches between `"character_creation"` and `"playing"`.

### Core loop (playing mode)
- [ ] Sidebar: stats+modifiers, AC, proficiency, spell slots, bardic inspiration, concentration, death
      saves, **[v2 NEW] gold, active conditions with remaining duration.**
- [ ] Sidebar: Inventory, Quest Log, NPC Relationships (tier labels only), Party (HP/status + **[v2 NEW]
      "Dismiss" button per companion**).
- [ ] Location panel: current room/town + exits.
- [ ] After each narrative call (non-combat turns only, see 9b(6) for combat): extraction call runs silently
      → **[v2 FIX] output passes through `validate_extraction_output()`** → apply all validated updates in
      one pass.
- [ ] Extraction JSON parse failure → one retry → fall back to no-op + small sidebar warning, never crash.
- [ ] Roll button with breakdown card, Advantage toggle, Bardic Inspiration button, roll history, roll queue.
- [ ] Lock chat input while a roll or combat round is pending.
- [ ] Movement via direction buttons.
- [ ] Combat panel (Section 9d) replaces normal input while `combat_state` is active, **[v2 NEW] plus an
      always-available "⚔️ Attack" button outside combat as a manual trigger fallback (9b-iv).**
- [ ] "Rest" button — restores HP/slots, pure Python, no LLM call. **[v2 NEW] Note: rest restores HP/slots
      but not `active_conditions` from injuries if you choose to layer that in later — see Section 17d.**
- [ ] Autosave after every resolved turn, **[v2 NEW] plus rolling backup rotation (5d).**
- [ ] **[v2 NEW]** Shop panel when entering a location with a `shop_id` (Section 19).
- [ ] **[v2 NEW]** Level-up popup when `xp_current` crosses a threshold (Section 17b) — pure UI, no LLM call.

### Performance
- [ ] `st.cache_resource` for Ollama/ChromaDB clients.
- [ ] Ollama `keep_alive` set.
- [ ] `num_ctx` set explicitly and benchmarked (Section 13).

---

## 16. Setup Script Checklist (`setup.py`)
Unchanged from v1 — check `ollama` on PATH, `ollama pull llama3` (or chosen model), `pip install -r requirements.txt`.
Model choice notes unchanged: test `llama3.1:8b-instruct-q4_K_M` / `qwen2.5:7b-instruct`; for Thai quality test
`scb10x/typhoon2.1-gemma3-4b`; extraction call can use a smaller model (`qwen2.5:3b-instruct`) independently.

---

## 17. Death, Injury & Consequence System (Kenshi-lite) — [v2 NEW]
Replaces v1's implicit "3 failed death saves = game over" with a system where death never ends the game —
only costs the player time, gold, or items.

### 17a. Downed outcome (core mechanic — build this)
When a combatant's HP hits 0 and death saves fail three times (still tracked exactly as in v1's
`resolve_death_save`), instead of ending the game:
```python
def resolve_downed_outcome(combatant, combat_state, world_state) -> dict:
    # weighted random choice, informed by combat context (who won, location danger level)
    outcome = random.choices(
        ["robbed_and_left", "captured", "rescued_by_npc"],
        weights=[...],  # tune based on whether player's side won the fight
    )[0]
    ...
    return {"outcome": outcome, "penalty": {...}}
```
- `robbed_and_left`: lose a random portion of gold/unequipped inventory, HP restored to 1, moved to the
  nearest previously-visited safe location.
- `captured`: `status` set to `"captive"` on the character sheet; see 17a-ii.
- `rescued_by_npc`: a narrative beat (LLM narrates it) with partial HP restored, no item loss.

This is one function plus one status field — it does not touch combat engine internals, item system, or
anything else.

### 17a-ii. Captive state (kept intentionally simple)
- While `status == "captive"`, movement buttons are replaced with a single "attempt escape" action —
  one `resolve_check` call against a fixed difficulty (e.g. `"hard"`, using the same enum from 8a, no new
  DC system needed). Success clears `status` and drops the player at a nearby location; failure costs a turn
  and can be retried. No multi-stage escape puzzle — this is meant to be a short inconvenience, not a new
  game mode.

### 17b. [v2 NEW] XP & Leveling
Milestone-leaning, not full 5e XP tables (too much bookkeeping for a hand-authored catalog at this scope).
- `monster_catalog.json` entries gain an `xp_value` field; completed quests grant a flat XP award defined per
  quest.
- `xp_current` tracked in the save file (5a); compare against a small fixed threshold table for levels 1–5
  (per the scope decision in 4e).
- On crossing a threshold: HP max increases (average hit die roll + CON mod), proficiency bonus increases at
  the fixed 5e milestones, spell slots increase if the class casts spells (ties into 18).
- **[v2 NEW, explicitly deferred]** Extra Attack and other level-5 martial features that would require the
  combat engine to support multiple attacks per turn are out of scope for this pass — flag it as a known gap
  rather than quietly ignoring it, and revisit once levels 1–5 core loop is verified stable, same treatment
  v1 gave companion jealousy.
- Level-up is a pure Python calculation + a UI popup; no LLM call needed.

### 17c. Rest interaction with injuries (optional layer)
If you want death/downed consequences to feel heavier without adding a full injury system: a short rest fully
restores HP/spell slots (unchanged from 5e), but recovery from being `captured` or `robbed_and_left` doesn't
auto-heal reputation/gold — it's simply gone until earned back through play. This costs nothing extra to
build; it's a consequence of 17a, not a new mechanic.

---

## 18. [v2 NEW] Spellcasting System
Scoped down from full 5e: 8–12 spells per casting class, single-target only for v1 (no AoE — defer, same
treatment as Extra Attack).

### 18a. `spell_catalog.json`
```json
{
  "vicious_mockery": {"name": "Vicious Mockery", "level": 0, "class": ["Bard"], "type": "attack_save", "save_stat": "WIS", "effect": {"damage": "1d4", "damage_type": "psychic"}, "on_fail_extra": "disadvantage_next_attack"},
  "healing_word": {"name": "Healing Word", "level": 1, "class": ["Bard", "Cleric"], "type": "heal", "effect": {"heal": "2d4+3"}, "slot_cost": 1}
}
```

### 18b. Resolution — two paths depending on spell type
- **Attack-roll spells** (e.g. Fire Bolt): resolved exactly like a weapon attack via `resolve_attack`, vs
  target AC — reuses the existing combat resolution function, no new code needed.
- **Save-forcing spells** (e.g. Hold Person, Vicious Mockery): use the new `resolve_spell_save()` from 8b —
  caster sets the DC, target rolls the save. This direction reversal is the one genuinely new piece of math
  this system needs.
- **Heal/buff spells**: flat Python effect application via `apply_state_updates`, no roll at all beyond the
  heal dice.

### 18c. Utility spells outside combat
Kept intentionally lightweight: a small set of narrative-flavor spells (e.g. Detect Magic) apply a temporary
flag (e.g. advantage on the next related check) rather than a bespoke mechanic each. The narrative call
describes the effect; Python just tracks the flag and its expiry.

### 18d. Slot management
`spell_slots` per level, unchanged shape from v1's schema — `use_consumable`-style decrement on cast, restored
on rest via the existing rest function.

---

## 19. [v2 NEW] Economy & Shop System

### 19a. `shop_catalog.json`
```json
{
  "general_store": {"name": "Riverside General Store", "sell_items": ["healing_potion", "torch", "rope"], "buy_multiplier": 0.5, "sell_multiplier": 1.0}
}
```
Referenced by the `"shops"` field already present in `dungeon_data.json` rooms (v1 had this field but no
system behind it).

### 19b. Required functions — `state_manager.py`
- `buy_item(item_id, shop_id, state) -> dict` — checks `gold >= price`, deducts gold, adds item. Price =
  `item_catalog[item_id]["value_gold"] * shop["sell_multiplier"]`.
- `sell_item(item_id, shop_id, state) -> dict` — removes item, adds gold at `buy_multiplier`.
Both pure Python, zero LLM involvement in the math — the narrative call only sets the scene ("the shopkeeper
nods") around a UI transaction, same principle as combat.

### 19c. Gold sources
- `starting_gold` per background (4a).
- Loot drops: add optional `gold_drop` range to `monster_catalog.json` entries.
- Quest completion rewards, defined per quest in `quest_log`.

### 19d. UI
Entering a location with a `shop_id` opens a shop panel (buy/sell list with prices, current gold shown)
replacing normal chat input temporarily — same UI pattern as the combat panel.

---

## 20. [v2 NEW / OPTIONAL] Status Effects & Conditions
Skip entirely if time-constrained — combat works fine on HP/AC alone (v1's original scope). Build this only
after the round-based combat fix (9b) is verified stable.

### 20a. Fixed vocabulary (never LLM-decided)
```python
CONDITIONS = ["prone", "poisoned", "stunned", "restrained", "frightened"]
```
Conditions are only ever applied via a static `applies_condition` field on a monster's attack in
`monster_catalog.json` (see 9a) — never inferred by the extraction call from narrative text.

### 20b. Effect table (simple, not full 5e nuance)
```python
CONDITION_EFFECTS = {
    "prone": {"attacks_against_have_advantage": True},
    "poisoned": {"attack_rolls_disadvantage": True},
    "stunned": {"skip_turn": True},
    "restrained": {"attack_rolls_disadvantage": True, "attacks_against_have_advantage": True},
    "frightened": {"cannot_approach_source": True},
}
```
`resolve_attack()` and `resolve_check()` check `active_conditions` before rolling and apply the relevant
modifier. Duration is a fixed number of rounds set at application time, decremented by `tick_conditions()`
each round, removed automatically at zero — no "save to shake it off" sub-mechanic for v1 (that's a further
layer to defer if this base version proves worth extending).

---

## 21. [v2 NEW] Companion Dismissal
### 21a. `dismiss_companion(npc_id, world_state) -> None`
Moves the companion from `party.companions` to `party.former_companions` (5b) rather than deleting them —
the companion becomes a recruitable NPC again at the last location they were encountered, so the player can
re-recruit them later without new content being written. Reuses the existing location/NPC structures; no new
system required.

### 21b. UI
Small "Dismiss" button per companion in the Party sidebar section, with a confirmation prompt to avoid
accidental taps.

---

## 21b. Known Limitations — accepted for v1, not oversights
Documented explicitly so they're never mistaken for forgotten bugs during review:
- **`saves/<n>.json` and `world_saves/<n>_world.json` are written as two independent atomic writes, not one
  transaction.** A crash between the two writes could leave them momentarily out of sync (e.g. XP updated,
  quest state not yet updated). Risk is low for a local single-player app and the rolling backup (2c) bounds
  the damage to at most one turn of progress. Not worth a cross-file transaction log at this scope.
- **No migration path from older `schema_version` saves.** A mismatched version refuses to load with a clear
  error rather than attempting to upgrade the save automatically. Acceptable pre-release; revisit only if
  real players end up with saves worth preserving across a schema change.

---

## 22. Manual Verification Plan
Extends v1's plan with the new systems:
1. `state_manager.py` standalone — `resolve_check`, `apply_state_updates`, concentration auto-trigger,
   point-buy validation, **[v2] XP threshold → level-up trigger, buy/sell gold math, `resolve_spell_save`
   DC direction.**
2. `combat_manager.py` standalone — initiative ordering, `resolve_attack` vs AC, damage/crit doubling,
   combat end conditions, **[v2] round batching produces exactly one narrative call per round regardless of
   combatant count, `classify_round_significance` correctly separates crit/kill/downed from routine hits,
   condition application/expiry via `tick_conditions`.**
3. `dungeon_manager.py` standalone — unchanged from v1, **[v2] plus optional world-event flag injection if built.**
4. `memory_manager.py` standalone — unchanged from v1.
5. **[v2 NEW]** `validation.py` standalone — feed deliberately malformed extraction JSON (unknown item_id,
   out-of-vocabulary action_tag, absurd hp_change, invalid npc_id) and confirm each is dropped individually
   without discarding the rest of the valid payload.
6. `python setup.py` — unchanged.
7. Full playthrough via `streamlit run app.py`:
   - Character creation end-to-end, zero Ollama calls.
   - Normal turn → sidebar updates.
   - Skill check with the new difficulty enum → confirm DC never appears as a raw number from the model.
   - Combat: trigger via both the extraction signal and the manual "Attack" button fallback; confirm the
     whole round produces one narrative call, not one per combatant.
   - Take damage while concentrating → concentration save auto-triggers, unchanged from v1.
   - Get downed in combat → confirm `resolve_downed_outcome` fires, game continues, never a hard game-over.
   - If built: get captured → attempt escape → succeed/fail both tested.
   - Cross an XP threshold → level-up popup, HP/slots increase correctly.
   - Enter a shop → buy and sell an item, confirm gold math both directions.
   - Cast an attack-roll spell and a save-forcing spell → confirm both resolution paths work and DC direction
     is correct for the latter.
   - If built: apply a status condition → confirm it affects the next roll and expires on schedule.
   - Dismiss a companion → confirm they appear as a recruitable NPC at their last location, not deleted.
   - Close and reload → confirm character, world, quests, relationships, gold, XP, and conditions all persist.
   - Check debug log's `eval_duration` over a long session — confirm the tiered budget in Section 13 is
     actually keeping context stable, not silently growing.
