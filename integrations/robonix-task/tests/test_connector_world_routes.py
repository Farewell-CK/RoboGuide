"""Exercise optional World exports through the real combined loopback HTTP handler."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import threading
import types
import unittest
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch

from robonix_task.scene_targets import SCHEMA, SceneTargetObserver


def load_connector() -> Any:
    """Substitute deployment camera/sensor imports while retaining actual route composition."""
    camera = types.ModuleType("camera_adapter")
    camera.Captures = object  # type: ignore[attr-defined]
    camera.handler_type = lambda _captures: BaseHTTPRequestHandler  # type: ignore[attr-defined]
    observer = types.ModuleType("status_bridge")
    observer.Status = object  # type: ignore[attr-defined]
    observer.handler_type = lambda _observer: BaseHTTPRequestHandler  # type: ignore[attr-defined]
    artifacts = types.ModuleType("artifact_client")
    artifacts.upload_capture = lambda *_args, **_kwargs: None  # type: ignore[attr-defined]
    path = Path(__file__).resolve().parents[1] / "deploy/connector.py"
    spec = importlib.util.spec_from_file_location("connector_world_route_fixture", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with patch.dict(
        sys.modules,
        {"camera_adapter": camera, "status_bridge": observer, "artifact_client": artifacts},
    ):
        spec.loader.exec_module(module)
    return module


@contextmanager
def serve(handler: type[BaseHTTPRequestHandler]) -> Iterator[str]:
    """Own a real bounded loopback HTTP lifetime and drain handling before fixture release."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def read_json(origin: str, path: str) -> dict[str, Any]:
    """Read one fixed local fixture response without a system proxy or follow-up request."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(origin + path, timeout=3) as response:
        return dict(json.load(response))


class WorldRoutesTests(unittest.TestCase):
    """Check opt-in, source ownership, HTTP serialization and absence of sampling on GET."""

    def test_world_profiles_disabled_and_unknown_routes_refuse(self) -> None:
        """No World read is exposed without a configured owner, and queries cannot choose one."""
        connector = load_connector()
        with serve(connector.combined_handler(None, None)) as origin:
            for path in (
                "/v1/world/entity-reference",
                "/v1/world/physical-observation",
                "/v1/world/scene-targets",
                "/v1/world/scene-targets?entity=another-body",
                "/v1/world/entity-reference?entity=another-body",
                "/v1/world/unknown",
            ):
                with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as refused:
                    read_json(origin, path)
                self.assertEqual(refused.exception.code, 404)

    def test_opt_in_world_route_reads_only_the_existing_physical_cache(self) -> None:
        """Entity identity is explicit and physical GETs contain no Node/control payload."""
        connector = load_connector()

        class Observer:
            """Offer cached deployment facts with no tool, device or network implementation."""

            def read(self) -> dict[str, Any]:
                """Supply Node-only payload that the World projection must filter out."""
                return {"atlas": {"providers": ["private"]}, "resources": ["private"]}

        with serve(
            connector.combined_handler(None, Observer(), physical_entity="entity-fixture")
        ) as origin:
            self.assertEqual(
                read_json(origin, "/v1/world/entity-reference"), {"entity_id": "entity-fixture"}
            )
            report = read_json(origin, "/v1/world/physical-observation")
            self.assertEqual(report["physical_entity_id"], "entity-fixture")
            self.assertNotIn("atlas", report)
            self.assertNotIn("resources", report)
            self.assertIsNone(report["posture"]["reported_posture"])
            self.assertFalse(report["device_stop_verified"])

    def test_scene_route_serves_only_the_selected_cached_observer(self) -> None:
        """A fixed GET reads only the existing cache without invoking Scene or a sampler."""
        connector = load_connector()

        class SceneCache:
            """Offer only an unknown source; no sample or source network method exists."""

            def read(self) -> dict[str, Any]:
                """Keep source unavailability explicit rather than inventing target evidence."""
                return {"availability": "unknown", "definition_json": None}

        with serve(connector.combined_handler(None, None, scene_targets=SceneCache())) as origin:
            self.assertEqual(
                read_json(origin, "/v1/world/scene-targets"),
                {"availability": "unknown", "definition_json": None},
            )
            with self.assertRaises(urllib.error.HTTPError) as refused:
                read_json(origin, "/v1/world/scene-targets?entity=another-body")
            self.assertEqual(refused.exception.code, 404)

    def test_scene_producer_connector_json_projection_and_mi_retain_original_definition(
        self,
    ) -> None:
        """Real producer/Connector/MI IO preserves bytes through a fixture Controller envelope.

        Only the Controller State storage is a fixture, with its explicit same-domain clocks.
        It does not prove Core admission or hardware localization; those remain separate gates.
        """
        from mission.grounding_reader import HttpMissionGroundingReader
        from mission.request_record import DialogueSpeaker, DialogueTurn, DialogueTurnKind

        documents = {
            "/api/state": {
                "map_binding": {
                    "map_id": "fixture-map",
                    "mode": None,
                    "generation": None,
                    "source": "synthetic-loopback",
                }
            },
            "/api/regions": {
                "ok": True,
                "annotations": [
                    {
                        "annotation_id": "fixture-region",
                        "kind": "region",
                        "name": "fixture destination",
                        "points": [[0.0, 0.0], [1.0000000000000002, 0.0], [1.0, 1.0]],
                        "stale": False,
                    }
                ],
            },
        }
        source_calls: list[str] = []

        class SourceHandler(BaseHTTPRequestHandler):
            """Serve original synthetic Scene JSON and track its three bounded source reads."""

            def do_GET(self) -> None:
                """Return only the requested fixed source document as finite original bytes."""
                source_calls.append(self.path)
                body = json.dumps(documents[self.path], ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                """Leave request observations in the explicit fixture list only."""

        connector = load_connector()
        with serve(SourceHandler) as source_origin:

            def read_source(path: str, timeout: float) -> bytes:
                """Substitute only source address while using actual finite local HTTP reads."""
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(source_origin + path, timeout=timeout) as response:
                    return bytes(response.read())

            observer = SceneTargetObserver("entity-fixture", "node-fixture", reader=read_source)
            observer.sample()
            with serve(connector.combined_handler(None, None, scene_targets=observer)) as origin:
                published = read_json(origin, "/v1/world/scene-targets")
            original = published["definition_json"].encode("utf-8")
            self.assertEqual(published["availability"], "current")
            self.assertEqual(
                published["region_source_digest"], "sha256:" + hashlib.sha256(original).hexdigest()
            )
            self.assertEqual(source_calls, ["/api/state", "/api/regions", "/api/state"])

        records = [
            {
                "object": {
                    "class": "world",
                    "object_type": "scene-targets",
                    "object_id": "entity-fixture",
                },
                "semantic": "reported",
                "source": "node:node-fixture/physical-observer",
                "channel_id": "scene-targets",
                "value": {
                    "payload_schema": SCHEMA,
                    "value": published,
                    "source_observed_at_ms": None,
                    "received_at_ms": 100,
                    "valid_for_ms": 5000,
                    "confidence_millionths": None,
                    "source_epoch": "fixture-session",
                    "sequence": 1,
                },
                "stale": False,
            }
        ]

        class ProjectionHandler(BaseHTTPRequestHandler):
            """Model the State envelope only; both JSON and MI transport remain actual HTTP."""

            def do_GET(self) -> None:
                """Serve finite explicit clocks and retain original source bodies inside JSON."""
                if self.path == "/v1/state/records?object_class=world":
                    value = {
                        "schema": "roboguide.state-query/v0.1",
                        "observed_at_ms": 110,
                        "records": records,
                    }
                else:
                    assert self.path == "/v1/memories"
                    value = {"schema": "roboguide.memory-catalog/v0.1", "memories": []}
                body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                """Keep expected fixture requests out of normal test output."""

        dialogue = (
            DialogueTurn(
                "fixture-turn", DialogueSpeaker.USER, DialogueTurnKind.INSTRUCTION, "inspect", 1
            ),
        )
        with serve(ProjectionHandler) as origin:
            reader = HttpMissionGroundingReader(
                origin, origin, 2, 16, 16, admitted_world_payload_schemas=frozenset({SCHEMA})
            )
            frozen = reader.capture("fixture-request", dialogue, 200)
            self.assertEqual(len(frozen.state_evidence), 1, frozen.gaps)
            content = frozen.state_evidence[0].value
            self.assertEqual(content["definition_json"].encode("utf-8"), original)
            self.assertEqual(content["region_source_digest"], published["region_source_digest"])
            self.assertFalse(content["localization_verified"])
            self.assertFalse(content["arrival_verified"])
            self.assertEqual(source_calls, ["/api/state", "/api/regions", "/api/state"])
            # A fixture tampering with the original string must remain a rejected source.
            records[0]["value"]["value"] = {**published, "definition_json": "{}"}
            refused = reader.capture("fixture-request", dialogue, 201)
            self.assertEqual(refused.state_evidence, ())
            self.assertTrue(any(gap.code == "state_record_rejected" for gap in refused.gaps))


if __name__ == "__main__":
    unittest.main()
