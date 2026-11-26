"""The Render blueprint must describe a practice-only, dry-run-by-default cron job.

Parsed as plain text so no YAML dependency is needed.
"""
import re
import unittest
from pathlib import Path

TEXT = (Path(__file__).resolve().parent.parent / "render.yaml").read_text()


def env_block(key):
    """Return the lines of one envVars entry (from its '- key:' line to the next)."""
    match = re.search(rf"- key: {key}\n((?:\s+(?!- key).*\n?)*)", TEXT)
    assert match, f"{key} missing from render.yaml"
    return match.group(1)


class TestRenderYaml(unittest.TestCase):
    def test_render_yaml_is_practice_cron(self):
        self.assertIn("type: cron", TEXT)
        self.assertIn('schedule: "2 20,21 * * 1-5"', TEXT)
        self.assertIn("startCommand: python -m src.live.run", TEXT)
        self.assertIn("region: frankfurt", TEXT)
        self.assertIn("value: practice", env_block("OANDA_ENV"))
        self.assertIn('value: "no"', env_block("TRADING_ENABLED"))
        for key in ("OANDA_TOKEN", "OANDA_ACCOUNT_ID"):
            block = env_block(key)
            self.assertIn("sync: false", block)
            self.assertNotIn("value:", block)


if __name__ == "__main__":
    unittest.main()
