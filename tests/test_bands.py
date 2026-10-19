"""Band plan, frequency parsing and mode mapping."""

import pytest

from pota_logger import bands


@pytest.mark.parametrize(
    "freq,expected",
    [
        (1.840, "160m"),
        (3.560, "80m"),
        (7.185, "40m"),
        (10.116, "30m"),
        (14.285, "20m"),
        (18.130, "17m"),
        (21.285, "15m"),
        (28.420, "10m"),
        (50.130, "6m"),
        (146.520, "2m"),
        (446.000, "70cm"),
        (1296.0, "23cm"),
        (13.0, None),
        (0.0, None),
    ],
)
def test_band_for_freq(freq, expected):
    assert bands.band_for_freq(freq) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("14.285", 14.285),
        ("14285", 14.285),      # kHz
        ("7185", 7.185),        # kHz
        ("146520", 146.52),     # kHz, must not be read as the 2mm band
        ("146.52", 146.52),
        ("1296", 1296.0),       # MHz, must not be read as 1.296 MHz
        ("2400", 2400.0),       # MHz on 13cm
        ("14,285", 14.285),
        ("7", 7.0),
        ("0.1357", 0.1357),
        ("", None),
        ("junk", None),
        ("-5", None),
        ("0", None),
    ],
)
def test_parse_freq(text, expected):
    assert bands.parse_freq(text) == expected


def test_parse_freq_prefers_an_in_band_reading():
    # 18100 kHz is in the 17m band; 18100 MHz is in no band at all.
    assert bands.band_for_freq(bands.parse_freq("18100")) == "17m"


@pytest.mark.parametrize(
    "text,expected",
    [("40m", "40m"), ("40", "40m"), ("70CM", "70cm"), ("2M", "2m"), ("bogus", None), ("", None)],
)
def test_normalize_band(text, expected):
    assert bands.normalize_band(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("ssb", "SSB"), ("usb", "SSB"), ("lsb", "SSB"), ("cw", "CW"),
        ("FT8", "FT8"), ("data", "FT8"), ("dmr", "DIGITALVOICE"),
        ("psk", "PSK31"), ("nonsense", None), ("", None),
    ],
)
def test_normalize_mode(text, expected):
    assert bands.normalize_mode(text) == expected


def test_ssb_submode_follows_frequency():
    # Amateur practice: LSB below 10 MHz, USB above it.
    assert bands.adif_mode("SSB", 7.185) == ("SSB", "LSB")
    assert bands.adif_mode("SSB", 14.285) == ("SSB", "USB")
    assert bands.adif_mode("SSB", None) == ("SSB", None)


def test_digital_modes_use_adif_mfsk_with_submode():
    assert bands.adif_mode("FT8") == ("MFSK", "FT8")
    assert bands.adif_mode("FT4") == ("MFSK", "FT4")
    assert bands.adif_mode("PSK31") == ("PSK", "PSK31")
    assert bands.adif_mode("CW", 7.030) == ("CW", None)


def test_default_rst_per_mode():
    assert bands.default_rst("SSB") == "59"
    assert bands.default_rst("CW") == "599"
    assert bands.default_rst("unknown") == ""


def test_band_table_is_ordered_and_non_overlapping():
    for lower, upper in zip(bands.BANDS, bands.BANDS[1:]):
        assert lower.low < lower.high
        assert lower.high < upper.low


def test_common_bands_and_calling_freqs_agree():
    for band, freq in bands.CALLING_FREQS.items():
        assert band in bands.BAND_NAMES
        assert bands.band_for_freq(freq) == band
    assert set(bands.CALLING_FREQS) <= set(bands.COMMON_BANDS)
