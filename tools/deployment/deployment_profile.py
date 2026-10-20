"""Inspect explicit deployment declarations and freeze bounded readonly observations."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import stat
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

SCHEMA = "roboguide.deployment-profile/v0.1"
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
KINDS = frozenset(
    {
        "controller",
        "replica",
        "mission",
        "artifact",
        "memory",
        "node",
        "runtime-mi",
        "operator-client",
    }
)
PROTOCOLS = frozenset(
    {
        "mission-http",
        "operator-http",
        "artifact-http",
        "node-grpc",
        "replica",
        "local-unix",
        "metrics",
    }
)
PROBES = {
    "none": None,
    "tls": None,
    "health": "/healthz",
    "memory-instance": "/v1/instance",
    "inventory": "/v1/inventory",
}


def canonical(value: object) -> bytes:
    """Encode the exact declaration identity without NaN or ambient configuration."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def digest(value: bytes) -> str:
    """Return the explicitly qualified byte identity of an original input or receipt."""
    return "sha256:" + hashlib.sha256(value).hexdigest()


def closed(value: object, fields: set[str]) -> dict[str, Any]:
    """Reject unknown or missing fields before interpreting any declaration."""
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("deployment object has missing or unknown fields")
    return value


def identity(value: object) -> str:
    """Require a bounded literal ID, never a selector expression or a filename."""
    if not isinstance(value, str) or IDENTIFIER.fullmatch(value) is None:
        raise ValueError("invalid deployment identifier")
    return value


def integer(value: object, maximum: int) -> int:
    """Reject Boolean integer coercion and unbounded configuration budgets."""
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError("invalid deployment positive integer bound")
    return value


def rows(value: object, maximum: int, *, empty: bool = False) -> list[Any]:
    """Bound every explicitly supplied topology or evidence collection."""
    if not isinstance(value, list) or not (0 if empty else 1) <= len(value) <= maximum:
        raise ValueError("deployment collection exceeds its bound or is empty")
    return value


def real_path(value: object, *, directory: bool = False) -> Path:
    """Require an actual absolute ordinary path without symlinks at any ancestor."""
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value):
        raise ValueError("deployment path must be bounded literal text")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("deployment path must be absolute and normalized")
    for parent in (*reversed(path.parents), path):
        metadata = parent.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError("deployment paths cannot traverse symlinks")
    mode = path.stat().st_mode
    if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
        raise ValueError("deployment input has the wrong actual file type")
    return path


def file_pin(value: object, *, maximum: int = 2 * 1024 * 1024) -> dict[str, Any]:
    """Hash an actual complete input and reject a changed or oversized declaration."""
    selected = closed(value, {"path", "sha256"})
    expected = selected["sha256"]
    if not isinstance(expected, str) or DIGEST.fullmatch(expected) is None:
        raise ValueError("deployment input requires its exact SHA-256 digest")
    path = real_path(selected["path"])
    if path.stat().st_size > maximum:
        raise ValueError("deployment input exceeds its byte bound")
    with path.open("rb") as stream:
        actual = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != expected:
        raise ValueError("original deployment file digest changed: " + str(path))
    return {"path": str(path), "sha256": actual, "bytes": path.stat().st_size}


def keyed(values: object, fields: set[str], maximum: int) -> dict[str, dict[str, Any]]:
    """Index one bounded closed collection without silently overwriting a repeated identity."""
    result = {}
    for value in rows(values, maximum):
        item = closed(value, fields)
        key = identity(item["id"])
        if key in result:
            raise ValueError("duplicate deployment identity: " + key)
        result[key] = item
    return result


def validate(profile: object) -> dict[str, Any]:
    """Validate declaration topology, unique storage ownership and every explicit trust boundary."""
    value = closed(
        profile,
        {
            "schema",
            "profile_id",
            "revision",
            "inspection_budget_ms",
            "mode",
            "local_host",
            "hosts",
            "components",
            "stores",
            "links",
            "contracts",
            "faults",
            "criteria",
        },
    )
    if value["schema"] != SCHEMA or value["mode"] not in {
        "single-controller",
        "replicated-controller",
    }:
        raise ValueError("unsupported deployment schema or mode")
    identity(value["profile_id"])
    integer(value["revision"], 2**63 - 1)
    integer(value["inspection_budget_ms"], 300000)
    hosts = keyed(value["hosts"], {"id", "fault_domain", "trust_domain"}, 64)
    for host in hosts.values():
        identity(host["fault_domain"])
        identity(host["trust_domain"])
    if value["local_host"] not in hosts:
        raise ValueError("explicit local inspection host is not declared")
    components = keyed(
        value["components"],
        {
            "id",
            "kind",
            "process_id",
            "host",
            "domain",
            "tenants",
            "binary",
            "inputs",
            "application_schema",
        },
        128,
    )
    for component in components.values():
        if component["kind"] not in KINDS or component["host"] not in hosts:
            raise ValueError("component kind or original host is unavailable")
        identity(component["domain"])
        identity(component["process_id"])
        tenants = rows(component["tenants"], 128)
        if len(set(identity(tenant) for tenant in tenants)) != len(tenants):
            raise ValueError("component tenants must be exact distinct identities")
        if component["application_schema"] is not None and (
            component["kind"] not in {"controller", "replica"}
            or not isinstance(component["application_schema"], str)
            or not 1 <= len(component["application_schema"]) <= 128
        ):
            raise ValueError("application schema inspection belongs to Controller binaries")
        rows(component["inputs"], 128, empty=True)
    processes: dict[str, tuple[object, ...]] = {}
    for component in components.values():
        signature = (component["host"], component["domain"], canonical(component["binary"]))
        process = component["process_id"]
        if process in processes and signature != processes[process]:
            raise ValueError("cohosted components disagree on the actual process deployment")
        processes[process] = signature
    controllers = [row for row in components.values() if row["kind"] == "controller"]
    replicas = [row for row in components.values() if row["kind"] == "replica"]
    if not controllers:
        raise ValueError("deployment must declare its actual Controller composition")
    if value["mode"] == "single-controller":
        if len(controllers) != 1 or replicas:
            raise ValueError("single Controller profile cannot conceal replicas or extra writers")
    elif not (
        3 <= len(replicas) <= 9
        and len({r["host"] for r in replicas}) == len(replicas)
        and len({hosts[r["host"]]["fault_domain"] for r in replicas}) >= 3
    ):
        raise ValueError(
            "replicated profile requires declared distinct hosts and three fault domains"
        )
    contracts = keyed(value["contracts"], {"id", "version", "file"}, 64)
    for contract in contracts.values():
        if not isinstance(contract["version"], str) or not 1 <= len(contract["version"]) <= 128:
            raise ValueError("deployment contract version is unavailable")
    stores = keyed(
        value["stores"],
        {"id", "owner", "path", "kind", "writer_contract", "recovery_contract"},
        256,
    )
    for store in stores.values():
        if store["owner"] not in components or store["kind"] not in {"sqlite", "directory"}:
            raise ValueError("store requires one declared actual writer and fixed storage kind")
        if any(store[name] not in contracts for name in ("writer_contract", "recovery_contract")):
            raise ValueError("store writer/recovery support is not pinned")
    links = keyed(
        value["links"],
        {"id", "source", "target", "protocol", "contract", "endpoint", "tls", "probe"},
        256,
    )
    for link in links.values():
        if (
            link["source"] not in components
            or link["target"] not in components
            or link["protocol"] not in PROTOCOLS
            or link["contract"] not in contracts
            or link["probe"] not in PROBES
        ):
            raise ValueError("link endpoint, protocol, support contract or probe is undeclared")
        source, target = components[link["source"]], components[link["target"]]
        if (
            not isinstance(link["endpoint"], str)
            or len(link["endpoint"]) > 2048
            or any(ord(character) < 33 for character in link["endpoint"])
        ):
            raise ValueError("link endpoint must be bounded literal text")
        parsed = urlsplit(link["endpoint"])
        if parsed.scheme == "unix":
            if (
                link["protocol"] != "local-unix"
                or source["host"] != target["host"]
                or source["domain"] != target["domain"]
                or parsed.netloc
                or link["tls"] is not None
                or link["probe"] != "none"
                or not Path(parsed.path).is_absolute()
                or ".." in Path(parsed.path).parts
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("Unix boundaries must retain the original same-host owner")
            continue
        if (
            parsed.scheme not in {"http", "https", "tls"}
            or not parsed.hostname
            or parsed.port is None
            or parsed.port == 0
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("link requires one explicit bounded transport origin")
        if parsed.scheme == "http":
            if (
                source["host"] != target["host"]
                or source["domain"] != target["domain"]
                or link["tls"] is not None
                or not ipaddress.ip_address(parsed.hostname).is_loopback
                or link["probe"] == "tls"
            ):
                raise ValueError("plaintext transport requires literal same-host loopback")
        else:
            tls = closed(
                link["tls"], {"ca", "certificate", "key_file", "server_name", "server_leaf"}
            )
            if (
                not isinstance(tls["server_name"], str)
                or not tls["server_name"]
                or len(tls["server_name"]) > 253
                or re.fullmatch(r"[A-Za-z0-9.:-]+", tls["server_name"]) is None
                or not isinstance(tls["server_leaf"], str)
                or DIGEST.fullmatch(tls["server_leaf"]) is None
            ):
                raise ValueError("TLS requires an explicit peer name and exact original leaf pin")
        if parsed.scheme == "tls" and link["probe"] not in {"none", "tls"}:
            raise ValueError("a raw TLS transport cannot claim an HTTP probe")
        if link["protocol"] in {"node-grpc", "replica"} and link["probe"] not in {"none", "tls"}:
            raise ValueError("formal framed protocols cannot claim an HTTP probe")
    criteria = keyed(value["criteria"], {"id", "stage", "assurance", "required", "evidence"}, 256)
    for criterion in criteria.values():
        if (
            criterion["stage"] not in {"F" + str(i) for i in range(11)}
            or criterion["assurance"] not in {"software", "hardware"}
            or type(criterion["required"]) is not bool
        ):
            raise ValueError("criterion must retain its exact stage and assurance boundary")
    faults: set[str] = set()
    for fault in rows(value["faults"], 128):
        selected = closed(
            fault,
            {"id", "affected", "owner", "trigger", "rto_ms", "rpo", "expected_fence", "criteria"},
        )
        fault_id = identity(selected["id"])
        if fault_id in faults:
            raise ValueError("duplicate fault identity")
        faults.add(fault_id)
        integer(selected["rto_ms"], 86_400_000)
        if (
            selected["owner"] not in components
            or any(item not in components for item in rows(selected["affected"], 128))
            or any(item not in criteria for item in rows(selected["criteria"], 256))
            or selected["trigger"]
            not in {
                "process-exit",
                "host-loss",
                "partition",
                "sql-failure",
                "certificate-expiry",
                "storage-loss",
            }
            or selected["expected_fence"] not in {"unknown-hold", "write-disabled", "local-safety"}
        ):
            raise ValueError("fault owner, evidence or conservative expected fence is unavailable")
        rpo = closed(selected["rpo"], {"amount", "unit"})
        if type(rpo["amount"]) is not int or not 0 <= rpo["amount"] <= 2**63 - 1:
            raise ValueError("RPO must retain an exact nonnegative target")
        if rpo["unit"] not in {
            "committed-commands",
            "finalized-bytes",
            "accepted-batches",
            "journal-rows",
        }:
            raise ValueError("RPO unit is not an explicit durable boundary")
    return value


def tls_context(tls: dict[str, Any]) -> ssl.SSLContext:
    """Check public input pins and the real private key without recording key contents."""
    ca, certificate = file_pin(tls["ca"]), file_pin(tls["certificate"])
    key = real_path(tls["key_file"])
    if key.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("TLS private key exceeds its bounded input size")
    context = ssl.create_default_context(cafile=ca["path"])
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certificate["path"], key)
    return context


def observe_link(link: dict[str, Any]) -> dict[str, Any]:
    """Perform only the declared bounded TLS handshake or fixed HTTP GET from this inspector."""
    selected = urlsplit(link["endpoint"])
    if link["probe"] == "none":
        return {"observed": False, "reason": "explicitly-not-probed"}
    started = time.monotonic()
    tls = link["tls"]
    channel = socket.create_connection((selected.hostname, selected.port), timeout=5)
    leaf = None
    try:
        if tls is not None:
            context = tls_context(tls)
            if link["protocol"] == "node-grpc":
                context.set_alpn_protocols(["h2"])
            channel = context.wrap_socket(channel, server_hostname=tls["server_name"])
            leaf = digest(channel.getpeercert(binary_form=True) or b"")
            if leaf != tls["server_leaf"]:
                raise ValueError("actual endpoint TLS leaf differs from the frozen peer")
        path = PROBES[link["probe"]]
        status, body_digest, node_count = None, None, None
        if path is not None:
            connection = http.client.HTTPConnection(selected.hostname, selected.port, timeout=5)
            connection.sock = channel
            try:
                connection.request("GET", path)
                response = connection.getresponse()
                body = response.read(1024 * 1024 + 1)
                if len(body) > 1024 * 1024 or response.status != 200:
                    raise ValueError(
                        "deployment readonly probe failed or exceeded its response bound"
                    )
                status, body_digest = response.status, digest(body)
                if link["probe"] == "inventory":
                    inventory = json.loads(body)
                    if (
                        not isinstance(inventory, dict)
                        or not isinstance(inventory.get("nodes"), list)
                        or len(inventory["nodes"]) > 4096
                    ):
                        raise ValueError("actual inventory has no bounded Node list")
                    node_count = len(inventory["nodes"])
            finally:
                connection.close()
        return {
            "observed": True,
            "tls_leaf": leaf,
            "http_status": status,
            "response_digest": body_digest,
            "reported_node_count": node_count,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "readiness_or_physical_assurance": False,
        }
    finally:
        channel.close()


def inspect(profile: object, *, probe: bool = False) -> dict[str, Any]:
    """Keep actual checks distinct from declarations, missing gates and physical evidence."""
    value = validate(profile)
    pins: list[dict[str, Any]] = []
    deadline = time.monotonic() + value["inspection_budget_ms"] / 1000

    def check_budget() -> None:
        """Check the original inspection budget without turning expiry into success."""
        if time.monotonic() > deadline:
            raise TimeoutError("original deployment inspection budget expired")

    def pinned(item: object, maximum: int = 2 * 1024 * 1024) -> dict[str, Any]:
        """Bound actual input count and total bytes before publishing a configuration receipt."""
        check_budget()
        if len(pins) >= 2048:
            raise ValueError("deployment inspection exceeds its total input count")
        result = file_pin(item, maximum=maximum)
        if sum(pin["bytes"] for pin in pins) + result["bytes"] > 4 * 1024**3:
            raise ValueError("deployment inspection exceeds its total original input bytes")
        check_budget()
        return result

    binary_schemas = {}
    for component in value["components"]:
        pins.extend(pinned(item) for item in component["inputs"])
        if component["binary"] is not None:
            binary = pinned(component["binary"], maximum=1024**3)
            pins.append(binary)
            if component["application_schema"] is not None:
                actual = subprocess.run(
                    [binary["path"], "--application-schema"],
                    capture_output=True,
                    timeout=min(10, max(0.001, deadline - time.monotonic())),
                    check=True,
                )
                check_budget()
                if (
                    len(actual.stdout) > 256
                    or actual.stdout.decode().strip() != component["application_schema"]
                ):
                    raise ValueError("actual binary application schema differs from its profile")
                binary_schemas[component["id"]] = component["application_schema"]
    pins.extend(pinned(item["file"]) for item in value["contracts"])
    ownership: list[dict[str, Any]] = []
    inodes: set[tuple[int, int]] = set()
    component_hosts = {row["id"]: row["host"] for row in value["components"]}
    for store in value["stores"]:
        if component_hosts[store["owner"]] != value["local_host"]:
            declared_path = Path(store["path"])
            if not declared_path.is_absolute() or ".." in declared_path.parts:
                raise ValueError("remote store requires a normalized declared absolute path")
            ownership.append(
                {
                    "id": store["id"],
                    "path": str(declared_path),
                    "owner": store["owner"],
                    "host": component_hosts[store["owner"]],
                    "observed": False,
                    "reason": "remote-store-not-inspected",
                    "actual_writer_lease_observed": False,
                }
            )
            continue
        path = real_path(store["path"], directory=store["kind"] == "directory")
        metadata = path.stat()
        if (metadata.st_dev, metadata.st_ino) in inodes:
            raise ValueError("store aliases conceal duplicate declared ownership")
        inodes.add((metadata.st_dev, metadata.st_ino))
        for previous in ownership:
            if not previous.get("observed", False):
                continue
            other = Path(previous["path"])
            if previous["owner"] != store["owner"] and (
                other in path.parents or path in other.parents
            ):
                raise ValueError("different writers declare overlapping store paths")
        ownership.append(
            {
                "id": store["id"],
                "path": str(path),
                "owner": store["owner"],
                "host": value["local_host"],
                "observed": True,
                "filesystem_device": metadata.st_dev,
                "inode": metadata.st_ino,
                "actual_writer_lease_observed": False,
            }
        )
    gates = []
    for criterion in value["criteria"]:
        evidence = criterion["evidence"]
        if evidence is not None:
            pins.append(pinned(evidence))
        gates.append(
            {
                "id": criterion["id"],
                "stage": criterion["stage"],
                "assurance": criterion["assurance"],
                "required": criterion["required"],
                "evidence_integrity_checked": evidence is not None,
                "semantic_stage_completion_asserted": False,
            }
        )
    links = {}
    for link in value["links"]:
        if link["tls"] is not None:
            pins.extend(pinned(link["tls"][key]) for key in ("ca", "certificate"))
            real_path(link["tls"]["key_file"])
            tls_context(link["tls"])
        links[link["id"]] = observe_link(link) if probe else {"observed": False}
        links[link["id"]]["observation_host"] = value["local_host"]
        links[link["id"]]["component_process_execution_proven"] = False
        check_budget()
    # Check original bytes once more after subprocess/network work; no changed source is frozen.
    for pin in pins:
        check_budget()
        file_pin({key: pin[key] for key in ("path", "sha256")}, maximum=1024**3)
    for original in ownership:
        if not original["observed"]:
            continue
        path = real_path(original["path"], directory=Path(original["path"]).is_dir())
        metadata = path.stat()
        if (metadata.st_dev, metadata.st_ino) != (original["filesystem_device"], original["inode"]):
            raise ValueError("original deployment store inode changed during inspection")
    check_budget()
    return {
        "schema": "roboguide.deployment-inspection/v0.1",
        "profile_digest": digest(canonical(value)),
        "profile_id": value["profile_id"],
        "revision": value["revision"],
        "declaration": value,
        "input_pins": pins,
        "binary_schemas": binary_schemas,
        "components_without_binary_pins": [
            row["id"] for row in value["components"] if row["binary"] is None
        ],
        "declared_storage_owners": ownership,
        "link_observations": links,
        "criteria": gates,
        "observation_host": value["local_host"],
        "actual_linux_boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "actual_hostname": socket.gethostname(),
        "observed_unix_ms": time.time_ns() // 1_000_000,
        "hardware_evidence_created": False,
        "services_started": 0,
        "nodes_registered": 0,
        "software_complete_asserted": False,
        "rto_rpo_achievement_asserted": False,
    }


def load(path: Path) -> object:
    """Parse a bounded exact profile while rejecting duplicate JSON keys and nonfinite constants."""
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("deployment profile exceeds its byte bound")

    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        """Preserve one unambiguous original declaration rather than overwriting duplicates."""
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate deployment JSON key")
            result[key] = value
        return result

    value = json.loads(path.read_bytes(), object_pairs_hook=pairs)
    canonical(value)
    return value


def main() -> None:
    """Inspect only explicit inputs and publish one private nonoverwriting observation receipt."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    receipt = inspect(load(args.profile), probe=args.probe)
    args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as target:
        target.write(canonical(receipt))
        target.flush()
        os.fsync(target.fileno())
    print(
        json.dumps(
            {
                "profile_digest": receipt["profile_digest"],
                "receipt_digest": digest(canonical(receipt)),
                "output": str(args.output),
                "software_complete_asserted": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
