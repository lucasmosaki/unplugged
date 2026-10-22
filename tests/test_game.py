import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import Game, DataError, normalize_evidence_id, parse_command, read_json  # noqa: E402


class GameTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.data = os.path.join(self.tmp, "data")
        shutil.copytree(os.path.join(ROOT, "data"), self.data)
        self.game = Game(self.data)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def play_to_tier3(self):
        g = self.game
        g.search("KEY-B")
        g.submit("rex", "5")
        g.search("badge")
        g.submit("n0va", "7")
        g.search("02:09")


class TestLoading(GameTestCase):
    def test_json_loads_and_validates(self):
        self.assertEqual(len(self.game.evidence), 10)
        self.assertEqual(len(self.game.survey_questions()), 7)

    def test_bad_json_raises(self):
        with open(os.path.join(self.data, "story.json"), "w") as f:
            f.write("{not json")
        with self.assertRaises(DataError):
            Game(self.data)

    def test_missing_file_raises(self):
        os.remove(os.path.join(self.data, "survey_data.json"))
        with self.assertRaises(DataError):
            Game(self.data)

    def test_survey_values(self):
        self.assertEqual(self.game.survey_value(3, 2), "62.5%")
        self.assertEqual(self.game.survey_value(6, 3), "0%")


class TestParsing(GameTestCase):
    def test_aliases(self):
        self.assertEqual(parse_command("grep key")[0], "search")
        self.assertEqual(parse_command("EVIDENCE")[0], "list")
        self.assertEqual(parse_command("cat 3"), ("examine", ["3"]))

    def test_unknown_and_empty(self):
        self.assertEqual(parse_command("dance")[0], "unknown")
        self.assertEqual(parse_command("   ")[0], "")

    def test_normalize_id(self):
        for raw in ("3", "003", "evid-3", "EVID-003"):
            self.assertEqual(normalize_evidence_id(raw), "EVID-003")
        self.assertIsNone(normalize_evidence_id("abc"))


class TestUnlocks(GameTestCase):
    def test_initial_state(self):
        self.assertTrue(self.game.is_unlocked("EVID-001"))
        self.assertFalse(self.game.is_unlocked("EVID-005"))
        self.assertEqual(self.game.clearance, 1)

    def test_examine_errors_and_bonus(self):
        self.assertEqual(self.game.examine("99")[0], "missing")
        self.assertEqual(self.game.examine("9")[0], "locked")
        status, _, bonus, _ = self.game.examine("1")
        self.assertEqual((status, bonus), ("ok", 2))
        self.assertEqual(self.game.examine("1")[2], 0)

    def test_search_unlocks_registry(self):
        res = self.game.search("KEY-B")
        self.assertIn("EVID-005", res.unlocked)
        self.assertTrue(self.game.is_unlocked("EVID-005"))

    def test_search_short_keyword(self):
        self.assertEqual(self.game.search("a").error, "short")

    def test_restricted_search_does_not_unlock(self):
        res = self.game.search("02:09")
        self.assertFalse(self.game.is_unlocked("EVID-009"))
        self.assertTrue(any(e == "EVID-009" for e, _ in res.restricted))

    def test_survey_is_searchable(self):
        self.assertTrue(self.game.search("convicted").survey_hits)


class TestDeductions(GameTestCase):
    def test_wrong_submission_penalty(self):
        self.game.add_score(10)
        res = self.game.submit("n0va", "1")
        self.assertEqual(res.status, "wrong")
        self.assertEqual(self.game.profile["score"], 8)
        self.assertEqual(self.game.profile["mistakes"], 1)

    def test_bad_inputs(self):
        self.assertEqual(self.game.submit("bob", "1").status, "error")
        self.assertEqual(self.game.submit("rex", "99").status, "error")
        self.assertEqual(self.game.submit("rex", "9").status, "error")

    def test_weak_and_hint(self):
        self.assertEqual(self.game.submit("n0va", "4").status, "weak")
        self.assertEqual(self.game.submit("n0va", "2").status, "hint")

    def test_first_deduction_raises_clearance(self):
        self.game.search("KEY-B")
        res = self.game.submit("hale", "evid-5")
        self.assertEqual(res.status, "solved")
        self.assertEqual(self.game.clearance, 2)
        self.assertTrue(self.game.is_unlocked("EVID-006"))
        self.assertTrue(self.game.is_unlocked("EVID-008"))
        self.assertEqual(self.game.submit("rex", "5").status, "repeat")

    def test_final_blocked_until_suspects_cleared(self):
        self.play_to_tier3()
        self.assertTrue(self.game.is_unlocked("EVID-009"))
        self.assertEqual(self.game.submit("rex", "9").status, "blocked")
        self.assertEqual(self.game.submit("tessa", "8").status, "solved")
        res = self.game.submit("rex", "9")
        self.assertTrue(res.final)
        self.assertTrue(self.game.profile["solved"])

    def test_hints_progress(self):
        self.assertIn("search KEY-B", self.game.hint())
        self.game.search("KEY-B")
        self.assertIn("submit rex 5", self.game.hint())


class TestPersistence(GameTestCase):
    def test_progress_saved_to_json(self):
        self.game.search("KEY-B")
        saved = read_json(os.path.join(self.data, "stats.json"))
        self.assertIn("EVID-005", saved["current_profile"]["unlocked"])
        self.assertNotIn("EVID-005", saved["default_profile"]["unlocked"])
        resumed = Game(self.data, resume=True)
        self.assertTrue(resumed.is_unlocked("EVID-005"))
        self.assertFalse(Game(self.data).is_unlocked("EVID-005"))


if __name__ == "__main__":
    unittest.main()
