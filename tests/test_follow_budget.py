"""Rules for the follow loop's ratio-derived daily budget.

The loop's flat 10/day cap is a RATE limit with no notion of the posture that
rate produces: measured over 30 nights it sat pinned at 10/10 every day while
follows went 143 → 431 and followers only 47 → 82. These tests pin the brake's
behaviour — especially that it never bites the two states it must not touch
(a cold-start account, and the under-following posture the loop was built to
fix in the first place).
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from follow_loop import (  # noqa: E402
    FOLLOWS_PER_DAY,
    POSTURE_CACHE_MAX_AGE_SECONDS,
    RATIO_FLOOR_BUDGET,
    RATIO_MIN_FOLLOWS,
    _cached_posture,
    _daily_budget,
    _resolve_budget,
)


class DailyBudgetTests(unittest.TestCase):
    def budget(self, followers: int, follows: int) -> int:
        return _daily_budget(followers, follows)[0]

    def test_healthy_posture_gets_the_full_ceiling(self) -> None:
        self.assertEqual(self.budget(100, 200), FOLLOWS_PER_DAY)

    def test_runaway_posture_drops_to_a_trickle(self) -> None:
        # The cafe's actual state on 2026-09-04: 431 follows, 82 followers.
        self.assertEqual(self.budget(82, 431), RATIO_FLOOR_BUDGET)

    def test_it_throttles_but_never_stops(self) -> None:
        # A trickle keeps discovery alive and the timeline fed; a hard stop
        # would leave a cron that silently does nothing forever.
        self.assertGreater(RATIO_FLOOR_BUDGET, 0)
        self.assertGreater(self.budget(1, 10_000), 0)

    def test_tier_boundaries_are_exclusive_upper_bounds(self) -> None:
        self.assertEqual(self.budget(100, 299), FOLLOWS_PER_DAY)  # ratio 2.99
        self.assertEqual(self.budget(100, 300), 4)                # ratio 3.00
        self.assertEqual(self.budget(100, 499), 4)                # ratio 4.99
        self.assertEqual(self.budget(100, 500), RATIO_FLOOR_BUDGET)

    def test_budget_never_exceeds_the_ceiling(self) -> None:
        for followers in (0, 1, 50, 500):
            for follows in (0, 1, 50, 500, 5000):
                self.assertLessEqual(self.budget(followers, follows), FOLLOWS_PER_DAY)

    def test_budget_is_monotonic_as_the_ratio_worsens(self) -> None:
        prev = FOLLOWS_PER_DAY
        for follows in range(RATIO_MIN_FOLLOWS, 1200, 10):
            cur = self.budget(100, follows)
            self.assertLessEqual(cur, prev, f"budget rose at follows={follows}")
            prev = cur

    def test_cold_start_is_not_throttled(self) -> None:
        # 0 followers / 10 follows is nominally "10:1" but the account simply
        # has not started yet. The brake must not trap it there.
        self.assertEqual(self.budget(0, 10), FOLLOWS_PER_DAY)
        self.assertEqual(self.budget(0, 0), FOLLOWS_PER_DAY)
        self.assertEqual(self.budget(2, RATIO_MIN_FOLLOWS - 1), FOLLOWS_PER_DAY)

    def test_the_original_underfollowing_bug_is_untouched(self) -> None:
        # The loop exists because the cafe followed exactly ONE account. The
        # brake must never be the reason that recurs.
        self.assertEqual(self.budget(47, 1), FOLLOWS_PER_DAY)

    def test_ratio_is_reported_for_the_log_line(self) -> None:
        budget, ratio = _daily_budget(82, 431)
        self.assertAlmostEqual(ratio, 431 / 82, places=6)
        self.assertEqual(budget, RATIO_FLOOR_BUDGET)


class PostureFallbackTests(unittest.TestCase):
    NOW = datetime(2026, 9, 26, 12, tzinfo=timezone.utc)

    def test_live_counts_are_remembered(self) -> None:
        state = {"followed": []}
        budget, ratio, source, counts = _resolve_budget(
            state, (100, 499), now=self.NOW,
        )
        self.assertEqual((budget, source, counts), (4, "live", (100, 499)))
        self.assertAlmostEqual(ratio, 4.99)
        self.assertEqual(state["posture"], {
            "followers": 100,
            "follows": 499,
            "ts": "2026-09-26T12:00:00Z",
        })

    def test_failed_read_reuses_recent_posture(self) -> None:
        state = {
            "followed": [],
            "posture": {
                "followers": 82,
                "follows": 431,
                "ts": "2026-09-26T09:00:00Z",
            },
        }
        budget, ratio, source, counts = _resolve_budget(state, None, now=self.NOW)
        self.assertEqual((budget, source, counts),
                         (RATIO_FLOOR_BUDGET, "cached", (82, 431)))
        self.assertAlmostEqual(ratio, 431 / 82)

    def test_cached_posture_projects_follows_made_after_snapshot(self) -> None:
        state = {
            "posture": {
                "followers": 100,
                "follows": 499,
                "ts": "2026-09-26T09:00:00Z",
            },
            "followed": [
                {"did": "before", "ts": "2026-09-26T08:59:59Z"},
                {"did": "after-1", "ts": "2026-09-26T10:00:00Z"},
                {"did": "after-2", "ts": "2026-09-26T11:00:00Z"},
            ],
        }
        cached = _cached_posture(state, now=self.NOW)
        self.assertEqual(cached[:2], (100, 501))
        budget, _ratio, source, counts = _resolve_budget(state, None, now=self.NOW)
        self.assertEqual((budget, source, counts),
                         (RATIO_FLOOR_BUDGET, "cached", (100, 501)))

    def test_missing_or_expired_cache_uses_nonzero_floor(self) -> None:
        empty = {"followed": []}
        self.assertEqual(
            _resolve_budget(empty, None, now=self.NOW),
            (RATIO_FLOOR_BUDGET, None, "conservative", None),
        )

        expired_at = self.NOW - timedelta(seconds=POSTURE_CACHE_MAX_AGE_SECONDS + 1)
        expired = {
            "followed": [],
            "posture": {
                "followers": 100,
                "follows": 200,
                "ts": expired_at.isoformat().replace("+00:00", "Z"),
            },
        }
        self.assertEqual(
            _resolve_budget(expired, None, now=self.NOW),
            (RATIO_FLOOR_BUDGET, None, "conservative", None),
        )

    def test_invalid_or_future_cache_is_not_trusted(self) -> None:
        for posture in (
            {"followers": "nope", "follows": 10, "ts": "2026-09-26T09:00:00Z"},
            {"followers": 100, "follows": 200, "ts": "not-a-time"},
            {"followers": 100, "follows": 200, "ts": 123},
            {"followers": 100, "follows": 200, "ts": "2026-09-26T13:00:00Z"},
        ):
            with self.subTest(posture=posture):
                budget, ratio, source, counts = _resolve_budget(
                    {"followed": [], "posture": posture}, None, now=self.NOW,
                )
                self.assertEqual((budget, ratio, source, counts),
                                 (RATIO_FLOOR_BUDGET, None, "conservative", None))


if __name__ == "__main__":
    unittest.main()
