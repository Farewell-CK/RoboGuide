"""Create private real software credentials for the maintained spatial Node process check."""

from __future__ import annotations

import json
import ssl
import uuid
from pathlib import Path
from typing import Any

from check_artifact_authority_processes import grant
from check_identity_processes import client, pki


def configure(
    output: Path,
    node_id: str,
    map_id: str,
    environment: dict[str, str],
) -> tuple[dict[str, ssl.SSLContext], str]:
    """Configure actual mutual TLS, stable scoped grants and an initially empty Node registry."""
    certificates = output / "pki"
    digests = pki(certificates)
    contexts = {name: client(certificates, name) for name in ("admin", "viewer", "enrolment-first")}
    operator = {
        "schema_version": "roboguide.operator-authorization/v0.1",
        "registry_id": "spatial-operators-" + uuid.uuid4().hex,
        "revision": 1,
        "grants": {
            "sha256:" + digests["admin"]: {
                "principal_id": "spatial-administrator",
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
    writer: dict[str, Any] = grant("configured-spatial-node", "writer", "local")
    writer.update(
        node_id=node_id,
        provider_ids=["spatial-file-maps", "spatial-occupancy-maps"],
        map_ids=[map_id],
    )
    viewer = grant("independent-file-reader", "viewer", "local")
    viewer["map_ids"] = [map_id]
    artifact = {
        "schema_version": "roboguide.artifact-authorization/v0.1",
        "registry_id": "spatial-content-" + uuid.uuid4().hex,
        "revision": 1,
        "grants": {
            "sha256:" + digests["enrolment-first"]: writer,
            "sha256:" + digests["viewer"]: viewer,
        },
    }
    for plane, profile in (("OPERATOR", operator), ("ARTIFACT", artifact)):
        path = output / (plane.lower() + "-authorization.json")
        path.write_text(json.dumps(profile, indent=2) + "\n")
        environment.update(
            {
                f"ROBOGUIDE_{plane}_MTLS_SERVER_CERT": str(certificates / "server.crt"),
                f"ROBOGUIDE_{plane}_MTLS_SERVER_KEY": str(certificates / "server.key"),
                f"ROBOGUIDE_{plane}_MTLS_CLIENT_CA": str(certificates / "ca.crt"),
                f"ROBOGUIDE_{plane}_MTLS_ROLE_MAP": str(path),
            }
        )
    environment.pop("ROBOGUIDE_OPERATOR_LOCAL_RECOVERY_ADMIN", None)
    environment.update(
        {
            "TOKIO_WORKER_THREADS": "8",
            "ROBOGUIDE_NODE_MTLS_SERVER_CERT": str(certificates / "server.crt"),
            "ROBOGUIDE_NODE_MTLS_SERVER_KEY": str(certificates / "server.key"),
            "ROBOGUIDE_NODE_MTLS_CLIENT_CA": str(certificates / "ca.crt"),
            "ROBOGUIDE_NODE_MTLS_NODE_CERT_DIGESTS": json.dumps(
                {
                    "schema": "roboguide.node-identity-registry/v0.1",
                    "registry_id": "spatial-node-identity-" + uuid.uuid4().hex,
                    "records": {},
                }
            ),
            "ROBOGUIDE_NODE_MTLS_CLIENT_CERT": str(certificates / "enrolment-first.crt"),
            "ROBOGUIDE_NODE_MTLS_CLIENT_KEY": str(certificates / "enrolment-first.key"),
            "ROBOGUIDE_NODE_MTLS_SERVER_CA": str(certificates / "ca.crt"),
            "ROBOGUIDE_NODE_MTLS_DOMAIN": "localhost",
            "ROBOGUIDE_ARTIFACT_CLIENT_CERT": str(certificates / "enrolment-first.crt"),
            "ROBOGUIDE_ARTIFACT_CLIENT_KEY": str(certificates / "enrolment-first.key"),
            "ROBOGUIDE_ARTIFACT_CLIENT_CA": str(certificates / "ca.crt"),
        }
    )
    return contexts, digests["enrolment-first"]
