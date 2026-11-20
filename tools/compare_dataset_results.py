import json
from pathlib import Path

old_path = Path(r"D:\npudaomang\cmp\results\swinl_hf.json")
new_path = Path(r"D:\npudaomang\cmp\results\final384-val2000.json")
out_dir = Path(r"D:\RoboGuide-guide-assistant\artifacts\swinl-under3s-20261104")
old = json.loads(old_path.read_text(encoding="utf-8"))
new = json.loads(new_path.read_text(encoding="utf-8"))
classes = ["car", "bicycle", "tree", "fence", "grass", "person", "other_obstacle",
           "sidewalk", "road", "crosswalk", "other_walkable", "other_unwalkable", "sky"]
shared = [name for name in classes if name not in ("crosswalk", "other_walkable")]
old11 = sum(old["iou"][name] for name in shared) / len(shared)
new11 = sum(new["iou"][name] for name in shared) / len(shared)
comparison = {
    "dataset": "val.zip / Mapillary Vistas navigation validation",
    "n_images": 2000,
    "old_source": {
        "model": old["model"], "geometry": "long edge 480, aspect preserving",
        "miou_13": old["miou_eval"], "miou_11": old11, "pixel_accuracy": old["pixel_acc"],
    },
    "final_384": {
        "model": new["model"], "geometry": "nearest RGB resize to 384x288",
        "miou_13": new["miou_eval"], "miou_11": new11, "pixel_accuracy": new["pixel_acc"],
        "plan_md5": new["source_plan_md5"], "evaluation_backend": "ONNX Runtime CPU for the final graph",
    },
    "delta_final_minus_source": {
        "miou_13": new["miou_eval"] - old["miou_eval"],
        "miou_11": new11 - old11,
        "pixel_accuracy": new["pixel_acc"] - old["pixel_acc"],
    },
    "per_class": {name: {"source": old["iou"][name], "final_384": new["iou"][name],
                          "delta": new["iou"][name] - old["iou"][name]} for name in classes},
    "caveat": "The source uses bilinear aspect-preserving long-edge-480 preprocessing; the phone final uses native nearest 384x288 preprocessing. The delta includes this geometry change."
}
out_dir.mkdir(parents=True, exist_ok=True)
(out_dir / "dataset-final384.json").write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
(out_dir / "dataset-comparison-source-vs-final384.json").write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(comparison, ensure_ascii=False, indent=2))
