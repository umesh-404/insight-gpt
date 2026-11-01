"""How a figure is rendered into prose, in one place.

Every number this service writes into a sentence — an insight headline, a
forecast summary, a report line — goes through :func:`format_value`. It existed
twice before (once in the anomaly detector, once in the forecast engine), and
the copies had already drifted apart on decimal places; a single definition is
the only way a headline and the tile beside it cannot disagree.

The scale is Indian: lakh and crore, rupee symbol, and 2-3-3 digit grouping.
This deliberately mirrors ``web/lib/utils.ts``, which formats the same figures
for the UI with ``Intl.NumberFormat('en-IN')`` and three significant digits —
the two must be read together and changed together.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

RUPEE = "₹"

_CRORE = 1_00_00_000
_LAKH = 1_00_000


def significant3(value: float) -> str:
    """Three significant digits with trailing zeros trimmed: ``13.0`` -> ``13``.

    Matches ``maximumSignificantDigits: 3`` on the UI side. Compacting to whole
    units instead would render 13,00,000 and 11,52,000 identically, which erases
    exactly the change a before/after pair exists to show.
    """
    text = f"{value:.3g}"
    if "." in text and "e" not in text:
        text = text.rstrip("0").rstrip(".")
    return text


def indian_group(value: float, decimals: int = 0) -> str:
    """Group digits on the Indian scale: ``1152000`` -> ``11,52,000``.

    The last three digits group together and every two group before that, so
    Python's ``{:,}`` (which groups in threes throughout) cannot be used.
    """
    sign = "-" if value < 0 else ""
    whole, _, frac = f"{abs(value):.{decimals}f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        pairs: list[str] = []
        while len(head) > 2:
            pairs.insert(0, head[-2:])
            head = head[:-2]
        if head:
            pairs.insert(0, head)
        whole = ",".join([*pairs, tail])
    return sign + whole + (f".{frac}" if frac else "")


def _round_sig3(value: float) -> Decimal:
    """Round to three significant digits, half-up, as exact decimal arithmetic.

    ``f"{v:.3g}"`` rounds half-to-even on the binary value, while the UI's
    ``Intl.NumberFormat`` rounds half-up (half-expand) on the decimal value, so
    the two disagree on an exact half such as ``9.995``. Deciding the digits
    here, in base 10 and half-up, removes that class of disagreement.
    """
    d = Decimal(repr(float(value)))
    if d == 0:
        return Decimal(0)
    quantum = Decimal(1).scaleb(d.adjusted() - 2)
    return d.quantize(quantum, rounding=ROUND_HALF_UP)


def _plain(d: Decimal) -> str:
    """A rounded Decimal as text with no exponent and no trailing zeros."""
    return format(d.normalize(), "f")


def format_value(value: float, fmt: str) -> str:
    """Render ``value`` for prose according to a governed metric's ``format``."""
    if fmt == "currency":
        sign = "-" if value < 0 else ""
        magnitude = abs(value)
        # ROUND FIRST, PROMOTE SECOND. Picking the unit from the raw magnitude
        # and rounding afterwards made 99,99,999 render as "₹100L" here while
        # the UI — which rounds to three significant digits before choosing a
        # unit — showed "₹1Cr" for the same figure in the chart beside it.
        # Everything from ₹99,95,000 up rounds to 100 lakh, i.e. one crore.
        lakhs = _round_sig3(magnitude / _LAKH)
        if lakhs >= 100:
            return f"{sign}{RUPEE}{_plain(_round_sig3(magnitude / _CRORE))}Cr"
        if lakhs >= 1:
            return f"{sign}{RUPEE}{_plain(lakhs)}L"
        return f"{sign}{RUPEE}{indian_group(magnitude)}"
    if fmt == "percent":
        return f"{value * 100:.1f}%"
    if fmt == "integer":
        return indian_group(value)
    return indian_group(value, 2)
