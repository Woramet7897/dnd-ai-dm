# PROGRESS.md — do not delete, read this first every session

Last updated: 2026-07-23 (Phase 3 DONE + pre-Phase 4 Issues A/B/C fixed)

| Phase | Status | Files written | Notes/deviations |
|---|---|---|---|
| 0 - Skeleton + catalogs | DONE | PROGRESS.md, setup.py (Ollama/pip bootstrap), requirements.txt, saves/, world_saves/, save_backups/, db/, races_catalog.json (7 races), classes_catalog.json (5 classes), backgrounds_catalog.json (7 backgrounds), item_catalog.json (15 items), spell_catalog.json (12 spells, 3 classes), shop_catalog.json (4 shops), monster_catalog.json (10 monsters), dungeon_data.json (7 rooms) | None — all DoD checks passed. Note: setup.py was in initial commit, not a numbered phase file. |
| 1 - character_creator + validation | DONE | character_creator.py, validation.py | DEV-1: derive_stats() hardcodes Persuasion+Performance for classes with "Any" skill pool — fix at Phase 7 (app.py multiselect). DEV-2: world_updates.new_location pass-through — RESOLVED Phase 3: validation.py calls dungeon_manager.validate_new_location(). DEV-3: quest_updates pass-through — RESOLVED pre-Phase 4: validate_quest_updates() now enforces spec Section 12b shape (new_quest/objective_update); old quest_id/status/notes shape removed. Issue C also resolved: state_updates.new_location renamed to move_to_location_id; existence check via dungeon_manager._all_known_room_ids() added before it reaches state_manager. DoD: 39/39 issue-fix tests + 45/45 phase3 regression tests passed. |
| 2 - state_manager core | DONE | state_manager.py (overwrote v1) | DEV-1: add_quest defaulted all quests to side — RESOLVED pre-Phase 4 (Issues A/B/C): apply_state_updates() no longer handles quest logic at all. Quest updates now go exclusively through validate_quest_updates() → apply_quest_updates() per spec Section 12b. move_to_location_id (renamed from new_location) replaces the old add_quest/remove_quest/new_location trifecta in state_updates. DEV-2: _roll_d20 is a module-level function for test monkeypatching. DoD: 73/73 + 39/39 fix tests passed. |
| 3 - dungeon_manager | DONE | dungeon_manager.py; validation.py updated (DEV-2 resolved) | DEV-2 resolved: validation.py calls dungeon_manager.validate_new_location() for world_updates.new_location. _all_known_room_ids() also now used by validation.py to verify move_to_location_id. No world-event flag injection (Phase 10, optional). DoD: 45/45 tests + 45/45 regression tests passed. |
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
All phases up to 3 are DONE (+ pre-Phase 4 Issues A/B/C fixed). Next: Phase 4 (combat_manager.py).

## Pre-Phase 4 cleanup notes (for future sessions)
- Issue A: app.py, llm_handler.py, memory_manager.py, player_state.json are in _deprecated_v1/ — v1 originals, NOT completed Phase 6/7 work. Rewrite from spec in Phase 6 (llm_handler), Phase 7 (app.py). memory_manager goes to Phase 5.
- Issue B: quest_updates path is now single and spec-aligned: validate_quest_updates() → apply_quest_updates(). The add_quest/remove_quest keys are GONE from state_updates. Never re-add them.
- Issue C: state_updates.new_location is now move_to_location_id (existing room only) with dungeon_manager existence check. world_updates.new_location (new room registration) is unchanged and separate.

