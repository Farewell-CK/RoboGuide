package com.elabrador.mobilenavigation;

import org.json.JSONObject;
import org.junit.Test;
import static org.junit.Assert.*;

public class RuntimeRecordingConfigurationTest {
    @Test public void copiedObservationsRetainMeasurementTimeAndCannotBeChangedByCallers() throws Exception {
        JSONObject input=new JSONObject().put("available",true).put("value",new JSONObject().put("firmware","fixture"));
        JSONObject observed=RuntimeRecordingConfiguration.remember("test_measurement",input,1000L,2000L);
        input.getJSONObject("value").put("firmware","mutated-input");
        observed.getJSONObject("value").put("firmware","mutated-return");
        JSONObject snapshot=RuntimeRecordingConfiguration.snapshotAt(3000L,4000L);
        JSONObject stored=snapshot.getJSONObject("test_measurement");
        assertEquals("fixture",stored.getJSONObject("value").getString("firmware"));
        assertEquals(1000L,stored.getLong("observed_at_unix_ms"));
        assertEquals(2000L,stored.getLong("observed_at_elapsed_ns"));
        assertEquals(3000L,snapshot.getLong("snapshot_unix_ms"));
        assertEquals(4000L,snapshot.getLong("snapshot_elapsed_ns"));
        stored.put("available",false);
        assertTrue(RuntimeRecordingConfiguration.snapshotAt(5000L,6000L).getJSONObject("test_measurement").getBoolean("available"));
    }

    @Test public void snapshotHasParameterUnitsGridModelAndMissingHardwareState() throws Exception {
        JSONObject snapshot=RuntimeRecordingConfiguration.snapshotAt(10L,20L);
        JSONObject planner=snapshot.getJSONObject("local_planner");
        assertEquals(MapTransform.WIDTH,planner.getInt("grid_width"));
        assertEquals(MapTransform.HEIGHT,planner.getInt("grid_height"));
        assertEquals(MapTransform.RESOLUTION,planner.getDouble("grid_resolution_m"),1e-8);
        assertEquals(LocalPlannerGrid.SAFETY_RADIUS_CELLS,planner.getInt("grid_safety_radius_cells"));
        JSONObject model=snapshot.getJSONObject("semantic_pipeline");
        assertEquals(BuildConfig.SEMANTIC_MODEL_WIDTH,model.getInt("input_width"));
        assertEquals(BuildConfig.SEMANTIC_MODEL_HEIGHT,model.getInt("input_height"));
        assertEquals("WGS84",snapshot.getJSONObject("phone_positioning").getString("position_coordinate_system"));
        assertFalse(snapshot.getJSONObject("camera").getBoolean("available"));
        assertEquals("not_observed_in_this_process",snapshot.getJSONObject("camera").getString("reason"));
    }
}
