"""ADIF band plan and mode tables.

Frequencies are megahertz. The band edges follow the ADIF 3.1.4 specification's
Band enumeration, which is what the POTA log processor validates against.
"""

from __future__ import annotations

from typing import NamedTuple


class Band(NamedTuple):
    name: str
    low: float
    high: float


# ADIF 3.1.4 Band enumeration, lowest to highest.
BANDS: tuple[Band, ...] = (
    Band("2190m", 0.1357, 0.1378),
    Band("630m", 0.472, 0.479),
    Band("560m", 0.501, 0.504),
    Band("160m", 1.8, 2.0),
    Band("80m", 3.5, 4.0),
    Band("60m", 5.06, 5.45),
    Band("40m", 7.0, 7.3),
    Band("30m", 10.1, 10.15),
    Band("20m", 14.0, 14.35),
    Band("17m", 18.068, 18.168),
    Band("15m", 21.0, 21.45),
    Band("12m", 24.890, 24.99),
    Band("10m", 28.0, 29.7),
    Band("8m", 40.0, 45.0),
    Band("6m", 50.0, 54.0),
    Band("5m", 54.000001, 69.9),
    Band("4m", 70.0, 71.0),
    Band("2m", 144.0, 148.0),
    Band("1.25m", 222.0, 225.0),
    Band("70cm", 420.0, 450.0),
    Band("33cm", 902.0, 928.0),
    Band("23cm", 1240.0, 1300.0),
    Band("13cm", 2300.0, 2450.0),
    Band("9cm", 3300.0, 3500.0),
    Band("6cm", 5650.0, 5925.0),
    Band("3cm", 10000.0, 10500.0),
    Band("1.25cm", 24000.0, 24250.0),
    Band("6mm", 47000.0, 47200.0),
    Band("4mm", 75500.0, 81000.0),
    Band("2.5mm", 119980.0, 123000.0),
    Band("2mm", 134000.0, 149000.0),
    Band("1mm", 241000.0, 250000.0),
)

BAND_NAMES: tuple[str, ...] = tuple(b.name for b in BANDS)

# Bands that actually see POTA activity, offered first in pickers.
COMMON_BANDS: tuple[str, ...] = (
    "160m", "80m", "60m", "40m", "30m", "20m", "17m",
    "15m", "12m", "10m", "6m", "2m", "1.25m", "70cm",
)

# Typical POTA hunting frequency per band, used to pre-fill the frequency prompt.
CALLING_FREQS: dict[str, float] = {
    "160m": 1.840, "80m": 3.560, "60m": 5.357, "40m": 7.185,
    "30m": 10.116, "20m": 14.285, "17m": 18.130, "15m": 21.285,
    "12m": 24.950, "10m": 28.420, "6m": 50.130, "2m": 146.520,
    "1.25m": 223.500, "70cm": 446.000,
}


def band_for_freq(freq_mhz: float) -> str | None:
    """Return the ADIF band name containing ``freq_mhz``, or None if out of band."""
    for band in BANDS:
        if band.low <= freq_mhz <= band.high:
            return band.name
    return None


def normalize_band(value: str) -> str | None:
    """Return the canonical ADIF spelling of a band name, or None if unknown."""
    wanted = value.strip().lower()
    if not wanted:
        return None
    if not wanted.endswith(("m", "cm", "mm")):
        wanted += "m"
    for name in BAND_NAMES:
        if name.lower() == wanted:
            return name
    return None


def parse_freq(value: str) -> float | None:
    """Parse a frequency entered as MHz or kHz.

    Operators type both ``14.285`` and ``14285`` for the same frequency. Whole
    numbers of 1000 or more are read as kHz first, everything else as MHz, and
    whichever reading lands inside an amateur band wins -- that keeps ``1296``
    (23cm, in MHz) and ``146520`` (2m, in kHz) from being mistaken for the
    microwave bands they would otherwise fall into.
    """
    text = value.strip().replace(",", "")
    if not text:
        return None
    try:
        freq = float(text)
    except ValueError:
        return None
    if freq <= 0:
        return None
    if freq >= 1000 and "." not in text:
        candidates = (freq / 1000, freq)
    else:
        candidates = (freq, freq / 1000)
    for candidate in candidates:
        if band_for_freq(candidate) is not None:
            return round(candidate, 6)
    return round(candidates[0], 6)


class Mode(NamedTuple):
    label: str
    adif_mode: str
    adif_submode: str | None
    default_rst: str


# Keyed by the label shown to the operator. ADIF requires digital modes such as
# FT8 to be reported as MODE=MFSK with a SUBMODE, which is what POTA expects.
MODES: tuple[Mode, ...] = (
    Mode("SSB", "SSB", None, "59"),
    Mode("CW", "CW", None, "599"),
    Mode("FM", "FM", None, "59"),
    Mode("AM", "AM", None, "59"),
    Mode("FT8", "MFSK", "FT8", "-10"),
    Mode("FT4", "MFSK", "FT4", "-10"),
    Mode("JS8", "MFSK", "JS8", "-10"),
    Mode("RTTY", "RTTY", None, "599"),
    Mode("PSK31", "PSK", "PSK31", "599"),
    Mode("DIGITALVOICE", "DIGITALVOICE", None, "59"),
)

MODE_LABELS: tuple[str, ...] = tuple(m.label for m in MODES)

_MODE_ALIASES: dict[str, str] = {
    "usb": "SSB", "lsb": "SSB", "phone": "SSB", "voice": "SSB",
    "data": "FT8", "digital": "FT8", "psk": "PSK31", "dv": "DIGITALVOICE",
    "dmr": "DIGITALVOICE", "dstar": "DIGITALVOICE", "c4fm": "DIGITALVOICE",
}


def normalize_mode(value: str) -> str | None:
    """Return the canonical mode label for operator input, or None if unknown."""
    wanted = value.strip().lower()
    if not wanted:
        return None
    if wanted in _MODE_ALIASES:
        return _MODE_ALIASES[wanted]
    for label in MODE_LABELS:
        if label.lower() == wanted:
            return label
    return None


def mode_info(label: str) -> Mode:
    """Return the :class:`Mode` for a canonical label."""
    for mode in MODES:
        if mode.label == label:
            return mode
    raise KeyError(f"unknown mode: {label}")


def adif_mode(label: str, freq_mhz: float | None = None) -> tuple[str, str | None]:
    """Map a mode label to an ADIF ``(MODE, SUBMODE)`` pair.

    SSB carries no submode in the table because sideband depends on frequency;
    below 10 MHz amateurs use LSB and above it USB.
    """
    mode = mode_info(label)
    if mode.adif_mode == "SSB" and freq_mhz:
        return "SSB", "LSB" if freq_mhz < 10.0 else "USB"
    return mode.adif_mode, mode.adif_submode


def default_rst(label: str) -> str:
    """Return the usual signal report for a mode, used as a prompt default."""
    try:
        return mode_info(label).default_rst
    except KeyError:
        return ""
