import contextlib
import io
import os
import tomllib
from pathlib import Path

import shbr
from shbr import cli, config


ROOT = Path(__file__).parents[1]


def test_canonical_product_identity_keeps_shbr_compatibility():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert metadata["project"]["name"] == "ai-usage-indicator"
    assert metadata["project"]["description"] == (
        "Local multi-account usage monitor for AI providers and agents"
    )
    assert metadata["project"]["scripts"] == {
        "ai-usage-indicator": "shbr.cli:main",
        "shbr": "shbr.cli:main",
    }
    assert shbr.APP_NAME == "AI Usage Indicator"
    assert shbr.CLI_NAME == "ai-usage-indicator"


def test_help_and_version_use_canonical_name_while_shbr_module_still_imports():
    help_out = io.StringIO()
    with contextlib.redirect_stdout(help_out), contextlib.redirect_stderr(help_out):
        try:
            cli.main(["--help"])
        except SystemExit as exc:
            assert exc.code == 0
    assert help_out.getvalue().startswith("usage: ai-usage-indicator")
    assert "SHawn Brain" not in help_out.getvalue()

    version_out = io.StringIO()
    with contextlib.redirect_stdout(version_out), contextlib.redirect_stderr(version_out):
        try:
            cli.main(["--version"])
        except SystemExit as exc:
            assert exc.code == 0
    assert version_out.getvalue().startswith("AI Usage Indicator ")


def test_macos_app_uses_new_product_and_prefers_new_cli_with_alias_fallback():
    package = (ROOT / "apps" / "menubar-macos" / "Package.swift").read_text(
        encoding="utf-8"
    )
    brain_model = (
        ROOT
        / "apps"
        / "menubar-macos"
        / "Sources"
        / "SHawnBrain"
        / "BrainModel.swift"
    ).read_text(encoding="utf-8")

    assert 'name: "AIUsageIndicator"' in package
    assert 'name: "SHawnBrain"' not in package
    assert "command -v ai-usage-indicator" in brain_model
    assert "command -v shbr" in brain_model
    assert "AI_USAGE_INDICATOR_CONFIG" in (
        ROOT / "src" / "shbr" / "config.py"
    ).read_text(encoding="utf-8")


def test_macos_packaging_and_swiftbar_scripts_follow_renamed_product():
    scripts = ROOT / "apps" / "menubar-macos" / "scripts"
    make_app = (scripts / "make-app.sh").read_text(encoding="utf-8")
    release = (scripts / "release.sh").read_text(encoding="utf-8")
    swiftbar = (ROOT / "contrib" / "swiftbar" / "shbr.10s.sh").read_text(
        encoding="utf-8"
    )

    assert 'APP="AIUsageIndicator"' in make_app
    assert 'DISPLAY_NAME="AI Usage Indicator"' in make_app
    assert 'APP="AIUsageIndicator"' in release
    assert "AI Usage Indicator" in swiftbar.splitlines()[1]
    assert "command -v ai-usage-indicator" in swiftbar
    assert "command -v shbr" in swiftbar


def test_canonical_config_environment_precedes_legacy_environment(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy.toml"
    canonical = tmp_path / "canonical.toml"
    legacy.write_text('state_dir = "/tmp/legacy-state"\n', encoding="utf-8")
    canonical.write_text('state_dir = "/tmp/canonical-state"\n', encoding="utf-8")
    monkeypatch.setenv("SHBR_CONFIG", os.fspath(legacy))
    monkeypatch.setenv("AI_USAGE_INDICATOR_CONFIG", os.fspath(canonical))

    loaded = config.load()

    assert loaded.path == canonical
    assert os.fspath(loaded.state_dir) == "/tmp/canonical-state"


def test_canonical_default_path_precedes_legacy_environment(tmp_path, monkeypatch):
    canonical = tmp_path / ".config" / "ai-usage-indicator" / "config.toml"
    canonical.parent.mkdir(parents=True)
    canonical.write_text('state_dir = "/tmp/canonical-default"\n', encoding="utf-8")
    legacy = tmp_path / "legacy.toml"
    legacy.write_text('state_dir = "/tmp/legacy-env"\n', encoding="utf-8")
    monkeypatch.setenv("HOME", os.fspath(tmp_path))
    monkeypatch.delenv("AI_USAGE_INDICATOR_CONFIG", raising=False)
    monkeypatch.setenv("SHBR_CONFIG", os.fspath(legacy))

    loaded = config.load()

    assert loaded.path == canonical
    assert os.fspath(loaded.state_dir) == "/tmp/canonical-default"
