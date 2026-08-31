"""Export the source Mapillary Mask2Former model at a fixed full-frame shape."""

from __future__ import annotations

import argparse
from pathlib import Path

import torch
import torch.nn.functional as F
from detectron2.config import get_cfg
from detectron2.engine import DefaultPredictor
from detectron2.projects.deeplab import add_deeplab_config
from mask2former import add_maskformer2_config
from mask2former.modeling.pixel_decoder.ops.modules.ms_deform_attn import (
    MSDeformAttn,
)
from mask2former.modeling.pixel_decoder.ops.functions.ms_deform_attn_func import (
    ms_deform_attn_core_pytorch,
)
from torch import nn


class SemanticWrapper(nn.Module):
    def __init__(self, predictor: DefaultPredictor) -> None:
        super().__init__()
        self.model = predictor.model
        self._replace_cuda_deformable_attention()

    def _replace_cuda_deformable_attention(self) -> None:
        # MSDeformAttnFunction is a CUDA extension without an ONNX symbolic.
        # Tracing that path can silently omit the sampling operation and produce
        # a valid-looking model with incorrect predictions. The project already
        # ships this mathematically equivalent PyTorch implementation for CPU;
        # it exports to standard ONNX GridSample nodes supported by ORT.
        for module in self.model.modules():
            if isinstance(module, MSDeformAttn):
                module.forward = _onnx_ms_deform_attn_forward.__get__(
                    module, MSDeformAttn
                )

    def forward(self, image: torch.Tensor) -> torch.Tensor:
        height = image.shape[-2]
        width = image.shape[-1]
        output = self.model(
            [{"image": image[0], "height": height, "width": width}]
        )[0]
        return output["sem_seg"]


def _onnx_ms_deform_attn_forward(
    self: MSDeformAttn,
    query: torch.Tensor,
    reference_points: torch.Tensor,
    input_flatten: torch.Tensor,
    input_spatial_shapes: torch.Tensor,
    input_level_start_index: torch.Tensor,
    input_padding_mask: torch.Tensor | None = None,
) -> torch.Tensor:
    batch_size, query_length, _ = query.shape
    _, input_length, _ = input_flatten.shape
    value = self.value_proj(input_flatten)
    if input_padding_mask is not None:
        value = value.masked_fill(input_padding_mask[..., None], 0.0)
    value = value.view(
        batch_size,
        input_length,
        self.n_heads,
        self.d_model // self.n_heads,
    )
    sampling_offsets = self.sampling_offsets(query).view(
        batch_size,
        query_length,
        self.n_heads,
        self.n_levels,
        self.n_points,
        2,
    )
    attention_weights = F.softmax(
        self.attention_weights(query).view(
            batch_size,
            query_length,
            self.n_heads,
            self.n_levels * self.n_points,
        ),
        dim=-1,
    ).view(
        batch_size,
        query_length,
        self.n_heads,
        self.n_levels,
        self.n_points,
    )
    if reference_points.shape[-1] == 2:
        offset_normalizer = torch.stack(
            [input_spatial_shapes[..., 1], input_spatial_shapes[..., 0]], dim=-1
        )
        sampling_locations = (
            reference_points[:, :, None, :, None, :]
            + sampling_offsets
            / offset_normalizer[None, None, None, :, None, :]
        )
    elif reference_points.shape[-1] == 4:
        sampling_locations = (
            reference_points[:, :, None, :, None, :2]
            + sampling_offsets
            / self.n_points
            * reference_points[:, :, None, :, None, 2:]
            * 0.5
        )
    else:
        raise ValueError("reference_points must end in 2 or 4 coordinates")
    output = ms_deform_attn_core_pytorch(
        value,
        input_spatial_shapes,
        sampling_locations,
        attention_weights,
    )
    return self.output_proj(output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--width", type=int, default=640)
    args = parser.parse_args()
    if args.height <= 0 or args.width <= 0:
        parser.error("--height and --width must be positive")
    if args.height % 32 or args.width % 32:
        parser.error("Swin-L export dimensions must be divisible by 32")

    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    cfg = get_cfg()
    add_deeplab_config(cfg)
    add_maskformer2_config(cfg)
    cfg.merge_from_file(str(args.config))
    cfg.MODEL.WEIGHTS = str(args.weights)
    cfg.MODEL.DEVICE = device
    cfg.TEST.AUG.ENABLED = False

    print(f"loading source Mapillary Mask2Former on {device}")
    predictor = DefaultPredictor(cfg)
    wrapper = SemanticWrapper(predictor).eval().to(device)
    dummy = torch.zeros(1, 3, args.height, args.width, device=device)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    print(f"exporting fixed full-frame {args.width}x{args.height} model")
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
