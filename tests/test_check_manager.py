"""
test_check_manager.py — Unit tests for Baldur's Gate 3 style skill checks in check_manager.py
"""

import unittest
import check_manager


class TestCheckManager(unittest.TestCase):
    def setUp(self):
        self.player = {
            "stats": {"STR": 16, "DEX": 14, "CON": 12, "INT": 10, "WIS": 15, "CHA": 8},
            "proficient_skills": ["survival", "athletics", "stealth"],
            "proficiency_bonus": 2,
        }

    def test_explicit_bracketed_tag_survival(self):
        action = "[Survival] ตามรอยเท้าสัตว์ประหลาดเข้าป่า"
        res = check_manager.detect_action_skill_check(action, self.player)
        self.assertIsNotNone(res)
        self.assertEqual(res["skill"], "survival")
        self.assertEqual(res["stat"], "WIS")
        self.assertTrue(res["is_proficient"])
        self.assertEqual(res["modifier"], 2)  # WIS 15 -> +2
        self.assertEqual(res["proficiency"], 2)
        self.assertEqual(res["total_modifier"], 4)

    def test_explicit_bracketed_tag_thai(self):
        action = "[แกะรอย] สังเกตรอยกรงเล็บบนต้นไม้"
        res = check_manager.detect_action_skill_check(action, self.player)
        self.assertIsNotNone(res)
        self.assertEqual(res["skill"], "survival")
        self.assertEqual(res["stat"], "WIS")

    def test_natural_language_tracking(self):
        action = "พยายามตามรอยก็อบลินไปตามแนวป่า"
        res = check_manager.detect_action_skill_check(action, self.player)
        self.assertIsNotNone(res)
        self.assertEqual(res["skill"], "survival")
        self.assertEqual(res["stat"], "WIS")

    def test_natural_language_climbing(self):
        action = "ปีนกำแพงหินขึ้นไปชั้นสอง"
        res = check_manager.detect_action_skill_check(action, self.player)
        self.assertIsNotNone(res)
        self.assertEqual(res["skill"], "athletics")
        self.assertEqual(res["stat"], "STR")
        self.assertTrue(res["is_proficient"])
        self.assertEqual(res["total_modifier"], 5)  # STR 16 (+3) + prof (+2) = 5

    def test_natural_language_stealth(self):
        action = "แอบย่องผ่านยามที่หน้าประตู"
        res = check_manager.detect_action_skill_check(action, self.player)
        self.assertIsNotNone(res)
        self.assertEqual(res["skill"], "stealth")
        self.assertEqual(res["stat"], "DEX")

    def test_non_check_simple_actions(self):
        non_checks = [
            "เดินไปทางทิศเหนือ",
            "มุ่งหน้ากลับหมู่บ้าน",
            "พูดคุยกับชาวบ้าน",
            "ซื้อยาฟื้นพลังจากร้านค้า",
            "พักผ่อนข้างกองไฟ",
        ]
        for a in non_checks:
            res = check_manager.detect_action_skill_check(a, self.player)
            self.assertIsNone(res, f"Action '{a}' should not require a check.")

    def test_exclusion_rules_block_casual_exploration(self):
        """Verify that casual exploration/walking phrases containing search words are excluded."""
        casual_actions = [
            "เดินสำรวจรอบเมืองเล่นๆ",
            "เดินไปมองหาทางออกจากห้อง",
            "มุ่งหน้าไปสอดส่องรอบค่าย เผื่อเจออะไร",
        ]
        for a in casual_actions:
            res = check_manager.detect_action_skill_check(a, self.player)
            self.assertIsNone(res, f"Action '{a}' should be excluded from skill checks.")

    def test_bracket_tag_not_blocked_by_exclusion(self):
        """Explicit bracketed tags like [Perception] must bypass exclusion rules and trigger checks."""
        action = "[Perception] มองหาทางออกจากห้อง"
        res = check_manager.detect_action_skill_check(action, self.player)
        self.assertIsNotNone(res)
        self.assertEqual(res["skill"], "perception")
        self.assertEqual(res["stat"], "WIS")


if __name__ == "__main__":
    unittest.main()
