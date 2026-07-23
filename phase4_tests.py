"""
phase4_tests.py — combat_manager.py (Phase 4) Definition-of-Done tests.

Spec coverage:
  Conditions: apply_condition / tick_conditions (Section 20)
  Dice:       roll_dice
  Initiative: roll_initiative
  Combat:     start_combat (idempotency), end_combat, check_combat_end
  Attacks:    resolve_attack (hit, miss, crit, fumble, advantage, disadvantage,
              conditions modify rolls, condition applied on hit)
  Turns:      resolve_enemy_turn, resolve_companion_turn (stunned skip)
  Round:      classify_round_significance, build_routine_summary,
              build_round_narration_block
  Integration: full round with multiple combatants

Run: python phase4_tests.py
"""
import sys, os, random, copy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import combat_manager as cm

PASS = 0
FAIL = 0

def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        print(f"  [PASS] {label}")
        PASS += 1
    else:
        print(f"  [FAIL] {label}  {detail}")
        FAIL += 1


# ── Helpers ───────────────────────────────────────────────────────────────────
def fresh_world():
    return {
        "schema_version": 4,
        "current_location": "town_riverside",
        "visited_rooms": ["town_riverside"],
        "dynamic_rooms": {},
        "cleared_rooms": [],
        "collected_loot": [],
        "npc_relationships": {},
        "party": {"companions": []},
        "quest_log": {"main": [], "side": []},
        "combat_state": None,
    }

def fresh_player():
    return {
        "schema_version": 4,
        "name": "Star",
        "class_name": "Bard",
        "level": 1,
        "hp": {"current": 10, "max": 10},
        "ac": 12,
        "stats": {"STR": 10, "DEX": 14, "CON": 10, "INT": 10, "WIS": 10, "CHA": 16},
        "proficiency_bonus": 2,
        "proficient_skills": [],
        "proficient_saves": [],
        "concentration": None,
        "death_saves": {"success": 0, "fail": 0},
        "status": "normal",
        "gold": 10,
        "inventory": [{"item_id": "dagger", "quantity": 1}],
        "active_conditions": [],
        "roll_log": [],
    }

def goblin_combatant(idx=1):
    return {
        "id": f"goblin_scout_{idx}",
        "name": f"Goblin Scout {idx}",
        "side": "enemy",
        "hp": {"current": 7, "max": 7},
        "ac": 13,
        "stats": {"STR": 8, "DEX": 14, "CON": 10, "INT": 10, "WIS": 8, "CHA": 8},
        "attacks": [{"name": "Scimitar", "attack_bonus": 4,
                     "damage": "1d6+2", "damage_type": "slashing",
                     "applies_condition": None}],
        "initiative": None,
        "active_conditions": [],
    }

def player_combatant():
    return {
        "id": "player", "name": "Star", "side": "player",
        "hp": {"current": 10, "max": 10}, "ac": 12,
        "stats": {"STR": 10, "DEX": 14, "CON": 10, "INT": 10, "WIS": 10, "CHA": 16},
        "attacks": [{"name": "Dagger", "attack_bonus": 2,
                     "damage": "1d4+0", "damage_type": "piercing",
                     "applies_condition": None}],
        "initiative": None, "active_conditions": [],
    }


# =============================================================================
print("=" * 65)
print("TEST 1 — roll_dice()")
print("=" * 65)

results = [cm.roll_dice("1d6") for _ in range(100)]
check("1d6 always in [1,6]", all(1 <= r <= 6 for r in results))

results2 = [cm.roll_dice("2d8+4") for _ in range(100)]
check("2d8+4 always in [6,20]", all(6 <= r <= 20 for r in results2))

results3 = [cm.roll_dice("1d4-1") for _ in range(100)]
check("1d4-1 minimum is 1 (floored)", all(r >= 1 for r in results3))

try:
    cm.roll_dice("not_a_dice")
    check("Bad dice expression raises ValueError", False)
except ValueError:
    check("Bad dice expression raises ValueError", True)
print()


# =============================================================================
print("=" * 65)
print("TEST 2 — apply_condition() / tick_conditions()")
print("=" * 65)

c = goblin_combatant()
cm.apply_condition(c, "poisoned", 2)
check("poisoned applied with duration=2", len(c["active_conditions"]) == 1)
check("condition name correct", c["active_conditions"][0]["condition"] == "poisoned")
check("condition duration=2", c["active_conditions"][0]["duration"] == 2)

# Refresh keeps max duration
cm.apply_condition(c, "poisoned", 3)
check("Refresh poisoned: duration updated to 3", c["active_conditions"][0]["duration"] == 3)
cm.apply_condition(c, "poisoned", 1)
check("Refresh poisoned with shorter: duration stays at 3", c["active_conditions"][0]["duration"] == 3)

# Unknown condition rejected
cm.apply_condition(c, "paralyzed", 2)
check("Unknown condition 'paralyzed' rejected", not any(e["condition"] == "paralyzed" for e in c["active_conditions"]))

# Invalid duration rejected
cm.apply_condition(c, "prone", 0)
check("Duration=0 rejected", not any(e["condition"] == "prone" for e in c["active_conditions"]))

# tick_conditions
combat_state_tick = {
    "player_combatant": player_combatant(),
    "enemies": [c],
    "companions": [],
}
cm.tick_conditions(combat_state_tick)
check("After 1 tick, poisoned duration = 2", c["active_conditions"][0]["duration"] == 2)
cm.tick_conditions(combat_state_tick)
cm.tick_conditions(combat_state_tick)
check("After 3 total ticks, poisoned expired and removed", len(c["active_conditions"]) == 0)
print()


# =============================================================================
print("=" * 65)
print("TEST 3 — roll_initiative()")
print("=" * 65)

combatants = [goblin_combatant(1), goblin_combatant(2), player_combatant()]
result = cm.roll_initiative(combatants)
check("Returns all combatants", len(result) == 3)
check("Sorted descending by initiative", result[0]["initiative"] >= result[1]["initiative"] >= result[2]["initiative"])
check("All have integer initiative", all(isinstance(c["initiative"], int) for c in result))
check("No _initiative_tiebreak key left", all("_initiative_tiebreak" not in c for c in result))
print()


# =============================================================================
print("=" * 65)
print("TEST 4 — start_combat() + idempotency guard")
print("=" * 65)

world = fresh_world()
player = fresh_player()

cs = cm.start_combat(["goblin_scout", "goblin_scout"], player, world)
check("Returns combat_state dict", cs is not None and isinstance(cs, dict))
check("combat_state written to world_state", world["combat_state"] is cs)
check("Two enemies instantiated", len(cs["enemies"]) == 2)
check("Enemies have unique ids", cs["enemies"][0]["id"] != cs["enemies"][1]["id"])
check("Player combatant present", cs["player_combatant"]["id"] == "player")
check("Turn order has 3 entries (player + 2 enemies)", len(cs["turn_order"]) == 3)
check("Round starts at 1", cs["round"] == 1)
check("Status is 'active'", cs["status"] == "active")

# Idempotency: second call while active returns None
cs2 = cm.start_combat(["goblin_scout"], player, world)
check("Second start_combat returns None (idempotency guard)", cs2 is None)
check("combat_state not overwritten", world["combat_state"] is cs)

# Unknown monster skipped
world2 = fresh_world()
cs3 = cm.start_combat(["nonexistent_monster"], player, world2)
check("start_combat with only unknown monsters returns None", cs3 is None)
check("No combat_state written for unknown-only enemy list", world2["combat_state"] is None)

# end_combat
cm.end_combat(world)
check("end_combat clears combat_state", world["combat_state"] is None)
cm.end_combat(world)  # should not raise
check("end_combat on inactive is safe no-op", True)
print()


# =============================================================================
print("=" * 65)
print("TEST 5 — resolve_attack() — hit/miss/crit/fumble")
print("=" * 65)

# Force hit: monkeypatch _roll_d20 to return 15
original_roll = cm._roll_d20
cm._roll_d20 = lambda: 15

attacker = player_combatant()
target   = goblin_combatant()
attack   = attacker["attacks"][0]  # Dagger +2

result = cm.resolve_attack(attacker, target, attack)
check("Hit with roll=15+2=17 vs AC 13", result["hit"] is True)
check("Not a crit", result["crit"] is False)
check("Not a fumble", result["fumble"] is False)
check("Damage > 0", result["damage"] > 0)
check("target_hp_after < 7", result["target_hp_after"] < 7)
check("Result has all required keys",
      all(k in result for k in ("hit","crit","fumble","damage","damage_type",
                                 "target_id","attacker_id","attack_name",
                                 "condition_applied","target_hp_after","target_downed")))

# Force miss
cm._roll_d20 = lambda: 5
target2 = goblin_combatant()
result_miss = cm.resolve_attack(attacker, target2, attack)
check("Miss with roll=5+2=7 vs AC 13", result_miss["hit"] is False)
check("Damage is 0 on miss", result_miss["damage"] == 0)
check("target_hp unchanged on miss", result_miss["target_hp_after"] == 7)

# Force crit (roll=20)
cm._roll_d20 = lambda: 20
target3 = goblin_combatant()
result_crit = cm.resolve_attack(attacker, target3, attack)
check("Crit on roll=20", result_crit["crit"] is True)
check("Crit is always a hit", result_crit["hit"] is True)
check("Crit damage >= 2 (double dice)", result_crit["damage"] >= 2)

# Force fumble (roll=1)
cm._roll_d20 = lambda: 1
target4 = goblin_combatant()
result_fumble = cm.resolve_attack(attacker, target4, attack)
check("Fumble on roll=1", result_fumble["fumble"] is True)
check("Fumble is always a miss", result_fumble["hit"] is False)
check("Fumble damage is 0", result_fumble["damage"] == 0)

cm._roll_d20 = original_roll  # restore
print()


# =============================================================================
print("=" * 65)
print("TEST 6 — resolve_attack() — advantage/disadvantage from conditions")
print("=" * 65)

rolls = []
original_roll = cm._roll_d20

# Attacker has 'poisoned' → disadvantage → take lower of 2 rolls
attacker_p = player_combatant()
cm.apply_condition(attacker_p, "poisoned", 2)
# Simulate: two rolls 15, 5 → with disadvantage should take 5
roll_seq = iter([15, 5])
cm._roll_d20 = lambda: next(roll_seq)
target5 = goblin_combatant()
result_disadv = cm.resolve_attack(attacker_p, target5, attacker_p["attacks"][0])
check("Disadvantage (poisoned): raw_roll = min(15,5) = 5", result_disadv["raw_roll"] == 5)

# Target has 'prone' → advantage → take higher of 2 rolls
attacker_a = player_combatant()
target6 = goblin_combatant()
cm.apply_condition(target6, "prone", 2)
roll_seq2 = iter([5, 15])
cm._roll_d20 = lambda: next(roll_seq2)
result_adv = cm.resolve_attack(attacker_a, target6, attacker_a["attacks"][0])
check("Advantage (target prone): raw_roll = max(5,15) = 15", result_adv["raw_roll"] == 15)

# Both advantage and disadvantage → cancel → straight roll
attacker_b = player_combatant()
cm.apply_condition(attacker_b, "poisoned", 2)
target7 = goblin_combatant()
cm.apply_condition(target7, "prone", 2)
roll_seq3 = iter([11])
cm._roll_d20 = lambda: next(roll_seq3)
result_cancel = cm.resolve_attack(attacker_b, target7, attacker_b["attacks"][0])
check("Adv + Disadv cancel → straight roll=11", result_cancel["raw_roll"] == 11)

cm._roll_d20 = original_roll  # restore
print()


# =============================================================================
print("=" * 65)
print("TEST 7 — resolve_attack() — applies_condition on hit")
print("=" * 65)

original_roll = cm._roll_d20
cm._roll_d20 = lambda: 18  # guaranteed hit

attacker_c = goblin_combatant()
attacker_c["attacks"] = [{"name": "Poison Bite", "attack_bonus": 3,
                           "damage": "1d6+1", "damage_type": "piercing",
                           "applies_condition": "poisoned"}]
target8 = player_combatant()
result_cond = cm.resolve_attack(attacker_c, target8, attacker_c["attacks"][0])
check("Attack with applies_condition hits (roll=18)", result_cond["hit"] is True)
check("'poisoned' condition applied on hit", result_cond["condition_applied"] == "poisoned")
check("Target now has 'poisoned' in active_conditions",
      any(e["condition"] == "poisoned" for e in target8["active_conditions"]))

# On miss, condition NOT applied
cm._roll_d20 = lambda: 1  # fumble/miss
target9 = player_combatant()
result_nocond = cm.resolve_attack(attacker_c, target9, attacker_c["attacks"][0])
check("Condition NOT applied on miss/fumble", result_nocond["condition_applied"] is None)
check("Target9 has no conditions", len(target9["active_conditions"]) == 0)

cm._roll_d20 = original_roll
print()


# =============================================================================
print("=" * 65)
print("TEST 8 — resolve_enemy_turn() / resolve_companion_turn()")
print("=" * 65)

original_roll = cm._roll_d20
cm._roll_d20 = lambda: 15  # guaranteed hit

enemy = goblin_combatant()
p_c   = player_combatant()
r8    = cm.resolve_enemy_turn(enemy, [p_c])
check("Enemy turn resolves (hit)", r8.get("hit") is True)
check("Enemy target is player", r8["target_id"] == "player")

# Stunned enemy skips
stunned_enemy = goblin_combatant()
cm.apply_condition(stunned_enemy, "stunned", 1)
r_stun = cm.resolve_enemy_turn(stunned_enemy, [player_combatant()])
check("Stunned enemy skips turn", r_stun.get("skipped") is True)
check("Skip reason is 'stunned'", r_stun.get("reason") == "stunned")

# No targets → skip
r_notarget = cm.resolve_enemy_turn(enemy, [])
check("Enemy with no targets skips", r_notarget.get("skipped") is True)

# Companion turn — targets lowest HP enemy
comp = {
    "id": "gale", "name": "Gale", "side": "player",
    "hp": {"current": 8, "max": 8}, "ac": 13,
    "stats": {"STR": 10, "DEX": 12, "CON": 10, "INT": 16, "WIS": 10, "CHA": 10},
    "attacks": [{"name": "Quarterstaff", "attack_bonus": 2,
                 "damage": "1d6+1", "damage_type": "bludgeoning",
                 "applies_condition": None}],
    "initiative": None, "active_conditions": [],
}
g1 = goblin_combatant(1)
g2 = goblin_combatant(2)
g2["hp"]["current"] = 2  # g2 lower HP
r_comp = cm.resolve_companion_turn(comp, [g1, g2])
check("Companion targets lowest-HP enemy (g2)", r_comp["target_id"] == "goblin_scout_2")

# Stunned companion skips
cm.apply_condition(comp, "stunned", 1)
r_comp_stun = cm.resolve_companion_turn(comp, [g1, g2])
check("Stunned companion skips", r_comp_stun.get("skipped") is True)

cm._roll_d20 = original_roll
print()


# =============================================================================
print("=" * 65)
print("TEST 9 — check_combat_end()")
print("=" * 65)

# All enemies down → victory
cs_end = {
    "player_combatant": player_combatant(),
    "enemies": [{"id": "e1", "hp": {"current": 0, "max": 7}}],
    "companions": [],
    "status": "active", "outcome": None,
}
outcome = cm.check_combat_end(cs_end)
check("All enemies down → 'player_victory'", outcome == "player_victory")
check("combat_state status set to 'ended'", cs_end["status"] == "ended")
check("combat_state outcome = 'player_victory'", cs_end["outcome"] == "player_victory")

# Player down → defeat
p_down = player_combatant()
p_down["hp"]["current"] = 0
cs_def = {
    "player_combatant": p_down,
    "enemies": [{"id": "e1", "hp": {"current": 5, "max": 7}}],
    "companions": [],
    "status": "active", "outcome": None,
}
outcome2 = cm.check_combat_end(cs_def)
check("Player downed → 'player_defeat'", outcome2 == "player_defeat")
check("combat_state outcome = 'player_defeat'", cs_def["outcome"] == "player_defeat")

# No end condition → None
cs_cont = {
    "player_combatant": player_combatant(),
    "enemies": [{"id": "e1", "hp": {"current": 5, "max": 7}}],
    "companions": [],
    "status": "active", "outcome": None,
}
outcome3 = cm.check_combat_end(cs_cont)
check("Combat not over → returns None", outcome3 is None)
check("Status stays 'active'", cs_cont["status"] == "active")
print()


# =============================================================================
print("=" * 65)
print("TEST 10 — classify_round_significance()")
print("=" * 65)

# Build a mock combat_state for HP threshold lookups
mock_cs = {
    "player_combatant": {"id": "player", "hp": {"current": 10, "max": 10}},
    "enemies": [
        {"id": "goblin_scout_1", "hp": {"current": 2, "max": 7}},  # below 25%
    ],
    "companions": [],
}

results_list = [
    # routine hit
    {"attacker_id": "player", "attacker_name": "Star", "target_id": "goblin_scout_1",
     "target_name": "Goblin Scout 1", "attack_name": "Dagger",
     "hit": True, "crit": False, "fumble": False, "damage": 3,
     "target_hp_after": 4, "target_downed": False, "condition_applied": None,
     "damage_type": "piercing"},
    # significant — crit
    {"attacker_id": "player", "attacker_name": "Star", "target_id": "goblin_scout_1",
     "target_name": "Goblin Scout 1", "attack_name": "Dagger",
     "hit": True, "crit": True, "fumble": False, "damage": 8,
     "target_hp_after": 2, "target_downed": False, "condition_applied": None,
     "damage_type": "piercing"},
    # significant — below 25% HP (target_hp_after=2 < 7*0.25=1.75? No — 2/7=28% > 25%, so routine)
    # Let's add an explicit below-25% case
    {"attacker_id": "goblin_scout_1", "attacker_name": "Goblin Scout 1",
     "target_id": "player", "target_name": "Star",
     "hit": True, "crit": False, "fumble": False, "damage": 2,
     "target_hp_after": 1, "target_downed": False, "condition_applied": None,
     "attack_name": "Scimitar", "damage_type": "slashing"},  # 1/10 = 10% < 25%
]

classified = cm.classify_round_significance(results_list, mock_cs)
check("classify returns 'significant' and 'routine' keys",
      "significant" in classified and "routine" in classified)
sig = classified["significant"]
rout = classified["routine"]
check("Crit is significant", any(r.get("crit") for r in sig))
check("Below-25%-HP attack on player is significant",
      any(r.get("target_id") == "player" for r in sig))
check("Routine hit is in routine", len(rout) >= 1)
check("Total results = 3", len(sig) + len(rout) == 3)
print()


# =============================================================================
print("=" * 65)
print("TEST 11 — build_routine_summary() / build_round_narration_block()")
print("=" * 65)

routine_results = [
    {"hit": True, "crit": False, "fumble": False, "damage": 4,
     "attacker_name": "Star", "target_name": "Goblin", "attack_name": "Dagger",
     "damage_type": "piercing", "target_downed": False},
    {"hit": False, "crit": False, "fumble": False, "damage": 0,
     "attacker_name": "Goblin", "target_name": "Star", "attack_name": "Scimitar",
     "damage_type": "slashing", "target_downed": False},
]

summary = cm.build_routine_summary(routine_results)
check("build_routine_summary returns non-empty string", isinstance(summary, str) and len(summary) > 0)
check("Summary has 2 lines", len(summary.strip().split("\n")) == 2)

sig_results = [
    {"attacker_name": "Star", "target_name": "Goblin", "attack_name": "Dagger",
     "crit": True, "fumble": False, "target_downed": False,
     "condition_applied": None, "target_hp_after": 1, "damage": 8},
]
block = cm.build_round_narration_block(3, sig_results, summary)
check("Narration block starts with '[System: Round Result]'",
      block.startswith("[System: Round Result]"))
check("Block contains 'Round 3'", "Round 3:" in block)
check("Block ends with narration instruction",
      "Do not invent additional actions" in block)
check("Significant event (crit) included in block", "crit" in block.lower() or "critically" in block.lower())
print()


# =============================================================================
print("=" * 65)
print("TEST 12 — Integration: full round with start_combat → round → end")
print("=" * 65)

original_roll = cm._roll_d20

# Set deterministic rolls: player hits (15), enemy misses (2)
rolls_int = iter([
    10, 8, 5,   # initiative rolls for player, goblin1, goblin2
    15,         # player attacks goblin1 (hit)
    2,          # goblin1 attacks player (miss)
    2,          # goblin2 attacks player (miss)
])
cm._roll_d20 = lambda: next(rolls_int)

world_i = fresh_world()
player_i = fresh_player()

cs_i = cm.start_combat(["goblin_scout", "goblin_scout"], player_i, world_i)
check("Integration: combat started", cs_i is not None)
check("Integration: 2 enemies in combat", len(cs_i["enemies"]) == 2)

# Resolve player attacks goblin_scout_1
patt = cs_i["player_combatant"]["attacks"][0]
r_player = cm.resolve_attack(cs_i["player_combatant"], cs_i["enemies"][0], patt)
check("Integration: player hits goblin (roll=15+2 vs AC13)", r_player["hit"] is True)

# Resolve both enemies attack player
gatt1 = cs_i["enemies"][0]["attacks"][0]
r_enemy1 = cm.resolve_attack(cs_i["enemies"][0], cs_i["player_combatant"], gatt1)
check("Integration: goblin1 misses player (roll=2+4=6 vs AC12)", r_enemy1["hit"] is False)

gatt2 = cs_i["enemies"][1]["attacks"][0]
r_enemy2 = cm.resolve_attack(cs_i["enemies"][1], cs_i["player_combatant"], gatt2)
check("Integration: goblin2 misses player (roll=2+4=6 vs AC12)", r_enemy2["hit"] is False)

# Classify round
round_results = [r_player, r_enemy1, r_enemy2]
classified_i = cm.classify_round_significance(round_results, cs_i)
check("Integration: classified results non-empty", len(classified_i["significant"]) + len(classified_i["routine"]) == 3)

# Build narration block
routine_text = cm.build_routine_summary(classified_i["routine"])
block_i = cm.build_round_narration_block(1, classified_i["significant"], routine_text)
check("Integration: narration block built", "[System: Round Result]" in block_i)

# Tick conditions (no conditions active, should be a no-op)
cm.tick_conditions(cs_i)
check("Integration: tick_conditions with no conditions doesn't crash", True)

# combat not over yet (goblins have hp > 0 or player at full)
outcome_i = cm.check_combat_end(cs_i)
check("Integration: combat not over after one round of misses", outcome_i is None)

cm._roll_d20 = original_roll  # restore
print()


# =============================================================================
print("=" * 65)
print("TEST 13 — start_combat() player attack list (dagger vs unarmed)")
print("=" * 65)

# Player with dagger gets dagger attack
world_d = fresh_world()
player_d = fresh_player()  # has dagger in inventory
cs_d = cm.start_combat(["goblin_scout"], player_d, world_d)
player_attacks = cs_d["player_combatant"]["attacks"]
check("Player with dagger gets Dagger attack",
      any(a["name"] == "Dagger" for a in player_attacks))

# Player without dagger gets unarmed strike
world_u = fresh_world()
player_u = fresh_player()
player_u["inventory"] = []
cs_u = cm.start_combat(["goblin_scout"], player_u, world_u)
player_attacks_u = cs_u["player_combatant"]["attacks"]
check("Player without dagger gets Unarmed Strike",
      any(a["name"] == "Unarmed Strike" for a in player_attacks_u))
print()



# =============================================================================
print("=" * 65)
print("TEST 14 — Fix 1: quest slug id collision deduplication (state_manager)")
print("=" * 65)

import state_manager

world_slug = {
    "schema_version": 4,
    "current_location": "town_riverside",
    "visited_rooms": [],
    "dynamic_rooms": {},
    "cleared_rooms": [],
    "collected_loot": [],
    "npc_relationships": {},
    "party": {"companions": []},
    "quest_log": {"main": [], "side": []},
}

state_manager.apply_quest_updates(
    {"new_quest": {"title": "Find the Blacksmith", "quest_type": "side"}},
    world_slug,
)
state_manager.apply_quest_updates(
    {"new_quest": {"title": "Find the Blacksmith", "quest_type": "side"}},
    world_slug,
)

q1 = world_slug["quest_log"]["side"][0]
q2 = world_slug["quest_log"]["side"][1]
print(f"\n  Quest 1 id: '{q1['id']}',  Quest 2 id: '{q2['id']}'")
check("First quest gets base slug 'q_find_the_blacksmith'",
      q1["id"] == "q_find_the_blacksmith")
check("Second quest gets deduplicated id 'q_find_the_blacksmith_2'",
      q2["id"] == "q_find_the_blacksmith_2")
check("Both quests have distinct ids", q1["id"] != q2["id"])

state_manager.apply_quest_updates(
    {"objective_update": {"quest_id": q2["id"], "objective_index": 0, "done": True}},
    world_slug,
)
q1_obj = q1.get("objectives", [])
q2_obj = q2.get("objectives", [])
print(f"  q1 objectives after update on q2: {q1_obj}")
print(f"  q2 objectives after update on q2: {q2_obj}")
check("objective_update on q2 id: q1 objectives unchanged (empty)",
      len(q1_obj) == 0)
check("objective_update on q2 id: q2 objective[0].done is True",
      len(q2_obj) >= 1 and q2_obj[0]["done"] is True)

state_manager.apply_quest_updates(
    {"new_quest": {"title": "Find the Blacksmith", "quest_type": "side"}},
    world_slug,
)
q3 = world_slug["quest_log"]["side"][2]
print(f"  Quest 3 id: '{q3['id']}'")
check("Third duplicate gets 'q_find_the_blacksmith_3'",
      q3["id"] == "q_find_the_blacksmith_3")
print()


# =============================================================================
print("=" * 65)
print("TEST 15 — Fix 2: roll_dice() flat-number support + Unarmed Strike real path")
print("=" * 65)

check("roll_dice('1+0') == 1",  cm.roll_dice("1+0") == 1)
check("roll_dice('5') == 5",    cm.roll_dice("5") == 5)
check("roll_dice('3-1') == 2",  cm.roll_dice("3-1") == 2)
check("roll_dice('10+0') == 10", cm.roll_dice("10+0") == 10)
check("roll_dice('1-1') floors at 1", cm.roll_dice("1-1") == 1)

results_d6 = [cm.roll_dice("1d6") for _ in range(50)]
check("roll_dice('1d6') still works (1-6)",
      all(1 <= r <= 6 for r in results_d6))

original_roll = cm._roll_d20
cm._roll_d20 = lambda: 20  # guaranteed crit

unarmed_attacker = player_combatant()
unarmed_attacker["attacks"] = [
    {"name": "Unarmed Strike", "attack_bonus": 0, "damage": "1+0",
     "damage_type": "bludgeoning", "applies_condition": None}
]
unarmed_target = goblin_combatant()
r_unarmed = cm.resolve_attack(unarmed_attacker, unarmed_target,
                               unarmed_attacker["attacks"][0])
check("Unarmed Strike resolve_attack succeeds (no exception)", r_unarmed is not None)
check("Unarmed Strike hit=True on crit", r_unarmed["hit"] is True)
check("Unarmed Strike crit damage = 2 (1+0 doubled)", r_unarmed["damage"] == 2)

cm._roll_d20 = lambda: 15
unarmed_target2 = goblin_combatant()
r_unarmed2 = cm.resolve_attack(unarmed_attacker, unarmed_target2,
                                unarmed_attacker["attacks"][0])
check("Unarmed Strike normal hit damage = 1",
      r_unarmed2["hit"] and r_unarmed2["damage"] == 1)
cm._roll_d20 = original_roll
print()


# =============================================================================
print("=" * 65)
print("TEST 16 — Fix 3: resolve_round() full multi-round integration")
print("=" * 65)

def mk_companion(cid, name, hp=8, ac=12, atk_bonus=3, dmg="1d6+1"):
    return {
        "id": cid, "name": name, "side": "player",
        "hp": {"current": hp, "max": hp}, "ac": ac,
        "stats": {"STR": 12, "DEX": 12, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10},
        "attacks": [{"name": "Sword", "attack_bonus": atk_bonus, "damage": dmg,
                     "damage_type": "slashing", "applies_condition": None}],
        "initiative": None, "active_conditions": [],
    }

original_roll = cm._roll_d20
initiative_rolls = iter([18, 15, 12, 10, 8])
cm._roll_d20 = lambda: next(initiative_rolls)

world_rr  = fresh_world()
player_rr = fresh_player()
comp1 = mk_companion("comp_gale", "Gale")
comp2 = mk_companion("comp_lae", "Lae'zel")

cs_rr = cm.start_combat(["goblin_scout", "goblin_scout"], player_rr, world_rr,
                         companion_states=[comp1, comp2])
cs_rr["enemies"][0]["hp"] = {"current": 6, "max": 6}
cs_rr["enemies"][1]["hp"] = {"current": 6, "max": 6}

check("resolve_round setup: 2 enemies, 2 companions",
      len(cs_rr["enemies"]) == 2 and len(cs_rr["companions"]) == 2)
check("Turn order has 5 entries",
      len(cs_rr["turn_order"]) == 5)

cm.apply_condition(cs_rr["enemies"][0], "poisoned", 2)
check("Goblin1 starts poisoned (duration=2)",
      any(e["condition"] == "poisoned"
          for e in cs_rr["enemies"][0]["active_conditions"]))

# --- Round 1: player pre-attack, then resolve_round for the rest ---
r1_player_roll = iter([15])
cm._roll_d20 = lambda: next(r1_player_roll)
goblin1 = cs_rr["enemies"][0]
p_attack = cs_rr["player_combatant"]["attacks"][0]
player_r1 = cm.resolve_attack(cs_rr["player_combatant"], goblin1, p_attack)
check("Round 1 player hits goblin1 (roll=15+2 vs AC13)", player_r1["hit"] is True)

r1_other_rolls = iter([14, 3, 4, 3])
cm._roll_d20 = lambda: next(r1_other_rolls)
rr1 = cm.resolve_round(cs_rr, player_attack_result=player_r1)

check("Round 1 round_num == 1", rr1["round_num"] == 1)
check("Round 1 has all required keys",
      all(k in rr1 for k in ("round_num","significant","routine",
                              "routine_summary","narration_block","combat_outcome")))
check("Narration block correct format",
      rr1["narration_block"].startswith("[System: Round Result]"))
check("After Round 1, cs round == 2", cs_rr["round"] == 2)
check("round_log has 1 entry after round 1", len(cs_rr["round_log"]) == 1)
check("round_log[0] contains player's result",
      any(r.get("attacker_id") == "player" for r in cs_rr["round_log"][0]))
check("Round 1 combat not ended", rr1["combat_outcome"] is None)

goblin1_conds_r1 = {e["condition"]: e["duration"]
                    for e in cs_rr["enemies"][0]["active_conditions"]}
print(f"\n  Goblin1 conditions after Round 1 tick: {goblin1_conds_r1}")
check("Goblin1 poisoned ticked to duration=1",
      goblin1_conds_r1.get("poisoned") == 1)

# --- Round 2: player crits goblin1 (kills), companions kill goblin2 ---
cm._roll_d20 = lambda: 20  # everything crits
goblin1_hp_before_r2 = cs_rr["enemies"][0]["hp"]["current"]
player_r2 = cm.resolve_attack(cs_rr["player_combatant"], goblin1, p_attack)
check(f"Round 2 player crits goblin1 (was {goblin1_hp_before_r2} HP)",
      player_r2["crit"] is True)

cs_rr["enemies"][1]["hp"]["current"] = 1  # one hit kills goblin2
r2_other_rolls = iter([15, 15, 15, 15])
cm._roll_d20 = lambda: next(r2_other_rolls)
rr2 = cm.resolve_round(cs_rr, player_attack_result=player_r2)

print(f"\n  Round 2 combat_outcome: {rr2['combat_outcome']}")
check("After Round 2, cs round == 3", cs_rr["round"] == 3)
check("round_log has 2 entries after round 2", len(cs_rr["round_log"]) == 2)
check("Round 2 combat_outcome == 'player_victory'",
      rr2["combat_outcome"] == "player_victory")
check("combat_state status == 'ended'", cs_rr["status"] == "ended")

goblin1_conds_r2 = cs_rr["enemies"][0]["active_conditions"]
print(f"  Goblin1 conditions after Round 2 tick: {goblin1_conds_r2}")
check("Goblin1 poisoned expired (duration 1→0) after round 2",
      len(goblin1_conds_r2) == 0)
check("round_log has exactly 2 entries (no phantom round 3)",
      len(cs_rr["round_log"]) == 2)

cm._roll_d20 = original_roll
print()


# =============================================================================
print("=" * 65)
print(f"RESULTS:  {PASS} passed,  {FAIL} failed")
print("=" * 65)
sys.exit(0 if FAIL == 0 else 1)
