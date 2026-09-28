package com.elabrador.mobilenavigation;
import org.junit.Test;
import static org.junit.Assert.*;

public class DeclinationPositionTest {
    @Test public void coarseNetworkFixCanCorrectNorthButCannotPassNavigationGate(){
        DeclinationPosition p=new DeclinationPosition();
        long now=10_000_000_000L;
        assertTrue(p.accept(40,116,33,now,now,"network"));
        assertTrue(p.fresh(now));
        OutdoorFixGate nav=new OutdoorFixGate();
        assertFalse(nav.accept(40,116,33,now,now));
        assertTrue(p.accept(40,116,40.2f,now+1,now+1,"gps"));
        assertFalse(nav.accept(40,116,40.2f,now+1,now+1));
    }
    @Test public void recentCachedPositionIsAcceptedButStaleInvalidAndReorderedAreRejected(){
        DeclinationPosition p=new DeclinationPosition();
        long fix=10_000_000_000L,now=fix+120_000_000_000L;
        assertTrue(p.accept(40,116,33,fix,now,"network"));
        assertFalse(p.accept(40,116,33,fix-1,now,"gps"));
        assertFalse(p.accept(Double.NaN,116,33,fix+1,now,"gps"));
        assertFalse(p.accept(40,181,33,fix+1,now,"gps"));
        assertFalse(p.accept(40,116,Float.NaN,fix+1,now,"gps"));
        assertFalse(p.accept(40,116,5001,fix+1,now,"gps"));
        assertFalse(p.accept(40,116,33,now+1,now,"gps"));
        assertTrue(p.fresh(fix+DeclinationPosition.MAX_AGE_NANOS));
        assertFalse(p.fresh(fix+DeclinationPosition.MAX_AGE_NANOS+1));
        assertFalse(new DeclinationPosition().accept(40,116,33,fix,fix+DeclinationPosition.MAX_AGE_NANOS+1,"network"));
    }
}
