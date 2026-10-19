"""Callsign resolution: local cache first, then QRZ.com.

The manual fallback lives in the prompt layer; this module only reports whether
a lookup succeeded and why it did not, so it stays testable without a console.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from .models import CallsignCache, utcnow
from .qrz import CallsignInfo, QrzClient, QrzError, QrzNotFound
from .validate import base_call, normalize_call

CACHE_FIELDS = (
    "name", "qth", "state", "county", "country", "grid", "dxcc", "cq_zone", "itu_zone",
)


@dataclass
class LookupResult:
    """Outcome of resolving one callsign."""

    call: str
    info: CallsignInfo | None
    source: str  # "cache", "qrz", "disabled", "not_found", or "error"
    detail: str = ""

    @property
    def found(self) -> bool:
        return self.info is not None


class CallsignResolver:
    """Resolves callsigns through the local cache and the QRZ XML API."""

    def __init__(
        self,
        session: Session,
        client: QrzClient | None = None,
        use_cache: bool = True,
    ) -> None:
        self.session = session
        self.client = client
        self.use_cache = use_cache
        # One soft failure (bad credentials, no subscription, no network) is
        # enough to stop retrying QRZ for the rest of the activation.
        self.disabled_reason: str | None = None

    def resolve(self, call: str) -> LookupResult:
        call = normalize_call(call)
        key = base_call(call)

        if self.use_cache:
            cached = self.session.get(CallsignCache, key)
            if cached is not None:
                return LookupResult(call, _cache_to_info(key, cached), "cache")

        if self.client is None or self.disabled_reason:
            detail = self.disabled_reason or "QRZ lookups are disabled"
            return LookupResult(call, None, "disabled", detail)

        try:
            info = self.client.lookup(key)
        except QrzNotFound as exc:
            return LookupResult(call, None, "not_found", str(exc))
        except QrzError as exc:
            # Credentials, subscription and network problems will not fix
            # themselves mid-activation, so fall back to prompting from here on.
            self.disabled_reason = f"QRZ unavailable: {exc}"
            return LookupResult(call, None, "error", str(exc))

        if self.use_cache:
            self._store(key, info)
        return LookupResult(call, info, "qrz")

    def _store(self, key: str, info: CallsignInfo) -> None:
        entry = self.session.get(CallsignCache, key) or CallsignCache(call=key)
        for name in CACHE_FIELDS:
            setattr(entry, name, getattr(info, name))
        entry.source = info.source
        entry.fetched_at = utcnow()
        self.session.add(entry)
        self.session.flush()

    def remember(self, call: str, info: CallsignInfo) -> None:
        """Cache manually entered details so the same station is not re-asked."""
        if self.use_cache:
            self._store(base_call(call), info)


def _cache_to_info(call: str, entry: CallsignCache) -> CallsignInfo:
    return CallsignInfo(
        call=call,
        **{name: getattr(entry, name) for name in CACHE_FIELDS},
        source=entry.source or "cache",
    )
