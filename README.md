# KNET Order Reconciler

A personal CLI tool that compares retailer shipping emails in my Gmail against KNET "package received" emails, and flags pairs I shipped to the KNET warehouse that never got checked in.

Match key: carrier tracking number (with order # and SKU as secondary signals).

## What it does

1. Reads my Gmail via the Gmail API (read-only OAuth).
2. Parses retailer order/shipping emails addressed to my KNET warehouse and extracts: retailer, order #, dates, item/SKU, size, price, carrier, tracking #.
3. Parses KNET "received" emails and extracts: receipt date, tracking #, item info.
4. Reconciles the two sides on tracking number.
5. Exports `reconciliation.xlsx` (or CSV) with sheets for all shipments, missing orders, pending in-transit, orphan receipts, and low-confidence parses.

Everything stays local — Gmail content is cached in a local SQLite DB and never leaves the machine.

## Setup

### 1. Install Python 3.11+ and uv

```powershell
winget install --id Python.Python.3.12 -e
python -m pip install --user uv
```

### 2. Clone and install dependencies

```powershell
cd "knet_reconciler"
uv venv
uv pip install -e ".[dev]"
```

### 3. Enable the Gmail API and download credentials

1. Go to <https://console.cloud.google.com/>, create a project (or reuse one).
2. Enable the **Gmail API** for the project.
3. Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
4. Application type: **Desktop app**. Name it anything.
5. Download the JSON, rename it to `credentials.json`, drop it in the repo root.
6. Under **OAuth consent screen**, add your own Gmail address as a test user (no need to publish the app).

### 4. Configure

Copy `.env.example` to `.env` (defaults are fine). Open `config.toml` and fill in `[knet].warehouse_address_lines` with your KNET warehouse address lines (one per line). At least one of these lines must appear in a retailer email body for the tool to count that email as outbound-to-KNET.

### 5. First-run OAuth

```powershell
knet-reconcile auth
```

A browser opens, you grant **read-only** Gmail access, and a `token.json` is written next to `credentials.json`. The token refreshes automatically afterward.

## Daily usage

```powershell
knet-reconcile run
```

This runs the full pipeline (fetch new emails → parse → reconcile → export) and writes `reconciliation.xlsx`. The **Missing Orders** sheet is the actionable list to send to `support@knetgroup.com`.

Other commands:

```powershell
knet-reconcile fetch [--since 2026-01-01]   # pull new emails into local DB
knet-reconcile parse                        # re-parse cached emails after a parser change
knet-reconcile reconcile                    # re-run matching
knet-reconcile report --xlsx out.xlsx --open
knet-reconcile review                       # interactively resolve low-confidence parses / orphan receipts
```

## Troubleshooting

- **Updated a parser and want to re-run it against already-fetched emails?** `knet-reconcile parse` re-parses every cached email; no need to re-hit Gmail.
- **OAuth token expired or revoked?** Delete `token.json` and re-run `knet-reconcile auth`.
- **An email is being misclassified?** Add an entry under `[retailers.overrides]` in `config.toml` to force a specific parser for that sender domain.

## Privacy

- Gmail scope: `gmail.readonly` only. The tool can read but never write to your inbox.
- The SQLite DB (`knet_reconciler.sqlite`) caches email bodies. The exported xlsx contains email-derived fields (tracking #s, addresses, SKUs). Both are git-ignored by default — don't commit them.

## Layout

```
knet_reconciler/
├── pyproject.toml
├── config.toml
├── .env.example
├── src/knet_reconciler/
│   ├── cli.py
│   ├── gmail_client.py
│   ├── db.py
│   ├── tracking.py
│   ├── reconcile.py
│   ├── export.py
│   └── parsers/
│       ├── base.py
│       ├── generic.py
│       ├── knet.py
│       └── ... (per-retailer parsers)
└── tests/
```
