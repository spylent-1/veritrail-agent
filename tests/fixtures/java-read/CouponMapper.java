package demo;

public interface CouponMapper {
    boolean hasClaim(long userId, long couponId);
    void insertClaim(long userId, long couponId);
}
