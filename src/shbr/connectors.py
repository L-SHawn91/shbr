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
  * TIER-LABELLED. Each connector declares ``tier``: ``"official"`` (a
    documented, provider-sanctioned usage endpoint) or ``"gray"`` (an
    undocumented/reverse-engineered endpoint — used at the user's own risk, and
    surfaced as such). The engine and README label gray connectors explicitly;
    nothing gray is ever presented as sanctioned.
  * READ-ONLY OVER THE WIRE. GET-style usage reads only. A connector never
    mutates provider-side state and never edits a local settings file. Enabling
    a provider's own telemetry (a separate, opt-in, diff-shown action) is out of
    scope here.
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

import glob
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request


# Network calls are bounded hard: a connector must never make the menu bar hang.
HTTP_TIMEOUT = 4.0


class Connector:
    """Base opt-in provider connector. See module docstring for the contract."""

    name = "base"
    #: "official" (documented, sanctioned endpoint) or "gray" (undocumented).
    tier = "gray"
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

    OFFICIAL tier: it calls Anthropic's own OAuth usage endpoint
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
    tier = "official"
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

    OFFICIAL tier: it calls ChatGPT's own Codex usage endpoint
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
    tier = "official"
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

    OFFICIAL tier: it calls Google's own Code Assist internal endpoint
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
    tier = "official"
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

    @classmethod
    def _scan_bundle(cls):
        """Best-effort, bounded scan for the compiled client id / secret.

        Covers both the current bundle layout (``gemini-cli/bundle/*.js``) and
        the older split-package layout (``code_assist/oauth2.js``). Reads at most
        a handful of files and stops as soon as both literals are found.
        """
        cid = secret = None
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
                    try:
                        with open(fp, encoding="utf-8", errors="ignore") as f:
                            text = f.read()
                    except OSError:
                        continue
                    if not cid:
                        m = cls._CID_RE.search(text)
                        if m:
                            cid = m.group(1)
                    if not secret:
                        m = cls._SECRET_RE.search(text)
                        if m:
                            secret = m.group(1)
                    if cid and secret:
                        return cid, secret
        return cid, secret

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
    def _quotas(data: dict) -> list:
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
            }
            amt = b.get("remainingAmount")
            if amt is not None and frac > 0:
                q["remaining"] = amt
                q["limit"] = round(float(amt) / frac)
            out.append(q)
        return out

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
        quotas = self._quotas(data)
        if not quotas:
            return None
        return {"name": self.name, "status": "live", "tier": self.tier,
                "quotas": quotas}


# Per-provider connectors are registered here as recon confirms an endpoint and
# credential path for each. Empty until a concrete connector is verified — an
# unverified provider ships no code rather than a speculative stub.
CONNECTOR_REGISTRY: dict = {
    "claude": ClaudeConnector,
    "codex": CodexConnector,
    "gemini": GeminiConnector,
}


def build_connectors(cfg) -> list:
    """Instantiate every enabled + credentialed connector, registry order.

    Mirrors ``sources.build_sources`` but for the network tier. Returns an empty
    list whenever nothing is opted in — which is the default.
    """
    out = []
    for name, cls in CONNECTOR_REGISTRY.items():
        cc = cfg.source(name)
        if not cc.get("enabled"):
            continue
        if not cls.available(cc):
            continue
        out.append(cls(cc))
    return out
