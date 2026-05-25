"""Configuration loader. Reads .env + config.toml into a typed object."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class KnetConfig:
    warehouse_address_lines: list[str] = field(default_factory=list)
    sender_domain: str = "knetgroup.com"


@dataclass(frozen=True)
class ReconcileConfig:
    stale_days: int = 14
    lookback_days: int = 180


@dataclass(frozen=True)
class GmailConfig:
    outbound_query: str = (
        '(subject:(shipped OR "your order" OR "order confirmation" OR tracking '
        'OR dispatched) OR from:(noreply OR no-reply OR shipping OR orders)) '
        '-from:knetgroup.com newer_than:180d'
    )
    knet_query: str = "from:knetgroup.com newer_than:180d"


@dataclass(frozen=True)
class RetailerOverride:
    from_domain: str
    parser: str


@dataclass(frozen=True)
class Paths:
    credentials: Path
    token: Path
    db: Path
    config: Path


@dataclass(frozen=True)
class Config:
    knet: KnetConfig
    reconcile: ReconcileConfig
    gmail: GmailConfig
    retailer_overrides: list[RetailerOverride]
    paths: Paths


def _resolve(env_var: str, default: str) -> Path:
    return Path(os.environ.get(env_var, default)).expanduser().resolve()


def load_config(config_path: Path | None = None) -> Config:
    load_dotenv()

    paths = Paths(
        credentials=_resolve("GMAIL_CREDENTIALS_PATH", "./credentials.json"),
        token=_resolve("GMAIL_TOKEN_PATH", "./token.json"),
        db=_resolve("DB_PATH", "./knet_reconciler.sqlite"),
        config=_resolve("CONFIG_PATH", "./config.toml"),
    )
    cfg_file = config_path or paths.config

    data: dict = {}
    if cfg_file.exists():
        with cfg_file.open("rb") as fh:
            data = tomllib.load(fh)

    knet_d = data.get("knet", {})
    rec_d = data.get("reconcile", {})
    gmail_d = data.get("gmail", {})
    overrides_d = data.get("retailers", {}).get("overrides", [])

    return Config(
        knet=KnetConfig(
            warehouse_address_lines=list(knet_d.get("warehouse_address_lines", [])),
            sender_domain=knet_d.get("sender_domain", "knetgroup.com"),
        ),
        reconcile=ReconcileConfig(
            stale_days=int(rec_d.get("stale_days", 14)),
            lookback_days=int(rec_d.get("lookback_days", 180)),
        ),
        gmail=GmailConfig(
            outbound_query=gmail_d.get("outbound_query", GmailConfig.__dataclass_fields__["outbound_query"].default),
            knet_query=gmail_d.get("knet_query", GmailConfig.__dataclass_fields__["knet_query"].default),
        ),
        retailer_overrides=[
            RetailerOverride(from_domain=o["from_domain"], parser=o["parser"])
            for o in overrides_d
        ],
        paths=paths,
    )
