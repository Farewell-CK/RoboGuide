"""Run unmodified eLabrador/OpenCV map filters to make Android test fixtures.

map_draw.py is imported unchanged. Only the cost-preprocessing statement block
of local_planner.py is extracted by AST, avoiding unrelated ROS/torch imports;
the extracted statements themselves are neither translated nor rewritten.
"""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import importlib.util
from pathlib import Path
import struct
import subprocess
import warnings

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SOURCE_RELATIVE = Path("edge/src/NVI/nvi_planning/src/local_planning")
SIDE = 80


def load_draw(path):
    spec = importlib.util.spec_from_file_location("elabrador_map_draw_oracle", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_preprocess(path):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    candidates = []
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        for first, statement in enumerate(body):
            if not isinstance(statement, ast.Assign):
                continue
            if not any(isinstance(target, ast.Name) and target.id == "kernel"
                       for target in statement.targets):
                continue
            value = statement.value
            if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
                    and value.func.attr == "getStructuringElement"):
                continue
            for last in range(first + 1, len(body)):
                end = body[last]
                if not isinstance(end, ast.Assign):
                    continue
                if not isinstance(end.value, ast.Constant) or end.value.value != 50:
                    continue
                target = end.targets[0]
                if (isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name)
                        and target.value.id == "map_data" and isinstance(target.slice, ast.Name)
                        and target.slice.id == "c0"):
                    candidates.append(body[first:last + 1])
                    break
    if len(candidates) != 1:
        raise ValueError(f"Expected exactly one official planner preprocessing block: {len(candidates)}")
    block = candidates[0]
    function = ast.FunctionDef(
        name="source_preprocess", args=ast.arguments(posonlyargs=[],
        args=[ast.arg(arg="map_data")], kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=copy.deepcopy(block) + [ast.Return(value=ast.Name(id="map_data", ctx=ast.Load()))],
        decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    namespace = {"cv2": cv2, "np": np}
    exec(compile(module, str(path), "exec"), namespace)
    return namespace["source_preprocess"], (block[0].lineno, block[-1].end_lineno)


def scenarios():
    rng = np.random.default_rng(20261105)

    def grid(value=0):
        return np.full((SIDE, SIDE), value, dtype=np.int8)

    for value in [-1, 0, 1, 5, 20, 50, 80, 100]:
        yield f"constant_{value}", grid(value), grid(-1 if value < 0 else value)

    # At corners the median uses replicated pixels, while Sobel reflects without
    # repeating the boundary pixel. These cases distinguish those two borders.
    positions = [(0, 0), (0, 79), (79, 0), (79, 79), (0, 40), (40, 0),
                 (79, 40), (40, 79), (1, 1), (40, 40)]
    for row, col in positions:
        cost, height = grid(), grid()
        cost[row, col] = 100
        height[row, col] = 25
        yield f"single_obstacle_{row}_{col}", cost, height
        cost = grid(-1)
        cost[max(0, row - 1):min(SIDE, row + 2), max(0, col - 1):min(SIDE, col + 2)] = 0
        yield f"free_patch_in_unknown_{row}_{col}", cost, height

    for size in [1, 2, 3, 5, 7, 9]:
        cost, height = grid(), grid()
        cost[38:38 + size, 38:38 + size] = 100
        height[38:38 + size, 38:38 + size] = 15
        yield f"square_obstacle_{size}", cost, height

    for value in [10, 12, 13, 29, 30, 31, 50]:
        cost, height = grid(), grid()
        cost[:, 40:] = 80
        height[:, 40:] = value
        yield f"vertical_step_{value}", cost, height
        yield f"horizontal_step_{value}", cost.T.copy(), height.T.copy()

    # Values 25/60 produce exact Sobel magnitudes 50/120 for the neighbouring
    # row/column, exercising the inclusive threshold endpoints.
    for value in [24, 25, 26, 59, 60, 61]:
        cost, height = grid(), grid()
        height[40, 40] = value
        cost[40, 40] = 5
        cost[40, 42] = 100
        cost[40, 44] = 20
        cost[39, 41] = -1
        yield f"exact_sobel_endpoint_{value}", cost, height

    rows, cols = np.indices((SIDE, SIDE))
    for period in [1, 2, 3, 4, 7]:
        index = ((rows // period) + (cols // period)) % 3
        cost = np.asarray([-1, 0, 100], dtype=np.int8)[index]
        height = np.asarray([-1, 0, 15], dtype=np.int8)[index]
        yield f"checkerboard_period_{period}", cost, height

    # Existing low positive cells must receive the grayscale neighbourhood max;
    # binary dilation followed by preserving the old positive value is different.
    cost, height = grid(), grid()
    cost[35:46, 35:46] = 5
    cost[40, 40] = 100
    cost[35:46, 39] = -1
    yield "grayscale_dilation_existing_low_positive", cost, height

    cost, height = grid(-1), grid(-1)
    cost[20:60, 20:60] = 0
    cost[37:43, 37:43] = 100
    height[20:60, 20:60] = 10
    height[37:43, 37:43] = 25
    yield "unknown_mask_restored_after_dilation", cost, height

    costs = np.asarray([-1, 0, 1, 5, 10, 20, 30, 50, 80, 90, 100], dtype=np.int8)
    heights = np.asarray([-1, 0, 1, 5, 10, 15, 20, 25, 30, 50, 80, 100], dtype=np.int8)
    for case in range(18):
        if case % 3 == 0:
            # Large tiles exercise interiors and narrow transition boundaries.
            cost = np.repeat(np.repeat(rng.choice(costs, (10, 10)), 8, 0), 8, 1)
            height = np.repeat(np.repeat(rng.choice(heights, (10, 10)), 8, 0), 8, 1)
        elif case % 3 == 1:
            cost = rng.choice(costs, (SIDE, SIDE), p=[.25, .45, .02, .02, .02, .02, .02, .05, .05, .04, .06])
            height = rng.integers(-1, 32, size=(SIDE, SIDE), dtype=np.int8)
        else:
            cost = rng.choice(costs, (SIDE, SIDE))
            height = rng.choice(heights, (SIDE, SIDE))
        yield f"seeded_random_{case:02d}", cost.astype(np.int8), height.astype(np.int8)


def put_text(handle, value):
    encoded = value.encode("utf-8")
    handle.write(struct.pack(">i", len(encoded)))
    handle.write(encoded)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, default=ROOT / "artifacts/elabrador-reference")
    parser.add_argument("--output", type=Path, default=ROOT / "navoutdoor/src/test/resources/source-filter-reference.bin")
    args = parser.parse_args()
    base = args.reference / SOURCE_RELATIVE
    draw_path, planner_path = base / "map_draw.py", base / "local_planner.py"
    draw = load_draw(draw_path)
    preprocess, lines = load_preprocess(planner_path)
    commit = subprocess.check_output(["git", "-C", str(args.reference), "rev-parse", "HEAD"], text=True).strip()
    cases = list(scenarios())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("wb") as handle:
        handle.write(b"SFF1")
        for value in [commit, hashlib.sha256(draw_path.read_bytes()).hexdigest(),
                      hashlib.sha256(planner_path.read_bytes()).hexdigest(),
                      f"numpy={np.__version__};opencv={cv2.__version__};planner_lines={lines[0]}-{lines[1]}"]:
            put_text(handle, value)
        handle.write(struct.pack(">i", len(cases)))
        for name, cost, height in cases:
            median = draw.median_filter(cost.copy(), 3)
            edges = draw.edge_filter(height.copy(), ksize=3, LOW_HEIGHT=50)
            planned = preprocess(cost.copy())
            assert cost.shape == height.shape == median.shape == edges.shape == planned.shape == (SIDE, SIDE)
            assert set(np.unique(edges)).issubset({0, 100})
            assert int(planned.min()) >= 0 and int(planned.max()) <= 100
            put_text(handle, name)
            for data in [cost, height, median, edges, planned]:
                handle.write(np.ascontiguousarray(data, dtype=np.int8).tobytes())
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        degenerate_cast = np.asarray([np.nan, np.inf, -np.inf]).astype(np.int8).tolist()
    print(f"commit={commit}")
    print(f"map_draw_sha256={hashlib.sha256(draw_path.read_bytes()).hexdigest()}")
    print(f"local_planner_sha256={hashlib.sha256(planner_path.read_bytes()).hexdigest()}")
    print(f"planner_statement_lines={lines[0]}-{lines[1]}")
    print(f"cases={len(cases)} cells_per_filter={len(cases) * SIDE * SIDE} filter_outputs={len(cases) * SIDE * SIDE * 3}")
    print(f"nan_posinf_neginf_to_int8={degenerate_cast} warnings={len(caught)}")
    print(f"numpy={np.__version__} opencv={cv2.__version__}")
    print(f"fixture={args.output} bytes={args.output.stat().st_size} sha256={hashlib.sha256(args.output.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
