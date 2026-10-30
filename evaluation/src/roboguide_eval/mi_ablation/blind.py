"""Export stage-blinded candidate packets; keep private branch mappings separate."""

from __future__ import annotations

import random
import re
from pathlib import Path
from typing import cast

from mission.models import JSONObject, JSONValue

from roboguide_eval.mi_ablation.evidence import digest, read_object, write_new
from roboguide_eval.mi_ablation.scoring import object_value, objects


def export_blind(directory: Path, output: Path, seed: int = 20261030) -> JSONObject:
    """Export every distinct retained candidate without revealing its A/B/C origin or review."""
    manifest = read_object(directory / "manifest.json")
    candidates: dict[str, JSONObject] = {}
    mappings: dict[str, list[JSONObject]] = {}
    incomplete: list[str] = []
    for slot in objects(manifest["slots"]):
        trial = directory / "trials" / str(slot["id"])
        if not (trial / "result.json").exists():
            incomplete.append(str(slot["id"]))
            continue
        result, public_input = read_object(trial / "result.json"), read_object(trial / "input.json")
        if result["input_digest"] != digest(public_input):
            raise ValueError("trial public input digest mismatch")
        for branch, raw in object_value(result["branches"]).items():
            record = object_value(raw)
            plan = record["plan"]
            if plan is None:
                continue
            if digest(plan) != record["candidate_digest"]:
                raise ValueError("candidate digest mismatch")
            key = digest({"input": public_input, "plan": plan})
            candidates[key] = {"input": public_input, "candidate": plan}
            mappings.setdefault(key, []).append({"slot": slot["id"], "branch": branch})
    keys = sorted(candidates)
    random.Random(seed).shuffle(keys)
    output.mkdir(parents=True, exist_ok=False)
    private: list[JSONValue] = []
    labels: list[JSONValue] = []
    for index, key in enumerate(keys, start=1):
        item_id = f"candidate-{index:04d}"
        packet = {"id": item_id, **candidates[key]}
        write_new(output / "packets" / f"{item_id}.json", packet)
        private.append({"id": item_id, "packet_digest": digest(packet), "origins": mappings[key]})
        labels.append(
            {
                "id": item_id,
                "packet_digest": digest(packet),
                "label": "unreviewed",
                "issues": [],
                "rationale": None,
                "reviewer_identity": None,
            }
        )
    write_new(output / "PRIVATE-branch-map.json", private)
    write_new(output / "labels-template.json", labels)
    summary: JSONObject = {
        "distinct_candidates": len(candidates),
        "unclosed_or_unstarted_slots": incomplete,
        "scope": "candidate_semantics_only_not_review_issue_correctness_or_physical_execution",
        "instructions": [
            "Give reviewers packets and a copy of labels-template only; keep PRIVATE map separate.",
            "Label correct, incorrect or uncertain against the task and frozen input.",
            "Accept semantically equivalent plans; do not require one task/actor count.",
            "Cite actual parameter/constraint/relation paths for every issue.",
            "Resolve disagreements before unblinding; preserve original label sheets.",
        ],
    }
    write_new(output / "README.json", summary)
    return summary


def label_counts(labels_path: Path) -> JSONObject:
    """Reject unreviewed/anonymous labels rather than publishing an invented success score."""
    import json

    labels = objects(json.loads(labels_path.read_text(encoding="utf-8")))
    counts: JSONObject = {"correct": 0, "incorrect": 0, "uncertain": 0}
    seen: set[str] = set()
    for row in labels:
        name = str(row["id"])
        if (
            re.fullmatch(r"candidate-[0-9]{4}", name) is None
            or name in seen
            or row.get("label") not in counts
        ):
            raise ValueError("duplicate, unreviewed or unknown blind label")
        packet = read_object(labels_path.parent / "packets" / f"{name}.json")
        if row.get("packet_digest") != digest(packet):
            raise ValueError("blind label/candidate digest mismatch")
        if not row.get("reviewer_identity") or not row.get("rationale"):
            raise ValueError("blind label lacks reviewer identity or rationale")
        seen.add(name)
        label = str(row["label"])
        counts[label] = cast(int, counts[label]) + 1
    expected = {p.stem for p in (labels_path.parent / "packets").glob("candidate-*.json")}
    if not expected or seen != expected:
        raise ValueError("blind labels omit or add candidates")
    return counts
