package com.elabrador.mobilenavigation;

import com.intel.realsense.librealsense.TimestampDomain;
import com.intel.realsense.librealsense.Intrinsic;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotSame;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;


import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;

public class LocalPlannerStateTest {
    @Test
    public void globalRealSenseTimestampIsAlreadySystemTime() {
        RealSenseTimestampMapper mapper = new RealSenseTimestampMapper();

        assertEquals(123456.0, mapper.toSystemTimeMilliseconds(
                123456.0, TimestampDomain.GLOBAL_TIME, 900000.0), 1e-9);
    }

    @Test
    public void hardwareTimestampUsesOneSourceStyleSystemTimeBaseAcrossStreams() {
        RealSenseTimestampMapper mapper = new RealSenseTimestampMapper();

        assertEquals(900000.0, mapper.toSystemTimeMilliseconds(
                5000.0, TimestampDomain.HARDWARE_CLOCK, 900000.0), 1e-9);
        assertEquals(900025.0, mapper.toSystemTimeMilliseconds(
                5025.0, TimestampDomain.HARDWARE_CLOCK, 900100.0), 1e-9);
        // A video frame can arrive after a newer IMU frame. RealSense ROS keeps
        // the common camera clock base; cross-stream delivery order is not a reset.
        assertEquals(899990.0, mapper.toSystemTimeMilliseconds(
                4990.0, TimestampDomain.HARDWARE_CLOCK, 901000.0), 1e-9);

        mapper.reset();
        assertEquals(902000.0, mapper.toSystemTimeMilliseconds(
                10.0, TimestampDomain.HARDWARE_CLOCK, 902000.0), 1e-9);
    }

    @Test
    public void missingTargetDirectionIsNotAPlanningFailure() {
        LocalPlanner.PathResult result = LocalPlanner.PathResult.waitingForTarget();

        assertFalse(result.planned);
        assertFalse(result.success);
        assertFalse(result.blocked);
        assertTrue(result.worldPath.isEmpty());
        assertEquals("等待导航目标方向", result.waitingReason);
    }

    @Test
    public void plannerRunsOnlyAfterDirectionIsSupplied() {
        int[][] map = new int[80][80];
        LocalPlanner.PathResult result = new LocalPlanner().plan(
                map, 0.2f, -7.9f, -7.9f, 0f, 0f, 0f, 1f);

        assertTrue(result.planned);
        assertTrue(result.success);
        assertFalse(result.worldPath.isEmpty());
    }

    @Test
    public void visualizationCopiesMapAndUsesSourcePathMarkers() {
        int[][] source = {
                {0, 10, 20, 30},
                {40, 50, 60, 70},
                {80, 90, 100, 110},
                {120, 121, 122, 123}
        };
        int[][] result = LocalPlanner.visualize(source, Arrays.asList(
                new float[]{1f, 1f},
                new float[]{2f, 1f},
                new float[]{2f, 2f}), 1f, 0f, 0f);

        assertNotSame(source, result);
        assertNotSame(source[0], result[0]);
        assertEquals(50, source[1][1]);
        assertEquals(127, result[1][1]);
        assertEquals(127, result[1][2]);
        assertEquals(127, result[2][2]);
        assertEquals(10, result[0][1]);
    }

    @Test
    public void allInBoundsPathPointsUseTheSamePathMarker() {
        int[][] source = new int[4][4];
        int[][] result = LocalPlanner.visualize(source, Arrays.asList(
                new float[]{-1f, -1f},
                new float[]{1f, 1f}), 1f, 0f, 0f);

        assertEquals(127, result[1][1]);
    }

    @Test
    public void historicalSearchPenaltyDoesNotBecomePurePursuitObstacle() {
        int[][] unknownMap = new int[80][80];
        for (int[] row : unknownMap) Arrays.fill(row, -1);
        LocalPlanner planner = new LocalPlanner();

        LocalPlanner.PathResult first = planner.plan(
                unknownMap, 0.2f, -7.9f, -7.9f, 0f, 0f, 0f, 1f);
        LocalPlanner.PathResult second = planner.plan(
                unknownMap, 0.2f, -7.9f, -7.9f, 0f, 0f, 0f, 1f);

        assertTrue(first.success);
        assertTrue(second.success);
        assertFalse(second.blocked);
    }

    @Test
    public void traversableRoadCostIsNotRejectedAfterAStarFindsIt() {
        int[][] roadMap = new int[80][80];
        for (int[] row : roadMap) Arrays.fill(row, 60);

        LocalPlanner.PathResult result = new LocalPlanner().plan(
                roadMap, 0.2f, -7.9f, -7.9f, 0f, 0f, 0f, 1f);

        assertTrue(result.success);
        assertFalse(result.blocked);
        assertFalse(result.worldPath.isEmpty());
        assertEquals(60, result.startCost);
    }

    @Test
    public void openUniformMapPathStaysCloseToStraightTargetRay() {
        int[][] openMap = new int[80][80];
        float dirX = 0.5f;
        float dirY = 1f;

        LocalPlanner.PathResult result = new LocalPlanner().plan(
                openMap, 0.2f, -7.9f, -7.9f, 0f, 0f, dirX, dirY);

        assertTrue(result.success);
        assertFalse(result.worldPath.isEmpty());
        float directionLength = (float) Math.hypot(dirX, dirY);
        float maxCrossTrack = 0f;
        for (float[] point : result.worldPath) {
            float crossTrack = Math.abs(dirY * point[0] - dirX * point[1])
                    / directionLength;
            maxCrossTrack = Math.max(maxCrossTrack, crossTrack);
        }
        assertTrue("open-grid path deviated " + maxCrossTrack + "m from target ray",
                maxCrossTrack <= 0.25f);
    }

    @Test(timeout = 2000L)
    public void repeatedPlanningOnMixedCostGridDoesNotReexpandStaleQueueEntries() {
        int[][] map = new int[80][80];
        for (int row = 0; row < map.length; row++) {
            for (int col = 0; col < map[row].length; col++) {
                map[row][col] = (row * 17 + col * 31) % 80;
            }
        }
        LocalPlanner planner = new LocalPlanner();
        LocalPlanner.PathResult first = planner.plan(
                map, 0.2f, -7.9f, -7.9f, 0f, 0f, 1f, 0f);
        LocalPlanner.PathResult second = planner.plan(
                map, 0.2f, -7.9f, -7.9f, 0f, 0f, 1f, 0f);

        assertTrue(first.success);
        assertTrue(second.success);
    }

    @Test(timeout = 2000L)
    public void purePursuitCannotLoopForeverAtBlockedPathEnd() {
        int[][] map = new int[80][80];
        fillObstacle(map, 42, 55, 36, 44);

        LocalPlanner.PathResult result = new LocalPlanner().plan(
                map, 0.2f, -7.9f, -7.9f, 0f, 0f, 0f, 1f);

        assertTrue(result.planned);
    }

    @Test(timeout = 2000L)
    public void clearingPlannerStateDoesNotWaitForPlanningCalculation() throws Exception {
        int[][] map = new int[80][80];
        for (int row = 0; row < map.length; row++) {
            for (int col = 0; col < map[row].length; col++) {
                map[row][col] = (row * 17 + col * 31) % 80;
            }
        }
        LocalPlanner planner = new LocalPlanner();
        CountDownLatch started = new CountDownLatch(1);
        Thread planning = new Thread(() -> {
            started.countDown();
            planner.plan(map, 0.2f, -7.9f, -7.9f, 0f, 0f, 1f, 0f);
        });
        planning.start();
        started.await(1, TimeUnit.SECONDS);

        planner.clearTargetPath();
        planning.join(1500L);

        assertFalse(planning.isAlive());
    }

    @Test
    public void localMapDisplayPlacesForwardAtTop() {
        assertEquals(79, LocalPlanView.displayRow(80, 0));
        assertEquals(0, LocalPlanView.displayRow(80, 79));
    }

    @Test
    public void egoMapYawUsesCameraRightAxisAfterImuExtrinsic() {
        double[] identityEstimatorPose = {
                0.0, 0.0, 0.0,
                0.0, 0.0, 0.0, 1.0,
                0.0, 0.0, 0.0,
                1.0, 0.0
        };
        double[] cameraToImuQuarterTurn = {
                0.0, -1.0, 0.0,
                1.0,  0.0, 0.0,
                0.0,  0.0, 1.0
        };
        VinsMono.Pose pose = new VinsMono.Pose(
                identityEstimatorPose, cameraToImuQuarterTurn,
                new double[]{0.0, 0.0, 0.0});

        assertEquals(Math.PI / 2.0, pose.egoRightAxisYawRadians(), 1e-6);
    }

    @Test
    public void semanticFrameUsesInterpolatedVinsPoseAtItsOwnTimestamp() {
        VinsPoseHistory history = new VinsPoseHistory();
        history.add(poseAt(10.0, 0.0, 0.0));
        history.add(poseAt(12.0, 10.0, 90.0));

        VinsMono.Pose pose = history.at(11.0);

        assertNotNull(pose);
        assertEquals(5.0, pose.x, 1e-9);
        assertEquals(45.0, Math.toDegrees(pose.egoRightAxisYawRadians()), 1e-5);
    }

    @Test
    public void semanticFrameWithoutBracketingVinsPosesIsNotProjected() {
        VinsPoseHistory history = new VinsPoseHistory();
        history.add(poseAt(10.0, 0.0, 0.0));
        history.add(poseAt(12.0, 10.0, 90.0));

        assertNull(history.at(9.0));
        assertNull(history.at(13.0));
        history.clear();
        assertNull(history.at(11.0));
    }

    @Test
    public void gpsTimestampCanUseNearestVinsPoseWithinBoundedSkew() {
        VinsPoseHistory history = new VinsPoseHistory();
        history.add(poseAt(10.0, 1.0, 0.0));
        history.add(poseAt(11.0, 2.0, 0.0));

        assertEquals(1.0, history.atOrNearest(9.7, 0.5).x, 1e-9);
        assertEquals(2.0, history.atOrNearest(11.4, 0.5).x, 1e-9);
        assertNull(history.atOrNearest(11.6, 0.5));
    }

    @Test
    public void obstacleAheadAndToRightProducesLeftSteering() {
        int[][] map = new int[80][80];
        fillObstacle(map, 45, 60, 40, 70);

        LocalPlanner.PathResult result = new LocalPlanner().plan(
                map, 0.2f, -7.9f, -7.9f, 0f, 0f, 0f, 1f);

        assertTrue(result.success);
        assertTrue(result.steeringDegrees > 10f);
    }

    @Test
    public void obstacleAheadAndToLeftProducesRightSteering() {
        int[][] map = new int[80][80];
        fillObstacle(map, 45, 60, 10, 40);

        LocalPlanner.PathResult result = new LocalPlanner().plan(
                map, 0.2f, -7.9f, -7.9f, 0f, 0f, 0f, 1f);

        assertTrue(result.success);
        assertTrue(result.steeringDegrees < -10f);
    }

    @Test
    public void gridRoundingMatchesPythonTiesToEven() {
        assertEquals(2, LocalPlannerGrid.pythonRound(2.5f));
        assertEquals(4, LocalPlannerGrid.pythonRound(3.5f));
        assertEquals(-2, LocalPlannerGrid.pythonRound(-2.5f));
    }

    @Test
    public void sourcePathLifetimeDropsStaleRetentionAfterThirtySeconds() {
        int[][] unknownMap = new int[80][80];
        for (int[] row : unknownMap) Arrays.fill(row, -1);
        AtomicLong clock = new AtomicLong();
        LocalPlanner planner = new LocalPlanner(clock::get);

        LocalPlanner.PathResult initialLeft = planner.plan(
                unknownMap, 0.2f, -7.9f, -7.9f, 0f, 0f, -1f, 1f);
        clock.set(TimeUnit.SECONDS.toNanos(31));
        LocalPlanner.PathResult newRight = planner.plan(
                unknownMap, 0.2f, -7.9f, -7.9f, 0f, 0f, 1f, 1f);

        assertTrue(initialLeft.steeringDegrees > 10f);
        assertTrue(newRight.steeringDegrees < -10f);
    }

    @Test
    public void preprocessingInflatesOnlyHardObstacles() {
        int[][] source = new int[9][9];
        source[4][4] = 100;
        source[4][5] = 10;
        source[0][0] = -1;

        int[][] result = LocalPlannerGrid.preprocess(source);

        assertEquals(100, result[4][4]);
        assertEquals(10, result[4][5]);
        assertEquals(20, result[4][1]);
        assertEquals(50, result[0][0]);
        assertEquals(0, result[8][8]);
    }

    @Test
    public void sourceInflationLeavesTheSafetyBandTraversable() {
        int[][] source = new int[15][15];
        source[7][4] = 100;
        source[7][9] = 100;

        int[][] result = LocalPlannerGrid.preprocess(source);

        assertEquals(100, result[7][4]);
        assertEquals(20, result[7][5]);
        assertEquals(20, result[7][8]);
        assertEquals(100, result[7][9]);
    }

    @Test
    public void unknownDepthRemainsUnknownAfterInflation() {
        int[][] source = new int[15][15];
        for (int[] row : source) Arrays.fill(row, -1);
        source[7][7] = 100;

        int[][] result = LocalPlannerGrid.preprocess(source);

        assertEquals(50, result[7][9]);
        assertEquals(50, result[7][10]);
        assertEquals(50, result[7][11]);
    }

    @Test
    public void bodyClearanceDoesNotSealAWideDoorway() {
        int[][] source = new int[21][21];
        source[10][4] = 100;
        source[10][16] = 100;

        int[][] result = LocalPlannerGrid.preprocess(source);

        assertFalse(Env.isObstacleCost(result[10][10]));
        assertEquals(0, result[10][10]);
    }

    @Test
    public void sourceInflationAlsoCoversSoftOccupiedCells() {
        int[][] source = new int[9][9];
        source[4][4] = 20;
        source[2][6] = 10;
        source[7][1] = 5;

        int[][] result = LocalPlannerGrid.preprocess(source);

        assertEquals(20, result[4][4]);
        assertEquals(10, result[2][6]);
        assertEquals(5, result[7][1]);
        assertEquals(20, result[4][3]);
        assertEquals(20, result[2][5]);
        assertEquals(20, result[7][2]);
    }

    @Test
    public void routeBearingUsesMatchedRouteTangentWhenGpsErrorIsWithinAccuracy() {
        double longitudeError = 30.0 / (111194.9 * Math.cos(Math.toRadians(60.0)));
        List<AmapRouteClient.GeoPoint> points = Arrays.asList(
                new AmapRouteClient.GeoPoint(60.0, 10.0, ""),
                new AmapRouteClient.GeoPoint(60.001, 10.0, ""));
        AmapRouteClient.RouteStep step = new AmapRouteClient.RouteStep(
                "向北直行", 111, "直行", points);
        AmapRouteClient.RouteResult route = new AmapRouteClient.RouteResult(
                "终点", 60.001, 10.0, 111, 80, "向北直行", 0f,
                Collections.singletonList(step), points);
        RouteFollower follower = new RouteFollower();
        follower.setRoute(route);

        RouteFollower.Guidance guidance = follower.update(
                60.0, 10.0 + longitudeError, 30f, Float.NaN);

        assertFalse(guidance.offRoute);
        assertEquals(0.0, guidance.targetBearingDegrees, 1.0);
    }

    @Test
    public void octomapSemanticColorUsesSourceBgrLookupOrder() {
        // OctoMap exports RGB (220,20,60); source map_transform.py looks up
        // node_color as (b,g,r), packed here as 0x3c14dc.
        assertEquals(0x3c14dc, MapTransform.sourceLookupColor(220, 20, 60));
    }

    @Test
    public void semanticColorRoundTripMatchesSourceConfigRgb() {
        int sourceRgb = 0xdc143c;
        int treeColor = SemanticPointCloud.sourceTreeColor(sourceRgb);

        assertEquals(0x3c14dc, treeColor);
        assertEquals(sourceRgb, MapTransform.sourceLookupColor(
                (treeColor >>> 16) & 0xff,
                (treeColor >>> 8) & 0xff,
                treeColor & 0xff));
    }

    @Test
    public void zeroDepthFrameProducesNoSemanticCloudAllocation() {
        SemanticPointCloud.Data cloud = SemanticPointCloud.generate(
                new int[4], new float[4], 2, 2,
                new byte[8], 2, 2, 4, 0.001f, new Intrinsic(),
                0, 0, 2, 2, new int[]{0});

        assertEquals(0, cloud.xyz.length);
        assertEquals(0, cloud.semanticRgb.length);
        assertEquals(0, cloud.confidence.length);
    }

    @Test
    public void fullFrameModelKeepsEntireD455Image() {
        assertArrayEquals(new int[]{0, 0, 640, 480},
                SemanticSegmenter.modelAspectCrop(640, 480, 640, 480));
    }

    @Test
    public void squareModelRetainsLegacyCenteredCrop() {
        assertArrayEquals(new int[]{80, 0, 480, 480},
                SemanticSegmenter.modelAspectCrop(640, 480, 320, 320));
    }

    @Test
    public void depthValidityCountsOnlyNonZeroZ16Samples() {
        byte[] depth = {
                0, 0, 1, 0, 0, 2, 0, 0,
                3, 0, 0, 0, 4, 1, 0, 0
        };

        assertEquals(4, DepthFrameStats.countValidSamples(depth, 4, 2, 8, 1));
        assertEquals(1, DepthFrameStats.countValidSamples(depth, 4, 2, 8, 2));
        assertEquals(0, DepthFrameStats.countValidSamples(null, 4, 2, 8, 1));
    }

    @Test
    public void unpublishedImageDoesNotConsumeVinsImu() {
        VinsInputBuffer buffer = new VinsInputBuffer();
        buffer.addAccelerometer(0.0, 0f, 0f, 9.8f);
        buffer.addGyroscope(50.0, 0f, 0f, 0f);
        buffer.addAccelerometer(100.0, 0f, 0f, 9.8f);
        buffer.addGyroscope(150.0, 0f, 0f, 0f);
        buffer.addAccelerometer(200.0, 0f, 0f, 9.8f);
        buffer.addImage(75.0, new byte[3], 1, 1, 3, null);
        buffer.addImage(125.0, new byte[3], 1, 1, 3, null);

        VinsInputBuffer.ImageSample unpublished = buffer.pollReadyImage(0.0);
        VinsInputBuffer.ImageSample published = buffer.pollReadyImage(0.0);
        VinsInputBuffer.Measurement measurement =
                buffer.consumeMeasurementForFeature(published, 0.0);

        assertNotNull(unpublished);
        assertNotNull(published);
        assertNotNull(measurement);
        assertEquals(2, measurement.imu.size());
        assertEquals(0.05, measurement.imu.get(0).timestampSeconds, 1e-9);
        assertEquals(0.15, measurement.imu.get(1).timestampSeconds, 1e-9);
    }

    @Test
    public void staleImageDoesNotBlockNextReadyImage() {
        VinsInputBuffer buffer = new VinsInputBuffer();
        buffer.addAccelerometer(100.0, 0f, 0f, 9.8f);
        buffer.addGyroscope(150.0, 0f, 0f, 0f);
        buffer.addAccelerometer(200.0, 0f, 0f, 9.8f);
        buffer.addGyroscope(250.0, 0f, 0f, 0f);
        buffer.addAccelerometer(300.0, 0f, 0f, 9.8f);
        buffer.addImage(50.0, new byte[3], 1, 1, 3, null);
        buffer.addImage(175.0, new byte[3], 1, 1, 3, null);

        VinsInputBuffer.ImageSample ready = buffer.pollReadyImage(0.0);

        assertNotNull(ready);
        assertEquals(0.175, ready.timestampSeconds, 1e-9);
    }

    @Test
    public void vinsImageQueueRetainsTwoSecondProcessingBurst() {
        VinsInputBuffer buffer = new VinsInputBuffer();
        for (int i = 0; i < 30; i++) {
            buffer.addImage(i * 66.0, new byte[3], 1, 1, 3, null);
        }

        VinsInputBuffer.Status status = buffer.status();
        assertEquals(30, status.queuedImages);
        assertEquals(0, status.droppedImages);
    }

    @Test
    public void vinsImuPairingUsesEstimatedTimeOffsetLikeSourceNode() {
        VinsInputBuffer buffer = new VinsInputBuffer();
        buffer.addAccelerometer(0.0, 0f, 0f, 9.8f);
        buffer.addGyroscope(50.0, 0f, 0f, 0f);
        buffer.addAccelerometer(100.0, 0f, 0f, 9.8f);
        buffer.addGyroscope(150.0, 0f, 0f, 0f);
        buffer.addAccelerometer(200.0, 0f, 0f, 9.8f);
        buffer.addGyroscope(250.0, 0f, 0f, 0f);
        buffer.addAccelerometer(300.0, 0f, 0f, 9.8f);
        buffer.addImage(125.0, new byte[3], 1, 1, 3, null);

        VinsInputBuffer.ImageSample image = buffer.pollReadyImage(0.05);
        VinsInputBuffer.Measurement measurement =
                buffer.consumeMeasurementForFeature(image, 0.05);

        assertNotNull(image);
        assertNotNull(measurement);
        assertEquals(3, measurement.imu.size());
        assertEquals(0.15, measurement.imu.get(1).timestampSeconds, 1e-9);
        assertEquals(0.25, measurement.imu.get(2).timestampSeconds, 1e-9);
    }

    @Test
    public void imuInterpolationRunsWhenGyroscopeArrivesAfterAccelBracket() {
        VinsInputBuffer buffer = new VinsInputBuffer();
        buffer.addAccelerometer(0.0, 0f, 0f, 9.7f);
        buffer.addAccelerometer(100.0, 0f, 0f, 9.9f);

        buffer.addGyroscope(50.0, 1f, 2f, 3f);
        buffer.addAccelerometer(200.0, 0f, 0f, 10.1f);
        buffer.addGyroscope(150.0, 4f, 5f, 6f);

        VinsInputBuffer.Status status = buffer.status();
        assertEquals(2, status.unifiedImu);
        buffer.addImage(125.0, new byte[3], 1, 1, 3, null);
        VinsInputBuffer.ImageSample image = buffer.pollReadyImage(0.0);
        assertNotNull(image);
    }

    @Test
    public void featureFrameWaitsForTrailingImuLikeSourceConditionVariable() throws Exception {
        VinsInputBuffer buffer = new VinsInputBuffer();
        buffer.addAccelerometer(0.0, 0f, 0f, 9.8f);
        buffer.addAccelerometer(100.0, 0f, 0f, 9.8f);
        buffer.addGyroscope(50.0, 0f, 0f, 0f);
        VinsInputBuffer.ImageSample image = new VinsInputBuffer.ImageSample(
                0.125, new byte[1], 1, 1, 1, null);
        AtomicReference<VinsInputBuffer.Measurement> result = new AtomicReference<>();
        CountDownLatch waiting = new CountDownLatch(1);
        Thread consumer = new Thread(() -> {
            waiting.countDown();
            result.set(buffer.awaitMeasurementForFeature(image, 0.0));
        });
        consumer.start();
        assertTrue(waiting.await(1, TimeUnit.SECONDS));

        buffer.addAccelerometer(200.0, 0f, 0f, 9.8f);
        buffer.addGyroscope(150.0, 0f, 0f, 0f);

        consumer.join(1000L);
        assertFalse(consumer.isAlive());
        assertNotNull(result.get());
        assertEquals(2, result.get().imu.size());
    }

    @Test
    public void astarTieBreakingKeepsAnOptimalStraightRoute() {
        int[][] map = new int[7][7];
        List<int[]> path = new AStar(
                new int[]{0, 0}, new int[]{6, 3}, map, Collections.emptySet(),
                AStar.Heuristic.EUCLIDEAN, 3.0).searching();

        assertEquals(7, path.size());
        assertTrue(Arrays.equals(new int[]{6, 3}, path.get(0)));
        assertTrue(Arrays.equals(new int[]{0, 0}, path.get(path.size() - 1)));
        for (int[] point : path) {
            double crossTrackCells = Math.abs(3.0 * point[0] - 6.0 * point[1])
                    / Math.hypot(6.0, 3.0);
            assertTrue(crossTrackCells <= 0.45);
        }
    }

    @Test
    public void dynamicCalibrationAlignsVinsWorldToNorthWithoutPhoneHeading() {
        DynamicHeadingCalibrator calibrator = new DynamicHeadingCalibrator();
        calibrator.start();
        double latitude = 34.0;
        double longitude = 113.0;
        double metersToLatitude = 1.0 / 111194.9;
        calibrator.update(latitude, longitude, 3f, 0.0, 0.0, true);
        calibrator.update(latitude + 5.0 * metersToLatitude, longitude, 3f, 5.0, 0.0, true);
        calibrator.update(latitude + 10.0 * metersToLatitude, longitude, 3f, 10.0, 0.0, true);
        calibrator.update(latitude + 15.0 * metersToLatitude, longitude, 3f, 15.0, 0.0, true);

        assertTrue(calibrator.isReady());
        assertEquals(-90.0, calibrator.northOffsetDegrees(), 1.0);

        double[] identityPose = {
                0.0, 0.0, 0.0,
                0.0, 0.0, 0.0, 1.0,
                0.0, 0.0, 0.0,
                1.0, 0.0
        };
        VinsMono.Pose pose = new VinsMono.Pose(identityPose,
                new double[]{1, 0, 0, 0, 1, 0, 0, 0, 1},
                new double[]{0, 0, 0});
        // Geographic north is +90 degrees in this VINS world, so the camera must turn right.
        assertEquals(90.0, calibrator.relativeTargetDegrees(0f, pose), 1.0);
    }

    @Test
    public void dynamicCalibrationRejectsPoorGpsAndResetsWithVins() {
        DynamicHeadingCalibrator calibrator = new DynamicHeadingCalibrator();
        calibrator.start();
        calibrator.update(34.0, 113.0, 30f, 0.0, 0.0, true);
        assertFalse(calibrator.isReady());

        calibrator.resetForVinsRestart();
        assertFalse(calibrator.isReady());
        assertTrue(calibrator.status().contains("重新动态标定"));
    }

    @Test
    public void dynamicCalibrationRecoversFromEarlyInconsistentSegments() {
        DynamicHeadingCalibrator calibrator = new DynamicHeadingCalibrator();
        calibrator.start();
        double latitude = 34.0;
        double longitude = 113.0;
        double metersToLatitude = 1.0 / 111194.9;
        calibrator.update(latitude, longitude, 3f, 0.0, 0.0, true);
        calibrator.update(latitude + 5.0 * metersToLatitude, longitude, 3f, 5.0, 0.0, true);
        calibrator.update(latitude + 10.0 * metersToLatitude, longitude, 3f, 0.0, 0.0, true);
        calibrator.update(latitude + 15.0 * metersToLatitude, longitude, 3f, 0.0, 5.0, true);
        calibrator.update(latitude + 20.0 * metersToLatitude, longitude, 3f, 0.0, 10.0, true);
        assertFalse(calibrator.isReady());

        calibrator.update(latitude + 25.0 * metersToLatitude, longitude, 3f, 0.0, 15.0, true);

        assertTrue(calibrator.isReady());
        assertEquals(0.0, calibrator.northOffsetDegrees(), 1.0);
    }

    @Test
    public void indoorAlignedCalibrationUsesPhoneHeadingOnlyAtAlignmentInstant() {
        double[] identityPose = {
                0.0, 0.0, 0.0,
                0.0, 0.0, 0.0, 1.0,
                0.0, 0.0, 0.0,
                1.0, 0.0
        };
        VinsMono.Pose pose = new VinsMono.Pose(identityPose,
                new double[]{1, 0, 0, 0, 1, 0, 0, 0, 1},
                new double[]{0, 0, 0});
        DynamicHeadingCalibrator calibrator = new DynamicHeadingCalibrator();

        assertTrue(calibrator.calibrateAligned(30f, pose));
        assertEquals(30.0, calibrator.northOffsetDegrees(), 1e-6);
        assertEquals(0.0, calibrator.relativeTargetDegrees(30f, pose), 1e-6);

        calibrator.resetForVinsRestart();
        assertFalse(calibrator.isReady());
    }

    private static void fillObstacle(int[][] map, int firstRow, int lastRow,
                                     int firstCol, int lastCol) {
        for (int row = firstRow; row <= lastRow; row++) {
            for (int col = firstCol; col <= lastCol; col++) map[row][col] = 100;
        }
    }

    private static VinsMono.Pose poseAt(double timestamp, double x, double yawDegrees) {
        double halfYaw = Math.toRadians(yawDegrees) / 2.0;
        double[] value = {
                x, 0.0, 0.0,
                0.0, 0.0, Math.sin(halfYaw), Math.cos(halfYaw),
                0.0, 0.0, 0.0,
                1.0, timestamp
        };
        return new VinsMono.Pose(value,
                new double[]{1, 0, 0, 0, 1, 0, 0, 0, 1},
                new double[]{0, 0, 0});
    }
}
