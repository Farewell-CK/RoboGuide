#!/usr/bin/env bash
set -euo pipefail

# Run inside the eLabrador semantic-server environment (normally the Orin
# container at /opt/nvi_server). The environment must already contain the
# source Detectron2, Mask2Former and custom CUDA operators.

SERVER_ROOT="${SERVER_ROOT:-/opt/nvi_server}"
TOOLS_ROOT="${TOOLS_ROOT:-${SERVER_ROOT}/tools/mobile-export}"
OUTPUT_ROOT="${OUTPUT_ROOT:-${SERVER_ROOT}/models/mobile-validation}"
IMAGE_ROOT="${IMAGE_ROOT:?Set IMAGE_ROOT to a directory of 640x480 D455 RGB images}"
CONFIG="${CONFIG:-${SERVER_ROOT}/configs/mask2former_detectron2_model.yaml}"
WEIGHTS="${WEIGHTS:-${SERVER_ROOT}/models/mask2former-swinL-semantic.pkl}"

mkdir -p "${OUTPUT_ROOT}"

python "${TOOLS_ROOT}/export_mask2former_source_tta_onnx.py" \
  --config "${CONFIG}" \
  --weights "${WEIGHTS}" \
  --output "${OUTPUT_ROOT}/swinl-mapillary-full-480.onnx" \
  --short-edges 480 \
  --no-flip

python "${TOOLS_ROOT}/export_mask2former_source_tta_onnx.py" \
  --config "${CONFIG}" \
  --weights "${WEIGHTS}" \
  --output "${OUTPUT_ROOT}/swinl-mapillary-full-480-flip.onnx" \
  --short-edges 480

# Export the 12-branch source reference last because it is the largest graph.
python "${TOOLS_ROOT}/export_mask2former_source_tta_onnx.py" \
  --config "${CONFIG}" \
  --weights "${WEIGHTS}" \
  --output "${OUTPUT_ROOT}/swinl-mapillary-source-exact-tta.onnx"

mapfile -t IMAGES < <(find "${IMAGE_ROOT}" -maxdepth 1 -type f \
  \( -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' \) | sort)
if [[ "${#IMAGES[@]}" -eq 0 ]]; then
  echo "No validation images found in ${IMAGE_ROOT}" >&2
  exit 1
fi

for MODEL in \
  "${OUTPUT_ROOT}/swinl-mapillary-full-480.onnx" \
  "${OUTPUT_ROOT}/swinl-mapillary-full-480-flip.onnx" \
  "${OUTPUT_ROOT}/swinl-mapillary-source-exact-tta.onnx"; do
  python "${TOOLS_ROOT}/compare_source_tta_and_onnx.py" \
    --config "${CONFIG}" \
    --weights "${WEIGHTS}" \
    --onnx "${MODEL}" \
    "${IMAGES[@]}" | tee "${MODEL%.onnx}.comparison.txt"
done
