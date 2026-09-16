package com.elabrador.mobilenavigation;

/** Rejects stale, out-of-order and physically implausible pedestrian GPS fixes. */
final class OutdoorFixGate {
    private long lastNanos=-1;
    private double latitude,longitude;
    private float accuracy;
    String rejection="";
    boolean accept(double lat,double lon,float acc,long fixNanos,long nowNanos){
        if(!Double.isFinite(lat)||Math.abs(lat)>90 || !Double.isFinite(lon)||Math.abs(lon)>180
                || !Float.isFinite(acc)||acc<=0||acc>15){
            rejection="GPS 精度不合格（需优于 15 米）";return false;
        }
        if(fixNanos<=0||nowNanos<fixNanos||nowNanos-fixNanos>5_000_000_000L||fixNanos<=lastNanos){
            rejection="GPS 过期或时间倒退";return false;
        }
        double dt=(fixNanos-lastNanos)/1e9;
        if(lastNanos>=0 && dt<=5){
            double north=Math.toRadians(lat-latitude)*6371000;
            double east=Math.toRadians(lon-longitude)*6371000*Math.cos(Math.toRadians(lat));
            if(Math.hypot(north,east)>3*dt+Math.max(10,acc+accuracy)){
                rejection="GPS 位移突跳，暂不采用";return false;
            }
        }
        lastNanos=fixNanos;latitude=lat;longitude=lon;accuracy=acc;rejection="";return true;
    }
}
