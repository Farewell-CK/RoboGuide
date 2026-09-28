package com.elabrador.mobilenavigation;
import org.junit.Test;
import static org.junit.Assert.*;
public class PlaceSearchPositionTest {
    @Test public void networkAndCoarseGpsCanSearchButCannotAuthorizeNavigation(){
        long now=10_000_000_000L;
        for(float acc:new float[]{33,40.2f,1000}){
            assertTrue(PlaceSearchPosition.usable(40,116,acc,now,now));
            assertFalse(new OutdoorFixGate().accept(40,116,acc,now,now));
        }
    }
    @Test public void staleFutureAndInvalidSearchCentersAreRejected(){
        long fix=10_000_000_000L;
        assertTrue(PlaceSearchPosition.usable(40,116,33,fix,fix+300_000_000_000L));
        assertFalse(PlaceSearchPosition.usable(40,116,33,fix,fix+300_000_000_001L));
        assertFalse(PlaceSearchPosition.usable(40,116,33,fix,fix-1));
        assertFalse(PlaceSearchPosition.usable(40,116,1001,fix,fix));
        assertFalse(PlaceSearchPosition.usable(Double.NaN,116,33,fix,fix));
        assertFalse(PlaceSearchPosition.usable(40,181,33,fix,fix));
    }
}
