"""Callsign resolution: cache, QRZ, and graceful degradation."""

from __future__ import annotations

import pytest

from pota_logger.lookup import CallsignResolver
from pota_logger.models import CallsignCache
from pota_logger.qrz import CallsignInfo, QrzAuthError, QrzError, QrzNotFound


class StubQrz:
    """A QRZ client stand-in that returns or raises whatever a test needs."""

    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[str] = []

    def lookup(self, call: str):
        self.calls.append(call)
        if self.error is not None:
            raise self.error
        return self.result


W1AW = CallsignInfo(
    call="W1AW", name="Hiram Maxim", qth="Newington", state="CT",
    country="United States", grid="FN31pr", dxcc="291",
)


def test_qrz_hit_is_returned_and_cached(session):
    client = StubQrz(result=W1AW)
    resolver = CallsignResolver(session, client)

    result = resolver.resolve("w1aw")
    assert result.found
    assert result.source == "qrz"
    assert result.info.name == "Hiram Maxim"

    cached = session.get(CallsignCache, "W1AW")
    assert cached is not None
    assert cached.state == "CT"
    assert cached.grid == "FN31pr"


def test_second_lookup_comes_from_the_cache(session):
    client = StubQrz(result=W1AW)
    resolver = CallsignResolver(session, client)
    resolver.resolve("W1AW")
    again = resolver.resolve("W1AW")
    assert again.source == "cache"
    assert again.info.state == "CT"
    assert client.calls == ["W1AW"]  # QRZ was asked exactly once


def test_portable_calls_share_the_base_call_cache_entry(session):
    client = StubQrz(result=W1AW)
    resolver = CallsignResolver(session, client)
    resolver.resolve("W1AW")
    portable = resolver.resolve("W1AW/P")
    assert portable.source == "cache"
    # The log keeps the call as sent, while the lookup used the base call.
    assert portable.call == "W1AW/P"
    assert client.calls == ["W1AW"]


def test_not_found_is_reported_without_disabling_qrz(session):
    client = StubQrz(error=QrzNotFound("ZZ9ZZZ not found on QRZ"))
    resolver = CallsignResolver(session, client)
    result = resolver.resolve("ZZ9ZZZ")
    assert not result.found
    assert result.source == "not_found"
    assert resolver.disabled_reason is None  # the next callsign is still tried


def test_auth_failure_disables_further_qrz_attempts(session):
    client = StubQrz(error=QrzAuthError("Username/password incorrect"))
    resolver = CallsignResolver(session, client)

    first = resolver.resolve("W1AW")
    assert first.source == "error"
    assert resolver.disabled_reason is not None

    second = resolver.resolve("K2ABC")
    assert second.source == "disabled"
    assert len(client.calls) == 1  # no repeat attempts after a hard failure


def test_network_error_falls_back_the_same_way(session):
    client = StubQrz(error=QrzError("QRZ request failed: timed out"))
    resolver = CallsignResolver(session, client)
    assert resolver.resolve("W1AW").source == "error"
    assert resolver.resolve("W1AW").source == "disabled"


def test_no_client_means_lookups_are_disabled(session):
    resolver = CallsignResolver(session, None)
    result = resolver.resolve("W1AW")
    assert not result.found
    assert result.source == "disabled"


def test_manual_details_are_remembered(session):
    resolver = CallsignResolver(session, None)
    manual = CallsignInfo(call="K2ABC", name="Ann", state="NY", source="manual")
    resolver.remember("K2ABC", manual)

    result = resolver.resolve("K2ABC")
    assert result.source == "cache"
    assert result.info.name == "Ann"
    assert result.info.source == "manual"


def test_cache_can_be_bypassed(session):
    session.add(CallsignCache(call="W1AW", name="Stale"))
    session.commit()
    client = StubQrz(result=W1AW)
    resolver = CallsignResolver(session, client, use_cache=False)
    result = resolver.resolve("W1AW")
    assert result.source == "qrz"
    assert result.info.name == "Hiram Maxim"
