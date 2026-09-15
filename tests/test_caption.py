from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib import caption  # noqa: E402


PAGE = """<!doctype html><html><head><title>The Tub at Two Thirds</title>
<style>body{color:#1d1f1c}</style></head><body>
<h1>The Tub at Two Thirds</h1>
<p>Press the space bar to start the machine.</p>
<p>The second kettle was put on at 7:52 and forgotten. It is still, in some sense, ours.</p>
<p>Read more at https://example.com/thing and tell your friends.</p>
<p>This page was made with AI and it is weird and retro.</p>
<p>Photo by Someone on Unsplash</p>
<p>OK</p>
<p>“Heads is the blue side. Tails is the pink side. We checked in 1994 with a flashlight.”</p>
<script>
const LINES = [`Wrong one. Again. It comes up wrong every second time, which means it isn't random, it means this thing is folded backwards.`,
 'pounds of water, and every ounce of that gets boiled off downstream at'];
const style = {color: "#fff", padding: "6px 40px"};
</script>
</body></html>"""

CANVAS_ONLY = """<html><head><title>Two Hundred Metres of Lath</title></head>
<body><canvas id="c"></canvas><script>(function(){var W=0;function r(){W=1}})();</script></body></html>"""


class GateTests(unittest.TestCase):
    def texts(self, **kw):
        frags = caption.candidates(PAGE, min_len=40, max_len=200, ideal_lo=60, ideal_hi=160)
        return [f.text for f in frags]

    def test_house_rules_block_the_right_lines(self) -> None:
        texts = self.texts()
        joined = "\n".join(texts)
        self.assertNotIn("space bar", joined)          # UI instruction
        self.assertNotIn("https://", joined)           # URL
        self.assertNotIn("weird", joined)              # cafe never self-describes
        self.assertNotIn("AI", joined)                 # no AI talk
        self.assertNotIn("Unsplash", joined)           # photo credit
        self.assertNotIn("The Tub at Two Thirds", texts)  # title line itself
        self.assertFalse(any(t == "OK" for t in texts))   # too short

    def test_keeps_real_prose_from_dom_and_script(self) -> None:
        texts = self.texts()
        self.assertIn("The second kettle was put on at 7:52 and forgotten. It is still, in some sense, ours.", texts)
        self.assertTrue(any(t.startswith("Wrong one. Again.") for t in texts))

    def test_script_template_fragment_is_rejected(self) -> None:
        # Starts lowercase, no terminal punctuation → a template piece, not a sentence.
        self.assertFalse(any("pounds of water" in t for t in self.texts()))

    def test_code_like_literal_is_rejected(self) -> None:
        self.assertFalse(any("padding" in t for t in self.texts()))

    def test_already_quoted_line_is_unwrapped_once(self) -> None:
        texts = self.texts()
        hit = [t for t in texts if t.startswith("Heads is the blue side")]
        self.assertEqual(len(hit), 1)
        self.assertFalse(hit[0].startswith("“"))


class CaptionTests(unittest.TestCase):
    def test_bsky_caption_is_quoted_signed_and_short(self) -> None:
        text, source = caption.excerpt_caption(PAGE, "—Doris", random.Random(1), platform="bsky")
        self.assertIn(source, ("dom", "script"))
        self.assertTrue(text.startswith("“"))
        self.assertTrue(text.endswith("\n\n—Doris"))
        self.assertLessEqual(len(text), 300)
        body = text.split("\n\n")[0]
        self.assertLessEqual(len(body), 202)  # ≤200 + the two quote marks

    def test_empty_signoff_means_no_trailer(self) -> None:
        text, _ = caption.excerpt_caption(PAGE, "", random.Random(1))
        self.assertNotIn("\n\n", text)

    def test_seed_is_deterministic(self) -> None:
        a = caption.excerpt_caption(PAGE, "-M", random.Random(7))
        b = caption.excerpt_caption(PAGE, "-M", random.Random(7))
        self.assertEqual(a, b)

    def test_text_free_page_falls_back_to_title(self) -> None:
        text, source = caption.excerpt_caption(CANVAS_ONLY, "—Robin", random.Random(1))
        self.assertEqual(source, "title")
        self.assertEqual(text, "Two Hundred Metres of Lath\n\n—Robin")

    def test_no_title_and_no_prose_returns_none(self) -> None:
        self.assertIsNone(caption.excerpt_caption("<html><body><canvas></canvas></body></html>", "-j"))

    def test_never_contains_blocked_terms_on_real_archive(self) -> None:
        # Every archived page: whatever the fallback would post passes the gate.
        pages = sorted((ROOT / "archive").glob("2026-*.html"))[-40:]
        if not pages:
            self.skipTest("no archive pages checked out")
        for p in pages:
            html = p.read_text(errors="ignore")
            for platform in ("bsky", "tumblr"):
                got = caption.excerpt_caption(html, "—Greta (the machine)", random.Random(3), platform=platform)
                self.assertIsNotNone(got, p.name)
                text, source = got
                body = text.rsplit("\n\n", 1)[0]
                if source != "title":
                    self.assertIsNone(caption._BLOCK_RE.search(body), (p.name, body))
                    self.assertIsNone(caption._CODEISH_RE.search(body), (p.name, body))
                self.assertLessEqual(len(text), 300 if platform == "bsky" else 460, (p.name, len(text)))


class SentenceSplitTests(unittest.TestCase):
    def test_splits_on_terminal_punctuation_only(self) -> None:
        parts = caption._split_sentences("One thing. Then 3.5 more, e.g. this! “Quoted?” Fine.")
        self.assertEqual(parts, ["One thing.", "Then 3.5 more, e.g. this!", "“Quoted?”", "Fine."])


if __name__ == "__main__":
    unittest.main()
