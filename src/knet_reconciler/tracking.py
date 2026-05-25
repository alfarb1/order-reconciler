"""Carrier detection and tracking number extraction / normalization.

Everything downstream matches on `normalize(number)` — strip whitespace and
dashes, uppercase. Carriers are detected from the number format itself and
from URLs in the email body.
"""
from __future__ import annotations

import re
from typing import Iterable, NamedTuple
from urllib.parse import parse_qs, urlparse

Carrier = str  # "ups" | "fedex" | "usps" | "dhl" | "unknown"

UPS = "ups"
FEDEX = "fedex"
USPS = "usps"
DHL = "dhl"
UNKNOWN = "unknown"


class Tracking(NamedTuple):
    carrier: Carrier
    number: str  # normalized


# --- Per-carrier patterns ----------------------------------------------------
# Word boundaries (\b) keep us from grabbing the middle of a larger digit run.

_UPS_RE = re.compile(r"\b(1Z[0-9A-Z]{16})\b")

# USPS: 20, 22, or 26 numeric digits, plus 13-char international format
# (2 letters + 9 digits + "US"). Most retailer emails show one of these.
_USPS_NUMERIC_RE = re.compile(r"\b(\d{20}|\d{22}|\d{26})\b")
_USPS_INTL_RE = re.compile(r"\b([A-Z]{2}\d{9}US)\b")
_USPS_PREFIX_DIGITS = ("92", "93", "94", "95", "96")  # IMpb prefix digits

# FedEx: 12, 15, 20, 22 numeric digits. Overlaps with USPS on 20/22 — we use
# context (the URL host, or surrounding text) to disambiguate.
_FEDEX_RE = re.compile(r"\b(\d{12}|\d{15}|\d{20}|\d{22})\b")

_DHL_PREFIX_RE = re.compile(r"\b(JJD\d{10,18}|JD\d{12,16})\b")
_DHL_BARE_RE = re.compile(r"\b(\d{10,11})\b")
_DHL_RE = re.compile(r"\b(JJD\d{10,18}|JD\d{12,16}|\d{10,11})\b")

# FedEx SmartPost / Ground Economy hands off the last mile to USPS. The physical label
# carries a 34-digit USPS IMpb GS1-128 barcode starting with `96`, with the 12-digit
# FedEx tracking embedded as the last 12 digits. KNET's receiving station scans the
# IMpb (so KNET emails contain the 34-digit form); retailers email only the underlying
# FedEx 12-digit form. We normalize both to the embedded FedEx tracking so they match.
_IMPB34_RE = re.compile(r"\b(96\d{32})\b")


def _embedded_fedex_from_impb34(n: str) -> str | None:
    if not n or len(n) != 34 or not n.startswith("96") or not n.isdigit():
        return None
    return n[-12:]

# Carrier-context patterns: the carrier name or hostname appearing in the email
# body. Bare-numeric DHL/FedEx matches require this — without it, any 10-15 digit
# run (order IDs, phone numbers, template placeholders) gets misclassified.
_CARRIER_CONTEXT_RE = {
    UPS:   re.compile(r"\bups\b|ups\.com", re.IGNORECASE),
    FEDEX: re.compile(r"\bfedex\b|fedex\.com", re.IGNORECASE),
    USPS:  re.compile(r"\busps\b|usps\.com|united\s+states\s+postal", re.IGNORECASE),
    DHL:   re.compile(r"\bdhl\b|dhl\.com|mydhl", re.IGNORECASE),
}


def _has_context(text: str, carrier: Carrier) -> bool:
    pat = _CARRIER_CONTEXT_RE.get(carrier)
    return bool(pat and pat.search(text))


def _is_obvious_placeholder(n: str) -> bool:
    """Reject template values like '666666666666668' that show up in HTML emails as image
    dimensions, CSS hex fragments, or actual placeholder copy. Only applied during the
    free-text scan, not when the carrier URL itself names the number."""
    if not n or not n.isdigit() or len(n) < 8:
        return False
    most_common = max(n.count(d) for d in set(n))
    if most_common / len(n) >= 0.7:
        return True
    for i in range(len(n) - 7):
        if len(set(n[i:i + 8])) == 1:
            return True
    return False

# Carrier hostnames found in tracking links.
_HOST_CARRIER = {
    "ups.com": UPS,
    "wwwapps.ups.com": UPS,
    "www.ups.com": UPS,
    "fedex.com": FEDEX,
    "www.fedex.com": FEDEX,
    "usps.com": USPS,
    "tools.usps.com": USPS,
    "www.usps.com": USPS,
    "dhl.com": DHL,
    "www.dhl.com": DHL,
    "mydhl.express.dhl": DHL,
}

# Common URL query keys that carry the tracking number.
_URL_KEYS = ("trknbr", "tracknumbers", "tracknum", "tracking", "trackingnumber",
             "tracknumber", "qtc_tlabels1", "tLabels", "label", "labels", "id")

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def normalize(number: str) -> str:
    """Strip spaces/dashes, uppercase. Use the result as the match key."""
    if number is None:
        return ""
    return re.sub(r"[\s\-]", "", number).upper()


def detect_carrier_from_number(number: str) -> Carrier:
    n = normalize(number)
    if _UPS_RE.fullmatch(n):
        return UPS
    if _USPS_INTL_RE.fullmatch(n):
        return USPS
    if _DHL_RE.fullmatch(n):
        # JD/JJD prefix wins outright. Pure-numeric 10/11 also DHL by spec.
        return DHL
    if n.isdigit():
        if len(n) in (20, 22, 26) and n[:2] in _USPS_PREFIX_DIGITS:
            return USPS
        if len(n) in (12, 15):
            return FEDEX
        if len(n) in (20, 22):
            # Ambiguous between USPS and FedEx without URL context.
            # Default to USPS for these lengths since they are USPS's IMpb
            # canonical lengths; FedEx callers should use URL context.
            return USPS
    return UNKNOWN


def _extract_from_url(url: str) -> list[Tracking]:
    """Pull a tracking number out of a carrier URL."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return []
    host = (parsed.hostname or "").lower()
    carrier = _HOST_CARRIER.get(host, UNKNOWN)
    if carrier == UNKNOWN:
        return []

    qs = parse_qs(parsed.query, keep_blank_values=False)
    candidates: list[str] = []
    for key in _URL_KEYS:
        for k, vs in qs.items():
            if k.lower() == key.lower():
                candidates.extend(vs)

    # FedEx sometimes puts the number in the path: /fedextrack/?trknbr=...
    # USPS sometimes uses /go/TrackConfirmAction?... — handled above.
    # UPS uses /track?loc=...&tracknum=... — handled above.
    if not candidates:
        # Last resort: scan the URL itself for a recognizable number.
        return _scan_text(url, hint=carrier)

    out: list[Tracking] = []
    for raw in candidates:
        # Comma- or space-separated lists do appear (FedEx qtc_tLabels).
        for piece in re.split(r"[,\s]+", raw):
            piece = piece.strip()
            if not piece:
                continue
            n = normalize(piece)
            if _looks_like_tracking(n):
                out.append(Tracking(carrier=carrier, number=n))
    return out


def _looks_like_tracking(n: str) -> bool:
    if _UPS_RE.fullmatch(n) or _USPS_INTL_RE.fullmatch(n) or _DHL_RE.fullmatch(n):
        return True
    if n.isdigit() and len(n) in (10, 11, 12, 15, 20, 22, 26):
        return True
    return False


def _scan_text(text: str, hint: Carrier = UNKNOWN) -> list[Tracking]:
    found: list[Tracking] = []
    seen: set[str] = set()

    def add(carrier: Carrier, raw: str):
        n = normalize(raw)
        if n in seen or _is_obvious_placeholder(n):
            return
        seen.add(n)
        detected = detect_carrier_from_number(n)
        chosen = detected if detected != UNKNOWN else (carrier if carrier != UNKNOWN else hint)
        found.append(Tracking(carrier=chosen, number=n))

    # 34-digit FedEx SmartPost IMpb — emit only the embedded FedEx tracking. This makes
    # KNET's IMpb-scanned receipts match the retailer-emailed FedEx 12-digit form.
    for m in _IMPB34_RE.finditer(text):
        fedex = _embedded_fedex_from_impb34(m.group(1))
        if fedex:
            add(FEDEX, fedex)

    # Unambiguous formats (strict regex + prefix) — always accept.
    for m in _UPS_RE.finditer(text):
        add(UPS, m.group(1))
    for m in _USPS_INTL_RE.finditer(text):
        add(USPS, m.group(1))
    for m in _DHL_PREFIX_RE.finditer(text):
        add(DHL, m.group(1))

    # Bare 10-11 digit "DHL" — require DHL context to avoid catching phone numbers / order IDs.
    if hint == DHL or _has_context(text, DHL):
        for m in _DHL_BARE_RE.finditer(text):
            add(DHL, m.group(1))

    # USPS IMpb 20/22/26-digit — only if the prefix is in the IMpb set OR USPS context present.
    for m in _USPS_NUMERIC_RE.finditer(text):
        n = m.group(1)
        if n[:2] in _USPS_PREFIX_DIGITS or hint == USPS or _has_context(text, USPS):
            add(USPS, n)

    # FedEx 12/15/20/22 digit — require FedEx context. The 20/22 lengths overlap with USPS so
    # we still skip anything already captured.
    if hint == FEDEX or _has_context(text, FEDEX):
        for m in _FEDEX_RE.finditer(text):
            if normalize(m.group(1)) not in seen:
                add(FEDEX, m.group(1))
    return found


def extract_tracking(text: str) -> list[Tracking]:
    """Find all tracking numbers in `text`.

    Strategy: scan URLs first (highest signal — carrier is unambiguous from
    the host), then scan the visible text for anything we missed.
    """
    if not text:
        return []

    results: list[Tracking] = []
    seen: set[tuple[Carrier, str]] = set()

    def push(t: Tracking):
        key = (t.carrier, t.number)
        if key in seen:
            return
        # Also dedupe by number alone — same number from text + URL is one hit.
        for c, n in seen:
            if n == t.number:
                return
        seen.add(key)
        results.append(t)

    for url_match in _URL_RE.finditer(text):
        for t in _extract_from_url(url_match.group(0)):
            push(t)

    for t in _scan_text(text):
        push(t)

    return results
