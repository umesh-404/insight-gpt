"""Currency rendering must agree with the UI formatter, digit for digit.

``app/formatting.py`` writes figures into prose (headlines, forecast summaries,
report lines) and ``web/lib/utils.ts`` writes the same figures into the chart
beside that prose. The UI rounds to three significant digits and *then* picks a
unit; the backend used to pick the unit from the raw magnitude and round
afterwards, so every figure from ₹99,95,000 to ₹99,99,999 appeared as lakh in
one place and crore in the other — and an exact half such as 9,99,500 split on
half-even versus half-expand as well.

These cases pin the two boundaries. If the UI formatter changes, this file is
the record of what it has to keep matching.
"""

from __future__ import annotations

import pytest

from app.formatting import format_value

# (value, expected) — expectations are what Intl.NumberFormat('en-IN') with
# maximumSignificantDigits: 3 produces on the UI side.
BOUNDARY_CASES = [
    # Promotion to crore happens AFTER rounding: 99.99999 lakh rounds to 100
    # lakh, which is one crore, not "100L".
    (9_999_999, "₹1Cr"),
    (9_995_000, "₹1Cr"),
    (9_994_999, "₹99.9L"),
    # Half-expand, not half-even: 9.995 lakh is 10 lakh, not 9.99 lakh.
    (999_500, "₹10L"),
    (999_999, "₹10L"),
    # Unremarkable values must not move.
    (13_293_312, "₹1.33Cr"),
    (20_200_000, "₹2.02Cr"),
    (1_000_000, "₹10L"),
    (100_000, "₹1L"),
    (99_949, "₹99,949"),
    (0, "₹0"),
]


@pytest.mark.parametrize(("value", "expected"), BOUNDARY_CASES)
def test_currency_matches_the_ui_formatter(value: int, expected: str) -> None:
    assert format_value(value, "currency") == expected


@pytest.mark.parametrize(("value", "expected"), BOUNDARY_CASES)
def test_negative_currency_keeps_the_sign_outside_the_symbol(
    value: int, expected: str
) -> None:
    if value == 0:
        pytest.skip("negative zero is not a figure the engine produces")
    assert format_value(-value, "currency") == "-" + expected


def test_crore_and_lakh_never_disagree_across_the_boundary() -> None:
    """No figure may render as 100 lakh or more — that is a crore."""
    for value in range(9_994_000, 10_006_000, 7):
        rendered = format_value(value, "currency")
        if rendered.endswith("L"):
            lakhs = float(rendered.removeprefix("₹").removesuffix("L"))
            assert lakhs < 100, f"{value} rendered as {rendered}, which is a crore"
