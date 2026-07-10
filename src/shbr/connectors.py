"""Provider connectors — opt-in, network-calling usage/quota readers.

A ``Connector`` is the deliberate counterpart to a ``Source`` (see
``sources.py``). A Source reads what an agent already wrote to *local disk* and
never touches the network. A Connector goes the other way: it reuses a
credential the agent already stored locally to call that provider's *own* usage
API, and returns the live per-window quota a local file cannot contain.

This module exists because some agents (gemini, cursor, copilot) keep no
readable local token ledger at all — their remaining-quota is a live value the
provider only returns from an authenticated call. Local reads physically cannot
surface it. Connectors close that gap without changing the local-read core.

Design contract — every connector obeys all of it:

  * OFF BY DEFAULT. ``available(cfg)`` returns False unless the user has
    *explicitly* set ``enabled = true`` for that connector in config. Absent
    config == no network, ever. The local-read core stays the zero-config path.
  * DOUBLE GATE. Even when enabled, a connector loads only if the credential it
    needs already exists on disk. It never prompts for, stores, or transmits a
    new secret; it reuses what the provider's own tool put there.
  * TRUST-LABELLED. Each connector declares ``tier``: ``"documented"`` only
    for a publicly documented provider API, or ``"experimental"`` for an
    undocumented/internal/reverse-engineered endpoint. A first-party hostname
    alone is never treated as proof that an API is public or supported.
  * NON-DESTRUCTIVE. Usage reads and OAuth refreshes may use GET or POST, but a
    connector never changes provider account content and never edits a local
    credential/settings file. Refreshed access tokens remain in memory only.
  * FAIL SILENT. Any network/parse/auth failure returns ``None``. A dead or
    slow endpoint must never blank the local screen or raise into the engine.
  * STDLIB ONLY. Network I/O goes through ``urllib`` — no third-party deps, in
    keeping with the core's zero-dependency rule.

A connector returns quota dicts shaped like ``sources`` provider quotas
(``{"id", "window", "remaining_percent"/"used_percent", ...}``) so the engine
can merge them into the existing providers table — augmenting a locally-read
provider with live quota, or adding a provider that has no local ledger at all.
"""
from __future__ import annotations

import base64
import glob
import json
import os
import re
import shutil
import sqlite3
import subprocess
import urllib.error
import urllib.parse
import urllib.request


# Network calls are bounded hard: a connector must never make the menu bar hang.
# 2s caps a hung provider's drag on the panel-open poll (which is otherwise
# build_meter-bound at ~1.6s); a slow/timed-out fetch falls back to the 300s
# cache (see _fetch_connector_results phase 3), so this trades a stale row on a
# slow network for a bounded wall — not a blank row.
HTTP_TIMEOUT = 2.0


class Connector:
    """Base opt-in provider connector. See module docstring for the contract."""

    name = "base"
    #: "documented" (public provider API) or "experimental" (internal/undocumented).
    tier = "experimental"
    #: Remote hosts a connector may contact after explicit opt-in.
    hosts: tuple = ()
    #: Local credential paths this connector reuses; existence is the gate.
    cred_paths: tuple = ()

    def __init__(self, cfg: dict):
        self.cfg = cfg

    @classmethod
    def available(cls, cfg: dict) -> bool:
        """Load only when explicitly enabled AND a local credential exists.

        The default (no ``enabled`` key) is off: connectors never activate
        themselves. This is the single choke point that keeps every network
        feature opt-in.
        """
        if not cfg.get("enabled"):
            return False
        paths = cfg.get("cred_paths") or cls.cred_paths
        return any(os.path.exists(os.path.expanduser(p)) for p in paths)

    def fetch(self):
        """Return a provider dict (usage/quota) or ``None``. Never raises."""
        return None

    # -- shared HTTP helper -------------------------------------------------
    @staticmethod
    def _get_json(url: str, headers: dict | None = None, timeout: float = HTTP_TIMEOUT):
        """GET a URL and parse JSON, or return ``None`` on any failure.

        Deliberately forgiving: connectors are best-effort augmentation, so a
        4xx/5xx/timeout/parse error is a non-event, not an exception.
        """
        req = urllib.request.Request(url, headers=headers or {}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    return None
                raw = resp.read()
        except (urllib.error.URLError, OSError, ValueError):
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None


class ClaudeConnector(Connector):
    """Claude Code remaining-quota reader — the live per-window limit that no
    local file contains.

    EXPERIMENTAL tier: it calls Anthropic's own OAuth usage endpoint
    (``/api/oauth/usage``) with the access token Claude Code already stored on
    this machine — the same credential the CLI itself uses. It reads only; it
    never refreshes, rotates, or writes the token back.

    Credential lookup, in order:
      1. macOS Keychain item ``Claude Code-credentials`` (a JSON blob).
      2. ``~/.claude/.credentials.json`` (same JSON shape) as a fallback.

    The response carries a ``limits`` array of ``{kind, group, percent,
    severity, resets_at, is_active}`` entries — the 5-hour session window, the
    7-day window, and any model-scoped weekly window. Each becomes a quota dict
    with ``remainingPercent = 100 - percent`` so the existing providers render
    path (which keys on ``remainingPercent``) shows the limit unchanged.
    """

    name = "claude"
    tier = "experimental"
    hosts = ("api.anthropic.com",)
    cred_paths = ("~/.claude/.credentials.json",)

    USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
    _KEYCHAIN_SERVICE = "Claude Code-credentials"
    # Anthropic's OAuth usage endpoint requires the Claude Code beta header and
    # a bearer token; mirror the CLI's request exactly or the API 401s.
    _HEADERS = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": "claude-code/2.1.69",
    }
    # kind -> human window label; scoped weekly windows append the model name.
    _WINDOW_LABELS = {"session": "5h", "weekly_all": "7d", "weekly_scoped": "7d"}

    @classmethod
    def _keychain_present(cls) -> bool:
        """True if the Keychain item exists — the gate for macOS, where Claude
        Code stores its credential in the Keychain rather than a dotfile."""
        try:
            return subprocess.run(
                ["security", "find-generic-password",
                 "-s", cls._KEYCHAIN_SERVICE, "-w"],
                capture_output=True, text=True, timeout=5,
            ).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    @classmethod
    def available(cls, cfg: dict) -> bool:
        """Enabled AND a credential exists — in the Keychain OR the dotfile.

        The base gate only checks ``cred_paths`` on disk; on macOS Claude Code
        keeps its token in the Keychain, so the file is usually absent. Accept
        either location so an opted-in user isn't silently gated out.
        """
        if not cfg.get("enabled"):
            return False
        return cls._keychain_present() or super().available(cfg)

    # -- credential ---------------------------------------------------------
    @staticmethod
    def _token_from_blob(raw: str):
        try:
            d = json.loads(raw)
        except (ValueError, TypeError):
            return None
        if not isinstance(d, dict):
            return None
        oa = d.get("claudeAiOauth") or d.get("oauth") or d
        if not isinstance(oa, dict):
            return None
        return oa.get("accessToken") or oa.get("access_token")

    def _token(self):
        # Keychain first (where Claude Code stores it on macOS), then the file.
        try:
            raw = subprocess.run(
                ["security", "find-generic-password",
                 "-s", self._KEYCHAIN_SERVICE, "-w"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            if raw:
                tok = self._token_from_blob(raw)
                if tok:
                    return tok
        except (OSError, subprocess.SubprocessError):
            pass
        for p in self.cfg.get("cred_paths") or self.cred_paths:
            try:
                with open(os.path.expanduser(p), encoding="utf-8") as f:
                    tok = self._token_from_blob(f.read())
                if tok:
                    return tok
            except (OSError, ValueError):
                continue
        return None

    # -- parse --------------------------------------------------------------
    @classmethod
    def _label(cls, lim: dict):
        base = cls._WINDOW_LABELS.get(lim.get("kind")) or lim.get("group") \
            or lim.get("kind") or "?"
        model = ((lim.get("scope") or {}).get("model") or {}).get("display_name")
        return f"{base}:{model}" if model else base

    @classmethod
    def _quotas(cls, data: dict) -> list:
        """Turn the usage payload's ``limits`` array into quota dicts.

        Prefers the structured ``limits`` array; falls back to the top-level
        ``five_hour`` / ``seven_day`` objects if it is absent.
        """
        out = []
        limits = data.get("limits")
        if isinstance(limits, list) and limits:
            for lim in limits:
                if not isinstance(lim, dict):
                    continue
                pct = lim.get("percent")
                if pct is None:
                    continue
                out.append({
                    "id": lim.get("kind"),
                    "window": cls._label(lim),
                    "usedPercent": round(float(pct), 1),
                    "remainingPercent": round(100 - float(pct), 1),
                    "resets_at": lim.get("resets_at"),
                    "severity": lim.get("severity"),
                    "active": bool(lim.get("is_active")),
                })
            return out
        for key, window in (("five_hour", "5h"), ("seven_day", "7d")):
            w = data.get(key)
            if isinstance(w, dict) and w.get("utilization") is not None:
                util = float(w["utilization"])
                out.append({
                    "id": key,
                    "window": window,
                    "usedPercent": round(util, 1),
                    "remainingPercent": round(100 - util, 1),
                    "resets_at": w.get("resets_at"),
                })
        return out

    # -- fetch --------------------------------------------------------------
    def fetch(self):
        tok = self._token()
        if not tok:
            return None
        headers = dict(self._HEADERS, Authorization="Bearer " + tok)
        data = self._get_json(self.USAGE_URL, headers=headers)
        if not isinstance(data, dict):
            return None
        quotas = self._quotas(data)
        if not quotas:
            return None
        return {"name": self.name, "status": "live", "tier": self.tier,
                "quotas": quotas}


class CodexConnector(Connector):
    """Codex (ChatGPT) remaining-quota reader — the live rolling-window limits
    the Codex CLI itself shows, which no local file contains.

    EXPERIMENTAL tier: it calls ChatGPT's undocumented Codex usage endpoint
    (``/backend-api/codex/usage``) with the access token the Codex CLI already
    stored in ``~/.codex/auth.json`` — the same credential the CLI uses. It reads
    only. The one write it ever performs is an OAuth *refresh* (a standard
    ``grant_type=refresh_token`` POST to ``auth.openai.com``) when the stored
    access token has expired; it does not persist the refreshed token back to
    disk — the value lives only in memory for the single usage read.

    The response's ``rate_limit`` holds a ``primary_window`` (5-hour) and
    ``secondary_window`` (weekly) each with ``used_percent`` and
    ``limit_window_seconds``; ``additional_rate_limits[]`` carries the same shape
    per named model tier. Each window becomes a quota dict with
    ``remainingPercent = 100 - used_percent`` and a ``Nh`` window label derived
    from ``limit_window_seconds // 3600`` — so the existing providers render path
    surfaces the Codex limit the same way it does Claude's.
    """

    name = "codex"
    tier = "experimental"
    hosts = ("chatgpt.com", "auth.openai.com")
    cred_paths = ("~/.codex/auth.json",)

    USAGE_URLS = (
        "https://chatgpt.com/backend-api/codex/usage",
        "https://chatgpt.com/backend-api/wham/usage",
        "https://chatgpt.com/api/codex/usage",
    )
    _TOKEN_URL = "https://auth.openai.com/oauth/token"
    # The Codex CLI's own public OAuth client id — required on the refresh call.
    _CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
    _UA = "shbr-connector"

    # -- credential ---------------------------------------------------------
    def _auth(self) -> dict:
        for p in self.cfg.get("cred_paths") or self.cred_paths:
            try:
                with open(os.path.expanduser(p), encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    return d.get("tokens") if isinstance(d.get("tokens"), dict) else {}
            except (OSError, ValueError):
                continue
        return {}

    def _refresh(self, refresh_token: str):
        """Exchange the refresh token for a fresh access token, or ``None``.

        The base ``_get_json`` is GET-only; an OAuth refresh needs a POST, so
        this connector issues its own urllib request. Fail-silent like the rest.
        """
        if not refresh_token:
            return None
        body = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self._CLIENT_ID,
        }).encode()
        req = urllib.request.Request(
            self._TOKEN_URL, data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                if resp.status != 200:
                    return None
                data = json.loads(resp.read())
            return data.get("access_token") if isinstance(data, dict) else None
        except (urllib.error.URLError, OSError, ValueError):
            return None

    # -- usage read ---------------------------------------------------------
    def _usage(self, access: str, account_id: str):
        """GET the usage payload with this access token, trying each endpoint.

        Returns the parsed dict on 200, the sentinel ``401`` if the token is
        rejected (so the caller can refresh and retry), or ``None`` otherwise.
        """
        headers = {
            "Authorization": "Bearer " + access,
            "Accept": "application/json",
            "User-Agent": self._UA,
        }
        if account_id:
            headers["chatgpt-account-id"] = account_id
        saw_401 = False
        for url in self.USAGE_URLS:
            req = urllib.request.Request(url, headers=headers, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                    if resp.status != 200:
                        continue
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    saw_401 = True
            except (urllib.error.URLError, OSError, ValueError):
                continue
        return 401 if saw_401 else None

    # -- parse --------------------------------------------------------------
    @staticmethod
    def _window(win: dict, label_prefix: str, tag: str | None):
        if not isinstance(win, dict) or win.get("used_percent") is None:
            return None
        secs = win.get("limit_window_seconds")
        if secs:
            secs = int(secs)
            # Windows of a day or longer read as days; shorter ones as hours.
            window = f"{secs // 86400}d" if secs >= 86400 else f"{secs // 3600}h"
        else:
            window = label_prefix
        if tag:
            window = f"{window}:{tag}"
        used = float(win["used_percent"])
        return {
            "id": f"{label_prefix}{(':' + tag) if tag else ''}",
            "window": window,
            "usedPercent": round(used, 1),
            "remainingPercent": round(100 - used, 1),
            "resets_at": win.get("reset_at"),
        }

    @classmethod
    def _quotas(cls, data: dict) -> list:
        out = []
        rl = data.get("rate_limit")
        if isinstance(rl, dict):
            for key, pref in (("primary_window", "5h"), ("secondary_window", "7d")):
                q = cls._window(rl.get(key), pref, None)
                if q:
                    out.append(q)
        extra = data.get("additional_rate_limits")
        if isinstance(extra, list):
            for item in extra:
                if not isinstance(item, dict):
                    continue
                tag = item.get("limit_name") or item.get("metered_feature")
                sub = item.get("rate_limit")
                if not isinstance(sub, dict):
                    continue
                for key, pref in (("primary_window", "5h"), ("secondary_window", "7d")):
                    q = cls._window(sub.get(key), pref, tag)
                    if q:
                        out.append(q)
        return out

    # -- fetch --------------------------------------------------------------
    def fetch(self):
        tokens = self._auth()
        access = tokens.get("access_token")
        account_id = tokens.get("account_id") or ""
        if not access:
            return None
        data = self._usage(access, account_id)
        if data == 401:
            access = self._refresh(tokens.get("refresh_token"))
            if not access:
                return None
            data = self._usage(access, account_id)
        if not isinstance(data, dict):
            return None
        quotas = self._quotas(data)
        if not quotas:
            return None
        return {"name": self.name, "status": "live", "tier": self.tier,
                "quotas": quotas}


class GeminiConnector(Connector):
    """Gemini (Google Code Assist) remaining-quota reader — the live per-model
    daily request limits Google returns, which no local file contains.

    EXPERIMENTAL tier: it calls Google's Code Assist internal endpoint
    (``cloudcode-pa.googleapis.com/v1internal``) with the OAuth credential the
    Gemini CLI already stored in ``~/.gemini/oauth_creds.json`` — the same login
    the CLI itself uses. It reads only. The one write it ever performs is an
    OAuth *refresh* (a standard ``grant_type=refresh_token`` POST to
    ``oauth2.googleapis.com``) when the stored access token has expired; the
    refreshed token lives only in memory for the single quota read and is never
    written back to disk.

    Unlike Codex/Claude, the Gemini credential file holds no ``client_id`` /
    ``client_secret`` — the CLI compiles them into its own JS bundle. This
    connector discovers them the same way the CLI's telemetry does, in order:
    ``GEMINI_OAUTH_CLIENT_ID`` / ``GEMINI_OAUTH_CLIENT_SECRET`` env vars, then
    the creds file (in case a variant put them there), then a bounded scan of the
    installed Gemini CLI bundle for the ``OAUTH_CLIENT_ID`` /
    ``OAUTH_CLIENT_SECRET`` literals. No scan, no refresh, no quota — fail silent.

    The quota read is two POSTs: ``:loadCodeAssist`` (resolves the project) then
    ``:retrieveUserQuota`` (returns ``buckets[]``). Each bucket carries a
    ``modelId`` and ``remainingFraction`` in ``[0,1]``; it becomes a quota dict
    with ``remainingPercent = remainingFraction * 100`` so the existing providers
    render path surfaces the per-model limit the same way it does the others.
    """

    name = "gemini"
    tier = "experimental"
    hosts = ("cloudcode-pa.googleapis.com", "oauth2.googleapis.com")
    cred_paths = (
        "~/.gemini/oauth_creds.json",
        "~/.gemini/antigravity-cli/oauth_creds.json",
        "~/.antigravity/oauth_creds.json",
    )

    _TOKEN_URL = "https://oauth2.googleapis.com/token"
    _CODE_ASSIST = "https://cloudcode-pa.googleapis.com/v1internal"
    _UA = "shbr-connector"

    _CID_RE = re.compile(r"""OAUTH_CLIENT_ID\s*[:=]\s*["']([^"']+)["']""")
    _SECRET_RE = re.compile(r"""OAUTH_CLIENT_SECRET\s*[:=]\s*["']([^"']+)["']""")

    # The quota API enumerates one bucket per model — currently 8, most of them
    # preview/opt-in. "Primary" = the models the Gemini CLI actually routes to by
    # default, i.e. its DEFAULT_GEMINI_MODEL / _FLASH_MODEL / _FLASH_LITE_MODEL
    # constants (not _MODEL_AUTO / _EMBEDDING_MODEL). We scan the installed CLI
    # bundle for those literals so the primary set tracks the CLI's own defaults
    # across model generations; the tuple below is the fallback when the scan
    # finds nothing. Each quota is tagged ``primary`` so the frontend can show
    # these expanded and collapse the rest.
    _MODEL_RE = re.compile(
        r"""DEFAULT_GEMINI(?:_FLASH(?:_LITE)?)?_MODEL\s*[:=]\s*["']([^"']+)["']""")
    _PRIMARY_FALLBACK = ("gemini-2.5-pro", "gemini-2.5-flash", "gemini-3.1-flash-lite")
    _primary_cache = None  # per-process memo; the installed bundle can't change mid-run

    # -- credential ---------------------------------------------------------
    def _creds(self) -> dict:
        for p in self.cfg.get("cred_paths") or self.cred_paths:
            try:
                with open(os.path.expanduser(p), encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, dict) and d.get("refresh_token"):
                    return d
            except (OSError, ValueError):
                continue
        return {}

    @classmethod
    def _scan_roots(cls):
        """Directories to search for the Gemini CLI's bundled OAuth literals."""
        roots = []
        exe = shutil.which("gemini")
        if exe:
            d = os.path.dirname(os.path.realpath(exe))
            roots += [d, os.path.dirname(d), os.path.join(d, "..", "libexec")]
        roots += [
            "/opt/homebrew/lib/node_modules",
            "/opt/homebrew/lib",
            "/opt/homebrew/Cellar/gemini-cli",
            "/usr/local/lib/node_modules",
            "/usr/local/lib",
            os.path.expanduser("~/.local/lib"),
            os.path.expanduser("~/.nvm/versions"),
        ]
        return roots

    # The bundle scan is the connector's single biggest cost: the recursive
    # ``**/`` fallbacks walk node_modules / nvm trees (~230k dirs, ~2.5s) on
    # every poll. But the file that holds the literals almost never moves, so we
    # remember its *path* (not the secret — that stays in the bundle) and read
    # just that file next time. A stale path (CLI reinstalled/moved) simply
    # fails the fast read and falls back to a full scan that re-remembers.
    _SCAN_STATE = os.path.expanduser("~/.local/state/shbr/gemini-scan.json")

    @classmethod
    def _scan_state(cls) -> dict:
        try:
            with open(cls._SCAN_STATE, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    @classmethod
    def _remember_scan(cls, key: str, path: str) -> None:
        d = cls._scan_state()
        if d.get(key) == path:
            return
        d[key] = path
        try:
            os.makedirs(os.path.dirname(cls._SCAN_STATE), exist_ok=True)
            with open(cls._SCAN_STATE, "w", encoding="utf-8") as f:
                json.dump(d, f)
        except OSError:
            pass

    @classmethod
    def _scan_bundle(cls):
        """Best-effort, bounded scan for the compiled client id / secret.

        Covers both the current bundle layout (``gemini-cli/bundle/*.js``) and
        the older split-package layout (``code_assist/oauth2.js``). Reads at most
        a handful of files and stops as soon as both literals are found.
        """
        cid = secret = None

        def _read(fp, cid, secret):
            try:
                with open(fp, encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            except OSError:
                return cid, secret
            if not cid:
                m = cls._CID_RE.search(text)
                if m:
                    cid = m.group(1)
            if not secret:
                m = cls._SECRET_RE.search(text)
                if m:
                    secret = m.group(1)
            return cid, secret

        # Fast path: the file that yielded the literals last time rarely moves.
        cached = cls._scan_state().get("bundle_file")
        if cached and os.path.exists(cached):
            cid, secret = _read(cached, cid, secret)
            if cid and secret:
                return cid, secret

        # Slow path: full (recursive) glob; remember the winning file.
        for root in cls._scan_roots():
            for pat in (
                os.path.join(root, "@google/gemini-cli/bundle/*.js"),
                os.path.join(root, "**/@google/gemini-cli/bundle/*.js"),
                os.path.join(
                    root, "**/@google/gemini-cli-core/dist/src/code_assist/oauth2.js"),
            ):
                try:
                    files = sorted(glob.glob(pat, recursive="**" in pat))
                except (OSError, ValueError):
                    continue
                for fp in files[:120]:
                    cid, secret = _read(fp, cid, secret)
                    if cid and secret:
                        cls._remember_scan("bundle_file", fp)
                        return cid, secret
        return cid, secret

    @classmethod
    def _scan_default_models(cls) -> set:
        """Bounded scan of the CLI bundle for its DEFAULT_*_MODEL literals.

        These are the models the Gemini CLI routes to by default (pro / flash /
        flash-lite) — the "primary" set. Memoised per process; returns an empty
        set when nothing is found so the caller can fall back to the allowlist.
        """
        if cls._primary_cache is not None:
            return cls._primary_cache

        def _read(fp):
            try:
                with open(fp, encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            except OSError:
                return set()
            return {m.group(1) for m in cls._MODEL_RE.finditer(text)}

        # Fast path: the previously-winning file, if it still exists.
        cached = cls._scan_state().get("models_file")
        if cached and os.path.exists(cached):
            got = _read(cached)
            if got:
                cls._primary_cache = got
                return got

        # Slow path: full (recursive) glob; remember the winning file.
        found: set = set()
        for root in cls._scan_roots():
            for pat in (
                os.path.join(root, "@google/gemini-cli/bundle/*.js"),
                os.path.join(root, "**/@google/gemini-cli/bundle/*.js"),
                os.path.join(
                    root, "**/@google/gemini-cli-core/dist/src/config/models.js"),
            ):
                try:
                    files = sorted(glob.glob(pat, recursive="**" in pat))
                except (OSError, ValueError):
                    continue
                win = None
                for fp in files[:120]:
                    got = _read(fp)
                    if got:
                        found |= got
                        win = fp
                if found:
                    if win:
                        cls._remember_scan("models_file", win)
                    cls._primary_cache = found
                    return found
        cls._primary_cache = found
        return found

    def _primary_models(self) -> set:
        """The models to show expanded: config override → bundle scan → fallback."""
        cfg_models = self.cfg.get("primary_models")
        if isinstance(cfg_models, (list, tuple)) and cfg_models:
            return {str(m) for m in cfg_models}
        return self._scan_default_models() or set(self._PRIMARY_FALLBACK)

    def _client_creds(self, creds: dict):
        cid = os.environ.get("GEMINI_OAUTH_CLIENT_ID") or creds.get("client_id")
        secret = (os.environ.get("GEMINI_OAUTH_CLIENT_SECRET")
                  or creds.get("client_secret"))
        if cid and secret:
            return cid, secret
        return self._scan_bundle()

    # -- OAuth refresh ------------------------------------------------------
    def _refresh(self, refresh_token: str, cid: str, secret: str):
        if not (refresh_token and cid and secret):
            return None
        body = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": cid,
            "client_secret": secret,
        }).encode()
        req = urllib.request.Request(
            self._TOKEN_URL, data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                if resp.status != 200:
                    return None
                data = json.loads(resp.read())
            return data.get("access_token") if isinstance(data, dict) else None
        except (urllib.error.URLError, OSError, ValueError):
            return None

    # -- Code Assist POST ---------------------------------------------------
    def _post(self, endpoint: str, payload: dict, access: str):
        """POST JSON to a Code Assist endpoint.

        Returns the parsed dict on 200, the sentinel ``401`` when the token is
        rejected (so the caller can refresh and retry), or ``None`` otherwise.
        """
        req = urllib.request.Request(
            self._CODE_ASSIST + endpoint,
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": "Bearer " + access,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": self._UA,
            },
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                if resp.status != 200:
                    return None
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return 401 if e.code in (401, 403) else None
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def _run(self, access: str, project):
        """loadCodeAssist -> retrieveUserQuota. Propagates the 401 sentinel."""
        payload = {"metadata": {"ideType": "IDE_UNSPECIFIED",
                                "platform": "PLATFORM_UNSPECIFIED",
                                "pluginType": "GEMINI"}}
        if project:
            payload["cloudaicompanionProject"] = project
            payload["metadata"]["duetProject"] = project
        load = self._post(":loadCodeAssist", payload, access)
        if not isinstance(load, dict):
            return load  # 401 sentinel or None
        proj = load.get("cloudaicompanionProject") or project
        return self._post(":retrieveUserQuota", {"project": proj}, access)

    # -- parse --------------------------------------------------------------
    @staticmethod
    def _quotas(data: dict, primary: set) -> list:
        buckets = data.get("buckets")
        if not isinstance(buckets, list):
            return []
        out = []
        for b in buckets:
            if not isinstance(b, dict):
                continue
            frac = b.get("remainingFraction")
            if frac is None:
                continue
            frac = float(frac)
            rp = round(frac * 100, 1)
            model = b.get("modelId") or "?"
            q = {
                "id": model,
                "window": model,
                "usedPercent": round(100 - rp, 1),
                "remainingPercent": rp,
                "resets_at": b.get("resetTime"),
                "tokenType": b.get("tokenType"),
                # Default-routed models are shown expanded; the rest (preview /
                # opt-in) are tagged secondary so the frontend can collapse them.
                "primary": model in primary,
            }
            amt = b.get("remainingAmount")
            if amt is not None and frac > 0:
                q["remaining"] = amt
                q["limit"] = round(float(amt) / frac)
            out.append(q)
        # Order newest-and-strongest first: generation descending (3.1 > 3 >
        # 2.5), then reasoning tier descending (pro > flash > flash-lite), then
        # stable before preview. The wire order is alphabetical, which buries
        # the flagship models below older ones.
        out.sort(key=lambda q: GeminiConnector._model_rank(q.get("id") or ""))
        return out

    # Reasoning-strength ordering key. Lower tuple sorts first, so generation
    # and tier are negated to put the newest, highest-reasoning model on top.
    @staticmethod
    def _model_rank(model: str) -> tuple:
        m = model.lower()
        gm = re.search(r"gemini-(\d+(?:\.\d+)?)", m)
        gen = float(gm.group(1)) if gm else 0.0
        if "pro" in m:
            tier = 3
        elif "flash-lite" in m:
            tier = 1
        elif "flash" in m:
            tier = 2
        else:
            tier = 0
        preview = 1 if "preview" in m else 0  # stable before preview
        return (-gen, -tier, preview, model)

    # -- fetch --------------------------------------------------------------
    def fetch(self):
        creds = self._creds()
        access = creds.get("access_token")
        refresh = creds.get("refresh_token")
        if not refresh:
            return None
        project = (os.environ.get("GOOGLE_CLOUD_PROJECT")
                   or os.environ.get("GOOGLE_CLOUD_PROJECT_ID"))
        data = self._run(access, project) if access else 401
        if data == 401:
            cid, secret = self._client_creds(creds)
            access = self._refresh(refresh, cid, secret)
            if not access:
                return None
            data = self._run(access, project)
        if not isinstance(data, dict):
            return None
        quotas = self._quotas(data, self._primary_models())
        if not quotas:
            return None
        return {"name": self.name, "status": "live", "tier": self.tier,
                "quotas": quotas}


class AntigravityConnector(GeminiConnector):
    """Antigravity (Google's agentic IDE) remaining-quota reader.

    Antigravity is a separate product from the plain Gemini CLI: it logs in with
    its *own* Google account and draws on a *separate* free quota pool
    (``auth_method: "consumer"``). It never writes to ``~/.gemini/tmp/*/chats``,
    so shbr's local gemini usage source counts none of its activity, and its
    quota is invisible to the gemini connector. This surfaces it as a distinct
    ``antigravity`` provider row.

    It reuses the entire Gemini Code Assist quota path (``:loadCodeAssist`` →
    ``:retrieveUserQuota`` → per-model buckets) — only the credential differs.
    Antigravity stores its OAuth token *nested* one level deeper than the plain
    CLI: ``{"token": {access_token, refresh_token, expiry, ...},
    "auth_method": ...}`` at ``~/.gemini/antigravity-cli/antigravity-oauth-token``.
    ``_creds()`` unwraps that inner object into the flat shape the reused logic
    expects. Same contract: OFF BY DEFAULT, double-gated on that file, read-only
    over the wire, fail-silent, stdlib-only, refreshed token kept in memory only.

    Note: like the gemini connector it holds no client id/secret, so an expired
    access token triggers a bundle-scan refresh. If Antigravity's OAuth client
    differs from the Gemini CLI's, that refresh may fail — in which case the read
    fails silently (returns ``None``) until the IDE next refreshes the token
    itself. A live (unexpired) access token needs no refresh and reads directly.
    """

    name = "antigravity"
    tier = "experimental"
    cred_paths = ("~/.gemini/antigravity-cli/antigravity-oauth-token",)

    def _creds(self) -> dict:
        for p in self.cfg.get("cred_paths") or self.cred_paths:
            try:
                with open(os.path.expanduser(p), encoding="utf-8") as f:
                    d = json.load(f)
            except (OSError, ValueError):
                continue
            if not isinstance(d, dict):
                continue
            # Nested antigravity shape → unwrap; tolerate a flat shape too.
            tok = d.get("token") if isinstance(d.get("token"), dict) else d
            if isinstance(tok, dict) and tok.get("refresh_token"):
                return tok
        return {}


class CursorConnector(Connector):
    """Cursor subscription-consumption reader — the live "구독 소모량" the Cursor
    dashboard shows, which no local file records.

    EXPERIMENTAL tier: the endpoint (``/api/usage-summary``) is undocumented and was
    confirmed by recon, not published by Cursor. It authenticates with the
    session token the Cursor IDE already stored on this machine — the same
    credential the app itself uses — and only ever issues a read GET. The token
    is read from Cursor's local SQLite store into memory for the single request
    and is never logged, written, or transmitted anywhere but back to Cursor.

    Credential: ``cursorAuth/accessToken`` in the ``ItemTable`` of Cursor's
    ``state.vscdb`` (opened strictly read-only). The token is a JWT whose ``sub``
    (``provider|user_...``) yields the userId. Cursor's API takes cookie auth,
    not bearer — the header is ``Cookie: WorkosCursorSessionToken=<uid>::<tok>``
    (``::`` percent-encoded). Bearer auth 401s.

    The response's ``individualUsage.plan.totalPercentUsed`` is Cursor's own
    authoritative headline ("You've used N% of your included total usage"); it
    becomes one quota dict with ``remainingPercent = 100 - totalPercentUsed`` and
    a billing-cycle reset, so the existing providers render path surfaces it the
    same way it does Claude/Codex/Gemini. Fail-silent throughout: a missing
    token, a rejected cookie, or a malformed payload returns None.
    """

    name = "cursor"
    tier = "experimental"
    hosts = ("cursor.com",)
    cred_paths = (
        "~/Library/Application Support/Cursor/User/globalStorage/state.vscdb",
    )

    USAGE_URL = "https://cursor.com/api/usage-summary"
    _UA = "shbr-connector"

    # -- credential ---------------------------------------------------------
    @staticmethod
    def _jwt_sub(token: str):
        """Decode a JWT's ``sub`` claim without any signature check — read-only
        introspection of a token this machine already holds. Returns None on any
        malformed segment."""
        try:
            payload = token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload))
        except (IndexError, ValueError, TypeError):
            return None
        sub = claims.get("sub") if isinstance(claims, dict) else None
        return sub if isinstance(sub, str) and sub else None

    def _token_and_uid(self):
        """Read ``cursorAuth/accessToken`` from Cursor's SQLite store (read-only)
        and derive the userId from its JWT ``sub``. Token stays in memory only —
        never logged or written. Returns ``(token, uid)`` or None."""
        for p in self.cfg.get("cred_paths") or self.cred_paths:
            db = os.path.expanduser(p)
            if not os.path.exists(db):
                continue
            try:
                con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
                try:
                    row = con.execute(
                        "SELECT value FROM ItemTable WHERE key=?",
                        ("cursorAuth/accessToken",),
                    ).fetchone()
                finally:
                    con.close()
            except sqlite3.Error:
                continue
            token = row[0] if row else None
            if not token or not isinstance(token, str):
                continue
            sub = self._jwt_sub(token)
            if not sub:
                continue
            uid = sub.split("|")[-1]
            if uid:
                return token, uid
        return None

    # -- parse --------------------------------------------------------------
    @staticmethod
    def _quotas(data: dict) -> list:
        iu = data.get("individualUsage")
        plan = iu.get("plan") if isinstance(iu, dict) else None
        if not isinstance(plan, dict):
            return []
        pct = plan.get("totalPercentUsed")
        if pct is None:
            return []
        try:
            pct = float(pct)
        except (ValueError, TypeError):
            return []
        window = data.get("membershipType") or "plan"
        return [{
            "id": "subscription",
            "window": window,
            "usedPercent": round(pct, 1),
            "remainingPercent": round(100 - pct, 1),
            "resets_at": data.get("billingCycleEnd"),
            "primary": True,
        }]

    # -- fetch --------------------------------------------------------------
    def fetch(self):
        creds = self._token_and_uid()
        if not creds:
            return None
        token, uid = creds
        # Cursor authenticates with a session cookie, not a bearer token; the
        # value is ``<uid>::<token>`` with ``::`` percent-encoded.
        cookie = "WorkosCursorSessionToken=" + urllib.parse.quote(
            f"{uid}::{token}", safe="=")
        headers = {"Cookie": cookie, "Accept": "application/json",
                   "User-Agent": self._UA}
        data = self._get_json(self.USAGE_URL, headers=headers)
        if not isinstance(data, dict):
            return None
        quotas = self._quotas(data)
        if not quotas:
            return None
        return {"name": self.name, "status": "live", "tier": self.tier,
                "quotas": quotas}


class CopilotConnector(Connector):
    """GitHub Copilot premium-request quota reader — the "구독 소모량" for the
    monthly premium-interaction allowance no local file records.

    EXPERIMENTAL tier: the endpoint (``copilot_internal/user``) is undocumented and was
    confirmed by recon, not published by GitHub. It reuses the GitHub credential
    the ``gh`` CLI already stored on this machine — the SAME login the user set up
    for GitHub — by shelling out to ``gh api`` (a read GET). Going through ``gh``
    means the token stays inside ``gh``'s own keyring: this connector never reads,
    logs, writes, or transmits it. There is no Copilot-editor dotfile on this
    machine (``~/.config/github-copilot/`` is absent), so the gh login is the only
    credential and the natural double-gate.

    The response carries a ``quota_snapshots`` map (``chat`` / ``completions`` /
    ``premium_interactions``); each snapshot has ``percent_remaining`` (0–100),
    ``unlimited``, ``has_quota`` and an ``entitlement``. The unlimited pools always
    read 100% and are skipped — the metered ``premium_interactions`` allowance is
    the meaningful meter and becomes the primary quota, with
    ``remainingPercent = percent_remaining`` and the plan-wide ``quota_reset_date``
    as its reset. Fail-silent throughout: no gh, an unauthenticated gh, a non-zero
    exit, or a malformed payload returns None.
    """

    name = "copilot"
    tier = "experimental"
    hosts = ("api.github.com",)
    # gh keeps its token in the OS keyring, not this file; presence still signals
    # a configured gh, and ``available`` also accepts an authenticated gh / env
    # token, mirroring ClaudeConnector's Keychain override.
    cred_paths = ("~/.config/gh/hosts.yml",)

    _API_PATH = "copilot_internal/user"
    # premium_interactions is the only genuinely metered pool; chat/completions
    # come back unlimited on these plans and are dropped rather than shown as a
    # permanent 100%.
    _PRIMARY = "premium_interactions"

    @staticmethod
    def _gh_bin():
        """Locate the ``gh`` binary via PATH, then common Homebrew locations —
        the app may run shbr with a trimmed PATH that omits /opt/homebrew/bin."""
        found = shutil.which("gh")
        if found:
            return found
        for p in ("/opt/homebrew/bin/gh", "/usr/local/bin/gh"):
            if os.path.exists(p):
                return p
        return None

    @classmethod
    def available(cls, cfg: dict) -> bool:
        """Enabled AND the ``gh`` binary is present.

        We deliberately do NOT run ``gh auth status`` here: this gate runs on
        every poll (a fresh subprocess rebuilds the connector list each time), and
        the auth probe costs ~1s of wall time on the panel-open path. Authentication
        is verified implicitly by ``_api_json`` — ``gh api`` fails cleanly when not
        logged in, the connector fetches nothing, and no row appears. So the
        display behaviour is identical to an explicit auth gate, minus the per-poll
        cost; and on warm polls the ConnectorCache serves the last result without
        even reaching ``gh api``."""
        if not cfg.get("enabled"):
            return False
        return cls._gh_bin() is not None

    # -- fetch --------------------------------------------------------------
    def _api_json(self):
        """``gh api copilot_internal/user`` → parsed dict, or None. gh owns the
        token; we only ever see the JSON body it returns."""
        gh = self._gh_bin()
        if not gh:
            return None
        try:
            proc = subprocess.run(
                [gh, "api", self._API_PATH],
                capture_output=True, text=True, timeout=HTTP_TIMEOUT + 4,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0 or not proc.stdout:
            return None
        try:
            data = json.loads(proc.stdout)
        except (ValueError, TypeError):
            return None
        return data if isinstance(data, dict) else None

    # -- parse --------------------------------------------------------------
    @classmethod
    def _quotas(cls, data: dict) -> list:
        snaps = data.get("quota_snapshots")
        if not isinstance(snaps, dict):
            return []
        resets = data.get("quota_reset_date") or data.get("quota_reset_date_utc")
        out = []
        for key, snap in snaps.items():
            if not isinstance(snap, dict):
                continue
            # Skip pools with no meter or an unlimited allowance — they always
            # read 100% and carry no usable "구독 소모량".
            if snap.get("unlimited") or not snap.get("has_quota"):
                continue
            pct = snap.get("percent_remaining")
            if pct is None:
                continue
            try:
                pct = float(pct)
            except (ValueError, TypeError):
                continue
            qid = snap.get("quota_id") or key
            out.append({
                "id": qid,
                "window": "premium" if key == cls._PRIMARY else key,
                "usedPercent": round(100 - pct, 1),
                "remainingPercent": round(pct, 1),
                "resets_at": resets,
                "primary": key == cls._PRIMARY,
            })
        # Guarantee a primary if premium_interactions was absent but others exist.
        if out and not any(q["primary"] for q in out):
            out[0]["primary"] = True
        return out

    def fetch(self):
        data = self._api_json()
        if not isinstance(data, dict):
            return None
        quotas = self._quotas(data)
        if not quotas:
            return None
        return {"name": self.name, "status": "live", "tier": self.tier,
                "quotas": quotas}


class OpenrouterConnector(Connector):
    """OpenRouter credit/spend reader — the live usage the OpenRouter dashboard
    shows for the API key opencode (and any other agent) routes through.

    DOCUMENTED tier: it calls OpenRouter's documented key and credits endpoints
    (``GET /api/v1/key``) with the ``OPENROUTER_API_KEY`` the shell already
    exports on this machine — the SAME key opencode uses to route requests. It
    reads only; it never rotates, writes, or persists the key. The key lives in
    the environment (opencode authenticates its gateways via env vars, not a
    readable dotfile), so this connector's credential and its double-gate are
    both that env var — there is no local file to check.

    Why this exists: opencode's recent activity is spent server-side on its
    gateways' consoles, not in the local token ledger (which the on-by-default
    ``opencode`` source already reads). OpenRouter is the one gateway with a
    public usage API, so this surfaces its live credit/spend that a local read
    physically cannot. (OpenCode Zen, opencode's other gateway, has no public
    balance endpoint yet — GitHub issue #10448 — so it stays web-console-only
    and ships no connector.)

    Two endpoints, two different questions:
      • ``GET /api/v1/credits`` → the ACCOUNT balance: ``total_credits`` (all $
        ever loaded) and ``total_usage`` (all $ ever spent). Their difference is
        the true remaining balance opencode draws down — this is the "여분양"
        the menu bar must show, and it is the PRIMARY quota here.
      • ``GET /api/v1/key`` → this KEY's own scope: ``usage`` (cumulative $ on
        the key), ``limit`` / ``limit_remaining`` (a per-key cap, null when
        uncapped), ``usage_daily`` (today's $), ``is_free_tier``. A per-key cap
        is a narrower ceiling than the account balance, so it is folded in only
        for today's spend and the free-tier flag; the account balance wins for
        remaining. (Reading ``/key`` alone — which an earlier version did —
        surfaced the tiny per-key cap instead of the real balance, so the
        remaining amount looked exhausted even when the account had funds.)

    The account quota carries ``remaining = total_credits - total_usage`` (raw
    $, clamped at 0 for display — a negative means over-spent, i.e. $0 left),
    ``limit = total_credits``, ``spent = total_usage`` and, when the account
    ever loaded credit, ``remainingPercent``. Fail-silent throughout: the
    account call is required (no balance → None); the per-key call is
    best-effort enrichment (its failure just omits today's spend).
    """

    name = "openrouter"
    tier = "documented"
    hosts = ("openrouter.ai",)
    _ENV_KEY = "OPENROUTER_API_KEY"
    cred_paths = ()  # credential is an env var, not a file — see available().

    CREDITS_URL = "https://openrouter.ai/api/v1/credits"
    USAGE_URL = "https://openrouter.ai/api/v1/key"
    _UA = "shbr-connector"

    @classmethod
    def available(cls, cfg: dict) -> bool:
        """Enabled AND the API key is exported in the environment.

        opencode authenticates its gateways via env vars rather than a readable
        file, so the base ``cred_paths`` gate never fires. Gate on the env var
        instead — mirroring ClaudeConnector's Keychain override."""
        if not cfg.get("enabled"):
            return False
        return bool(os.environ.get(cls._ENV_KEY))

    @staticmethod
    def _num(v):
        """Coerce a JSON number to float, or None if absent/non-numeric."""
        if v is None:
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None

    @classmethod
    def _account_quota(cls, credits: dict, key_data) -> dict:
        """Build the primary balance quota from ``/credits`` (account totals),
        enriched with today's spend + free-tier flag from ``/key`` when present.

        Returns None if the account payload lacks the totals we need."""
        d = credits.get("data") if isinstance(credits, dict) else None
        if not isinstance(d, dict):
            return None
        total = cls._num(d.get("total_credits"))
        used = cls._num(d.get("total_usage"))
        if total is None and used is None:
            return None
        # ``/key`` enrichment (best-effort — may be absent on a failed call).
        kd = key_data.get("data") if isinstance(key_data, dict) else None
        daily = cls._num(kd.get("usage_daily")) if isinstance(kd, dict) else None
        free = bool(kd.get("is_free_tier")) if isinstance(kd, dict) else False
        q = {
            "id": "balance",
            "window": "free" if free else "credit",
            "resets_at": None,  # OpenRouter credit has no rolling reset.
            "primary": True,
        }
        if used is not None:
            q["spent"] = round(used, 4)
        if daily is not None:
            q["spentToday"] = round(daily, 4)
        if total is not None:
            q["limit"] = round(total, 4)
            raw_remaining = total - (used or 0.0)
            # Negative means the account is over its loaded credit → $0 left.
            q["remaining"] = round(max(0.0, raw_remaining), 4)
            if total > 0:
                rp = max(0.0, min(100.0, raw_remaining / total * 100))
                q["remainingPercent"] = round(rp, 1)
                q["usedPercent"] = round(100 - rp, 1)
        return q

    def fetch(self):
        key = os.environ.get(self._ENV_KEY)
        if not key:
            return None
        headers = {"Authorization": "Bearer " + key, "Accept": "application/json",
                   "User-Agent": self._UA}
        # Account balance is required; per-key data is best-effort enrichment.
        credits = self._get_json(self.CREDITS_URL, headers=headers)
        key_data = self._get_json(self.USAGE_URL, headers=headers)
        q = self._account_quota(credits, key_data)
        if q is None:
            return None
        return {"name": self.name, "status": "live", "tier": self.tier,
                "quotas": [q]}


# Per-provider connectors are registered here as recon confirms an endpoint and
# credential path for each. Empty until a concrete connector is verified — an
# unverified provider ships no code rather than a speculative stub.
CONNECTOR_REGISTRY: dict = {
    "claude": ClaudeConnector,
    "codex": CodexConnector,
    "gemini": GeminiConnector,
    "antigravity": AntigravityConnector,
    # OFF by default (absent from DEFAULTS.sources); opt-in via
    # ``[sources.copilot] enabled = true``. Credential is the existing ``gh`` CLI
    # login — no Copilot-editor dotfile exists on this machine.
    "copilot": CopilotConnector,
    # Registered under a *distinct* key from the on-by-default ``cursor`` local
    # composer-session source, so this network connector stays OFF by default
    # (opt-in via ``[sources.cursor_quota] enabled = true``). Its provider row is
    # still ``cursor`` (the ``name`` attr), so the quota merges into that row.
    "cursor_quota": CursorConnector,
    # OFF by default; opt-in via ``[sources.openrouter] enabled = true``.
    # DOCUMENTED tier — OpenRouter's ``GET /api/v1/key`` read using the
    # ``OPENROUTER_API_KEY`` env var opencode already routes through, surfacing
    # the gateway credit/spend a local disk read cannot see. Distinct key from
    # the on-by-default local ``opencode`` token-ledger source.
    "openrouter": OpenrouterConnector,
}


def build_connectors(cfg) -> list:
    """Instantiate every enabled + credentialed connector, registry order.

    Mirrors ``sources.build_sources`` but for the network tier. Returns an empty
    list whenever nothing is opted in — which is the default.
    """
    out = []
    hidden = cfg.hidden_set()
    for name, cls in CONNECTOR_REGISTRY.items():
        cc = cfg.source(name)
        if not cc.get("enabled"):
            continue
        # The user hid this provider from the display — don't even build the
        # connector, so its network fetch never runs. Keyed by display-name
        # (``cls.name``), which can differ from the registry key
        # (e.g. ``cursor_quota`` → row ``cursor``).
        if getattr(cls, "name", name) in hidden:
            continue
        if not cls.available(cc):
            continue
        out.append(cls(cc))
    return out
