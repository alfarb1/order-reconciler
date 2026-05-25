"""Parser framework: ABC, ParseResult, and a priority-ordered registry."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..db import Email


class ParserKind(str, Enum):
    SHIPMENT = "shipment"  # outbound to KNET (Side A)
    RECEIPT = "receipt"    # KNET inbound notification (Side B)


@dataclass
class ParseResult:
    kind: ParserKind
    confidence: float  # 0.0 – 1.0
    # Shipment fields (Side A)
    retailer: str | None = None
    order_number: str | None = None
    order_date: datetime | None = None
    ship_date: datetime | None = None
    item_description: str | None = None
    sku: str | None = None
    size: str | None = None
    price: float | None = None
    currency: str | None = None
    recipient_name: str | None = None
    recipient_address: str | None = None
    # Tracking (both kinds may emit multiple)
    tracking: list[tuple[str, str]] = field(default_factory=list)
    # (carrier, normalized_number)
    # Receipt-specific
    received_at: datetime | None = None
    notes: str | None = None


class Parser(ABC):
    name: str = "base"
    kind: ParserKind = ParserKind.SHIPMENT
    priority: int = 100  # lower = higher priority

    @abstractmethod
    def matches(self, email: Email) -> bool:
        ...

    @abstractmethod
    def parse(self, email: Email) -> ParseResult | None:
        ...


class _Registry:
    def __init__(self):
        self._parsers: list[Parser] = []

    def register(self, parser: Parser) -> Parser:
        self._parsers.append(parser)
        self._parsers.sort(key=lambda p: p.priority)
        return parser

    def reset(self):
        self._parsers.clear()

    def pick(self, email: Email) -> Parser | None:
        for p in self._parsers:
            try:
                if p.matches(email):
                    return p
            except Exception:
                continue
        return None

    def all(self) -> list[Parser]:
        return list(self._parsers)


registry = _Registry()
