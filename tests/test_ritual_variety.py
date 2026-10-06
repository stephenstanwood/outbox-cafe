"""Rules for the Sunday voices' week-to-week memory (scripts/lib/ritual_variety).

Doris reviewed Plum-Cardamom 7 times in 13 columns, called October 4 "late
September", and Mr. Quiet wrote "slow mornings are not lost time" two Sundays
running. These pin the code-side choices that stop that.
"""

from __future__ import annotations

import json
import random
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib import ritual_variety as rv  # noqa: E402


def col(title: str, body: str = "") -> str:
    return f"{title}\n\n{body}\n—Doris"


class SundayDateTest(unittest.TestCase):
    def test_monday_prep_targets_that_weeks_sunday(self):
        # Prep runs Monday 2:30am; the column posts the following Sunday.
        self.assertEqual(rv.ritual_sunday(datetime(2026, 9, 28, 2, 30)), date(2026, 10, 4))

    def test_sunday_is_its_own_sunday(self):
        self.assertEqual(rv.ritual_sunday(datetime(2026, 10, 4, 15, 6)), date(2026, 10, 4))

    def test_season_phrase(self):
        self.assertEqual(rv.season_phrase(date(2026, 10, 4)), "early October")
        self.assertEqual(rv.season_phrase(date(2026, 10, 11)), "mid-October")
        self.assertEqual(rv.season_phrase(date(2026, 9, 27)), "late September")


class MuffinTest(unittest.TestCase):
    def test_recent_fruit_is_excluded(self):
        recent = [col("This Week's Muffin · Plum-Cardamom, and a Word About Patience")]
        for seed in range(50):
            m = rv.pick_muffin(date(2026, 9, 20), recent, random.Random(seed))
            self.assertNotIn("Plum", m)

    def test_flavor_is_from_the_right_month(self):
        m = rv.pick_muffin(date(2026, 12, 6), [], random.Random(1))
        self.assertIn(m, rv.MUFFINS_BY_MONTH[12])

    def test_exhausted_pool_still_returns_something(self):
        recent = [col(f"This Week's Muffin · {f}") for f in rv.MUFFINS_BY_MONTH[3]]
        self.assertIn(rv.pick_muffin(date(2026, 3, 1), recent), rv.MUFFINS_BY_MONTH[3])


class RoyTest(unittest.TestCase):
    def test_recent_jam_is_excluded(self):
        recent = [col("t", "Roy went through a period of making jam.")]
        for seed in range(50):
            self.assertNotIn("jam", rv.pick_roy_hobby(recent, random.Random(seed)))

    def test_restraint_does_not_read_as_trains(self):
        recent = [col("This Week's Muffin · X, and a Word About Restraint")]
        picks = {rv.pick_roy_hobby(recent, random.Random(s)) for s in range(200)}
        self.assertIn("model trains", picks)


class ColumnTitleTest(unittest.TestCase):
    recent = [
        "This Week's Muffin · Plum-Cardamom, and a Word About Restraint",
        "This Week's Muffin · Peach Cornmeal, and the Trouble With Ambition",
    ]

    def test_repeat_title_rejected(self):
        self.assertFalse(rv.column_title_ok(col("This week's muffin · plum cardamom, and a word about restraint"), self.recent))

    def test_repeat_descriptor_rejected(self):
        self.assertFalse(rv.column_title_ok(col("This Week's Muffin · Maple-Oat, and a Word About Restraint"), self.recent))

    def test_fresh_title_ok(self):
        self.assertTrue(rv.column_title_ok(col("This Week's Muffin · Maple-Oat, Which Took Its Time"), self.recent))


class SlipTest(unittest.TestCase):
    def test_the_real_echo_is_caught(self):
        prev = ["slow mornings are not lost time; they are how the week learns your name."]
        self.assertTrue(rv.slip_too_similar("slow mornings are not lost time; they are the week learning to stand.", prev))

    def test_shared_idea_different_words_is_caught(self):
        self.assertTrue(rv.slip_too_similar("the quiet hours are still counting in your favor.", ["the quiet hour still counts as arrival."]))

    def test_new_line_passes(self):
        prev = ["the spoon waits patiently for the cup to be ready.", "sunday keeps what the week could not carry."]
        self.assertIsNone(rv.slip_too_similar("the radiator hums the songs it was never taught.", prev))

    def test_recent_slips_reads_post_log_newest_first_deduped(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "post_log.jsonl"
            rows = [
                {"type": "slip_bsky", "text": "old line."},
                {"type": "slip_tumblr", "text": "old line."},
                {"type": "drop", "text": "not a slip."},
                {"type": "slip_bsky", "text": "new line."},
            ]
            log.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
            orig = rv.POST_LOG
            rv.POST_LOG = log
            try:
                self.assertEqual(rv.recent_slips(), ["new line.", "old line."])
            finally:
                rv.POST_LOG = orig

    def test_missing_history_is_not_fatal(self):
        orig = rv.POST_LOG
        rv.POST_LOG = Path("/nonexistent/post_log.jsonl")
        try:
            self.assertEqual(rv.recent_slips(), [])
            self.assertIn(rv.pick_slip_object([]), rv.SLIP_OBJECTS)
        finally:
            rv.POST_LOG = orig


if __name__ == "__main__":
    unittest.main()
