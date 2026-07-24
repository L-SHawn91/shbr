"""Security boundary for optional browser-session usage observations.

This module never opens Chrome databases, reads provider cookies, or speaks CDP.
A site-specific helper running inside an explicitly isolated browser profile may
publish a small, sanitized usage payload to a short-lived authenticated loopback
bridge. The core only performs a read-only GET against that bridge.
"""
from __future__ import annotations

import json
import math
import os
import re
import stat
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MAX_PAYLOAD_BYTES = 64 * 1024
_ALLOWED_TOP_LEVEL = {
    "provider", "account_id", "observed_at", "status", "plan", "quotas"
}
_ALLOWED_QUOTA_FIELDS = {
    "id", "label", "window", "remainingPercent", "usedPercent",
    "remaining_percent", "used_percent", "remaining", "used", "limit",
    "resets_at", "resetsAt", "tokenType", "token_type", "unit", "primary",
    "spentToday",
}
_FORBIDDEN_KEYS = {
    "cookie", "cookies", "password", "authorization", "accesstoken",
    "refreshtoken", "idtoken", "html", "document", "localstorage",
    "sessionstorage", "prompt", "completion",
}
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{16,256}$")
_QUOTA_TEXT_FIELDS = {"id", "label", "window", "tokenType", "token_type", "unit"}
_QUOTA_NUMERIC_FIELDS = {
    "remainingPercent", "usedPercent", "remaining_percent", "used_percent",
    "remaining", "used", "limit", "spentToday",
}
_PERCENT_FIELDS = {
    "remainingPercent", "usedPercent", "remaining_percent", "used_percent",
}
# JSON numbers above 2**53 - 1 are not represented exactly by both Python and
# Swift Double.  Keeping bridge values in this range also makes every numeric
# field safe for the native client's Int conversion and formatter.
_MAX_SAFE_NUMBER = 9_007_199_254_740_991


class UnsafeBrowserBridge(ValueError):
    """Raised when a browser profile or bridge violates the local-only policy."""


def _safe_id(value: str, field_name: str) -> str:
    text = str(value)
    if not _ID_PATTERN.fullmatch(text):
        raise UnsafeBrowserBridge(f"unsafe {field_name}")
    return text


def _safe_token(value: str) -> str:
    token = str(value).strip()
    if not _TOKEN_PATTERN.fullmatch(token):
        raise UnsafeBrowserBridge("invalid bridge capability token")
    return token


def read_bridge_token(path: str | Path) -> str:
    """Read one owner-only local capability token without logging it."""
    token_path = Path(path).expanduser()
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise UnsafeBrowserBridge("platform lacks no-follow file opening")
    flags = os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(os.fspath(token_path), flags)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise UnsafeBrowserBridge("bridge token must be a regular file")
            if stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
                raise UnsafeBrowserBridge(
                    "bridge token file must be owned by the user and mode 0600"
                )
            if info.st_size > 512:
                raise UnsafeBrowserBridge("bridge token file is too large")
            raw = os.read(fd, 513)
        finally:
            os.close(fd)
        return _safe_token(raw.decode("utf-8"))
    except UnsafeBrowserBridge:
        raise
    except (OSError, UnicodeDecodeError) as exc:
        raise UnsafeBrowserBridge("could not read bridge token") from exc


def _scan_forbidden(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).lower())
            if normalized in _FORBIDDEN_KEYS:
                raise UnsafeBrowserBridge(f"sensitive field rejected: {key}")
            _scan_forbidden(nested)
    elif isinstance(value, list):
        for nested in value:
            _scan_forbidden(nested)


@dataclass(frozen=True)
class BrowserProfile:
    """One provider/account-specific browser profile directory."""

    provider: str
    account_id: str
    path: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _safe_id(self.provider, "provider"))
        object.__setattr__(self, "account_id", _safe_id(self.account_id, "account id"))
        object.__setattr__(self, "path", Path(self.path).expanduser())

    def prepare(self) -> Path:
        """Create the profile directory with owner-only permissions."""
        if self.path.is_symlink():
            raise UnsafeBrowserBridge("browser profile must not be a symlink")
        self.path.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.path, 0o700)
        self.validate()
        return self.path

    def validate(self) -> None:
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise UnsafeBrowserBridge("platform lacks no-follow directory opening")
        flags = os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_DIRECTORY", 0)
        try:
            fd = os.open(os.fspath(self.path), flags)
            try:
                info = os.fstat(fd)
            finally:
                os.close(fd)
        except OSError as exc:
            raise UnsafeBrowserBridge(
                "browser profile must be a real directory"
            ) from exc
        if not stat.S_ISDIR(info.st_mode):
            raise UnsafeBrowserBridge("browser profile must be a directory")
        if stat.S_IMODE(info.st_mode) != 0o700:
            raise UnsafeBrowserBridge("browser profile mode must be 0700")
        if info.st_uid != os.getuid():
            raise UnsafeBrowserBridge("browser profile must be owned by the current user")


class _RejectRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        raise UnsafeBrowserBridge("browser bridge redirects are forbidden")


class BrowserBridgeClient:
    """Read sanitized usage metadata from one short-lived loopback bridge."""

    def __init__(
        self,
        host: str,
        port: int,
        provider: str,
        account_id: str,
        token: str,
        timeout: float = 2.0,
    ):
        if host != "127.0.0.1":
            raise UnsafeBrowserBridge("browser bridge must bind to 127.0.0.1")
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise UnsafeBrowserBridge("invalid browser bridge port")
        self.host = host
        self.port = port
        self.provider = _safe_id(provider, "provider")
        self.account_id = _safe_id(account_id, "account id")
        self.token = _safe_token(token)
        self.timeout = timeout
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _RejectRedirect(),
        )

    @staticmethod
    def validate_payload(
        payload: Any,
        provider: str,
        account_id: str,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise UnsafeBrowserBridge("bridge payload must be an object")
        _scan_forbidden(payload)
        if set(payload) - _ALLOWED_TOP_LEVEL:
            raise UnsafeBrowserBridge("unknown top-level bridge fields")
        if payload.get("provider") != provider:
            raise UnsafeBrowserBridge("bridge provider does not match profile")
        if payload.get("account_id") != account_id:
            raise UnsafeBrowserBridge("bridge account does not match profile")
        for field in ("observed_at", "status", "plan"):
            value = payload.get(field)
            if value is not None and (not isinstance(value, str) or len(value) > 256):
                raise UnsafeBrowserBridge(f"invalid bridge field: {field}")
        quotas = payload.get("quotas")
        if not isinstance(quotas, list) or len(quotas) > 256:
            raise UnsafeBrowserBridge("bridge quotas must be a bounded list")
        for quota in quotas:
            if not isinstance(quota, dict):
                raise UnsafeBrowserBridge("each bridge quota must be an object")
            if set(quota) - _ALLOWED_QUOTA_FIELDS:
                raise UnsafeBrowserBridge("unknown bridge quota fields")
            for key, value in quota.items():
                if key in _QUOTA_TEXT_FIELDS:
                    if not isinstance(value, str) or not value or len(value) > 256:
                        raise UnsafeBrowserBridge(f"invalid quota text field: {key}")
                elif key in _QUOTA_NUMERIC_FIELDS:
                    valid = (
                        not isinstance(value, bool)
                        and isinstance(value, (int, float))
                        and math.isfinite(value)
                        and abs(value) <= _MAX_SAFE_NUMBER
                    )
                    if key in _PERCENT_FIELDS:
                        valid = valid and 0 <= value <= 100
                    if not valid:
                        raise UnsafeBrowserBridge(f"invalid quota numeric field: {key}")
                elif key == "primary" and not isinstance(value, bool):
                    raise UnsafeBrowserBridge("invalid quota primary field")
                elif key in {"resets_at", "resetsAt"}:
                    valid_number = (
                        not isinstance(value, bool)
                        and isinstance(value, (int, float))
                        and math.isfinite(value)
                        and abs(value) <= _MAX_SAFE_NUMBER
                    )
                    valid_text = isinstance(value, str) and 0 < len(value) <= 256
                    if not (valid_number or valid_text):
                        raise UnsafeBrowserBridge(f"invalid quota reset field: {key}")
        return dict(payload)

    @property
    def url(self) -> str:
        provider = urllib.parse.quote(self.provider, safe="")
        account = urllib.parse.quote(self.account_id, safe="")
        return f"http://{self.host}:{self.port}/v1/usage/{provider}/{account}"

    def fetch(self) -> dict[str, Any]:
        request = urllib.request.Request(
            self.url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.token}",
                "User-Agent": "ai-usage-indicator/browser-bridge",
            },
            method="GET",
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise UnsafeBrowserBridge("browser bridge returned non-200")
                if response.headers.get_content_type() != "application/json":
                    raise UnsafeBrowserBridge("browser bridge must return JSON")
                raw = response.read(MAX_PAYLOAD_BYTES + 1)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            if isinstance(exc, UnsafeBrowserBridge):
                raise
            raise UnsafeBrowserBridge("browser bridge unavailable") from exc
        if len(raw) > MAX_PAYLOAD_BYTES:
            raise UnsafeBrowserBridge("browser bridge payload too large")
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, ValueError) as exc:
            raise UnsafeBrowserBridge("invalid browser bridge JSON") from exc
        return self.validate_payload(payload, self.provider, self.account_id)
