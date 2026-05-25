"""Shopify-pattern parser: catches the long-tail of small reseller shops.

Sits between named retailers (priority 50) and the heuristic generic (priority
1000). Triggers when the email looks Shopify-shaped: from a `shop.*` /
`mail.*` subdomain, mentions an order # in classic Shopify format, or
references a `myshopify.com` tracking link.
"""
from __future__ import annotations

import re

from ..db import Email
from ._named import NamedRetailerParser
from .generic import _text_from_email, address_matches, retailer_from_email

SHOPIFY_HOSTS = ("myshopify.com", "shopifyemail.com", "shopify.com")
SHOPIFY_ORDER_RE = re.compile(r"#\d{4,7}\b")


class ShopifyParser(NamedRetailerParser):
    name = "shopify"
    retailer = "Shopify shop"
    domains = ()  # we match by content, not strictly by sender
    confidence_floor = 0.7
    priority = 200  # ahead of generic, behind named retailers

    def matches(self, email: Email) -> bool:
        dom = (email.from_domain or "").lower()
        text = _text_from_email(email)
        html = email.raw_html or ""
        looks_shopify = (
            any(h in dom for h in SHOPIFY_HOSTS)
            or any(h in html.lower() for h in SHOPIFY_HOSTS)
            or bool(SHOPIFY_ORDER_RE.search(text))
        )
        if not looks_shopify:
            return False
        return address_matches(text, self._address_lines)

    def parse(self, email: Email):
        result = super().parse(email)
        if result is None:
            return None
        # Prefer the display name from the From header — Shopify-hosted shops always
        # set it (e.g. "Reynolds & Sons" <store+...@t.shopifyemail.com>) and the
        # domain alone says nothing about the brand.
        name = retailer_from_email(email)
        if name:
            result.retailer = name
        return result
