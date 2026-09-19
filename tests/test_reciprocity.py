"""Attribution rules for the reciprocity pass.

The cafe's outbound gestures were bare counts with no idea whether any of
them ever came back. These pin the rules that turn them into a conversion
rate honestly: a gesture only claims an event it PRECEDED and within the
window; the cafe-went-first kind outranks the they-went-first kind; DIDs beat
handles; the first night only seeds; and the summary never counts a pre-seed
gesture on one side of the fraction but not the other.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import reciprocity as R  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 9, 20, 9, 30, tzinfo=UTC)


def g(type_, days_ago, *, did=None, handle="someone.bsky.social"):
    return {"type": type_, "ts": NOW - timedelta(days=days_ago), "did": did, "handle": handle}


class GestureMatchingTests(unittest.TestCase):
    def test_matches_by_handle_when_no_did(self):
        gs = [g("wild", 2, handle="a.bsky.social"), g("wild", 2, handle="b.bsky.social")]
        hits = R.gestures_for(None, "@A.bsky.social", gs, NOW)
        self.assertEqual([h["handle"] for h in hits], ["a.bsky.social"])

    def test_did_beats_handle_when_both_present(self):
        # Same handle, different DID: the handle was renamed onto someone else.
        gs = [g("follow", 3, did="did:plc:one", handle="x.bsky.social")]
        self.assertEqual(R.gestures_for("did:plc:two", "x.bsky.social", gs, NOW), [])
        self.assertEqual(len(R.gestures_for("did:plc:one", "renamed.bsky.social", gs, NOW)), 1)

    def test_gesture_after_event_never_claims_it(self):
        gs = [g("wild", -1)]  # one day in the future relative to the event
        self.assertEqual(R.gestures_for(None, "someone.bsky.social", gs, NOW), [])

    def test_window_floor_is_inclusive_and_bounded(self):
        inside = g("wild", R.ATTRIBUTION_DAYS)
        outside = g("wild", R.ATTRIBUTION_DAYS + 0.01)
        hits = R.gestures_for(None, "someone.bsky.social", [inside, outside], NOW)
        self.assertEqual(hits, [inside])

    def test_results_keep_oldest_first_order(self):
        gs = sorted([g("wild", 1), g("follow", 5), g("like_loop", 3)], key=lambda x: x["ts"])
        hits = R.gestures_for(None, "someone.bsky.social", gs, NOW)
        self.assertEqual([h["type"] for h in hits], ["follow", "like_loop", "wild"])


class AttributionTests(unittest.TestCase):
    def test_nothing_is_none(self):
        self.assertIsNone(R.attribute([]))

    def test_proactive_outranks_reactive_even_if_later(self):
        matches = [g("reply", 10), g("wild", 2)]  # oldest first
        self.assertEqual(R.attribute(matches)["type"], "wild")

    def test_earliest_proactive_wins(self):
        matches = [g("follow", 9), g("like_loop", 4), g("wild", 1)]
        self.assertEqual(R.attribute(matches)["type"], "follow")

    def test_reactive_only_falls_through_to_earliest_reactive(self):
        matches = [g("follow_ack_like", 8), g("reply", 2)]
        self.assertEqual(R.attribute(matches)["type"], "follow_ack_like")

    def test_every_gesture_type_is_classified(self):
        for t in R.GESTURE_TYPES:
            self.assertTrue(t in R.PROACTIVE or t in R.REACTIVE, t)
            self.assertIn(t, R.LABELS)


class FollowerDiffTests(unittest.TestCase):
    def test_new_and_lost(self):
        prev = {"did:a": {"handle": "a"}, "did:b": {"handle": "b"}}
        cur = {"did:b": "b", "did:c": "c"}
        new, lost = R.diff_followers(prev, cur)
        self.assertEqual(new, {"did:c": "c"})
        self.assertEqual(list(lost), ["did:a"])


class LoadGesturesTests(unittest.TestCase):
    def test_reads_post_log_and_like_state_and_strips_own_uri(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "post_log.jsonl"
            rows = [
                # wild row: uri is OUR reply — must not become the target DID
                {"ts": "2026-09-18T10:00:00Z", "type": "wild", "persona": "Doris",
                 "uri": "at://did:plc:cafe/app.bsky.feed.post/x", "subject": "@wildone.bsky.social"},
                {"ts": "2026-09-18T11:00:00Z", "type": "follow",
                 "uri": "at://did:plc:them", "subject": "@them.bsky.social"},
                {"ts": "2026-09-18T12:00:00Z", "type": "follow_ack_like",
                 "uri": "at://did:plc:fan/app.bsky.feed.post/y", "subject": "@fan.bsky.social"},
                # own posts are not gestures
                {"ts": "2026-09-18T13:00:00Z", "type": "drop",
                 "uri": "at://did:plc:cafe/app.bsky.feed.post/z", "subject": "our:2026.html"},
                # too old
                {"ts": "2026-01-01T00:00:00Z", "type": "wild",
                 "uri": "at://did:plc:cafe/app.bsky.feed.post/w", "subject": "@ancient.bsky.social"},
            ]
            log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
            likes = Path(d) / "like_state.json"
            likes.write_text(json.dumps({"bsky": [
                {"uri": "at://did:plc:liked/app.bsky.feed.post/q", "author": "Liked.bsky.social",
                 "ts": "2026-09-18T09:00:00Z"}
            ], "tumblr": [{"id": 1, "blog": "x", "ts": "2026-09-18T09:00:00Z"}]}))
            since = datetime(2026, 9, 1, tzinfo=UTC)
            gs = R.load_gestures(since, post_log=log, like_state=likes)
        by_type = {x["type"]: x for x in gs}
        self.assertEqual(set(by_type), {"wild", "follow", "follow_ack_like", "like_loop"})
        self.assertIsNone(by_type["wild"]["did"])
        self.assertEqual(by_type["wild"]["handle"], "wildone.bsky.social")
        self.assertEqual(by_type["follow"]["did"], "did:plc:them")
        self.assertEqual(by_type["follow_ack_like"]["did"], "did:plc:fan")
        self.assertEqual(by_type["like_loop"]["did"], "did:plc:liked")
        self.assertEqual(by_type["like_loop"]["handle"], "liked.bsky.social")
        self.assertEqual([x["type"] for x in gs], ["like_loop", "wild", "follow", "follow_ack_like"])


class SummaryTests(unittest.TestCase):
    def test_pre_floor_gestures_count_on_neither_side(self):
        floor = NOW - timedelta(days=5)
        gestures = [
            g("wild", 10, handle="old.bsky.social"),   # before floor
            g("wild", 2, handle="new.bsky.social"),    # after floor
            g("wild", 1, handle="other.bsky.social"),
        ]
        events = [
            {"ts": R._iso(NOW), "event": "follow", "did": None, "handle": "old.bsky.social",
             "attributed": "wild",
             "gestures": [{"type": "wild", "ts": R._iso(NOW - timedelta(days=10))}]},
            {"ts": R._iso(NOW), "event": "follow", "did": None, "handle": "new.bsky.social",
             "attributed": "wild",
             "gestures": [{"type": "wild", "ts": R._iso(NOW - timedelta(days=2))}]},
            {"ts": R._iso(NOW), "event": "like", "did": None, "handle": "other.bsky.social",
             "attributed": "wild",
             "gestures": [{"type": "wild", "ts": R._iso(NOW - timedelta(days=1))}]},
        ]
        line = R.summarize(events, gestures, floor)
        self.assertIn("wild reply 2 → 1 followed, 1 engaged", line)

    def test_unprompted_and_ack_like_have_no_followed_count(self):
        floor = NOW - timedelta(days=5)
        gestures = [g("follow_ack_like", 1, did="did:plc:fan", handle="fan.bsky.social")]
        events = [
            {"ts": R._iso(NOW), "event": "follow", "did": "did:plc:stranger",
             "handle": "s.bsky.social", "attributed": None, "gestures": []},
            {"ts": R._iso(NOW), "event": "reply", "did": "did:plc:fan", "handle": "fan.bsky.social",
             "attributed": "follow_ack_like",
             "gestures": [{"type": "follow_ack_like", "ts": R._iso(NOW - timedelta(days=1))}]},
        ]
        line = R.summarize(events, gestures, floor)
        self.assertIn("ack-like 1 → 1 engaged", line)
        self.assertNotIn("ack-like 1 → 0 followed", line)
        self.assertIn("unprompted follows 1", line)

    def test_inbound_line_and_silence(self):
        floor = NOW - timedelta(days=5)
        self.assertEqual(R.summarize([], [], floor), "")
        self.assertEqual(R.summarize([], [], floor, inbound_today=(0, 0, 0)), "")
        line = R.summarize([], [], floor, inbound_today=(7, 4, 2))
        self.assertIn("7 like/reply/repost from 4 account(s) — 2 the cafe had reached first", line)

    def test_one_event_credits_only_the_selected_gesture(self):
        floor = NOW - timedelta(days=5)
        gestures = [
            g("follow", 3, did="did:plc:x", handle="x.bsky.social"),
            g("wild", 2, handle="x.bsky.social"),
        ]
        events = [{
            "ts": R._iso(NOW), "event": "like", "did": "did:plc:x",
            "handle": "x.bsky.social", "attributed": "follow",
            "gestures": [
                {"type": "follow", "ts": R._iso(NOW - timedelta(days=3))},
                {"type": "wild", "ts": R._iso(NOW - timedelta(days=2))},
            ],
        }]
        line = R.summarize(events, gestures, floor)
        self.assertIn("follow 1 → 0 followed, 1 engaged", line)
        self.assertIn("wild reply 1 → 0 followed, 0 engaged", line)


class RunTests(unittest.TestCase):
    """End-to-end with the network stubbed: seed night, then a real night."""

    def _run(self, d, followers, notifs, now, post_log_rows):
        log = Path(d) / "post_log.jsonl"
        log.write_text("\n".join(json.dumps(r) for r in post_log_rows) + "\n")
        old_log, old_like = R.POST_LOG, R.LIKE_STATE
        R.POST_LOG, R.LIKE_STATE = log, Path(d) / "nope.json"
        try:
            return R.run(
                now=now,
                state_path=Path(d) / "state.json",
                events_path=Path(d) / "events.jsonl",
                login_fn=lambda: ("did:plc:cafe", "jwt"),
                fetch_followers_fn=lambda did, jwt: followers,
                fetch_notifications_fn=lambda jwt, since: notifs,
            )
        finally:
            R.POST_LOG, R.LIKE_STATE = old_log, old_like

    def test_seed_then_attribute(self):
        rows = [
            {"ts": R._iso(NOW - timedelta(hours=18)), "type": "wild",
             "uri": "at://did:plc:cafe/app.bsky.feed.post/r", "subject": "@newbie.bsky.social"},
            {"ts": R._iso(NOW - timedelta(hours=12)), "type": "follow",
             "uri": "at://did:plc:fb", "subject": "@fb.bsky.social"},
        ]
        with tempfile.TemporaryDirectory() as d:
            night1 = NOW - timedelta(days=1)
            text = self._run(d, {"did:plc:old": "old.bsky.social"}, [], night1, rows)
            self.assertIn("started tracking", text)
            state = json.loads((Path(d) / "state.json").read_text())
            self.assertEqual(list(state["followers"]), ["did:plc:old"])
            self.assertFalse((Path(d) / "events.jsonl").exists())

            followers = {
                "did:plc:old": "old.bsky.social",
                "did:plc:newbie": "newbie.bsky.social",   # wild-replied 3 days ago
                "did:plc:fb": "fb.bsky.social",           # followed 2 days ago
                "did:plc:random": "random.bsky.social",   # never touched
            }
            notifs = [
                {"reason": "like", "indexedAt": R._iso(NOW - timedelta(hours=2)),
                 "author": {"did": "did:plc:newbie", "handle": "newbie.bsky.social"}},
                {"reason": "follow", "indexedAt": R._iso(NOW - timedelta(hours=2)),
                 "author": {"did": "did:plc:random", "handle": "random.bsky.social"}},
                {"reason": "like", "indexedAt": R._iso(NOW - timedelta(hours=1)),
                 "author": {"did": "did:plc:cafe", "handle": "cafe"}},  # self, ignored
            ]
            text = self._run(d, followers, notifs, NOW, rows)
            events = [json.loads(l) for l in (Path(d) / "events.jsonl").read_text().splitlines()]
            follows = {e["handle"]: e for e in events if e["event"] == "follow"}
            self.assertEqual(follows["newbie.bsky.social"]["attributed"], "wild")
            self.assertEqual(follows["fb.bsky.social"]["attributed"], "follow")
            self.assertIsNone(follows["random.bsky.social"]["attributed"])
            likes = [e for e in events if e["event"] == "like"]
            self.assertEqual(len(likes), 1)  # the self-like was dropped
            self.assertEqual(likes[0]["attributed"], "wild")
            self.assertIn("wild reply 1 → 1 followed, 1 engaged", text)
            self.assertIn("follow 1 → 1 followed, 0 engaged", text)
            self.assertIn("unprompted follows 1", text)
            self.assertIn("inbound today: 1 like/reply/repost from 1 account(s) — 1 the cafe had reached first", text)
            state = json.loads((Path(d) / "state.json").read_text())
            self.assertEqual(len(state["followers"]), 4)
            self.assertEqual(state["followers"]["did:plc:old"]["first_seen"], R._iso(night1))
            self.assertGreater(state["notif_watermark"], R._iso(night1))

    def test_pre_seed_gesture_is_not_credited(self):
        rows = [{
            "ts": R._iso(NOW - timedelta(days=3)), "type": "wild",
            "uri": "at://did:plc:cafe/app.bsky.feed.post/r", "subject": "@newbie.bsky.social",
        }]
        with tempfile.TemporaryDirectory() as d:
            night1 = NOW - timedelta(days=1)
            self._run(d, {"did:plc:old": "old.bsky.social"}, [], night1, rows)
            self._run(d, {
                "did:plc:old": "old.bsky.social",
                "did:plc:newbie": "newbie.bsky.social",
            }, [], NOW, rows)
            events = [json.loads(l) for l in (Path(d) / "events.jsonl").read_text().splitlines()]
            follow = next(e for e in events if e["event"] == "follow")
            self.assertIsNone(follow["attributed"])
            self.assertEqual(follow["gestures"], [])

    def test_ignored_notification_advances_watermark(self):
        rows = [{
            "ts": R._iso(NOW - timedelta(hours=12)), "type": "follow_ack_like",
            "uri": "at://did:plc:fan/app.bsky.feed.post/r", "subject": "@fan.bsky.social",
        }]
        with tempfile.TemporaryDirectory() as d:
            night1 = NOW - timedelta(days=1)
            followers = {"did:plc:fan": "fan.bsky.social"}
            self._run(d, followers, [], night1, rows)
            follow_notice_at = NOW - timedelta(hours=2)
            text = self._run(d, followers, [{
                "reason": "follow", "indexedAt": R._iso(follow_notice_at),
                "author": {"did": "did:plc:fan", "handle": "fan.bsky.social"},
            }], NOW, rows)
            state = json.loads((Path(d) / "state.json").read_text())
            self.assertEqual(state["notif_watermark"], R._iso(follow_notice_at))
            self.assertNotIn("inbound today:", text)

    def test_reactive_touch_is_not_reported_as_cafe_reached_first(self):
        rows = [{
            "ts": R._iso(NOW - timedelta(hours=12)), "type": "follow_ack_like",
            "uri": "at://did:plc:fan/app.bsky.feed.post/r", "subject": "@fan.bsky.social",
        }]
        with tempfile.TemporaryDirectory() as d:
            night1 = NOW - timedelta(days=1)
            followers = {"did:plc:fan": "fan.bsky.social"}
            self._run(d, followers, [], night1, rows)
            text = self._run(d, followers, [{
                "reason": "like", "indexedAt": R._iso(NOW - timedelta(hours=2)),
                "author": {"did": "did:plc:fan", "handle": "fan.bsky.social"},
            }], NOW, rows)
            self.assertIn("1 like/reply/repost from 1 account(s) — 0 the cafe had reached first", text)

    def test_network_failure_is_quiet(self):
        with tempfile.TemporaryDirectory() as d:
            def boom(did, jwt):
                raise OSError("timed out")
            (Path(d) / "post_log.jsonl").write_text("")
            old = R.POST_LOG
            R.POST_LOG = Path(d) / "post_log.jsonl"
            try:
                text = R.run(now=NOW, state_path=Path(d) / "s.json", events_path=Path(d) / "e.jsonl",
                             login_fn=lambda: ("did", "jwt"), fetch_followers_fn=boom)
            finally:
                R.POST_LOG = old
            self.assertEqual(text, "")
            self.assertFalse((Path(d) / "s.json").exists())


if __name__ == "__main__":
    unittest.main()
