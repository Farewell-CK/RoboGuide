"""Probe whether a fixed-shape Mask2Former ONNX graph accepts another input size."""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("model", type=Path)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--width", type=int, required=True)
    args = parser.parse_args()

    graph = onnx.load(args.model, load_external_data=True)
    input_shape = graph.graph.input[0].type.tensor_type.shape.dim
    original = [dim.dim_value for dim in input_shape]
    input_shape[2].dim_value = args.height
    input_shape[3].dim_value = args.width

    with tempfile.TemporaryDirectory(prefix="mask2former-shape-") as directory:
        candidate = Path(directory) / "candidate.onnx"
        onnx.save(graph, candidate)
        session = ort.InferenceSession(candidate, providers=["CPUExecutionProvider"])
        image = np.zeros((1, 3, args.height, args.width), dtype=np.float32)
        outputs = session.run(None, {session.get_inputs()[0].name: image})

    print(f"original_input={original}")
    print(f"candidate_input={[1, 3, args.height, args.width]}")
    print(f"output_shapes={[list(value.shape) for value in outputs]}")


if __name__ == "__main__":
    main()
