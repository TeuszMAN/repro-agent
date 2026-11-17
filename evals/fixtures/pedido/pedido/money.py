"""Money helpers. All amounts are Decimal, never float."""

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def round_cents(value: Decimal) -> Decimal:
    """Rounds to cents, half up: 1.005 becomes 1.01, like a cash register."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)
