# PROJECT: Offline RAG D&D 5E Solo AI Dungeon Master (v2 Build Spec)

Target hardware: Intel i7 12th gen, 16GB RAM, RTX 3050 (4GB VRAM). 100% offline via Ollama on
`localhost:11434`. Solo single-player build — no multiplayer, no concurrency handling needed.

Full reference doc: `DND_AI_DM_full_spec_v2.md` — read it alongside this prompt, it has the reasoning behind
every decision below. This prompt is the actionable build checklist.

Tone target: **not** a SillyTavern-style romance/RP tool. This is a real D&D 5e ruleset engine with reliable
state, plus a Kenshi-lite consequence system — death costs the player time/gold/items, it never ends the game,
and the world should feel like it doesn't wait around for the player (optional, cuttable).

---

## PART 0: DESIGN PRINCIPLE (applies to everything below)
- **LLM = narrative only**, plus deciding *what kind* of check/attack/event is needed. Never trust it with
  math — **this now explicitly includes DC**: the LLM only ever picks a difficulty enum
  (`"easy"|"medium"|"hard"|"very_hard"`), never a raw DC number. Python maps enum → DC.
- **Python = 100% of all math**: dice, modifiers, proficiency, advantage/disadvantage, crit/fumble, death
  saves, concentration DC, DC-from-enum, combat resolution, item effects, quest state, relationship values,
  XP/leveling, shop pricing, status effect durations.
- **Two separate LLM calls per turn outside combat, never combined**: (1) streamed narrative call, pure story
  text, no JSON; (2) `format="json"` extraction call, not streamed, minimal prompt.
- **Inside combat: one narrative call per whole round, not per action** (see PART 6). **No extraction call
  during combat at all** — combat state changes are already fully known from deterministic resolution
  functions; resume narrative+extraction cycle the turn after combat ends.
- **Every extraction call's JSON output must pass through `validation.py` before anything is applied** — never
  trust raw model output (see PART 5b).
- Any JSON parse failure → retry once with a stricter format reminder → fall back to no-op + small
  non-blocking UI warning. Never crash the game loop.
- Set `num_ctx` explicitly in every Ollama call, and enforce a real token budget with priority tiers (PART 10) —
  don't just pick a number and hope it fits.
- Keep two separate `st.session_state` buffers: `chat_display` (full history, never sent to the LLM) and
  `llm_context_window` (fixed 6-turn window, actually sent to Ollama). Never conflate them.
- **Death never ends the game.** HP 0 + failed death saves → a downed-outcome resolution (PART 7), not game
  over.

---

## PART 1: Character Creation System
Unchanged in spirit from v1 — `st.session_state["app_mode"] = "character_creation"`, zero Ollama calls,
pure Streamlit forms + Python validation.

1. Flow: Name → Race → Class (no subclass at level 1) → Background (grants 2 skill proficiencies +
   `background_hook` + **`starting_gold`**) → Point-buy (27 points, `{8:0,9:1,10:2,11:3,12:4,13:5,14:7,15:9}`)
   → Campaign tone (`"serious"|"comedic"|"mature"`) → Confirm.
2. `validate_point_buy(stats) -> (bool, error_message)` — reject with specific error, never silently clamp.
3. On confirm: derive HP/AC/proficiency, write `saves/<n>.json` + `world_saves/<n>_world.json`, switch to
   `"playing"` mode.
4. Landing screen: "New Character" / "Load Save" dropdown / **"Restore from backup" option**.
5. **Scope decision to build against: levels 1–5 only.** Document this in code comments — full 5e's 1–20
   range with subclass features every level is out of scope for a hand-authored catalog at this size.

---

## PART 2: Save Files — Two Per Character, Plus Rolling Backup
`saves/<n>.json` (character sheet) and `world_saves/<n>_world.json` (world/party/quests/relationships),
kept separate as in v1.

### 2a. `saves/<n>.json` — schema_version 4
```json
{
  "schema_version": 4,
  "name": "Star", "race": "Half-Elf", "class": "Bard", "background": "Entertainer",
  "background_hook": "...", "level": 1, "xp_current": 0, "campaign_tone": "comedic",
  "hp": {"current": 10, "max": 10}, "ac": 12,
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
  "inventory": [{"item_id": "cloak_of_protection", "equipped": true}, {"item_id": "healing_potion", "equipped": false, "quantity": 1}],
  "roll_log": []
}
```
New vs. v1 schema 3: `xp_current`, `known_spells`, `status`, `active_conditions`, `gold`. Refuse to load a
save with a mismatched `schema_version` — clear error, not a crash.

### 2b. `world_saves/<n>_world.json` — schema_version 4
```json
{
  "schema_version": 4,
  "current_location": "town_riverside",
  "visited_rooms": ["town_riverside"], "cleared_rooms": [],
  "locations": {},
  "quest_log": {"main": [], "side": []},
  "npc_relationships": {},
  "party": {"companions": [], "former_companions": []},
  "combat_state": null,
  "world_event_flags": []
}
```

### 2c. Save integrity
- **Atomic writes**: temp file + `os.replace()`.
- **Autosave**: after every fully resolved turn.
- **Rolling backup**: keep last 3 autosaves per character in `save_backups/<name>/`, oldest rotated out —
  plain file copy on save, no new logic beyond rotation.

---

## PART 3: World & Dungeon System — Hand-Authored Hub + Lazy Generation
Unchanged from v1: `dungeon_data.json` static hub, lazy generation via `world_updates.new_location` merged
into the save's `locations` dict only, `register_new_location()` validates duplicate IDs and `connects_to`.
Required functions: `get_current_room`, `move_player`, `register_new_location`, `mark_room_cleared`,
`get_room_loot`.

**Optional, build only if time allows:** lightweight world events — small random chance on `move_player` to
set a flag string (e.g. `"goblin_camp_grew"`) for a location the player isn't currently in, injected as one
line of context when they later visit. No simulation loop. Cut without regret if short on time.

---

## PART 4: Item, Spell & Shop Systems

### 4a. Items — unchanged from v1, plus `value_gold` on every entry
```json
{"cloak_of_protection": {"name": "Cloak of Protection", "type": "wearable", "slot": "cloak", "effects": {"ac_bonus": 1, "saving_throw_bonus": 1}, "rarity": "uncommon", "value_gold": 25}}
```
`equip_item`, `unequip_item`, `get_active_effects`, `use_consumable` — unchanged.

### 4b. NEW: `spell_catalog.json` (8–12 spells per casting class, single-target only, no AoE for v1)
```json
{"vicious_mockery": {"name": "Vicious Mockery", "level": 0, "class": ["Bard"], "type": "attack_save", "save_stat": "WIS", "effect": {"damage": "1d4", "damage_type": "psychic"}}}
```
Two resolution paths:
- Attack-roll spells → reuse `resolve_attack` vs target AC, same as a weapon.
- Save-forcing spells → new `resolve_spell_save(caster, target, spell, state)`: DC = `8 + proficiency_bonus +
  casting_stat_modifier`, **target** rolls the save (direction reversed from a normal check — write this as
  its own function, don't just flip a parameter on `resolve_check`).
- Heal/buff spells → flat effect via `apply_state_updates`.
Utility/out-of-combat spells: apply a temporary flag (e.g. advantage on next check) rather than a bespoke
mechanic per spell.

### 4c. NEW: `shop_catalog.json`, referenced by the `"shops"` field already in `dungeon_data.json`
```json
{"general_store": {"name": "Riverside General Store", "sell_items": ["healing_potion", "torch", "rope"], "buy_multiplier": 0.5, "sell_multiplier": 1.0}}
```
`buy_item(item_id, shop_id, state)` / `sell_item(item_id, shop_id, state)` — pure Python gold math, zero LLM
involvement. Gold sources: `starting_gold` per background, optional `gold_drop` range on monsters, quest
rewards. UI: shop panel replaces chat input when entering a location with a shop, same pattern as combat panel.

---

## PART 5: Core D&D Resolution — `state_manager.py`

### 5a. Pure Python, zero LLM involvement
- `get_modifier(stat_value) -> int`, `is_proficient(skill_or_save, state) -> bool`
- `resolve_check(stat, difficulty: str, state, advantage=False, disadvantage=False, proficient=False, bonus_dice=None) -> dict`
  — takes the difficulty enum, maps internally via `DIFFICULTY_TO_DC = {"easy": 10, "medium": 13, "hard": 16,
  "very_hard": 19}`. Returns `{"roll","modifier","proficiency","bonus","total","dc","success","critical","fumble"}`.
  Nat 20 always succeeds, nat 1 always fails.
- `resolve_death_save(state) -> dict`
- `resolve_concentration_check(damage_taken, state) -> dict` — DC = `max(10, damage_taken // 2)`,
  auto-triggered inside `apply_state_updates` whenever `hp_change < 0` and `concentration` is not null.
- `resolve_spell_save(caster, target, spell, state) -> dict` — see 4b.
- `apply_state_updates(updates: dict)` — **only ever called with output that has already passed
  `validate_extraction_output()`** (PART 5b). Routes hp/inventory/item/quest/relationship/world/xp/gold
  sub-fields to their handlers.
- `log_roll(entry: dict)`.
- **NEW:** `award_xp(amount, state)`, `check_level_up(state) -> bool`, `apply_level_up(state)` — HP max
  increase, proficiency bonus at fixed milestones, spell slot increase for casters. Pure calculation, no LLM.
- **NEW:** `resolve_downed_outcome(combatant, combat_state, world_state) -> dict` — see PART 7.
- **NEW:** `buy_item` / `sell_item` — see 4c.
- **NEW:** `dismiss_companion(npc_id, world_state)` — moves companion from `party.companions` to
  `party.former_companions`, doesn't delete; they become recruitable again at their last-seen location.

### 5b. NEW FILE: `validation.py`
Every extraction call output passes through here before `apply_state_updates` ever sees it:
- `validate_action_tags(tags) -> list` — drop anything outside the fixed `ACTION_TAGS` vocabulary, keep the rest.
- `validate_item_id(item_id) -> bool` — must exist in `item_catalog.json`; unknown IDs logged and dropped.
- `validate_monster_ids(enemy_ids) -> list` — same pattern against `monster_catalog.json`.
- `validate_npc_id(npc_id, world_state) -> bool` — must exist in `npc_relationships` or `party.companions`.
- `validate_numeric_range(field_name, value, min_val, max_val) -> int | None` — drop out-of-bounds values
  rather than clamping them to a guessed "safe" number.
- `validate_extraction_output(raw: dict) -> dict` — top-level entry point, runs all sub-validators field by
  field, returns only what passed, logs every drop with a reason for later prompt tuning. **Field-level
  filtering, never all-or-nothing rejection of the whole payload.**

---

## PART 6: Combat Engine — round-based narration (critical fix, build this exactly)

Player, companions, and monsters share one **combatant** shape (unchanged from v1):
```json
{"id": "goblin_scout_1", "name": "Goblin Scout", "side": "enemy", "hp": {"current": 7, "max": 7}, "ac": 13, "stats": {...}, "attacks": [{"name": "Scimitar", "attack_bonus": 4, "damage": "1d6+2", "damage_type": "slashing", "applies_condition": null}], "initiative": null, "active_conditions": []}
```

### 6a. Trigger — two paths, not one
- Extraction call sets `combat_start: {"enemies": [...]}`, **or**
- Player presses an always-available **"⚔️ Attack" button** during exploration — a manual fallback that
  doesn't depend on the extraction call correctly inferring hostile intent. Both call `start_combat()`.
- **Required idempotency guard:** since two paths can call `start_combat()`, it must check
  `if world_state.get("combat_state") is not None: return` as its first line — a second trigger while combat
  is already active is a no-op, preventing a double-initiative-roll if both paths fire in the same turn.

### 6b. Whole-round resolution, then ONE narrative call
This is the core performance fix — v1's per-action narration (6-8 LLM calls/round) is too slow on 4GB VRAM.
1. `roll_initiative(combatants)` — 1d20 + DEX mod each, sorted descending.
2. **Resolve the entire round in Python first**, no LLM calls during resolution: player's chosen action →
   every companion's turn (`resolve_companion_turn`, deterministic from `combat_behavior` flag) → every
   enemy's turn (`resolve_enemy_turn`, simple behavior flags). All via `resolve_attack`/`roll_damage`/
   `resolve_spell_save` as applicable.
3. **Classify results by significance** via `classify_round_significance(round_results) -> dict`:
   - Significant (always fully narrated): critical hit/fumble, a combatant downed/killed, combat start/end,
     any spell/item use, any named combatant dropping below 25% HP.
   - Routine (never sent to the model): normal hit/miss, no special outcome — describe with a randomly
     picked Python string template instead (e.g. `"{attacker} lands a solid hit on {target} for {damage}."`).
4. Send **one combined `[System: Round Result]` block** covering the whole round to the narrative call —
   this replaces one-call-per-action with one-call-per-round.
5. `hp <= 0` → `"downed"` (player/companion → PART 7's outcome system; monster → removed from turn order).
   `check_combat_end(combat_state) -> "victory"|"defeat"|None`.
6. **No extraction call runs during combat at all** — all state is already known from deterministic
   resolution. Resume normal narrative+extraction cycle the turn after combat ends.
7. Status effects (optional, build only after round-batching is verified stable): `tick_conditions(combat_state)`
   decrements duration each round, removes expired conditions; `resolve_attack`/`resolve_check` check
   `active_conditions` before rolling.

### 6c. Required functions — `combat_manager.py`
`start_combat`, `roll_initiative`, `resolve_attack(attacker, target, advantage, disadvantage)`,
`roll_damage(dice_string, critical)`, `resolve_companion_turn`, `resolve_enemy_turn`, `advance_turn`,
`check_combat_end`, **`classify_round_significance`**, **`apply_condition`**, **`tick_conditions`**.

### 6d. `app.py` combat UI
Combat panel replaces normal chat input while `combat_state` is active: turn order + HP bars; player turn
shows action buttons; companion/enemy turns auto-resolve; **the whole round's narration appears as one
block**, not a stream of individual action messages.

---

## PART 7: Death, Injury & Consequence System (Kenshi-lite, build this — it's the tone-defining feature)

Replaces any notion of permadeath/game-over. HP 0 + 3 failed death saves →
`resolve_downed_outcome(combatant, combat_state, world_state) -> dict`:
- Weighted random choice: `"robbed_and_left"` (lose some gold/unequipped items, HP → 1, relocate to nearest
  visited safe location), `"captured"` (`status: "captive"`), `"rescued_by_npc"` (narrated beat, partial HP,
  no loss). Weight the roll by whether the player's side won the fight.
- **Captive state:** while `status == "captive"`, movement buttons replaced by a single "attempt escape"
  action — one `resolve_check` at fixed difficulty `"hard"` (reuses the enum from PART 5a, no new system).
  Success clears status, relocates player; failure costs a turn, retry allowed. Keep this simple — no
  multi-stage escape puzzle.
- This touches exactly one function plus one status field — it must not require changes to the combat
  engine's internals, item system, or anything else.

---

## PART 8: XP & Leveling (build this)
Milestone-leaning, not full 5e XP tables. `xp_value` added per monster in `monster_catalog.json`; flat XP
award per completed quest. `xp_current` tracked in save file, compared against a small fixed threshold table
for **levels 1–5 only** (explicit scope limit — do not build past level 5). On level-up: HP max increases
(average hit die + CON mod), proficiency bonus at fixed 5e milestones, spell slots increase for casters —
pure Python, UI popup, no LLM call. **Explicitly out of scope, flag in code comments rather than silently
skip:** Extra Attack and other multi-attack-per-turn features — would require combat engine changes, defer
until core loop (creation → explore → combat → leveling) is verified stable.

---

## PART 9: Party & Companion System — unchanged from v1, plus dismissal
```json
{"id": "npc_gale", "name": "Gale", "class": "Wizard", "hp": {...}, "stats": {...}, "combat_behavior": "ranged_support", "persona_seed": "...", "approves": [...], "disapproves": [...], "affection": 0}
```
Outside combat: `persona_seed` folded into the single narrative call's system prompt for present companions
only — zero extra Ollama calls. In combat: `combat_behavior` drives deterministic Python turns — zero LLM
calls. Cap 2–3 companions (narrative clarity, not performance). **NEW:** `dismiss_companion` (5a) moves a
companion to `former_companions`, recruitable again later, never deleted. Small "Dismiss" button per
companion in the Party sidebar with a confirmation prompt.

---

## PART 10: Relationship System — unchanged from v1
Fixed `ACTION_TAGS` vocabulary: `["honesty", "kindness", "curiosity", "humor", "cruelty", "greed", "cowardice",
"bravery", "flirtation"]`. `apply_approval_tags`, `get_relationship_tier` via fixed thresholds
(Stranger/Acquaintance/Friendly/Trusted/Romantic Interest — never LLM-invented). `romance_available` flag at
top tier, narrative call handles the actual scene. Keep this as an optional layer, not the core loop, given
the project's Kenshi-lite tone — the core focus is the death/consequence and combat systems above, not
relationship depth. Companion exclusivity/jealousy remains explicitly out of scope for v1.

---

## PART 11: `llm_handler.py` — The Brain

### 11a. System prompt, built in priority tiers (drop from the bottom under token pressure)
1. **Tier 0, never dropped:** override block — Python owns all math including DC and damage; treat
   `[System: Roll Result]` / `[System: Round Result]` as absolute truth, never contradict or recompute.
2. **Tier 1, never dropped:** campaign tone + `background_hook`.
3. **Tier 2, droppable:** present companions' `persona_seed`, only those in the current scene.
4. **Tier 3, droppable:** RAG lore, normally 2 major + 1 minor, degrade to 1 major only under pressure.
5. **Tier 4, compressed by default:** a condensed rules **cheat-sheet** (bullet facts only) replaces the full
   16-section prose ruleset for what actually gets sent to the model every call — keep the full prose version
   as developer reference documentation only, not something transmitted every turn. This is the single
   biggest token saving available.

### 11b. Extraction call schema (all fields optional/nullable)
```json
{
  "state_updates": {"hp_change": -10, "add_item_id": "healing_potion"},
  "requires_roll": {"type": "skill_check", "stat": "DEX", "skill": "Stealth", "difficulty": "hard", "advantage": false},
  "combat_start": {"enemies": ["goblin_scout"]},
  "world_updates": {"new_location": {"...": "PART 3"}},
  "quest_updates": {"new_quest": {"..."}, "objective_update": {"..."}},
  "action_tags": ["honesty", "flirtation"],
  "npc_relationship_change": {"npc_id": "npc_gale", "delta": 0}
}
```
Note vs v1: `requires_roll.dc` (raw number) → `requires_roll.difficulty` (enum) — the model must never emit a
raw DC. This JSON is the single source of truth for all non-combat state changes and always passes through
`validate_extraction_output()` before anything is applied. **Not called at all during combat rounds** (PART 6).

### 11c. Result injection
```
[System: Roll Result] stat=DEX skill=Stealth roll=14 modifier=+3 proficiency=+2 total=19 dc=15 success=true critical=false
[System: Round Result]
Round 3:
- Star attacks Goblin Scout 1: hit, 7 damage, defeated.
- Gale casts Fire Bolt at Goblin Scout 2: hit, 6 damage.
- Goblin Scout 2 attacks Star: miss.
Narrate this round based strictly on these results. Do not invent additional actions or change any outcome.
```

---

## PART 12: `memory_manager.py` — Tiered RAG Memory — unchanged from v1
`chromadb.PersistentClient(path="./db")`, `sentence-transformers/all-MiniLM-L6-v2` embeddings. Short-term:
last 6 turns. Auto-summarize oldest 2 into `minor_lore` past 6 turns. Consolidate every 20 minor entries into
one `major_lore` chapter summary, archive (never delete) consolidated minors. `get_relevant_lore()` capped at
3 total. `st.cache_resource` for client/model. **Optional:** on save load, generate a one-time "previously in
your story..." recap from the last 2-3 `major_lore` entries.

---

## PART 13: Context Budget — actually enforced (build this, it's a required fix not optional)
- `num_ctx` set explicitly, benchmark the real ceiling on this hardware once the condensed ruleset (11a tier 4)
  is in place — may allow more than 4096.
- Before every narrative call: **count tokens** for tiers 0-4 in priority order. If total exceeds ~70% of
  `num_ctx` (leave room for history + output), drop tiers from the bottom until it fits. Tiers 0/1 never drop.
- Log final token count and which tiers were trimmed to a debug log (not shown to player).
- Extraction call prompt stays minimal — narrative text + short instruction only, no system prompt/history/lore.

---

## PART 14: `app.py` — Full UI / Game Loop
- Landing: "New Character" / "Load Save" dropdown / **"Restore from backup."**
- Character creation mode: PART 1's flow, zero Ollama calls.
- Playing mode sidebar: stats+modifiers, AC, proficiency, spell slots, bardic inspiration, concentration,
  death saves, **gold, active conditions with remaining duration**; Inventory; Quest Log; NPC Relationships
  (tier labels only); Party (HP/status + **Dismiss button**); Location panel with exits.
- After each non-combat narrative call: extraction call → **`validate_extraction_output()`** → apply
  validated updates in one pass. Parse failure → one retry → no-op + small warning, never crash.
- **During combat: no extraction call** — whole round resolves in Python, one narrative call per round (PART 6).
  Accepted tradeoff: `action_tags`/relationship changes cannot fire on events inside a fight (e.g. sparing a
  downed enemy). This is intentional for v1, not a bug — do not silently add extraction calls mid-combat to
  "fix" it.
- Roll button: breakdown card, Advantage toggle, Bardic Inspiration button, roll history, roll queue. Lock
  chat input while a roll or combat round is pending.
- Movement: direction buttons. **Always-available "⚔️ Attack" button** as manual combat trigger fallback.
- Combat panel replaces normal input while `combat_state` is active.
- **Shop panel** when entering a location with a shop.
- **Level-up popup** when `xp_current` crosses a threshold, pure Python + UI, no LLM call.
- "Rest" button restores HP/slots, pure Python, no LLM call.
- Autosave after every resolved turn, plus **rolling backup rotation.**
- `st.cache_resource` for Ollama/ChromaDB clients, `keep_alive` set, `num_ctx` set and benchmarked.

---

## PART 15: `setup.py` — unchanged from v1
Check `ollama` on PATH (halt with clear message if missing), `ollama pull llama3` (or chosen model),
`pip install -r requirements.txt`. Comments: `llama3` 8B tight on 4GB VRAM, test
`llama3.1:8b-instruct-q4_K_M`/`qwen2.5:7b-instruct`; Thai quality test `scb10x/typhoon2.1-gemma3-4b`;
**default to the SAME model for both narrative and extraction calls** — do not split them by default. Two
different models cannot both stay resident on 4GB VRAM; `keep_alive` prevents unloading an idle model but does
nothing to stop reload overhead from constantly switching between two different models every turn. Only test a
smaller extraction-only model (`qwen2.5:3b-instruct`) as an explicit, benchmarked experiment if the single-model
round-trip proves too slow — never as a silent default.

---

## EXECUTION INSTRUCTIONS
Output files in this order, waiting for approval between each:
`races_catalog.json` → `classes_catalog.json` → `backgrounds_catalog.json` → `item_catalog.json` →
`spell_catalog.json` → `shop_catalog.json` → `monster_catalog.json` → `dungeon_data.json` →
`character_creator.py` → `validation.py` → `state_manager.py` → `dungeon_manager.py` → `combat_manager.py` →
`memory_manager.py` → `llm_handler.py` → `app.py` → `setup.py`.

**Priority order if time runs out before finishing everything:** core loop (creation → explore → round-based
combat → save/load) is non-negotiable. Death/downed-outcome system (PART 7) is the tone-defining feature —
build it before XP/spellcasting/shop/status-effects/dismissal. Those five are independent of each other and
of the core loop — build in whatever order is convenient, cut any of them without breaking anything else if
time runs short. World events (PART 3 optional) and session recap (PART 12 optional) are the first things to
cut if pressed for time.

Flag any place you had to deviate from this spec due to a technical constraint, rather than silently changing
behavior. This is a solo single-player build — no multiplayer, no concurrent-write handling needed anywhere
in the stack.
