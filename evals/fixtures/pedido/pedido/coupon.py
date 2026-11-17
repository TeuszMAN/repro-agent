"""Discount coupons: a percentage of the subtotal or a fixed amount."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from pedido.money import ZERO, round_cents


class CouponKind(StrEnum):
    PERCENTAGE = "percentage"
    FIXED = "fixed"


@dataclass(frozen=True)
class Coupon:
    code: str
    kind: CouponKind
    value: Decimal  # percent (10 means 10%) or an amount in reais

    def __post_init__(self) -> None:
        if self.value <= 0:
            raise ValueError("coupon value must be positive")
        if self.kind is CouponKind.PERCENTAGE and self.value > 100:
            raise ValueError("percentage coupon cannot be above 100%")


def discount_for(coupon: Coupon | None, amount: Decimal) -> Decimal:
    """Discount that `coupon` gives on `amount`. It is never larger than `amount`."""
    if coupon is None:
        return ZERO
    if coupon.kind is CouponKind.PERCENTAGE:
        discount = round_cents(amount * coupon.value / 100)
    else:
        discount = coupon.value
    return min(discount, amount)
