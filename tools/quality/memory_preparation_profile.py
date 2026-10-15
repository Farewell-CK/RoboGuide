"""Build explicit two-process file deployments without introducing a robot inventory."""

from __future__ import annotations

import copy
import json
import ssl
import uuid
from pathlib import Path
from typing import Any

from check_artifact_authority_processes import grant
from check_identity_processes import client, pki
from check_memory_processes import memory_config
from check_state_verifier_processes import node_config, plan, save


def tls(
    output: Path, nodes: list[str], memory_id: str, environment: dict[str, str]
) -> tuple[dict[str, ssl.SSLContext], dict[str, str]]:
    """Issue private credentials and scoped grants; enrollment remains an API action."""
    certificates = output / "pki"
    digests = pki(certificates)
    contexts = {
        name: client(certificates, name)
        for name in ("admin", "viewer", "enrolment-first", "enrolment-next")
    }
    operator = {
        "schema_version": "roboguide.operator-authorization/v0.1",
        "registry_id": "file-operators-" + uuid.uuid4().hex,
        "revision": 1,
        "grants": {
            "sha256:" + digests["admin"]: {
                "principal_id": "file-administrator",
                "role": "recovery-admin",
                "tenant_ids": ["*"],
            },
            "sha256:" + digests["viewer"]: {
                "principal_id": "independent-file-reader",
                "role": "operator",
                "tenant_ids": ["local"],
            },
        },
    }
    artifact_grants = {}
    for index, name in enumerate(("enrolment-first", "enrolment-next")):
        value = grant("file-node-" + str(index), "writer", "local")
        value.update(node_id=nodes[index], provider_ids=["files"], memory_ids=[memory_id])
        artifact_grants["sha256:" + digests[name]] = value
    reader = grant("independent-file-reader", "viewer", "local")
    reader["memory_ids"] = [memory_id]
    artifact_grants["sha256:" + digests["viewer"]] = reader
    artifact = {
        "schema_version": "roboguide.artifact-authorization/v0.1",
        "registry_id": "file-content-" + uuid.uuid4().hex,
        "revision": 1,
        "grants": artifact_grants,
    }
    for plane, profile in (("OPERATOR", operator), ("ARTIFACT", artifact)):
        path = output / (plane.lower() + "-authorization.json")
        save(path, profile)
        environment.update(
            {
                f"ROBOGUIDE_{plane}_MTLS_SERVER_CERT": str(certificates / "server.crt"),
                f"ROBOGUIDE_{plane}_MTLS_SERVER_KEY": str(certificates / "server.key"),
                f"ROBOGUIDE_{plane}_MTLS_CLIENT_CA": str(certificates / "ca.crt"),
                f"ROBOGUIDE_{plane}_MTLS_ROLE_MAP": str(path),
            }
        )
    environment.update(
        {
            "TOKIO_WORKER_THREADS": "8",
            "ROBOGUIDE_NODE_MTLS_SERVER_CERT": str(certificates / "server.crt"),
            "ROBOGUIDE_NODE_MTLS_SERVER_KEY": str(certificates / "server.key"),
            "ROBOGUIDE_NODE_MTLS_CLIENT_CA": str(certificates / "ca.crt"),
            "ROBOGUIDE_NODE_MTLS_NODE_CERT_DIGESTS": json.dumps(
                {
                    "schema": "roboguide.node-identity-registry/v0.1",
                    "registry_id": "file-node-identities-" + uuid.uuid4().hex,
                    "records": {},
                }
            ),
        }
    )
    return contexts, digests


def node_environment(environment: dict[str, str], output: Path, name: str) -> dict[str, str]:
    """Bind each actual Node process to its own certificate rather than sharing Node identity."""
    result = dict(environment)
    for prefix in ("ROBOGUIDE_ARTIFACT_CLIENT", "ROBOGUIDE_NODE_MTLS_CLIENT"):
        result[prefix + "_CERT"] = str(output / "pki" / (name + ".crt"))
        result[prefix + "_KEY"] = str(output / "pki" / (name + ".key"))
    result["ROBOGUIDE_ARTIFACT_CLIENT_CA"] = str(output / "pki/ca.crt")
    result["ROBOGUIDE_NODE_MTLS_SERVER_CA"] = str(output / "pki/ca.crt")
    result["ROBOGUIDE_NODE_MTLS_DOMAIN"] = "localhost"
    return result


def configuration(
    directory: Path, node: str, grpc: str, backend: str, cas: str, consumer: bool
) -> str:
    """Map real file health, hashing, import/query and availability to configured Local How."""
    text = node_config(directory, node, grpc, backend)
    text = text.replace("node-config/v0.7", "node-config/v0.10")
    text = text.replace(f'server_endpoint = "http://{grpc}"', f'server_endpoint = "https://{grpc}"')
    before, rest = text.split("[[state_exports]]", 1)
    text = before + "[[operations]]" + rest.split("[[operations]]", 1)[1]
    text = text.replace('path = "/health"', 'path = "/v1/file-jobs/health"')
    text = text.replace('path = "/capability"', 'path = "/v1/file-jobs/health"')
    text = text.replace(
        '[capability_profiles.readiness]\nstate_pointer = "/state"',
        '[capability_profiles.readiness]\nstate_pointer = "/readiness"',
    )
    text = text.replace(
        "compute.hash-files@v1",
        "compute.hash-imported-file@v1" if consumer else "compute.hash-exported-file@v1",
    )
    text = text.replace('path = "/execute"', 'path = "/v1/file-jobs/compute"')
    text = text.replace('path = "/status"', 'path = "/v1/file-jobs/status"')
    text = text.replace('path = "/cancel"', 'path = "/v1/file-jobs/cancel"')
    text = text.replace(
        '[operations.workflow.status.operation]\nkind = "http"\nmethod = "POST"',
        '[operations.workflow.status.operation]\nkind = "http"\nmethod = "GET"',
    )
    text = text.replace(
        'reason_pointer = "/steps/status/detail"', 'reason_pointer = "/steps/status/reason"'
    )
    text += memory_config(backend, cas, directory)
    if consumer:
        text += """
[memory_providers.availability]
result_pointer = "/availability"
[memory_providers.availability.observation]
id = "availability"
connection = "memory-http"
[memory_providers.availability.observation.operation]
kind = "http"
method = "GET"
path = "/v1/availability"
[[memory_providers.availability.observation.request.bindings]]
target = "/memory_import"
[memory_providers.availability.observation.request.bindings.value]
kind = "pointer"
pointer = "/invocation/memory_import"
"""
    return text


def mission(identifier: str, manifest: dict[str, Any]) -> dict[str, Any]:
    """Declare two real file computations; only the second requires an immutable imported input."""
    result = copy.deepcopy(plan(identifier))
    result["schema_version"] = "roboguide.mission-plan/v0.9"
    result["mission"]["objective"] = (
        "hash an exported file then independently hash its imported bytes"
    )
    result["mission"]["actors"] = [{"id": "source-process"}, {"id": "consumer-process"}]
    result["contexts"][0]["roles"] = [
        {"id": "source", "actor": "source-process"},
        {"id": "consumer", "actor": "consumer-process"},
    ]
    for index, task in enumerate(result["tasks"]):
        role = task["roles"][0]
        role["context_role"] = "consumer" if index else "source"
        operation = {
            "namespace": "compute",
            "name": "hash-imported-file" if index else "hash-exported-file",
            "version": "v1",
        }
        role["requirements"]["capabilities"][0]["contract"] = operation
        role["requirements"]["memories"] = (
            [
                {
                    "selector": manifest["selector"],
                    "kind": manifest["kind"],
                    "payload_schema": manifest["payload_schema"],
                    "media_type": manifest["media_type"],
                }
            ]
            if index
            else []
        )
        role["execution_intent"]["operation"] = operation
        role["execution_intent"]["parameters"] = (
            {"rounds": 2}
            if index
            else {
                "rounds": 2,
                "input_memory_id": manifest["selector"]["memory_id"],
                "input_revision_id": manifest["selector"]["revision_id"],
            }
        )
    return result
