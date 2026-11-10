"""Offline checks of measurement identities, timing boundaries and power validity."""
import unittest
from measure_ppt_metrics import analyze, navigation_ready, parse_events


def line(wall, message, pid=123):
    return f'11-05 12:00:00.000 {pid} 456 I NavigationAudit: {wall} {message}'


def resource(uptime, charge, ac='false', status='3'):
    return dict(raw=f'{uptime} 0\ncpu  100 0 10 {int(uptime)*10} 0 0 0 0\n'
                f'cpu0 100 0 10 100 0 0 0 0\nMemTotal: 4096 kB\nMemAvailable: 1024 kB\n'
                f'Charge counter: {charge}\nvoltage: 4000\nstatus: {status}\n'
                f'AC powered: {ac}\nUSB powered: false\nWireless powered: false\n')


class MeasurementTest(unittest.TestCase):
    def test_inference_map_and_draw_are_distinct(self):
        text = '\n'.join([
            line(1000000000000, 'SWIN_INFERENCE_AUDIT frame=1 generation=1 prep_ms=1.5 model_ms=3000.5 decode_ms=0.6 capture_age_ms=3020'),
            line(1000000000100, 'FRAME_AUDIT frame=1 generation=1 model_ms=3000 prep_ms=1 decode_ms=0 cloud_ms=40 map_ms=100 age_ms=3120'),
            line(1000000000200, 'MAP_DRAW_AUDIT frame=1 generation=1 capture_to_draw_ms=3220'),
            line(1000000000210, 'MAP_DRAW_AUDIT frame=1 generation=1 capture_to_draw_ms=3230'),
            line(1000000000300, 'MAP_DRAW_AUDIT frame=1.2 capture_to_draw_ms=200'),
            line(1000000000400, 'STALE_MAP_DRAW_AUDIT frame=1 capture_to_draw_ms=3420'),
            line(1000000003000, 'SWIN_INFERENCE_AUDIT frame=2 generation=1 prep_ms=2 model_ms=2900.8 decode_ms=0.7 capture_age_ms=2930'),
            line(1000000003300, 'FRAME_AUDIT frame=2 generation=1 model_ms=2900 prep_ms=2 decode_ms=0 cloud_ms=50 map_ms=300 age_ms=3230'),
            line(1000000003400, 'MAP_DRAW_AUDIT frame=2 generation=1 capture_to_draw_ms=3330'),
            line(1000000003405, 'PLAN_DRAW_AUDIT frame=2 generation=1 capture_to_draw_ms=3335'),
            # An inference that failed/did not reach mapping must remain counted.
            line(1000000006000, 'SWIN_INFERENCE_AUDIT frame=3 generation=1 model_ms=2800.4'),
        ])
        report, events = analyze(text)
        self.assertEqual(report['semantic_model_ms']['all']['n'], 3)
        self.assertAlmostEqual(report['semantic_model_ms']['first20_values'][0], 3000.5)
        self.assertEqual(report['semantic_model_ms']['continuous']['n'], 2)
        self.assertEqual(report['semantic_refresh']['interval_ms']['mean'], 3200)
        self.assertEqual(report['semantic_map_new_draw_refresh']['distinct_frames'], 2)
        self.assertEqual(report['semantic_map_new_draw_refresh']['interval_ms']['mean'], 3200)
        self.assertEqual(report['unmatched_draw_events'], 1)
        self.assertEqual(len(events['STALE_MAP_DRAW_AUDIT']), 1)
        self.assertEqual(len(events['MAP_DRAW_AUDIT']), 4)
        self.assertTrue(navigation_ready(text))

    def test_generation_reset_and_duplicate_events(self):
        text = '\n'.join([
            line(1000000000000, 'FRAME_AUDIT frame=1 generation=1 model_ms=3000'),
            line(1000000000100, 'FRAME_AUDIT frame=1 generation=1 model_ms=3000'),
            line(1000000000200, 'FRAME_AUDIT frame=1 generation=2 model_ms=3300'),
            # Missing draw generation cannot resolve repeated camera identity.
            line(1000000000300, 'MAP_DRAW_AUDIT frame=1 capture_to_draw_ms=3400'),
            line(1000000000400, 'MAP_DRAW_AUDIT frame=1 generation=2 capture_to_draw_ms=3500')])
        report, _ = analyze(text)
        self.assertEqual(report['duplicate_frame_events'], 1)
        self.assertIsNone(report['semantic_refresh']['interval_ms'])
        self.assertEqual(report['semantic_model_ms']['first_observed_per_generation']['n'], 2)
        self.assertIsNone(report['semantic_model_ms']['continuous'])
        self.assertEqual(report['unmatched_draw_events'], 1)
        self.assertEqual(report['semantic_map_new_draw_refresh']['distinct_frames'], 1)

    def test_java_full_precision_draw_matches_six_decimal_frame_log(self):
        text = '\n'.join([
            line(1000000000000, 'FRAME_AUDIT frame=1793845008.570716 generation=1 model_ms=3000'),
            line(1000000000100, 'MAP_DRAW_AUDIT frame=1.7938450085707164E9 generation=1 capture_to_draw_ms=3100')])
        report, _ = analyze(text)
        self.assertEqual(report['semantic_map_new_draw_refresh']['distinct_frames'], 1)
        self.assertEqual(report['unmatched_draw_events'], 0)

    def test_no_legacy_live_or_ui_tick_readiness(self):
        self.assertFalse(navigation_ready(line(1000000000000, 'LIVE_PLAN_DRAW frame=1')))
        self.assertFalse(navigation_ready(line(1000000000000, 'SWIN_INFERENCE_AUDIT frame=1 generation=1 model_ms=3000')))
        self.assertFalse(navigation_ready('\n'.join([
            line(1000000000000, 'FRAME_AUDIT frame=1 generation=1 model_ms=3000'),
            line(1000000000010, 'MAP_DRAW_AUDIT frame=100 capture_to_draw_ms=10') ])))

    def test_power_is_not_reported_when_charging_or_counters_missing(self):
        import json
        records = '\n'.join(json.dumps(r) for r in [resource(100, 1000000), resource(340, 990000)])
        report, _ = analyze('', records)
        power = report['battery_discharge']
        self.assertTrue(power['valid_discharge_window'])
        self.assertTrue(power['ppt_240s_window'])
        self.assertAlmostEqual(power['estimated_whole_device_w'], .6)
        charging = '\n'.join(json.dumps(r) for r in [resource(100, 1000000, 'true', '2'), resource(340, 990000)])
        report, _ = analyze('', charging)
        self.assertIsNone(report['battery_discharge']['estimated_whole_device_w'])
        report, _ = analyze('', '')
        self.assertIsNone(report['battery_discharge']['estimated_whole_device_w'])
        self.assertFalse(report['actual_rgb_depth_evidence'])

    def test_short_discharge_window_does_not_produce_power(self):
        import json
        for duration in (30, 239.99):
            records = '\n'.join(json.dumps(r) for r in
                                [resource(100, 1000000), resource(100+duration, 990000)])
            report, _ = analyze('', records)
            power = report['battery_discharge']
            self.assertTrue(power['all_samples_discharging'])
            self.assertFalse(power['ppt_240s_window'])
            self.assertFalse(power['valid_discharge_window'])
            self.assertIsNone(power['estimated_whole_device_w'])

    def test_legacy_freshness_diagnostic_and_ground_drop_evidence(self):
        text = '\n'.join([
            line(1000000000000, 'FRAME_AUDIT frame=1 generation=1 model_ms=3000'),
            line(1000000000010, 'MAP_DRAW_AUDIT frame=1 generation=1 within_legacy_1500ms=false capture_to_draw_ms=3200'),
            line(1000000000020, 'FRAME_AUDIT frame=2 generation=1 model_ms=3000'),
            line(1000000000030, 'MAP_DRAW_AUDIT frame=2 generation=1 within_1500ms=true capture_to_draw_ms=1400'),
            line(1000000000040, 'SEMANTIC_MAP_DROP frame=3 generation=1 reason=pose_unavailable'),
            line(1000000000050, 'SOURCE_GROUND_AUDIT frame=4 generation=1 candidates=200 inliers=150')])
        report, events = analyze(text)
        diagnostic = report['legacy_1500ms_diagnostic']
        self.assertEqual(diagnostic['checked_draws'], 2)
        self.assertEqual(diagnostic['within_legacy_1500ms'], 1)
        self.assertFalse(diagnostic['used_for_movement_authorization'])
        self.assertEqual(report['semantic_map_drop_events'], 1)
        self.assertEqual(events['SEMANTIC_MAP_DROP'][0]['reason'], 'pose_unavailable')
        self.assertEqual(events['SOURCE_GROUND_AUDIT'][0]['inliers'], '150')

    def test_precise_cold_compile_counters_do_not_double_count_runner(self):
        precise = line(1000000000010, 'SWIN_INFERENCE_AUDIT frame=1 generation=1 model_ms=3000 cache_hits=40 cold_compiles=4')
        duplicate = '11-05 12:00:00.000 123 456 I SwinLPlan: complete ms=3000 cache_hits=40 cold_compiles=4 cpu_fallback=false'
        warm = line(1000000003010, 'SWIN_INFERENCE_AUDIT frame=2 generation=1 model_ms=2900 cache_hits=44 cold_compiles=0')
        report, _ = analyze('\n'.join([duplicate, precise, warm]))
        self.assertEqual(report['cold_compile_events'], 1)
        self.assertEqual(report['cold_compiled_subgraphs_total'], 4)
        self.assertEqual(report['runner_cache_hits']['n'], 2)
        self.assertIn('SWIN_INFERENCE_AUDIT', report['cache_metric_source'])
        # Measurement excludes cold startup seen only in the recording preamble.
        report, _ = analyze(warm, full_text='\n'.join([duplicate, precise, warm]))
        self.assertEqual(report['cold_compile_events'], 1)
        self.assertEqual(report['cold_compile_events_measurement'], 0)
        legacy, _ = analyze(duplicate)
        self.assertEqual(legacy['cold_compile_events'], 1)
        self.assertEqual(legacy['cold_compiled_subgraphs_total'], 4)

    def test_plain_file_audit_and_nonfinite_values(self):
        events = parse_events('1000000000000 FRAME_AUDIT frame=1 generation=1 age_ms=NaN model_ms=none')
        self.assertEqual(events['FRAME_AUDIT'][0]['wall_ms'], 1000000000000)
        report, _ = analyze('1000000000000 FRAME_AUDIT frame=1 generation=1 age_ms=NaN model_ms=none')
        self.assertIsNone(report['semantic_model_ms']['all'])


if __name__ == '__main__':
    unittest.main()
