package com.elabrador.mobilenavigation;

import java.util.Locale;

/** Checks paired displacements without assuming geographic heading or phone orientation. */
final class CalibrationMotionGate {
    private long previousNanos;
    private double latitude,longitude,x,y;
    private float accuracy;

    String observe(double lat,double lon,float acc,double vx,double vy,long fixNanos){
        String restart="";
        if(previousNanos>0){
            double dt=(fixNanos-previousNanos)/1e9;
            double north=Math.toRadians(lat-latitude)*6371000;
            double east=Math.toRadians(lon-longitude)*6371000*Math.cos(Math.toRadians((lat+latitude)/2));
            double gpsDistance=Math.hypot(north,east),vinsDistance=Math.hypot(vx-x,vy-y);
            // This tolerance detects abrupt discrepancies, not centimetre-level noise.
            double tolerance=Math.max(3,Math.min(5,(acc+accuracy)*0.5));
            if(dt<=0 || dt>5)restart="同期数据中断，重新建立连续轨迹窗口";
            else if(Math.abs(gpsDistance-vinsDistance)>tolerance)
                restart=String.format(Locale.CHINA,
                        "同期位移不符：GPS %.1fm / VINS %.1fm（%.1fs，允许差 %.1fm）",
                        gpsDistance,vinsDistance,dt,tolerance);
        }
        previousNanos=fixNanos;latitude=lat;longitude=lon;x=vx;y=vy;accuracy=acc;
        return restart;
    }
    void reset(){previousNanos=0;}
}
