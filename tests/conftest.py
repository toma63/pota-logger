"""Shared fixtures, including a harness that drives the questionary prompts."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from pota_logger.config import ENV_OVERRIDES, Config
from pota_logger.db import init_db
from pota_logger.models import Activation, Qso


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Keep the developer's own POTA/QRZ environment out of the tests."""
    for names in ENV_OVERRIDES.values():
        for name in names:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("POTA_NO_QRZ", raising=False)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "pota.sqlite3"


@pytest.fixture
def session_factory(db_path):
    return init_db(db_path)


@pytest.fixture
def session(session_factory):
    db_session = session_factory()
    yield db_session
    db_session.close()


@pytest.fixture
def config(tmp_path, db_path):
    return Config(
        station_callsign="K1ABC",
        my_grid="FN42bb",
        my_state="NH",
        use_qrz=False,
        db_path=str(db_path),
        adif_dir=str(tmp_path / "adif"),
    )


@pytest.fixture
def activation(session):
    record = Activation(
        station_callsign="K1ABC",
        operator="K1ABC",
        park_ref="US-0001",
        park_name="Test State Park",
        my_grid="FN42bb",
        my_state="NH",
        my_country="United States",
        started_at=datetime(2026, 10, 18, 14, 0, 0),
    )
    session.add(record)
    session.commit()
    return record


@pytest.fixture
def make_qso(session, activation):
    """Add a QSO to the fixture activation."""
    def _make(call: str, **kwargs) -> Qso:
        fields = {
            "band": "20m",
            "mode": "SSB",
            "freq_mhz": 14.285,
            "rst_sent": "59",
            "rst_rcvd": "59",
            "qso_at": datetime(2026, 10, 18, 14, 5, 0),
        }
        fields.update(kwargs)
        qso = Qso(activation_id=activation.id, call=call, **fields)
        session.add(qso)
        session.commit()
        return qso
    return _make


@pytest.fixture
def typed():
    """Run a callable while feeding it keystrokes as if typed at the prompts.

    ``\\r`` accepts a prompt, so ``"w1aw\\r"`` answers one text question.
    """
    def _run(keystrokes: str, func: Callable[[], object]) -> object:
        with create_pipe_input() as pipe:
            pipe.send_text(keystrokes)
            with create_app_session(input=pipe, output=DummyOutput()):
                return func()
    return _run
