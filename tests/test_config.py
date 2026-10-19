"""Configuration loading, precedence and saving."""

from __future__ import annotations

from pota_logger.config import Config, load_config, save_config


def test_defaults_when_no_file_exists(tmp_path):
    config = load_config(tmp_path / "missing.toml")
    assert config.station_callsign == ""
    assert config.use_qrz is True
    assert config.db_path.endswith("pota.sqlite3")


def test_round_trip(tmp_path):
    original = Config(
        station_callsign="K1ABC",
        my_grid="FN42bb",
        default_park="US-0001",
        qrz_username="user",
        qrz_password="secret",
        prompt_on_lookup_failure=False,
    )
    path = save_config(original, tmp_path / "config.toml")
    loaded = load_config(path, use_env=False)
    assert loaded.station_callsign == "K1ABC"
    assert loaded.my_grid == "FN42bb"
    assert loaded.default_park == "US-0001"
    assert loaded.qrz_username == "user"
    assert loaded.qrz_password == "secret"
    assert loaded.prompt_on_lookup_failure is False


def test_secrets_can_be_left_out_of_the_file(tmp_path):
    config = Config(qrz_username="user", qrz_password="secret")
    path = save_config(config, tmp_path / "config.toml", include_secrets=False)
    assert "secret" not in path.read_text(encoding="utf-8")
    assert load_config(path, use_env=False).qrz_password == ""


def test_environment_overrides_the_file(tmp_path, monkeypatch):
    path = save_config(Config(station_callsign="K1ABC", my_state="NH"), tmp_path / "c.toml")
    monkeypatch.setenv("POTA_STATION_CALLSIGN", "K9XYZ")
    monkeypatch.setenv("POTA_MY_STATE", "VT")
    config = load_config(path)
    assert config.station_callsign == "K9XYZ"
    assert config.my_state == "VT"


def test_qrz_credentials_can_come_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("QRZ_USERNAME", "envuser")
    monkeypatch.setenv("QRZ_PASSWORD", "envpass")
    config = load_config(tmp_path / "missing.toml")
    assert config.qrz_username == "envuser"
    assert config.qrz_password == "envpass"
    assert config.qrz_configured


def test_pota_prefixed_qrz_variables_win(tmp_path, monkeypatch):
    monkeypatch.setenv("QRZ_USERNAME", "generic")
    monkeypatch.setenv("POTA_QRZ_USERNAME", "specific")
    assert load_config(tmp_path / "missing.toml").qrz_username == "specific"


def test_no_qrz_environment_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("POTA_NO_QRZ", "1")
    config = load_config(tmp_path / "missing.toml")
    assert config.use_qrz is False
    assert not config.qrz_configured


def test_qrz_configured_needs_both_halves():
    assert not Config(qrz_username="u").qrz_configured
    assert not Config(qrz_password="p").qrz_configured
    assert Config(qrz_username="u", qrz_password="p").qrz_configured
    assert not Config(qrz_username="u", qrz_password="p", use_qrz=False).qrz_configured


def test_booleans_survive_string_values_in_the_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('use_qrz = "no"\nprompt_on_lookup_failure = "yes"\n', encoding="utf-8")
    config = load_config(path, use_env=False)
    assert config.use_qrz is False
    assert config.prompt_on_lookup_failure is True


def test_a_pota_section_is_accepted(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[pota]\nstation_callsign = "K1ABC"\n', encoding="utf-8")
    assert load_config(path, use_env=False).station_callsign == "K1ABC"


def test_unknown_keys_are_ignored(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('station_callsign = "K1ABC"\nnonsense = 42\n', encoding="utf-8")
    assert load_config(path, use_env=False).station_callsign == "K1ABC"


def test_windows_paths_survive_the_toml_round_trip(tmp_path):
    config = Config(db_path=r"C:\Users\ham\pota.sqlite3")
    path = save_config(config, tmp_path / "config.toml")
    assert load_config(path, use_env=False).db_path == r"C:\Users\ham\pota.sqlite3"


def test_redacted_masks_the_password():
    data = Config(qrz_username="user", qrz_password="secret").redacted()
    assert data["qrz_password"] == "***set***"
    assert data["qrz_username"] == "user"
