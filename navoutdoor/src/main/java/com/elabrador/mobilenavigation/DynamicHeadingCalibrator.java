package com.elabrador.mobilenavigation;

import android.content.SharedPreferences;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/** Source GPS-to-VINS rigid fit with additional outdoor quality gates. */
final class DynamicHeadingCalibrator {
    private static final double EARTH_RADIUS_METERS = 6371000.0;
    private static final int MIN_TRANSFORM_SIZE = 16;
    static final int MIN_READY_SAMPLES = MIN_TRANSFORM_SIZE + 1;
    // Use a bounded recent trajectory, so bad startup samples do not remain until
    // 64 new observations. Never select arbitrary inliers to make a fit pass.
    private static final int MAX_BUFFER_SIZE = MIN_READY_SAMPLES;
    private static final double MIN_DELTA_DISTANCE_METERS = 1.5;
    static final float MAX_GPS_ACCURACY_METERS = 8f;
    private final List<Point> gpsPoints = new ArrayList<>();
    private final List<Point> vinsPoints = new ArrayList<>();
    private double initialLatitude, initialLongitude;
    private Point lastVinsPoint;
    private boolean collecting, ready;
    private double r00, r01, r10, r11;
    private String fitQuality = "";
    private String fitFailure = "";
    private double sampledDistance;
    private int totalSamples;
    private final CalibrationMotionGate motionGate=new CalibrationMotionGate();
    private String lastWindowRestart="";
    private long lastPairedFixNanos;
    private String status = "源码轨迹标定：等待 GPS 与 VINS";

    synchronized void save(SharedPreferences preferences) {
        // The transform belongs to the current arbitrary VINS frame.
        preferences.edit().remove("heading_calibration_ready")
                .remove("heading_calibration_offset").apply();
    }

    synchronized void start() {
        clearSamples(); collecting=true; ready=false;
        motionGate.reset();lastWindowRestart="";lastPairedFixNanos=0;
        status="采集最近 17 个同期 GPS/VINS 点；失败后随行走逐点替换旧点";
    }

    synchronized void resetForVinsRestart() {
        start(); status="VINS 已重启，方向轨迹需要重新采集（0/17）";
    }

    synchronized void updateTimed(double latitude,double longitude,float accuracyMeters,
                                  double vinsX,double vinsY,boolean initialized,long fixNanos){
        if(!collecting||ready)return;
        if(fixNanos<=0||fixNanos<=lastPairedFixNanos){status="忽略重复或倒序的定位时间";return;}
        lastPairedFixNanos=fixNanos;
        if(initialized && Double.isFinite(latitude)&&Math.abs(latitude)<=90
                && Double.isFinite(longitude)&&Math.abs(longitude)<=180
                && Double.isFinite(vinsX)&&Double.isFinite(vinsY)
                && Float.isFinite(accuracyMeters)&&accuracyMeters>0
                && accuracyMeters<=MAX_GPS_ACCURACY_METERS){
            String discontinuity=motionGate.observe(latitude,longitude,accuracyMeters,vinsX,vinsY,fixNanos);
            if(!discontinuity.isEmpty()){
                int total=totalSamples;double distance=sampledDistance;
                clearSamples();totalSamples=total;sampledDistance=distance;
                lastWindowRestart=discontinuity;
            }
        }
        update(latitude,longitude,accuracyMeters,vinsX,vinsY,initialized);
    }

    synchronized void update(double latitude, double longitude, float accuracyMeters,
                             double vinsX, double vinsY, boolean vinsInitialized) {
        if (!collecting || ready) return;
        if (!vinsInitialized || !Double.isFinite(vinsX) || !Double.isFinite(vinsY)) {
            status="源码轨迹标定：等待 VINS 初始化"; return;
        }
        if (!Double.isFinite(latitude) || !Double.isFinite(longitude)
                || Math.abs(latitude)>90 || Math.abs(longitude)>180
                || !Float.isFinite(accuracyMeters) || accuracyMeters<=0 || accuracyMeters>MAX_GPS_ACCURACY_METERS) {
            status="等待 GPS 精度达到 8 米以内"; return;
        }
        Point currentVins=new Point(vinsX,vinsY);
        if(lastVinsPoint==null) {
            initialLatitude=latitude; initialLongitude=longitude;
            append(new Point(0,0),currentVins); status=progressStatus(); return;
        }
        if(currentVins.distance(lastVinsPoint)<MIN_DELTA_DISTANCE_METERS) {
            status=progressStatus(); return;
        }
        double north=latitudeDeltaMeters(initialLatitude,latitude);
        double east=longitudeDeltaMeters(initialLongitude,longitude,initialLatitude);
        // Source calibration.py: distance*[cos(-azimuth), sin(-azimuth)].
        append(new Point(north,-east),currentVins);
        if(vinsPoints.size()<=MIN_TRANSFORM_SIZE) { status=progressStatus(); return; }
        if(estimateSourceRigidRotation()) {
            ready=true; collecting=false;
            status=String.format(Locale.CHINA,
                    "方向标定完成并锁定：%d 组，北向偏角 %.1f°",
                    vinsPoints.size(),northOffsetDegrees());
        } else status="方向尚未通过检查，请查看下方具体原因";
    }

    synchronized void waitForTimeAlignedVinsPose(){if(collecting)status="源码轨迹标定：等待 GPS 时刻对应的 VINS 位姿";}
    synchronized void waitForFreshGps(){if(collecting)status="源码轨迹标定：等待新鲜 GPS 定位";}
    synchronized boolean isReady(){return ready;}
    synchronized int sampleCount(){return vinsPoints.size();}
    synchronized int totalSampleCount(){return totalSamples;}
    synchronized double sampledDistanceMeters(){return sampledDistance;}
    synchronized String qualityDetails(){
        String warning=!ready && sampledDistance>=30
                ? "\n已采集超过 30 米仍未通过；请到开阔处。当前只检查最近 17 点，不应在此无限来回走" : "";
        if(!lastWindowRestart.isEmpty())warning+="\n最近窗口重建原因："+lastWindowRestart;
        if(fitQuality.isEmpty())return "达到 17 个有效点后开始拟合，不再等待三次确认"+warning;
        return (fitFailure.isEmpty()?"拟合检查通过":("上次未通过："+fitFailure))
                +"\n"+fitQuality+warning;
    }
    synchronized String status(){return status;}
    synchronized double northOffsetDegrees(){
        return ready?normalizeDegrees(Math.toDegrees(Math.atan2(r00,r10))):Double.NaN;
    }

    synchronized float relativeTargetDegrees(float geographicBearingDegrees,VinsMono.Pose pose){
        if(!ready||pose==null||!pose.initialized||!Float.isFinite(geographicBearingDegrees))return Float.NaN;
        double bearing=Math.toRadians(geographicBearingDegrees);
        double gpsX=Math.cos(bearing),gpsY=-Math.sin(bearing);
        double worldX=r00*gpsX+r01*gpsY,worldY=r10*gpsX+r11*gpsY;
        double yaw=pose.egoRightAxisYawRadians();
        double right=Math.cos(yaw)*worldX+Math.sin(yaw)*worldY;
        double forward=-Math.sin(yaw)*worldX+Math.cos(yaw)*worldY;
        return (float)normalizeDegrees(Math.toDegrees(Math.atan2(right,forward)));
    }

    static double normalizeDegrees(double degrees){return ((degrees+540.0)%360.0)-180.0;}

    private void append(Point gps,Point vins){
        if(lastVinsPoint!=null)sampledDistance+=vins.distance(lastVinsPoint);
        totalSamples++;
        gpsPoints.add(gps);vinsPoints.add(vins);lastVinsPoint=vins;
        while(gpsPoints.size()>MAX_BUFFER_SIZE){gpsPoints.remove(0);vinsPoints.remove(0);}
    }
    private String progressStatus(){return String.format(Locale.CHINA,
            "有效轨迹点 %d/%d；每个新点需让 D455F 移动至少 1.5 米",
            vinsPoints.size(),MIN_READY_SAMPLES);}

    /** Closed-form 2-D Kabsch fit; this always produces det(R)=+1. */
    private boolean estimateSourceRigidRotation(){
        int count=gpsPoints.size();if(count!=vinsPoints.size()||count<=MIN_TRANSFORM_SIZE)return false;
        double gsx=0,gsy=0,vsx=0,vsy=0;
        for(int i=0;i<count;i++){gsx+=gpsPoints.get(i).x;gsy+=gpsPoints.get(i).y;vsx+=vinsPoints.get(i).x;vsy+=vinsPoints.get(i).y;}
        gsx/=count;gsy/=count;vsx/=count;vsy/=count;
        double dot=0,cross=0,spread=0,vinsSpread=0;
        for(int i=0;i<count;i++){
            double sx=gpsPoints.get(i).x-gsx,sy=gpsPoints.get(i).y-gsy;
            double tx=vinsPoints.get(i).x-vsx,ty=vinsPoints.get(i).y-vsy;
            dot+=sx*tx+sy*ty;cross+=sx*ty-sy*tx;spread+=sx*sx+sy*sy;vinsSpread+=tx*tx+ty*ty;
        }
        fitFailure="";
        double gpsBaseline=baseline(gpsPoints,0,count),vinsBaseline=baseline(vinsPoints,0,count);
        fitQuality=String.format(Locale.CHINA,"轨迹跨度 GPS %.1fm / VINS %.1fm（均需 ≥12m）",gpsBaseline,vinsBaseline);
        if(spread<1e-6){fitFailure="GPS 轨迹几乎没有移动";return false;}
        if(Math.hypot(dot,cross)<1e-6){fitFailure="GPS 与 VINS 轨迹无法确定旋转方向";return false;}
        if(gpsBaseline<12 || vinsBaseline<12){fitFailure="有效空间跨度不足，来回走的累计距离不等于跨度";return false;}
        double angle=Math.atan2(cross,dot),c=Math.cos(angle),s=Math.sin(angle);
        double scale=Math.sqrt(vinsSpread/spread), squaredError=0;
        for(int i=0;i<count;i++){
            double sx=gpsPoints.get(i).x-gsx,sy=gpsPoints.get(i).y-gsy;
            double ex=c*sx-s*sy-(vinsPoints.get(i).x-vsx);
            double ey=s*sx+c*sy-(vinsPoints.get(i).y-vsy);
            squaredError+=ex*ex+ey*ey;
        }
        double rmse=Math.sqrt(squaredError/count);
        int mid=count/2;
        double a=segmentAngle(0,mid),b=segmentAngle(mid,count);
        double disagreement=Math.abs(normalizeDegrees(Math.toDegrees(a-b)));
        double normalizedError=rmse/Math.sqrt(vinsSpread/count);
        fitQuality=String.format(Locale.CHINA,
                "残差 %.2fm（≤2.5）· 相对残差 %.0f%%（≤20%%）\n尺度 %.2f（0.70～1.30）· 前后半段方向差 %s（≤10°）",
                rmse,normalizedError*100,scale,Double.isFinite(disagreement)
                ?String.format(Locale.CHINA,"%.1f°",disagreement):"无法估计");
        List<String> failures=new ArrayList<>();
        if(!Double.isFinite(scale)||scale<0.7||scale>1.3)failures.add("GPS 与 VINS 位移尺度不符");
        if(!Double.isFinite(rmse)||rmse>2.5)failures.add("轨迹拟合误差过大");
        if(!Double.isFinite(normalizedError)||normalizedError>0.20)failures.add("误差相对有效位移过大");
        if(!Double.isFinite(disagreement))failures.add("半段轨迹跨度不足或方向不可观测");
        else if(disagreement>10)failures.add("前后半段算出的方向不一致");
        if(!failures.isEmpty()){fitFailure=String.join("；",failures);return false;}
        r00=c;r01=-s;r10=s;r11=c;return true;
    }
    private double segmentAngle(int from,int to){
        if(baseline(gpsPoints,from,to)<5 || baseline(vinsPoints,from,to)<5)return Double.NaN;
        double gx=0,gy=0,vx=0,vy=0;
        for(int i=from;i<to;i++){gx+=gpsPoints.get(i).x;gy+=gpsPoints.get(i).y;vx+=vinsPoints.get(i).x;vy+=vinsPoints.get(i).y;}
        int n=to-from;gx/=n;gy/=n;vx/=n;vy/=n;
        double dot=0,cross=0;
        for(int i=from;i<to;i++){
            double x=gpsPoints.get(i).x-gx,y=gpsPoints.get(i).y-gy;
            double u=vinsPoints.get(i).x-vx,v=vinsPoints.get(i).y-vy;
            dot+=x*u+y*v;cross+=x*v-y*u;
        }
        return Math.hypot(dot,cross)<1e-6?Double.NaN:Math.atan2(cross,dot);
    }
    private static double baseline(List<Point> points,int from,int to){
        double max=0;
        for(int i=from;i<to;i++)for(int j=i+1;j<to;j++)max=Math.max(max,points.get(i).distance(points.get(j)));
        return max;
    }
    private void clearSamples(){gpsPoints.clear();vinsPoints.clear();lastVinsPoint=null;
        r00=r01=r10=r11=0;sampledDistance=0;totalSamples=0;fitQuality="";fitFailure="";}
    private static double latitudeDeltaMeters(double first,double second){return Math.toRadians(second-first)*EARTH_RADIUS_METERS;}
    private static double longitudeDeltaMeters(double first,double second,double referenceLatitude){return Math.toRadians(second-first)*EARTH_RADIUS_METERS*Math.cos(Math.toRadians(referenceLatitude));}
    private static final class Point{final double x,y;Point(double x,double y){this.x=x;this.y=y;}double distance(Point other){return Math.hypot(x-other.x,y-other.y);}}
}
