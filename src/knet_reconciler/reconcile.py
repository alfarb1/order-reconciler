"""Reconciliation: pair every shipment with a receipt where possible.

Strategy per SPEC §6.6:
  1. Tracking-exact match (normalized) → match_type = tracking_exact.
  2. No receipt, ship_date older than stale_days → flag as missing.
  3. No receipt, ship_date within stale_days → flag as pending (in transit).
  4. Receipt with no matched shipment → orphan (report separately).

Idempotent: re-running upserts the matches row by shipment_id.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Email, Match, Receipt, Shipment
from .parsers.base import ParseResult, ParserKind, registry
from .parsers.generic import (
    _extract_order_numbers,
    _root_domain,
    _text_from_email,
    address_matches,
    is_cancellation_subject,
    set_verified_orders,
)

log = logging.getLogger(__name__)

MATCH_TRACKING = "tracking_exact"
MATCH_ORDER = "order_number"
MATCH_NONE = "none"
MATCH_MANUAL = "manual"

FLAG_MISSING = "missing"
FLAG_PENDING = "pending"
FLAG_LOW_CONFIDENCE = "low_confidence"


def _domain_of(email: Email) -> str:
    return (email.from_domain or "").lower()


def _build_verified_orders(session: Session, warehouse_address_lines: list[str]) -> set[tuple[str, str]]:
    """Pre-pass over ALL cached emails (parsed or not). Whenever an email body contains the
    KNET warehouse address, every order number it mentions is treated as "address-verified"
    for that retailer's root domain. Shipment-notification emails that lack the address inline
    can then be accepted by cross-referencing their order number against this index.

    Cancellation/refund emails pull their (retailer, order_number) back out — Nike and ASICS
    in particular send order-received emails that hit the address filter, then a separate
    cancellation email arrives later; we don't want the cancelled order numbers polluting the
    index."""
    verified: set[tuple[str, str]] = set()
    cancelled: set[tuple[str, str]] = set()

    emails = session.query(Email).all()

    for email in emails:
        if email.from_domain and email.from_domain.endswith("knetgroup.com"):
            continue
        if not is_cancellation_subject(email.subject):
            continue
        root = _root_domain(email.from_domain)
        if not root:
            continue
        text = _text_from_email(email)
        haystack = (email.subject or "") + "\n" + text
        for order_num in _extract_order_numbers(haystack):
            cancelled.add((root, order_num))

    for email in emails:
        if email.from_domain and email.from_domain.endswith("knetgroup.com"):
            continue
        if is_cancellation_subject(email.subject):
            continue
        text = _text_from_email(email)
        if not text or not address_matches(text, warehouse_address_lines):
            continue
        root = _root_domain(email.from_domain)
        if not root:
            continue
        haystack = (email.subject or "") + "\n" + text
        for order_num in _extract_order_numbers(haystack):
            key = (root, order_num)
            if key in cancelled:
                continue
            verified.add(key)
    return verified


def parse_all(session: Session, warehouse_address_lines: list[str]) -> dict:
    """Run registered parsers across every email row that hasn't been parsed yet.

    Writes Shipment / Receipt rows. Returns counts for the caller.
    """
    counts = {"emails_parsed": 0, "shipments_added": 0, "receipts_added": 0, "skipped": 0, "errors": 0}

    set_verified_orders(_build_verified_orders(session, warehouse_address_lines))

    emails = session.query(Email).filter(Email.parsed.is_(False)).all()
    for email in emails:
        parser = registry.pick(email)
        if parser is None:
            email.parsed = True
            email.parser_used = "none"
            counts["skipped"] += 1
            continue
        try:
            result: ParseResult | None = parser.parse(email)
        except Exception as e:
            log.exception("parser %s failed on %s", parser.name, email.gmail_id)
            email.parsed = True
            email.parser_used = parser.name
            email.parse_error = str(e)
            counts["errors"] += 1
            continue

        email.parsed = True
        email.parser_used = parser.name
        counts["emails_parsed"] += 1

        if result is None:
            counts["skipped"] += 1
            continue

        if result.kind == ParserKind.SHIPMENT:
            counts["shipments_added"] += _persist_shipments(session, email, result)
        elif result.kind == ParserKind.RECEIPT:
            counts["receipts_added"] += _persist_receipts(session, email, result)

    session.commit()
    return counts


def _persist_shipments(session: Session, email: Email, result: ParseResult) -> int:
    """Write one or more Shipment rows for a parsed retailer email. Idempotent globally —
    a given tracking number produces exactly one Shipment row regardless of how many emails
    reference it (e.g., 'shipped' + 'out for delivery' + 'delivered' for the same package)."""
    added = 0
    for carrier, normalized in result.tracking or [(None, None)]:
        if normalized:
            existing = session.execute(
                select(Shipment).where(Shipment.tracking_number_normalized == normalized)
            ).scalar_one_or_none()
            if existing is not None:
                continue
        session.add(
            Shipment(
                email_id=email.gmail_id,
                retailer=result.retailer,
                order_number=result.order_number,
                order_date=result.order_date,
                ship_date=result.ship_date or email.received_at,
                tracking_number=normalized,
                tracking_number_normalized=normalized,
                carrier=carrier,
                recipient_name=result.recipient_name,
                recipient_address=result.recipient_address,
                item_description=result.item_description,
                sku=result.sku,
                size=result.size,
                price=result.price,
                currency=result.currency,
                confidence=result.confidence,
            )
        )
        added += 1
    return added


def _persist_receipts(session: Session, email: Email, result: ParseResult) -> int:
    """KNET emits 'received' and 'checked in' for the same package — dedupe globally
    on the normalized tracking number so we don't double-count receipts."""
    added = 0
    for carrier, normalized in result.tracking or []:
        if normalized:
            existing = session.execute(
                select(Receipt).where(Receipt.tracking_number_normalized == normalized)
            ).scalar_one_or_none()
            if existing is not None:
                continue
        session.add(
            Receipt(
                email_id=email.gmail_id,
                received_at=result.received_at or email.received_at,
                tracking_number=normalized,
                tracking_number_normalized=normalized,
                carrier=carrier,
                sku=result.sku,
                notes=result.notes,
            )
        )
        added += 1
    return added


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def reconcile(session: Session, stale_days: int = 14) -> dict:
    """Build/refresh the matches table. Idempotent — upserts per shipment_id."""
    shipments = session.query(Shipment).all()
    receipts = session.query(Receipt).all()

    by_tracking: dict[str, list[Receipt]] = {}
    for r in receipts:
        key = r.tracking_number_normalized
        if not key:
            continue
        by_tracking.setdefault(key, []).append(r)

    counts = {"matched": 0, "missing": 0, "pending": 0, "orphans": 0, "total_shipments": len(shipments)}
    now = _utcnow()
    matched_receipt_ids: set[int] = set()

    for s in shipments:
        match = session.query(Match).filter(Match.shipment_id == s.id).one_or_none()
        if match is None:
            match = Match(shipment_id=s.id, match_type=MATCH_NONE)
            session.add(match)

        hit: Receipt | None = None
        if s.tracking_number_normalized:
            candidates = by_tracking.get(s.tracking_number_normalized, [])
            if candidates:
                hit = candidates[0]

        if hit is not None:
            match.receipt_id = hit.id
            match.match_type = MATCH_TRACKING
            match.flagged_reason = None
            matched_receipt_ids.add(hit.id)
            counts["matched"] += 1
        else:
            ship_dt = _as_utc(s.ship_date) or now
            age_days = (now - ship_dt).days
            match.receipt_id = None
            match.match_type = MATCH_NONE
            if age_days > stale_days:
                match.flagged_reason = FLAG_MISSING
                counts["missing"] += 1
            else:
                match.flagged_reason = FLAG_PENDING
                counts["pending"] += 1

    # Orphan receipts: never picked up by any shipment.
    counts["orphans"] = sum(1 for r in receipts if r.id not in matched_receipt_ids)

    session.commit()
    return counts


def orphan_receipts(session: Session) -> list[Receipt]:
    matched_ids = {m.receipt_id for m in session.query(Match).filter(Match.receipt_id.isnot(None)).all()}
    return [r for r in session.query(Receipt).all() if r.id not in matched_ids]
