from __future__ import annotations

import socket
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib import bsky  # noqa: E402


RECORD = {"$type": "app.bsky.feed.post", "text": "hi", "createdAt": "2026-09-15T20:00:00.000Z"}


class CreatePostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sleep = mock.patch.object(bsky.time, "sleep").start()
        self.addCleanup(mock.patch.stopall)

    def test_first_try_success_makes_one_request(self) -> None:
        with mock.patch.object(bsky, "request", return_value={"uri": "at://x/1"}) as req:
            self.assertEqual(bsky.create_post("did", "jwt", RECORD)["uri"], "at://x/1")
        self.assertEqual(req.call_count, 1)

    def test_timeout_then_found_in_repo_does_not_repost(self) -> None:
        calls = []

        def fake(path, **kw):
            calls.append(path)
            if path.startswith("/com.atproto.repo.createRecord"):
                raise socket.timeout("The read operation timed out")
            return {"records": [{"uri": "at://x/landed", "cid": "c",
                                 "value": {"createdAt": RECORD["createdAt"]}}]}

        with mock.patch.object(bsky, "request", side_effect=fake):
            resp = bsky.create_post("did", "jwt", RECORD)
        self.assertEqual(resp["uri"], "at://x/landed")
        # one create attempt, one listRecords probe, no second create
        self.assertEqual(sum(p.startswith("/com.atproto.repo.createRecord") for p in calls), 1)

    def test_timeout_then_not_found_retries(self) -> None:
        state = {"n": 0}

        def fake(path, **kw):
            if path.startswith("/com.atproto.repo.createRecord"):
                state["n"] += 1
                if state["n"] == 1:
                    raise socket.timeout("timed out")
                return {"uri": "at://x/2"}
            return {"records": [{"value": {"createdAt": "some-other-time"}}]}

        with mock.patch.object(bsky, "request", side_effect=fake):
            self.assertEqual(bsky.create_post("did", "jwt", RECORD)["uri"], "at://x/2")
        self.assertEqual(state["n"], 2)

    def test_http_error_is_not_retried(self) -> None:
        import urllib.error
        err = urllib.error.HTTPError("u", 400, "bad", {}, None)
        with mock.patch.object(bsky, "request", side_effect=err) as req:
            with self.assertRaises(urllib.error.HTTPError):
                bsky.create_post("did", "jwt", RECORD)
        self.assertEqual(req.call_count, 1)

    def test_gives_up_after_attempts(self) -> None:
        def fake(path, **kw):
            if path.startswith("/com.atproto.repo.createRecord"):
                raise socket.timeout("timed out")
            return {"records": []}

        with mock.patch.object(bsky, "request", side_effect=fake):
            with self.assertRaises(socket.timeout):
                bsky.create_post("did", "jwt", RECORD, attempts=3)


if __name__ == "__main__":
    unittest.main()
