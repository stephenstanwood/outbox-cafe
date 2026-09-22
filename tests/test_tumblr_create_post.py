"""tumblr.create_post must survive a write timeout without double-posting.

The 2026-09-22 12:11 drop was lost to a single
`<urlopen error The write operation timed out>` with no retry — the gen landed
on the site and never appeared on Tumblr at all.
"""
from __future__ import annotations

import io
import json
import socket
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib import tumblr  # noqa: E402

CREDS = {
    "TUMBLR_CONSUMER_KEY": "ck", "TUMBLR_CONSUMER_SECRET": "cs",
    "TUMBLR_OAUTH_TOKEN": "ot", "TUMBLR_OAUTH_TOKEN_SECRET": "os",
}
TEXT_FIELDS = {"type": "text", "title": "Nine Racks", "body": "<p>the arm went round</p>"}


def _resp(payload: dict):
    body = io.BytesIO(json.dumps(payload).encode())
    body.__enter__ = lambda self=body: self
    body.__exit__ = lambda *a: False
    return body


class CreatePostTests(unittest.TestCase):
    def setUp(self) -> None:
        mock.patch.dict(tumblr.os.environ, CREDS, clear=False).start()
        self.sleep = mock.patch.object(tumblr.time, "sleep").start()
        self.addCleanup(mock.patch.stopall)

    def test_first_try_success_makes_one_request(self) -> None:
        with mock.patch.object(tumblr.urllib.request, "urlopen",
                               return_value=_resp({"response": {"id": 1}})) as u:
            out = tumblr.create_post("outbox-cafe", TEXT_FIELDS)
        self.assertEqual(out["response"]["id"], 1)
        self.assertEqual(u.call_count, 1)

    def test_timeout_then_found_on_blog_does_not_repost(self) -> None:
        posted = []

        def fake(req, timeout=30):
            if req.get_method() == "POST":
                posted.append(req)
                raise socket.timeout("The write operation timed out")
            # the listing: the post did land, the response was just lost
            return _resp({"response": {"posts": [
                {"id": 77, "timestamp": tumblr.time.time(),
                 "title": "Nine Racks", "body": "<p>the arm went round</p>"},
            ]}})

        with mock.patch.object(tumblr.urllib.request, "urlopen", side_effect=fake):
            out = tumblr.create_post("outbox-cafe", TEXT_FIELDS)
        self.assertEqual(out["response"]["id"], 77)
        self.assertTrue(out["response"]["recovered"])
        self.assertEqual(len(posted), 1, "must not POST a second time")

    def test_timeout_and_nothing_landed_retries_and_succeeds(self) -> None:
        posts = []

        def fake(req, timeout=30):
            if req.get_method() == "POST":
                posts.append(req)
                if len(posts) == 1:
                    raise socket.timeout("The write operation timed out")
                return _resp({"response": {"id": 88}})
            return _resp({"response": {"posts": []}})  # nothing landed

        with mock.patch.object(tumblr.urllib.request, "urlopen", side_effect=fake):
            out = tumblr.create_post("outbox-cafe", TEXT_FIELDS)
        self.assertEqual(out["response"]["id"], 88)
        self.assertEqual(len(posts), 2)

    def test_each_attempt_signs_fresh(self) -> None:
        """OAuth nonces are single-use — a replayed header is a silent 401."""
        headers = []

        def fake(req, timeout=30):
            if req.get_method() == "POST":
                headers.append(req.get_header("Authorization"))
                if len(headers) == 1:
                    raise socket.timeout("timed out")
                return _resp({"response": {"id": 9}})
            return _resp({"response": {"posts": []}})

        with mock.patch.object(tumblr.urllib.request, "urlopen", side_effect=fake):
            tumblr.create_post("outbox-cafe", TEXT_FIELDS)
        self.assertEqual(len(headers), 2)
        self.assertNotEqual(headers[0], headers[1])

    def test_http_error_is_not_retried(self) -> None:
        err = urllib.error.HTTPError("u", 401, "no", {}, io.BytesIO(b"{}"))
        with mock.patch.object(tumblr.urllib.request, "urlopen", side_effect=err) as u:
            with self.assertRaises(urllib.error.HTTPError):
                tumblr.create_post("outbox-cafe", TEXT_FIELDS)
        self.assertEqual(u.call_count, 1, "a 4xx won't clear on retry")

    def test_persistent_timeout_raises_the_real_error(self) -> None:
        def fake(req, timeout=30):
            if req.get_method() == "POST":
                raise socket.timeout("The write operation timed out")
            return _resp({"response": {"posts": []}})

        with mock.patch.object(tumblr.urllib.request, "urlopen", side_effect=fake):
            with self.assertRaises(socket.timeout):
                tumblr.create_post("outbox-cafe", TEXT_FIELDS)

    def test_photo_post_goes_up_as_multipart(self) -> None:
        seen = {}

        def fake(req, timeout=30):
            seen["ctype"] = req.get_header("Content-type")
            return _resp({"response": {"id": 5}})

        with mock.patch.object(tumblr.urllib.request, "urlopen", side_effect=fake):
            tumblr.create_post("outbox-cafe", {"type": "photo", "caption": "<p>hi</p>"},
                               image_bytes=b"\x89PNG", image_name="t.png")
        self.assertTrue(seen["ctype"].startswith("multipart/form-data"))

    def test_older_identical_post_is_not_mistaken_for_this_one(self) -> None:
        """A rerun weeks later must not match its own past post and go silent."""
        posts = []

        def fake(req, timeout=30):
            if req.get_method() == "POST":
                posts.append(req)
                if len(posts) == 1:
                    raise socket.timeout("timed out")
                return _resp({"response": {"id": 99}})
            return _resp({"response": {"posts": [
                {"id": 3, "timestamp": tumblr.time.time() - 86400,
                 "title": "Nine Racks", "body": "<p>the arm went round</p>"},
            ]}})

        with mock.patch.object(tumblr.urllib.request, "urlopen", side_effect=fake):
            out = tumblr.create_post("outbox-cafe", TEXT_FIELDS)
        self.assertEqual(out["response"]["id"], 99)

    def test_fingerprint_survives_tumblrs_html_rewriting(self) -> None:
        sent = tumblr._fingerprint({"title": "A Rack", "body": "<p>nine&nbsp;racks  waiting</p>"})
        stored = tumblr._post_text({"title": "A  Rack",
                                    "body": '<p class="x">nine racks waiting</p><!--x-->'})
        self.assertTrue(stored.startswith(sent))

    def test_listing_failure_falls_through_to_a_retry(self) -> None:
        """Better a rare duplicate (swept at midnight) than a lost drop."""
        posts = []

        def fake(req, timeout=30):
            if req.get_method() == "POST":
                posts.append(req)
                if len(posts) == 1:
                    raise socket.timeout("timed out")
                return _resp({"response": {"id": 11}})
            raise socket.timeout("listing timed out too")

        with mock.patch.object(tumblr.urllib.request, "urlopen", side_effect=fake):
            out = tumblr.create_post("outbox-cafe", TEXT_FIELDS)
        self.assertEqual(out["response"]["id"], 11)


if __name__ == "__main__":
    unittest.main()
