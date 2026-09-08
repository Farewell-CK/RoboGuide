import 'dart:convert';
import 'dart:typed_data';

/// Hand-rolled protobuf codec for the 4 Liaison messages RoboGuide needs.
/// The message surface is all-scalar and tiny; a generator toolchain
/// (protoc/protoc_plugin) is unnecessary. Wire shapes verified against
/// robonix-client-android proto definitions (liaison.proto / pilot.proto).
///
/// proto3 rules applied: default values (empty string, 0, false) are not
/// serialized, matching the official client's builder behavior.

// ── writer ──────────────────────────────────────────────────────────────

class _PbWriter {
  final _out = BytesBuilder();

  void _varint(int v) {
    var value = v;
    while (true) {
      if (value <= 0x7f) {
        _out.addByte(value);
        return;
      }
      _out.addByte((value & 0x7f) | 0x80);
      value >>= 7;
    }
  }

  void _tag(int field, int wire) => _varint((field << 3) | wire);

  void _lengthDelimited(int field, List<int> bytes) {
    _tag(field, 2);
    _varint(bytes.length);
    _out.add(bytes);
  }

  void string(int field, String value) {
    if (value.isEmpty) return;
    _lengthDelimited(field, utf8.encode(value));
  }

  void uint32(int field, int value) {
    if (value == 0) return;
    _tag(field, 0);
    _varint(value);
  }

  void boolean(int field, bool value) {
    if (!value) return;
    _tag(field, 0);
    _out.addByte(1);
  }

  Uint8List take() => _out.takeBytes();
}

/// Encode StartVoiceSession_Request (liaison.proto fields 1..5, 11).
Uint8List encodeStartVoiceSessionRequest({
  required String sessionId,
  required String clientUserId,
  int recordSeconds = 30,
  String language = 'zh',
  bool ttsEnabled = true,
  String contextJson = '',
}) {
  final w = _PbWriter()
    ..string(1, sessionId)
    ..string(2, clientUserId)
    ..uint32(3, recordSeconds)
    ..string(4, language)
    ..boolean(5, ttsEnabled)
    ..string(11, contextJson);
  return w.take();
}

/// Encode FinishVoiceCapture_Request (liaison.proto field 1).
Uint8List encodeFinishVoiceCaptureRequest(String sessionId) {
  final w = _PbWriter()..string(1, sessionId);
  return w.take();
}

// ── reader ──────────────────────────────────────────────────────────────

class _PbReader {
  final Uint8List _data;
  int _offset = 0;

  _PbReader(this._data);

  bool get atEnd => _offset >= _data.length;

  /// (field, wireType); wire 0=varint, 2=length-delimited.
  (int, int) readTag() {
    final key = _readVarint();
    return (key >> 3, key & 0x7);
  }

  int readVarint() => _readVarint();

  int _readVarint() {
    var result = 0;
    var shift = 0;
    while (true) {
      if (_offset >= _data.length) {
        throw const FormatException('varint truncated');
      }
      final b = _data[_offset++];
      result |= (b & 0x7f) << shift;
      if (b & 0x80 == 0) return result;
      shift += 7;
    }
  }

  Uint8List readBytes() {
    final len = _readVarint();
    if (_offset + len > _data.length) {
      throw const FormatException('length-delimited field truncated');
    }
    final out = Uint8List.sublistView(_data, _offset, _offset + len);
    _offset += len;
    return out;
  }

  String readString() => utf8.decode(readBytes());
}

// ── decoded models ──────────────────────────────────────────────────────

class DecodedVoiceEvent {
  final int eventKind;
  final String sessionId;
  final String text;
  final String userId;
  final String error;
  final String statusMessage;

  const DecodedVoiceEvent({
    required this.eventKind,
    this.sessionId = '',
    this.text = '',
    this.userId = '',
    this.error = '',
    this.statusMessage = '',
  });
}

class FinishResult {
  final bool ok;
  final String detail;
  const FinishResult(this.ok, this.detail);
}

/// Decode a streamed StartVoiceSession response item.
///
/// Deployed Liaison builds stream BARE VoiceEvent messages (field 1 = varint
/// event_kind), while the proto declares StartVoiceSession_Response wrappers
/// (field 1 = length-delimited event). Distinguish by the wire type of
/// field 1 — same fallback the official Android client applies.
DecodedVoiceEvent decodeVoiceEventResponse(Uint8List bytes) {
  final r = _PbReader(bytes);
  while (!r.atEnd) {
    final (field, wire) = r.readTag();
    if (field == 1 && wire == 2) {
      return _decodeVoiceEvent(r.readBytes());
    }
    if (field == 1 && wire == 0) {
      // bare VoiceEvent stream — restart parse from the beginning
      return _decodeVoiceEvent(bytes);
    }
    _skip(r, wire);
  }
  return const DecodedVoiceEvent(eventKind: -1);
}

DecodedVoiceEvent _decodeVoiceEvent(Uint8List bytes) {
  final r = _PbReader(bytes);
  var kind = -1;
  var sessionId = '';
  var text = '';
  var userId = '';
  var error = '';
  var status = '';
  while (!r.atEnd) {
    final (field, wire) = r.readTag();
    switch (field) {
      case 1:
        if (wire == 0) kind = r.readVarint();
      case 2:
        sessionId = r.readString();
      case 3:
        text = r.readString();
      case 4:
        userId = r.readString();
      case 6:
        final pilot = _decodePilotText(r.readBytes());
        if (pilot.isNotEmpty) text = pilot;
      case 7:
        error = r.readString();
      case 8:
        status = r.readString();
      default:
        _skip(r, wire);
    }
  }
  return DecodedVoiceEvent(
    eventKind: kind,
    sessionId: sessionId,
    text: text,
    userId: userId,
    error: error,
    statusMessage: status,
  );
}

/// PilotEvent fields we consume: 3 = text_chunk, 7 = final_text.
/// final_text wins (mirrors thor_spp_server._on_voice_event).
String _decodePilotText(Uint8List bytes) {
  final r = _PbReader(bytes);
  var chunk = '';
  var finalText = '';
  while (!r.atEnd) {
    final (field, wire) = r.readTag();
    if (field == 3 && wire == 2) {
      chunk = r.readString();
    } else if (field == 7 && wire == 2) {
      finalText = r.readString();
    } else {
      _skip(r, wire);
    }
  }
  return finalText.isNotEmpty ? finalText : chunk;
}

/// Decode FinishVoiceCapture_Response: 1 = ok (bool), 3 = detail (string).
FinishResult decodeFinishVoiceCaptureResponse(Uint8List bytes) {
  final r = _PbReader(bytes);
  var ok = false;
  var detail = '';
  while (!r.atEnd) {
    final (field, wire) = r.readTag();
    switch (field) {
      case 1:
        if (wire == 0) ok = r.readVarint() != 0;
      case 3:
        detail = r.readString();
      default:
        _skip(r, wire);
    }
  }
  return FinishResult(ok, detail);
}

void _skip(_PbReader r, int wire) {
  switch (wire) {
    case 0:
      r.readVarint();
    case 2:
      r.readBytes();
    case 5:
      r.readBytes(); // fixed32 — length checked inside
    default:
      throw FormatException('unsupported wire type $wire');
  }
}
