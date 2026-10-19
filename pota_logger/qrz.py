"""QRZ.com XML callsign lookup client.

Uses the QRZ XML Logbook Data service (https://www.qrz.com/XML/current_spec.html),
which requires a QRZ account; full address/grid data requires an XML
subscription. The service hands out a session key that is reused for every
lookup and re-requested only when it expires.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import quote

import requests

QRZ_URL = "https://xmldata.qrz.com/xml/current/"
DEFAULT_AGENT = "pota-logger1.0"


class QrzError(Exception):
    """Base class for QRZ lookup failures."""


class QrzAuthError(QrzError):
    """Credentials were rejected by QRZ."""


class QrzNotFound(QrzError):
    """QRZ has no record for the callsign."""


class QrzSubscriptionError(QrzError):
    """The account lacks the XML subscription needed for full records."""


@dataclass
class CallsignInfo:
    """Normalized subset of a QRZ record that matters for a POTA log."""

    call: str
    name: str | None = None
    qth: str | None = None
    state: str | None = None
    county: str | None = None
    country: str | None = None
    grid: str | None = None
    dxcc: str | None = None
    cq_zone: str | None = None
    itu_zone: str | None = None
    license_class: str | None = None
    aliases: list[str] = field(default_factory=list)
    source: str = "qrz"

    def summary(self) -> str:
        """One-line description for the operator to eyeball before saving."""
        parts = [p for p in (self.name, self.qth, self.state, self.country) if p]
        if self.grid:
            parts.append(self.grid)
        return ", ".join(parts) if parts else "(no details)"


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _parse_xml(text: str) -> dict[str, dict[str, str]]:
    """Parse a QRZ response into ``{section: {field: value}}``, namespaces removed."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise QrzError(f"malformed XML from QRZ: {exc}") from exc
    result: dict[str, dict[str, str]] = {}
    for child in root:
        section = result.setdefault(_strip_ns(child.tag), {})
        for item in child:
            value = (item.text or "").strip()
            if value:
                section[_strip_ns(item.tag)] = value
    return result


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class QrzClient:
    """Minimal QRZ XML API client with lazy login and one automatic re-login."""

    def __init__(
        self,
        username: str,
        password: str,
        agent: str = DEFAULT_AGENT,
        timeout: float = 10.0,
        session: requests.Session | None = None,
    ) -> None:
        if not username or not password:
            raise QrzAuthError("QRZ username and password are both required")
        self.username = username
        self.password = password
        self.agent = agent
        self.timeout = timeout
        self._http = session or requests.Session()
        self._key: str | None = None
        self.sub_expires: str | None = None

    def _get(self, query: str) -> dict[str, dict[str, str]]:
        # QRZ documents semicolon-separated parameters, so the query string is
        # assembled by hand rather than passed through requests' params=.
        try:
            response = self._http.get(
                f"{QRZ_URL}?{query}", timeout=self.timeout,
                headers={"User-Agent": self.agent},
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise QrzError(f"QRZ request failed: {exc}") from exc
        return _parse_xml(response.text)

    def login(self) -> str:
        """Authenticate and cache a session key."""
        query = (
            f"username={quote(self.username)}"
            f";password={quote(self.password)}"
            f";agent={quote(self.agent)}"
        )
        sections = self._get(query)
        session = sections.get("Session", {})
        key = _clean(session.get("Key"))
        if not key:
            message = session.get("Error") or session.get("Message") or "login refused"
            raise QrzAuthError(f"QRZ login failed: {message}")
        self._key = key
        self.sub_expires = _clean(session.get("SubExp"))
        return key

    @property
    def session_key(self) -> str:
        if self._key is None:
            return self.login()
        return self._key

    def lookup(self, call: str) -> CallsignInfo:
        """Look up ``call``, re-authenticating once if the session has expired."""
        call = call.strip().upper()
        if not call:
            raise QrzNotFound("no callsign given")

        sections = self._lookup_raw(call, self.session_key)
        error = sections.get("Session", {}).get("Error", "")
        if _is_expired_session(error):
            sections = self._lookup_raw(call, self.login())
            error = sections.get("Session", {}).get("Error", "")

        record = sections.get("Callsign", {})
        if not record:
            lowered = error.lower()
            if "not found" in lowered:
                raise QrzNotFound(f"{call} not found on QRZ")
            if "subscription" in lowered:
                raise QrzSubscriptionError(error.strip())
            if _is_auth_error(error):
                raise QrzAuthError(error.strip())
            raise QrzError(error.strip() or f"no record returned for {call}")
        return _record_to_info(call, record)

    def _lookup_raw(self, call: str, key: str) -> dict[str, dict[str, str]]:
        return self._get(f"s={quote(key)};callsign={quote(call)}")

    def close(self) -> None:
        self._http.close()


def _is_expired_session(error: str) -> bool:
    lowered = error.lower()
    return "session timeout" in lowered or "invalid session key" in lowered


def _is_auth_error(error: str) -> bool:
    lowered = error.lower()
    return "password" in lowered or "username" in lowered or "not authorized" in lowered


def _record_to_info(call: str, record: dict[str, str]) -> CallsignInfo:
    """Map QRZ's ``<Callsign>`` fields onto :class:`CallsignInfo`."""
    first = _clean(record.get("fname"))
    last = _clean(record.get("name"))
    full_name = " ".join(p for p in (first, last) if p) or None
    return CallsignInfo(
        call=_clean(record.get("call")) or call,
        name=full_name,
        # addr2 is the city; addr1 is the street address, which a log never needs.
        qth=_clean(record.get("addr2")),
        state=_clean(record.get("state")),
        county=_clean(record.get("county")),
        country=_clean(record.get("country")),
        grid=_clean(record.get("grid")),
        dxcc=_clean(record.get("dxcc")),
        cq_zone=_clean(record.get("cqzone")),
        itu_zone=_clean(record.get("ituzone")),
        license_class=_clean(record.get("class")),
        aliases=[a for a in (_clean(record.get("aliases")) or "").split(",") if a],
        source="qrz",
    )
