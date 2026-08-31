"""Export Mapillary Mask2Former with the eLabrador source TTA pipeline.

The source server does not call the model once on a square crop. It preserves
the full image aspect ratio, evaluates six short-edge sizes, evaluates a
horizontal flip at every size, restores every result to the source resolution,
and averages all twelve semantic score maps. This exporter keeps that behavior
inside one fixed-shape ONNX graph so all branches share one set of weights.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable, Sequence

import torch
import torch.nn.functional as functional
from detectron2.config import get_cfg
from detectron2.engine import DefaultPredictor
from detectron2.projects.deeplab import add_deeplab_config
from mask2former import add_maskformer2_config
from torch import nn


SOURCE_SHORT_EDGES = (192, 256, 320, 384, 448, 480)


def resized_shape(
    source_height: int,
    source_width: int,
    short_edge: int,
    max_edge: int,
) -> tuple[int, int]:
    """Match detectron2 ResizeShortestEdge.get_output_shape rounding."""
    scale = short_edge / min(source_height, source_width)
    resized_height = source_height * scale
    resized_width = source_width * scale
    if max(resized_height, resized_width) > max_edge:
        limit_scale = max_edge / max(resized_height, resized_width)
        resized_height *= limit_scale
        resized_width *= limit_scale
    return int(resized_height + 0.5), int(resized_width + 0.5)


class SourceTtaSemanticWrapper(nn.Module):
    """Fixed-camera-shape equivalent of the source MultiScalePredictor."""

    def __init__(
        self,
        predictor: DefaultPredictor,
        source_height: int,
        source_width: int,
        short_edges: Sequence[int],
        flip: bool,
        max_edge: int,
    ) -> None:
        super().__init__()
        self.model = predictor.model
        self.source_height = source_height
        self.source_width = source_width
        self.short_edges = tuple(short_edges)
        self.flip = flip
        self.max_edge = max_edge

    def forward(self, image: torch.Tensor) -> torch.Tensor:
        score_sum = None
        prediction_count = 0
        for short_edge in self.short_edges:
            target_shape = resized_shape(
                self.source_height,
                self.source_width,
                short_edge,
                self.max_edge,
            )
            resized = functional.interpolate(
                image,
                size=target_shape,
                mode="bilinear",
                align_corners=False,
            )
            inputs = [
                {
                    "image": resized[0],
                    "height": self.source_height,
                    "width": self.source_width,
                }
            ]
            if self.flip:
                inputs.append(
                    {
                        "image": torch.flip(resized[0], dims=[2]),
                        "height": self.source_height,
                        "width": self.source_width,
                    }
                )

            predictions = self.model(inputs)
            score = predictions[0]["sem_seg"]
            score_sum = score if score_sum is None else score_sum + score
            prediction_count += 1
            if self.flip:
                score_sum = score_sum + torch.flip(
                    predictions[1]["sem_seg"], dims=[2]
                )
                prediction_count += 1

        if score_sum is None:
            raise RuntimeError("at least one short-edge size is required")
        return score_sum / prediction_count


def parse_short_edges(value: str) -> tuple[int, ...]:
    try:
        result = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as error:
        raise argparse.ArgumentTypeError("short edges must be comma-separated integers") from error
    if not result or any(item <= 0 for item in result):
        raise argparse.ArgumentTypeError("short edges must contain positive integers")
    return result


def describe_branches(
    short_edges: Iterable[int],
    source_height: int,
    source_width: int,
    max_edge: int,
    flip: bool,
) -> None:
    for short_edge in short_edges:
        height, width = resized_shape(
            source_height, source_width, short_edge, max_edge
        )
        print(
            f"short_edge={short_edge}: {width}x{height} "
            f"passes={2 if flip else 1}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--max-edge", type=int, default=4096)
    parser.add_argument(
        "--short-edges",
        type=parse_short_edges,
        default=SOURCE_SHORT_EDGES,
        help="source default: 192,256,320,384,448,480",
    )
    parser.add_argument(
        "--no-flip",
        action="store_true",
        help="export an accuracy ablation without horizontal-flip TTA",
    )
    args = parser.parse_args()
    if args.height <= 0 or args.width <= 0 or args.max_edge <= 0:
        parser.error("height, width, and max-edge must be positive")

    flip = not args.no_flip
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    cfg = get_cfg()
    add_deeplab_config(cfg)
    add_maskformer2_config(cfg)
    cfg.merge_from_file(str(args.config))
    cfg.MODEL.WEIGHTS = str(args.weights)
    cfg.MODEL.DEVICE = device

    print(f"loading source Mapillary Mask2Former on {device}")
    predictor = DefaultPredictor(cfg)
    wrapper = SourceTtaSemanticWrapper(
        predictor,
        source_height=args.height,
        source_width=args.width,
        short_edges=args.short_edges,
        flip=flip,
        max_edge=args.max_edge,
    ).eval().to(device)
    dummy = torch.zeros(1, 3, args.height, args.width, device=device)

    print("export branches:")
    describe_branches(
        args.short_edges,
        args.height,
        args.width,
        args.max_edge,
        flip,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with torch.no_grad():
        torch.onnx.export(
            wrapper,
            dummy,
            args.output,
            opset_version=17,
            input_names=["image"],
            output_names=["semantic_logits"],
            dynamic_axes=None,
            do_constant_folding=True,
        )
    print(f"exported {args.output}")


if __name__ == "__main__":
    main()
