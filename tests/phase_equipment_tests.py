"""
phase_equipment_tests.py — Tests for state_manager.py Equipment & Consumables system (spec Section 7b).

Tests cover:
  1. Equip chain_mail -> AC becomes 16 regardless of DEX
  2. Equip leather_armor with DEX 16 (+3) -> AC becomes 14 (11 + 3)
  3. Equip shield on top of leather_armor -> AC becomes 16 (11 + 3 + 2)
  4. Equip a second chest item -> first chest item auto-unequips, only one chest slot active
  5. get_active_effects() aggregates ac_bonus, saving_throw_bonus, skill_bonus, etc.
  6. use_consumable("healing_potion") -> restores HP capped at max, decrements quantity, removes entry at 0
  7. use_consumable("longsword") -> fails cleanly (not a consumable), no state mutated
  8. combat_manager._player_attacks() with equipped dagger -> uses real catalog stats; unequipped dagger -> Unarmed Strike
"""

import sys
import state_manager
import combat_manager

PASS = 0
FAIL = 0

def check(description: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {description}")
    else:
        FAIL += 1
        print(f"  [FAIL] {description}")
        if detail:
            print(f"         Detail: {detail}")

print("=" * 65)
print("TEST 1 — Equip chain_mail (no DEX bonus)")
print("=" * 65)

char1 = {
    "stats": {"DEX": 18}, # +4 DEX mod
    "inventory": [
        {"item_id": "chain_mail", "equipped": False, "quantity": 1}
    ],
    "ac": 14
}

ok, msg = state_manager.equip_item("chain_mail", char1)
check("equip_item returns success", ok)
check("chain_mail sets AC to 16 (no DEX bonus)", char1["ac"] == 16, f"ac={char1['ac']}")
check("chain_mail is marked equipped", char1["inventory"][0]["equipped"] is True)

print("\n" + "=" * 65)
print("TEST 2 — Equip leather_armor (with DEX bonus)")
print("=" * 65)

char2 = {
    "stats": {"DEX": 16}, # +3 DEX mod
    "inventory": [
        {"item_id": "leather_armor", "equipped": False, "quantity": 1}
    ],
    "ac": 10
}

ok, msg = state_manager.equip_item("leather_armor", char2)
check("equip_item returns success", ok)
check("leather_armor sets AC to 14 (11 + 3)", char2["ac"] == 14, f"ac={char2['ac']}")

print("\n" + "=" * 65)
print("TEST 3 — Equip shield on top of leather_armor (+2 AC bonus)")
print("=" * 65)

char3 = {
    "stats": {"DEX": 16}, # +3 DEX mod
    "inventory": [
        {"item_id": "leather_armor", "equipped": True, "quantity": 1},
        {"item_id": "shield", "equipped": False, "quantity": 1}
    ],
    "ac": 14
}

ok, msg = state_manager.equip_item("shield", char3)
check("equip_item shield returns success", ok)
check("AC includes +2 shield bonus (11 + 3 + 2 = 16)", char3["ac"] == 16, f"ac={char3['ac']}")

print("\n" + "=" * 65)
print("TEST 4 — Equip second chest item auto-unequips first")
print("=" * 65)

char4 = {
    "stats": {"DEX": 10}, # +0 DEX mod
    "inventory": [
        {"item_id": "leather_armor", "equipped": True, "quantity": 1},
        {"item_id": "chain_mail", "equipped": False, "quantity": 1}
    ],
    "ac": 11
}

ok, msg = state_manager.equip_item("chain_mail", char4)
check("equip_item chain_mail returns success", ok)
check("leather_armor is auto-unequipped", char4["inventory"][0]["equipped"] is False)
check("chain_mail is equipped", char4["inventory"][1]["equipped"] is True)
check("AC updated to chain_mail AC (16)", char4["ac"] == 16, f"ac={char4['ac']}")

# Unequip chain_mail -> back to unarmored (10 + 0 = 10)
ok, msg = state_manager.unequip_item("chain_mail", char4)
check("unequip_item returns success", ok)
check("chain_mail is unequipped", char4["inventory"][1]["equipped"] is False)
check("AC drops back to unarmored 10", char4["ac"] == 10, f"ac={char4['ac']}")

print("\n" + "=" * 65)
print("TEST 5 — get_active_effects() aggregation")
print("=" * 65)

char5 = {
    "stats": {"DEX": 14},
    "inventory": [
        {"item_id": "shield", "equipped": True, "quantity": 1},              # ac_bonus: 2
        {"item_id": "cloak_of_protection", "equipped": True, "quantity": 1}, # ac_bonus: 1, saving_throw_bonus: 1
        {"item_id": "thieves_tools", "equipped": True, "quantity": 1},       # skill_bonus: Sleight of Hand +2
        {"item_id": "component_pouch", "equipped": True, "quantity": 1},     # enables_spellcasting: True
        {"item_id": "longsword", "equipped": False, "quantity": 1}
    ],
    "ac": 15
}

effects = state_manager.get_active_effects(char5)
check("ac_bonus_total == 3 (shield 2 + cloak 1)", effects["ac_bonus_total"] == 3, f"got {effects['ac_bonus_total']}")
check("saving_throw_bonus == 1", effects["saving_throw_bonus"] == 1, f"got {effects['saving_throw_bonus']}")
check("skill_bonus Sleight of Hand == 2", effects["skill_bonus"].get("Sleight of Hand") == 2, f"got {effects['skill_bonus']}")
check("enables_spellcasting is True", effects["enables_spellcasting"] is True)

print("\n" + "=" * 65)
print("TEST 6 — use_consumable() healing potion")
print("=" * 65)

char6 = {
    "hp": {"current": 5, "max": 20},
    "inventory": [
        {"item_id": "healing_potion", "equipped": False, "quantity": 2}
    ]
}

ok, msg, res = state_manager.use_consumable("healing_potion", char6)
check("use_consumable returns success", ok)
check("res dict has 'healed' and 'hp_now'", "healed" in res and "hp_now" in res)
check("HP increased from 5", char6["hp"]["current"] > 5 and char6["hp"]["current"] <= 20)
check("potion quantity decremented to 1", char6["inventory"][0]["quantity"] == 1)

# Use second potion
ok, msg, res = state_manager.use_consumable("healing_potion", char6)
check("use_consumable second potion returns success", ok)
check("potion removed from inventory when quantity hits 0", len(char6["inventory"]) == 0)

print("\n" + "=" * 65)
print("TEST 7 — use_consumable() on non-consumable item")
print("=" * 65)

char7 = {
    "hp": {"current": 5, "max": 20},
    "inventory": [
        {"item_id": "longsword", "equipped": False, "quantity": 1}
    ]
}

ok, msg, res = state_manager.use_consumable("longsword", char7)
check("use_consumable fails on non-consumable item", not ok)
check("HP remains unchanged", char7["hp"]["current"] == 5)
check("longsword still in inventory", len(char7["inventory"]) == 1 and char7["inventory"][0]["quantity"] == 1)

print("\n" + "=" * 65)
print("TEST 8 — combat_manager._player_attacks() equipped vs unequipped weapon")
print("=" * 65)

char8_unequipped = {
    "inventory": [
        {"item_id": "dagger", "equipped": False, "quantity": 1} # carried but NOT equipped
    ]
}

attacks_unequipped = combat_manager._player_attacks(char8_unequipped)
check("Unequipped dagger yields Unarmed Strike", len(attacks_unequipped) == 1 and attacks_unequipped[0]["name"] == "Unarmed Strike")

char8_equipped = {
    "inventory": [
        {"item_id": "dagger", "equipped": True, "quantity": 1} # EQUIPPED dagger
    ]
}

attacks_equipped = combat_manager._player_attacks(char8_equipped)
check("Equipped dagger yields Dagger attack", len(attacks_equipped) == 1 and attacks_equipped[0]["name"] == "Dagger")
check("Dagger damage comes from catalog ('1d4')", attacks_equipped[0]["damage"] == "1d4")
check("Dagger damage_type comes from catalog ('piercing')", attacks_equipped[0]["damage_type"] == "piercing")

print("\n" + "=" * 65)
print("TEST 9 — combat_manager._player_attacks() attack_bonus calculation (prof + stat mod)")
print("=" * 65)

# Character: STR 16 (+3 mod), DEX 10 (+0 mod), proficiency_bonus 2
char9_fighter = {
    "stats": {"STR": 16, "DEX": 10},
    "proficiency_bonus": 2,
    "inventory": [
        {"item_id": "longsword", "equipped": True, "quantity": 1}
    ]
}
attacks9_fighter = combat_manager._player_attacks(char9_fighter)
check("Fighter with STR 16 (+3) & prof +2 longsword gets attack_bonus == 5 exactly",
      attacks9_fighter[0]["attack_bonus"] == 5, f"got {attacks9_fighter[0]['attack_bonus']}")

# Character: STR 10 (+0 mod), DEX 16 (+3 mod), proficiency_bonus 2, Finesse weapon (Dagger)
char9_rogue = {
    "stats": {"STR": 10, "DEX": 16},
    "proficiency_bonus": 2,
    "inventory": [
        {"item_id": "dagger", "equipped": True, "quantity": 1}
    ]
}
attacks9_rogue = combat_manager._player_attacks(char9_rogue)
check("Rogue with DEX 16 (+3) & prof +2 finesse dagger gets attack_bonus == 5 exactly",
      attacks9_rogue[0]["attack_bonus"] == 5, f"got {attacks9_rogue[0]['attack_bonus']}")

# Unarmed Strike with STR 16 (+3) and prof +2
char9_unarmed = {
    "stats": {"STR": 16, "DEX": 10},
    "proficiency_bonus": 2,
    "inventory": []
}
attacks9_unarmed = combat_manager._player_attacks(char9_unarmed)
check("Unarmed Strike with STR 16 (+3) & prof +2 gets attack_bonus == 5 exactly",
      attacks9_unarmed[0]["attack_bonus"] == 5, f"got {attacks9_unarmed[0]['attack_bonus']}")

print("\n" + "=" * 65)
print(f"RESULTS:  {PASS} passed,  {FAIL} failed")
print("=" * 65)
sys.exit(0 if FAIL == 0 else 1)

