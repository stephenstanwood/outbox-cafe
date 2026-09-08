"""The reflection loop must only ever average posts the cafe itself made.

post_log.jsonl records two very different things under one schema: posts the
cafe published (uri = ours) and outbound gestures like acknowledging a new
follower with a like (uri = THEIRS). Scoring the second kind measures how
popular a stranger is. It did, for weeks: `follow_ack_like` led the nightly
digest at ~35x every other line, entirely on the strength of three viral posts
by other people that the cafe had clicked a single heart on — and a builder run
then wrote that asymmetry into the roadmap as the evidence for where growth
work should go.

These tests pin the units rule. The persona cases matter most: persona weights
are the one part of this loop that ACTS on the cafe's voice.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from reflect import (  # noqa: E402
    MIN_TYPE_SAMPLE,
    OWN_DID_FALLBACK,
    ROW_GESTURE,
    ROW_OFF_BSKY,
    ROW_OWN,
    _gesture_breakdown,
    _persona_multipliers,
    _summary,
    _type_breakdown,
    _wild_topics_warm,
    at_uri_did,
    classify_row,
    infer_own_did,
    is_own_post,
)

OURS = "did:plc:ourcafe"
THEIRS = "did:plc:astranger"


def uri(did: str, rkey: str = "abc") -> str:
    return f"at://{did}/app.bsky.feed.post/{rkey}"


def row(type_: str, did: str, rkey: str = "abc", **kw) -> dict:
    return {"type": type_, "uri": uri(did, rkey), **kw}


def counts(like: int = 0, reply: int = 0, repost: int = 0, quote: int = 0) -> dict:
    return {
        "like_count": like,
        "reply_count": reply,
        "repost_count": repost,
        "quote_count": quote,
    }


class AtUriParsingTests(unittest.TestCase):
    def test_parses_post_uri(self):
        self.assertEqual(at_uri_did(uri(OURS)), OURS)

    def test_bare_at_did_from_follow_loop(self):
        # follow_loop logs `at://<did>` with no collection path at all.
        self.assertEqual(at_uri_did(f"at://{THEIRS}"), THEIRS)

    def test_non_at_uris_are_none(self):
        for u in (
            "https://outbox-cafe.tumblr.com/post/827215786704814080",
            "",
            None,
            "at://",
            "at://not-a-did/app.bsky.feed.post/x",
            "notauri",
        ):
            self.assertIsNone(at_uri_did(u), u)

    def test_whitespace_tolerated(self):
        self.assertEqual(at_uri_did(f"  {uri(OURS)}  "), OURS)


class ClassifyTests(unittest.TestCase):
    def test_three_buckets(self):
        self.assertEqual(classify_row(row("drop", OURS), OURS), ROW_OWN)
        self.assertEqual(classify_row(row("follow_ack_like", THEIRS), OURS), ROW_GESTURE)
        self.assertEqual(
            classify_row({"type": "tumblr_drop", "uri": "https://x.tumblr.com/post/1"}, OURS),
            ROW_OFF_BSKY,
        )

    def test_tumblr_own_post_is_not_a_gesture(self):
        """A tumblr drop is the cafe's OWN post; it must not be reported as an
        outbound gesture just because its uri isn't attributable."""
        r = {"type": "tumblr_drop", "uri": "https://outbox-cafe.tumblr.com/post/1"}
        self.assertNotEqual(classify_row(r, OURS), ROW_GESTURE)

    def test_is_own_post_agrees_with_classify(self):
        for r in (row("drop", OURS), row("follow_ack_like", THEIRS)):
            self.assertEqual(is_own_post(r, OURS), classify_row(r, OURS) == ROW_OWN)


class InferOwnDidTests(unittest.TestCase):
    def test_infers_from_own_post_types(self):
        entries = [row("drop", OURS, "1"), row("ambient", OURS, "2"), row("follow_ack_like", THEIRS)]
        self.assertEqual(infer_own_did(entries), OURS)

    def test_majority_wins_over_a_stray(self):
        entries = [row("drop", OURS, str(i)) for i in range(5)] + [row("drop", THEIRS, "x")]
        self.assertEqual(infer_own_did(entries), OURS)

    def test_falls_back_when_no_own_posts(self):
        # Only gestures in the window — nothing to infer from.
        self.assertEqual(infer_own_did([row("follow_ack_like", THEIRS)]), OWN_DID_FALLBACK)

    def test_gestures_never_seed_inference(self):
        """A window of nothing but acks must not conclude the stranger is us."""
        entries = [row("follow_ack_like", THEIRS, str(i)) for i in range(20)]
        self.assertNotEqual(infer_own_did(entries), THEIRS)


class TypeBreakdownTests(unittest.TestCase):
    """The regression itself: a viral stranger post must not enter any average."""

    def setUp(self):
        self.entries = [
            row("drop", OURS, "d1"),
            row("drop", OURS, "d2"),
            row("follow_ack_like", THEIRS, "viral"),
        ]
        self.counts = {
            uri(OURS, "d1"): counts(like=2),
            uri(OURS, "d2"): counts(like=4),
            uri(THEIRS, "viral"): counts(like=500, reply=40),
        }

    def test_gesture_excluded_from_type_breakdown(self):
        out = _type_breakdown(self.entries, self.counts, OURS)
        self.assertIn("drop", out)
        self.assertNotIn("follow_ack_like", out)
        self.assertEqual(out["drop"]["avg_score"], 3.0)
        self.assertEqual(out["drop"]["posts"], 2)

    def test_viral_stranger_cannot_move_our_average(self):
        base = _type_breakdown(self.entries[:2], self.counts, OURS)
        withgesture = _type_breakdown(self.entries, self.counts, OURS)
        self.assertEqual(base, withgesture)


class PersonaMultiplierTests(unittest.TestCase):
    """Persona weights ACT on the cafe. A gesture must never steer a voice."""

    def test_gesture_with_a_persona_is_still_excluded(self):
        # No call site passes persona= on a gesture today. Nothing enforces
        # that but this test — so pin it rather than rely on the coincidence.
        own = [row("ambient", OURS, str(i), persona="Doris") for i in range(6)]
        own += [row("ambient", OURS, f"m{i}", persona="M.") for i in range(6)]
        c = {e["uri"]: counts(like=2) for e in own}

        gesture = row("follow_ack_like", THEIRS, "viral", persona="Doris")
        c[gesture["uri"]] = counts(like=500)

        clean = _persona_multipliers(own, c, OURS)
        polluted = _persona_multipliers(own + [gesture], c, OURS)
        self.assertEqual(clean, polluted)
        self.assertEqual(polluted["Doris"]["multiplier"], 1.0)

    def test_own_posts_still_produce_multipliers(self):
        entries = [row("ambient", OURS, f"a{i}", persona="Doris") for i in range(6)]
        entries += [row("ambient", OURS, f"b{i}", persona="Pancake") for i in range(6)]
        c = {}
        for e in entries:
            c[e["uri"]] = counts(like=10 if e["persona"] == "Doris" else 0)
        out = _persona_multipliers(entries, c, OURS)
        self.assertGreater(out["Doris"]["multiplier"], out["Pancake"]["multiplier"])


class GestureBreakdownTests(unittest.TestCase):
    def test_counts_gestures_and_distinct_accounts(self):
        entries = [
            row("follow_ack_like", THEIRS, "1", subject="@a"),
            row("follow_ack_like", THEIRS, "2", subject="@a"),
            row("follow_ack_like", THEIRS, "3", subject="@b"),
            row("drop", OURS, "d"),
        ]
        out = _gesture_breakdown(entries, OURS)
        self.assertEqual(out["follow_ack_like"], {"count": 3, "accounts": 2})
        self.assertNotIn("drop", out)

    def test_carries_no_engagement_score(self):
        out = _gesture_breakdown([row("follow_ack_like", THEIRS, subject="@a")], OURS)
        self.assertNotIn("avg_score", out["follow_ack_like"])

    def test_off_bsky_rows_are_not_gestures(self):
        entries = [{"type": "tumblr_drop", "uri": "https://x.tumblr.com/post/1", "subject": "our:f"}]
        self.assertEqual(_gesture_breakdown(entries, OURS), {})

    def test_bare_at_did_follow_rows_count(self):
        entries = [{"type": "follow", "uri": f"at://{THEIRS}", "subject": "@x"}]
        self.assertEqual(_gesture_breakdown(entries, OURS)["follow"]["count"], 1)


class WildTopicsTests(unittest.TestCase):
    def test_wild_rows_are_ours_and_still_counted(self):
        e = row("wild", OURS, "w", topic="the kettle")
        out = _wild_topics_warm([e], {e["uri"]: counts(reply=1)}, OURS)
        self.assertEqual([w["topic"] for w in out], ["the kettle"])

    def test_a_gesture_row_never_becomes_a_warm_topic(self):
        e = row("wild", THEIRS, "w", topic="not ours")
        self.assertEqual(_wild_topics_warm([e], {e["uri"]: counts(reply=9)}, OURS), [])


class SummaryTests(unittest.TestCase):
    def test_thin_types_never_lead_the_line(self):
        type_break = {
            "slip_bsky": {"avg_score": 9.9, "posts": 2},
            "drop": {"avg_score": 2.1, "posts": 50},
        }
        line = _summary({}, type_break, {}, [], sample=100)
        seg = line.split("our posts avg: ")[1].split(" | ")[0]
        self.assertTrue(seg.startswith("drop:"), seg)
        self.assertIn("n2?", seg)
        self.assertIn("n50", seg)

    def test_gestures_reported_as_counts_not_scores(self):
        line = _summary(
            {}, {"drop": {"avg_score": 2.1, "posts": 50}},
            {"follow_ack_like": {"count": 16, "accounts": 16}}, [], sample=100,
        )
        self.assertIn("gestures out: follow_ack_like:16", line)

    def test_small_sample_short_circuits(self):
        self.assertIn("sample too small", _summary({}, {}, {}, [], sample=1))

    def test_min_type_sample_is_a_real_threshold(self):
        self.assertGreater(MIN_TYPE_SAMPLE, 1)


if __name__ == "__main__":
    unittest.main()
