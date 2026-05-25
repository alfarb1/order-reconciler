"""SQLAlchemy models and session factory. Schema mirrors SPEC §5."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterator

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    sessionmaker,
    Session,
)


class Base(DeclarativeBase):
    pass


class Email(Base):
    __tablename__ = "emails"

    gmail_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    thread_id: Mapped[str | None] = mapped_column(String(64), index=True)
    from_address: Mapped[str | None] = mapped_column(String(320), index=True)
    from_domain: Mapped[str | None] = mapped_column(String(255), index=True)
    subject: Mapped[str | None] = mapped_column(String(998))
    received_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    snippet: Mapped[str | None] = mapped_column(Text)
    raw_html: Mapped[str | None] = mapped_column(Text)
    raw_text: Mapped[str | None] = mapped_column(Text)
    parsed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    parser_used: Mapped[str | None] = mapped_column(String(64))
    parse_error: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    shipments: Mapped[list["Shipment"]] = relationship(back_populates="email", cascade="all, delete-orphan")
    receipts: Mapped[list["Receipt"]] = relationship(back_populates="email", cascade="all, delete-orphan")


class Shipment(Base):
    __tablename__ = "shipments"
    __table_args__ = (
        UniqueConstraint("email_id", "tracking_number_normalized", name="uq_shipment_email_tracking"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email_id: Mapped[str] = mapped_column(ForeignKey("emails.gmail_id", ondelete="CASCADE"), index=True)
    retailer: Mapped[str | None] = mapped_column(String(128), index=True)
    order_number: Mapped[str | None] = mapped_column(String(64), index=True)
    order_date: Mapped[datetime | None] = mapped_column(DateTime)
    ship_date: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    tracking_number: Mapped[str | None] = mapped_column(String(64))
    tracking_number_normalized: Mapped[str | None] = mapped_column(String(64), index=True)
    carrier: Mapped[str | None] = mapped_column(String(32))
    recipient_name: Mapped[str | None] = mapped_column(String(255))
    recipient_address: Mapped[str | None] = mapped_column(Text)
    item_description: Mapped[str | None] = mapped_column(Text)
    sku: Mapped[str | None] = mapped_column(String(128), index=True)
    size: Mapped[str | None] = mapped_column(String(16))
    price: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(8))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)

    email: Mapped[Email] = relationship(back_populates="shipments")
    match: Mapped["Match | None"] = relationship(back_populates="shipment", uselist=False)


class Receipt(Base):
    __tablename__ = "receipts"
    __table_args__ = (
        UniqueConstraint("email_id", "tracking_number_normalized", name="uq_receipt_email_tracking"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email_id: Mapped[str] = mapped_column(ForeignKey("emails.gmail_id", ondelete="CASCADE"), index=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    tracking_number: Mapped[str | None] = mapped_column(String(64))
    tracking_number_normalized: Mapped[str | None] = mapped_column(String(64), index=True)
    carrier: Mapped[str | None] = mapped_column(String(32))
    item_description: Mapped[str | None] = mapped_column(Text)
    sku: Mapped[str | None] = mapped_column(String(128))
    notes: Mapped[str | None] = mapped_column(Text)

    email: Mapped[Email] = relationship(back_populates="receipts")


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id", ondelete="CASCADE"), unique=True)
    receipt_id: Mapped[int | None] = mapped_column(ForeignKey("receipts.id", ondelete="SET NULL"))
    match_type: Mapped[str] = mapped_column(String(32), index=True)
    flagged_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    shipment: Mapped[Shipment] = relationship(back_populates="match")
    receipt: Mapped[Receipt | None] = relationship()


MATCH_TYPES = ("tracking_exact", "order_number", "sku_date_window", "manual", "none")


def build_engine(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{db_path}", future=True)


def init_db(db_path: Path):
    """Create tables if they don't exist. Idempotent."""
    engine = build_engine(db_path)
    Base.metadata.create_all(engine)
    return engine


def make_session_factory(db_path: Path) -> sessionmaker[Session]:
    return sessionmaker(bind=init_db(db_path), expire_on_commit=False, future=True)


def iter_unparsed_emails(session: Session) -> Iterator[Email]:
    yield from session.query(Email).filter(Email.parsed.is_(False)).all()
