package com.elabrador.mobilenavigation;

/** Approximate search center only; it cannot authorize navigation guidance. */
final class PlaceSearchPosition {
    static boolean usable(double lat,double lon,float accuracy,long fixNanos,long nowNanos){
        return Double.isFinite(lat)&&Math.abs(lat)<=90&&Double.isFinite(lon)&&Math.abs(lon)<=180
                &&Float.isFinite(accuracy)&&accuracy>0&&accuracy<=1000
                &&fixNanos>0&&nowNanos>=fixNanos&&nowNanos-fixNanos<=300_000_000_000L;
    }
}
