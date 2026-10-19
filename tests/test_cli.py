"""The non-interactive CLI commands."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from pota_logger import cli
from pota_logger.config import Config, save_config
from pota_logger.models import Activation, Qso


@pytest.fixture
def cli_env(tmp_path, db_path, session_factory):
    """A config file and database the CLI can be pointed at."""
    config_path = tmp_path / "config.toml"
    save_config(
        Config(
            station_callsign="K1ABC",
            my_grid="FN42bb",
            use_qrz=False,
            db_path=str(db_path),
            adif_dir=str(tmp_path / "adif"),
        ),
        config_path,
    )
    return {
        "config": config_path,
        "db": db_path,
        "adif_dir": tmp_path / "adif",
        "factory": session_factory,
    }


def run(cli_env, *args: str) -> int:
    return cli.main(["--config", str(cli_env["config"]), *args])


@pytest.fixture
def logged(cli_env):
    """Two activations, the newer one holding two QSOs."""
    session = cli_env["factory"]()
    older = Activation(
        station_callsign="K1ABC", operator="K1ABC", park_ref="US-0002",
        started_at=datetime(2026, 10, 1, 13, 0, 0), ended_at=datetime(2026, 10, 1, 15, 0, 0),
    )
    newer = Activation(
        station_callsign="K1ABC", operator="K1ABC", park_ref="US-0001",
        my_grid="FN42bb", started_at=datetime(2026, 10, 18, 14, 0, 0),
    )
    session.add_all([older, newer])
    session.commit()
    older.qsos.append(Qso(
        call="N0CALL", qso_at=datetime(2026, 10, 1, 13, 5, 0),
        band="20m", mode="SSB", freq_mhz=14.285, rst_sent="59", rst_rcvd="59",
    ))
    newer.qsos.append(Qso(
        call="W1AW", qso_at=datetime(2026, 10, 18, 14, 5, 0),
        band="20m", mode="SSB", submode="USB", freq_mhz=14.285,
        rst_sent="59", rst_rcvd="57", name="Hiram Maxim", state="CT",
    ))
    newer.qsos.append(Qso(
        call="VE3XYZ", qso_at=datetime(2026, 10, 18, 14, 9, 0),
        band="40m", mode="CW", freq_mhz=7.032, rst_sent="599", rst_rcvd="579",
        p2p_ref="CA-0123",
    ))
    session.commit()
    ids = {"older": older.id, "newer": newer.id, "qsos": [q.id for q in newer.qsos]}
    session.close()
    return ids


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------

def test_export_defaults_to_the_latest_activation(cli_env, logged):
    assert run(cli_env, "export") == 0
    files = list(cli_env["adif_dir"].glob("*.adi"))
    assert [f.name for f in files] == ["K1ABC@US-0001-20261018.adi"]
    text = files[0].read_text(encoding="utf-8")
    assert text.count("<EOR>") == 2
    assert "<MY_SIG_INFO:7>US-0001" in text
    assert "N0CALL" not in text


def test_export_by_activation_id(cli_env, logged):
    assert run(cli_env, "export", "-a", str(logged["older"])) == 0
    files = [f.name for f in cli_env["adif_dir"].glob("*.adi")]
    assert files == ["K1ABC@US-0002-20261001.adi"]


def test_export_all_writes_one_combined_file(cli_env, logged):
    assert run(cli_env, "export", "--all") == 0
    files = list(cli_env["adif_dir"].glob("*.adi"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert text.count("<EOR>") == 3
    # Each record carries its own park, so a combined file is still valid.
    assert "<MY_SIG_INFO:7>US-0001" in text
    assert "<MY_SIG_INFO:7>US-0002" in text


def test_export_filters_by_park(cli_env, logged):
    assert run(cli_env, "export", "--park", "us0002") == 0
    text = next(cli_env["adif_dir"].glob("*.adi")).read_text(encoding="utf-8")
    assert "N0CALL" in text
    assert "W1AW" not in text


def test_export_filters_by_date(cli_env, logged):
    assert run(cli_env, "export", "--date", "2026-10-01") == 0
    text = next(cli_env["adif_dir"].glob("*.adi")).read_text(encoding="utf-8")
    assert text.count("<EOR>") == 1
    assert "N0CALL" in text


def test_export_rejects_an_unreadable_date(cli_env, logged, capsys):
    assert run(cli_env, "export", "--date", "last tuesday") == 1
    assert "Could not read the date" in capsys.readouterr().out


def test_export_to_an_explicit_file(cli_env, logged, tmp_path):
    out = tmp_path / "upload" / "mylog.adi"
    assert run(cli_env, "export", "-o", str(out)) == 0
    assert out.is_file()
    assert out.read_text(encoding="utf-8").count("<EOR>") == 2


def test_export_to_a_directory_uses_the_pota_name(cli_env, logged, tmp_path):
    out_dir = tmp_path / "pota-upload"
    out_dir.mkdir()
    assert run(cli_env, "export", "-o", str(out_dir)) == 0
    assert [f.name for f in out_dir.glob("*.adi")] == ["K1ABC@US-0001-20261018.adi"]


def test_export_to_stdout(cli_env, logged, capsys):
    assert run(cli_env, "export", "--stdout") == 0
    out = capsys.readouterr().out
    assert "<EOH>" in out
    assert out.count("<EOR>") == 2
    assert not list(cli_env["adif_dir"].glob("*.adi"))


def test_export_reports_records_that_pota_would_reject(cli_env, logged, capsys):
    session = cli_env["factory"]()
    broken = session.get(Qso, logged["qsos"][0])
    broken.band = ""
    session.commit()
    session.close()

    assert run(cli_env, "export") == 1
    assert "missing BAND" in capsys.readouterr().out


def test_export_warns_below_the_pota_minimum(cli_env, logged, capsys):
    assert run(cli_env, "export") == 0
    assert "below the 10-QSO POTA minimum" in capsys.readouterr().out


def test_export_with_no_activations(cli_env, capsys):
    assert run(cli_env, "export") == 1
    assert "No matching activations" in capsys.readouterr().out


def test_export_with_no_qsos(cli_env, capsys):
    session = cli_env["factory"]()
    session.add(Activation(station_callsign="K1ABC", park_ref="US-0003"))
    session.commit()
    session.close()
    assert run(cli_env, "export") == 1
    assert "no QSOs" in capsys.readouterr().out


# --------------------------------------------------------------------------
# listing and editing
# --------------------------------------------------------------------------

def test_activations_listing(cli_env, logged, capsys):
    assert run(cli_env, "activations") == 0
    out = capsys.readouterr().out
    assert "US-0001" in out and "US-0002" in out
    assert "open" in out and "closed" in out


def test_activations_open_only(cli_env, logged, capsys):
    assert run(cli_env, "activations", "--open") == 0
    out = capsys.readouterr().out
    assert "US-0001" in out
    assert "US-0002" not in out


def test_activations_when_empty(cli_env, capsys):
    assert run(cli_env, "activations") == 0
    assert "No activations logged yet" in capsys.readouterr().out


def test_qsos_listing(cli_env, logged, capsys):
    assert run(cli_env, "qsos") == 0
    out = capsys.readouterr().out
    assert "W1AW" in out and "VE3XYZ" in out and "CA-0123" in out


def test_qsos_filtered_by_activation(cli_env, logged, capsys):
    assert run(cli_env, "qsos", "-a", str(logged["older"])) == 0
    out = capsys.readouterr().out
    assert "N0CALL" in out
    assert "W1AW" not in out


def test_qsos_filtered_by_call(cli_env, logged, capsys):
    assert run(cli_env, "qsos", "-c", "w1aw") == 0
    out = capsys.readouterr().out
    assert "W1AW" in out
    assert "VE3XYZ" not in out


def test_delete_qso(cli_env, logged, capsys):
    target = logged["qsos"][0]
    assert run(cli_env, "delete-qso", str(target), "--yes") == 0
    session = cli_env["factory"]()
    assert session.get(Qso, target) is None
    session.close()


def test_delete_missing_qso(cli_env, logged, capsys):
    assert run(cli_env, "delete-qso", "9999", "--yes") == 1
    assert "No QSO #9999" in capsys.readouterr().out


def test_close_activation(cli_env, logged):
    assert run(cli_env, "close") == 0
    session = cli_env["factory"]()
    assert session.get(Activation, logged["newer"]).ended_at is not None
    session.close()


def test_close_is_idempotent(cli_env, logged, capsys):
    assert run(cli_env, "close", "-a", str(logged["older"])) == 0
    assert "already closed" in capsys.readouterr().out


def test_log_rejects_an_unknown_activation(cli_env, capsys):
    assert run(cli_env, "log", "-a", "4242") == 1
    assert "No activation #4242" in capsys.readouterr().out


def test_log_creates_an_activation_logs_a_qso_and_exports(cli_env, typed):
    """The whole `pota log` command, from an empty database to an ADIF file."""
    keys = (
        "\r"                    # station callsign: accept the configured K1ABC
        "\r"                    # operator: blank, same as station
        "us-0001\r"             # park reference, typed without the hyphen case
        "Test State Park\r"     # park name
        "\r"                    # grid: accept FN42bb from the config
        "\r"                    # state: accept NH
        "\r"                    # country: blank
        "14.285\r"              # frequency
        "\r"                    # mode: SSB
        "w1aw\r"                # callsign
        "Hiram\rCT\rfn31pr\r"   # manual details (QRZ is off in this config)
        "\r57\r"                # RST sent default, 57 received
        "\r"                    # blank callsign stops the loop
        "y\r"                   # yes, export now
    )
    assert typed(keys, lambda: run(cli_env, "log")) == 0

    exported = list(cli_env["adif_dir"].glob("*.adi"))
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    assert [f.name for f in exported] == [f"K1ABC@US-0001-{today}.adi"]
    text = exported[0].read_text(encoding="utf-8")
    assert "<CALL:4>W1AW" in text
    assert "<MY_SIG_INFO:7>US-0001" in text
    assert "<RST_RCVD:2>57" in text
    assert "<NAME:5>Hiram" in text
    assert "<GRIDSQUARE:6>FN31pr" in text

    session = cli_env["factory"]()
    activation = session.scalars(select(Activation)).one()
    assert activation.park_ref == "US-0001"
    assert activation.park_name == "Test State Park"
    assert activation.my_grid == "FN42bb"
    assert activation.is_open          # not closed unless /end is used
    assert len(activation.qsos) == 1
    session.close()


# --------------------------------------------------------------------------
# lookup and config
# --------------------------------------------------------------------------

def test_lookup_without_credentials_falls_back(cli_env, capsys):
    assert run(cli_env, "lookup", "W1AW") == 1
    out = capsys.readouterr().out
    assert "W1AW" in out


def test_lookup_uses_the_cache(cli_env, capsys, monkeypatch):
    from pota_logger.lookup import CallsignResolver
    from pota_logger.qrz import CallsignInfo

    session = cli_env["factory"]()
    CallsignResolver(session, None).remember(
        "W1AW", CallsignInfo(call="W1AW", name="Hiram Maxim", state="CT", source="manual")
    )
    session.commit()
    session.close()

    assert run(cli_env, "lookup", "w1aw") == 0
    out = capsys.readouterr().out
    assert "Hiram Maxim" in out
    assert "CT" in out


def test_config_shows_effective_values_with_the_password_masked(cli_env, capsys, monkeypatch):
    monkeypatch.setenv("POTA_QRZ_PASSWORD", "secret")
    assert run(cli_env, "config") == 0
    out = capsys.readouterr().out
    assert "K1ABC" in out
    assert "***set***" in out
    assert "secret" not in out


def test_db_flag_overrides_the_config_file(cli_env, logged, tmp_path, capsys):
    other = tmp_path / "other.sqlite3"
    assert cli.main(["--config", str(cli_env["config"]), "--db", str(other), "activations"]) == 0
    assert "No activations logged yet" in capsys.readouterr().out
    assert other.is_file()


def test_bare_invocation_defaults_to_log(monkeypatch):
    called = {}

    def fake_log(args):
        called["ran"] = True
        return 0

    # main() builds the parser on each call, so it picks up the patched function.
    monkeypatch.setattr(cli, "cmd_log", fake_log)
    assert cli.main([]) == 0
    assert called == {"ran": True}


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert "pota-logger" in capsys.readouterr().out
