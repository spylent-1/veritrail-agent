package demo;

public class ShopService {
    public long discountedPrice(long price, long discount) {
        if (price < 0 || discount < 0) {
            throw new IllegalArgumentException("Amounts must be nonnegative");
        }
        return Math.max(0, price - discount);
    }

    public boolean canClaim(long userId, int remainingStock) {
        return userId > 0 && remainingStock > 0;
    }

    public int remainingStock(int stock, int quantity) {
        if (quantity < 0 || quantity > stock) {
            throw new IllegalArgumentException("Invalid quantity");
        }
        return stock - quantity;
    }
}
