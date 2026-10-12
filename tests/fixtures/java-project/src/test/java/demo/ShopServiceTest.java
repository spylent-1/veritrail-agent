package demo;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class ShopServiceTest {
    private final ShopService service = new ShopService();

    @Test void discountsAreSubtracted() {
        assertEquals(70, service.discountedPrice(100, 30));
        assertEquals(0, service.discountedPrice(20, 30));
        assertThrows(IllegalArgumentException.class, () -> service.discountedPrice(-1, 0));
    }

    @Test void claimsRequireUserAndStock() {
        assertFalse(service.canClaim(0, 1));
        assertFalse(service.canClaim(1, 0));
        assertTrue(service.canClaim(1, 2));
    }

    @Test void stockIsSubtractedAndValidated() {
        assertEquals(7, service.remainingStock(10, 3));
        assertEquals(0, service.remainingStock(10, 10));
        assertThrows(IllegalArgumentException.class, () -> service.remainingStock(10, 11));
    }
}
