"""The cheap like loop must never treat loose search results as safe content."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import like_loop  # noqa: E402


def post(uri: str, text: str, *, did: str = "did:plc:them") -> dict:
    return {
        "uri": uri,
        "cid": f"cid-{uri}",
        "author": {"did": did, "handle": "them.example"},
        "record": {"text": text},
        "viewer": {},
    }


class PhraseMatchTests(unittest.TestCase):
    def test_phrase_match_is_boundary_safe_and_punctuation_tolerant(self):
        self.assertTrue(like_loop._contains_phrase("A SMALL-WEB page", "small web"))
        self.assertTrue(like_loop._contains_phrase("warm light over a zine", "zine"))
        self.assertFalse(like_loop._contains_phrase("warm light", "war"))
        self.assertFalse(like_loop._contains_phrase("magazine rack", "zine"))


class RejectionTests(unittest.TestCase):
    def assertRejected(self, text: str, query: str, reason: str):
        self.assertEqual(like_loop._bsky_rejection_reason(text, query), reason)

    def test_accepts_specific_on_brand_posts(self):
        examples = (
            ("I rebuilt my little Neocities page after work.", "neocities"),
            ("Today's fountain-pen and paper combination.", "fountain pen"),
            ("This zine is full of tiny hand-drawn maps.", "zine"),
            ("My art journal has tiny maps in every margin.", "art journal"),
        )
        for text, query in examples:
            with self.subTest(text=text):
                self.assertIsNone(like_loop._bsky_rejection_reason(text, query))

    def test_rejects_loose_search_result_without_the_selected_interest(self):
        self.assertRejected(
            "Digital marketing agency Singapore, services for every business.",
            "small web",
            "off_topic",
        )

    def test_rejects_empty_and_too_short_posts(self):
        self.assertRejected("", "zine", "empty_or_too_short")
        self.assertRejected("nice zine", "zine", "empty_or_too_short")

    def test_rejects_at_the_start_of_text_not_just_after_a_space(self):
        self.assertRejected("Election zine for the local senate race.", "zine", "politics")

    def test_rejects_observed_off_brand_categories(self):
        cases = (
            ("A small web thread about Peter Thiel today.", "small web", "politics"),
            ("Old web memories from the war years.", "old web", "conflict"),
            ("A zine about a friend who passed away.", "zine", "loss"),
            ("Fire up the obituaries typewriter.", "typewriter", "loss"),
            ("Pixel art NFT collection launching today.", "pixel art", "finance"),
            ("NSFW mail art in the link.", "mail art", "adult"),
            ("Old browser security flaws and phishing news.", "old browser", "news_or_incident"),
            ("An OpenAI employee rebuilt an old browser.", "old browser", "ai_meta"),
            ("AI bloat sent me back to Neocities.", "neocities", "ai_meta"),
            ("Digital marketing agency for small web shops.", "small web", "promotion"),
            ("DMs open for my custom pixel art.", "pixel art", "promotion"),
            ("Make a gift to our neighborhood zine fundraiser.", "zine", "promotion"),
            ("Upgrade your desk with this typewriter keyboard combo.", "typewriter", "promotion"),
            ("New riso print on sale in my online shop.", "riso print", "promotion"),
            ("Screw social media; I moved to Neocities.", "neocities", "hostility"),
            ("A quietly devastating typewriter story with a painful ending.", "typewriter", "hostility"),
            ("My library-card zine is also an ANTIFA card.", "zine", "politics"),
            ("Vintage poster for sale, only $25.", "vintage poster", "promotion"),
        )
        for text, query, reason in cases:
            with self.subTest(text=text):
                self.assertRejected(text, query, reason)


class CandidateTests(unittest.TestCase):
    def test_candidates_are_source_relevant_deduped_and_auditable(self):
        shared = post("at://did:plc:them/app.bsky.feed.post/1", "A zine for the small-web crowd.")
        loose = post("at://did:plc:other/app.bsky.feed.post/2", "Digital marketing agency Singapore.")

        def search(term: str, _jwt: str, limit: int = 25) -> list[dict]:
            self.assertEqual(limit, 20)
            return [shared, loose]

        with (
            mock.patch.object(like_loop, "BSKY_SEARCH_TERMS", ["zine", "small web"]),
            mock.patch.object(like_loop, "_bsky_search", side_effect=search),
            mock.patch.object(like_loop.time, "sleep"),
        ):
            candidates = like_loop._bsky_like_candidates({}, "did:plc:ours", "jwt")

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["uri"], shared["uri"])
        self.assertIn(candidates[0]["term"], {"zine", "small web"})


if __name__ == "__main__":
    unittest.main()
