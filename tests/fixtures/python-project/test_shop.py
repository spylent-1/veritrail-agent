import pytest
from shop import can_claim, discounted_price, remaining_stock


def test_discount():
    assert discounted_price(100, 30) == 70
    assert discounted_price(20, 30) == 0
    with pytest.raises(ValueError):
        discounted_price(-1, 0)


def test_claim():
    assert not can_claim(0, 1)
    assert not can_claim(1, 0)
    assert can_claim(1, 2)


def test_stock():
    assert remaining_stock(10, 3) == 7
    assert remaining_stock(10, 10) == 0
    with pytest.raises(ValueError):
        remaining_stock(10, 11)
