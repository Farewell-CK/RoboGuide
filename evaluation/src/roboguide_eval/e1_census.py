"""Locate historical four-family archives without treating access gaps as non-execution."""

from __future__ import annotations

import csv
import io
import json
from collections import Counter
from pathlib import Path
from typing import Any

from roboguide_eval.e1_publication import verify_files
from roboguide_eval.publication_io import (
    ArchiveReader,
    boolean,
    code_identity,
    encode,
    public_safe,
    publication_metadata,
    sha256,
    write_new,
    write_rows,
)


def census_summary(rows: list[dict[str, Any]], plans: dict[str, list[str]]) -> dict[str, Any]:
    """Recompute per-source-family coverage, results, code/model identities and pairing limits."""
    groups: dict[str, Any] = {}
    for cohort, family in sorted({(r["cohort"], r["family"]) for r in rows}):
        selected = [r for r in rows if r["cohort"] == cohort and r["family"] == family]
        groups[cohort + "/" + family] = {
            "planned_workloads": len(plans[family]),
            "located_workloads": len({r["episode_id"] for r in selected}),
            "access_gap_episode_ids": sorted(
                set(plans[family]) - {r["episode_id"] for r in selected}
            ),
            "gap_meaning": "archive access gap, never proof that no historical run occurred",
            "arms": {},
        }
        for arm in ("emos", "roboguide"):
            observed = [r for r in selected if r["arm"] == arm]
            counts = Counter(
                "unavailable" if r["raw_official"] is None else str(r["raw_official"]).lower()
                for r in observed
            )
            groups[cohort + "/" + family]["arms"][arm] = {
                "located_original_records": len(observed),
                "source_accessible": sum(r["source_accessible"] for r in observed),
                "physical_execution_observed": sum(
                    r["physical_episode_executed"] is True for r in observed
                ),
                "physical_execution_unknown": sum(
                    r["physical_episode_executed"] is None for r in observed
                ),
                "raw_official": {k: counts[k] for k in ("true", "false", "unavailable")},
                "original_code_identities": dict(
                    sorted(Counter(r["code_sha"] or "unavailable" for r in observed).items())
                ),
                "model_declarations": sorted(
                    {r["requested_model"] for r in observed if r["requested_model"]}
                ),
                "model_declaration_unknown": sum(r["requested_model"] is None for r in observed),
                "pair_eligibility_unknown": sum(r["pair_eligible"] is None for r in observed),
                "pair_eligible_as_recorded": sum(r["pair_eligible"] is True for r in observed),
            }
    return {
        "schema_version": "roboguide.e1.archive-census/v0.1",
        "plans": plans,
        "four_family_planned_workloads": sum(map(len, plans.values())),
        "groups": groups,
        "combined_success_rate": None,
        "raw_evidence_backup": "local_only",
    }


def export_census(bindings: Path, output: Path, exporter_sha: str) -> dict[str, Any]:
    """Use the historical locator only to find and re-read originals; publish no absolute paths."""
    if output.exists():
        raise ValueError("census output must be new")
    if code_identity(exporter_sha) is None:
        raise ValueError("census requires a complete exporter identity")
    local = json.loads(bindings.read_bytes())
    reader = ArchiveReader({key: Path(path) for key, path in local["archives"].items()})
    if any(
        output.resolve().is_relative_to(root) or root.is_relative_to(output.resolve())
        for root in reader.roots.values()
    ):
        raise ValueError("census output overlaps an original archive")
    cfg = local["census"]
    definition, definition_ref = reader.document(Path(cfg["dataset_census"]))
    aliases = {
        "Mobility": "navigation",
        "Manipulation": "height_man",
        "Perception": "perception",
        "Rearrange": "rearrangement",
    }
    plans = {
        aliases[row["family"]]: [str(x) for x in row["episode_ids"]]
        for row in definition["candidates"]
    }
    raw, locator_ref = reader.read(Path(cfg["historical_locator"]))
    assert raw is not None
    locator = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    rows = []
    cache: dict[str, tuple[Any, str]] = {}
    for previous in locator:
        family = previous["family"]
        if family not in plans:
            continue
        original_path = Path(previous["source"])
        if str(original_path) not in cache:
            cache[str(original_path)] = reader.document(original_path, optional=True)
        original, ref = cache[str(original_path)]
        facts: dict[str, Any] = {}
        pair_eligible = None
        source_code = None
        declared_model = None
        if original is not None:
            if family == "navigation":
                facts = next(
                    x
                    for x in original["arms"]
                    if x["pair_id"] == previous["pair_id"]
                    and ("emos" if x["arm"] == "native" else x["arm"]) == previous["arm"]
                )
                source_code = (
                    original["original_code_sha"]
                    if facts["cohort"] == "original"
                    else original["continuation_code_sha"]
                )
                declared_model = original.get("model")
            elif "arms" in original:
                facts = next(x for x in original["arms"] if x["arm"] == previous["arm"])
                pair_eligible = boolean(original.get("comparison_eligible"))
                source_code = original.get("code_sha")
            else:
                facts = original
            raw_value = boolean(facts.get("official_pddl_success"))
            locator_value = {"True": True, "False": False, "": None}[
                previous["raw_official_pddl_success"]
            ]
            if raw_value is not locator_value:
                raise ValueError("historical locator and accessible original disagree")
        directory_value = (facts.get("process") or {}).get("directory") or facts.get("directory")
        if not directory_value and original_path.name == "arm-summary.json":
            directory_value = str(original_path.parent)
        if original_path.name == "arm-summary.json":
            pair_doc, _ = reader.document(
                original_path.parent.parent / "pair-outcome.json", optional=True
            )
            if pair_doc:
                pair_eligible = boolean(pair_doc.get("comparison_eligible"))
        launch_ref = None
        if directory_value:
            launch, launch_ref = reader.document(
                Path(directory_value) / "launch.json", optional=True
            )
            if launch:
                source_code = launch.get("code_sha") or source_code
                declared_model = launch.get("requested_model") or declared_model
        physical = boolean(facts.get("physical_episode_executed"))
        if (
            physical is None
            and type(facts.get("simulator_steps")) is int
            and facts["simulator_steps"] > 0
        ):
            physical = True
        assessment = {"True": True, "False": False, "": None}[
            previous["verified_official_pddl_success"]
        ]
        rows.append(
            {
                "cohort": "historical-retained",
                "source_subcohort": previous["cohort"],
                "family": family,
                "episode_id": previous["episode_id"],
                "pair_id": previous["pair_id"],
                "arm": previous["arm"],
                "source_accessible": original is not None,
                "raw_official": boolean(facts.get("official_pddl_success")),
                "physical_episode_executed": physical,
                "code_sha": code_identity(source_code),
                "historical_locator_code_claim": code_identity(previous["code_sha"]),
                "requested_model": declared_model,
                "reported_model": None,
                "pair_eligible": pair_eligible,
                "historical_independent_assessment": assessment,
                "assessment_status": "locator claim only; original authority not overwritten",
                "source_ref": ref,
                "locator_ref": locator_ref,
                "launch_ref": launch_ref,
                "gaps": [
                    "model_response_identity_not_reverified_in_census",
                    "historical_conditions_not_uniform",
                ],
            }
        )
    staged = reader.roots[cfg["staged_source_id"]]
    cases, cases_ref = reader.document(staged / "CASES.json")
    for job in cases:
        outcome, ref = reader.document(
            staged / "cases" / job["job_id"] / "pair-outcome.json", optional=True
        )
        for arm in (outcome or {}).get("arms", []):
            launch, launch_ref = reader.document(
                Path(arm["process"]["directory"]) / "launch.json", optional=True
            )
            rows.append(
                {
                    "cohort": "staged-primary",
                    "source_subcohort": "primary",
                    "family": job["family"],
                    "episode_id": job["expected_episode_id"],
                    "pair_id": job["pair_id"],
                    "arm": arm["arm"],
                    "source_accessible": True,
                    "raw_official": boolean(arm.get("official_pddl_success")),
                    "physical_episode_executed": boolean(arm.get("physical_episode_executed")),
                    "code_sha": code_identity((launch or {}).get("code_sha")),
                    "historical_locator_code_claim": None,
                    "requested_model": (launch or {}).get("requested_model"),
                    "reported_model": None,
                    "pair_eligible": boolean(outcome.get("comparison_eligible")),
                    "historical_independent_assessment": None,
                    "assessment_status": "not performed",
                    "source_ref": ref,
                    "locator_ref": cases_ref,
                    "launch_ref": launch_ref,
                    "gaps": ["original native arm may be reused; see migrated cohort"],
                }
            )
    reader.verify_unchanged()
    public_safe(rows)
    summary = census_summary(rows, plans)
    output.mkdir(parents=True)
    files = write_rows(output, "records", rows)
    artifact_files = write_rows(output, "artifacts", list(reader.artifacts.values()))
    write_new(output / "summary.json", encode(summary))
    write_new(output / "REPORT.md", census_report(summary).encode())
    write_new(
        output / "manifest.json",
        encode(
            {
                "schema_version": "roboguide.e1.archive-census/v0.1",
                "exporter_code_sha": exporter_sha,
                "publication": publication_metadata(local),
                "record_files": files,
                "artifact_files": artifact_files,
                "source_ref": definition_ref,
                "status": "staging",
                "scope": "four families; locators rechecked against originals; no merged rate",
                "plans": plans,
            }
        ),
    )
    write_new(
        output / "SHA256SUMS",
        "".join(f"{sha256(p.read_bytes())}  {p.name}\n" for p in sorted(output.iterdir())).encode(),
    )
    write_new(
        bindings.parent / (output.name + "-census-local-source-witness.json"),
        encode(
            {
                "sources": {key: str(path) for key, path in reader.paths.items()},
                "original_bytes_unchanged": True,
            }
        ),
    )
    return validate_census(output)


def census_report(summary: dict[str, Any]) -> str:
    """Render historical coverage without collapsing mixed versions into one success rate."""
    lines = [
        "# E1 四类历史原档清点",
        "",
        "四类历史清单共 "
        f"{summary['four_family_planned_workloads']} 个 workload。下表按来源集合分别计数；"
        "历史覆盖与近期复测不能相加当独立样本，不生成跨版本成功率。",
        "",
        "| 来源/类别 | 计划 | 已定位 | 原记录可比对 | RG 真/假/不可用 | EMOS 真/假/不可用 |",
        "| --- | ---: | ---: | --- | --- | --- |",
    ]
    for key, group in summary["groups"].items():
        arms = group["arms"]
        values = [
            " / ".join(str(arms[a]["raw_official"][k]) for k in ("true", "false", "unavailable"))
            for a in ("roboguide", "emos")
        ]
        rg = arms["roboguide"]
        pairs = (
            "unknown" if rg["pair_eligibility_unknown"] else str(rg["pair_eligible_as_recorded"])
        )
        lines.append(
            f"| {key} | {group['planned_workloads']} | {group['located_workloads']} | "
            f"{pairs} | {values[0]} | {values[1]} |"
        )
    lines += [
        "",
        "## 访问缺口与解释",
        "",
        "- 历史清点以已保存 locator 找原始 JSON 后重新读取，不仅复制之前汇总。"
        "未定位的原档是访问/覆盖缺口，不证明实验未做过。",
        "- 模型声明来自原 launch 或原报告，返回模型身份未在清点阶段逐次复核；"
        "已迁移批次的记账另有完整投影。代码未知不使用当前 main 补写。",
        "- 导航旧报告缺独立 Pair Validator 资格，保留 unknown；"
        "其他旧 raw eligibility 也不能证明严格组织层等价或完整初态相同。",
        "- height_man 的原始 EMOS 结构化结果与独立复核不同："
        "本表保持原始值，独立复核声明保留于逐行 assessment 字段，未覆盖原值。",
        "- 额外 distance 变体不在四类范围内；小样本诊断、后续失败选择队列均不混入。",
        "- 原档仍为 local_only；本次未建立独立远端备份。",
        "",
    ]
    return "\n".join(lines)


def validate_census(root: Path) -> dict[str, Any]:
    """Recompute the complete historical census using only portable committed records."""
    files = verify_files(root)
    manifest = json.loads((root / "manifest.json").read_bytes())
    if manifest.get("schema_version") != "roboguide.e1.archive-census/v0.1":
        raise ValueError("unsupported census generation")
    rows: list[dict[str, Any]] = []
    for name in manifest["record_files"]:
        if name not in files:
            raise ValueError("missing census record shard")
        rows.extend(json.loads(line) for line in (root / name).read_text().splitlines())
    identities = {(row["cohort"], row["family"], row["episode_id"], row["arm"]) for row in rows}
    if len(identities) != len(rows):
        raise ValueError("duplicate historical cohort/workload arm")
    artifacts: list[dict[str, Any]] = []
    for name in manifest["artifact_files"]:
        if name not in files:
            raise ValueError("missing census artifact shard")
        artifacts.extend(json.loads(line) for line in (root / name).read_text().splitlines())
    references = {row["artifact_id"] for row in artifacts}
    if len(references) != len(artifacts):
        raise ValueError("duplicate census artifact")
    for row in rows:
        if row["raw_official"] is not None and type(row["raw_official"]) is not bool:
            raise ValueError("invalid historical official value")
        for key in ("source_ref", "locator_ref", "launch_ref"):
            if row.get(key) and row[key] not in references:
                raise ValueError("unresolved census source")
    summary = census_summary(rows, manifest["plans"])
    if (
        encode(summary) != (root / "summary.json").read_bytes()
        or census_report(summary).encode() != (root / "REPORT.md").read_bytes()
    ):
        raise ValueError("census statistics do not recompute")
    return {
        "state": "PASS",
        "records": len(rows),
        "four_family_planned_workloads": summary["four_family_planned_workloads"],
        "raw_archive_verification": "not performed by portable validation",
    }
