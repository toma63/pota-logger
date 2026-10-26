"""The interactive logging flow, driven by piped keystrokes."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import select

from pota_logger import operate
from pota_logger.lookup import CallsignResolver
from pota_logger.models import Qso
from pota_logger.operate import Sticky
from pota_logger.qrz import CallsignInfo, QrzError, QrzNotFound

from .test_lookup import StubQrz

W1AW = CallsignInfo(
    call="W1AW", name="Hiram Maxim", qth="Newington", state="CT",
    country="United States", grid="FN31pr", dxcc="291", cq_zone="5", itu_zone="8",
)


@pytest.fixture
def resolver(session):
    return CallsignResolver(session, None)


def qsos_of(session, activation) -> list[Qso]:
    return list(session.scalars(
        select(Qso).where(Qso.activation_id == activation.id).order_by(Qso.id)
    ))


# --------------------------------------------------------------------------
# Time handling
# --------------------------------------------------------------------------

def test_time_override_uses_the_current_utc_day():
    now = datetime(2026, 10, 18, 15, 0, 0)
    assert operate.parse_time_override("1432", now) == datetime(2026, 10, 18, 14, 32, 0)
    assert operate.parse_time_override("143215", now) == datetime(2026, 10, 18, 14, 32, 15)
    assert operate.parse_time_override("14:32", now) == datetime(2026, 10, 18, 14, 32, 0)


def test_future_time_is_read_as_yesterday():
    # Just after UTC midnight, "2359" means the day that just ended.
    now = datetime(2026, 10, 18, 0, 30, 0)
    assert operate.parse_time_override("2359", now) == datetime(2026, 10, 17, 23, 59, 0)


@pytest.mark.parametrize("text", ["", "bad", "9999", "12", "12345", "99:99"])
def test_bad_times_are_rejected(text):
    assert operate.parse_time_override(text, datetime(2026, 10, 18, 15, 0)) is None


# --------------------------------------------------------------------------
# Single QSO entry
# --------------------------------------------------------------------------

def test_log_qso_with_qrz_details(session, activation, config, typed):
    client = StubQrz(result=W1AW)
    resolver = CallsignResolver(session, client)
    sticky = Sticky(band="20m", mode="SSB", freq_mhz=14.285)

    qso = typed("\r\r", lambda: operate.log_qso(
        session, activation, resolver, config, sticky, "W1AW"
    ))

    assert qso is not None
    assert qso.call == "W1AW"
    assert qso.band == "20m"
    assert qso.mode == "SSB"
    assert qso.submode == "USB"       # derived from the frequency
    assert qso.rst_sent == "59"       # mode default taken by pressing Enter
    assert qso.rst_rcvd == "59"
    assert qso.name == "Hiram Maxim"
    assert qso.state == "CT"
    assert qso.grid == "FN31pr"
    assert qso.dxcc == "291"
    assert qso.lookup_source == "qrz"


def test_typed_rst_replaces_the_default(session, activation, config, resolver, typed):
    # Regression: a pre-filled default used to be appended to, giving "5957".
    config.prompt_on_lookup_failure = False
    sticky = Sticky(band="20m", mode="SSB", freq_mhz=14.285)
    qso = typed("55\r57\r", lambda: operate.log_qso(
        session, activation, resolver, config, sticky, "W1AW"
    ))
    assert (qso.rst_sent, qso.rst_rcvd) == ("55", "57")


def test_manual_fallback_prompts_when_lookup_fails(session, activation, config, resolver, typed):
    sticky = Sticky(band="40m", mode="CW", freq_mhz=7.032)
    # name, state, grid, then the two RST prompts.
    qso = typed("Bob\ron\rfn03\r\r579\r", lambda: operate.log_qso(
        session, activation, resolver, config, sticky, "VE3XYZ"
    ))
    assert qso.name == "Bob"
    assert qso.state == "ON"
    assert qso.grid == "FN03"
    assert qso.mode == "CW"
    assert qso.submode is None
    assert qso.rst_sent == "599"      # CW default
    assert qso.rst_rcvd == "579"
    assert qso.lookup_source == "manual"


def test_manual_details_are_cached_for_the_next_contact(session, activation, config, resolver, typed):
    sticky = Sticky()
    typed("Bob\rON\rfn03\r\r\r", lambda: operate.log_qso(
        session, activation, resolver, config, sticky, "VE3XYZ"
    ))
    # Second time around only the two RST prompts are answered.
    second = typed("\r\r", lambda: operate.log_qso(
        session, activation, resolver, config, sticky, "VE3XYZ/P"
    ))
    assert second.name == "Bob"
    assert second.state == "ON"


def test_lookup_failure_can_skip_the_manual_prompts(session, activation, config, resolver, typed):
    config.prompt_on_lookup_failure = False
    sticky = Sticky()
    qso = typed("\r\r", lambda: operate.log_qso(
        session, activation, resolver, config, sticky, "VE3XYZ"
    ))
    assert qso.name is None
    assert qso.lookup_source == "none"


def test_duplicate_is_refused_unless_confirmed(session, activation, config, resolver, make_qso, typed):
    make_qso("W1AW", band="20m", mode="SSB")
    sticky = Sticky(band="20m", mode="SSB", freq_mhz=14.285)
    config.prompt_on_lookup_failure = False

    # "n" at the "log it anyway?" confirmation.
    assert typed("n\r", lambda: operate.log_qso(
        session, activation, resolver, config, sticky, "W1AW"
    )) is None
    assert len(qsos_of(session, activation)) == 1

    # "y" logs the second contact.
    assert typed("y\r\r\r", lambda: operate.log_qso(
        session, activation, resolver, config, sticky, "W1AW"
    )) is not None
    assert len(qsos_of(session, activation)) == 2


def test_a_different_band_is_not_a_duplicate(session, activation, config, resolver, make_qso, typed):
    make_qso("W1AW", band="20m", mode="SSB")
    config.prompt_on_lookup_failure = False
    sticky = Sticky(band="40m", mode="SSB", freq_mhz=7.185)
    # No duplicate confirmation is expected, only the RST prompts.
    qso = typed("\r\r", lambda: operate.log_qso(
        session, activation, resolver, config, sticky, "W1AW"
    ))
    assert qso is not None and qso.band == "40m"


def test_full_mode_prompts_for_p2p_and_comment(session, activation, config, resolver, typed):
    config.prompt_on_lookup_failure = False
    sticky = Sticky()
    qso = typed("\r\rca-0123\rthanks\r", lambda: operate.log_qso(
        session, activation, resolver, config, sticky, "VE3XYZ", full=True
    ))
    assert qso.p2p_ref == "CA-0123"
    assert qso.comment == "thanks"


def test_time_override_is_consumed(session, activation, config, resolver, typed):
    config.prompt_on_lookup_failure = False
    sticky = Sticky(time_override=datetime(2026, 10, 18, 14, 32, 0))

    first = typed("\r\r", lambda: operate.log_qso(
        session, activation, resolver, config, sticky, "VE3XYZ"
    ))
    assert first.qso_at == datetime(2026, 10, 18, 14, 32, 0)
    assert sticky.time_override is None

    second = typed("\r\r", lambda: operate.log_qso(
        session, activation, resolver, config, sticky, "K2ABC"
    ))
    assert second.qso_at != datetime(2026, 10, 18, 14, 32, 0)


# --------------------------------------------------------------------------
# Callsign not on QRZ
# --------------------------------------------------------------------------

class DirectoryQrz:
    """A QRZ stand-in that knows a fixed set of callsigns."""

    def __init__(self, *known: CallsignInfo) -> None:
        self.known = {info.call: info for info in known}
        self.calls: list[str] = []

    def lookup(self, call: str):
        self.calls.append(call)
        if call not in self.known:
            raise QrzNotFound(f"Not found: {call}")
        return self.known[call]


def test_not_found_call_can_be_corrected(session, activation, config, typed):
    client = DirectoryQrz(W1AW)
    resolver = CallsignResolver(session, client)

    # W1AE is a typo: re-enter W1AW, then the two RST prompts.
    qso = typed("w1aw\r\r\r", lambda: operate.log_qso(
        session, activation, resolver, config, Sticky(), "W1AE"
    ))

    assert client.calls == ["W1AE", "W1AW"]
    assert qso.call == "W1AW"
    assert qso.name == "Hiram Maxim"
    assert qso.lookup_source == "qrz"


def test_correction_can_be_retried_until_found(session, activation, config, typed):
    client = DirectoryQrz(W1AW)
    resolver = CallsignResolver(session, client)
    qso = typed("w1ax\rw1aw\r\r\r", lambda: operate.log_qso(
        session, activation, resolver, config, Sticky(), "W1AE"
    ))
    assert client.calls == ["W1AE", "W1AX", "W1AW"]
    assert qso.call == "W1AW"


def test_blank_correction_keeps_the_call_and_prompts_by_hand(session, activation, config, typed):
    resolver = CallsignResolver(session, DirectoryQrz(W1AW))
    # Blank keeps VE3XYZ; then name, state, grid and the two RSTs.
    qso = typed("\rBob\ron\rfn03\r\r\r", lambda: operate.log_qso(
        session, activation, resolver, config, Sticky(), "VE3XYZ"
    ))
    assert qso.call == "VE3XYZ"
    assert qso.name == "Bob"
    assert qso.lookup_source == "manual"


def test_corrected_call_is_checked_for_duplicates(session, activation, config, make_qso, typed):
    make_qso("W1AW", band="20m", mode="SSB")
    resolver = CallsignResolver(session, DirectoryQrz(W1AW))
    sticky = Sticky(band="20m", mode="SSB", freq_mhz=14.285)
    # Correct to W1AW, then decline the duplicate.
    assert typed("w1aw\rn\r", lambda: operate.log_qso(
        session, activation, resolver, config, sticky, "W1AE"
    )) is None
    assert len(qsos_of(session, activation)) == 1


def test_qrz_outage_goes_straight_to_manual_entry(session, activation, config, typed):
    resolver = CallsignResolver(session, StubQrz(error=QrzError("no network")))
    # No correction prompt: name, state, grid, then the two RSTs.
    qso = typed("Bob\ron\rfn03\r\r\r", lambda: operate.log_qso(
        session, activation, resolver, config, Sticky(), "VE3XYZ"
    ))
    assert qso.call == "VE3XYZ"
    assert qso.name == "Bob"


def test_cancelling_a_prompt_saves_nothing(session, activation, config, resolver, typed):
    config.prompt_on_lookup_failure = False
    sticky = Sticky()
    with pytest.raises(operate.Cancelled):
        typed("\x03", lambda: operate.log_qso(   # Ctrl+C at the RST prompt
            session, activation, resolver, config, sticky, "W1AW"
        ))
    assert qsos_of(session, activation) == []


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def test_frequency_command_sets_band(session, activation, config):
    sticky = Sticky()
    assert operate.handle_command("/f 7.185", session, activation, config, sticky)
    assert (sticky.freq_mhz, sticky.band) == (7.185, "40m")


def test_frequency_command_accepts_khz(session, activation, config):
    sticky = Sticky()
    operate.handle_command("/f 14285", session, activation, config, sticky)
    assert (sticky.freq_mhz, sticky.band) == (14.285, "20m")


def test_out_of_band_frequency_is_refused(session, activation, config):
    sticky = Sticky(band="20m", freq_mhz=14.285)
    operate.handle_command("/f 13.0", session, activation, config, sticky)
    assert (sticky.freq_mhz, sticky.band) == (14.285, "20m")


def test_band_command_sets_a_calling_frequency(session, activation, config):
    sticky = Sticky()
    operate.handle_command("/b 40", session, activation, config, sticky)
    assert sticky.band == "40m"
    assert sticky.freq_mhz == pytest.approx(7.185)


def test_mode_command(session, activation, config):
    sticky = Sticky()
    operate.handle_command("/m cw", session, activation, config, sticky)
    assert sticky.mode == "CW"
    operate.handle_command("/m nonsense", session, activation, config, sticky)
    assert sticky.mode == "CW"  # unchanged


def test_p2p_command_applies_to_the_qso_just_logged(session, activation, config, make_qso):
    make_qso("W1AW", qso_at=datetime(2026, 10, 18, 14, 9, 0))
    # Back-dated with /t, but entered last, so it is the one /p targets.
    make_qso("K2ABC", qso_at=datetime(2026, 10, 18, 14, 1, 0))

    operate.handle_command("/p us-0002", session, activation, config, Sticky())
    assert [q.p2p_ref for q in qsos_of(session, activation)] == [None, "US-0002"]

    operate.handle_command("/p", session, activation, config, Sticky())
    assert [q.p2p_ref for q in qsos_of(session, activation)] == [None, None]


def test_p2p_command_rejects_a_bad_reference(session, activation, config, make_qso):
    make_qso("W1AW")
    operate.handle_command("/p nonsense", session, activation, config, Sticky())
    assert qsos_of(session, activation)[0].p2p_ref is None


def test_p2p_command_with_no_qsos_is_harmless(session, activation, config):
    assert operate.handle_command("/p us-0002", session, activation, config, Sticky()) is True


def test_time_command(session, activation, config):
    sticky = Sticky()
    operate.handle_command("/t 1432", session, activation, config, sticky)
    assert sticky.time_override is not None and sticky.time_override.hour == 14
    operate.handle_command("/t", session, activation, config, sticky)
    assert sticky.time_override is None


def test_end_command_closes_the_activation(session, activation, config):
    assert operate.handle_command("/end", session, activation, config, Sticky()) is False
    session.refresh(activation)
    assert activation.ended_at is not None
    assert not activation.is_open


def test_undo_deletes_the_last_qso(session, activation, config, make_qso, typed):
    make_qso("W1AW", qso_at=datetime(2026, 10, 18, 14, 1, 0))
    make_qso("K2ABC", qso_at=datetime(2026, 10, 18, 14, 9, 0))
    typed("y\r", lambda: operate.handle_command("/undo", session, activation, config, Sticky()))
    remaining = [q.call for q in qsos_of(session, activation)]
    assert remaining == ["W1AW"]


def test_undo_can_be_declined(session, activation, config, make_qso, typed):
    make_qso("W1AW")
    typed("n\r", lambda: operate.handle_command("/undo", session, activation, config, Sticky()))
    assert len(qsos_of(session, activation)) == 1


def test_export_command_writes_the_file(session, activation, config, make_qso, typed, tmp_path):
    make_qso("W1AW")
    typed("", lambda: operate.handle_command("/export", session, activation, config, Sticky()))
    written = list(config.resolved_adif_dir().glob("*.adi"))
    assert [p.name for p in written] == ["K1ABC@US-0001-20261018.adi"]


def test_unknown_command_keeps_the_loop_running(session, activation, config):
    assert operate.handle_command("/bogus", session, activation, config, Sticky()) is True


# --------------------------------------------------------------------------
# The whole loop
# --------------------------------------------------------------------------

def test_run_session_logs_a_short_activation(session, activation, config, typed):
    client = StubQrz(result=W1AW)
    resolver = CallsignResolver(session, client)
    keys = (
        "14.285\r"      # frequency
        "\r"            # mode -> SSB (first choice)
        "w1aw\r\r57\r"  # one QSO, default RST sent, 57 received
        "/m cw\r"       # switch mode
        "/f 7.032\r"    # and band
        "k2abc\r\r579\r"
        "\r"            # blank line stops
    )
    typed(keys, lambda: operate.run_session(session, activation, resolver, config))

    logged = qsos_of(session, activation)
    assert [(q.call, q.band, q.mode, q.rst_rcvd) for q in logged] == [
        ("W1AW", "20m", "SSB", "57"),
        ("K2ABC", "40m", "CW", "579"),
    ]
    assert logged[0].freq_mhz == 14.285
    assert logged[1].freq_mhz == 7.032
    # The activation is left open so it can be resumed.
    session.refresh(activation)
    assert activation.is_open


def test_run_session_p2p_after_the_exchange(session, activation, config, resolver, typed):
    config.prompt_on_lookup_failure = False
    keys = (
        "14.285\r\r"      # frequency, SSB
        "k2abc\r\r\r"     # calls and reports first
        "/p us-0002\r"    # then the other park
        "w1aw\r\r\r"
        "\r"
    )
    typed(keys, lambda: operate.run_session(session, activation, resolver, config))
    assert [(q.call, q.p2p_ref) for q in qsos_of(session, activation)] == [
        ("K2ABC", "US-0002"),
        ("W1AW", None),
    ]


def test_blank_frequency_falls_back_to_the_band_picker(session, activation, config, resolver, typed):
    # Blank frequency, then Enter accepts 20m in the band list and SSB in the
    # mode list, then a blank callsign stops the loop.
    typed("\r\r\r\r", lambda: operate.run_session(session, activation, resolver, config))
    assert qsos_of(session, activation) == []


def test_run_session_rejects_junk_at_the_call_prompt(session, activation, config, resolver, typed):
    typed("\r\r\rnot a call\r\r", lambda: operate.run_session(
        session, activation, resolver, config
    ))
    assert qsos_of(session, activation) == []


def test_run_session_resumes_the_previous_band_and_mode(
    session, activation, config, resolver, make_qso, typed, monkeypatch
):
    make_qso("W1AW", band="40m", mode="CW", submode=None, freq_mhz=7.032)
    captured = {}
    monkeypatch.setattr(operate, "prompt_band_mode", lambda sticky: captured.update(
        band=sticky.band, mode=sticky.mode, freq=sticky.freq_mhz
    ))

    typed("\r", lambda: operate.run_session(session, activation, resolver, config))

    assert captured == {"band": "40m", "mode": "CW", "freq": 7.032}


def test_ctrl_c_at_the_call_prompt_leaves_the_log_intact(session, activation, config, resolver, make_qso, typed):
    make_qso("W1AW")
    typed("\r\r\r\x03", lambda: operate.run_session(session, activation, resolver, config))
    assert len(qsos_of(session, activation)) == 1
    session.refresh(activation)
    assert activation.is_open
