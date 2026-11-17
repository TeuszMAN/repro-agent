from decimal import Decimal

import pytest
from pedido import Coupon, CouponKind, Item, calculate_order
from pedido.money import round_cents


def burger(quantity: int = 1) -> Item:
    return Item(name="burger", unit_price=Decimal("25.00"), quantity=quantity)


# Items


def test_item_total_is_price_times_quantity() -> None:
    assert Item(name="soda", unit_price=Decimal("6.50"), quantity=3).total == Decimal("19.50")


@pytest.mark.parametrize("quantity", [0, -1, 100])
def test_item_rejects_quantity_out_of_range(quantity: int) -> None:
    with pytest.raises(ValueError):
        Item(name="soda", unit_price=Decimal("6.50"), quantity=quantity)


def test_item_rejects_fractional_quantity() -> None:
    with pytest.raises(ValueError):
        Item(name="soda", unit_price=Decimal("6.50"), quantity=1.5)  # type: ignore[arg-type]


def test_item_rejects_negative_price() -> None:
    with pytest.raises(ValueError):
        Item(name="soda", unit_price=Decimal("-1.00"), quantity=1)


# Subtotal and delivery


def test_small_order_pays_delivery() -> None:
    order = calculate_order([burger(2)])

    assert order.subtotal == Decimal("50.00")
    assert order.delivery_fee == Decimal("7.90")
    assert order.total == Decimal("57.90")


def test_order_just_below_minimum_pays_delivery() -> None:
    order = calculate_order([Item(name="combo", unit_price=Decimal("99.99"), quantity=1)])

    assert order.delivery_fee == Decimal("7.90")


def test_big_order_has_free_delivery() -> None:
    order = calculate_order([burger(6)])

    assert order.subtotal == Decimal("150.00")
    assert order.delivery_fee == Decimal("0.00")
    assert order.total == Decimal("150.00")


# Coupons


def test_percentage_coupon() -> None:
    coupon = Coupon(code="DEZ", kind=CouponKind.PERCENTAGE, value=Decimal("10"))

    order = calculate_order([burger(8)], coupon)

    assert order.discount == Decimal("20.00")
    assert order.total == Decimal("180.00")


def test_fixed_coupon() -> None:
    coupon = Coupon(code="QUINZE", kind=CouponKind.FIXED, value=Decimal("15.00"))

    order = calculate_order([burger(2)], coupon)

    assert order.discount == Decimal("15.00")
    assert order.total == Decimal("42.90")


def test_fixed_coupon_never_discounts_more_than_the_subtotal() -> None:
    coupon = Coupon(code="VIP", kind=CouponKind.FIXED, value=Decimal("200.00"))

    order = calculate_order([burger(5)], coupon)

    assert order.discount == Decimal("125.00")
    assert order.total == Decimal("0.00")


def test_hundred_percent_coupon_is_allowed() -> None:
    coupon = Coupon(code="GRATIS", kind=CouponKind.PERCENTAGE, value=Decimal("100"))

    assert calculate_order([burger(6)], coupon).total == Decimal("0.00")


@pytest.mark.parametrize("value", ["0", "-5"])
def test_coupon_value_must_be_positive(value: str) -> None:
    with pytest.raises(ValueError):
        Coupon(code="X", kind=CouponKind.FIXED, value=Decimal(value))


# Rounding


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1.004", "1.00"), ("1.005", "1.01"), ("2.675", "2.68"), ("10", "10.00")],
)
def test_round_cents_rounds_half_up(value: str, expected: str) -> None:
    assert round_cents(Decimal(value)) == Decimal(expected)
