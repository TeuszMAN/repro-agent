"""Order total: items subtotal, minus coupon discount, plus delivery fee."""

from dataclasses import dataclass
from decimal import Decimal

from pedido.coupon import Coupon, discount_for
from pedido.delivery import delivery_fee
from pedido.money import ZERO, round_cents

MAX_QUANTITY = 99


@dataclass(frozen=True)
class Item:
    name: str
    unit_price: Decimal
    quantity: int

    def __post_init__(self) -> None:
        if not isinstance(self.quantity, int) or isinstance(self.quantity, bool):
            raise ValueError("quantity must be a whole number")
        if self.quantity < 1:
            raise ValueError("quantity must be at least 1")
        if self.quantity > MAX_QUANTITY:
            raise ValueError(f"quantity cannot be above {MAX_QUANTITY}")
        if self.unit_price < 0:
            raise ValueError("unit price cannot be negative")

    @property
    def total(self) -> Decimal:
        return round_cents(self.unit_price * self.quantity)


@dataclass(frozen=True)
class OrderSummary:
    subtotal: Decimal
    discount: Decimal
    delivery_fee: Decimal
    total: Decimal


def calculate_order(items: list[Item], coupon: Coupon | None = None) -> OrderSummary:
    """Calculates the order.

    The coupon applies to the items subtotal only, never to the delivery fee.
    Free delivery is decided by the subtotal before the discount.
    An empty cart costs nothing: no items, no delivery.
    """
    if not items:
        return OrderSummary(subtotal=ZERO, discount=ZERO, delivery_fee=ZERO, total=ZERO)

    subtotal = sum((item.total for item in items), ZERO)
    discount = discount_for(coupon, subtotal)
    fee = delivery_fee(subtotal)
    total = subtotal - discount + fee
    return OrderSummary(subtotal=subtotal, discount=discount, delivery_fee=fee, total=total)
