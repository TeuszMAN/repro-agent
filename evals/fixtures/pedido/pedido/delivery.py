"""Delivery fee rules."""

from decimal import Decimal

from pedido.money import ZERO

DELIVERY_FEE = Decimal("7.90")
FREE_DELIVERY_MINIMUM = Decimal("100.00")


def delivery_fee(subtotal: Decimal) -> Decimal:
    """Delivery is free when the items subtotal reaches FREE_DELIVERY_MINIMUM (inclusive)."""
    if subtotal >= FREE_DELIVERY_MINIMUM:
        return ZERO
    return DELIVERY_FEE
