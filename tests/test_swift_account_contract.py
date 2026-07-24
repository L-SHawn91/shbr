from pathlib import Path


ROOT = Path(__file__).parents[1]
SNAPSHOT = ROOT / "apps/menubar-macos/Sources/SHawnBrain/Snapshot.swift"
CONTENT = ROOT / "apps/menubar-macos/Sources/SHawnBrain/ContentView.swift"


def test_swift_contract_decodes_account_metric_source_hierarchy():
    source = SNAPSHOT.read_text(encoding="utf-8")

    assert "var accounts: [UsageAccount]?" in source
    assert "struct UsageAccount: Decodable, Identifiable" in source
    assert "struct UsageMetricSource: Decodable, Identifiable" in source
    assert "struct UsageMetric: Decodable, Identifiable" in source
    assert 'case metricSources = "metric_sources"' in source
    assert 'case remainingPercent = "remaining_percent"' in source


def test_provider_ui_renders_accounts_without_cross_unit_total():
    source = CONTENT.read_text(encoding="utf-8")

    assert 'sectionTitle("ACCOUNTS")' in source
    assert "private func accountCard(" in source
    assert "private func metricValue(" in source
    assert 'Text("\\(accounts.count) accounts")' in source
    assert "account.metricSources" in source
    assert "crossAccountTotal" not in source
