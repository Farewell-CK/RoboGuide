#!/usr/bin/env python3
"""Append Android-friendly semantic mask/confidence outputs to a Mask2Former model."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--confidence",
        choices=("source-u8", "raw-score", "softmax"),
        default="source-u8",
        help=(
            "source-u8 reproduces the source HTTP uint8 confidence contract; "
            "raw-score is diagnostic; softmax is legacy"
        ),
    )
    args = parser.parse_args()

    model = onnx.load(args.input)
    graph = model.graph
    if len(graph.output) != 1 or graph.output[0].name != "semantic_logits":
        raise ValueError("expected one semantic_logits output")

    output_dimensions = graph.output[0].type.tensor_type.shape.dim
    input_dimensions = graph.input[0].type.tensor_type.shape.dim
    if len(output_dimensions) != 3 or len(input_dimensions) != 4:
        raise ValueError("expected [classes, height, width] logits and NCHW input")
    height = output_dimensions[1].dim_value or input_dimensions[2].dim_value
    width = output_dimensions[2].dim_value or input_dimensions[3].dim_value
    if height <= 0 or width <= 0:
        raise ValueError("model height and width must be fixed")

    graph.node.extend([
        helper.make_node(
            "ArgMax", ["semantic_logits"], ["semantic_mask_i64"],
            axis=0, keepdims=0, select_last_index=0, name="app/ArgMax"
        ),
        helper.make_node(
            "Cast", ["semantic_mask_i64"], ["semantic_mask"],
            to=TensorProto.INT32, name="app/CastMask"
        ),
    ])
    if args.confidence in ("source-u8", "raw-score"):
        graph.node.extend([
            helper.make_node(
                "ReduceMax", ["semantic_logits"], ["semantic_confidence_raw"],
                axes=[0], keepdims=0, name="app/MaxSourceScore"
            )
        ])
        if args.confidence == "source-u8":
            # The source FastAPI service computes
            # (max_score * 255).astype(np.uint8), and the edge divides by 255.
            # Mask2Former semantic scores are non-negative. Floor + modulo
            # reproduces NumPy's uint8 conversion even if overlapping queries
            # make a score exceed one.
            graph.node.extend([
                helper.make_node(
                    "Mul", ["semantic_confidence_raw", "app_confidence_scale"],
                    ["semantic_confidence_scaled"], name="app/ScaleSourceConfidence"
                ),
                helper.make_node(
                    "Floor", ["semantic_confidence_scaled"],
                    ["semantic_confidence_floor"], name="app/FloorSourceConfidence"
                ),
                helper.make_node(
                    "Mod", ["semantic_confidence_floor", "app_uint8_modulus"],
                    ["semantic_confidence_u8"], fmod=1, name="app/Uint8SourceConfidence"
                ),
                helper.make_node(
                    "Div", ["semantic_confidence_u8", "app_confidence_scale"],
                    ["semantic_confidence"], name="app/UnscaleSourceConfidence"
                ),
            ])
            graph.initializer.append(
                numpy_helper.from_array(
                    np.asarray(255.0, dtype=np.float32), "app_confidence_scale"
                )
            )
            graph.initializer.append(
                numpy_helper.from_array(
                    np.asarray(256.0, dtype=np.float32), "app_uint8_modulus"
                )
            )
        else:
            graph.node.extend([
                helper.make_node(
                    "Identity", ["semantic_confidence_raw"], ["semantic_confidence"],
                    name="app/RawSourceConfidence"
                )
            ])
    else:
        graph.node.extend([
            helper.make_node(
                "Softmax", ["semantic_logits"], ["semantic_probabilities"],
                axis=0, name="app/Softmax"
            ),
            helper.make_node(
                "ReduceMax", ["semantic_probabilities"], ["semantic_confidence_raw"],
                axes=[0], keepdims=0, name="app/MaxConfidence"
            ),
            helper.make_node(
                "Mul", ["semantic_confidence_raw", "app_confidence_scale"],
                ["semantic_confidence_scaled"], name="app/ScaleConfidence"
            ),
            helper.make_node(
                "Add", ["semantic_confidence_scaled", "app_rounding_offset"],
                ["semantic_confidence_offset"], name="app/OffsetConfidence"
            ),
            helper.make_node(
                "Floor", ["semantic_confidence_offset"], ["semantic_confidence_rounded"],
                name="app/RoundConfidence"
            ),
            helper.make_node(
                "Div", ["semantic_confidence_rounded", "app_confidence_scale"],
                ["semantic_confidence"], name="app/UnscaleConfidence"
            ),
        ])
        graph.initializer.append(
            numpy_helper.from_array(np.asarray(255.0, dtype=np.float32), "app_confidence_scale")
        )
        graph.initializer.append(
            numpy_helper.from_array(np.asarray(0.5, dtype=np.float32), "app_rounding_offset")
        )
    del graph.output[:]
    graph.output.extend(
        [
            helper.make_tensor_value_info(
                "semantic_mask", TensorProto.INT32, [height, width]
            ),
            helper.make_tensor_value_info(
                "semantic_confidence", TensorProto.FLOAT, [height, width]
            ),
        ]
    )
    onnx.checker.check_model(model)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(model, args.output)


if __name__ == "__main__":
    main()
