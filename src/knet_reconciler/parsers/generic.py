"""Generic heuristic parser. Handles long-tail Shopify shops and unknown retailers.

Workhorse: most retailers fall here. It only emits a ParseResult if the email
body contains at least one of the configured KNET warehouse address lines —
otherwise the email isn't shipped to KNET and we drop it.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime

import dateparser
from bs4 import BeautifulSoup

from ..db import Email
from ..tracking import extract_tracking
from .base import Parser, ParseResult, ParserKind

log = logging.getLogger(__name__)

# Reasonable confidence band per the spec (§6.2): generic returns 0.3–0.7.
_BASE_CONFIDENCE = 0.4
_CONFIDENCE_PER_FIELD = 0.05  # bumped for each non-trivial field we pulled

ORDER_NUMBER_RE = re.compile(
    r"(?:order(?:\s*(?:number|no\.?|#))?|confirmation(?:\s*(?:number|no\.?|#))?)"
    r"[:\s#]*([A-Z0-9][A-Z0-9\-]{3,19})",
    re.IGNORECASE,
)
SIZE_RE = re.compile(r"(?:^|\s)(?:size|US|EU)[:\s]*((?:\d{1,2})(?:\.\d)?(?:[WMY])?)\b", re.IGNORECASE)
PRICE_RE = re.compile(r"([\$£€])\s*(\d{1,4}(?:[,.]\d{2})?)")
SKU_RE = re.compile(r"\b(?:sku|style)[:\s#]*([A-Z0-9][A-Z0-9\-]{3,20})\b", re.IGNORECASE)


def _text_from_email(email: Email) -> str:
    if email.raw_text:
        return email.raw_text
    if email.raw_html:
        try:
            return BeautifulSoup(email.raw_html, "lxml").get_text(separator="\n")
        except Exception:
            return re.sub(r"<[^>]+>", " ", email.raw_html)
    return email.snippet or ""


def _normalize_for_match(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def address_matches(text: str, address_lines: list[str]) -> bool:
    """True if ANY configured address line appears in `text`, ignoring whitespace and case."""
    if not address_lines:
        return False
    haystack = _normalize_for_match(text)
    for line in address_lines:
        needle = _normalize_for_match(line)
        if needle and needle in haystack:
            return True
    return False


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return dateparser.parse(s)
    except Exception:
        return None


def _first_image_alt(html: str | None) -> str | None:
    if not html:
        return None
    try:
        soup = BeautifulSoup(html, "lxml")
        for img in soup.find_all("img"):
            alt = (img.get("alt") or "").strip()
            if alt and len(alt) > 4 and "logo" not in alt.lower():
                return alt
    except Exception:
        return None
    return None


def _first_heading(html: str | None) -> str | None:
    if not html:
        return None
    try:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup.find_all(["h1", "h2", "h3", "strong"]):
            txt = tag.get_text(strip=True)
            if txt and 4 < len(txt) < 200:
                return txt
    except Exception:
        return None
    return None


class GenericParser(Parser):
    name = "generic"
    kind = ParserKind.SHIPMENT
    priority = 1000  # last resort — runs after named retailer parsers

    def __init__(self, warehouse_address_lines: list[str]):
        self._address_lines = warehouse_address_lines

    def matches(self, email: Email) -> bool:
        if email.from_domain and email.from_domain.endswith("knetgroup.com"):
            return False
        text = _text_from_email(email)
        if not text:
            return False
        return address_matches(text, self._address_lines)

    def parse(self, email: Email) -> ParseResult | None:
        text = _text_from_email(email)
        if not text:
            return None
        if not address_matches(text, self._address_lines):
            return None  # safety net — should have been filtered by matches()

        tracking = extract_tracking(text + " " + (email.raw_html or ""))
        if not tracking:
            return None  # no tracking → useless for reconciliation

        retailer = (email.from_domain or "").split(".")[0] if email.from_domain else None

        order_match = ORDER_NUMBER_RE.search(text)
        order_number = order_match.group(1) if order_match else None

        ship_date = email.received_at

        size_match = SIZE_RE.search(text)
        size = size_match.group(1) if size_match else None

        price_match = PRICE_RE.search(text)
        price = None
        currency = None
        if price_match:
            currency_sym, amount = price_match.groups()
            currency = {"$": "USD", "£": "GBP", "€": "EUR"}.get(currency_sym)
            try:
                price = float(amount.replace(",", "."))
            except ValueError:
                price = None

        sku_match = SKU_RE.search(text)
        sku = sku_match.group(1) if sku_match else None

        item = _first_image_alt(email.raw_html) or _first_heading(email.raw_html)

        # Confidence bumps from optional fields successfully extracted.
        confidence = _BASE_CONFIDENCE
        for field_val in (order_number, size, price, sku, item):
            if field_val:
                confidence += _CONFIDENCE_PER_FIELD
        confidence = min(confidence, 0.7)  # cap per spec

        return ParseResult(
            kind=ParserKind.SHIPMENT,
            confidence=confidence,
            retailer=retailer,
            order_number=order_number,
            ship_date=ship_date,
            item_description=item,
            sku=sku,
            size=size,
            price=price,
            currency=currency,
            tracking=[(t.carrier, t.number) for t in tracking],
        )
