import json
from pathlib import Path
import unittest
import numpy as np
from obstacle_policy import observe

ADE = {0: 'wall', 3: 'floor', 12: 'person', 14: 'door', 19: 'chair',
       33: 'desk', 41: 'box', 53: 'stairs', 87: 'streetlight', 121: 'step'}
ROAD = {0: 'Bird', 6: 'Wall', 13: 'Road', 19: 'Person',
        43: 'Pothole', 44: 'Street Light'}


class PolicyTest(unittest.TestCase):
    def scene(self, class_id, score=1.):
        labels = np.zeros((100, 100), dtype=int)
        labels[40:96, 40:60] = class_id
        return labels, np.full(labels.shape, score)

    def test_background_never_means_safe(self):
        result = observe(np.full((100, 100), 3), np.ones((100, 100)), ADE)
        self.assertEqual(result['level'], 'unknown')
        self.assertFalse(result['distance_available'])
        self.assertFalse(result['automatic_detour'])
        self.assertIn('不代表', result['message'])

    def test_large_person_and_indoor_furniture(self):
        for class_id, name in [(12, '行人'), (19, '椅子'), (33, '办公桌'), (41, '箱子')]:
            with self.subTest(name=name):
                result = observe(*self.scene(class_id), ADE)
                detection = result['detections'][0]
                self.assertEqual(detection['label'], name)
                self.assertEqual(detection['level'], 'caution')
                self.assertEqual(detection['box'], [.4, .4, .6, .96])
                self.assertIsNone(detection['distance_m'])

    def test_class_numbers_are_not_reused_between_checkpoints(self):
        self.assertEqual(observe(*self.scene(19), ADE)['detections'][0]['label'], '椅子')
        self.assertEqual(observe(*self.scene(19), ROAD, 'outdoor')['detections'][0]['label'], '行人')

    def test_indoor_filters_road_predictions_but_keeps_diagnostics(self):
        for class_id in (43, 44):
            result = observe(*self.scene(class_id, .99), ROAD)
            self.assertEqual(result['detections'], [])
            raw = next(p for p in result['raw_predictions'] if p['class_id'] == class_id)
            self.assertEqual(raw['disposition'], 'filtered_scene')
            self.assertEqual(raw['confidence'], .99)
        self.assertEqual(observe(*self.scene(87), ADE)['detections'], [])

    def test_tiny_low_score_or_nonfinite_patches_are_not_obstacles(self):
        for score in (.3, .64, float('nan')):
            self.assertEqual(observe(*self.scene(19, score), ADE)['detections'], [])
        labels, scores = self.scene(0)
        labels[60:67, 45:52] = 19
        result = observe(labels, scores, ADE)
        self.assertEqual(result['detections'], [])
        self.assertEqual(next(p for p in result['raw_predictions'] if p['class_id'] == 19)
                         ['disposition'], 'below_confidence_or_area')

    def test_separate_people_do_not_merge_into_center(self):
        labels, scores = self.scene(0)
        labels[40:90, 15:30] = 12
        labels[40:90, 70:85] = 12
        result = observe(labels, scores, ADE)
        self.assertEqual(len(result['detections']), 2)
        self.assertEqual({d['position'] for d in result['detections']}, {'左前方', '右前方'})
        self.assertEqual(result['level'], 'notice')

    def test_door_and_stairs_do_not_invent_depth_or_closed_state(self):
        door = observe(*self.scene(14), ADE)['message']
        self.assertIn('观察开闭状态', door)
        self.assertNotIn('已关闭', door)
        for class_id in (53, 121):
            self.assertIn('停下确认', observe(*self.scene(class_id), ADE)['message'])

    def test_top_and_edge_noise_is_outside_roi(self):
        labels, scores = self.scene(0)
        labels[:25, :] = 12
        labels[:, :10] = 12
        self.assertEqual(observe(labels, scores, ADE)['detections'], [])

    def test_explicit_outdoor_mode_remains_available(self):
        result = observe(*self.scene(43), ROAD, 'outdoor')
        self.assertEqual(result['profile'], 'outdoor')
        self.assertEqual(result['detections'][0]['label'], '坑洼')

    def test_invalid_shapes_and_profiles_fail(self):
        for labels, scores in [(np.ones(3), np.ones(3)),
                               (np.ones((2, 3)), np.ones((3, 2))),
                               (np.empty((0, 0)), np.empty((0, 0)))]:
            with self.assertRaises(ValueError):
                observe(labels, scores, ADE)
        with self.assertRaises(ValueError):
            observe(*self.scene(12), ADE, 'invalid')

    def test_pinned_indoor_config_has_expected_labels(self):
        path = Path(__file__).parent / 'model-indoor' / 'config.json'
        if not path.exists():
            self.skipTest('weights/config not downloaded in this checkout')
        names = json.loads(path.read_text())['id2label']
        self.assertEqual(len(names), 150)
        self.assertEqual(names['12'], 'person')
        self.assertEqual(names['19'], 'chair')
        self.assertEqual(names['41'], 'box')


if __name__ == '__main__':
    unittest.main()
