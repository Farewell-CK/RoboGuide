package com.elabrador.mobilenavigation;

import android.content.Context;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;

/** Loads the original Mapillary class/color contract without changing it. */
final class MapillaryMetadata {
    static final int SIDEWALK_CLASS_INDEX = 15;
    private static volatile int[] packedColors;

    private MapillaryMetadata() {}

    static int[] colors(Context context) throws Exception {
        int[] cached = packedColors;
        if (cached != null) return cached;
        StringBuilder json = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(
                context.getAssets().open("source_port/semantic/dataconfig_mapillary.json"),
                StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) json.append(line);
        }
        JSONArray labels = new JSONObject(json.toString()).getJSONArray("labels");
        int[] loaded = new int[Math.min(65, labels.length())];
        for (int i = 0; i < loaded.length; i++) {
            JSONArray color = labels.getJSONObject(i).getJSONArray("color");
            loaded[i] = (color.getInt(0) << 16) | (color.getInt(1) << 8) | color.getInt(2);
        }
        packedColors = loaded;
        return loaded;
    }
}
