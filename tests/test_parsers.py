"""Parser tests. Synthetic fixtures for now.

TODO(real-samples): swap in real `.eml` fixtures from tests/fixtures/<retailer>/
once we have them. Mark each synthetic case below with the retailer it pretends
to be so it's clear what to replace.
"""
from datetime import datetime, timezone

import pytest

from knet_reconciler.db import Email
from knet_reconciler.parsers.generic import GenericParser, address_matches
from knet_reconciler.parsers.knet import KnetParser

WAREHOUSE = [
    "KNET",
    "123 Sneaker Way",
    "Suite 200",
    "Inglewood, CA 90301",
]


def make_email(
    *, gmail_id="g1", from_domain="example.com", subject="Your order shipped",
    raw_text=None, raw_html=None, received_at=None
):
    return Email(
        gmail_id=gmail_id,
        thread_id="t1",
        from_address=f"orders@{from_domain}",
        from_domain=from_domain,
        subject=subject,
        received_at=received_at or datetime(2026, 5, 1, tzinfo=timezone.utc),
        snippet=None,
        raw_text=raw_text,
        raw_html=raw_html,
        parsed=False,
    )


class TestAddressMatch:
    def test_exact(self):
        assert address_matches("Ship to:\nKNET\n123 Sneaker Way\nSuite 200", WAREHOUSE)

    def test_case_and_whitespace_insensitive(self):
        assert address_matches("ship to: knet,  123  sneaker  way", WAREHOUSE)

    def test_no_match_rejects(self):
        assert not address_matches("Ship to: 999 Elsewhere Ave", WAREHOUSE)

    def test_empty_lines_never_match_everything(self):
        assert not address_matches("anything", ["", "  "])


class TestGenericParser:
    @pytest.fixture
    def parser(self):
        return GenericParser(WAREHOUSE)

    def test_drops_email_not_addressed_to_knet(self, parser):
        email = make_email(raw_text="Ship to: 555 Other St")
        assert parser.matches(email) is False

    def test_skips_knet_sender(self, parser):
        email = make_email(from_domain="knetgroup.com", raw_text="KNET\n123 Sneaker Way")
        assert parser.matches(email) is False

    def test_parses_shipment_with_tracking(self, parser):
        body = (
            "Hi! Your order #ABC-12345 has shipped.\n"
            "Ship to: KNET\n123 Sneaker Way\nSuite 200\nInglewood, CA 90301\n"
            "Tracking: 1Z999AA10123456784\n"
            "Size: 10.5\n"
            "Total: $250.00\n"
        )
        email = make_email(from_domain="shop.example.com", raw_text=body)
        assert parser.matches(email) is True
        result = parser.parse(email)
        assert result is not None
        assert result.order_number == "ABC-12345"
        assert ("ups", "1Z999AA10123456784") in result.tracking
        assert result.size == "10.5"
        assert result.price == 250.0
        assert result.currency == "USD"
        # Without a display name in From, retailer falls back to the registrable domain
        # ("shop.example.com" -> "Example") — the leftmost subdomain is junk noise like
        # "emails", "t", "store" that doesn't identify the brand.
        assert result.retailer == "Example"
        assert 0.3 <= result.confidence <= 0.7

    def test_retailer_extracted_from_display_name(self, parser):
        body = (
            "Hi! Your order #ABC-12345 has shipped.\n"
            "Ship to: KNET\n123 Sneaker Way\nSuite 200\nInglewood, CA 90301\n"
            "Tracking: 1Z999AA10123456784\n"
        )
        email = Email(
            gmail_id="g2", thread_id="t1",
            from_address='"Reynolds & Sons" <store+12345@t.shopifyemail.com>',
            from_domain="t.shopifyemail.com",
            subject="A shipment from order #ABC-12345 has shipped",
            received_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            snippet=None, raw_text=body, raw_html=None, parsed=False,
        )
        result = parser.parse(email)
        assert result is not None
        assert result.retailer == "Reynolds & Sons"

    def test_returns_none_when_no_tracking(self, parser):
        body = "Ship to: KNET\n123 Sneaker Way\n(no tracking yet)"
        email = make_email(raw_text=body)
        assert parser.parse(email) is None


class TestKnetParser:
    @pytest.fixture
    def parser(self):
        return KnetParser()

    def test_matches_knetgroup_sender(self, parser):
        email = make_email(from_domain="knetgroup.com", subject="Package received")
        assert parser.matches(email) is True

    def test_does_not_match_other_sender(self, parser):
        email = make_email(from_domain="stockx.com")
        assert parser.matches(email) is False

    def test_parses_receipt_with_tracking_and_date(self, parser):
        body = (
            "We've received your package!\n"
            "Received on May 3, 2026\n"
            "Tracking: 1Z999AA10123456784\n"
            "SKU: ABC123\n"
        )
        email = make_email(from_domain="knetgroup.com", subject="Package received", raw_text=body)
        result = parser.parse(email)
        assert result is not None
        assert result.received_at is not None
        assert result.received_at.year == 2026 and result.received_at.month == 5
        assert ("ups", "1Z999AA10123456784") in result.tracking
        assert result.sku == "ABC123"

    def test_emits_multiple_tracking_per_email(self, parser):
        body = (
            "Received the following packages:\n"
            "- 1Z999AA10123456784\n"
            "- JJD000123456789\n"
        )
        email = make_email(from_domain="knetgroup.com", subject="Inventory update", raw_text=body)
        result = parser.parse(email)
        assert result is not None
        nums = {n for _, n in result.tracking}
        assert {"1Z999AA10123456784", "JJD000123456789"} <= nums
