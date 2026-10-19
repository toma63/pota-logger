"""QRZ XML API client, driven through a stub HTTP session."""

from __future__ import annotations

import pytest
import requests

from pota_logger.qrz import (
    QrzAuthError,
    QrzClient,
    QrzError,
    QrzNotFound,
    QrzSubscriptionError,
)

NS = 'xmlns="http://xmldata.qrz.com/xml/current/"'

LOGIN_OK = f"""<?xml version="1.0" encoding="utf-8" ?>
<QRZDatabase version="1.34" {NS}>
  <Session><Key>abc123sessionkey</Key><Count>42</Count>
  <SubExp>Wed Dec 31 00:00:00 2027</SubExp><GMTime>Sun Oct 18 17:00:00 2026</GMTime></Session>
</QRZDatabase>"""

LOGIN_BAD = f"""<?xml version="1.0" encoding="utf-8" ?>
<QRZDatabase version="1.34" {NS}>
  <Session><Error>Username/password incorrect</Error></Session>
</QRZDatabase>"""

CALLSIGN_OK = f"""<?xml version="1.0" encoding="utf-8" ?>
<QRZDatabase version="1.34" {NS}>
  <Callsign>
    <call>W1AW</call><aliases>W1INF,W1AW/1</aliases><dxcc>291</dxcc>
    <fname>Hiram Percy</fname><name>Maxim</name>
    <addr1>225 Main St</addr1><addr2>Newington</addr2><state>CT</state>
    <zip>06111</zip><country>United States</country><county>Hartford</county>
    <grid>FN31pr</grid><cqzone>5</cqzone><ituzone>8</ituzone><class>C</class>
    <email>w1aw@arrl.org</email>
  </Callsign>
  <Session><Key>abc123sessionkey</Key><Count>43</Count></Session>
</QRZDatabase>"""

NOT_FOUND = f"""<?xml version="1.0" encoding="utf-8" ?>
<QRZDatabase version="1.34" {NS}>
  <Session><Key>abc123sessionkey</Key><Error>Not found: ZZ9ZZZ</Error></Session>
</QRZDatabase>"""

TIMED_OUT = f"""<?xml version="1.0" encoding="utf-8" ?>
<QRZDatabase version="1.34" {NS}>
  <Session><Error>Session Timeout</Error></Session>
</QRZDatabase>"""

NEEDS_SUB = f"""<?xml version="1.0" encoding="utf-8" ?>
<QRZDatabase version="1.34" {NS}>
  <Session><Key>abc123sessionkey</Key>
  <Error>A subscription is required to access the complete record</Error></Session>
</QRZDatabase>"""


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        pass


class FakeSession:
    """Stands in for ``requests.Session``, returning queued bodies in order."""

    def __init__(self, *bodies: str) -> None:
        self.bodies = list(bodies)
        self.urls: list[str] = []

    def get(self, url, timeout=None, headers=None):
        self.urls.append(url)
        if not self.bodies:
            raise AssertionError(f"unexpected extra request: {url}")
        return FakeResponse(self.bodies.pop(0))

    def close(self) -> None:
        pass


def make_client(*bodies: str) -> tuple[QrzClient, FakeSession]:
    http = FakeSession(*bodies)
    return QrzClient("user", "secret", session=http), http


def test_credentials_are_required():
    with pytest.raises(QrzAuthError):
        QrzClient("", "secret")
    with pytest.raises(QrzAuthError):
        QrzClient("user", "")


def test_login_returns_session_key_and_subscription_expiry():
    client, _ = make_client(LOGIN_OK)
    assert client.login() == "abc123sessionkey"
    assert client.sub_expires == "Wed Dec 31 00:00:00 2027"


def test_login_failure_raises_auth_error():
    client, _ = make_client(LOGIN_BAD)
    with pytest.raises(QrzAuthError, match="Username/password incorrect"):
        client.login()


def test_lookup_maps_qrz_fields():
    client, _ = make_client(LOGIN_OK, CALLSIGN_OK)
    info = client.lookup("w1aw")
    assert info.call == "W1AW"
    assert info.name == "Hiram Percy Maxim"
    assert info.qth == "Newington"      # addr2 is the city, addr1 the street
    assert info.state == "CT"
    assert info.county == "Hartford"
    assert info.country == "United States"
    assert info.grid == "FN31pr"
    assert info.dxcc == "291"
    assert info.cq_zone == "5"
    assert info.itu_zone == "8"
    assert info.license_class == "C"
    assert info.aliases == ["W1INF", "W1AW/1"]
    assert info.source == "qrz"


def test_lookup_logs_in_once_and_reuses_the_session_key():
    client, http = make_client(LOGIN_OK, CALLSIGN_OK, CALLSIGN_OK)
    client.lookup("W1AW")
    client.lookup("W1AW")
    assert len(http.urls) == 3
    assert "username=user" in http.urls[0]
    assert http.urls[1].count("s=abc123sessionkey") == 1
    assert "username=" not in http.urls[2]


def test_expired_session_triggers_one_relogin():
    client, http = make_client(LOGIN_OK, TIMED_OUT, LOGIN_OK, CALLSIGN_OK)
    info = client.lookup("W1AW")
    assert info.call == "W1AW"
    assert len(http.urls) == 4
    assert "username=user" in http.urls[2]


def test_unknown_callsign_raises_not_found():
    client, _ = make_client(LOGIN_OK, NOT_FOUND)
    with pytest.raises(QrzNotFound, match="ZZ9ZZZ"):
        client.lookup("ZZ9ZZZ")


def test_missing_subscription_raises_subscription_error():
    client, _ = make_client(LOGIN_OK, NEEDS_SUB)
    with pytest.raises(QrzSubscriptionError, match="subscription"):
        client.lookup("W1AW")


def test_network_failure_is_wrapped():
    class Broken(FakeSession):
        def get(self, url, timeout=None, headers=None):
            raise requests.ConnectionError("no route to host")

    client = QrzClient("user", "secret", session=Broken())
    with pytest.raises(QrzError, match="QRZ request failed"):
        client.lookup("W1AW")


def test_malformed_xml_is_wrapped():
    client, _ = make_client("<not xml")
    with pytest.raises(QrzError, match="malformed XML"):
        client.login()


def test_empty_callsign_is_rejected_without_a_request():
    client, http = make_client()
    with pytest.raises(QrzNotFound):
        client.lookup("   ")
    assert http.urls == []


def test_credentials_are_url_encoded():
    http = FakeSession(LOGIN_OK)
    client = QrzClient("user@example.com", "pa ss&word", session=http)
    client.login()
    assert "user%40example.com" in http.urls[0]
    assert "pa%20ss%26word" in http.urls[0]
    # Parameters are semicolon separated, as the QRZ specification documents.
    assert ";password=" in http.urls[0]


def test_summary_is_human_readable():
    client, _ = make_client(LOGIN_OK, CALLSIGN_OK)
    info = client.lookup("W1AW")
    assert info.summary() == "Hiram Percy Maxim, Newington, CT, United States, FN31pr"
