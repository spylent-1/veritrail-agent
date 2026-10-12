def discounted_price(price: int, discount: int) -> int:
    if price < 0 or discount < 0:
        raise ValueError("Amounts must be nonnegative")
    return max(0, price - discount)


def can_claim(user_id: int, remaining_stock: int) -> bool:
    return user_id > 0 and remaining_stock > 0


def remaining_stock(stock: int, quantity: int) -> int:
    if quantity < 0 or quantity > stock:
        raise ValueError("Invalid quantity")
    return stock - quantity
