# KNET sample emails

This folder is intentionally empty.

The KNET parser is built on configurable heuristics until we have real samples
to tune against. Drop 2–3 saved `.eml` files (or HTML bodies) here, then update
`tests/test_parsers.py` to load them and assert the extracted fields.

To save a Gmail message as `.eml`:
1. Open the message in Gmail.
2. Click the three-dot menu → **Download message** (saves a `.eml` file).
3. Drop it in this folder and reference it from a test.

Scrub any addresses/tracking-numbers you don't want committed; the parser
only cares about *structure*, not the literal numbers.
