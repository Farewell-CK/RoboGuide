package com.elabrador.mobilenavigation;

/** Coarse position for geomagnetic declination only. Never a navigation location. */
final class DeclinationPosition {
    static final long MAX_AGE_NANOS=3_600_000_000_000L;
    private long fixNanos=-1;
    double latitude,longitude;
    String provider="";
    boolean accept(double lat,double lon,float accuracy,long fix,long now,String source){
        if(!Double.isFinite(lat)||Math.abs(lat)>90||!Double.isFinite(lon)||Math.abs(lon)>180
                ||!Float.isFinite(accuracy)||accuracy<=0||accuracy>5000
                ||fix<=0||now<fix||now-fix>MAX_AGE_NANOS||fix<=fixNanos)return false;
        latitude=lat;longitude=lon;fixNanos=fix;provider=source==null?"unknown":source;
        return true;
    }
    boolean fresh(long now){return fixNanos>0&&now>=fixNanos&&now-fixNanos<=MAX_AGE_NANOS;}
}
