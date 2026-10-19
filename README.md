# pota-logger

An interactive **Parks On The Air** logger for the command line. It prompts for
each QSO with [questionary](https://github.com/tmbo/questionary), stores the log
in a local SQLite database through SQLAlchemy, looks callsigns up on QRZ.com,
and exports an ADIF file ready to upload to the POTA site.

```
? [20m SSB 14.285] Call: w1aw
  QRZ: Hiram Percy Maxim, Newington, CT, United States, FN31pr
?   RST sent: [59]
?   RST rcvd: [59] 57
  #1   W1AW 20m SSB 14:03:12Z logged.
? [20m SSB 14.285] Call:
```

## Install

Python 3.11 or newer. A virtual environment keeps it away from your other
projects:

```sh
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux
pip install -e .
```

That installs the `pota` command. Without installing, `python -m pota_logger`
works the same way from the project directory.

## Set up

```sh
pota setup
```

This asks for your callsign, grid square, usual park and QRZ credentials, then
writes a config file and creates the database. Run it once; everything it asks
can be overridden later per activation.

QRZ lookups need a QRZ.com account, and the full record (grid, county, zones)
needs an XML subscription. The password can be stored in the config file or
left in the environment instead:

```sh
set POTA_QRZ_USERNAME=K1ABC          # Windows
set POTA_QRZ_PASSWORD=secret
export POTA_QRZ_USERNAME=K1ABC       # macOS/Linux
export POTA_QRZ_PASSWORD=secret
```

Check it works before you drive out to the park:

```sh
pota lookup w1aw
```

## Log an activation

```sh
pota log
```

It offers to resume any open activation or start a new one, asks for the park
reference, frequency and mode, and then loops on a single callsign prompt. Each
contact is written to the database the moment you enter it, so a dead battery
costs you nothing.

Per QSO you type the callsign and the two signal reports — three keystroke
groups. The frequency, band and mode stay as you set them until you change
them. When a lookup succeeds, the name, QTH, state, county, grid, DXCC and
zones are filled in from QRZ. When it fails — unlicensed-looking call, no
subscription, no mobile signal — you are prompted for name, state and grid
instead, and what you type is cached so the same station is not asked about
twice.

### Commands at the callsign prompt

| Command | Effect |
| --- | --- |
| `/f 14.285` | set frequency (MHz or kHz); the band follows automatically |
| `/b 20m` | set the band directly |
| `/m cw` | set mode: SSB CW FM AM FT8 FT4 JS8 RTTY PSK31 DIGITALVOICE |
| `/p US-0001` | park-to-park reference, applied to the next QSO only |
| `/t 1432` | log the next QSO at this UTC time (HHMM or HHMMSS) |
| `/undo` | delete the most recent QSO |
| `/list` | show the last 10 QSOs |
| `/stats` | QSO count, band and mode breakdown, park-to-park total |
| `/export` | write the ADIF file now and carry on logging |
| `/end` | close the activation and exit |
| `/help` | the same table, in the terminal |
| *blank* or `q` | stop logging, leaving the activation open to resume |

`pota log --full` also prompts for a park-to-park reference and a comment on
every contact, rather than only when you ask for them.

Duplicates are caught: working the same station again on the same band and mode
within one activation asks for confirmation first.

## Export for POTA

```sh
pota export                      # the most recent activation
pota export -a 3                 # a particular activation
pota export --park US-0001       # every activation at one park
pota export --date 2026-10-18    # everything started on one day
pota export --all -o mylog.adi   # everything, into one file
pota export --stdout             # to the terminal, for inspection
```

Files are named the way the POTA upload page expects —
`K1ABC@US-0001-20261018.adi` — and land in the ADIF directory from your config
unless `-o` says otherwise.

Each record carries the fields POTA requires, and `pota export` exits non-zero
and names any record that is missing one:

| Field | Source |
| --- | --- |
| `STATION_CALLSIGN` | the activation's callsign |
| `CALL` | the station worked |
| `QSO_DATE`, `TIME_ON` | the moment the callsign was entered, in UTC |
| `BAND`, `MODE` | ADIF band and mode enumerations |
| `MY_SIG`, `MY_SIG_INFO` | `POTA` and the park you activated |
| `SIG`, `SIG_INFO` | `POTA` and the other operator's park, for park-to-park |

`OPERATOR`, `SUBMODE`, `FREQ`, `RST_SENT`, `RST_RCVD`, `MY_GRIDSQUARE`,
`MY_STATE`, `MY_COUNTRY`, `NAME`, `QTH`, `STATE`, `CNTY`, `COUNTRY`,
`GRIDSQUARE`, `DXCC`, `CQZ`, `ITUZ` and `COMMENT` are written when known. POTA
ignores the extras; LoTW and Club Log use them.

Digital modes follow ADIF rather than common shorthand: FT8 is exported as
`MODE=MFSK` with `SUBMODE=FT8`. SSB gets `SUBMODE=LSB` below 10 MHz and `USB`
above it.

## Review the log

```sh
pota activations          # every activation, newest first
pota activations --open   # only ones still open
pota qsos                 # recent contacts
pota qsos -a 3            # one activation
pota qsos -c w1aw         # one callsign
pota delete-qso 42        # remove a contact
pota close                # mark the latest activation finished
pota config               # show the effective settings
```

## Configuration

`pota config` prints the active settings and where they came from. Precedence is
defaults, then the config file, then the environment.

| Setting | Environment variable | Meaning |
| --- | --- | --- |
| `station_callsign` | `POTA_STATION_CALLSIGN` | your callsign |
| `operator` | `POTA_OPERATOR` | operator, if not the station callsign |
| `my_grid` | `POTA_MY_GRID` | your grid square |
| `my_state`, `my_country` | `POTA_MY_STATE`, `POTA_MY_COUNTRY` | your location |
| `default_park` | `POTA_DEFAULT_PARK` | pre-filled park reference |
| `qrz_username`, `qrz_password` | `POTA_QRZ_USERNAME`/`QRZ_USERNAME`, `POTA_QRZ_PASSWORD`/`QRZ_PASSWORD` | QRZ credentials |
| `use_qrz` | `POTA_NO_QRZ=1` to disable | whether to query QRZ at all |
| `prompt_on_lookup_failure` | — | prompt for details when a lookup fails |
| `db_path` | `POTA_DB` | SQLite file |
| `adif_dir` | `POTA_ADIF_DIR` | where exports land |

Global flags `--config`, `--db`, `--adif-dir` and `--no-qrz` override all of it
for one run, which is handy for a separate club-station log:

```sh
pota --db club.sqlite3 log
```

## Notes

- Everything is stored and exported in UTC, as amateur logs and ADIF require.
- Frequencies are accepted as MHz or kHz: `14.285`, `14285` and `146520` all do
  the right thing. The band is derived from the ADIF band plan.
- The callsign cache means a repeat hunter costs no API call, and the logger
  keeps working with no network at all.
- Portable calls are logged as sent (`W1AW/P`) but looked up by their base call.
- Interactive prompting needs a real console. On Windows use PowerShell, cmd or
  Windows Terminal; under mintty/git-bash, prefix with `winpty`.

## Tests

```sh
pip install -e .[dev]
pytest
```

The suite covers the band plan, ADIF rendering, the QRZ client against stubbed
HTTP responses, the config precedence rules and the CLI. The interactive flow is
tested for real by feeding keystrokes through a prompt_toolkit pipe, so the
prompts, the `/` commands and the manual fallback are all exercised end to end.

## License

Copyright (C) 2026 Tom Arns

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version. See [LICENSE](LICENSE) for the full text.
