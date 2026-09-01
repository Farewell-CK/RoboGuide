# -*- coding: utf-8 -*-
# Generated protocol buffer stub for the `robonix/primitive/map/*`
# contracts served by the `lite3_map_browser` primitive on the robot.
#
# This file is hand-written: the robonix-client repository does not commit
# `protoc` to the dev environment, and the robonix side regenerates the
# matching stub via `rbnx codegen` on every `rbnx build`. The shape mirrors
# `map_browser.proto`; if the robonix-side codegen drifts, regenerate this
# file with:
#
#   protoc --proto_path=src/robonix_client/proto \
#           --python_out=src/robonix_client/proto \
#           src/robonix_client/proto/map_browser.proto
#
# and replace the runtime-constructed stub below with the protoc output.
from __future__ import annotations

from google.protobuf import descriptor_pool as _descriptor_pool
from google.protobuf import message_factory as _message_factory
from google.protobuf import symbol_database as _symbol_database

_sym_db = _symbol_database.Default()


# ── Runtime FileDescriptorProto construction ─────────────────────────────────
# Building the FileDescriptorProto in Python lets us avoid running protoc
# during install. The serialized form is then handed to DescriptorPool,
# which produces the same Message classes protoc would have emitted.
def _build_file_descriptor() -> bytes:
    from google.protobuf import descriptor_pb2

    fp = descriptor_pb2.FileDescriptorProto()
    fp.name = "map_browser.proto"
    fp.package = "robonix.primitive.map"
    fp.syntax = "proto3"

    def _add_msg(name, fields):
        msg = fp.message_type.add()
        msg.name = name
        for fname, number, ftype in fields:
            f = msg.field.add()
            f.name = fname
            f.number = number
            f.type = ftype
            f.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
            f.json_name = fname

    STRING = descriptor_pb2.FieldDescriptorProto.TYPE_STRING
    BYTES = descriptor_pb2.FieldDescriptorProto.TYPE_BYTES

    _add_msg("ListMaps_Request", [])
    _add_msg("ListMaps_Response", [("data", 1, STRING)])

    _add_msg("GetMap_Request", [("name", 1, STRING)])
    _add_msg(
        "GetMap_Response",
        [("name", 1, STRING), ("data", 2, BYTES)],
    )

    _add_msg("PushMap_Request", [("name", 1, STRING), ("data", 2, BYTES)])
    _add_msg("PushMap_Response", [("data", 1, STRING)])

    _add_msg("DeleteMap_Request", [("name", 1, STRING)])
    _add_msg("DeleteMap_Response", [("data", 1, STRING)])

    return fp.SerializeToString()


_pool = _descriptor_pool.DescriptorPool()
_pool.AddSerializedFile(_build_file_descriptor())


def _message(name: str):
    return _message_factory.GetMessageClass(
        _pool.FindMessageTypeByName(name)
    )


ListMaps_Request = _message("robonix.primitive.map.ListMaps_Request")
ListMaps_Response = _message("robonix.primitive.map.ListMaps_Response")
GetMap_Request = _message("robonix.primitive.map.GetMap_Request")
GetMap_Response = _message("robonix.primitive.map.GetMap_Response")
PushMap_Request = _message("robonix.primitive.map.PushMap_Request")
PushMap_Response = _message("robonix.primitive.map.PushMap_Response")
DeleteMap_Request = _message("robonix.primitive.map.DeleteMap_Request")
DeleteMap_Response = _message("robonix.primitive.map.DeleteMap_Response")

for _msg in (
    ListMaps_Request,
    ListMaps_Response,
    GetMap_Request,
    GetMap_Response,
    PushMap_Request,
    PushMap_Response,
    DeleteMap_Request,
    DeleteMap_Response,
):
    _sym_db.RegisterMessage(_msg)


# gRPC method paths derived from the robonix contract naming convention
# (see audio_pb2 / executor_pb2): "/<package>.<Service>/<Method>". The
# robonix side registers the handler at the same path; if a future
# robonix codegen version changes the casing, adjust these constants.
GRPC_SERVICE_NAME = "robonix.primitive.map.Lite3MapBrowser"
GRPC_METHOD_LIST_MAPS = f"/{GRPC_SERVICE_NAME}/ListMaps"
GRPC_METHOD_GET_MAP = f"/{GRPC_SERVICE_NAME}/GetMap"
GRPC_METHOD_PUSH_MAP = f"/{GRPC_SERVICE_NAME}/PushMap"
GRPC_METHOD_DELETE_MAP = f"/{GRPC_SERVICE_NAME}/DeleteMap"


if __name__ == "__main__":
    # Smoke test: instantiate all messages and round-trip a payload so
    # `python -m robonix_client.proto.map_browser_pb2` can verify the
    # stub loads in any environment.
    get_resp = GetMap_Response()
    get_resp.name = "rtabmap.db"
    get_resp.data = b"\x00\x01\x02hello"
    wire = get_resp.SerializeToString()
    decoded = GetMap_Response()
    decoded.ParseFromString(wire)
    assert decoded.name == "rtabmap.db", decoded.name
    assert decoded.data == b"\x00\x01\x02hello", decoded.data
    print(
        "map_browser_pb2 OK:",
        GRPC_METHOD_LIST_MAPS,
        GRPC_METHOD_GET_MAP,
        GRPC_METHOD_PUSH_MAP,
        GRPC_METHOD_DELETE_MAP,
    )
