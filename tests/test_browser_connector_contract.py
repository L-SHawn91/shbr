import json
import os
import stat
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from shbr import engine
from shbr.browser import (
    MAX_PAYLOAD_BYTES,
    BrowserBridgeClient,
    BrowserProfile,
    UnsafeBrowserBridge,
    read_bridge_token,
)
from shbr.connectors import BrowserSessionConnector


class BrowserConnectorContractTests(unittest.TestCase):
    def _token_file(self, root: str, token: str = "local-bridge-capability-0123456789") -> Path:
        path = Path(root) / "bridge-token"
        path.write_text(token, encoding="utf-8")
        os.chmod(path, 0o600)
        return path

    def test_profile_prepare_is_account_scoped_owned_and_mode_0700(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "claude-work"
            profile = BrowserProfile("claude", "work", path)

            profile.prepare()

            self.assertTrue(path.is_dir())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
            self.assertEqual(path.stat().st_uid, os.getuid())
            profile.validate()

    def test_profile_rejects_group_world_access_or_foreign_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shared"
            path.mkdir(mode=0o755)
            os.chmod(path, 0o755)
            with self.assertRaises(UnsafeBrowserBridge):
                BrowserProfile("claude", "work", path).validate()

            os.chmod(path, 0o700)
            with mock.patch("shbr.browser.os.getuid", return_value=os.getuid() + 1):
                with self.assertRaises(UnsafeBrowserBridge):
                    BrowserProfile("claude", "work", path).validate()

    def test_bridge_capability_token_requires_owner_only_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            token_file = self._token_file(tmp)
            self.assertEqual(
                read_bridge_token(token_file),
                "local-bridge-capability-0123456789",
            )
            symlink = Path(tmp) / "bridge-token-link"
            symlink.symlink_to(token_file)
            with self.assertRaises(UnsafeBrowserBridge):
                read_bridge_token(symlink)
            os.chmod(token_file, 0o644)
            with self.assertRaises(UnsafeBrowserBridge):
                read_bridge_token(token_file)

    def test_bridge_rejects_non_loopback_sensitive_fields_and_bad_values(self):
        with self.assertRaises(UnsafeBrowserBridge):
            BrowserBridgeClient(
                "0.0.0.0", 8765, "claude", "work", "local-token-0123456789"
            )
        with self.assertRaises(UnsafeBrowserBridge):
            BrowserBridgeClient.validate_payload({
                "provider": "claude",
                "account_id": "work",
                "quotas": [],
                "access_token": "must-never-cross-the-bridge",
            }, "claude", "work")
        with self.assertRaises(UnsafeBrowserBridge):
            BrowserBridgeClient.validate_payload({
                "provider": "claude",
                "account_id": "work",
                "quotas": [{"id": "weekly", "remainingPercent": "many"}],
            }, "claude", "work")
        with self.assertRaises(UnsafeBrowserBridge):
            BrowserBridgeClient.validate_payload({
                "provider": "claude",
                "account_id": "work",
                "quotas": [{"id": "x" * 300, "remainingPercent": 10}],
            }, "claude", "work")

    def test_bridge_rejects_out_of_range_or_swift_unsafe_numeric_values(self):
        invalid_rows = (
            {"id": "negative-percent", "remainingPercent": -0.1},
            {"id": "over-percent", "usedPercent": 100.1},
            {"id": "too-large", "remaining": 9_007_199_254_740_992},
            {"id": "bad-reset", "resetsAt": 9_007_199_254_740_992},
        )
        for quota in invalid_rows:
            with self.subTest(quota=quota):
                with self.assertRaises(UnsafeBrowserBridge):
                    BrowserBridgeClient.validate_payload({
                        "provider": "claude",
                        "account_id": "work",
                        "quotas": [quota],
                    }, "claude", "work")

    def test_redirect_is_rejected_without_following_second_location(self):
        seen = []

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.path)
                if self.path == "/v1/usage/claude/work":
                    self.send_response(302)
                    self.send_header("Location", "/must-not-follow")
                    self.end_headers()
                else:
                    body = b'{"provider":"claude","account_id":"work","quotas":[]}'
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            def log_message(self, format, *args):
                del format, args

        server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = BrowserBridgeClient(
                "127.0.0.1",
                server.server_port,
                "claude",
                "work",
                "local-token-0123456789",
            )
            with self.assertRaises(UnsafeBrowserBridge):
                client.fetch()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(seen, ["/v1/usage/claude/work"])

    def test_bridge_rejects_wrong_content_type_and_oversized_body(self):
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append(self.path)
                if len(calls) == 1:
                    body = b"{}"
                    content_type = "text/plain"
                else:
                    body = b" " * (MAX_PAYLOAD_BYTES + 1)
                    content_type = "application/json"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                del format, args

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = BrowserBridgeClient(
                "127.0.0.1",
                server.server_port,
                "claude",
                "work",
                "local-token-0123456789",
            )
            with self.assertRaises(UnsafeBrowserBridge):
                client.fetch()
            with self.assertRaises(UnsafeBrowserBridge):
                client.fetch()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(len(calls), 2)

    def test_bounded_authenticated_loopback_pilot_fetches_sanitized_usage(self):
        seen = {}
        expected_auth = "Bearer local-bridge-capability-0123456789"

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen["calls"] = seen.get("calls", 0) + 1
                seen["path"] = self.path
                seen["authorization"] = self.headers.get("Authorization")
                body = json.dumps({
                    "provider": "claude",
                    "account_id": "work",
                    "observed_at": "2026-07-24T00:00:00Z",
                    "quotas": [{
                        "id": "weekly",
                        "window": "7d",
                        "remainingPercent": 63,
                    }],
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                del format, args

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                profile_path = Path(tmp) / "claude-work"
                BrowserProfile("claude", "work", profile_path).prepare()
                token_file = self._token_file(tmp)
                connector = BrowserSessionConnector({
                    "enabled": True,
                    "provider": "claude",
                    "account_id": "work",
                    "account_label": "Work",
                    "profile_dir": str(profile_path),
                    "bridge_host": "127.0.0.1",
                    "bridge_port": server.server_port,
                    "bridge_token_file": str(token_file),
                })

                result = connector.fetch()
                meters = engine.apply_connectors([], [connector])
                calls_before_mode_change = seen["calls"]
                os.chmod(profile_path, 0o755)
                self.assertIsNone(connector.fetch())
                self.assertEqual(seen["calls"], calls_before_mode_change)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(seen["path"], "/v1/usage/claude/work")
        self.assertEqual(seen["authorization"], expected_auth)
        self.assertEqual(result["name"], "claude")
        self.assertEqual(result["source_kind"], "browser-session")
        provider = meters[0]["providers"]["claude"]
        account = provider["accounts"][0]
        self.assertEqual(account["id"], "work")
        self.assertEqual(account["metric_sources"][0]["kind"], "browser-session")
        self.assertEqual(
            account["metric_sources"][0]["metrics"][0]["remaining_percent"],
            63,
        )


if __name__ == "__main__":
    unittest.main()
