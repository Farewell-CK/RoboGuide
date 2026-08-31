"""Offline audit for the Android Mask2Former model and preprocessing contract."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
from PIL import Image


SIZE = 320


def tensor_signature(tensor: onnx.TensorProto) -> str:
    digest = hashlib.sha256()
    digest.update(tensor.name.encode("utf-8"))
    digest.update(np.asarray(tensor.dims, dtype=np.int64).tobytes())
    digest.update(tensor.raw_data)
    return digest.hexdigest()


def model_summary(path: Path) -> tuple[onnx.ModelProto, dict[str, str]]:
    model = onnx.load(path, load_external_data=False)
    initializers = {item.name: tensor_signature(item) for item in model.graph.initializer}
    print(f"MODEL {path}")
    print(f"  producer={model.producer_name!r} version={model.producer_version!r}")
    print(f"  nodes={len(model.graph.node)} initializers={len(initializers)}")
    print(f"  inputs={[item.name for item in model.graph.input]}")
    print(f"  outputs={[item.name for item in model.graph.output]}")
    constants = {}
    for node in model.graph.node:
        if node.op_type != "Constant" or not node.output:
            continue
        for attribute in node.attribute:
            if attribute.name == "value":
                constants[node.output[0]] = onnx.numpy_helper.to_array(attribute.t)
    input_node_names = {model.graph.input[0].name}
    for node in model.graph.node[:80]:
        if any(name in input_node_names for name in node.input):
            print(f"  input_path_node={node.op_type} inputs={list(node.input)} outputs={list(node.output)}")
            for name in node.input:
                if name in constants:
                    print(f"    constant {name}={constants[name].ravel().tolist()}")
            input_node_names.update(node.output)
    return model, initializers


def compare_models(first: Path, second: Path) -> None:
    first_model, first_initializers = model_summary(first)
    second_model, second_initializers = model_summary(second)
    names = set(first_initializers) | set(second_initializers)
    changed = [name for name in names if first_initializers.get(name) != second_initializers.get(name)]
    first_ops = collections.Counter(node.op_type for node in first_model.graph.node)
    second_ops = collections.Counter(node.op_type for node in second_model.graph.node)
    print("MODEL_COMPARISON")
    print(f"  initializer_names={len(names)} changed_or_missing={len(changed)}")
    print(f"  graph_ops_equal={first_ops == second_ops}")
    if changed:
        print(f"  first_changed={changed[:10]}")


def compare_outputs(original: Path, optimized: Path, image: Path) -> None:
    original_session = ort.InferenceSession(
        str(original), providers=["CPUExecutionProvider"]
    )
    optimized_session = ort.InferenceSession(
        str(optimized), providers=["CPUExecutionProvider"]
    )
    input_tensor = android_crop(Image.open(image), "rgb")
    (logits,) = original_session.run(
        None, {original_session.get_inputs()[0].name: input_tensor}
    )
    actual_mask, actual_confidence = optimized_session.run(
        None, {optimized_session.get_inputs()[0].name: input_tensor}
    )
    expected_mask = np.argmax(logits, axis=0).astype(np.int32)
    shifted = logits.astype(np.float64) - np.max(logits, axis=0, keepdims=True)
    expected_confidence = (
        np.floor((1.0 / np.exp(shifted).sum(axis=0)) * 255.0 + 0.5) / 255.0
    ).astype(np.float32)
    mask_errors = int(np.count_nonzero(expected_mask != actual_mask))
    confidence_errors = int(np.count_nonzero(expected_confidence != actual_confidence))
    confidence_max_error = float(
        np.max(np.abs(expected_confidence - actual_confidence))
    )
    print("OUTPUT_COMPARISON")
    print(f"  mask_errors={mask_errors}/{expected_mask.size}")
    print(f"  confidence_errors={confidence_errors}/{expected_confidence.size}")
    print(f"  confidence_max_error={confidence_max_error:.9f}")


def android_crop(
    image: Image.Image,
    channel_order: str,
    size: int = SIZE,
    resampling: Image.Resampling = Image.Resampling.NEAREST,
    resize_mode: str = "crop",
) -> np.ndarray:
    rgb_image = image.convert("RGB")
    width, height = rgb_image.size
    if resize_mode == "crop":
        square = min(width, height)
        x = (width - square) // 2
        y = (height - square) // 2
        prepared = rgb_image.crop((x, y, x + square, y + square)).resize(
            (size, size), resampling
        )
    elif resize_mode == "stretch":
        prepared = rgb_image.resize((size, size), resampling)
    elif resize_mode == "letterbox":
        scale = min(size / width, size / height)
        resized_width = max(1, round(width * scale))
        resized_height = max(1, round(height * scale))
        content = rgb_image.resize((resized_width, resized_height), resampling)
        prepared = Image.new("RGB", (size, size), (0, 0, 0))
        prepared.paste(content, ((size - resized_width) // 2, (size - resized_height) // 2))
    else:
        raise ValueError(f"Unsupported resize mode: {resize_mode}")
    resized = np.asarray(prepared, dtype=np.float32)
    if channel_order == "bgr":
        resized = resized[..., ::-1]
    return np.transpose(resized, (2, 0, 1))[None, ...].copy()


def load_labels(path: Path) -> tuple[list[str], list[list[int]], list[float]]:
    labels = json.loads(path.read_text(encoding="utf-8"))["labels"][:65]
    return (
        [item["readable"] for item in labels],
        [item["color"] for item in labels],
        [float(item["degree"]) for item in labels],
    )


def output_mask(outputs: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray | None]:
    first = np.asarray(outputs[0])
    if np.issubdtype(first.dtype, np.integer):
        mask = np.squeeze(first).astype(np.int64)
        confidence = np.squeeze(np.asarray(outputs[1])) if len(outputs) > 1 else None
        return mask, confidence
    logits = np.squeeze(first)
    if logits.ndim != 3:
        raise RuntimeError(f"Unsupported output shape: {first.shape}")
    return np.argmax(logits, axis=0), np.max(logits, axis=0)


def tiled_full_frame(
    session: ort.InferenceSession,
    image: Image.Image,
    channel_order: str,
    size: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Run overlapping fixed-size windows over a native-height RGB frame."""
    rgb_image = image.convert("RGB")
    width, height = rgb_image.size
    if height != size or width < size:
        raise ValueError(
            f"tile mode requires height={size} and width>={size}, got {width}x{height}"
        )
    x_offsets = list(range(0, width - size + 1, size))
    if not x_offsets or x_offsets[-1] != width - size:
        x_offsets.append(width - size)
    best_confidence = np.full((height, width), -np.inf, dtype=np.float32)
    best_mask = np.zeros((height, width), dtype=np.int64)
    input_name = session.get_inputs()[0].name
    for x_offset in x_offsets:
        tile = rgb_image.crop((x_offset, 0, x_offset + size, size))
        tensor = np.asarray(tile, dtype=np.float32)
        if channel_order == "bgr":
            tensor = tensor[..., ::-1]
        tensor = np.transpose(tensor, (2, 0, 1))[None, ...].copy()
        mask, confidence = output_mask(session.run(None, {input_name: tensor}))
        if confidence is None:
            confidence = np.ones(mask.shape, dtype=np.float32)
        destination = slice(x_offset, x_offset + size)
        replace = confidence > best_confidence[:, destination]
        best_mask[:, destination][replace] = mask[replace]
        best_confidence[:, destination][replace] = confidence[replace]
    return best_mask, best_confidence


def render_mask(mask: np.ndarray, colors: list[list[int]], output: Path) -> None:
    palette = np.asarray(colors, dtype=np.uint8)
    clipped = np.clip(mask, 0, len(palette) - 1)
    Image.fromarray(palette[clipped], mode="RGB").save(output)


def run_images(
    model: Path,
    images: list[Path],
    metadata: Path,
    output_dir: Path,
    size: int,
    resampling: Image.Resampling,
    resize_mode: str,
    channel_orders: tuple[str, ...],
) -> None:
    names, colors, degrees = load_labels(metadata)
    session = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
    input_name = session.get_inputs()[0].name
    output_dir.mkdir(parents=True, exist_ok=True)
    for image_path in images:
        image = Image.open(image_path)
        for order in channel_orders:
            if resize_mode == "tile":
                mask, confidence = tiled_full_frame(session, image, order, size)
            else:
                outputs = session.run(
                    None,
                    {input_name: android_crop(
                        image, order, size, resampling, resize_mode
                    )},
                )
                mask, confidence = output_mask(outputs)
            counts = np.bincount(mask.ravel(), minlength=len(names))
            top = np.argsort(counts)[::-1][:8]
            weighted_cost = sum(counts[i] * degrees[i] for i in range(len(names))) / mask.size
            hard_ratio = sum(counts[i] for i in range(len(names)) if degrees[i] > 0.9) / mask.size
            print(f"IMAGE {image_path.name} order={order}")
            print(f"  mean_source_cost={weighted_cost * 100:.2f} hard_class_ratio={hard_ratio * 100:.2f}%")
            if confidence is not None:
                print(f"  mean_confidence={float(np.mean(confidence)):.4f}")
            print("  top=" + ", ".join(
                f"{names[i]}:{counts[i] * 100 / mask.size:.2f}%" for i in top if counts[i]
            ))
            render_mask(mask, colors, output_dir / f"{image_path.stem}_{order}.png")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--compare-model", type=Path)
    parser.add_argument("--compare-outputs", action="store_true")
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--size", type=int, default=SIZE)
    parser.add_argument(
        "--resampling",
        choices=("nearest", "bilinear"),
        default="nearest",
    )
    parser.add_argument(
        "--resize-mode",
        choices=("crop", "stretch", "letterbox", "tile"),
        default="crop",
    )
    parser.add_argument(
        "--channel-order",
        choices=("rgb", "bgr", "both"),
        default="both",
    )
    parser.add_argument("images", type=Path, nargs="*")
    args = parser.parse_args()
    if args.compare_model:
        compare_models(args.model, args.compare_model)
    else:
        model_summary(args.model)
    if args.compare_outputs:
        if not args.compare_model or not args.images:
            parser.error("--compare-outputs requires --compare-model and one image")
        compare_outputs(args.model, args.compare_model, args.images[0])
    if args.images and not args.compare_outputs:
        if args.metadata is None or args.output_dir is None:
            parser.error("image inference requires --metadata and --output-dir")
        resampling = (
            Image.Resampling.BILINEAR
            if args.resampling == "bilinear"
            else Image.Resampling.NEAREST
        )
        run_images(
            args.model,
            args.images,
            args.metadata,
            args.output_dir,
            args.size,
            resampling,
            args.resize_mode,
            ("rgb", "bgr") if args.channel_order == "both" else (args.channel_order,),
        )


if __name__ == "__main__":
    main()
