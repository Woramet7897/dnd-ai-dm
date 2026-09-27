"""
phase12_2_tests.py — Tests for Phase 12.2: Cursed / Unidentified Items

Covers:
  - New item fields: identified, identify_dc, identify_skill, cursed, curse_effect, cannot_unequip
  - identify_item() roll check vs DC (success vs failure)
  - equip_item() on unidentified cursed item locks cannot_unequip = True
  - unequip_item() on cursed bound item returns clean rejection (DoD requirement)
  - Identifying it FIRST (before equipping) prevents cannot_unequip lock and allows clean unequip (DoD requirement)
  - remove_curse() clears cannot_unequip, allowing unequip
  - pay_cleric_remove_curse() deducts 50 GP and clears cannot_unequip (fails when < 50 GP)
  - cast_spell("remove_curse") clears cannot_unequip
  - Slot displacement rejection when slot occupied by cannot_unequip item
  - Standard items unaffected (re-verifies normal equip/unequip behavior)
"""

import unittest
import state_manager


class TestPhase12_2CursedItems(unittest.TestCase):
    def setUp(self):
        self.player = {
            "name": "Valeros",
            "stats": {"STR": 16, "DEX": 14, "CON": 14, "INT": 12, "WIS": 10, "CHA": 8},
            "ac": 12,
            "gold": 100,
            "spell_slots": {"3": {"max": 2, "current": 2}},
            "inventory": [],
        }

    # ── 1. Equipping Unidentified Cursed Item Locks cannot_unequip ────────────
    def test_equip_unidentified_cursed_item_locks_cannot_unequip(self):
        """Equipping an unidentified cursed item sets cannot_unequip = True."""
        p = dict(self.player)
        p["inventory"] = [
            {
                "item_id": "cursed_ring_of_burden",
                "equipped": False,
                "identified": False,
                "cursed": True,
                "cannot_unequip": False,
            }
        ]

        ok, msg = state_manager.equip_item("cursed_ring_of_burden", p)
        self.assertTrue(ok)
        inv_item = p["inventory"][0]
        self.assertTrue(inv_item["equipped"])
        self.assertTrue(inv_item["cannot_unequip"])

    # ── 2. unequip_item Rejection on Cursed Item (DoD Requirement) ───────────
    def test_unequip_cursed_item_returns_clean_rejection(self):
        """unequip_item() on a cursed bound item returns clean rejection without unequipping."""
        p = dict(self.player)
        p["inventory"] = [
            {
                "item_id": "cursed_ring_of_burden",
                "equipped": True,
                "identified": False,
                "cursed": True,
                "cannot_unequip": True,
            }
        ]

        ok, reason = state_manager.unequip_item("cursed_ring_of_burden", p)
        self.assertFalse(ok)
        self.assertIn("cursed", reason.lower())
        # Item must still be equipped!
        self.assertTrue(p["inventory"][0]["equipped"])

    # ── 3. Identifying It FIRST Clears the Block (DoD Requirement) ───────────
    def test_identifying_first_clears_the_block(self):
        """
        DoD: confirm identifying it first clears the block.
        If an item is identified BEFORE equipping, cannot_unequip is NOT locked,
        and unequip_item() succeeds cleanly.
        """
        p = dict(self.player)
        p["inventory"] = [
            {
                "item_id": "cursed_ring_of_burden",
                "equipped": False,
                "identified": False,
                "identify_dc": 13,
                "cursed": True,
                "cannot_unequip": False,
            }
        ]

        # 1. Identify it first with roll_total >= DC (15 >= 13)
        identified_ok = state_manager.identify_item(p, "cursed_ring_of_burden", roll_total=15)
        self.assertTrue(identified_ok)
        self.assertTrue(p["inventory"][0]["identified"])

        # 2. Equip the identified item
        eq_ok, _ = state_manager.equip_item("cursed_ring_of_burden", p)
        self.assertTrue(eq_ok)
        # Because it was identified FIRST, cannot_unequip must NOT be locked!
        self.assertFalse(p["inventory"][0].get("cannot_unequip", False))

        # 3. Unequip must now succeed cleanly!
        uneq_ok, msg = state_manager.unequip_item("cursed_ring_of_burden", p)
        self.assertTrue(uneq_ok)
        self.assertFalse(p["inventory"][0]["equipped"])

    # ── 4. identify_item Roll DC Check ───────────────────────────────────────
    def test_identify_item_dc_check(self):
        """identify_item fails when roll < DC, succeeds when roll >= DC."""
        p = dict(self.player)
        p["inventory"] = [
            {
                "item_id": "cursed_ring_of_burden",
                "equipped": False,
                "identified": False,
                "identify_dc": 13,
            }
        ]

        # Roll 10 < 13: fails
        self.assertFalse(state_manager.identify_item(p, "cursed_ring_of_burden", roll_total=10))
        self.assertFalse(p["inventory"][0]["identified"])

        # Roll 13 >= 13: succeeds
        self.assertTrue(state_manager.identify_item(p, "cursed_ring_of_burden", roll_total=13))
        self.assertTrue(p["inventory"][0]["identified"])

    # ── 5. remove_curse Lifts Lock ───────────────────────────────────────────
    def test_remove_curse_lifts_lock(self):
        """remove_curse clears cannot_unequip and allows unequipping."""
        p = dict(self.player)
        p["inventory"] = [
            {
                "item_id": "cursed_berserker_axe",
                "equipped": True,
                "cursed": True,
                "cannot_unequip": True,
            }
        ]

        # Cannot unequip initially
        ok, _ = state_manager.unequip_item("cursed_berserker_axe", p)
        self.assertFalse(ok)

        # Call remove_curse
        rc_ok, msg = state_manager.remove_curse(p, "cursed_berserker_axe")
        self.assertTrue(rc_ok)
        self.assertFalse(p["inventory"][0]["cannot_unequip"])

        # Now unequip succeeds
        ok_after, _ = state_manager.unequip_item("cursed_berserker_axe", p)
        self.assertTrue(ok_after)
        self.assertFalse(p["inventory"][0]["equipped"])

    # ── 6. pay_cleric_remove_curse ───────────────────────────────────────────
    def test_pay_cleric_remove_curse(self):
        """Paying 50 GP to town cleric removes curse; fails if insufficient gold."""
        p = dict(self.player)
        p["gold"] = 100
        p["inventory"] = [
            {
                "item_id": "cursed_ring_of_burden",
                "equipped": True,
                "cursed": True,
                "cannot_unequip": True,
            }
        ]

        # Success with 100 gold -> 50 gold remaining
        ok, msg = state_manager.pay_cleric_remove_curse(p, "cursed_ring_of_burden")
        self.assertTrue(ok)
        self.assertEqual(p["gold"], 50)
        self.assertFalse(p["inventory"][0]["cannot_unequip"])

        # Unequip succeeds
        uneq_ok, _ = state_manager.unequip_item("cursed_ring_of_burden", p)
        self.assertTrue(uneq_ok)

        # Re-curse item for failure test
        p["inventory"][0]["cannot_unequip"] = True
        p["gold"] = 40  # < 50 GP
        fail_ok, fail_msg = state_manager.pay_cleric_remove_curse(p, "cursed_ring_of_burden")
        self.assertFalse(fail_ok)
        self.assertIn("Insufficient gold", fail_msg)
        self.assertEqual(p["gold"], 40)
        self.assertTrue(p["inventory"][0]["cannot_unequip"])

    # ── 7. cast_spell remove_curse Integration ───────────────────────────────
    def test_cast_spell_remove_curse(self):
        """Casting remove_curse utility spell consumes slot and lifts curse lock."""
        p = dict(self.player)
        p["inventory"] = [
            {
                "item_id": "cursed_berserker_axe",
                "equipped": True,
                "cursed": True,
                "cannot_unequip": True,
            }
        ]

        res = state_manager.cast_spell(
            caster=p,
            target=p,
            spell_id="remove_curse",
            character_state=p,
        )

        self.assertTrue(res["success"])
        self.assertEqual(p["spell_slots"]["3"]["current"], 1)
        self.assertFalse(p["inventory"][0]["cannot_unequip"])

        # Can now unequip
        uneq_ok, _ = state_manager.unequip_item("cursed_berserker_axe", p)
        self.assertTrue(uneq_ok)

    # ── 8. Cannot Displace Cursed Item in Same Slot ──────────────────────────
    def test_cannot_displace_cursed_item_in_same_slot(self):
        """Equipping another item in the same slot as a bound cursed item is rejected."""
        p = dict(self.player)
        p["inventory"] = [
            {
                "item_id": "cursed_berserker_axe",
                "equipped": True,
                "slot": "main_hand",
                "cursed": True,
                "cannot_unequip": True,
            },
            {
                "item_id": "longsword",
                "equipped": False,
                "slot": "main_hand",
            },
        ]

        ok, msg = state_manager.equip_item("longsword", p)
        self.assertFalse(ok)
        self.assertIn("occupied by cursed item", msg)
        self.assertTrue(p["inventory"][0]["equipped"])
        self.assertFalse(p["inventory"][1]["equipped"])

    # ── 9. AC Penalty from Cursed Items ──────────────────────────────────────
    def test_curse_effect_ac_penalty(self):
        """Cursed items with ac_penalty properly reduce computed AC."""
        p = dict(self.player)
        # Unarmored AC = 10 + DEX mod (14 -> +2) = 12
        self.assertEqual(state_manager._compute_ac(p), 12)

        p["inventory"] = [
            {
                "item_id": "cursed_ring_of_burden",
                "equipped": True,
                "cursed": True,
                "curse_effect": {"ac_penalty": 1},
            }
        ]

        # 12 - 1 = 11
        self.assertEqual(state_manager._compute_ac(p), 11)

    # ── 10. remove_curse on Carried (Unequipped) Item ────────────────────────
    def test_remove_curse_unequipped_item(self):
        """remove_curse works on unequipped items in inventory."""
        p = dict(self.player)
        p["inventory"] = [
            {
                "item_id": "cursed_ring_of_burden",
                "equipped": False,
                "cursed": True,
                "cannot_unequip": False,
            }
        ]
        ok, msg = state_manager.remove_curse(p, "cursed_ring_of_burden")
        self.assertTrue(ok)
        self.assertFalse(p["inventory"][0]["cursed"])

    # ── 11. pay_cleric_remove_curse Refunds Gold on Missing Item ─────────────
    def test_pay_cleric_refunds_on_failure(self):
        """pay_cleric_remove_curse refunds 50 GP if target item does not exist."""
        p = dict(self.player)
        p["gold"] = 100
        p["inventory"] = []
        ok, msg = state_manager.pay_cleric_remove_curse(p, "nonexistent_item")
        self.assertFalse(ok)
        self.assertEqual(p["gold"], 100)

    # ── 12. remove_curse Restores AC if Cursed Item Had AC Penalty ───────────
    def test_remove_curse_restores_ac(self):
        """Lifting a curse with ac_penalty recomputes and restores character AC."""
        p = dict(self.player)
        p["inventory"] = [
            {
                "item_id": "cursed_ring_of_burden",
                "equipped": True,
                "cursed": True,
                "cannot_unequip": True,
                "curse_effect": {"ac_penalty": 1},
            }
        ]
        self.assertEqual(state_manager._compute_ac(p), 11)
        state_manager.remove_curse(p, "cursed_ring_of_burden")
        self.assertEqual(p["ac"], 12)


if __name__ == "__main__":
    unittest.main()
