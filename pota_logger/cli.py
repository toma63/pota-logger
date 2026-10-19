"""Command line interface for the POTA logger."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

import questionary
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import __version__, adif
from .config import CONFIG_PATH, Config, load_config, save_config
from .db import init_db
from .lookup import CallsignResolver
from .models import Activation, Qso
from .models import utcnow
from .operate import (
    Cancelled,
    choose_activation,
    error,
    export_now,
    good,
    info,
    run_session,
    say,
    show_stats,
    warn,
)
from .qrz import QrzClient, QrzError
from .validate import is_valid_call, is_valid_grid, is_valid_park, normalize_call, normalize_grid, normalize_park


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------

def build_config(args: argparse.Namespace) -> Config:
    """Load configuration and fold in the global command line overrides."""
    config = load_config(getattr(args, "config", None))
    if getattr(args, "db", None):
        config.db_path = args.db
    if getattr(args, "no_qrz", False):
        config.use_qrz = False
    if getattr(args, "adif_dir", None):
        config.adif_dir = args.adif_dir
    return config


def build_qrz_client(config: Config, quiet: bool = False) -> QrzClient | None:
    """Create a QRZ client, or return None when lookups are unavailable."""
    if not config.use_qrz:
        if not quiet:
            info("QRZ lookups are off; callsign details will be prompted for.")
        return None
    if not config.qrz_username or not config.qrz_password:
        if not quiet:
            warn("No QRZ credentials configured; run `pota setup` or set "
                 "POTA_QRZ_USERNAME / POTA_QRZ_PASSWORD. Falling back to prompts.")
        return None
    try:
        return QrzClient(config.qrz_username, config.qrz_password)
    except QrzError as exc:  # pragma: no cover - constructor only validates inputs
        warn(f"QRZ unavailable ({exc}); falling back to prompts.")
        return None


def resolve_activation(session: Session, activation_id: int | None) -> Activation | None:
    """Look up an activation by id, or the most recently started one."""
    if activation_id is not None:
        return session.get(Activation, activation_id)
    stmt = select(Activation).order_by(Activation.started_at.desc()).limit(1)
    return session.scalars(stmt).first()


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_log(args: argparse.Namespace) -> int:
    """Start or resume an activation and log QSOs interactively."""
    config = build_config(args)
    factory = init_db(config.resolved_db_path())
    session = factory()
    try:
        if args.activation is not None:
            activation = session.get(Activation, args.activation)
            if activation is None:
                error(f"No activation #{args.activation}.")
                return 1
        else:
            activation = choose_activation(session, config)

        client = build_qrz_client(config)
        resolver = CallsignResolver(session, client)
        try:
            run_session(session, activation, resolver, config, full=args.full)
        finally:
            if client is not None:
                client.close()

        session.refresh(activation)
        show_stats(session, activation)
        if activation.qsos and questionary.confirm(
            "Export the ADIF file now?", default=True
        ).ask():
            export_now(session, activation, config)
        return 0
    except Cancelled:
        warn("\nAborted.")
        return 130
    finally:
        session.close()


def cmd_export(args: argparse.Namespace) -> int:
    """Write an ADIF file for one activation, or for a filtered set of them."""
    config = build_config(args)
    factory = init_db(config.resolved_db_path())
    session = factory()
    try:
        activations = select_activations(session, args)
        if not activations:
            error("No matching activations.")
            return 1

        pairs = [(a, q) for a in activations for q in a.qsos]
        if not pairs:
            error("Those activations have no QSOs.")
            return 1

        problems = [
            f"activation #{a.id} QSO {q.id} ({q.call}): {', '.join(issues)}"
            for a, q in pairs
            if (issues := adif.validate_qso(q, a))
        ]
        for problem in problems:
            error(f"  {problem}")

        if args.stdout:
            sys.stdout.write(adif.render_adif(pairs))
            return 1 if problems else 0

        if args.out:
            out_path = Path(args.out).expanduser()
            if out_path.is_dir():
                out_path = out_path / adif.pota_filename(activations[0])
        elif len(activations) == 1:
            out_path = config.resolved_adif_dir() / adif.pota_filename(activations[0])
        else:
            stamp = utcnow().strftime("%Y%m%d-%H%M%S")
            out_path = config.resolved_adif_dir() / f"pota-export-{stamp}.adi"

        adif.write_adif(pairs, out_path)
        good(f"Wrote {len(pairs)} QSO(s) from {len(activations)} activation(s) to {out_path}")
        for activation in activations:
            count = len(activation.qsos)
            flag = "" if count >= 10 else "  <- below the 10-QSO POTA minimum"
            say(f"  #{activation.id} {activation.station_callsign} @ {activation.park_ref}"
                f" {activation.started_at:%Y-%m-%d}: {count} QSOs{flag}")
        return 1 if problems else 0
    finally:
        session.close()


def select_activations(session: Session, args: argparse.Namespace) -> list[Activation]:
    """Apply the export command's selection flags."""
    if args.activation is not None:
        activation = session.get(Activation, args.activation)
        return [activation] if activation else []

    stmt = select(Activation).order_by(Activation.started_at)
    if args.park:
        stmt = stmt.where(Activation.park_ref == normalize_park(args.park))
    if args.date:
        day = parse_date(args.date)
        if day is None:
            error(f"Could not read the date '{args.date}'; expected YYYY-MM-DD.")
            return []
        stmt = stmt.where(
            Activation.started_at >= day, Activation.started_at < day + timedelta(days=1)
        )
    activations = list(session.scalars(stmt))
    if args.all or args.park or args.date:
        return activations
    return activations[-1:]


def parse_date(text: str) -> datetime | None:
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
    return None


def cmd_activations(args: argparse.Namespace) -> int:
    """List activations."""
    config = build_config(args)
    factory = init_db(config.resolved_db_path())
    session = factory()
    try:
        stmt = select(Activation).order_by(Activation.started_at.desc())
        if args.open:
            stmt = stmt.where(Activation.ended_at.is_(None))
        activations = list(session.scalars(stmt.limit(args.limit)))
        if not activations:
            info("No activations logged yet. Run `pota log` to start one.")
            return 0
        say(f"{'ID':>4}  {'CALL':<10} {'PARK':<10} {'STARTED (UTC)':<17} {'QSOS':>4}  STATUS")
        for activation in activations:
            status = "open" if activation.is_open else "closed"
            say(f"{activation.id:>4}  {activation.station_callsign:<10} "
                f"{activation.park_ref:<10} {activation.started_at:%Y-%m-%d %H:%M}  "
                f"{len(activation.qsos):>4}  {status}")
        return 0
    finally:
        session.close()


def cmd_qsos(args: argparse.Namespace) -> int:
    """List logged QSOs."""
    config = build_config(args)
    factory = init_db(config.resolved_db_path())
    session = factory()
    try:
        stmt = select(Qso).order_by(Qso.qso_at.desc()).limit(args.limit)
        if args.activation is not None:
            stmt = stmt.where(Qso.activation_id == args.activation)
        if args.call:
            stmt = stmt.where(Qso.call == normalize_call(args.call))
        qsos = list(session.scalars(stmt))
        if not qsos:
            info("No matching QSOs.")
            return 0
        say(f"{'ID':>5}  {'ACT':>4}  {'DATE':<10} {'UTC':<8} {'CALL':<10} "
            f"{'BAND':>6} {'MODE':<6} {'S':>4}/{'R':<4} P2P")
        for qso in reversed(qsos):
            say(f"{qso.id:>5}  {qso.activation_id:>4}  {qso.qso_at:%Y-%m-%d} "
                f"{qso.qso_at:%H:%M:%S} {qso.call:<10} {qso.band:>6} {qso.mode:<6} "
                f"{qso.rst_sent or '':>4}/{qso.rst_rcvd or '':<4} {qso.p2p_ref or ''}")
        return 0
    finally:
        session.close()


def cmd_delete_qso(args: argparse.Namespace) -> int:
    """Delete a single QSO by id."""
    config = build_config(args)
    factory = init_db(config.resolved_db_path())
    session = factory()
    try:
        qso = session.get(Qso, args.qso_id)
        if qso is None:
            error(f"No QSO #{args.qso_id}.")
            return 1
        label = f"#{qso.id} {qso.call} {qso.band} {qso.mode} {qso.qso_at:%Y-%m-%d %H:%M:%S}Z"
        if not args.yes and not questionary.confirm(f"Delete {label}?", default=False).ask():
            info("Left alone.")
            return 0
        session.delete(qso)
        session.commit()
        good(f"Deleted {label}.")
        return 0
    finally:
        session.close()


def cmd_close(args: argparse.Namespace) -> int:
    """Mark an activation as finished."""
    config = build_config(args)
    factory = init_db(config.resolved_db_path())
    session = factory()
    try:
        activation = resolve_activation(session, args.activation)
        if activation is None:
            error("No such activation.")
            return 1
        if not activation.is_open:
            info(f"Activation #{activation.id} is already closed.")
            return 0
        activation.ended_at = utcnow()
        session.commit()
        good(f"Closed activation #{activation.id} ({activation.station_callsign} "
             f"@ {activation.park_ref}, {len(activation.qsos)} QSOs).")
        return 0
    finally:
        session.close()


def cmd_lookup(args: argparse.Namespace) -> int:
    """Look up a callsign, to check credentials or inspect a record."""
    config = build_config(args)
    factory = init_db(config.resolved_db_path())
    session = factory()
    client = build_qrz_client(config)
    try:
        resolver = CallsignResolver(session, client, use_cache=not args.no_cache)
        result = resolver.resolve(args.call)
        session.commit()
        if not result.found:
            error(f"{result.call}: {result.detail or result.source}")
            return 1
        station = result.info
        good(f"{result.call} (via {result.source})")
        for label, value in (
            ("name", station.name), ("qth", station.qth), ("state", station.state),
            ("county", station.county), ("country", station.country),
            ("grid", station.grid), ("dxcc", station.dxcc),
            ("cq zone", station.cq_zone), ("itu zone", station.itu_zone),
            ("class", station.license_class),
        ):
            if value:
                say(f"  {label:<9} {value}")
        return 0
    finally:
        if client is not None:
            client.close()
        session.close()


def cmd_setup(args: argparse.Namespace) -> int:
    """Interactively write the configuration file."""
    config_path = Path(args.config) if args.config else CONFIG_PATH
    config = load_config(config_path, use_env=False)
    try:
        config.station_callsign = normalize_call(questionary.text(
            "Your station callsign:",
            default=config.station_callsign,
            validate=lambda v: True if not v.strip() or is_valid_call(v) else "Enter a valid callsign",
        ).unsafe_ask())
        config.operator = normalize_call(questionary.text(
            "Operator callsign (blank if same):", default=config.operator
        ).unsafe_ask())
        config.my_grid = normalize_grid(questionary.text(
            "Your usual grid square:",
            default=config.my_grid,
            validate=lambda v: True if not v.strip() or is_valid_grid(v) else "Expected a locator like FN42bb",
        ).unsafe_ask())
        config.my_state = questionary.text(
            "Your state/province:", default=config.my_state
        ).unsafe_ask().strip().upper()
        config.my_country = questionary.text(
            "Your country:", default=config.my_country
        ).unsafe_ask().strip()
        config.default_park = normalize_park(questionary.text(
            "Default park reference (optional):",
            default=config.default_park,
            validate=lambda v: True if not v.strip() or is_valid_park(v) else "Expected a reference like US-0001",
        ).unsafe_ask())

        config.use_qrz = questionary.confirm(
            "Look up callsigns on QRZ.com?", default=config.use_qrz
        ).unsafe_ask()
        store_password = False
        if config.use_qrz:
            config.qrz_username = questionary.text(
                "QRZ username:", default=config.qrz_username
            ).unsafe_ask().strip()
            password = questionary.password(
                "QRZ password (blank to keep using the environment variable):"
            ).unsafe_ask()
            if password:
                store_password = questionary.confirm(
                    "Save the password in the config file? "
                    "(No = read it from POTA_QRZ_PASSWORD each run)",
                    default=False,
                ).unsafe_ask()
                config.qrz_password = password
            config.prompt_on_lookup_failure = questionary.confirm(
                "Prompt for name/state/grid when a lookup fails?",
                default=config.prompt_on_lookup_failure,
            ).unsafe_ask()

        config.db_path = questionary.text(
            "Database file:", default=config.db_path
        ).unsafe_ask().strip() or config.db_path
        config.adif_dir = questionary.text(
            "ADIF export directory:", default=config.adif_dir
        ).unsafe_ask().strip() or config.adif_dir
    except KeyboardInterrupt:
        warn("\nSetup aborted; nothing written.")
        return 130

    written = save_config(config, config_path, include_secrets=store_password)
    good(f"Wrote {written}")
    if config.use_qrz and config.qrz_password and not store_password:
        info("Password not saved. Set POTA_QRZ_PASSWORD in your environment before logging.")
    init_db(config.resolved_db_path())
    info(f"Database ready at {config.resolved_db_path()}")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    """Show the effective configuration."""
    config = build_config(args)
    config_path = Path(args.config) if args.config else CONFIG_PATH
    say(f"config file: {config_path}{'' if config_path.is_file() else '  (not created yet)'}")
    for key, value in config.redacted().items():
        say(f"  {key:<26} {value}")
    return 0


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pota",
        description="Interactive Parks On The Air activation logger with "
                    "QRZ.com lookups and ADIF export.",
    )
    parser.add_argument("--version", action="version", version=f"pota-logger {__version__}")
    parser.add_argument("--config", help="path to an alternate config.toml")
    parser.add_argument("--db", help="path to an alternate SQLite database")
    parser.add_argument("--adif-dir", help="directory for ADIF exports")
    parser.add_argument("--no-qrz", action="store_true",
                        help="skip QRZ lookups and prompt for callsign details")

    subparsers = parser.add_subparsers(dest="command")

    log_parser = subparsers.add_parser("log", help="log QSOs interactively (default)")
    log_parser.add_argument("-a", "--activation", type=int,
                            help="resume this activation id without prompting")
    log_parser.add_argument("--full", action="store_true",
                            help="also prompt for park-to-park and a comment on every QSO")
    log_parser.set_defaults(func=cmd_log)

    export_parser = subparsers.add_parser("export", help="write an ADIF file for POTA upload")
    export_parser.add_argument("-a", "--activation", type=int, help="activation id to export")
    export_parser.add_argument("--all", action="store_true", help="export every activation")
    export_parser.add_argument("--park", help="export activations at this park reference")
    export_parser.add_argument("--date", help="export activations started on this date")
    export_parser.add_argument("-o", "--out", help="output file or directory")
    export_parser.add_argument("--stdout", action="store_true", help="write ADIF to stdout")
    export_parser.set_defaults(func=cmd_export)

    activations_parser = subparsers.add_parser("activations", help="list activations")
    activations_parser.add_argument("--open", action="store_true", help="only open activations")
    activations_parser.add_argument("-n", "--limit", type=int, default=25)
    activations_parser.set_defaults(func=cmd_activations)

    qsos_parser = subparsers.add_parser("qsos", help="list logged QSOs")
    qsos_parser.add_argument("-a", "--activation", type=int, help="limit to one activation")
    qsos_parser.add_argument("-c", "--call", help="limit to one callsign")
    qsos_parser.add_argument("-n", "--limit", type=int, default=25)
    qsos_parser.set_defaults(func=cmd_qsos)

    delete_parser = subparsers.add_parser("delete-qso", help="delete a QSO by id")
    delete_parser.add_argument("qso_id", type=int)
    delete_parser.add_argument("-y", "--yes", action="store_true", help="do not ask first")
    delete_parser.set_defaults(func=cmd_delete_qso)

    close_parser = subparsers.add_parser("close", help="close an activation")
    close_parser.add_argument("-a", "--activation", type=int,
                              help="activation id (default: most recent)")
    close_parser.set_defaults(func=cmd_close)

    lookup_parser = subparsers.add_parser("lookup", help="look up a callsign on QRZ")
    lookup_parser.add_argument("call")
    lookup_parser.add_argument("--no-cache", action="store_true",
                               help="ignore the local cache and ask QRZ")
    lookup_parser.set_defaults(func=cmd_lookup)

    setup_parser = subparsers.add_parser("setup", help="write the configuration file")
    setup_parser.set_defaults(func=cmd_setup)

    config_parser = subparsers.add_parser("config", help="show the effective configuration")
    config_parser.set_defaults(func=cmd_config)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        # Bare `pota` goes straight to logging, which is what it is for.
        args = parser.parse_args((argv or []) + ["log"])
    try:
        return args.func(args)
    except KeyboardInterrupt:
        warn("\nInterrupted.")
        return 130
    except Cancelled:
        warn("\nAborted.")
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
