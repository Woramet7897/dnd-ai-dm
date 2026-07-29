"""
phase5_tests.py — Phase 5 Definition of Done tests for memory_manager.py

Tests cover every explicit spec requirement from Part 12 / Section 14:
  TEST 1  — ChromaDB PersistentClient initialises without error
  TEST 2  — Embedding model loads and produces correct-dimension vectors
  TEST 3  — add_turn() short-term window management
  TEST 4  — Auto-summarize OLDEST 2 turns when window exceeds 6
  TEST 5  — Disk persistence: data survives client restart (same path, new singleton)
  TEST 6  — get_relevant_lore() hard cap at 3 (with > 3 candidates stored)
  TEST 7  — minor→major consolidation fires at EXACTLY 20 (not 19, not 21)
  TEST 8  — Archived minors survive (not deleted) but excluded from queries
  TEST 9  — get_relevant_lore() returns combined major+minor, AT MOST 3 total
  TEST 10 — Module-level helper wrappers work correctly

Strategy for DB isolation:
  Each test section uses its own unique DB subdirectory so shutil.rmtree never
  has to delete a database that ChromaDB still holds open.  Between tests we
  call reset_singletons() so memory_manager picks up the new path on next call.

Pre-clean strategy:
  Leftover DB dirs from a previous run are wiped HERE, before 'import memory_manager',
  so no ChromaDB clients exist yet and Windows file locks are absent.
  End-of-run cleanup is best-effort (same-process locks may still hold).
"""

import gc
import os
import shutil
import sys
import time

PASS = 0
FAIL = 0

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB = {n: os.path.join(BASE_DIR, f"db_test_p5_t{n}") for n in range(1, 11)}
ALL_TEST_DBS = list(DB.values())

# ── Pre-clean: wipe any leftover test DBs from a previous run.
#    Must happen BEFORE 'import memory_manager' so no ChromaDB clients
#    exist in this process yet — Windows file locks are guaranteed absent.
for _path in ALL_TEST_DBS:
    if os.path.exists(_path):
        shutil.rmtree(_path, ignore_errors=True)


def check(description: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {description}")
    else:
        FAIL += 1
        info = f"  ({detail})" if detail else ""
        print(f"  [FAIL] {description}{info}")


def fresh_mm(character_name: str, test_num: int) -> "memory_manager.MemoryManager":
    """Create a MemoryManager pointed at the test-specific DB directory."""
    return memory_manager.MemoryManager(
        character_name=character_name,
        db_path=DB[test_num],
    )


def switch_test(next_test_num: int) -> None:
    """
    Reset the module-level singleton so the next test's fresh_mm() call
    opens a brand-new client pointed at DB[next_test_num].
    The old client and its DB files are left alone (no rmtree needed).
    """
    memory_manager.reset_singletons()
    gc.collect()   # encourage Python to release any lingering file handles


def cleanup_all_dbs() -> None:
    """Remove all temporary DB directories created during the test run."""
    memory_manager.reset_singletons()
    gc.collect()
    time.sleep(0.3)   # let SQLite flush before we delete
    for path in ALL_TEST_DBS:
        if os.path.exists(path):
            for attempt in range(6):
                try:
                    shutil.rmtree(path)
                    break
                except PermissionError:
                    gc.collect()
                    time.sleep(0.5 * (attempt + 1))


# ── Import ────────────────────────────────────────────────────────────────────
import memory_manager  # noqa: E402


# =============================================================================
print("=" * 65)
print("TEST 1 — ChromaDB PersistentClient initialises without error")
print("=" * 65)

switch_test(1)
client = None
try:
    client = memory_manager.get_chroma_client(DB[1])
    check("PersistentClient is not None", client is not None)
    hb = client.heartbeat()
    check("client.heartbeat() returns an integer nanoseconds value",
          isinstance(hb, int) and hb > 0, f"got: {hb}")
except Exception as e:
    check("PersistentClient created without exception", False, str(e))
print()


# =============================================================================
print("=" * 65)
print("TEST 2 — Embedding model loads and produces correct-dimension vectors")
print("=" * 65)

# Reuse TEST 1's client/path — no switch needed.
model = None
try:
    model = memory_manager.get_embedding_model(memory_manager.EMBEDDING_MODEL_NAME)
    check("Model object is not None", model is not None)
    embedding = model.encode("test sentence").tolist()
    check("Embedding is a non-empty list",
          isinstance(embedding, list) and len(embedding) > 0)
    # all-MiniLM-L6-v2 outputs 384-dim vectors
    check("Embedding dimension is 384", len(embedding) == 384,
          f"got {len(embedding)}")
    check("All embedding values are floats",
          all(isinstance(v, float) for v in embedding[:10]))
    check("Model name matches spec exactly",
          memory_manager.EMBEDDING_MODEL_NAME
          == "sentence-transformers/all-MiniLM-L6-v2")
except Exception as e:
    check("Embedding model loaded without exception", False, str(e))
print()


# =============================================================================
print("=" * 65)
print("TEST 3 — add_turn() short-term window management")
print("=" * 65)

switch_test(3)
mm3 = fresh_mm("HeroT3", 3)

for i in range(4):
    mm3.add_turn(f"Turn {i+1}: Something happened.")

check("SHORT_TERM_MAX_TURNS == 6 (spec)", memory_manager.SHORT_TERM_MAX_TURNS == 6)
check("SUMMARIZE_OLDEST_N == 2 (spec)",   memory_manager.SUMMARIZE_OLDEST_N == 2)
check("After 4 turns, short_term has 4 entries", len(mm3.short_term_turns) == 4)
check("short_term[0] is Turn 1",
      mm3.short_term_turns[0] == "Turn 1: Something happened.")

mm3.add_turn("Turn 5: Another event.")
mm3.add_turn("Turn 6: Yet another event.")
check("After 6 turns, short_term has 6 entries", len(mm3.short_term_turns) == 6)
print()


# =============================================================================
print("=" * 65)
print("TEST 4 — Auto-summarize OLDEST 2 when window exceeds 6")
print("=" * 65)

# Continue with mm3 (same path / same singleton)
mm3.add_turn("Turn 7: Overflow — should trigger summarization.")

st_after = mm3.short_term_turns
print(f"\n  Short-term after 7th turn: {len(st_after)} entries")
if st_after:
    print(f"  First entry now: '{st_after[0][:60]}'")

check("After 7th turn, oldest 2 flushed — short_term has 5 entries",
      len(st_after) == 5)
check("Flushed: Turn 1 no longer in short_term",
      not any("Turn 1:" in t for t in st_after))
check("Flushed: Turn 2 no longer in short_term",
      not any("Turn 2:" in t for t in st_after))
check("Turn 3 still present (not flushed)",
      any("Turn 3:" in t for t in st_after))
check("Turn 7 present in short_term",
      any("Turn 7:" in t for t in st_after))

minor_ids = mm3.get_all_minor_lore_ids(include_archived=False)
print(f"  Active minor_lore entries: {len(minor_ids)}")
check("One minor_lore entry created from flushed turns",
      len(minor_ids) == 1)
print()


# =============================================================================
print("=" * 65)
print("TEST 5 — Disk persistence: data survives client restart")
print("=" * 65)

switch_test(5)
mm5_write = fresh_mm("PersistHero", 5)
mm5_write.add_turn("Chapter 1: The hero began their journey in the tavern.")
mm5_write.add_turn("Chapter 2: A mysterious stranger approached.")
for i in range(3, 9):   # turns 3-8 — 7th turn triggers a flush
    mm5_write.add_turn(f"Chapter {i}: Events unfolded further.")

minor_ids_before = mm5_write.get_all_minor_lore_ids(include_archived=False)
print(f"\n  Minor lore entries before 'restart': {len(minor_ids_before)}")
check("At least 1 minor_lore entry before 'restart'",
      len(minor_ids_before) >= 1)

# ── Simulate process restart: clear singletons, leave DB files on disk ──────
# Also discard the MemoryManager (its collection refs hold the client open
# until GC; we force GC here before re-opening).
del mm5_write
memory_manager.reset_singletons()
gc.collect()
time.sleep(0.2)

# Re-open with the SAME db_path — simulates a new process opening the data.
mm5_read = memory_manager.MemoryManager(
    character_name="PersistHero",
    db_path=DB[5],
)
minor_ids_after = mm5_read.get_all_minor_lore_ids(include_archived=False)
print(f"  Minor lore entries after 'restart': {len(minor_ids_after)}")
check("Data persists: same count of minor_lore entries after restart",
      len(minor_ids_after) == len(minor_ids_before),
      f"before={len(minor_ids_before)}, after={len(minor_ids_after)}")

lore = mm5_read.get_relevant_lore("tavern journey mysterious stranger")
check("get_relevant_lore() returns ≥1 result after restart",
      len(lore) >= 1)
check("Returned lore entries have 'text' key",
      all("text" in entry for entry in lore))
print()


# =============================================================================
print("=" * 65)
print("TEST 6 — get_relevant_lore() hard cap at 3 (with > 3 candidates)")
print("=" * 65)

switch_test(6)
mm6 = fresh_mm("CapHero", 6)

topics = [
    "The goblin camp was raided last night.",
    "A merchant caravan arrived from the south.",
    "The blacksmith went missing near the river.",
    "Strange lights were seen in the forest.",
    "A travelling bard told of the Dragon King.",
    "The village elder revealed a secret passage.",
]
for text in topics:
    mm6._store_minor_lore(text)

active_count = len(mm6.get_all_minor_lore_ids(include_archived=False))
print(f"\n  Stored {active_count} minor_lore candidates.")
check("6 candidates stored (more than the 3-entry cap)",
      active_count == 6)

lore6 = mm6.get_relevant_lore("goblin merchant blacksmith dragon", n_results=3)
print(f"  get_relevant_lore() returned {len(lore6)} entries.")
for entry in lore6:
    print(f"    [{entry['type']}] {entry['text'][:60]}")
check("get_relevant_lore() returns AT MOST 3 entries with 6 candidates",
      len(lore6) <= 3)
check("get_relevant_lore() returns at least 1 entry",
      len(lore6) >= 1)

lore6b = mm6.get_relevant_lore("dragon king", n_results=2)
check("n_results=2 cap respected", len(lore6b) <= 2)

lore6c = mm6.get_relevant_lore("anything", n_results=0)
check("n_results=0 returns empty list", len(lore6c) == 0)
print()


# =============================================================================
print("=" * 65)
print("TEST 7 — Consolidation fires at EXACTLY 20 minor_lore entries")
print("=" * 65)

switch_test(7)
mm7 = fresh_mm("ConsolidateHero", 7)

# Store 19 minor entries — threshold not yet reached
for i in range(19):
    mm7._store_minor_lore(f"Minor event {i+1}: something happened.")

active_19 = len(mm7.get_all_minor_lore_ids(include_archived=False))
major_19  = len(mm7.get_all_major_lore_ids())
print(f"\n  After 19 minors: active={active_19}, major={major_19}")
check("MINOR_LORE_CONSOLIDATE_AT == 20 (spec)",
      memory_manager.MINOR_LORE_CONSOLIDATE_AT == 20)
check("At 19 minors: NO consolidation yet (major_count == 0)",
      major_19 == 0, f"major_count={major_19}")
check("At 19 minors: all 19 still active",
      active_19 == 19, f"active_count={active_19}")

# Add the 20th — consolidation MUST fire now
mm7._store_minor_lore("Minor event 20: the threshold is reached.")
mm7._maybe_consolidate()

active_20   = len(mm7.get_all_minor_lore_ids(include_archived=False))
major_20    = len(mm7.get_all_major_lore_ids())
all_minor   = len(mm7.get_all_minor_lore_ids(include_archived=True))
print(f"  After 20th + consolidation: active={active_20}, major={major_20}, "
      f"all_minor(incl.archived)={all_minor}")
check("Exactly 1 major_lore entry created at the 20th minor",
      major_20 == 1, f"major_count={major_20}")
check("All 20 minors still exist in storage (archived, not deleted)",
      all_minor == 20, f"total_including_archived={all_minor}")
check("Active (non-archived) minor count == 0 after consolidation",
      active_20 == 0, f"active_count={active_20}")

# Edge: 21st minor should NOT trigger another consolidation yet
mm7._store_minor_lore("Minor event 21: one after consolidation.")
mm7._maybe_consolidate()
active_21 = len(mm7.get_all_minor_lore_ids(include_archived=False))
major_21  = len(mm7.get_all_major_lore_ids())
print(f"  After 21st minor: active={active_21}, major={major_21}")
check("At 21 total (1 active after consolidation): still only 1 major",
      major_21 == 1, f"major_count={major_21}")
check("21st minor is the only active entry",
      active_21 == 1, f"active_count={active_21}")
print()


# =============================================================================
print("=" * 65)
print("TEST 8 — Archived minors survive but are excluded from queries")
print("=" * 65)

switch_test(8)
mm8 = fresh_mm("ArchiveHero", 8)

archived_id = mm8._store_minor_lore("This entry will be archived.", archived=True)
active_id   = mm8._store_minor_lore("This entry stays active.")

all_ids    = mm8.get_all_minor_lore_ids(include_archived=True)
active_ids = mm8.get_all_minor_lore_ids(include_archived=False)
print(f"\n  All minor IDs (incl. archived): {len(all_ids)}")
print(f"  Active minor IDs: {len(active_ids)}")
check("Archived entry still exists in storage",
      archived_id in all_ids)
check("Archived entry NOT in active query results",
      archived_id not in active_ids)
check("Active entry present in active query",
      active_id in active_ids)

meta = mm8.get_minor_lore_metadata(archived_id)
check("Archived entry has metadata archived='true'",
      meta.get("archived") == "true", f"meta={meta}")

lore8 = mm8.get_relevant_lore("archived content")
returned_ids = [e["id"] for e in lore8]
check("get_relevant_lore() does NOT return archived entries",
      archived_id not in returned_ids,
      f"archived_id={archived_id}, returned={returned_ids}")
print()


# =============================================================================
print("=" * 65)
print("TEST 9 — get_relevant_lore() combined major+minor, AT MOST 3 total")
print("=" * 65)

switch_test(9)
mm9 = fresh_mm("CombinedHero", 9)

mm9._store_major_lore("Chapter A: The hero defeated the goblin king.")
mm9._store_major_lore("Chapter B: The village was saved from bandits.")
for i in range(4):
    mm9._store_minor_lore(f"Minor lore {i+1}: a small event occurred.")

major_count = len(mm9.get_all_major_lore_ids())
minor_count = len(mm9.get_all_minor_lore_ids(include_archived=False))
print(f"\n  Stored {major_count} major + {minor_count} minor entries.")
check("2 major entries stored", major_count == 2)
check("4 minor entries stored", minor_count == 4)

lore9 = mm9.get_relevant_lore("hero village goblin bandits", n_results=3)
print(f"  Combined query returned {len(lore9)} entries (max 3).")
for entry in lore9:
    print(f"    [{entry['type']}] {entry['text'][:60]}")

check("Combined query returns AT MOST 3 total (not 3 major + 3 minor)",
      len(lore9) <= 3)
check("At least 1 entry returned",
      len(lore9) >= 1)
check("All entries have required keys: text, type, id",
      all(all(k in e for k in ("text", "type", "id")) for e in lore9))
check("Entry types are 'major' or 'minor'",
      all(e["type"] in ("major", "minor") for e in lore9))
print()


# =============================================================================
print("=" * 65)
print("TEST 10 — Module-level helper wrappers work correctly")
print("=" * 65)

switch_test(10)
mm10 = fresh_mm("WrapperHero", 10)

check("memory_manager.add_turn wrapper exists",
      callable(getattr(memory_manager, "add_turn", None)))
check("memory_manager.get_relevant_lore wrapper exists",
      callable(getattr(memory_manager, "get_relevant_lore", None)))

for i in range(6):
    memory_manager.add_turn(mm10, f"Wrapper turn {i+1}.")
check("After 6 via module wrapper, short_term has 6",
      len(mm10.short_term_turns) == 6)

memory_manager.add_turn(mm10, "Wrapper turn 7 — triggers flush.")
check("After 7th via module wrapper, short_term has 5",
      len(mm10.short_term_turns) == 5)

lore10 = memory_manager.get_relevant_lore(mm10, "wrapper events")
check("Module-level get_relevant_lore returns a list",
      isinstance(lore10, list))
check("Module-level get_relevant_lore respects 3-entry cap",
      len(lore10) <= memory_manager.MAX_RELEVANT_LORE_RESULTS)
check("MAX_RELEVANT_LORE_RESULTS == 3 (spec)",
      memory_manager.MAX_RELEVANT_LORE_RESULTS == 3)
print()


# =============================================================================
# Cleanup
print("Cleaning up test databases …")
cleanup_all_dbs()
print("  Done.")
print()

print("=" * 65)
print(f"RESULTS:  {PASS} passed,  {FAIL} failed")
print("=" * 65)
sys.exit(0 if FAIL == 0 else 1)
