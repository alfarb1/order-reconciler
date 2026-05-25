"""Shared base for named-retailer parsers.

Each named parser pins:
  - a `retailer` name (used in reports),
  - a tuple of `domains` it matches against `email.from_domain`,
  - optionally, `subject_hints` to tighten the matches() check,
  - a confidence floor (named parsers are more reliable than the heuristic).

They reuse the generic body-parsing logic so we don't reinvent it per retailer
until real samples justify a custom path.
"""
from __future__ import annotations

from ..db import Email
from .base import ParserKind, ParseResult
from .generic import GenericParser, address_matches, _text_from_email


class NamedRetailerParser(GenericParser):
    retailer: str = "unknown"
    domains: tuple[str, ...] = ()
    subject_hints: tuple[str, ...] = ()
    confidence_floor: float = 0.85

    name = "named"
    kind = ParserKind.SHIPMENT
    priority = 100  # ahead of GenericParser (1000)

    def matches(self, email: Email) -> bool:
        dom = (email.from_domain or "").lower()
        if not any(dom == d or dom.endswith("." + d) for d in self.domains):
            return False
        if self.subject_hints:
            subj = (email.subject or "").lower()
            if not any(h in subj for h in self.subject_hints):
                return False
        text = _text_from_email(email)
        return address_matches(text, self._address_lines)

    def parse(self, email: Email) -> ParseResult | None:
        result = super().parse(email)
        if result is None:
            return None
        result.retailer = self.retailer
        result.confidence = max(result.confidence, self.confidence_floor)
        return result
