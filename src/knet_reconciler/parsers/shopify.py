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
from .generic import _text_from_email, address_matches

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
        # Prefer the from-domain hostname (often the shop's own name) over the
        # generic parser's first-label heuristic.
        result = super().parse(email)
        if result is None:
            return None
        if email.from_domain:
            parts = email.from_domain.split(".")
            if len(parts) >= 2:
                result.retailer = parts[-2]
        return result
