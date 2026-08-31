#!/usr/bin/env python3
"""Verify that ONNX post-processing matches the previous Java implementation."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort


def elapsed_run(session: ort.InferenceSession, image: np.ndarray):
    start = time.perf_counter()
    result = session.run(None, {"image": image})
    return result, time.perf_counter() - start


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("original", type=Path)
    parser.add_argument("optimized", type=Path)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument(
        "--confidence",
        choices=("source-u8", "raw-score", "softmax"),
        default="source-u8",
    )
    args = parser.parse_args()

    rng = np.random.default_rng(9300)
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1

    original = ort.InferenceSession(args.original, options, providers=["CPUExecutionProvider"])
    optimized = ort.InferenceSession(args.optimized, options, providers=["CPUExecutionProvider"])
    input_shape = original.get_inputs()[0].shape
    if len(input_shape) != 4 or not all(isinstance(value, int) for value in input_shape):
        raise RuntimeError(f"expected a fixed NCHW input, got {input_shape}")
    original_total = 0.0
    optimized_total = 0.0
    for sample in range(args.samples):
        image = rng.random(tuple(input_shape), dtype=np.float32) * 255.0
        (logits,), original_seconds = elapsed_run(original, image)
        (actual_mask, actual_confidence), optimized_seconds = elapsed_run(optimized, image)
        original_total += original_seconds
        optimized_total += optimized_seconds

        expected_mask = np.argmax(logits, axis=0).astype(np.int32)
        if args.confidence == "source-u8":
            raw_confidence = np.max(logits, axis=0).astype(np.float32)
            expected_confidence = (
                np.fmod(np.floor(raw_confidence * np.float32(255.0)), np.float32(256.0))
                / np.float32(255.0)
            ).astype(np.float32)
        elif args.confidence == "raw-score":
            expected_confidence = np.max(logits, axis=0)
        else:
            shifted = logits.astype(np.float64) - np.max(logits, axis=0, keepdims=True)
            probability = 1.0 / np.exp(shifted).sum(axis=0)
            expected_confidence = (
                np.floor(probability * 255.0 + 0.5) / 255.0
            ).astype(np.float32)

        mask_errors = int(np.count_nonzero(expected_mask != actual_mask))
        confidence_errors = int(np.count_nonzero(expected_confidence != actual_confidence))
        max_error = float(np.max(np.abs(expected_confidence - actual_confidence)))
        print(
            f"sample={sample + 1} mask_errors={mask_errors}/{expected_mask.size} "
            f"confidence_errors={confidence_errors}/{expected_confidence.size} "
            f"max_confidence_error={max_error:.9f}"
        )
        if mask_errors or confidence_errors:
            raise SystemExit(1)
    print(f"original_average_seconds={original_total / args.samples:.3f}")
    print(f"optimized_average_seconds={optimized_total / args.samples:.3f}")


if __name__ == "__main__":
    main()
