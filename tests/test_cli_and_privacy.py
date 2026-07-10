import contextlib
import io
import json
import os
import tempfile
import time
import unittest

from shbr import cli, config
from shbr.sources import ClaudeSessionSource


class CliAndPrivacyTests(unittest.TestCase):
    def test_provider_hide_show_persists_without_rewriting_other_config(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "config.toml")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[sources.usage]\nenabled = true\n")
            cfg = config.load(path)
            cli._persist_hidden(cfg, {"gemini", "codex"})
            loaded = config.load(path)
            self.assertEqual(loaded.hidden_set(), {"gemini", "codex"})
            self.assertTrue(loaded.enabled("usage"))

    def test_doctor_json_contract(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "config.toml")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(
                    f'state_dir = "{td}/state"\n'
                    "[sources.usage]\nenabled = false\n"
                    "[sources.claude_memory]\nenabled = false\n"
                    "[sources.claude_sessions]\nenabled = false\n"
                    "[sources.cursor]\nenabled = false\n"
                    "[sources.system]\nenabled = false\n"
                )
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = cli.main(["--config", path, "doctor", "--json"])
            self.assertEqual(rc, 0)
            payload = json.loads(out.getvalue())
            self.assertTrue(payload["redaction_safe"])
            self.assertEqual(payload["sources"], [])
            self.assertIn("checks", payload)

    def test_help_does_not_advertise_unimplemented_commands(self):
        out = io.StringIO()
        with self.assertRaises(SystemExit) as raised, contextlib.redirect_stdout(out):
            cli.main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        for command in ("registry", "drift", "guard"):
            self.assertNotIn(command, out.getvalue())

    def test_claude_session_output_excludes_prompt_content(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "session.jsonl")
            secret_prompt = "DO-NOT-EXPOSE-PRIVATE-PROMPT"
            now = time.time()
            rows = [
                {
                    "sessionId": "abc",
                    "cwd": "/tmp/example",
                    "timestamp": now,
                    "type": "user",
                    "message": {"content": secret_prompt},
                },
                {
                    "sessionId": "abc",
                    "timestamp": now + 1,
                    "type": "assistant",
                    "message": {
                        "model": "example-model",
                        "content": "private completion",
                        "usage": {"input_tokens": 10, "output_tokens": 5},
                    },
                },
            ]
            with open(path, "w", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(json.dumps(row) + "\n")
            source = ClaudeSessionSource({"glob": os.path.join(td, "*.jsonl")})
            sessions = source.sessions(24)
            rendered = json.dumps(sessions)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0]["tokens"], 15)
            self.assertNotIn(secret_prompt, rendered)
            self.assertNotIn("private completion", rendered)


if __name__ == "__main__":
    unittest.main()
