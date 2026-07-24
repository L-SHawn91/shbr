"""Canonical multi-account usage data model.

The legacy renderer still consumes provider-level ``today/week/month/all`` and
``quotas`` fields.  This module adds the lossless hierarchy used by new clients:

    ProviderUsage -> Account -> MetricSource -> Metric

A Metric always carries its own unit.  Consequently token, request, currency,
and percentage observations can coexist without creating a misleading aggregate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


Number = int | float


def _required(value: str, field_name: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _without_none(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


@dataclass(frozen=True)
class Metric:
    """One observed quantity in one explicit unit and optional time window."""

    id: str
    unit: str
    used: Number | None = None
    limit: Number | None = None
    remaining: Number | None = None
    used_percent: Number | None = None
    remaining_percent: Number | None = None
    window: str | None = None
    resets_at: str | Number | None = None
    label: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required(self.id, "metric id"))
        object.__setattr__(self, "unit", _required(self.unit, "metric unit"))

    def to_dict(self) -> dict[str, Any]:
        return _without_none({
            "id": self.id,
            "label": self.label,
            "unit": self.unit,
            "window": self.window,
            "used": self.used,
            "limit": self.limit,
            "remaining": self.remaining,
            "used_percent": self.used_percent,
            "remaining_percent": self.remaining_percent,
            "resets_at": self.resets_at,
        })


@dataclass(frozen=True)
class MetricSource:
    """A local ledger, official provider API, or isolated browser observation."""

    id: str
    kind: str
    tier: str
    metrics: tuple[Metric, ...] = field(default_factory=tuple)
    status: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required(self.id, "metric source id"))
        object.__setattr__(self, "kind", _required(self.kind, "metric source kind"))
        object.__setattr__(self, "tier", _required(self.tier, "metric source tier"))
        object.__setattr__(self, "metrics", tuple(self.metrics))

    def to_dict(self) -> dict[str, Any]:
        return _without_none({
            "id": self.id,
            "kind": self.kind,
            "tier": self.tier,
            "status": self.status,
            "metrics": [metric.to_dict() for metric in self.metrics],
        })


@dataclass(frozen=True)
class Account:
    """A stable user-declared account identity within one provider."""

    id: str
    label: str
    metric_sources: tuple[MetricSource, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required(self.id, "account id"))
        object.__setattr__(self, "label", _required(self.label, "account label"))
        object.__setattr__(self, "metric_sources", tuple(self.metric_sources))
        ids = [source.id for source in self.metric_sources]
        if len(ids) != len(set(ids)):
            raise ValueError("metric source ids must be unique within an account")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "metric_sources": [source.to_dict() for source in self.metric_sources],
        }


@dataclass(frozen=True)
class ProviderUsage:
    """All observed accounts for one provider; never a cross-unit aggregate."""

    id: str
    label: str
    accounts: tuple[Account, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required(self.id, "provider id"))
        object.__setattr__(self, "label", _required(self.label, "provider label"))
        object.__setattr__(self, "accounts", tuple(self.accounts))
        ids = [account.id for account in self.accounts]
        if len(ids) != len(set(ids)):
            raise ValueError("account ids must be unique within a provider")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "accounts": [account.to_dict() for account in self.accounts],
        }


def local_metric_source(provider: dict[str, Any]) -> MetricSource | None:
    """Convert legacy token windows into one local-ledger metric source."""

    metrics = tuple(
        Metric(id=window, label=window, unit="tokens", used=provider.get(window),
               window=window)
        for window in ("today", "week", "month", "all")
        if provider.get(window) is not None
    )
    if not metrics:
        return None
    return MetricSource(
        id="local-ledger",
        kind="local-ledger",
        tier="local",
        status=provider.get("status"),
        metrics=metrics,
    )


def quota_metric(quota: dict[str, Any], index: int = 0) -> Metric:
    """Losslessly project a legacy quota row into an explicitly unitized Metric."""

    token_type = quota.get("tokenType") or quota.get("token_type")
    unit = quota.get("unit")
    if not unit and token_type:
        unit = str(token_type).strip().lower()
    if not unit and (
        quota.get("remainingPercent") is not None
        or quota.get("usedPercent") is not None
        or quota.get("remaining_percent") is not None
        or quota.get("used_percent") is not None
    ):
        unit = "percent"
    if not unit:
        unit = "unknown"

    metric_id = quota.get("id") or quota.get("window") or f"quota-{index + 1}"
    return Metric(
        id=str(metric_id),
        label=quota.get("label") or quota.get("tokenType"),
        unit=str(unit),
        window=quota.get("window"),
        used=quota.get("used"),
        limit=quota.get("limit"),
        remaining=quota.get("remaining"),
        used_percent=(quota.get("usedPercent")
                      if quota.get("usedPercent") is not None
                      else quota.get("used_percent")),
        remaining_percent=(quota.get("remainingPercent")
                           if quota.get("remainingPercent") is not None
                           else quota.get("remaining_percent")),
        resets_at=(quota.get("resetsAt")
                   if quota.get("resetsAt") is not None
                   else quota.get("resets_at")),
    )


def connector_metric_source(result: dict[str, Any]) -> MetricSource:
    """Convert one connector result into its account-scoped metric source."""

    quotas: Iterable[dict[str, Any]] = result.get("quotas") or ()
    return MetricSource(
        id=str(result.get("source_id") or "provider-api"),
        kind=str(result.get("source_kind") or "provider-api"),
        tier=str(result.get("tier") or "experimental"),
        status=result.get("status"),
        metrics=tuple(quota_metric(quota, i) for i, quota in enumerate(quotas)),
    )
