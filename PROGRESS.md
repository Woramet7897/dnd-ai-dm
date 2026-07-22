# PROGRESS.md — do not delete, read this first every session

Last updated: 2026-07-22 (Phase 3 DONE + pre-Phase 4 quest fixes)

| Phase | Status | Files written | Notes/deviations |
|---|---|---|---|
| 0 - Skeleton + catalogs | DONE | PROGRESS.md, setup.py (Ollama/pip bootstrap), requirements.txt, saves/, world_saves/, save_backups/, db/, races_catalog.json (7 races), classes_catalog.json (5 classes), backgrounds_catalog.json (7 backgrounds), item_catalog.json (15 items), spell_catalog.json (12 spells, 3 classes), shop_catalog.json (4 shops), monster_catalog.json (10 monsters), dungeon_data.json (7 rooms) | None — all DoD checks passed. Note: setup.py was in initial commit, not a numbered phase file. |
| 1 - character_creator + validation | DONE | character_creator.py, validation.py | DEV-1: derive_stats() hardcodes Persuasion+Performance for classes with "Any" skill pool — fix at Phase 7 (app.py multiselect). DEV-2: world_updates.new_location pass-through — RESOLVED Phase 3: validation.py calls dungeon_manager.validate_new_location(). DEV-3: quest_updates pass-through — RESOLVED pre-Phase 4: validate_quest_updates() added to validation.py (owner was always validation.py, not dungeon_manager). DoD: 38/38 + 32/32 fix tests passed. |
| 2 - state_manager core | DONE | state_manager.py (overwrote v1) | DEV-1: add_quest defaulted all quests to side — RESOLVED pre-Phase 4: apply_state_updates() now reads optional quest_type key ('main'|'side'). Owner was always state_manager.py, not dungeon_manager. DEV-2: _roll_d20 is a module-level function for test monkeypatching. DoD: 73/73 + 32/32 fix tests passed. |
| 3 - dungeon_manager | DONE | dungeon_manager.py; validation.py updated (DEV-2 resolved) | DEV-2 resolved: validation.py now calls dungeon_manager.validate_new_location() for world_updates.new_location. No world-event flag injection (Phase 10, optional). DoD: 45/45 tests passed, exit code 0. |
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
All phases up to 3 are DONE (+ pre-Phase 4 quest fixes committed). Next: Phase 4 (combat_manager.py).
