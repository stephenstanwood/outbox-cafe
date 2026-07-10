from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib.llm import claude_cmd  # noqa: E402


class ClaudeCommandTests(unittest.TestCase):
    def test_one_shot_command_avoids_cache_and_agent_context(self) -> None:
        cmd = claude_cmd("opus")

        self.assertEqual(cmd[:4], [
            "/usr/bin/env",
            "DISABLE_PROMPT_CACHING=1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1",
            "claude",
        ])
        self.assertIn("--system-prompt", cmd)
        self.assertNotIn("--exclude-dynamic-system-prompt-sections", cmd)
        self.assertIn("--safe-mode", cmd)
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        self.assertEqual(cmd[cmd.index("--setting-sources") + 1], "")
        self.assertIn("--strict-mcp-config", cmd)
        self.assertEqual(cmd[-2:], ["--model", "opus"])


if __name__ == "__main__":
    unittest.main()
