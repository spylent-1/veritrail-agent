package demo;

public class CouponController {
    private final CouponService service;

    public CouponController(CouponService service) {
        this.service = service;
    }

    public void claim(long userId, long couponId) {
        service.claim(userId, couponId);
    }
}
