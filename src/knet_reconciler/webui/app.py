"""Flask app factory + server runner for the KNET reconciler UI."""
from __future__ import annotations

import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template_string, request

from ..config import Config, load_config
from ..db import Email, Match, Receipt, Shipment, make_session_factory
from ..reconcile import (
    FLAG_MISSING,
    FLAG_PENDING,
    MATCH_MANUAL,
    MATCH_NONE,
    MATCH_TRACKING,
    orphan_receipts,
)
from .template import INDEX_HTML


def create_app(cfg: Config | None = None) -> Flask:
    if cfg is None:
        cfg = load_config()
    SessionFactory = make_session_factory(cfg.paths.db)

    app = Flask(__name__)
    app.config["JSON_SORT_KEYS"] = False
    app.config["KNET_CFG"] = cfg
    app.config["KNET_SESSION_FACTORY"] = SessionFactory

    _register_routes(app)
    return app


def run_server(cfg: Config | None = None, port: int = 5050, open_browser: bool = True) -> None:
    app = create_app(cfg)
    url = f"http://127.0.0.1:{port}/"
    if open_browser:
        webbrowser.open(url)
    print(f"KNET reconciler UI: {url}  (Ctrl+C to stop)")
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


# ---------------- routes ----------------

def _register_routes(app: Flask) -> None:
    @app.get("/")
    def index():
        return render_template_string(INDEX_HTML)

    @app.get("/api/data")
    def api_data():
        SessionFactory = app.config["KNET_SESSION_FACTORY"]
        with SessionFactory() as session:
            shipments = _shipment_rows(session)
            orphans = _orphan_rows(session)
            counts = _counts(shipments, orphans)
        return jsonify({"shipments": shipments, "orphans": orphans, "counts": counts})

    @app.get("/api/email/<email_id>")
    def api_email(email_id: str):
        SessionFactory = app.config["KNET_SESSION_FACTORY"]
        with SessionFactory() as session:
            e = session.query(Email).filter(Email.gmail_id == email_id).one_or_none()
            if e is None:
                return jsonify({"error": "not found"}), 404
            return jsonify({
                "gmail_id": e.gmail_id,
                "thread_id": e.thread_id,
                "from_address": e.from_address,
                "subject": e.subject,
                "received_at": _iso(e.received_at),
                "snippet": e.snippet,
                "gmail_url": _gmail_url(e),
            })

    @app.post("/api/shipments/<int:shipment_id>/resolve")
    def api_resolve(shipment_id: int):
        body = request.get_json(silent=True) or {}
        note = (body.get("note") or "").strip() or None
        receipt_id = body.get("receipt_id")  # optional — manually pair with an orphan
        return jsonify(_resolve(app, [shipment_id], note, receipt_id))

    @app.post("/api/shipments/<int:shipment_id>/unresolve")
    def api_unresolve(shipment_id: int):
        return jsonify(_unresolve(app, [shipment_id]))

    @app.post("/api/shipments/bulk-resolve")
    def api_bulk_resolve():
        body = request.get_json(silent=True) or {}
        ids = body.get("ids") or []
        note = (body.get("note") or "").strip() or None
        if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
            return jsonify({"error": "ids must be a list of integers"}), 400
        return jsonify(_resolve(app, ids, note, None))

    @app.post("/api/label-missing")
    def api_label_missing():
        return jsonify(_label_missing(app))


# ---------------- helpers ----------------

def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _gmail_url(email: Email | None) -> str | None:
    if not email or not email.thread_id:
        return None
    return f"https://mail.google.com/mail/u/0/#all/{email.thread_id}"


def _status_for(match: Match | None) -> str:
    if match is None:
        return "unknown"
    if match.match_type == MATCH_MANUAL:
        return "manually_resolved"
    if match.match_type == MATCH_TRACKING:
        return "matched"
    if match.flagged_reason == FLAG_MISSING:
        return "missing"
    if match.flagged_reason == FLAG_PENDING:
        return "pending"
    return "unknown"


def _shipment_rows(session) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for s in session.query(Shipment).all():
        match = s.match
        email = s.email
        receipt = match.receipt if match and match.receipt_id else None
        rows.append({
            "id": s.id,
            "status": _status_for(match),
            "retailer": s.retailer,
            "order_number": s.order_number,
            "ship_date": _iso(s.ship_date),
            "tracking_number": s.tracking_number,
            "carrier": s.carrier,
            "item_description": s.item_description,
            "sku": s.sku,
            "size": s.size,
            "price": s.price,
            "currency": s.currency,
            "confidence": s.confidence,
            "email_id": s.email_id,
            "email_subject": email.subject if email else None,
            "gmail_url": _gmail_url(email),
            "note": match.note if match else None,
            "resolved_at": _iso(match.resolved_at) if match else None,
            "receipt": {
                "id": receipt.id,
                "received_at": _iso(receipt.received_at),
                "tracking_number": receipt.tracking_number,
            } if receipt else None,
        })
    rows.sort(key=lambda r: (r["status"] != "missing", r["ship_date"] or ""))
    return rows


def _orphan_rows(session) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for r in orphan_receipts(session):
        email = r.email
        rows.append({
            "id": r.id,
            "received_at": _iso(r.received_at),
            "tracking_number": r.tracking_number,
            "carrier": r.carrier,
            "sku": r.sku,
            "notes": r.notes,
            "email_id": r.email_id,
            "email_subject": email.subject if email else None,
            "gmail_url": _gmail_url(email),
        })
    rows.sort(key=lambda r: r["received_at"] or "", reverse=True)
    return rows


def _counts(shipments: list[dict], orphans: list[dict]) -> dict[str, int]:
    out = {"all": len(shipments), "missing": 0, "pending": 0, "matched": 0,
           "manually_resolved": 0, "orphans": len(orphans)}
    for s in shipments:
        out[s["status"]] = out.get(s["status"], 0) + 1
    return out


def _resolve(app: Flask, ids: list[int], note: str | None, receipt_id: int | None) -> dict:
    SessionFactory = app.config["KNET_SESSION_FACTORY"]
    now = datetime.now(timezone.utc)
    resolved: list[int] = []
    with SessionFactory() as session:
        for sid in ids:
            match = session.query(Match).filter(Match.shipment_id == sid).one_or_none()
            if match is None:
                match = Match(shipment_id=sid, match_type=MATCH_MANUAL)
                session.add(match)
            match.match_type = MATCH_MANUAL
            match.flagged_reason = None
            if receipt_id is not None:
                match.receipt_id = receipt_id
            if note is not None:
                match.note = note
            match.resolved_at = now
            resolved.append(sid)
        session.commit()
    return {"ok": True, "resolved": resolved, "count": len(resolved)}


def _unresolve(app: Flask, ids: list[int]) -> dict:
    from ..reconcile import reconcile as run_reconcile
    cfg: Config = app.config["KNET_CFG"]
    SessionFactory = app.config["KNET_SESSION_FACTORY"]
    unresolved: list[int] = []
    with SessionFactory() as session:
        for sid in ids:
            match = session.query(Match).filter(Match.shipment_id == sid).one_or_none()
            if match is None or match.match_type != MATCH_MANUAL:
                continue
            match.match_type = MATCH_NONE
            match.receipt_id = None
            match.note = None
            match.resolved_at = None
            unresolved.append(sid)
        session.commit()
        # Re-run reconcile so the just-cleared rows get a fresh missing/pending
        # classification — otherwise the UI shows them as 'unknown' until the
        # user runs `knet-reconcile reconcile` from the CLI.
        if unresolved:
            run_reconcile(session, stale_days=cfg.reconcile.stale_days)
    return {"ok": True, "unresolved": unresolved, "count": len(unresolved)}


def _label_missing(app: Flask) -> dict:
    """Same logic as cli.label_missing, returned as JSON instead of printed."""
    from ..gmail_client import GmailClient
    cfg: Config = app.config["KNET_CFG"]
    SessionFactory = app.config["KNET_SESSION_FACTORY"]
    label = "KNET-Missing"

    with SessionFactory() as session:
        missing_email_ids = [
            row[0] for row in session.query(Shipment.email_id)
            .join(Match, Match.shipment_id == Shipment.id)
            .filter(Match.flagged_reason == FLAG_MISSING)
            .distinct().all()
            if row[0]
        ]

    if not missing_email_ids:
        return {"ok": True, "added": 0, "skipped": 0, "label": label, "message": "Nothing to label."}

    try:
        client = GmailClient.from_paths(cfg.paths.credentials, cfg.paths.token)
        label_id = client.get_or_create_label(label)
        added = skipped = 0
        for gmail_id in missing_email_ids:
            if client.add_label(gmail_id, label_id):
                added += 1
            else:
                skipped += 1
        return {"ok": True, "added": added, "skipped": skipped, "label": label}
    except Exception as e:
        return {"ok": False, "error": str(e)}
