package com.elabrador.mobilenavigation;

import java.util.Collections;
import java.util.List;

final class RouteFollower {
    private static final long MISMATCH_GRACE_NANOS = 3_000_000_000L;
    private static final long RECOVERY_CONFIRMATION_WINDOW_NANOS = 5_000_000_000L;
    private static final double MIN_RECOVERY_FORWARD_WINDOW_METERS = 100.0;
    private static final double RECOVERY_PROGRESS_TOLERANCE_METERS = 30.0;
    static final class Guidance {
        final String action;
        final String instruction;
        final int remainingMeters;
        final int distanceToInstructionMeters;
        final float targetBearingDegrees;
        final float relativeTurnDegrees;
        final int crossTrackMeters;
        final boolean offRoute;
        final boolean arrived;

        Guidance(
                String action,
                String instruction,
                int remainingMeters,
                int distanceToInstructionMeters,
                float targetBearingDegrees,
                float relativeTurnDegrees,
                int crossTrackMeters,
                boolean offRoute,
                boolean arrived) {
            this.action = action;
            this.instruction = instruction;
            this.remainingMeters = remainingMeters;
            this.distanceToInstructionMeters = distanceToInstructionMeters;
            this.targetBearingDegrees = targetBearingDegrees;
            this.relativeTurnDegrees = relativeTurnDegrees;
            this.crossTrackMeters = crossTrackMeters;
            this.offRoute = offRoute;
            this.arrived = arrived;
        }
    }

    private static final double EARTH_RADIUS_METERS = 6371000.0;
    private static final double LOOKAHEAD_METERS = 12.0;

    private AmapRouteClient.RouteResult route;
    private List<AmapRouteClient.GeoPoint> points = Collections.emptyList();
    private double[] cumulativeMeters = new double[0];
    private double routeGeometryMeters;
    private double lastProgressMeters = -1.0;
    /** Timestamp of the last accepted route match. */
    private long lastFixNanos=-1;
    /** Timestamp of the last fix examined, including rejected fixes. */
    private long lastSeenFixNanos=-1;
    private double advanceBudget=12;
    private Guidance lastGuidance;
    private Guidance lastUpdateResult;
    private long mismatchStartedNanos=-1;
    private long recoveryCandidateNanos=-1;
    private double recoveryCandidateProgress=-1;
    private int recoveryCandidateCount;
    private String waitingReason="等待有效 GPS 路线匹配";
    synchronized String waitingReason(){return waitingReason;}

    synchronized void setRoute(AmapRouteClient.RouteResult route) {
        this.route = route;
        points = route.polyline;
        cumulativeMeters = new double[points.size()];
        for (int i = 1; i < points.size(); i++) {
            cumulativeMeters[i] = cumulativeMeters[i - 1]
                    + AmapRouteClient.distanceMeters(points.get(i - 1), points.get(i));
        }
        routeGeometryMeters = cumulativeMeters.length == 0
                ? 0.0
                : cumulativeMeters[cumulativeMeters.length - 1];
        lastProgressMeters = -1.0;
        resetMatchState();
        waitingReason="等待有效 GPS 路线匹配";
    }

    synchronized void clear() {
        resetMatchState();
        route = null;
        points = Collections.emptyList();
        cumulativeMeters = new double[0];
        routeGeometryMeters = 0.0;
        lastProgressMeters = -1.0;
        waitingReason="等待有效 GPS 路线匹配";
    }

    synchronized boolean hasRoute() {
        return route != null && points.size() >= 2;
    }

    synchronized Guidance update(double wgsLatitude, double wgsLongitude, float accuracyMeters, float heading) {
        return update(wgsLatitude,wgsLongitude,accuracyMeters,heading,System.nanoTime());
    }

    synchronized Guidance update(double wgsLatitude,double wgsLongitude,float accuracyMeters,
                                  float heading,long fixNanos) {
        if(!hasRoute())return null;
        if(!Double.isFinite(wgsLatitude)||Math.abs(wgsLatitude)>90
                ||!Double.isFinite(wgsLongitude)||Math.abs(wgsLongitude)>180){
            waitingReason="等待有效手机位置";return null;
        }
        // Legacy aligned mode accepts both network and GPS fixes; accuracy is diagnostic.
        if(!Float.isFinite(accuracyMeters)||accuracyMeters<0)accuracyMeters=0;
        if(fixNanos<lastSeenFixNanos){waitingReason="GPS 时间倒退";return null;}
        if(fixNanos==lastSeenFixNanos)return lastUpdateResult;
        lastSeenFixNanos=fixNanos;
        double candidateAdvanceBudget=advanceBudget;
        if(lastFixNanos>=0)candidateAdvanceBudget=Math.min(15,advanceBudget
                +3*Math.min(5,Math.max(0,(fixNanos-lastFixNanos)/1e9)));
        AmapRouteClient.GeoPoint current = AmapRouteClient.wgs84ToGcj02(
                wgsLatitude, wgsLongitude);
        double matchThreshold=Math.max(25,accuracyMeters*2);
        Match match = findClosestMatch(current,candidateAdvanceBudget,true);
        boolean recovered=false;
        if(match.distanceMeters>matchThreshold){
            Match recovery=findClosestMatch(current,Double.POSITIVE_INFINITY,false);
            if(recovery.distanceMeters<=matchThreshold && recoveryProgressAllowed(recovery,fixNanos)){
                if(recoveryCandidateNanos>=0
                        && fixNanos-recoveryCandidateNanos<=RECOVERY_CONFIRMATION_WINDOW_NANOS
                        && Math.abs(recovery.progressMeters-recoveryCandidateProgress)
                        <=RECOVERY_PROGRESS_TOLERANCE_METERS){
                    recoveryCandidateCount++;
                } else {
                    recoveryCandidateCount=1;
                }
                recoveryCandidateNanos=fixNanos;
                recoveryCandidateProgress=recovery.progressMeters;
                if(recoveryCandidateCount>=2){
                    match=recovery;
                    recovered=true;
                }
            } else {
                clearRecoveryCandidate();
            }
            if(!recovered){
                if(mismatchStartedNanos<0)mismatchStartedNanos=fixNanos;
                long mismatchAge=Math.max(0,fixNanos-mismatchStartedNanos);
                waitingReason=recoveryCandidateCount>0
                        ?"定位短暂跳出连续路线，正在自动恢复匹配"
                        :"定位暂时无法匹配路线，正在等待定位恢复";
                lastUpdateResult=mismatchAge<=MISMATCH_GRACE_NANOS?lastGuidance:null;
                return lastUpdateResult;
            }
        }
        mismatchStartedNanos=-1;
        clearRecoveryCandidate();
        lastFixNanos=fixNanos;
        advanceBudget=recovered?12:candidateAdvanceBudget;
        double previous=lastProgressMeters;
        lastProgressMeters = Math.max(lastProgressMeters, match.progressMeters);
        if(previous>=0&&!recovered)advanceBudget=Math.max(0,advanceBudget-(lastProgressMeters-previous));

        double apiProgress = routeGeometryMeters > 0.0
                ? lastProgressMeters / routeGeometryMeters * route.distanceMeters
                : 0.0;
        int remaining = (int) Math.max(0, Math.round(route.distanceMeters - apiProgress));
        AmapRouteClient.GeoPoint destination = points.get(points.size() - 1);
        double destinationDistance = AmapRouteClient.distanceMeters(current, destination);
        boolean arrived = routeGeometryMeters-lastProgressMeters<=12
                && destinationDistance<=10.0;
        double offRouteThreshold = Math.max(25.0, Math.max(0.0f, accuracyMeters) * 1.5);
        boolean offRoute = !arrived && match.distanceMeters > offRouteThreshold;

        StepProgress step = findCurrentStep(apiProgress);
        AmapRouteClient.GeoPoint matchedRoutePoint = pointAtProgress(lastProgressMeters);
        AmapRouteClient.GeoPoint target = pointAtProgress(
                Math.min(routeGeometryMeters, lastProgressMeters + LOOKAHEAD_METERS));
        float bearing = AmapRouteClient.bearingDegrees(
                offRoute ? current.latitude : matchedRoutePoint.latitude,
                offRoute ? current.longitude : matchedRoutePoint.longitude,
                target.latitude, target.longitude);
        float relativeTurn = Float.isFinite(heading)
                ? normalizeTurn(bearing - heading)
                : Float.NaN;

        String action;
        if (arrived) {
            action = "已到达目的地";
        } else if (offRoute) {
            action = "已偏离路线，请返回规划路线";
        } else if (!Float.isFinite(relativeTurn)) {
            action = "沿路线前进";
        } else {
            action = actionForTurn(relativeTurn);
        }

        lastGuidance = new Guidance(
                action,
                step.instruction,
                remaining,
                step.distanceToEndMeters,
                bearing,
                relativeTurn,
                (int) Math.round(match.distanceMeters),
                offRoute,
                arrived);
        waitingReason="";
        lastUpdateResult=lastGuidance;
        return lastUpdateResult;
    }

    private boolean recoveryProgressAllowed(Match recovery,long fixNanos){
        if(lastProgressMeters<0)return recovery.progressMeters<=MIN_RECOVERY_FORWARD_WINDOW_METERS;
        double elapsedSeconds=lastFixNanos<0?0:Math.max(0,(fixNanos-lastFixNanos)/1e9);
        double forwardWindow=Math.max(MIN_RECOVERY_FORWARD_WINDOW_METERS,30+5*elapsedSeconds);
        return recovery.progressMeters>=lastProgressMeters-15
                && recovery.progressMeters<=lastProgressMeters+forwardWindow;
    }

    private void clearRecoveryCandidate(){
        recoveryCandidateNanos=-1;
        recoveryCandidateProgress=-1;
        recoveryCandidateCount=0;
    }

    private void resetMatchState(){
        lastFixNanos=-1;
        lastSeenFixNanos=-1;
        advanceBudget=12;
        lastGuidance=null;
        lastUpdateResult=null;
        mismatchStartedNanos=-1;
        clearRecoveryCandidate();
    }

    private Match findClosestMatch(AmapRouteClient.GeoPoint current,double progressBudget,
                                   boolean constrainProgress) {
        double bestDistance = Double.MAX_VALUE;
        double bestProgress = 0.0;
        double latitudeRadians = Math.toRadians(current.latitude);
        double longitudeScale = Math.cos(latitudeRadians) * EARTH_RADIUS_METERS;
        double latitudeScale = EARTH_RADIUS_METERS;

        for (int i = 0; i < points.size() - 1; i++) {
            double segmentStart = cumulativeMeters[i];
            double segmentEnd = cumulativeMeters[i + 1];
            if(constrainProgress && lastProgressMeters<0 && segmentStart>25)continue;
            if (constrainProgress && lastProgressMeters >= 0.0
                    && (segmentEnd < lastProgressMeters - 15.0
                    || segmentStart>lastProgressMeters+progressBudget)) {
                continue;
            }

            AmapRouteClient.GeoPoint first = points.get(i);
            AmapRouteClient.GeoPoint second = points.get(i + 1);
            double ax = Math.toRadians(first.longitude - current.longitude) * longitudeScale;
            double ay = Math.toRadians(first.latitude - current.latitude) * latitudeScale;
            double bx = Math.toRadians(second.longitude - current.longitude) * longitudeScale;
            double by = Math.toRadians(second.latitude - current.latitude) * latitudeScale;
            double dx = bx - ax;
            double dy = by - ay;
            double lengthSquared = dx * dx + dy * dy;
            double t = lengthSquared <= 0.001 ? 0.0 : -(ax * dx + ay * dy) / lengthSquared;
            t = Math.max(0.0, Math.min(1.0, t));
            if(constrainProgress && lastProgressMeters<0 && segmentEnd>segmentStart)
                t=Math.min(t,Math.max(0,(25-segmentStart)/(segmentEnd-segmentStart)));
            if(constrainProgress && lastProgressMeters>=0 && segmentEnd>segmentStart)
                t=Math.min(t,Math.max(0,(lastProgressMeters+progressBudget-segmentStart)/(segmentEnd-segmentStart)));
            double x = ax + t * dx;
            double y = ay + t * dy;
            double distance = Math.hypot(x, y);
            if (distance < bestDistance) {
                bestDistance = distance;
                bestProgress = segmentStart + t * (segmentEnd - segmentStart);
            }
        }
        return new Match(bestProgress, bestDistance);
    }

    private StepProgress findCurrentStep(double apiProgressMeters) {
        double stepEnd = 0.0;
        for (AmapRouteClient.RouteStep step : route.steps) {
            stepEnd += step.distanceMeters;
            if (apiProgressMeters <= stepEnd) {
                return new StepProgress(
                        step.instruction,
                        (int) Math.max(0, Math.round(stepEnd - apiProgressMeters)));
            }
        }
        return new StepProgress("继续前往目的地", Math.max(0, route.distanceMeters
                - (int) Math.round(apiProgressMeters)));
    }

    private AmapRouteClient.GeoPoint pointAtProgress(double progressMeters) {
        for (int i = 0; i < cumulativeMeters.length - 1; i++) {
            if (progressMeters <= cumulativeMeters[i + 1]) {
                double segmentLength = cumulativeMeters[i + 1] - cumulativeMeters[i];
                double t = segmentLength <= 0.001
                        ? 0.0
                        : (progressMeters - cumulativeMeters[i]) / segmentLength;
                AmapRouteClient.GeoPoint first = points.get(i);
                AmapRouteClient.GeoPoint second = points.get(i + 1);
                return new AmapRouteClient.GeoPoint(
                        first.latitude + (second.latitude - first.latitude) * t,
                        first.longitude + (second.longitude - first.longitude) * t,
                        "");
            }
        }
        return points.get(points.size() - 1);
    }

    private static float normalizeTurn(float degrees) {
        return ((degrees + 540f) % 360f) - 180f;
    }

    private static String actionForTurn(float relativeTurn) {
        float magnitude = Math.abs(relativeTurn);
        if (magnitude < 30f) {
            return "沿路线直行";
        }
        String side = relativeTurn > 0f ? "右" : "左";
        if (magnitude < 60f) {
            return "向" + side + "前方行进";
        }
        if (magnitude < 135f) {
            return "向" + side + "转";
        }
        return "请掉头";
    }

    private static final class Match {
        final double progressMeters;
        final double distanceMeters;

        Match(double progressMeters, double distanceMeters) {
            this.progressMeters = progressMeters;
            this.distanceMeters = distanceMeters;
        }
    }

    private static final class StepProgress {
        final String instruction;
        final int distanceToEndMeters;

        StepProgress(String instruction, int distanceToEndMeters) {
            this.instruction = instruction;
            this.distanceToEndMeters = distanceToEndMeters;
        }
    }
}
