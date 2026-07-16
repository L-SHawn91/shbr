import os
import re
import unittest
from pathlib import Path
from unittest import mock

from shbr import cli, config, engine
from shbr.connectors import (
    CONNECTOR_REGISTRY,
    OpenrouterConnector,
    build_connectors,
)


class TrustBoundaryTests(unittest.TestCase):
    def test_default_config_builds_no_network_connectors(self):
        cfg = config.Config(config.DEFAULTS)
        with mock.patch("urllib.request.urlopen") as urlopen:
            self.assertEqual(build_connectors(cfg), [])
        urlopen.assert_not_called()

    def test_every_connector_declares_hosts_and_known_tier(self):
        for key, connector in CONNECTOR_REGISTRY.items():
            with self.subTest(connector=key):
                self.assertIn(connector.tier, {"documented", "experimental"})
                self.assertTrue(connector.hosts)
        self.assertEqual(OpenrouterConnector.tier, "documented")
        experimental = {
            key for key, connector in CONNECTOR_REGISTRY.items()
            if connector.tier == "experimental"
        }
        self.assertEqual(
            experimental,
            {"claude", "codex", "gemini", "antigravity", "copilot",
             "cursor_quota", "ollama_cloud"},
        )

    def test_example_config_uses_real_connector_registry_keys(self):
        text = (Path(__file__).parents[1] / "config.example.toml").read_text()
        example_keys = set(re.findall(r"^# \[sources\.([^]]+)\]", text, re.MULTILINE))
        self.assertTrue(set(CONNECTOR_REGISTRY).issubset(example_keys))

    def test_provider_payload_separates_local_and_network_enablement(self):
        cfg = config.Config({
            "sources": {
                "usage": {"enabled": True},
                "codex": {"enabled": True},
            }
        })
        codex = next(row for row in cli._provider_rows(cfg) if row["name"] == "codex")
        self.assertTrue(codex["local_enabled"])
        self.assertTrue(codex["connector_enabled"])
        self.assertEqual(codex["tier"], "experimental")
        self.assertIn("chatgpt.com", codex["hosts"])

    def test_hidden_provider_never_fetches(self):
        class FakeConnector:
            name = "fake"

            def __init__(self):
                self.called = False

            def fetch(self):
                self.called = True
                return {"name": self.name, "quotas": []}

        connector = FakeConnector()
        self.assertEqual(
            engine.apply_connectors([], [connector], hidden={"fake"}),
            [],
        )
        self.assertFalse(connector.called)

    def test_doctor_never_emits_environment_secret(self):
        secret = "TOP_SECRET_SENTINEL_DO_NOT_PRINT"
        cfg = config.Config({
            "state_dir": "/tmp/shbr-doctor-test",
            "sources": {"openrouter": {"enabled": True}},
        })
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": secret}), \
             mock.patch.object(cli.Ctx, "__init__", return_value=None):
            ctx = cli.Ctx.__new__(cli.Ctx)
            ctx.cfg = cfg
            ctx.sources = []
            ctx.connectors = []
            ctx.source_names = lambda: []
            report = cli._doctor_report(ctx)
        rendered = repr(report)
        self.assertNotIn(secret, rendered)
        self.assertNotIn(str(os.path.expanduser("~")), rendered)
        self.assertTrue(report["redaction_safe"])
        connector = next(c for c in report["connectors"] if c["key"] == "openrouter")
        self.assertTrue(connector["runtime_gate_available"])
        self.assertEqual(connector["hosts"], ["openrouter.ai"])


if __name__ == "__main__":
    unittest.main()
