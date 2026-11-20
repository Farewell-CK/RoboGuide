"""Generate a binary Java oracle by executing eLabrador's original Python function.

The only stub is nvi_octomap's point/leaf-iterator API. SemanticConfig,
MetricConfig, numpy aggregation, coordinates and height encoding are loaded
unchanged from the reference checkout; no Java algorithm is copied here.
This checks cost projection, not native OctoMap insertion or model accuracy.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import types

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SOURCE_RELATIVE = Path("edge/src/NVI/nvi_planning/src/local_planning")


def f32(value):
    return struct.unpack(">f", struct.pack(">f", float(value)))[0]


class Point3d:
    def __init__(self, x, y, z):
        self.values = (x, y, z)

    def x(self):
        return self.values[0]

    def y(self):
        return self.values[1]

    def z(self):
        return self.values[2]


class LeafIterator:
    def __init__(self, leaves, index=0):
        self.leaves = leaves
        self.index = index

    def __eq__(self, other):
        # Official code compares a mutable iterator to an independent end iterator.
        return isinstance(other, LeafIterator) and self.index == other.index

    def __ne__(self, other):
        return not self == other

    def next(self):
        self.index += 1

    def get_x(self):
        return self.leaves[self.index][0]

    def get_y(self):
        return self.leaves[self.index][1]

    def get_z(self):
        return self.leaves[self.index][2]

    def get_occupancy(self):
        return self.leaves[self.index][3]

    def get_color(self):
        record = self.leaves[self.index]
        return types.SimpleNamespace(r=int(record[4]), g=int(record[5]), b=int(record[6]))


class LeafTree:
    def __init__(self, leaves):
        self.leaves = leaves
        self.selected = []

    def begin_leafs_bbx(self, low, high):
        self.selected = [
            point for point in self.leaves
            if all(a <= value <= b for value, a, b in zip(point[:3], low.values, high.values))
        ]
        return LeafIterator(self.selected)

    def end_leafs_bbx(self):
        return LeafIterator(self.selected, len(self.selected))


def load_original(source):
    api = types.ModuleType("nvi_octomap")
    api.Point3d = Point3d
    sys.modules["nvi_octomap"] = api
    package_name = "elabrador_cost_reference"
    package = types.ModuleType(package_name)
    package.__path__ = [str(source)]
    sys.modules[package_name] = package
    spec = importlib.util.spec_from_file_location(package_name + ".map_transform", source / "map_transform.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def leaf(x, y, z, color, occupancy=0.8):
    # NativeOctomap flat format is x,y,z,occupancy,r,g,b,semanticConfidence.
    # Source map_transform looks up node_color.b,g,r, so keep that byte order.
    r, g, b = color
    return tuple(f32(v) for v in (x, y, z, occupancy, b, g, r, 0.95))


def scene(name, location, yaw, leaves):
    return name, tuple(f32(v) for v in location), f32(yaw), leaves


def cases(labels):
    scenarios = [scene("empty_grid_is_unknown", (0, 0, 0), 0, [])]
    class_leaves = []
    for index, label in enumerate(labels):
        x = -5.76 + (index % 11) * 1.02
        y = -4.72 + (index // 11) * 1.03
        class_leaves.append(leaf(x, y, -1, label["color"]))
    scenarios.append(scene("all_66_extended_config_labels_model_first65", (0, 0, 0), 0, class_leaves))

    hard = next(label["color"] for label in labels if label["readable"] == "Building")
    free = next(label["color"] for label in labels if label["readable"] == "Sidewalk")
    road = next(label["color"] for label in labels if label["readable"] == "Road")
    vegetation = next(label["color"] for label in labels if label["readable"] == "Vegetation")
    dzs = [-2.5001, -2.5, -2.4999, -2, -0.4001, -0.4, -0.3999, -0.3,
           -0.2001, -0.2, -0.1999, -0.01, 0, 0.0001]
    scenarios.append(scene("curve1_full_height_boundaries", (0, 0, 0), 0,
                           [leaf(-5.75 + i * 0.83, 0.53, z, hard) for i, z in enumerate(dzs)]))
    for count in [1, 2, 3, 5, 10]:
        records = [leaf(0.14, 1.14, -1.4 + i * 0.041, free) for i in range(count)]
        records.append(leaf(0.14, 1.14, -0.61, hard))
        scenarios.append(scene(f"mixed_vertical_column_mean_one_building_{count}_sidewalk", (0, 0, 0), 0, records))
    scenarios.append(scene("same_column_height_weighted_mean", (0, 0, 0), 0, [
        leaf(0.14, 1.14, -1.4, road), leaf(0.14, 1.14, -0.3, hard),
        leaf(0.14, 1.14, -0.1, hard), leaf(0.14, 1.14, -1.1, vegetation)]))
    scenarios.append(scene("occupancy_half_threshold_and_unknown_color", (0, 0, 0), 0, [
        leaf(-3.77, 1.13, -1.1, hard, 0.499999), leaf(-2.77, 1.13, -1.1, hard, 0.5),
        leaf(-1.77, 1.13, -1.1, hard, 0.500001), leaf(0.23, 1.13, -1.1, [3, 5, 7]),
        leaf(1.23, 1.13, -1.1, hard, 0)]))
    # Distinguishes BGR handling using non-gray classes, including the road and sidewalk.
    scenarios.append(scene("bgr_semantic_lookup_non_gray", (0, 0, 0), 0, [
        leaf(-2.73 + i * 0.71, 2.17, -1.2, color)
        for i, color in enumerate([road, free, vegetation, labels[0]["color"], labels[1]["color"]])]))
    edge_records = [leaf(x, y, -1.11, hard) for x, y in [
        (-7.5001, 0.17), (-7.5, 1.17), (-7.4999, 2.17), (7.4999, 3.17),
        (7.5, 4.17), (7.5001, 5.17), (0.13, -7.5001), (1.13, -7.5),
        (2.13, -7.4999), (3.13, 7.4999), (4.13, 7.5), (5.13, 7.5001)]]
    scenarios.append(scene("world_bounding_box_edges", (0, 0, 0), 0, edge_records))

    rng = np.random.default_rng(20261105)
    # Quantize inputs before calling the original function, because JNI exports float arrays.
    for index in range(80):
        location = tuple(f32(v) for v in rng.uniform(-10, 10, 3))
        yaw = f32(rng.uniform(-math.pi, math.pi))
        records = []
        for _ in range(64 + index * 7):
            x, y = rng.uniform(-8.2, 8.2, 2)
            z = rng.uniform(-2.8, 0.2)
            label = labels[int(rng.integers(len(labels)))]
            occupancy = rng.choice([0.2, 0.499, 0.5, 0.8, 0.97])
            records.append(leaf(location[0] + x, location[1] + y,
                                location[2] + z, label["color"], occupancy))
        # Mixed colors and heights at an identical x/y stress averaging and height encoding.
        for _ in range(12):
            records.append(leaf(location[0] + 1.137, location[1] + 2.049,
                                location[2] + rng.uniform(-2.3, -0.05),
                                labels[int(rng.integers(len(labels)))]["color"]))
        scenarios.append(scene(f"random_projection_{index:02d}", location, yaw, records))
    return scenarios


def write_text(stream, text):
    raw = text.encode("utf-8")
    stream.write(struct.pack(">I", len(raw)))
    stream.write(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, default=ROOT / "artifacts/elabrador-reference")
    parser.add_argument("--output", type=Path, default=ROOT / "navoutdoor/src/test/resources/source-cost-reference.bin")
    arguments = parser.parse_args()
    source = arguments.reference / SOURCE_RELATIVE
    module = load_original(source)
    config_file = source / "config/dataconfig_mapillary_extend.json"
    labels = json.loads(config_file.read_text(encoding="utf-8"))["labels"]
    scenarios = cases(labels)
    commit = subprocess.check_output(["git", "-C", str(arguments.reference), "rev-parse", "HEAD"], text=True).strip()
    source_hash = hashlib.sha256((source / "map_transform.py").read_bytes()).hexdigest()
    config_hash = hashlib.sha256(config_file.read_bytes()).hexdigest()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    with arguments.output.open("wb") as output:
        output.write(b"SCF1")
        for text in [commit, source_hash, config_hash]:
            write_text(output, text)
        output.write(struct.pack(">I", len(labels)))
        for label in labels:
            r, g, b = label["color"]
            output.write(struct.pack(">If", (r << 16) | (g << 8) | b, label["degree"]))
        output.write(struct.pack(">I", len(scenarios)))
        leaf_count = 0
        for name, location, yaw, leaves in scenarios:
            # Unknown colors intentionally exercise skip behavior; keep their warning off stdout.
            with contextlib.redirect_stdout(io.StringIO()):
                costs, resolution, origin, heights = module.octree2localprmap_height_ego(
                    LeafTree(leaves), location, yaw)
            assert resolution == 0.2 and origin == (-7.9, -7.9)
            assert costs.shape == heights.shape == (80, 80)
            write_text(output, name)
            output.write(struct.pack(">4fI", *location, yaw, len(leaves)))
            for record in leaves:
                output.write(struct.pack(">8f", *record))
            output.write(costs.astype(np.int8).tobytes())
            output.write(heights.astype(np.int8).tobytes())
            leaf_count += len(leaves)
    print(json.dumps({"reference_commit": commit, "map_transform_sha256": source_hash,
                      "config_sha256": config_hash, "config_labels": len(labels),
                      "scenarios": len(scenarios), "leaf_records": leaf_count,
                      "grid_cells_compared": len(scenarios) * 6400,
                      "bytes": arguments.output.stat().st_size,
                      "fixture": str(arguments.output),
                      "scope": "Original Python cost projection with stub leaf iteration; not native fusion or model accuracy."},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
