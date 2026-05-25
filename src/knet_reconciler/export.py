"""Excel + CSV export of the reconciliation result. Per SPEC §6.9."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy.orm import Session

from .db import Match, Receipt, Shipment
from .reconcile import FLAG_MISSING, FLAG_PENDING, MATCH_TRACKING, orphan_receipts

GREEN = PatternFill("solid", start_color="C6EFCE", end_color="C6EFCE")
YELLOW = PatternFill("solid", start_color="FFEB9C", end_color="FFEB9C")
RED = PatternFill("solid", start_color="FFC7CE", end_color="FFC7CE")
HEADER_FILL = PatternFill("solid", start_color="305496", end_color="305496")
HEADER_FONT = Font(bold=True, color="FFFFFF")

SHIPMENT_COLUMNS = [
    "retailer",
    "order_number",
    "ship_date",
    "tracking_number_normalized",
    "carrier",
    "item_description",
    "sku",
    "size",
    "price",
    "currency",
    "confidence",
    "match_status",
    "received_at",
    "days_to_receipt",
    "flag",
    "email_id",
]


@dataclass
class ShipmentRow:
    shipment: Shipment
    match: Match | None
    receipt: Receipt | None

    def as_tuple(self) -> tuple:
        s = self.shipment
        m = self.match
        r = self.receipt
        status = m.match_type if m else "none"
        flag = m.flagged_reason if m else None
        days = None
        if r and s.ship_date and r.received_at:
            try:
                days = (r.received_at - s.ship_date).days
            except TypeError:
                days = None
        return (
            s.retailer,
            s.order_number,
            s.ship_date,
            s.tracking_number_normalized,
            s.carrier,
            s.item_description,
            s.sku,
            s.size,
            s.price,
            s.currency,
            round(s.confidence or 0.0, 2),
            status,
            r.received_at if r else None,
            days,
            flag,
            s.email_id,
        )


def _gather(session: Session) -> list[ShipmentRow]:
    rows: list[ShipmentRow] = []
    for s in session.query(Shipment).all():
        match = session.query(Match).filter(Match.shipment_id == s.id).one_or_none()
        receipt = None
        if match and match.receipt_id:
            receipt = session.get(Receipt, match.receipt_id)
        rows.append(ShipmentRow(s, match, receipt))
    return rows


def _strip_tz(rows: list[tuple]) -> list[tuple]:
    """openpyxl rejects tz-aware datetimes; strip tzinfo for the spreadsheet."""
    out = []
    for tup in rows:
        new = tuple(
            v.replace(tzinfo=None) if isinstance(v, datetime) and v.tzinfo else v
            for v in tup
        )
        out.append(new)
    return out


def _write_header(ws, columns: list[str]):
    for col_idx, name in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=col_idx, value=name)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
    ws.freeze_panes = "A2"


def _autosize(ws):
    for col_cells in ws.columns:
        max_len = 10
        letter = get_column_letter(col_cells[0].column)
        for cell in col_cells:
            v = cell.value
            if v is None:
                continue
            max_len = max(max_len, min(60, len(str(v))))
        ws.column_dimensions[letter].width = max_len + 2


def write_xlsx(session: Session, output: Path, stale_days: int = 14) -> Path:
    rows = _gather(session)
    output.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    # Summary
    summary = wb.active
    summary.title = "Summary"
    total = len(rows)
    matched = sum(1 for r in rows if r.match and r.match.match_type == MATCH_TRACKING)
    missing = sum(1 for r in rows if r.match and r.match.flagged_reason == FLAG_MISSING)
    pending = sum(1 for r in rows if r.match and r.match.flagged_reason == FLAG_PENDING)
    orphans = len(orphan_receipts(session))
    low_conf = sum(1 for r in rows if (r.shipment.confidence or 0) < 0.6)

    summary["A1"] = "KNET Reconciliation Report"
    summary["A1"].font = Font(bold=True, size=14)
    summary["A3"] = "Generated at"
    summary["B3"] = datetime.now(timezone.utc).replace(tzinfo=None)
    summary["A4"] = "Stale threshold (days)"
    summary["B4"] = stale_days
    summary["A6"] = "Total shipments"
    summary["B6"] = total
    summary["A7"] = "Matched"
    summary["B7"] = matched
    summary["A8"] = "Missing (past stale)"
    summary["B8"] = missing
    summary["A9"] = "Pending (in transit)"
    summary["B9"] = pending
    summary["A10"] = "Orphan receipts"
    summary["B10"] = orphans
    summary["A11"] = "Low-confidence parses"
    summary["B11"] = low_conf

    _write_sheet(wb, "All Shipments", rows, SHIPMENT_COLUMNS)
    _write_sheet(wb, "Missing Orders",
                 sorted([r for r in rows if r.match and r.match.flagged_reason == FLAG_MISSING],
                        key=lambda r: r.shipment.ship_date or datetime.min),
                 SHIPMENT_COLUMNS)
    _write_sheet(wb, "Pending (In Transit)",
                 [r for r in rows if r.match and r.match.flagged_reason == FLAG_PENDING],
                 SHIPMENT_COLUMNS)
    _write_sheet(wb, "Low-Confidence Parses",
                 [r for r in rows if (r.shipment.confidence or 0) < 0.6],
                 SHIPMENT_COLUMNS)
    _write_orphans(wb, session)

    wb.save(output)
    return output


def _write_sheet(wb: Workbook, title: str, rows: list[ShipmentRow], columns: list[str]):
    ws = wb.create_sheet(title)
    _write_header(ws, columns)
    data_rows = _strip_tz([r.as_tuple() for r in rows])
    for r_idx, tup in enumerate(data_rows, start=2):
        for c_idx, val in enumerate(tup, start=1):
            ws.cell(row=r_idx, column=c_idx, value=val)

    # Conditional formatting on `flag` column.
    if data_rows:
        flag_col = columns.index("flag") + 1
        flag_letter = get_column_letter(flag_col)
        last_row = len(data_rows) + 1
        rng = f"{flag_letter}2:{flag_letter}{last_row}"
        ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=[f'"{FLAG_MISSING}"'], fill=RED))
        ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=[f'"{FLAG_PENDING}"'], fill=YELLOW))
        status_col = columns.index("match_status") + 1
        status_letter = get_column_letter(status_col)
        rng2 = f"{status_letter}2:{status_letter}{last_row}"
        ws.conditional_formatting.add(rng2, CellIsRule(operator="equal", formula=[f'"{MATCH_TRACKING}"'], fill=GREEN))
    _autosize(ws)


def _write_orphans(wb: Workbook, session: Session):
    ws = wb.create_sheet("Orphan Receipts")
    cols = ["received_at", "tracking_number_normalized", "carrier", "sku", "notes", "email_id"]
    _write_header(ws, cols)
    for idx, r in enumerate(orphan_receipts(session), start=2):
        row = (
            r.received_at.replace(tzinfo=None) if r.received_at and r.received_at.tzinfo else r.received_at,
            r.tracking_number_normalized,
            r.carrier,
            r.sku,
            r.notes,
            r.email_id,
        )
        for c_idx, val in enumerate(row, start=1):
            ws.cell(row=idx, column=c_idx, value=val)
    _autosize(ws)


def write_csv(session: Session, output: Path) -> Path:
    rows = _gather(session)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(SHIPMENT_COLUMNS)
        for r in _strip_tz([row.as_tuple() for row in rows]):
            w.writerow(r)
    return output
