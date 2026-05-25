"""End-to-end smoke test: synthetic shipment + matching synthetic KNET receipt
through the full pipeline. No Gmail calls — we insert Email rows directly.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from knet_reconciler.db import Email, Match, Receipt, Shipment, make_session_factory
from knet_reconciler.export import write_xlsx
from knet_reconciler.parsers.base import registry
from knet_reconciler.parsers.generic import GenericParser
from knet_reconciler.parsers.knet import KnetParser
from knet_reconciler.reconcile import parse_all, reconcile

WAREHOUSE = ["KNET", "1709 Imperial Way"]


@pytest.fixture
def session(tmp_path: Path):
    db = tmp_path / "test.sqlite"
    SessionFactory = make_session_factory(db)
    yield SessionFactory()


@pytest.fixture(autouse=True)
def _parsers():
    registry.reset()
    registry.register(KnetParser())
    registry.register(GenericParser(WAREHOUSE))
    yield
    registry.reset()


def _seed_shipment_email(session, *, gmail_id, tracking, days_old=2):
    received_at = datetime.now(timezone.utc) - timedelta(days=days_old)
    session.add(
        Email(
            gmail_id=gmail_id,
            thread_id="t1",
            from_address="orders@shop.example.com",
            from_domain="shop.example.com",
            subject="Your order has shipped",
            received_at=received_at,
            snippet=None,
            raw_text=(
                f"Hi! Your order #ABC-1234 shipped.\n"
                f"Ship to: KNET\n1709 Imperial Way\n"
                f"Tracking: {tracking}\nSize: 10.5\nTotal: $250.00\n"
            ),
            parsed=False,
        )
    )


def _seed_knet_email(session, *, gmail_id, tracking, days_old=1):
    received_at = datetime.now(timezone.utc) - timedelta(days=days_old)
    session.add(
        Email(
            gmail_id=gmail_id,
            thread_id="t2",
            from_address="notify@knetgroup.com",
            from_domain="knetgroup.com",
            subject="Package received",
            received_at=received_at,
            snippet=None,
            raw_text=f"We've received your package!\nTracking: {tracking}\nSKU: SKU-987\n",
            parsed=False,
        )
    )


def test_matched_shipment_lands_in_xlsx(session, tmp_path):
    _seed_shipment_email(session, gmail_id="S1", tracking="1Z999AA10123456784", days_old=2)
    _seed_knet_email(session, gmail_id="R1", tracking="1Z999AA10123456784", days_old=1)
    session.commit()

    parse_all(session, WAREHOUSE)
    counts = reconcile(session, stale_days=14)
    assert counts["matched"] == 1
    assert counts["missing"] == 0
    assert counts["pending"] == 0

    out = write_xlsx(session, tmp_path / "report.xlsx", stale_days=14)
    assert out.exists()
    assert out.stat().st_size > 0


def test_stale_shipment_with_no_receipt_is_missing(session, tmp_path):
    _seed_shipment_email(session, gmail_id="S2", tracking="1Z999AA10999999999", days_old=30)
    session.commit()
    parse_all(session, WAREHOUSE)
    counts = reconcile(session, stale_days=14)
    assert counts["missing"] == 1
    assert counts["matched"] == 0


def test_recent_shipment_with_no_receipt_is_pending(session, tmp_path):
    _seed_shipment_email(session, gmail_id="S3", tracking="1Z999AA10000000001", days_old=2)
    session.commit()
    parse_all(session, WAREHOUSE)
    counts = reconcile(session, stale_days=14)
    assert counts["pending"] == 1
    assert counts["matched"] == 0


def test_orphan_receipt_with_no_shipment_is_flagged(session, tmp_path):
    _seed_knet_email(session, gmail_id="R-orphan", tracking="JJD000999999999", days_old=1)
    session.commit()
    parse_all(session, WAREHOUSE)
    counts = reconcile(session, stale_days=14)
    assert counts["orphans"] == 1


def test_rerun_is_idempotent(session, tmp_path):
    _seed_shipment_email(session, gmail_id="S4", tracking="1Z999AA10000000002", days_old=2)
    _seed_knet_email(session, gmail_id="R4", tracking="1Z999AA10000000002", days_old=1)
    session.commit()

    parse_all(session, WAREHOUSE)
    reconcile(session, stale_days=14)
    n_ship_1 = session.query(Shipment).count()
    n_match_1 = session.query(Match).count()

    # Mark emails unparsed and re-run.
    for e in session.query(Email).all():
        e.parsed = False
    session.commit()

    parse_all(session, WAREHOUSE)
    reconcile(session, stale_days=14)

    assert session.query(Shipment).count() == n_ship_1
    assert session.query(Match).count() == n_match_1
