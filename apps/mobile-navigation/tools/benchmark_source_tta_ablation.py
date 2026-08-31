"""Measure smaller inference schedules against eLabrador's exact source TTA."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
import detectron2.data.transforms as transforms
import numpy as np
import torch
from detectron2.config import get_cfg
from detectron2.engine import DefaultPredictor
from detectron2.projects.deeplab import add_deeplab_config
from mask2former import add_maskformer2_config
from PIL import Image


SOURCE_SHORT_EDGES = (192, 256, 320, 384, 448, 480)
SCHEDULES = {
    "full-480": ((480, False),),
    "full-480-flip": ((480, False), (480, True)),
    "full-320-480-flip": (
        (320, False),
        (320, True),
        (480, False),
        (480, True),
    ),
    "source-exact-tta": tuple(
        (short_edge, flipped)
        for short_edge in SOURCE_SHORT_EDGES
        for flipped in (False, True)
    ),
}


@torch.no_grad()
def run_branches(
    predictor: DefaultPredictor,
    rgb: np.ndarray,
) -> tuple[dict[tuple[int, bool], np.ndarray], dict[tuple[int, bool], float]]:
    height, width = rgb.shape[:2]
    scores = {}
    seconds = {}
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
        start = time.perf_counter()
        results = predictor.model(
            [
                {"image": image, "height": height, "width": width},
                {
                    "image": torch.flip(image, dims=[2]),
                    "height": height,
                    "width": width,
                },
            ]
        )
        torch.cuda.synchronize() if image.is_cuda else None
        elapsed = time.perf_counter() - start
        scores[(short_edge, False)] = results[0]["sem_seg"].cpu().numpy()
        scores[(short_edge, True)] = (
            torch.flip(results[1]["sem_seg"], dims=[2]).cpu().numpy()
        )
        seconds[(short_edge, False)] = elapsed / 2.0
        seconds[(short_edge, True)] = elapsed / 2.0
    return scores, seconds


def average_schedule(
    branches: dict[tuple[int, bool], np.ndarray],
    schedule: tuple[tuple[int, bool], ...],
) -> np.ndarray:
    return np.stack([branches[key] for key in schedule], axis=0).mean(axis=0)


def class_histogram(mask: np.ndarray) -> dict[str, float]:
    classes, counts = np.unique(mask, return_counts=True)
    order = np.argsort(counts)[::-1]
    return {
        str(int(classes[index])): round(float(counts[index] / mask.size), 6)
        for index in order
    }


def colorize(mask: np.ndarray) -> np.ndarray:
    # Deterministic diagnostic palette; class IDs are also preserved in NPZ.
    palette = np.asarray(
        [
            ((index * 67 + 29) % 256, (index * 131 + 71) % 256, (index * 197 + 13) % 256)
            for index in range(65)
        ],
        dtype=np.uint8,
    )
    return palette[mask]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("images", nargs="+", type=Path)
    args = parser.parse_args()

    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    cfg = get_cfg()
    add_deeplab_config(cfg)
    add_maskformer2_config(cfg)
    cfg.merge_from_file(str(args.config))
    cfg.MODEL.WEIGHTS = str(args.weights)
    cfg.MODEL.DEVICE = device
    predictor = DefaultPredictor(cfg)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    report = {}
    for image_path in args.images:
        rgb = np.asarray(Image.open(image_path).convert("RGB"), dtype=np.uint8)
        branches, branch_seconds = run_branches(predictor, rgb)
        reference_scores = average_schedule(
            branches, SCHEDULES["source-exact-tta"]
        )
        reference_mask = np.argmax(reference_scores, axis=0).astype(np.uint8)
        image_report = {
            "shape": list(rgb.shape),
            "reference_histogram": class_histogram(reference_mask),
            "schedules": {},
        }
        stem = image_path.stem
        np.savez_compressed(
            args.output_dir / f"{stem}-source-reference.npz",
            mask=reference_mask,
            confidence=np.max(reference_scores, axis=0).astype(np.float32),
        )
        cv2.imwrite(
            str(args.output_dir / f"{stem}-source-reference.png"),
            cv2.cvtColor(colorize(reference_mask), cv2.COLOR_RGB2BGR),
        )

        for name, schedule in SCHEDULES.items():
            scores = average_schedule(branches, schedule)
            mask = np.argmax(scores, axis=0).astype(np.uint8)
            agreement = float(np.mean(mask == reference_mask))
            estimated_seconds = sum(branch_seconds[key] for key in schedule)
            image_report["schedules"][name] = {
                "passes": len(schedule),
                "estimated_seconds": round(estimated_seconds, 4),
                "pixel_agreement": round(agreement, 6),
                "changed_pixels": int(np.count_nonzero(mask != reference_mask)),
                "histogram": class_histogram(mask),
            }
            cv2.imwrite(
                str(args.output_dir / f"{stem}-{name}.png"),
                cv2.cvtColor(colorize(mask), cv2.COLOR_RGB2BGR),
            )
            print(
                f"{image_path.name} {name}: passes={len(schedule)} "
                f"agreement={agreement:.4%} estimated_seconds={estimated_seconds:.3f}"
            )
        report[str(image_path)] = image_report

    report_path = args.output_dir / "source-tta-ablation.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"report={report_path}")


if __name__ == "__main__":
    main()
