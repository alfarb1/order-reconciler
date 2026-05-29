"""Tests for the schema migration, reconcile preservation, and webui APIs."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from knet_reconciler.config import (
    Config,
    GmailConfig,
    KnetConfig,
    Paths,
    ReconcileConfig,
)
from knet_reconciler.db import (
    Email,
    Match,
    Receipt,
    Shipment,
    init_db,
    make_session_factory,
)
from knet_reconciler.reconcile import (
    FLAG_MISSING,
    MATCH_MANUAL,
    MATCH_NONE,
    MATCH_TRACKING,
    reconcile,
)
from knet_reconciler.webui import create_app


# ---------- helpers ----------

def _cfg(tmp_path: Path) -> Config:
    return Config(
        knet=KnetConfig(),
        reconcile=ReconcileConfig(),
        gmail=GmailConfig(),
        retailer_overrides=[],
        paths=Paths(
            credentials=tmp_path / "credentials.json",
            token=tmp_path / "token.json",
            db=tmp_path / "test.sqlite",
            config=tmp_path / "config.toml",
        ),
    )


def _seed_shipment(session, *, tracking="1Z999AA10123456784", days_old=20, gmail_id="g1") -> Shipment:
    ship_dt = datetime.now(timezone.utc) - timedelta(days=days_old)
    e = Email(
        gmail_id=gmail_id, thread_id="t-" + gmail_id, from_address="x@shop.example",
        from_domain="shop.example", subject="Shipped", received_at=ship_dt,
        raw_text="ok", parsed=True,
    )
    session.add(e)
    s = Shipment(
        email_id=gmail_id, retailer="Shop", order_number="O1",
        ship_date=ship_dt, tracking_number=tracking,
        tracking_number_normalized=tracking, carrier="UPS", confidence=0.9,
    )
    session.add(s)
    session.commit()
    return s


def _seed_receipt(session, *, tracking, gmail_id="rcv1") -> Receipt:
    e = Email(
        gmail_id=gmail_id, thread_id="t-" + gmail_id, from_address="r@knetgroup.com",
        from_domain="knetgroup.com", subject="Received", received_at=datetime.now(timezone.utc),
        raw_text="ok", parsed=True,
    )
    session.add(e)
    r = Receipt(
        email_id=gmail_id, received_at=datetime.now(timezone.utc),
        tracking_number=tracking, tracking_number_normalized=tracking, carrier="UPS",
    )
    session.add(r)
    session.commit()
    return r


# ---------- schema migration ----------

class TestSchemaMigration:
    def test_init_db_creates_new_columns(self, tmp_path):
        engine = init_db(tmp_path / "x.sqlite")
        with engine.begin() as conn:
            cols = {row[1] for row in conn.exec_driver_sql('PRAGMA table_info("matches")').fetchall()}
        assert "note" in cols
        assert "resolved_at" in cols

    def test_init_db_is_idempotent(self, tmp_path):
        # Calling twice on the same DB must not crash (no "duplicate column" error)
        init_db(tmp_path / "x.sqlite")
        init_db(tmp_path / "x.sqlite")  # second call exercises the "column already exists" branch

    def test_migration_preserves_existing_rows(self, tmp_path):
        # Simulate an old DB without the new columns, then run init_db, then verify rows survive.
        import sqlite3
        db = tmp_path / "old.sqlite"
        # create an old-shape matches table by hand
        conn = sqlite3.connect(db)
        conn.executescript("""
            CREATE TABLE shipments (id INTEGER PRIMARY KEY);
            CREATE TABLE receipts (id INTEGER PRIMARY KEY);
            CREATE TABLE matches (
                id INTEGER PRIMARY KEY, shipment_id INTEGER UNIQUE,
                receipt_id INTEGER, match_type TEXT, flagged_reason TEXT,
                created_at DATETIME
            );
            INSERT INTO shipments (id) VALUES (1);
            INSERT INTO matches (id, shipment_id, match_type)
                VALUES (99, 1, 'tracking_exact');
        """)
        conn.commit()
        conn.close()
        # Run migration
        init_db(db)
        # Verify the old row is still there + new columns are NULL
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT id, shipment_id, match_type, note, resolved_at FROM matches WHERE id=99"
        ).fetchone()
        conn.close()
        assert row == (99, 1, "tracking_exact", None, None)


# ---------- reconcile preserves manual ----------

class TestReconcilePreservesManual:
    def test_manual_match_survives_reconcile(self, tmp_path):
        SessionFactory = make_session_factory(tmp_path / "t.sqlite")
        with SessionFactory() as session:
            s = _seed_shipment(session, tracking="T123", days_old=30)
            # No receipt for T123 → would normally be missing.
            # User manually resolves with a note.
            m = Match(
                shipment_id=s.id, match_type=MATCH_MANUAL,
                note="KNET support confirmed by email", resolved_at=datetime.now(timezone.utc),
            )
            session.add(m)
            session.commit()

            counts = reconcile(session)

            after = session.query(Match).filter(Match.shipment_id == s.id).one()
            assert after.match_type == MATCH_MANUAL
            assert after.note == "KNET support confirmed by email"
            assert counts["manually_resolved"] == 1
            assert counts["missing"] == 0

    def test_normal_reconcile_still_flags_missing(self, tmp_path):
        # Sanity check we didn't break the default path.
        SessionFactory = make_session_factory(tmp_path / "t.sqlite")
        with SessionFactory() as session:
            _seed_shipment(session, tracking="T999", days_old=30)
            counts = reconcile(session)
            assert counts["missing"] == 1
            assert counts["manually_resolved"] == 0

    def test_manual_match_with_receipt_counts_that_receipt_as_matched(self, tmp_path):
        # If user manually pairs the shipment to an orphan receipt, that receipt
        # must not also show up as an orphan on the next reconcile run.
        SessionFactory = make_session_factory(tmp_path / "t.sqlite")
        with SessionFactory() as session:
            s = _seed_shipment(session, tracking="ABC", days_old=30, gmail_id="ship")
            r = _seed_receipt(session, tracking="XYZ-different", gmail_id="rcv")
            # User pairs them manually despite tracking mismatch.
            session.add(Match(shipment_id=s.id, match_type=MATCH_MANUAL, receipt_id=r.id))
            session.commit()

            counts = reconcile(session)
            # Orphan count should be 0 (the receipt is owned by the manual match).
            assert counts["orphans"] == 0
            assert counts["manually_resolved"] == 1


# ---------- webui API ----------

class TestWebUIApi:
    @pytest.fixture
    def app_and_db(self, tmp_path):
        cfg = _cfg(tmp_path)
        app = create_app(cfg)
        app.config["TESTING"] = True
        SessionFactory = app.config["KNET_SESSION_FACTORY"]
        with SessionFactory() as session:
            s1 = _seed_shipment(session, tracking="A1", days_old=30, gmail_id="g-a1")
            s2 = _seed_shipment(session, tracking="A2", days_old=30, gmail_id="g-a2")
            s3 = _seed_shipment(session, tracking="A3", days_old=30, gmail_id="g-a3")
            # Initial reconcile populates Match rows as MISSING
            reconcile(session)
            ids = (s1.id, s2.id, s3.id)
        return app, SessionFactory, ids

    def test_data_endpoint_returns_three_missing(self, app_and_db):
        app, _, _ = app_and_db
        client = app.test_client()
        data = client.get("/api/data").get_json()
        assert data["counts"]["missing"] == 3
        assert len(data["shipments"]) == 3
        assert all(s["status"] == "missing" for s in data["shipments"])

    def test_resolve_endpoint_marks_manual(self, app_and_db):
        app, SessionFactory, (sid, _, _) = app_and_db
        client = app.test_client()
        r = client.post(f"/api/shipments/{sid}/resolve", json={"note": "test note"}).get_json()
        assert r["ok"] is True
        assert r["count"] == 1
        with SessionFactory() as session:
            m = session.query(Match).filter(Match.shipment_id == sid).one()
            assert m.match_type == MATCH_MANUAL
            assert m.note == "test note"
            assert m.resolved_at is not None

    def test_unresolve_endpoint_reverts(self, app_and_db):
        app, SessionFactory, (sid, _, _) = app_and_db
        client = app.test_client()
        client.post(f"/api/shipments/{sid}/resolve", json={"note": "x"})
        client.post(f"/api/shipments/{sid}/unresolve", json={})
        with SessionFactory() as session:
            m = session.query(Match).filter(Match.shipment_id == sid).one()
            assert m.match_type == MATCH_NONE
            assert m.note is None
            assert m.resolved_at is None

    def test_bulk_resolve_marks_many(self, app_and_db):
        app, SessionFactory, ids = app_and_db
        client = app.test_client()
        r = client.post("/api/shipments/bulk-resolve",
                        json={"ids": list(ids), "note": "batch confirmation"}).get_json()
        assert r["ok"] is True
        assert r["count"] == 3
        with SessionFactory() as session:
            for sid in ids:
                m = session.query(Match).filter(Match.shipment_id == sid).one()
                assert m.match_type == MATCH_MANUAL
                assert m.note == "batch confirmation"

    def test_bulk_resolve_rejects_bad_payload(self, app_and_db):
        app, _, _ = app_and_db
        client = app.test_client()
        rv = client.post("/api/shipments/bulk-resolve", json={"ids": "not a list"})
        assert rv.status_code == 400

    def test_data_endpoint_after_resolve_moves_to_manual_bucket(self, app_and_db):
        app, _, (sid, _, _) = app_and_db
        client = app.test_client()
        client.post(f"/api/shipments/{sid}/resolve", json={"note": ""})
        data = client.get("/api/data").get_json()
        assert data["counts"]["missing"] == 2
        assert data["counts"]["manually_resolved"] == 1

    def test_resolve_survives_reconcile_via_api(self, app_and_db):
        # The whole point — resolving in the UI must survive a reconcile.
        app, SessionFactory, (sid, _, _) = app_and_db
        client = app.test_client()
        client.post(f"/api/shipments/{sid}/resolve", json={"note": "support email 5/28"})
        with SessionFactory() as session:
            reconcile(session)
            m = session.query(Match).filter(Match.shipment_id == sid).one()
            assert m.match_type == MATCH_MANUAL
            assert m.note == "support email 5/28"


# ---------- dashboard HTML smoke ----------

def test_index_renders(tmp_path):
    cfg = _cfg(tmp_path)
    app = create_app(cfg)
    client = app.test_client()
    rv = client.get("/")
    assert rv.status_code == 200
    body = rv.get_data(as_text=True)
    assert "KNET Reconciler" in body
    assert "/api/data" in body  # the JS calls this on load
