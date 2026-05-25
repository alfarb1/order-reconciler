"""KNET 'received / arrived / checked in' email parser.

TODO(real-samples): once we have 2–3 actual KNET emails, tighten the subject
and body signals here and add fixture-based tests. For now we operate on the
configurable heuristics described in SPEC §6.4.
"""
from __future__ import annotations

import re
from datetime import datetime

import dateparser
from bs4 import BeautifulSoup

from ..db import Email
from ..tracking import extract_tracking
from .base import Parser, ParseResult, ParserKind

SUBJECT_HINTS = (
    "received",
    "arrived",
    "checked in",
    "check-in",
    "inventory",
    "delivered to warehouse",
    "package arrived",
)

# "Received on Jan 5, 2026" / "Received: 2026-01-05" / "on 1/5/2026"
RECEIVED_AT_RE = re.compile(
    r"(?:received(?:\s+on)?|arrived(?:\s+on)?|checked\s+in(?:\s+on)?)[:\s]*"
    r"([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}|\d{1,4}[/\-]\d{1,2}[/\-]\d{1,4})",
    re.IGNORECASE,
)

SKU_RE = re.compile(r"\b(?:sku|style)[:\s#]*([A-Z0-9][A-Z0-9\-]{3,20})\b", re.IGNORECASE)


def _text(email: Email) -> str:
    if email.raw_text:
        return email.raw_text
    if email.raw_html:
        try:
            return BeautifulSoup(email.raw_html, "lxml").get_text(separator="\n")
        except Exception:
            return re.sub(r"<[^>]+>", " ", email.raw_html)
    return email.snippet or ""


class KnetParser(Parser):
    name = "knet"
    kind = ParserKind.RECEIPT
    priority = 0  # always try KNET first when sender matches

    def __init__(self, sender_domain: str = "knetgroup.com"):
        self._sender = sender_domain.lower()

    def matches(self, email: Email) -> bool:
        if not email.from_domain:
            return False
        if not email.from_domain.lower().endswith(self._sender):
            return False
        subject = (email.subject or "").lower()
        if any(h in subject for h in SUBJECT_HINTS):
            return True
        # Fall through — even if subject doesn't match, body might be a receipt.
        # We still return True so the parser sees it; parse() decides finally.
        return True

    def parse(self, email: Email) -> ParseResult | None:
        text = _text(email)
        if not text:
            return None

        tracking = extract_tracking(text + " " + (email.raw_html or ""))
        if not tracking:
            return None  # no tracking → can't reconcile

        m = RECEIVED_AT_RE.search(text)
        received_at: datetime | None = None
        if m:
            received_at = dateparser.parse(m.group(1))
        if not received_at:
            received_at = email.received_at

        sku_match = SKU_RE.search(text)
        sku = sku_match.group(1) if sku_match else None

        confidence = 0.85
        if sku:
            confidence = 0.9

        return ParseResult(
            kind=ParserKind.RECEIPT,
            confidence=confidence,
            received_at=received_at,
            sku=sku,
            tracking=[(t.carrier, t.number) for t in tracking],
        )
