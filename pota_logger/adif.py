"""ADIF 3.1.4 export shaped for the POTA log submission page.

POTA requires each record to carry STATION_CALLSIGN, CALL, QSO_DATE, TIME_ON,
BAND and MODE, plus MY_SIG/MY_SIG_INFO naming the activated park. Park-to-park
contacts add SIG/SIG_INFO for the park the other operator is activating.
Everything else written here is optional context that POTA ignores but other
log services (LoTW, Club Log) use.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import Activation, Qso
from .models import utcnow

ADIF_VERSION = "3.1.4"
PROGRAM_ID = "pota-logger"
PROGRAM_VERSION = "0.1.0"

# Fields POTA will reject a record for missing.
REQUIRED_FIELDS = ("STATION_CALLSIGN", "CALL", "QSO_DATE", "TIME_ON", "BAND", "MODE")


def adif_field(name: str, value: object) -> str:
    """Render one ADIF field, or an empty string when there is nothing to write.

    The declared length is the value's length in bytes, not characters, as the
    ADIF specification requires for UTF-8 data.
    """
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    # Newlines would break the one-record-per-line layout readers expect.
    text = text.replace("\r", " ").replace("\n", " ")
    return f"<{name.upper()}:{len(text.encode('utf-8'))}>{text}"


def build_header(created: datetime | None = None, record_count: int | None = None) -> str:
    """Build the ADIF header block, terminated by <EOH>."""
    created = created or utcnow()
    lines = [f"ADIF export from {PROGRAM_ID} - Parks On The Air activation log"]
    if record_count is not None:
        lines.append(f"{record_count} QSO record(s)")
    lines.append(adif_field("ADIF_VER", ADIF_VERSION))
    lines.append(adif_field("PROGRAMID", PROGRAM_ID))
    lines.append(adif_field("PROGRAMVERSION", PROGRAM_VERSION))
    lines.append(adif_field("CREATED_TIMESTAMP", created.strftime("%Y%m%d %H%M%S")))
    lines.append("<EOH>")
    return "\n".join(lines) + "\n"


def qso_fields(qso: Qso, activation: Activation) -> list[tuple[str, object]]:
    """Ordered ADIF fields for one QSO, POTA-required ones first."""
    fields: list[tuple[str, object]] = [
        ("STATION_CALLSIGN", activation.station_callsign),
        ("CALL", qso.call),
        ("QSO_DATE", qso.qso_at.strftime("%Y%m%d")),
        ("TIME_ON", qso.qso_at.strftime("%H%M%S")),
        ("BAND", qso.band),
        ("MODE", qso.mode),
        ("SUBMODE", qso.submode),
        # The park being activated, which is what makes this a POTA log.
        ("MY_SIG", "POTA"),
        ("MY_SIG_INFO", activation.park_ref),
    ]
    # Only emit OPERATOR when it differs, so a solo activation stays uncluttered.
    if activation.operator and activation.operator != activation.station_callsign:
        fields.append(("OPERATOR", activation.operator))
    if qso.p2p_ref:
        fields.append(("SIG", "POTA"))
        fields.append(("SIG_INFO", qso.p2p_ref))
    fields.extend([
        ("FREQ", f"{qso.freq_mhz:.6f}".rstrip("0").rstrip(".") if qso.freq_mhz else None),
        ("RST_SENT", qso.rst_sent),
        ("RST_RCVD", qso.rst_rcvd),
        ("MY_GRIDSQUARE", activation.my_grid),
        ("MY_STATE", activation.my_state),
        ("MY_COUNTRY", activation.my_country),
        ("NAME", qso.name),
        ("QTH", qso.qth),
        ("STATE", qso.state),
        ("CNTY", qso.county),
        ("COUNTRY", qso.country),
        ("GRIDSQUARE", qso.grid),
        ("DXCC", qso.dxcc),
        ("CQZ", qso.cq_zone),
        ("ITUZ", qso.itu_zone),
        ("COMMENT", qso.comment),
    ])
    return fields


def qso_to_adif(qso: Qso, activation: Activation) -> str:
    """Render one QSO as an ADIF record line ending in <EOR>."""
    rendered = [adif_field(name, value) for name, value in qso_fields(qso, activation)]
    return "".join(part for part in rendered if part) + "<EOR>\n"


def validate_qso(qso: Qso, activation: Activation) -> list[str]:
    """Return human-readable reasons POTA would reject this record."""
    present = {name: value for name, value in qso_fields(qso, activation)}
    problems = [
        f"missing {name}"
        for name in REQUIRED_FIELDS
        if not str(present.get(name) or "").strip()
    ]
    if not activation.park_ref:
        problems.append("missing MY_SIG_INFO (park reference)")
    return problems


def render_adif(pairs: Iterable[tuple[Activation, Qso]], created: datetime | None = None) -> str:
    """Render a full ADIF document for ``(activation, qso)`` pairs."""
    records = [qso_to_adif(qso, activation) for activation, qso in pairs]
    return build_header(created=created, record_count=len(records)) + "".join(records)


def pota_filename(activation: Activation, date: datetime | None = None) -> str:
    """Build the filename POTA's upload page expects.

    POTA asks for ``<callsign>@<park>-<YYYYMMDD>.adi``, with any ``/`` in a
    portable callsign replaced by ``-`` so the name is filesystem-safe.
    """
    date = date or activation.started_at
    call = activation.station_callsign.upper().replace("/", "-")
    return f"{call}@{activation.park_ref.upper()}-{date:%Y%m%d}.adi"


def export_activation(activation: Activation, out_dir: str | Path) -> Path:
    """Write one activation's QSOs to a POTA-named ADIF file and return the path."""
    out_path = Path(out_dir).expanduser() / pota_filename(activation)
    pairs = [(activation, qso) for qso in activation.qsos]
    return write_adif(pairs, out_path)


def write_adif(pairs: Iterable[tuple[Activation, Qso]], out_path: str | Path) -> Path:
    """Write an ADIF document to ``out_path``, creating parent directories."""
    path = Path(out_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    # ADIF is ASCII-safe but names and QTHs are not, so write UTF-8 with CRLF
    # line endings, which every logger and the POTA parser accept.
    path.write_text(render_adif(pairs), encoding="utf-8", newline="\r\n")
    return path
