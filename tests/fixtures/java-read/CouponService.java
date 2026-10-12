package demo;

public class CouponService {
    private final CouponMapper mapper;

    public CouponService(CouponMapper mapper) {
        this.mapper = mapper;
    }

    public void claim(long userId, long couponId) {
        if (mapper.hasClaim(userId, couponId)) {
            throw new IllegalStateException("Already claimed");
        }
        mapper.insertClaim(userId, couponId);
    }
}
