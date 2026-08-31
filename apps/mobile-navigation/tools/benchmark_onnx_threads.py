#!/usr/bin/env python3
"""Benchmark one ONNX model with multiple CPU thread counts and compare outputs."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort


def run(model: Path, image: np.ndarray, threads: int) -> tuple[list[np.ndarray], float]:
    options = ort.SessionOptions()
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    options.add_session_config_entry("session.inter_op.allow_spinning", "0")
    session = ort.InferenceSession(
        str(model), sess_options=options, providers=["CPUExecutionProvider"]
    )
    started = time.perf_counter()
    outputs = session.run(None, {session.get_inputs()[0].name: image})
    return outputs, time.perf_counter() - started


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("model", type=Path)
    parser.add_argument("--baseline-threads", type=int, default=1)
    parser.add_argument("--candidate-threads", type=int, default=4)
    args = parser.parse_args()

    session = ort.InferenceSession(str(args.model), providers=["CPUExecutionProvider"])
    shape = session.get_inputs()[0].shape
    if shape != [1, 3, 480, 480]:
        raise RuntimeError(f"unexpected model input shape: {shape}")
    del session

    rng = np.random.default_rng(9300)
    image = rng.random(shape, dtype=np.float32) * 255.0
    baseline, baseline_seconds = run(args.model, image, args.baseline_threads)
    candidate, candidate_seconds = run(args.model, image, args.candidate_threads)

    if len(baseline) != len(candidate):
        raise RuntimeError("output count changed")
    for index, (expected, actual) in enumerate(zip(baseline, candidate)):
        equal = np.array_equal(expected, actual)
        max_error = 0.0 if equal else float(
            np.max(np.abs(expected.astype(np.float64) - actual.astype(np.float64)))
        )
        print(f"output={index} equal={equal} max_error={max_error:.9f}")
        if not equal:
            raise SystemExit(1)
    print(f"baseline_threads={args.baseline_threads} seconds={baseline_seconds:.3f}")
    print(f"candidate_threads={args.candidate_threads} seconds={candidate_seconds:.3f}")
    print(f"speedup={baseline_seconds / candidate_seconds:.3f}x")


if __name__ == "__main__":
    main()
