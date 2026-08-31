"""Compare the eLabrador source TTA output with a candidate ONNX model."""

from __future__ import annotations

import argparse
import io
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
import torch
import detectron2.data.transforms as transforms
from detectron2.config import get_cfg
from detectron2.engine import DefaultPredictor
from detectron2.projects.deeplab import add_deeplab_config
from mask2former import add_maskformer2_config
from PIL import Image

from export_mask2former_source_tta_onnx import SOURCE_SHORT_EDGES


def load_rgb(path: Path, width: int, height: int) -> np.ndarray:
    image = Image.open(path).convert("RGB")
    if image.size != (width, height):
        raise RuntimeError(
            f"expected a {width}x{height} D455 frame, got {image.size}: {path}"
        )
    return np.asarray(image, dtype=np.uint8)


@torch.no_grad()
def source_tta_scores(
    predictor: DefaultPredictor,
    rgb: np.ndarray,
) -> np.ndarray:
    """Run the unmodified eLabrador MultiScalePredictor algorithm."""
    height, width = rgb.shape[:2]
    predictions = []
    for short_edge in SOURCE_SHORT_EDGES:
        augmentation = transforms.ResizeShortestEdge(
            [short_edge, short_edge], 4096
        )
        resized = (
            augmentation.get_transform(rgb)
            .apply_image(rgb)
            .astype("float32")
            .transpose(2, 0, 1)
        )
        image = torch.as_tensor(resized, device=predictor.cfg.MODEL.DEVICE)
        flipped = torch.flip(image, dims=[2])
        results = predictor.model(
            [
                {"image": image, "height": height, "width": width},
                {"image": flipped, "height": height, "width": width},
            ]
        )
        predictions.append(results[0]["sem_seg"])
        predictions.append(torch.flip(results[1]["sem_seg"], dims=[2]))
    return torch.stack(predictions, dim=0).mean(dim=0).cpu().numpy()


def top_classes(mask: np.ndarray, limit: int = 12) -> str:
    labels, counts = np.unique(mask, return_counts=True)
    order = np.argsort(counts)[::-1][:limit]
    total = mask.size
    return ", ".join(
        f"{int(labels[index])}:{100.0 * int(counts[index]) / total:.2f}%"
        for index in order
    )


def jpeg_round_trip(rgb: np.ndarray, quality: int) -> np.ndarray:
    buffer = io.BytesIO()
    Image.fromarray(rgb, mode="RGB").save(buffer, "JPEG", quality=quality)
    buffer.seek(0)
    return np.asarray(Image.open(buffer).convert("RGB"), dtype=np.uint8)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--onnx", type=Path, required=True)
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument(
        "--jpeg-quality",
        type=int,
        default=50,
        help="also compare with the source edge JPEG transport (default: 50)",
    )
    args = parser.parse_args()

    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    cfg = get_cfg()
    add_deeplab_config(cfg)
    add_maskformer2_config(cfg)
    cfg.merge_from_file(str(args.config))
    cfg.MODEL.WEIGHTS = str(args.weights)
    cfg.MODEL.DEVICE = device
    source = DefaultPredictor(cfg)

    providers = (
        ["CUDAExecutionProvider", "CPUExecutionProvider"]
        if "CUDAExecutionProvider" in ort.get_available_providers()
        else ["CPUExecutionProvider"]
    )
    session = ort.InferenceSession(args.onnx, providers=providers)
    input_name = session.get_inputs()[0].name

    raw_agreements = []
    transport_agreements = []
    for path in args.images:
        rgb = load_rgb(path, args.width, args.height)
        nchw = np.transpose(rgb.astype(np.float32), (2, 0, 1))[None, ...]

        start = time.perf_counter()
        source_logits = source_tta_scores(source, rgb)
        source_seconds = time.perf_counter() - start

        jpeg_rgb = jpeg_round_trip(rgb, args.jpeg_quality)
        start = time.perf_counter()
        transported_source_logits = source_tta_scores(source, jpeg_rgb)
        transported_source_seconds = time.perf_counter() - start

        start = time.perf_counter()
        candidate_outputs = session.run(None, {input_name: nchw})
        candidate_seconds = time.perf_counter() - start
        candidate_logits = candidate_outputs[0]
        if candidate_logits.ndim == 4 and candidate_logits.shape[0] == 1:
            candidate_logits = candidate_logits[0]
        if candidate_logits.ndim != 3:
            raise RuntimeError(
                "candidate must output [classes,height,width] semantic scores; "
                f"got {candidate_logits.shape}"
            )

        source_mask = np.argmax(source_logits, axis=0)
        transported_source_mask = np.argmax(transported_source_logits, axis=0)
        candidate_mask = np.argmax(candidate_logits, axis=0)
        raw_agreement = float(np.mean(source_mask == candidate_mask))
        transport_agreement = float(
            np.mean(transported_source_mask == candidate_mask)
        )
        raw_agreements.append(raw_agreement)
        transport_agreements.append(transport_agreement)
        print(path)
        print(
            f"  source_seconds={source_seconds:.3f} "
            f"source_jpeg_seconds={transported_source_seconds:.3f} "
            f"candidate_seconds={candidate_seconds:.3f} "
            f"raw_pixel_agreement={raw_agreement:.4%} "
            f"source_transport_agreement={transport_agreement:.4%}"
        )
        print(f"  source_top_classes={top_classes(source_mask)}")
        print(
            "  source_jpeg_top_classes="
            f"{top_classes(transported_source_mask)}"
        )
        print(f"  candidate_top_classes={top_classes(candidate_mask)}")

    print(f"mean_raw_pixel_agreement={np.mean(raw_agreements):.4%}")
    print(
        "mean_source_transport_agreement="
        f"{np.mean(transport_agreements):.4%}"
    )


if __name__ == "__main__":
    main()
