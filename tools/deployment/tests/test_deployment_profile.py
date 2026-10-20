"""Check declaration boundaries against actual files without creating device or Node facts."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import pytest
from deployment_profile import digest, inspect, load, validate


def pin(path: Path) -> dict[str, str]:
    """Retain the actual original file digest rather than an implementation-owned Boolean."""
    return {"path": str(path), "sha256": digest(path.read_bytes())}


def profile(root: Path) -> dict[str, Any]:
    """Declare a unit metadata profile with unobserved binaries and no Node inventory."""
    original = root / "original.json"
    original.write_text('{"passed":false,"physical_evidence":null}')
    database = root / "controller.sqlite3"
    database.touch()
    return {
        "schema": "roboguide.deployment-profile/v0.1",
        "profile_id": "metadata-unit",
        "revision": 1,
        "inspection_budget_ms": 30000,
        "mode": "single-controller",
        "local_host": "here",
        "hosts": [{"id": "here", "fault_domain": "here", "trust_domain": "here"}],
        "components": [
            {
                "id": name,
                "kind": kind,
                "process_id": name,
                "host": "here",
                "domain": "local",
                "tenants": ["local"],
                "binary": None,
                "inputs": [pin(original)],
                "application_schema": None,
            }
            for name, kind in (("control", "controller"), ("inspector", "operator-client"))
        ],
        "stores": [
            {
                "id": "checkpoint",
                "owner": "control",
                "path": str(database),
                "kind": "sqlite",
                "writer_contract": "writer",
                "recovery_contract": "writer",
            }
        ],
        "contracts": [{"id": "writer", "version": "unit-declaration/v1", "file": pin(original)}],
        "links": [
            {
                "id": "observe",
                "source": "inspector",
                "target": "control",
                "protocol": "operator-http",
                "contract": "writer",
                "endpoint": "http://127.0.0.1:10001",
                "tls": None,
                "probe": "none",
            }
        ],
        "faults": [
            {
                "id": "sql",
                "affected": ["control"],
                "owner": "control",
                "trigger": "sql-failure",
                "rto_ms": 1000,
                "rpo": {"amount": 0, "unit": "committed-commands"},
                "expected_fence": "write-disabled",
                "criteria": ["software"],
            }
        ],
        "criteria": [
            {
                "id": "software",
                "stage": "F0",
                "assurance": "software",
                "required": True,
                "evidence": pin(original),
            },
            {
                "id": "hardware",
                "stage": "F0",
                "assurance": "hardware",
                "required": True,
                "evidence": None,
            },
        ],
    }


def test_freeze_does_not_turn_file_integrity_into_a_pass_or_hardware_fact(tmp_path: Path) -> None:
    """Keep a failing evidence file and pending hardware distinct from source integrity."""
    selected = profile(tmp_path)
    result = inspect(selected)
    assert result["declaration"]["criteria"][0]["evidence"] == selected["criteria"][0]["evidence"]
    assert json.loads(Path(result["input_pins"][0]["path"]).read_bytes())["passed"] is False
    assert result["criteria"][0]["evidence_integrity_checked"] is True
    assert result["criteria"][1]["evidence_integrity_checked"] is False
    assert result["software_complete_asserted"] is False
    assert result["hardware_evidence_created"] is False
    assert result["services_started"] == result["nodes_registered"] == 0
    assert result["link_observations"]["observe"]["observed"] is False
    assert result["rto_rpo_achievement_asserted"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("node_inventory", []),
        ("stop_confirmed", True),
        ("revision", True),
        ("local_host", "undeclared"),
    ],
)
def test_invalid_or_authoritative_top_level_input_is_rejected(
    tmp_path: Path, field: str, value: Any
) -> None:
    """Profile syntax cannot add inventory, stop authority or coerce a Boolean revision."""
    selected = profile(tmp_path)
    selected[field] = value
    with pytest.raises(ValueError):
        validate(selected)


@pytest.mark.parametrize(
    "case",
    [
        "remote",
        "cross-host",
        "cross-domain",
        "credentials",
        "empty-credentials",
        "query",
        "formal-as-http",
    ],
)
def test_plaintext_and_ambiguous_link_boundaries_are_rejected(tmp_path: Path, case: str) -> None:
    """A listener declaration cannot hide a remote plaintext or ambiguous authenticated boundary."""
    selected = profile(tmp_path)
    link = selected["links"][0]
    if case == "remote":
        link["endpoint"] = "http://192.0.2.1:8000"
    elif case == "cross-host":
        selected["hosts"].append(
            {"id": "elsewhere", "fault_domain": "elsewhere", "trust_domain": "elsewhere"}
        )
        selected["components"][1]["host"] = "elsewhere"
    elif case == "cross-domain":
        selected["components"][1]["domain"] = "another-domain"
    elif case == "credentials":
        link["endpoint"] = "http://ignored:secret@127.0.0.1:8000"
    elif case == "empty-credentials":
        link["endpoint"] = "http://@127.0.0.1:8000"
    elif case == "formal-as-http":
        link["protocol"] = "node-grpc"
        link["probe"] = "health"
    else:
        link["endpoint"] += "?authority=admin"
    with pytest.raises(ValueError):
        validate(selected)


def test_changed_original_file_refuses_without_rewriting_input(tmp_path: Path) -> None:
    """A changed actual authority/config/evidence file cannot retain its earlier pin."""
    selected = profile(tmp_path)
    original = tmp_path / "original.json"
    original.write_text('{"passed":true}')
    with pytest.raises(ValueError, match="digest changed"):
        inspect(selected)
    assert original.read_text() == '{"passed":true}'


def test_hardlink_alias_cannot_create_another_declared_store_owner(tmp_path: Path) -> None:
    """Independent ownership names cannot hide an alias of an actual storage inode."""
    selected = profile(tmp_path)
    other = tmp_path / "aliased.sqlite3"
    os.link(tmp_path / "controller.sqlite3", other)
    selected["stores"].append({**selected["stores"][0], "id": "second", "path": str(other)})
    with pytest.raises(ValueError, match="aliases"):
        inspect(selected)


def test_directory_ownership_cannot_overlap_another_component(tmp_path: Path) -> None:
    """A parent directory cannot be declared independent of another component's nested store."""
    selected = profile(tmp_path)
    selected["stores"].append(
        {
            **selected["stores"][0],
            "id": "parent",
            "owner": "inspector",
            "path": str(tmp_path),
            "kind": "directory",
        }
    )
    with pytest.raises(ValueError, match="overlapping"):
        inspect(selected)


def test_symlinked_original_input_is_never_followed(tmp_path: Path) -> None:
    """Matching file bytes through a symlink cannot replace the original ordinary path."""
    selected = profile(tmp_path)
    alias = tmp_path / "alias.json"
    alias.symlink_to(tmp_path / "original.json")
    selected["components"][0]["inputs"] = [pin(alias)]
    with pytest.raises(ValueError, match="symlinks"):
        inspect(selected)


def test_cohosted_components_require_the_same_original_process_inputs(tmp_path: Path) -> None:
    """Two components labelled one process cannot belong to different hosts or domains."""
    selected = profile(tmp_path)
    selected["components"][1]["process_id"] = "control"
    selected["components"][1]["domain"] = "different"
    with pytest.raises(ValueError, match="cohosted"):
        validate(selected)


def test_fault_evidence_must_exist_in_the_declared_matrix(tmp_path: Path) -> None:
    """RTO/RPO targets cannot refer to nonexistent acceptance gates."""
    selected = profile(tmp_path)
    selected["faults"][0]["criteria"] = ["missing-proof"]
    with pytest.raises(ValueError, match="fault owner"):
        validate(selected)


def test_duplicate_json_and_nonfinite_values_never_select_a_different_profile(
    tmp_path: Path,
) -> None:
    """The original wire document cannot silently overwrite identity or contain NaN."""
    original = tmp_path / "duplicated.json"
    original.write_text('{"revision":1,"revision":2}')
    with pytest.raises(ValueError, match="duplicate"):
        load(original)
    original.write_text('{"revision":NaN}')
    with pytest.raises(ValueError):
        load(original)


def test_validation_does_not_mutate_the_authored_profile(tmp_path: Path) -> None:
    """Inspection never fills declarations with invented execution or physical data."""
    selected = profile(tmp_path)
    original = copy.deepcopy(selected)
    inspect(selected)
    assert selected == original


def test_remote_store_is_not_inferred_from_the_inspectors_local_files(tmp_path: Path) -> None:
    """A remote host declaration cannot turn a coincident local path into remote evidence."""
    selected = profile(tmp_path)
    selected["hosts"].append({"id": "remote", "fault_domain": "remote", "trust_domain": "remote"})
    selected["components"].append(
        {
            **selected["components"][0],
            "id": "remote-provider",
            "kind": "memory",
            "process_id": "remote-provider",
            "host": "remote",
        }
    )
    selected["stores"].append(
        {
            **selected["stores"][0],
            "id": "remote-sql",
            "owner": "remote-provider",
            "path": "/uninspected-remote/store.sqlite3",
        }
    )
    observed = inspect(selected)["declared_storage_owners"][-1]
    assert observed["observed"] is False and observed["reason"] == "remote-store-not-inspected"
    assert "inode" not in observed and observed["actual_writer_lease_observed"] is False
