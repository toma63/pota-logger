"""SQLAlchemy models for the POTA log.

All datetimes are stored as naive UTC. Amateur logging is conducted entirely in
UTC and ADIF has no concept of a local offset, so keeping UTC end to end avoids
a class of off-by-one-day export bugs around local midnight.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Current time as a naive UTC datetime, truncated to whole seconds."""
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


class Base(DeclarativeBase):
    pass


class Activation(Base):
    """One operating session: a single callsign at a single park on one day."""

    __tablename__ = "activations"

    id: Mapped[int] = mapped_column(primary_key=True)
    station_callsign: Mapped[str] = mapped_column(String(32), index=True)
    operator: Mapped[str | None] = mapped_column(String(32))
    park_ref: Mapped[str] = mapped_column(String(16), index=True)
    park_name: Mapped[str | None] = mapped_column(String(128))
    my_grid: Mapped[str | None] = mapped_column(String(10))
    my_state: Mapped[str | None] = mapped_column(String(8))
    my_country: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    notes: Mapped[str | None] = mapped_column(Text)

    qsos: Mapped[list["Qso"]] = relationship(
        back_populates="activation",
        cascade="all, delete-orphan",
        order_by="Qso.qso_at",
    )

    @property
    def is_open(self) -> bool:
        return self.ended_at is None

    @property
    def label(self) -> str:
        return (
            f"#{self.id} {self.station_callsign} @ {self.park_ref} "
            f"{self.started_at:%Y-%m-%d %H:%M}Z ({len(self.qsos)} QSOs)"
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Activation {self.id} {self.station_callsign}@{self.park_ref}>"


class Qso(Base):
    """A single logged contact."""

    __tablename__ = "qsos"

    id: Mapped[int] = mapped_column(primary_key=True)
    activation_id: Mapped[int] = mapped_column(
        ForeignKey("activations.id", ondelete="CASCADE"), index=True
    )

    call: Mapped[str] = mapped_column(String(32), index=True)
    qso_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    band: Mapped[str] = mapped_column(String(12))
    mode: Mapped[str] = mapped_column(String(16))
    submode: Mapped[str | None] = mapped_column(String(16))
    freq_mhz: Mapped[float | None] = mapped_column(Float)
    rst_sent: Mapped[str | None] = mapped_column(String(12))
    rst_rcvd: Mapped[str | None] = mapped_column(String(12))

    # Park-to-park: the reference the station being worked is activating.
    p2p_ref: Mapped[str | None] = mapped_column(String(16))

    # Worked-station details, from QRZ or the manual fallback prompts.
    name: Mapped[str | None] = mapped_column(String(128))
    qth: Mapped[str | None] = mapped_column(String(128))
    state: Mapped[str | None] = mapped_column(String(8))
    county: Mapped[str | None] = mapped_column(String(64))
    country: Mapped[str | None] = mapped_column(String(64))
    grid: Mapped[str | None] = mapped_column(String(10))
    dxcc: Mapped[str | None] = mapped_column(String(8))
    cq_zone: Mapped[str | None] = mapped_column(String(8))
    itu_zone: Mapped[str | None] = mapped_column(String(8))

    comment: Mapped[str | None] = mapped_column(Text)
    lookup_source: Mapped[str] = mapped_column(String(16), default="none")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    activation: Mapped[Activation] = relationship(back_populates="qsos")

    __table_args__ = (
        Index("ix_qsos_dupe", "activation_id", "call", "band", "mode"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Qso {self.call} {self.band} {self.mode} {self.qso_at:%Y-%m-%d %H:%MZ}>"


class CallsignCache(Base):
    """Cached QRZ lookups, so repeat callers cost no API calls and work offline."""

    __tablename__ = "callsign_cache"

    call: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(128))
    qth: Mapped[str | None] = mapped_column(String(128))
    state: Mapped[str | None] = mapped_column(String(8))
    county: Mapped[str | None] = mapped_column(String(64))
    country: Mapped[str | None] = mapped_column(String(64))
    grid: Mapped[str | None] = mapped_column(String(10))
    dxcc: Mapped[str | None] = mapped_column(String(8))
    cq_zone: Mapped[str | None] = mapped_column(String(8))
    itu_zone: Mapped[str | None] = mapped_column(String(8))
    source: Mapped[str] = mapped_column(String(16), default="qrz")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
