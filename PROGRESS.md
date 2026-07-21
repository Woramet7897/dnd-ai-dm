# PROGRESS.md — do not delete, read this first every session

Last updated: 2026-07-22 (Phase 1 DONE — Phase 2 IN_PROGRESS)

| Phase | Status | Files written | Notes/deviations |
|---|---|---|---|
| 0 - Skeleton + catalogs | DONE | PROGRESS.md, saves/, world_saves/, save_backups/, db/, races_catalog.json (7 races), classes_catalog.json (5 classes), backgrounds_catalog.json (7 backgrounds), item_catalog.json (15 items), spell_catalog.json (12 spells, 3 classes), shop_catalog.json (4 shops), monster_catalog.json (10 monsters), dungeon_data.json (7 rooms) | None — all DoD checks passed. |
| 1 - character_creator + validation | DONE | character_creator.py, validation.py | DEV-1: derive_stats() hardcodes Persuasion+Performance for classes with "Any" skill pool — fix at Phase 7: accept player skill choices as a parameter from the app.py multiselect widget. DEV-2: validate_extraction_output() passes world_updates through structurally (is-dict check only) — fix at Phase 3: add call to dungeon_manager.validate_new_location() once that module exists, keeping location validation rules in one place. DoD: 34/34 tests passed, exit code 0. |
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
Phase 2: Writing state_manager.py now.
