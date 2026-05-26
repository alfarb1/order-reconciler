"""Typer CLI: auth | fetch | parse | reconcile | report | run | review."""
# NOTE: avoid non-ASCII characters in docstrings/help text — Windows cp1252
# console encoding chokes on arrows etc.
from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .config import Config, load_config
from .db import init_db, make_session_factory
from .export import write_csv, write_xlsx
from .gmail_client import GmailClient, authenticate, cache_messages, known_gmail_ids, with_since
from .parsers.base import registry
from .parsers.generic import GenericParser
from .parsers.knet import KnetParser
from .reconcile import parse_all, reconcile

app = typer.Typer(add_completion=False, no_args_is_help=True, help=__doc__)
console = Console()
log = logging.getLogger("knet")


def _register_parsers(cfg: Config):
    """Set up the parser registry: named retailers (lowest priority number = first),
    then Shopify generic, then heuristic generic. KNET parser owns receipts."""
    registry.reset()
    registry.register(KnetParser(sender_domain=cfg.knet.sender_domain))

    # Named retailers — imported lazily to keep the module import light.
    from .parsers import stockx, goat, finishline, jdsports, footlocker, nike, adidas, shopify

    for cls in (
        stockx.StockXParser,
        goat.GoatParser,
        finishline.FinishLineParser,
        jdsports.JDSportsParser,
        footlocker.FootLockerParser,
        nike.NikeParser,
        adidas.AdidasParser,
        shopify.ShopifyParser,
    ):
        registry.register(cls(cfg.knet.warehouse_address_lines))

    # Manual sender-domain overrides from config.toml.
    for ov in cfg.retailer_overrides:
        log.info("override: %s -> %s", ov.from_domain, ov.parser)

    # Always-last heuristic fallback.
    registry.register(GenericParser(cfg.knet.warehouse_address_lines))


@app.command()
def auth():
    """Run the Gmail OAuth flow once. Writes token.json next to credentials.json."""
    cfg = load_config()
    creds = authenticate(cfg.paths.credentials, cfg.paths.token)
    console.print(f"[green]Authenticated.[/] Token saved to {cfg.paths.token}")


def _build_client(cfg: Config) -> GmailClient:
    return GmailClient.from_paths(cfg.paths.credentials, cfg.paths.token)


@app.command()
def fetch(
    since: str | None = typer.Option(None, help="ISO date (YYYY-MM-DD). Only fetch messages after this date."),
):
    """Pull new messages from Gmail into the local SQLite cache."""
    cfg = load_config()
    SessionFactory = make_session_factory(cfg.paths.db)
    since_dt = datetime.fromisoformat(since).replace(tzinfo=timezone.utc) if since else None

    outbound_q = with_since(cfg.gmail.outbound_query, since_dt)
    knet_q = with_since(cfg.gmail.knet_query, since_dt)

    client = _build_client(cfg)
    with SessionFactory() as session:
        existing = known_gmail_ids(session)
        console.print(f"[dim]Known emails in DB: {len(existing)}[/]")

        n_out = cache_messages(session, client.fetch_new_messages(outbound_q, existing))
        console.print(f"[green]Outbound: cached {n_out} new emails[/]")

        existing = known_gmail_ids(session)
        n_knet = cache_messages(session, client.fetch_new_messages(knet_q, existing))
        console.print(f"[green]KNET: cached {n_knet} new emails[/]")


@app.command()
def parse(reparse: bool = typer.Option(False, "--reparse", help="Re-parse every cached email, not just unparsed ones.")):
    """Run parsers across cached emails. Writes shipments + receipts."""
    cfg = load_config()
    _register_parsers(cfg)
    SessionFactory = make_session_factory(cfg.paths.db)
    with SessionFactory() as session:
        if reparse:
            from .db import Email
            session.query(Email).update({Email.parsed: False, Email.parser_used: None, Email.parse_error: None})
            session.commit()
        counts = parse_all(session, cfg.knet.warehouse_address_lines)
    _print_counts("Parsed", counts)


@app.command(name="reconcile")
def reconcile_cmd():
    """Match shipments to KNET receipts. Idempotent."""
    cfg = load_config()
    SessionFactory = make_session_factory(cfg.paths.db)
    with SessionFactory() as session:
        counts = reconcile(session, stale_days=cfg.reconcile.stale_days)
    _print_counts("Reconciled", counts)


@app.command()
def report(
    xlsx: Path = typer.Option(Path("reconciliation.xlsx"), help="Output xlsx path."),
    csv: Path | None = typer.Option(None, help="Also write a flat CSV to this path."),
    open_after: bool = typer.Option(False, "--open", help="Open the xlsx after writing."),
):
    """Write the reconciliation spreadsheet."""
    cfg = load_config()
    SessionFactory = make_session_factory(cfg.paths.db)
    with SessionFactory() as session:
        out = write_xlsx(session, xlsx, stale_days=cfg.reconcile.stale_days)
        console.print(f"[green]Wrote[/] {out}")
        if csv:
            out_csv = write_csv(session, csv)
            console.print(f"[green]Wrote[/] {out_csv}")
    if open_after and sys.platform == "win32":
        os.startfile(out)  # type: ignore[attr-defined]
    elif open_after:
        subprocess.run(["open", str(out)], check=False)


@app.command()
def run(
    xlsx: Path = typer.Option(Path("reconciliation.xlsx")),
    csv: Path | None = typer.Option(None),
    open_after: bool = typer.Option(False, "--open"),
):
    """Fetch -> parse -> reconcile -> report. Use daily."""
    fetch(since=None)
    parse(reparse=False)
    reconcile_cmd()
    report(xlsx=xlsx, csv=csv, open_after=open_after)


@app.command(name="label-missing")
def label_missing(
    label: str = typer.Option("KNET-Missing", help="Gmail label name to apply to source emails of missing shipments."),
):
    """Apply a Gmail label to every email backing a currently-missing shipment.

    Idempotent — emails that already carry the label are skipped. Requires the
    gmail.modify OAuth scope; if the existing token only has gmail.readonly,
    `knet-reconcile auth` will re-run the browser flow to grant the new permission."""
    cfg = load_config()
    SessionFactory = make_session_factory(cfg.paths.db)
    client = _build_client(cfg)

    with SessionFactory() as session:
        from .db import Match, Shipment
        from .reconcile import FLAG_MISSING
        missing_email_ids = [
            row[0] for row in session.query(Shipment.email_id)
            .join(Match, Match.shipment_id == Shipment.id)
            .filter(Match.flagged_reason == FLAG_MISSING)
            .distinct()
            .all()
            if row[0]
        ]

    if not missing_email_ids:
        console.print("[dim]No missing shipments — nothing to label.[/]")
        return

    label_id = client.get_or_create_label(label)
    added = 0
    skipped = 0
    for gmail_id in missing_email_ids:
        if client.add_label(gmail_id, label_id):
            added += 1
        else:
            skipped += 1
    console.print(f"[green]Labelled[/] {added} new, {skipped} already-tagged — Gmail label: [bold]{label}[/]")


@app.command()
def weekly(
    reports_dir: Path = typer.Option(Path("reports"), help="Directory for date-stamped weekly reports."),
    apply_label: bool = typer.Option(True, "--label/--no-label", help="Apply the KNET-Missing Gmail label to missing-shipment emails."),
    label: str = typer.Option("KNET-Missing", help="Label name when --label is on."),
):
    """Weekly scheduled run. Writes both reconciliation.xlsx (latest) and reports/reconciliation-YYYY-MM-DD.xlsx.

    Prints a single-line summary as the LAST line of stdout so the Task Scheduler wrapper can parse it:
        SUMMARY missing=N matched=N pending=N orphans=N total=N report=<path>
    """
    from datetime import date
    cfg = load_config()
    _register_parsers(cfg)
    SessionFactory = make_session_factory(cfg.paths.db)

    # Same flow as run: fetch -> parse -> reconcile -> report
    fetch(since=None)
    parse(reparse=False)
    reconcile_cmd()

    reports_dir.mkdir(parents=True, exist_ok=True)
    dated = reports_dir / f"reconciliation-{date.today().isoformat()}.xlsx"
    latest = Path("reconciliation.xlsx")

    with SessionFactory() as session:
        write_xlsx(session, dated, stale_days=cfg.reconcile.stale_days)
        try:
            write_xlsx(session, latest, stale_days=cfg.reconcile.stale_days)
        except PermissionError:
            # User has the latest file open in Excel — skip the overwrite, keep the dated copy.
            console.print(f"[yellow]Skipped {latest} (file is open). Use {dated} instead.[/]")

        from .db import Shipment
        from .reconcile import (
            FLAG_MISSING,
            FLAG_PENDING,
            MATCH_TRACKING,
            orphan_receipts,
        )
        from sqlalchemy import func, select
        from .db import Match

        total = session.query(Shipment).count()
        matched = session.query(Match).filter(Match.match_type == MATCH_TRACKING).count()
        missing = session.query(Match).filter(Match.flagged_reason == FLAG_MISSING).count()
        pending = session.query(Match).filter(Match.flagged_reason == FLAG_PENDING).count()
        orphans = len(orphan_receipts(session))

    console.print(f"[green]Wrote[/] {dated}")
    console.print(f"[green]Wrote[/] {latest}")

    if apply_label and missing:
        try:
            label_missing(label=label)
        except Exception as e:
            console.print(f"[yellow]Label step failed (continuing):[/] {e}")

    # Machine-parsable last line for the PowerShell wrapper.
    print(
        f"SUMMARY missing={missing} matched={matched} pending={pending} "
        f"orphans={orphans} total={total} report={dated.resolve()}"
    )


@app.command()
def review():
    """Interactive resolver for low-confidence parses and orphan receipts."""
    cfg = load_config()
    SessionFactory = make_session_factory(cfg.paths.db)
    with SessionFactory() as session:
        from .db import Shipment
        from .reconcile import orphan_receipts

        low = session.query(Shipment).filter(Shipment.confidence < 0.6).all()
        orphans = orphan_receipts(session)
        console.print(f"[bold]Review queue[/]: {len(low)} low-confidence shipments, {len(orphans)} orphan receipts.")
        for s in low:
            console.print(
                f"  [yellow]{s.retailer or '?'}[/] order={s.order_number or '?'} "
                f"tracking={s.tracking_number_normalized} sku={s.sku} conf={s.confidence:.2f}"
            )
        for r in orphans:
            console.print(
                f"  [magenta]orphan[/] tracking={r.tracking_number_normalized} "
                f"received_at={r.received_at} sku={r.sku}"
            )
    console.print("\n[dim]Manual edits not implemented yet — open the SQLite DB to override.[/]")


def _print_counts(title: str, counts: dict):
    table = Table(title=title)
    table.add_column("metric")
    table.add_column("value", justify="right")
    for k, v in counts.items():
        table.add_row(k, str(v))
    console.print(table)


if __name__ == "__main__":
    app()
