package com.elabrador.mobilenavigation;

import android.location.Location;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

final class AmapRouteClient {
    interface Callback {
        void onSuccess(RouteResult result);

        void onError(String message);
    }

    interface SearchCallback {
        void onSuccess(List<PlaceSuggestion> suggestions);

        void onError(String message);
    }

    static final class PlaceSuggestion {
        final String name;
        final String address;
        final double latitude;
        final double longitude;
        final int distanceMeters;

        PlaceSuggestion(
                String name,
                String address,
                double latitude,
                double longitude,
                int distanceMeters) {
            this.name = name;
            this.address = address;
            this.latitude = latitude;
            this.longitude = longitude;
            this.distanceMeters = distanceMeters;
        }
    }

    static final class RouteResult {
        final String destinationName;
        final double destinationLatitude;
        final double destinationLongitude;
        final int distanceMeters;
        final int durationSeconds;
        final String firstInstruction;
        final float initialBearingDegrees;
        final List<RouteStep> steps;
        final List<GeoPoint> polyline;

        RouteResult(
                String destinationName,
                double destinationLatitude,
                double destinationLongitude,
                int distanceMeters,
                int durationSeconds,
                String firstInstruction,
                float initialBearingDegrees,
                List<RouteStep> steps,
                List<GeoPoint> polyline) {
            this.destinationName = destinationName;
            this.destinationLatitude = destinationLatitude;
            this.destinationLongitude = destinationLongitude;
            this.distanceMeters = distanceMeters;
            this.durationSeconds = durationSeconds;
            this.firstInstruction = firstInstruction;
            this.initialBearingDegrees = initialBearingDegrees;
            this.steps = steps;
            this.polyline = polyline;
        }
    }

    static final class RouteStep {
        final String instruction;
        final int distanceMeters;
        final String action;
        final List<GeoPoint> points;

        RouteStep(String instruction, int distanceMeters, String action, List<GeoPoint> points) {
            this.instruction = instruction;
            this.distanceMeters = distanceMeters;
            this.action = action;
            this.points = points;
        }
    }

    private static final String GEOCODE_URL = "https://restapi.amap.com/v3/geocode/geo";
    private static final String PLACE_AROUND_URL = "https://restapi.amap.com/v3/place/around";
    private static final String WALKING_URL = "https://restapi.amap.com/v3/direction/walking";
    private static final int TIMEOUT_MILLIS = 10000;
    private static final double MAX_WALKING_DISTANCE_METERS = 5000.0;

    private final ExecutorService executor = Executors.newSingleThreadExecutor();

    void planWalkingRoute(String key, Location origin, String destination, Callback callback) {
        executor.execute(() -> {
            try {
                GeoPoint start = wgs84ToGcj02(origin.getLatitude(), origin.getLongitude());
                GeoPoint end = resolveDestination(key, destination, start);
                callback.onSuccess(requestWalkingRoute(key, start, end));
            } catch (Exception error) {
                String message = error.getMessage();
                callback.onError(message == null ? error.getClass().getSimpleName() : message);
            }
        });
    }

    void planWalkingRoute(
            String key, Location origin, PlaceSuggestion destination, Callback callback) {
        executor.execute(() -> {
            try {
                GeoPoint start = wgs84ToGcj02(origin.getLatitude(), origin.getLongitude());
                GeoPoint end = new GeoPoint(
                        destination.latitude,
                        destination.longitude,
                        destination.address.isEmpty()
                                ? destination.name
                                : destination.name + "（" + destination.address + "）");
                callback.onSuccess(requestWalkingRoute(key, start, end));
            } catch (Exception error) {
                String message = error.getMessage();
                callback.onError(message == null ? error.getClass().getSimpleName() : message);
            }
        });
    }

    void searchNearby(String key, Location origin, String keyword, SearchCallback callback) {
        executor.execute(() -> {
            try {
                GeoPoint start = wgs84ToGcj02(origin.getLatitude(), origin.getLongitude());
                callback.onSuccess(searchNearbyResults(key, keyword, start));
            } catch (Exception error) {
                String message = error.getMessage();
                callback.onError(message == null ? error.getClass().getSimpleName() : message);
            }
        });
    }

    private GeoPoint resolveDestination(String key, String destination, GeoPoint start)
            throws Exception {
        GeoPoint nearby = searchNearby(key, destination, start);
        if (nearby != null) {
            return nearby;
        }

        GeoPoint geocoded = geocode(key, destination);
        double distance = distanceMeters(start, geocoded);
        if (distance > MAX_WALKING_DISTANCE_METERS) {
            throw new IllegalArgumentException(String.format(
                    Locale.CHINA,
                    "附近 5 公里内没有找到该目的地，地址解析结果距离约 %.1f 公里",
                    distance / 1000.0));
        }
        return geocoded;
    }

    private GeoPoint searchNearby(String key, String keyword, GeoPoint start) throws Exception {
        List<PlaceSuggestion> results = searchNearbyResults(key, keyword, start);
        if (results.isEmpty()) {
            return null;
        }
        PlaceSuggestion first = results.get(0);
        return new GeoPoint(
                first.latitude,
                first.longitude,
                first.address.isEmpty() ? first.name : first.name + "（" + first.address + "）");
    }

    private List<PlaceSuggestion> searchNearbyResults(
            String key, String keyword, GeoPoint start) throws Exception {
        String location = String.format(
                Locale.US, "%.6f,%.6f", start.longitude, start.latitude);
        String query = "location=" + encode(location)
                + "&keywords=" + encode(keyword)
                + "&radius=5000&sortrule=distance&offset=20&extensions=all"
                + "&output=json&key=" + encode(key);
        JSONObject response = requestJson(PLACE_AROUND_URL + "?" + query);
        requireSuccess(response);
        JSONArray pois = response.optJSONArray("pois");
        List<PlaceSuggestion> results = new ArrayList<>();
        if (pois == null || pois.length() == 0) {
            return results;
        }

        for (int i = 0; i < pois.length() && results.size() < 8; i++) {
            JSONObject poi = pois.getJSONObject(i);
            String coordinateText = poi.optString("entr_location", "");
            if (coordinateText.isEmpty() || !coordinateText.contains(",")) {
                coordinateText = poi.optString("location", "");
            }
            String[] coordinate = coordinateText.split(",");
            if (coordinate.length != 2) {
                continue;
            }

            double latitude = Double.parseDouble(coordinate[1]);
            double longitude = Double.parseDouble(coordinate[0]);
            GeoPoint point = new GeoPoint(latitude, longitude, "");
            int distance = (int) Math.round(distanceMeters(start, point));
            if (distance > MAX_WALKING_DISTANCE_METERS) {
                continue;
            }
            String address = poi.optString("address", "");
            if (address.equals("[]")) {
                address = "";
            }
            results.add(new PlaceSuggestion(
                    poi.optString("name", "目的地"),
                    address,
                    latitude,
                    longitude,
                    distance));
        }
        return results;
    }

    void close() {
        executor.shutdownNow();
    }

    private GeoPoint geocode(String key, String destination) throws Exception {
        String query = "address=" + encode(destination)
                + "&output=json&key=" + encode(key);
        JSONObject response = requestJson(GEOCODE_URL + "?" + query);
        requireSuccess(response);
        JSONArray geocodes = response.optJSONArray("geocodes");
        if (geocodes == null || geocodes.length() == 0) {
            throw new IllegalArgumentException("没有找到该目的地，请输入更完整的地址");
        }

        JSONObject geocode = geocodes.getJSONObject(0);
        String[] coordinate = geocode.getString("location").split(",");
        if (coordinate.length != 2) {
            throw new IllegalStateException("高德返回了无效的目的地坐标");
        }
        return new GeoPoint(
                Double.parseDouble(coordinate[1]),
                Double.parseDouble(coordinate[0]),
                geocode.optString("formatted_address", destination));
    }

    private RouteResult requestWalkingRoute(String key, GeoPoint start, GeoPoint end)
            throws Exception {
        String origin = String.format(Locale.US, "%.6f,%.6f", start.longitude, start.latitude);
        String destination = String.format(Locale.US, "%.6f,%.6f", end.longitude, end.latitude);
        String query = "origin=" + encode(origin)
                + "&destination=" + encode(destination)
                + "&output=json&key=" + encode(key);
        JSONObject response = requestJson(WALKING_URL + "?" + query);
        requireSuccess(response);

        JSONObject route = response.optJSONObject("route");
        JSONArray paths = route == null ? null : route.optJSONArray("paths");
        if (paths == null || paths.length() == 0) {
            throw new IllegalArgumentException("高德未返回可用的步行路线");
        }

        JSONObject path = paths.getJSONObject(0);
        JSONArray stepJson = path.optJSONArray("steps");
        String instruction = "按路线开始步行";
        float bearing = bearingDegrees(start.latitude, start.longitude, end.latitude, end.longitude);
        List<RouteStep> steps = new ArrayList<>();
        List<GeoPoint> routePoints = new ArrayList<>();
        if (stepJson != null) {
            for (int i = 0; i < stepJson.length(); i++) {
                JSONObject item = stepJson.getJSONObject(i);
                List<GeoPoint> points = parsePolyline(item.optString("polyline", ""));
                String stepInstruction = item.optString("instruction", "继续沿路线步行");
                String action = item.optString("action", "");
                if (action.isEmpty()) {
                    action = item.optString("assistant_action", "");
                }
                steps.add(new RouteStep(
                        stepInstruction,
                        parseInteger(item.optString("distance", "0")),
                        action,
                        points));
                appendDistinct(routePoints, points);
            }
        }
        if (!steps.isEmpty()) {
            instruction = steps.get(0).instruction;
        }
        if (routePoints.size() < 2) {
            routePoints.clear();
            routePoints.add(start);
            routePoints.add(end);
        } else {
            bearing = bearingFromPoints(routePoints, bearing);
        }

        return new RouteResult(
                end.name,
                end.latitude,
                end.longitude,
                parseInteger(path.optString("distance", "0")),
                parseInteger(path.optString("duration", "0")),
                instruction,
                bearing,
                steps,
                routePoints);
    }

    private JSONObject requestJson(String requestUrl) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(requestUrl).openConnection();
        connection.setConnectTimeout(TIMEOUT_MILLIS);
        connection.setReadTimeout(TIMEOUT_MILLIS);
        connection.setRequestMethod("GET");
        connection.setRequestProperty("Accept", "application/json");
        try {
            int status = connection.getResponseCode();
            InputStream stream = status >= 200 && status < 300
                    ? connection.getInputStream()
                    : connection.getErrorStream();
            if (stream == null) {
                throw new IllegalStateException("高德服务连接失败：HTTP " + status);
            }
            StringBuilder body = new StringBuilder();
            try (BufferedReader reader = new BufferedReader(
                    new InputStreamReader(stream, StandardCharsets.UTF_8))) {
                String line;
                while ((line = reader.readLine()) != null) {
                    body.append(line);
                }
            }
            return new JSONObject(body.toString());
        } finally {
            connection.disconnect();
        }
    }

    private void requireSuccess(JSONObject response) {
        if (!"1".equals(response.optString("status"))) {
            String info = response.optString("info", "未知错误");
            String code = response.optString("infocode", "");
            throw new IllegalStateException("高德接口错误：" + info + " " + code);
        }
    }

    private String encode(String value) throws Exception {
        return URLEncoder.encode(value, StandardCharsets.UTF_8.name());
    }

    private int parseInteger(String value) {
        try {
            return Math.round(Float.parseFloat(value));
        } catch (NumberFormatException ignored) {
            return 0;
        }
    }

    private List<GeoPoint> parsePolyline(String polyline) {
        List<GeoPoint> result = new ArrayList<>();
        for (String point : polyline.split(";")) {
            String[] coordinate = point.split(",");
            if (coordinate.length != 2) {
                continue;
            }
            try {
                result.add(new GeoPoint(
                        Double.parseDouble(coordinate[1]),
                        Double.parseDouble(coordinate[0]),
                        ""));
            } catch (NumberFormatException ignored) {
                // Ignore a malformed point while preserving the rest of the route.
            }
        }
        return result;
    }

    private void appendDistinct(List<GeoPoint> destination, List<GeoPoint> source) {
        for (GeoPoint point : source) {
            if (destination.isEmpty()
                    || distanceMeters(destination.get(destination.size() - 1), point) > 0.1) {
                destination.add(point);
            }
        }
    }

    private float bearingFromPoints(List<GeoPoint> points, float fallback) {
        GeoPoint first = points.get(0);
        for (int i = 1; i < points.size(); i++) {
            GeoPoint next = points.get(i);
            if (distanceMeters(first, next) > 0.5) {
                return bearingDegrees(
                        first.latitude, first.longitude, next.latitude, next.longitude);
            }
        }
        return fallback;
    }

    static float bearingDegrees(double lat1, double lon1, double lat2, double lon2) {
        double firstLat = Math.toRadians(lat1);
        double secondLat = Math.toRadians(lat2);
        double deltaLon = Math.toRadians(lon2 - lon1);
        double y = Math.sin(deltaLon) * Math.cos(secondLat);
        double x = Math.cos(firstLat) * Math.sin(secondLat)
                - Math.sin(firstLat) * Math.cos(secondLat) * Math.cos(deltaLon);
        return (float) ((Math.toDegrees(Math.atan2(y, x)) + 360.0) % 360.0);
    }

    static double distanceMeters(GeoPoint first, GeoPoint second) {
        double lat1 = Math.toRadians(first.latitude);
        double lat2 = Math.toRadians(second.latitude);
        double deltaLat = lat2 - lat1;
        double deltaLon = Math.toRadians(second.longitude - first.longitude);
        double haversine = Math.sin(deltaLat / 2.0) * Math.sin(deltaLat / 2.0)
                + Math.cos(lat1) * Math.cos(lat2)
                * Math.sin(deltaLon / 2.0) * Math.sin(deltaLon / 2.0);
        return 6371000.0 * 2.0 * Math.atan2(Math.sqrt(haversine), Math.sqrt(1.0 - haversine));
    }

    static GeoPoint wgs84ToGcj02(double latitude, double longitude) {
        if (longitude < 72.004 || longitude > 137.8347
                || latitude < 0.8293 || latitude > 55.8271) {
            return new GeoPoint(latitude, longitude, "");
        }

        double deltaLat = transformLatitude(longitude - 105.0, latitude - 35.0);
        double deltaLon = transformLongitude(longitude - 105.0, latitude - 35.0);
        double radLat = Math.toRadians(latitude);
        double magic = Math.sin(radLat);
        magic = 1 - 0.00669342162296594323 * magic * magic;
        double sqrtMagic = Math.sqrt(magic);
        deltaLat = deltaLat * 180.0
                / ((6378245.0 * (1 - 0.00669342162296594323))
                / (magic * sqrtMagic) * Math.PI);
        deltaLon = deltaLon * 180.0
                / (6378245.0 / sqrtMagic * Math.cos(radLat) * Math.PI);
        return new GeoPoint(latitude + deltaLat, longitude + deltaLon, "");
    }

    private static double transformLatitude(double x, double y) {
        double value = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y
                + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x));
        value += (20.0 * Math.sin(6.0 * x * Math.PI)
                + 20.0 * Math.sin(2.0 * x * Math.PI)) * 2.0 / 3.0;
        value += (20.0 * Math.sin(y * Math.PI)
                + 40.0 * Math.sin(y / 3.0 * Math.PI)) * 2.0 / 3.0;
        value += (160.0 * Math.sin(y / 12.0 * Math.PI)
                + 320.0 * Math.sin(y * Math.PI / 30.0)) * 2.0 / 3.0;
        return value;
    }

    private static double transformLongitude(double x, double y) {
        double value = 300.0 + x + 2.0 * y + 0.1 * x * x
                + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x));
        value += (20.0 * Math.sin(6.0 * x * Math.PI)
                + 20.0 * Math.sin(2.0 * x * Math.PI)) * 2.0 / 3.0;
        value += (20.0 * Math.sin(x * Math.PI)
                + 40.0 * Math.sin(x / 3.0 * Math.PI)) * 2.0 / 3.0;
        value += (150.0 * Math.sin(x / 12.0 * Math.PI)
                + 300.0 * Math.sin(x / 30.0 * Math.PI)) * 2.0 / 3.0;
        return value;
    }

    static final class GeoPoint {
        final double latitude;
        final double longitude;
        final String name;

        GeoPoint(double latitude, double longitude, String name) {
            this.latitude = latitude;
            this.longitude = longitude;
            this.name = name;
        }
    }
}
