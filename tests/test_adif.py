"""ADIF rendering, POTA-required fields and file naming."""

import re
from datetime import datetime

import pytest

from pota_logger import adif
from pota_logger.models import Qso


def test_field_length_is_bytes_not_characters():
    # ADIF declares lengths in bytes, so a non-ASCII name must not be
    # under-counted or readers will mis-parse the rest of the record.
    assert adif.adif_field("NAME", "Jose") == "<NAME:4>Jose"
    assert adif.adif_field("NAME", "José") == "<NAME:5>José"
    assert adif.adif_field("QTH", "Münster") == "<QTH:8>Münster"


def test_empty_fields_are_omitted():
    assert adif.adif_field("NAME", None) == ""
    assert adif.adif_field("NAME", "") == ""
    assert adif.adif_field("NAME", "   ") == ""


def test_newlines_are_flattened():
    assert adif.adif_field("COMMENT", "two\nlines") == "<COMMENT:9>two lines"


def test_record_contains_every_pota_required_field(activation, make_qso):
    qso = make_qso("W1AW")
    record = adif.qso_to_adif(qso, activation)
    for field in adif.REQUIRED_FIELDS:
        assert f"<{field}:" in record
    # The park being activated is what makes it a POTA log.
    assert "<MY_SIG:4>POTA" in record
    assert "<MY_SIG_INFO:7>US-0001" in record
    assert record.endswith("<EOR>\n")


def test_date_and_time_are_utc_formatted(activation, make_qso):
    qso = make_qso("W1AW", qso_at=datetime(2026, 10, 18, 14, 3, 12))
    record = adif.qso_to_adif(qso, activation)
    assert "<QSO_DATE:8>20261018" in record
    assert "<TIME_ON:6>140312" in record


def test_park_to_park_adds_sig_fields(activation, make_qso):
    qso = make_qso("VE3XYZ", p2p_ref="CA-0123")
    record = adif.qso_to_adif(qso, activation)
    assert "<SIG:4>POTA" in record
    assert "<SIG_INFO:7>CA-0123" in record


def test_no_sig_fields_without_park_to_park(activation, make_qso):
    record = adif.qso_to_adif(make_qso("W1AW"), activation)
    assert "<SIG:" not in record
    assert "<SIG_INFO:" not in record


def test_operator_only_written_when_it_differs(activation, make_qso, session):
    qso = make_qso("W1AW")
    assert "<OPERATOR:" not in adif.qso_to_adif(qso, activation)

    activation.operator = "K9XYZ"
    session.commit()
    assert "<OPERATOR:5>K9XYZ" in adif.qso_to_adif(qso, activation)


def test_frequency_is_trimmed_not_padded(activation, make_qso):
    record = adif.qso_to_adif(make_qso("W1AW", freq_mhz=14.285), activation)
    assert "<FREQ:6>14.285" in record
    record = adif.qso_to_adif(make_qso("W1AX", freq_mhz=7.0), activation)
    assert "<FREQ:1>7" in record


def test_header_declares_version_and_ends_with_eoh():
    header = adif.build_header(created=datetime(2026, 10, 18, 17, 0, 0), record_count=3)
    assert "<ADIF_VER:5>3.1.4" in header
    assert "<PROGRAMID:11>pota-logger" in header
    assert "<CREATED_TIMESTAMP:15>20261018 170000" in header
    assert header.rstrip().endswith("<EOH>")
    # The header must not begin with a field, per the ADIF specification.
    assert not header.startswith("<")


def test_render_adif_emits_one_record_per_qso(activation, make_qso):
    pairs = [(activation, make_qso(call)) for call in ("W1AW", "K2ABC", "VE3XYZ")]
    document = adif.render_adif(pairs)
    assert document.count("<EOR>") == 3
    assert document.index("<EOH>") < document.index("<EOR>")


def test_validate_reports_missing_required_fields(activation):
    empty = Qso(call="", band="", mode="", qso_at=datetime(2026, 1, 1))
    problems = adif.validate_qso(empty, activation)
    assert "missing CALL" in problems
    assert "missing BAND" in problems
    assert "missing MODE" in problems


def test_validate_passes_a_complete_qso(activation, make_qso):
    assert adif.validate_qso(make_qso("W1AW"), activation) == []


def test_pota_filename_convention(activation):
    assert adif.pota_filename(activation) == "K1ABC@US-0001-20261018.adi"


def test_pota_filename_is_filesystem_safe_for_portable_calls(activation, session):
    activation.station_callsign = "K1ABC/P"
    session.commit()
    name = adif.pota_filename(activation)
    assert "/" not in name
    assert name == "K1ABC-P@US-0001-20261018.adi"


def test_export_activation_writes_file(activation, make_qso, tmp_path):
    make_qso("W1AW")
    make_qso("K2ABC")
    path = adif.export_activation(activation, tmp_path / "out")
    assert path.name == "K1ABC@US-0001-20261018.adi"
    text = path.read_text(encoding="utf-8")
    assert text.count("<EOR>") == 2
    # CRLF line endings, which every ADIF reader accepts.
    assert "\r\n" in path.read_bytes().decode("utf-8")


def test_records_are_one_per_line(activation, make_qso, tmp_path):
    make_qso("W1AW")
    path = adif.export_activation(activation, tmp_path)
    body = path.read_text(encoding="utf-8").split("<EOH>")[1]
    records = [line for line in body.splitlines() if line.strip()]
    assert len(records) == 1
    assert re.match(r"^(<[A-Z_]+:\d+>[^<]*)+<EOR>$", records[0])
