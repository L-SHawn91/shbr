import unittest
from unittest import mock

from shbr import config, engine
from shbr.connectors import Connector, build_connectors
from shbr.model import Account, Metric, MetricSource, ProviderUsage


class MultiAccountModelTests(unittest.TestCase):
    def test_first_class_model_serializes_without_cross_unit_aggregation(self):
        account = Account(
            id="work",
            label="Work",
            metric_sources=(
                MetricSource(
                    id="local-ledger",
                    kind="local-ledger",
                    tier="local",
                    metrics=(
                        Metric(id="today", unit="tokens", used=1200),
                    ),
                ),
                MetricSource(
                    id="provider-api",
                    kind="provider-api",
                    tier="documented",
                    metrics=(
                        Metric(
                            id="daily-requests",
                            unit="requests",
                            remaining=40,
                            limit=100,
                            remaining_percent=40,
                        ),
                    ),
                ),
            ),
        )
        payload = ProviderUsage(id="gemini", label="Gemini", accounts=(account,)).to_dict()

        self.assertEqual(payload["accounts"][0]["id"], "work")
        sources = payload["accounts"][0]["metric_sources"]
        self.assertEqual([s["metrics"][0]["unit"] for s in sources], ["tokens", "requests"])
        self.assertNotIn("total", payload)
        self.assertNotIn("used", payload)

    def test_build_connectors_expands_explicit_accounts_with_distinct_cache_keys(self):
        class FakeConnector(Connector):
            name = "fake"
            hosts = ("example.invalid",)

            @classmethod
            def available(cls, cfg):
                return bool(cfg.get("enabled"))

        cfg = config.Config({
            "sources": {
                "fake": {
                    "enabled": True,
                    "accounts": [
                        {"id": "personal", "label": "Personal"},
                        {"id": "work", "label": "Work"},
                        {"id": "work", "label": "Duplicate"},
                        {"id": "disabled", "enabled": False},
                    ],
                }
            }
        })
        with mock.patch.dict("shbr.connectors.CONNECTOR_REGISTRY", {"fake": FakeConnector}, clear=True):
            connectors = build_connectors(cfg)

        self.assertEqual([c.account_id for c in connectors], ["personal", "work"])
        self.assertEqual([c.cache_key for c in connectors], ["fake:personal", "fake:work"])

    def test_build_connectors_fails_silently_if_second_construction_changes(self):
        class UnstableConnector(Connector):
            name = "unstable"
            hosts = ("127.0.0.1",)
            construction_count = 0

            def __init__(self, cfg):
                type(self).construction_count += 1
                if type(self).construction_count == 2:
                    raise ValueError("credential changed after availability check")
                super().__init__(cfg)

            @classmethod
            def available(cls, cfg):
                try:
                    cls(cfg)
                except (OSError, TypeError, ValueError):
                    return False
                return True

        cfg = config.Config({
            "sources": {"unstable": {"enabled": True}},
        })
        with mock.patch.dict(
            "shbr.connectors.CONNECTOR_REGISTRY",
            {"unstable": UnstableConnector},
            clear=True,
        ):
            self.assertEqual(build_connectors(cfg), [])

    def test_dynamic_browser_provider_name_honors_hidden_filter_before_fetch(self):
        class DynamicConnector(Connector):
            name = "browser_pilot"
            hosts = ("127.0.0.1",)

            def __init__(self, cfg):
                super().__init__(cfg)
                self.name = cfg["provider"]

            @classmethod
            def available(cls, cfg):
                return bool(cfg.get("enabled"))

        cfg = config.Config({
            "providers": {"hidden": ["claude"]},
            "sources": {
                "browser_pilot": {
                    "enabled": True,
                    "provider": "claude",
                    "account_id": "work",
                }
            },
        })
        with mock.patch.dict(
            "shbr.connectors.CONNECTOR_REGISTRY",
            {"browser_pilot": DynamicConnector},
            clear=True,
        ):
            connectors = build_connectors(cfg)

        self.assertEqual(connectors, [])

    def test_merge_preserves_legacy_quotas_and_adds_account_metric_sources(self):
        meters = [{
            "kind": "providers",
            "source": "usage",
            "providers": {
                "gemini": {
                    "status": "ok",
                    "today": 100,
                    "week": 300,
                    "month": 900,
                    "all": 1200,
                    "quotas": [],
                }
            },
        }]
        results = [
            {
                "name": "gemini",
                "account_id": "personal",
                "account_label": "Personal",
                "source_id": "provider-api",
                "source_kind": "provider-api",
                "tier": "experimental",
                "quotas": [{
                    "id": "daily",
                    "window": "daily",
                    "remaining": 40,
                    "limit": 100,
                    "remainingPercent": 40,
                    "tokenType": "REQUESTS",
                }],
            },
            {
                "name": "gemini",
                "account_id": "work",
                "account_label": "Work",
                "source_id": "provider-api",
                "source_kind": "provider-api",
                "tier": "experimental",
                "quotas": [{
                    "id": "daily",
                    "window": "daily",
                    "remainingPercent": 75,
                }],
            },
        ]

        merged = engine._merge_connector_results(meters, results)
        provider = merged[0]["providers"]["gemini"]

        self.assertEqual(len(provider["quotas"]), 2)  # compatibility path
        accounts = {a["id"]: a for a in provider["accounts"]}
        self.assertEqual(set(accounts), {"unattributed-local", "personal", "work"})
        local_account = accounts["unattributed-local"]
        self.assertEqual(local_account["label"], "Unattributed local usage")
        local = local_account["metric_sources"][0]
        self.assertEqual(local["kind"], "local-ledger")
        self.assertTrue(all(m["unit"] == "tokens" for m in local["metrics"]))
        personal_metric = accounts["personal"]["metric_sources"][0]["metrics"][0]
        self.assertEqual(personal_metric["unit"], "requests")
        work_metric = accounts["work"]["metric_sources"][0]["metrics"][0]
        self.assertEqual(work_metric["unit"], "percent")
        self.assertNotIn("total", provider)

    def test_apply_connectors_exposes_local_account_even_without_network_connectors(self):
        meters = [{
            "kind": "providers",
            "source": "usage",
            "providers": {
                "codex": {
                    "status": "ok",
                    "today": 10,
                    "week": 20,
                    "month": 30,
                    "all": 40,
                    "quotas": [],
                }
            },
        }]

        result = engine.apply_connectors(meters, [])

        account = result[0]["providers"]["codex"]["accounts"][0]
        self.assertEqual(account["id"], "unattributed-local")
        self.assertEqual(account["label"], "Unattributed local usage")
        self.assertEqual(account["metric_sources"][0]["kind"], "local-ledger")


if __name__ == "__main__":
    unittest.main()
