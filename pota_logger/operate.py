"""Interactive activation and QSO prompting built on questionary.

Each QSO is committed as soon as it is entered. A POTA activation happens on a
hilltop with a battery and no second chances, so the log is durable after every
contact rather than only at the end of the session.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import questionary
from questionary import Choice
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import adif, bands
from .config import Config
from .lookup import CallsignResolver, LookupResult
from .models import Activation, Qso, utcnow
from .qrz import CallsignInfo
from .validate import (
    is_valid_call,
    is_valid_grid,
    is_valid_park,
    normalize_call,
    normalize_grid,
    normalize_park,
)

# A valid POTA activation needs at least this many contacts from one park.
POTA_ACTIVATION_MINIMUM = 10

HELP_TEXT = """
Commands (type at the callsign prompt):
  /f 14.285     set frequency (MHz or kHz); band follows automatically
  /b 20m        set band directly
  /m cw         set mode (SSB CW FM AM FT8 FT4 JS8 RTTY PSK31 DIGITALVOICE)
  /p US-0001    park-to-park: add this park to the QSO just logged (/p clears it)
  /t 1432       log the next QSO at this UTC time (HHMM or HHMMSS)
  /undo         delete the most recent QSO
  /list         show the last 10 QSOs
  /stats        show totals for this activation
  /export       write the ADIF file now and keep logging
  /help         this help
  /end          close the activation and exit
  <Enter> or q  stop logging, leaving the activation open
"""


class Cancelled(Exception):
    """Raised when the operator aborts a prompt with Ctrl+C."""


@dataclass
class Sticky:
    """Operating state carried from one QSO to the next."""

    band: str = "20m"
    mode: str = "SSB"
    freq_mhz: float | None = None
    # One-shot value, consumed by the next QSO and then cleared.
    time_override: datetime | None = None

    @property
    def banner(self) -> str:
        freq = f" {self.freq_mhz:g}" if self.freq_mhz else ""
        return f"[{self.band} {self.mode}{freq}]"


def _ask(prompt) -> str:
    """Run a questionary prompt, converting Ctrl+C into :class:`Cancelled`."""
    answer = prompt.ask()
    if answer is None:
        raise Cancelled
    return answer


def ask_with_default(message: str, default: str, validate=None) -> str:
    """Prompt with the default shown in the message instead of pre-filled.

    questionary pre-fills a ``default`` and leaves the cursor after it, so
    typing a different value appends to it and the operator has to backspace
    first. That is wrong for a value like RST that changes every contact, so
    the buffer starts empty and a blank answer means "take the default".
    """
    label = f"{message} [{default}]" if default else message
    answer = _ask(questionary.text(label, validate=validate)).strip()
    return answer or default


# prompt_toolkit cannot build a styled output stream outside a real console
# (notably mintty/git-bash on Windows). Fall back to plain printing once that
# has been established, rather than failing on every line of output.
_plain_output = False


def say(text: str, style: str = "") -> None:
    global _plain_output
    if not _plain_output:
        try:
            questionary.print(text, style=style)
            return
        except Exception:
            _plain_output = True
    print(text)


def info(text: str) -> None:
    say(text, style="fg:cyan")


def warn(text: str) -> None:
    say(text, style="fg:yellow")


def error(text: str) -> None:
    say(text, style="fg:red bold")


def good(text: str) -> None:
    say(text, style="fg:green")


# --------------------------------------------------------------------------
# Activation setup
# --------------------------------------------------------------------------

def open_activations(session: Session) -> list[Activation]:
    stmt = (
        select(Activation)
        .where(Activation.ended_at.is_(None))
        .order_by(Activation.started_at.desc())
    )
    return list(session.scalars(stmt))


def choose_activation(session: Session, config: Config) -> Activation:
    """Resume an open activation or create a new one."""
    existing = open_activations(session)
    if existing:
        choices = [Choice(title=a.label, value=a) for a in existing]
        choices.append(Choice(title="Start a new activation", value=None))
        picked = _ask(questionary.select("Activation:", choices=choices))
        if picked is not None:
            info(
                f"Resuming {picked.station_callsign} at {picked.park_ref} "
                f"({len(picked.qsos)} QSOs logged)."
            )
            return picked
    return create_activation(session, config)


def create_activation(session: Session, config: Config) -> Activation:
    """Prompt for the details that apply to every QSO in a new activation."""
    station = normalize_call(_ask(questionary.text(
        "Station callsign (yours):",
        default=config.station_callsign,
        validate=lambda v: True if is_valid_call(v) else "Enter a valid callsign",
    )))
    operator = normalize_call(_ask(questionary.text(
        "Operator callsign (blank if same as station):",
        default=config.operator,
        validate=lambda v: True if not v.strip() or is_valid_call(v) else "Enter a valid callsign",
    )))
    park = normalize_park(_ask(questionary.text(
        "Park reference (e.g. US-0001):",
        default=config.default_park,
        validate=lambda v: True if is_valid_park(v) else "Expected a reference like US-0001",
    )))
    park_name = _ask(questionary.text("Park name (optional):")).strip()
    grid = normalize_grid(_ask(questionary.text(
        "Your grid square:",
        default=config.my_grid,
        validate=lambda v: True if not v.strip() or is_valid_grid(v) else "Expected a locator like FN42bb",
    )))
    state = _ask(questionary.text(
        "Your state/province (optional):", default=config.my_state
    )).strip().upper()
    country = _ask(questionary.text(
        "Your country (optional):", default=config.my_country
    )).strip()

    activation = Activation(
        station_callsign=station,
        operator=operator or station,
        park_ref=park,
        park_name=park_name or None,
        my_grid=grid or None,
        my_state=state or None,
        my_country=country or None,
        started_at=utcnow(),
    )
    session.add(activation)
    session.commit()
    good(f"Started activation #{activation.id}: {station} at {park}.")
    return activation


def prompt_band_mode(sticky: Sticky) -> None:
    """Prompt for the starting frequency and mode of an operating stretch."""
    raw = _ask(questionary.text(
        "Frequency in MHz (blank to pick a band instead):",
        default=f"{sticky.freq_mhz:g}" if sticky.freq_mhz else "",
        validate=lambda v: True if not v.strip() or bands.parse_freq(v) else "Could not read that frequency",
    ))
    freq = bands.parse_freq(raw) if raw.strip() else None
    if freq is not None:
        band = bands.band_for_freq(freq)
        if band is None:
            warn(f"{freq:g} MHz is outside the amateur bands; pick a band instead.")
        else:
            sticky.freq_mhz, sticky.band = freq, band

    if freq is None or bands.band_for_freq(freq) is None:
        sticky.band = _ask(questionary.select(
            "Band:",
            choices=list(bands.COMMON_BANDS),
            default=sticky.band if sticky.band in bands.COMMON_BANDS else "20m",
        ))
        sticky.freq_mhz = bands.CALLING_FREQS.get(sticky.band)

    sticky.mode = _ask(questionary.select(
        "Mode:", choices=list(bands.MODE_LABELS), default=sticky.mode
    ))


# --------------------------------------------------------------------------
# QSO entry
# --------------------------------------------------------------------------

def find_duplicate(
    session: Session, activation: Activation, call: str, band: str, mode: str
) -> Qso | None:
    """Return a same-band, same-mode contact with this station in this activation."""
    stmt = (
        select(Qso)
        .where(
            Qso.activation_id == activation.id,
            Qso.call == call,
            Qso.band == band,
            Qso.mode == mode,
        )
        .order_by(Qso.qso_at.desc())
    )
    return session.scalars(stmt).first()


def describe_lookup(result: LookupResult) -> None:
    """Tell the operator what the lookup produced."""
    if result.found:
        label = "QRZ" if result.source == "qrz" else "cached"
        say(f"  {label}: {result.info.summary()}", style="fg:green")
    elif result.source == "not_found":
        warn(f"  not on QRZ ({result.detail})")
    elif result.source == "error":
        error(f"  lookup failed: {result.detail}")
    # "disabled" is silent: the operator already knows QRZ is switched off.


def prompt_station_details(call: str, existing: CallsignInfo | None) -> CallsignInfo:
    """Fallback prompts for the fields QRZ would otherwise have supplied."""
    base = existing or CallsignInfo(call=call, source="manual")
    name = _ask(questionary.text("  Name:", default=base.name or "")).strip()
    state = _ask(questionary.text(
        "  State/province:", default=base.state or ""
    )).strip().upper()
    grid = normalize_grid(_ask(questionary.text(
        "  Grid:",
        default=base.grid or "",
        validate=lambda v: True if not v.strip() or is_valid_grid(v) else "Expected a locator like FN42bb",
    )))
    return CallsignInfo(
        call=call,
        name=name or None,
        qth=base.qth,
        state=state or None,
        county=base.county,
        country=base.country,
        grid=grid or None,
        dxcc=base.dxcc,
        cq_zone=base.cq_zone,
        itu_zone=base.itu_zone,
        source="manual",
    )


def prompt_call_correction(call: str) -> str | None:
    """Ask for a corrected callsign. Returns None to keep ``call`` as entered."""
    raw = _ask(questionary.text(
        f"  Re-enter the call (blank keeps {call}):",
        validate=lambda v: True if not v.strip() or is_valid_call(v) else "Enter a valid callsign",
    ))
    corrected = normalize_call(raw) if raw.strip() else ""
    if not corrected or corrected == call:
        return None
    return corrected


def parse_time_override(text: str, now: datetime | None = None) -> datetime | None:
    """Parse ``HHMM``/``HHMMSS`` UTC into a datetime on the current UTC day.

    A time later than the current clock is read as yesterday, which is what the
    operator means when catching up on paper notes across UTC midnight.
    """
    digits = text.strip().replace(":", "")
    if not digits.isdigit() or len(digits) not in (4, 6):
        return None
    now = now or utcnow()
    try:
        hour, minute = int(digits[0:2]), int(digits[2:4])
        second = int(digits[4:6]) if len(digits) == 6 else 0
        stamp = now.replace(hour=hour, minute=minute, second=second, microsecond=0)
    except ValueError:
        return None
    if stamp > now + timedelta(minutes=1):
        stamp -= timedelta(days=1)
    return stamp


def apply_station_info(qso: Qso, station: CallsignInfo) -> None:
    """Copy resolved callsign details onto the QSO row."""
    qso.name = station.name
    qso.qth = station.qth
    qso.state = station.state
    qso.county = station.county
    qso.country = station.country
    qso.grid = station.grid
    qso.dxcc = station.dxcc
    qso.cq_zone = station.cq_zone
    qso.itu_zone = station.itu_zone


def log_qso(
    session: Session,
    activation: Activation,
    resolver: CallsignResolver,
    config: Config,
    sticky: Sticky,
    call: str,
    full: bool = False,
) -> Qso | None:
    """Resolve, confirm and persist one contact. Returns None if it was skipped."""
    # Timestamp at call entry, so neither the lookup nor the remaining prompts
    # push the logged time away from when the exchange actually happened.
    qso_at = sticky.time_override or utcnow()

    adif_mode, submode = bands.adif_mode(sticky.mode, sticky.freq_mhz)
    while True:
        duplicate = find_duplicate(session, activation, call, sticky.band, adif_mode)
        if duplicate is not None:
            warn(
                f"  duplicate: {call} already worked on {sticky.band} {sticky.mode} "
                f"at {duplicate.qso_at:%H:%M}Z"
            )
            if not _ask(questionary.confirm("  Log it anyway?", default=False)):
                return None

        result = resolver.resolve(call)
        describe_lookup(result)
        if result.source != "not_found":
            break
        # QRZ answered but has no such call: far more often a typo than an
        # unlisted station, so offer a correction before the manual prompts.
        corrected = prompt_call_correction(call)
        if corrected is None:
            break
        call = corrected

    station = result.info
    if station is None and config.prompt_on_lookup_failure:
        station = prompt_station_details(call, None)
        if any((station.name, station.state, station.grid)):
            resolver.remember(call, station)

    default_rst = bands.default_rst(sticky.mode)
    rst_sent = ask_with_default("  RST sent:", default_rst)
    rst_rcvd = ask_with_default("  RST rcvd:", default_rst)

    p2p = None
    comment = ""
    if full:
        p2p_raw = _ask(questionary.text(
            "  Park-to-park reference (blank for none):",
            validate=lambda v: True if not v.strip() or is_valid_park(v) else "Expected a reference like US-0001",
        ))
        p2p = normalize_park(p2p_raw) if p2p_raw.strip() else None
        comment = _ask(questionary.text("  Comment (optional):")).strip()

    qso = Qso(
        activation_id=activation.id,
        call=call,
        qso_at=qso_at,
        band=sticky.band,
        mode=adif_mode,
        submode=submode,
        freq_mhz=sticky.freq_mhz,
        rst_sent=rst_sent or None,
        rst_rcvd=rst_rcvd or None,
        p2p_ref=p2p,
        comment=comment or None,
        lookup_source=result.source if result.found else (station.source if station else "none"),
    )
    if station is not None:
        apply_station_info(qso, station)

    session.add(qso)
    session.commit()

    # A time override applies to a single contact only.
    sticky.time_override = None
    return qso


# --------------------------------------------------------------------------
# Commands and the main loop
# --------------------------------------------------------------------------

def show_recent(session: Session, activation: Activation, limit: int = 10) -> None:
    stmt = (
        select(Qso)
        .where(Qso.activation_id == activation.id)
        .order_by(Qso.qso_at.desc())
        .limit(limit)
    )
    rows = list(session.scalars(stmt))
    if not rows:
        info("No QSOs logged yet.")
        return
    for qso in reversed(rows):
        p2p = f" P2P {qso.p2p_ref}" if qso.p2p_ref else ""
        say(
            f"  {qso.qso_at:%H:%M:%S}Z  {qso.call:<10} {qso.band:>6} {qso.mode:<5}"
            f" {qso.rst_sent or '':>4}/{qso.rst_rcvd or '':<4}{p2p}"
        )


def show_stats(session: Session, activation: Activation) -> None:
    qsos = list(session.scalars(
        select(Qso).where(Qso.activation_id == activation.id).order_by(Qso.qso_at)
    ))
    total = len(qsos)
    info(f"Activation #{activation.id}: {activation.station_callsign} at {activation.park_ref}")
    say(f"  QSOs: {total}  (POTA needs {POTA_ACTIVATION_MINIMUM} for a valid activation)")
    if total:
        by_band: dict[str, int] = {}
        by_mode: dict[str, int] = {}
        for qso in qsos:
            by_band[qso.band] = by_band.get(qso.band, 0) + 1
            by_mode[qso.mode] = by_mode.get(qso.mode, 0) + 1
        say("  bands: " + ", ".join(f"{k} {v}" for k, v in sorted(by_band.items())))
        say("  modes: " + ", ".join(f"{k} {v}" for k, v in sorted(by_mode.items())))
        say(f"  p2p:   {sum(1 for q in qsos if q.p2p_ref)}")
        say(f"  first: {qsos[0].qso_at:%Y-%m-%d %H:%M}Z   last: {qsos[-1].qso_at:%H:%M}Z")
    if total and total < POTA_ACTIVATION_MINIMUM:
        warn(f"  {POTA_ACTIVATION_MINIMUM - total} more QSO(s) needed.")


def undo_last(session: Session, activation: Activation) -> None:
    stmt = (
        select(Qso)
        .where(Qso.activation_id == activation.id)
        .order_by(Qso.qso_at.desc(), Qso.id.desc())
        .limit(1)
    )
    last = session.scalars(stmt).first()
    if last is None:
        info("Nothing to undo.")
        return
    label = f"{last.call} {last.band} {last.mode} at {last.qso_at:%H:%M:%S}Z"
    if _ask(questionary.confirm(f"Delete {label}?", default=False)):
        session.delete(last)
        session.commit()
        good(f"Deleted {label}.")


def set_last_p2p(session: Session, activation: Activation, arg: str) -> None:
    """Set or clear the park-to-park reference on the most recently entered QSO.

    The other park is usually given after calls and reports are exchanged, so
    it is attached to the contact just logged rather than the next one.
    """
    if arg and not is_valid_park(arg):
        error("  Usage: /p US-0001   (or /p alone to clear)")
        return
    # Latest by id, not time: a /t back-dated QSO is still the one just typed.
    last = session.scalars(
        select(Qso)
        .where(Qso.activation_id == activation.id)
        .order_by(Qso.id.desc())
        .limit(1)
    ).first()
    if last is None:
        error("  No QSO logged yet to attach a park to.")
        return
    if arg:
        last.p2p_ref = normalize_park(arg)
        session.commit()
        good(f"  {last.call} is park-to-park with {last.p2p_ref}.")
    else:
        last.p2p_ref = None
        session.commit()
        info(f"  Park-to-park cleared on {last.call}.")


def export_now(session: Session, activation: Activation, config: Config) -> None:
    """Write the activation's ADIF file, reporting anything POTA would reject."""
    session.refresh(activation)
    if not activation.qsos:
        info("Nothing to export yet.")
        return
    for qso in activation.qsos:
        issues = adif.validate_qso(qso, activation)
        if issues:
            error(f"  QSO {qso.id} ({qso.call}): {', '.join(issues)}")
    path = adif.export_activation(activation, config.resolved_adif_dir())
    good(f"Wrote {len(activation.qsos)} QSO(s) to {path}")
    if len(activation.qsos) < POTA_ACTIVATION_MINIMUM:
        warn(
            f"  Only {len(activation.qsos)} QSOs: below the POTA minimum of "
            f"{POTA_ACTIVATION_MINIMUM} for a valid activation."
        )


def handle_command(
    raw: str,
    session: Session,
    activation: Activation,
    config: Config,
    sticky: Sticky,
) -> bool:
    """Handle a ``/command``. Returns False when the logging loop should stop."""
    parts = raw[1:].split(maxsplit=1)
    name = parts[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""

    if name in {"h", "help", "?"}:
        say(HELP_TEXT)
    elif name in {"f", "freq"}:
        freq = bands.parse_freq(arg) if arg else None
        if freq is None:
            error("  Usage: /f 14.285")
        elif bands.band_for_freq(freq) is None:
            error(f"  {freq:g} MHz is outside the amateur bands.")
        else:
            sticky.freq_mhz = freq
            sticky.band = bands.band_for_freq(freq)
            info(f"  Now {freq:g} MHz ({sticky.band}).")
    elif name in {"b", "band"}:
        band = bands.normalize_band(arg) if arg else None
        if band is None:
            error(f"  Usage: /b 20m   (common: {', '.join(bands.COMMON_BANDS)})")
        else:
            sticky.band = band
            sticky.freq_mhz = bands.CALLING_FREQS.get(band)
            info(f"  Now {band}.")
    elif name in {"m", "mode"}:
        mode = bands.normalize_mode(arg) if arg else None
        if mode is None:
            error(f"  Usage: /m cw   (known: {', '.join(bands.MODE_LABELS)})")
        else:
            sticky.mode = mode
            info(f"  Now {mode}.")
    elif name in {"p", "p2p"}:
        set_last_p2p(session, activation, arg)
    elif name in {"t", "time"}:
        if not arg:
            sticky.time_override = None
            info("  Using the current time again.")
        else:
            stamp = parse_time_override(arg)
            if stamp is None:
                error("  Usage: /t 1432  (UTC HHMM or HHMMSS)")
            else:
                sticky.time_override = stamp
                info(f"  Next QSO will be logged at {stamp:%Y-%m-%d %H:%M:%S}Z.")
    elif name == "undo":
        undo_last(session, activation)
    elif name in {"l", "list"}:
        show_recent(session, activation)
    elif name in {"s", "stats"}:
        show_stats(session, activation)
    elif name in {"e", "export"}:
        export_now(session, activation, config)
    elif name == "end":
        activation.ended_at = utcnow()
        session.commit()
        good(f"Closed activation #{activation.id}.")
        return False
    else:
        error(f"  Unknown command '/{name}'. Try /help.")
    return True


def run_session(
    session: Session,
    activation: Activation,
    resolver: CallsignResolver,
    config: Config,
    full: bool = False,
) -> None:
    """Run the callsign-entry loop until the operator stops."""
    sticky = Sticky()
    last = session.scalars(
        select(Qso)
        .where(Qso.activation_id == activation.id)
        .order_by(Qso.qso_at.desc())
        .limit(1)
    ).first()
    if last is not None:
        # Pick up where the previous stretch left off.
        sticky.band = last.band
        sticky.freq_mhz = last.freq_mhz
        sticky.mode = bands.normalize_mode(last.submode or last.mode) or "SSB"

    prompt_band_mode(sticky)
    info("Type a callsign and press Enter. /help lists commands, blank line stops.")

    count = len(activation.qsos)
    while True:
        try:
            raw = _ask(questionary.text(f"{sticky.banner} Call:")).strip()
        except Cancelled:
            warn("\nStopped. The activation is still open; run `pota log` to continue.")
            return

        if not raw or raw.lower() in {"q", "quit", "exit", "."}:
            info("Stopping. The activation stays open for later.")
            return

        try:
            if raw.startswith("/"):
                if not handle_command(raw, session, activation, config, sticky):
                    return
                continue

            call = normalize_call(raw)
            if not is_valid_call(call):
                error(f"  '{raw}' does not look like a callsign (/help for commands).")
                continue

            qso = log_qso(session, activation, resolver, config, sticky, call, full)
        except Cancelled:
            warn("  QSO abandoned, nothing saved.")
            continue

        if qso is None:
            continue
        count += 1
        good(f"  #{count:<3} {qso.call} {qso.band} {qso.mode} {qso.qso_at:%H:%M:%S}Z logged.")
        if count == POTA_ACTIVATION_MINIMUM:
            good(f"  That is {POTA_ACTIVATION_MINIMUM} QSOs: a valid activation.")
