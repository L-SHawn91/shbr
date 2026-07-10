import unittest

from shbr.connectors import (
    ClaudeConnector,
    CodexConnector,
    CursorConnector,
    OpenrouterConnector,
)


class ConnectorParserTests(unittest.TestCase):
    def test_claude_quota_parser(self):
        rows = ClaudeConnector._quotas({
            "limits": [{
                "kind": "session",
                "percent": 25,
                "resets_at": "2026-07-10T12:00:00Z",
                "is_active": True,
            }]
        })
        self.assertEqual(rows[0]["remainingPercent"], 75.0)
        self.assertEqual(rows[0]["window"], "5h")

    def test_codex_quota_parser(self):
        rows = CodexConnector._quotas({
            "rate_limit": {
                "primary_window": {
                    "used_percent": 40,
                    "limit_window_seconds": 18_000,
                    "reset_at": 123,
                }
            }
        })
        self.assertEqual(rows[0]["remainingPercent"], 60.0)
        self.assertEqual(rows[0]["window"], "5h")

    def test_cursor_quota_parser(self):
        rows = CursorConnector._quotas({
            "individualUsage": {"plan": {"totalPercentUsed": 61.5}},
            "membershipType": "pro",
        })
        self.assertEqual(rows[0]["remainingPercent"], 38.5)
        self.assertEqual(rows[0]["window"], "pro")

    def test_openrouter_balance_parser(self):
        quota = OpenrouterConnector._account_quota(
            {"data": {"total_credits": 20, "total_usage": 5}},
            {"data": {"usage_daily": 1.25, "is_free_tier": False}},
        )
        self.assertEqual(quota["remaining"], 15.0)
        self.assertEqual(quota["remainingPercent"], 75.0)
        self.assertEqual(quota["spentToday"], 1.25)


if __name__ == "__main__":
    unittest.main()
