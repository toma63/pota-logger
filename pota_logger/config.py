"""Configuration: defaults, a TOML file, and environment overrides.

Precedence is dataclass defaults < config file < environment. QRZ credentials
are read from the environment first so they need never be written to disk.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

APP_NAME = "pota-logger"

CONFIG_DIR = Path(user_config_dir(APP_NAME, appauthor=False))
DATA_DIR = Path(user_data_dir(APP_NAME, appauthor=False))
CONFIG_PATH = CONFIG_DIR / "config.toml"
DEFAULT_DB_PATH = DATA_DIR / "pota.sqlite3"
DEFAULT_ADIF_DIR = DATA_DIR / "adif"

# Config key -> environment variables checked in order.
ENV_OVERRIDES: dict[str, tuple[str, ...]] = {
    "station_callsign": ("POTA_STATION_CALLSIGN", "POTA_CALLSIGN"),
    "operator": ("POTA_OPERATOR",),
    "my_grid": ("POTA_MY_GRID",),
    "my_state": ("POTA_MY_STATE",),
    "my_country": ("POTA_MY_COUNTRY",),
    "default_park": ("POTA_DEFAULT_PARK",),
    "qrz_username": ("POTA_QRZ_USERNAME", "QRZ_USERNAME"),
    "qrz_password": ("POTA_QRZ_PASSWORD", "QRZ_PASSWORD"),
    "db_path": ("POTA_DB",),
    "adif_dir": ("POTA_ADIF_DIR",),
}

SECRET_KEYS = frozenset({"qrz_password"})


@dataclass
class Config:
    """Everything the logger needs to know between runs."""

    station_callsign: str = ""
    operator: str = ""
    my_grid: str = ""
    my_state: str = ""
    my_country: str = ""
    default_park: str = ""
    qrz_username: str = ""
    qrz_password: str = ""
    use_qrz: bool = True
    prompt_on_lookup_failure: bool = True
    db_path: str = str(DEFAULT_DB_PATH)
    adif_dir: str = str(DEFAULT_ADIF_DIR)

    @property
    def qrz_configured(self) -> bool:
        return bool(self.use_qrz and self.qrz_username and self.qrz_password)

    def resolved_db_path(self) -> Path:
        return Path(self.db_path).expanduser()

    def resolved_adif_dir(self) -> Path:
        return Path(self.adif_dir).expanduser()

    def redacted(self) -> dict[str, object]:
        """Config as a dict with secrets masked, for display."""
        data = asdict(self)
        for key in SECRET_KEYS:
            if data.get(key):
                data[key] = "***set***"
        return data


def load_config(path: str | Path | None = None, use_env: bool = True) -> Config:
    """Load configuration from ``path`` (default: the user config file)."""
    config_path = Path(path) if path else CONFIG_PATH
    known = {f.name: f.type for f in fields(Config)}
    values: dict[str, object] = {}

    if config_path.is_file():
        with config_path.open("rb") as handle:
            raw = tomllib.load(handle)
        # Accept both a flat table and a [pota] section.
        raw = {**raw, **raw.get("pota", {})} if isinstance(raw.get("pota"), dict) else raw
        for key, value in raw.items():
            if key in known and not isinstance(value, dict):
                values[key] = value

    if use_env:
        for key, env_names in ENV_OVERRIDES.items():
            for env_name in env_names:
                env_value = os.environ.get(env_name)
                if env_value:
                    values[key] = env_value
                    break
        disable = os.environ.get("POTA_NO_QRZ", "").strip().lower()
        if disable in {"1", "true", "yes"}:
            values["use_qrz"] = False

    config = Config()
    for key, value in values.items():
        current = getattr(config, key)
        if isinstance(current, bool) and not isinstance(value, bool):
            value = str(value).strip().lower() in {"1", "true", "yes", "on"}
        setattr(config, key, type(current)(value) if not isinstance(value, type(current)) else value)
    return config


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def save_config(config: Config, path: str | Path | None = None, include_secrets: bool = True) -> Path:
    """Write ``config`` to a TOML file and return the path.

    The file is created with owner-only permissions where the platform supports
    it, because it can hold a QRZ password.
    """
    config_path = Path(path) if path else CONFIG_PATH
    config_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# pota-logger configuration",
        "# Secrets may also be supplied via POTA_QRZ_USERNAME / POTA_QRZ_PASSWORD.",
        "",
    ]
    for field_def in fields(Config):
        value = getattr(config, field_def.name)
        if field_def.name in SECRET_KEYS and not include_secrets:
            continue
        lines.append(f"{field_def.name} = {_toml_value(value)}")
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        config_path.chmod(0o600)
    except OSError:  # pragma: no cover - best effort on platforms without POSIX modes
        pass
    return config_path
