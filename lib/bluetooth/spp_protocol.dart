import 'dart:convert';
import 'dart:typed_data';

/// SPP application frame magic values.
enum SppFrameType { audio, control }

class SppFrame {
  final SppFrameType type;
  final Uint8List payload;
  const SppFrame(this.type, this.payload);
}

/// RGAD/RGCT framing: 4-byte magic + uint32 little-endian length + payload.
class SppFramer {
  static const int headerBytes = 8;
  static const int maxPayloadBytes = 1024 * 1024;
  final BytesBuilder _buffer = BytesBuilder(copy: false);

  void add(Uint8List bytes) => _buffer.add(bytes);

  List<SppFrame> takeFrames() {
    final input = _buffer.toBytes();
    var offset = 0;
    final frames = <SppFrame>[];
    while (input.length - offset >= headerBytes) {
      final magic = utf8.decode(input.sublist(offset, offset + 4), allowMalformed: false);
      final length = ByteData.sublistView(input, offset + 4, offset + 8)
          .getUint32(0, Endian.little);
      if (length > maxPayloadBytes || (magic != 'RGAD' && magic != 'RGCT')) {
        // Resynchronise on the next known magic instead of losing the whole
        // stream after a Bluetooth fragment or a legacy/raw peer.
        final nextAudio = _indexOf(input, const [0x52, 0x47, 0x41, 0x44], offset + 1);
        final nextControl = _indexOf(input, const [0x52, 0x47, 0x43, 0x54], offset + 1);
        final candidates = [nextAudio, nextControl].where((i) => i >= 0).toList();
        if (candidates.isEmpty) {
          _buffer.clear();
          if (input.length - offset >= 3) {
            _buffer.add(input.sublist(input.length - 3));
          }
          return frames;
        }
        offset = candidates.reduce((a, b) => a < b ? a : b);
        continue;
      }
      final end = offset + headerBytes + length;
      if (input.length < end) break;
      final payload = Uint8List.fromList(input.sublist(offset + headerBytes, end));
      frames.add(SppFrame(magic == 'RGAD' ? SppFrameType.audio : SppFrameType.control, payload));
      offset = end;
    }
    _buffer.clear();
    if (offset < input.length) _buffer.add(input.sublist(offset));
    return frames;
  }

  static int _indexOf(Uint8List haystack, List<int> needle, int start) {
    for (var i = start; i <= haystack.length - needle.length; i++) {
      var match = true;
      for (var j = 0; j < needle.length; j++) {
        if (haystack[i + j] != needle[j]) {
          match = false;
          break;
        }
      }
      if (match) return i;
    }
    return -1;
  }

  static Uint8List encodeAudio(Uint8List payload) => _encode('RGAD', payload);

  static Uint8List encodeControl(Map<String, dynamic> value) =>
      _encode('RGCT', Uint8List.fromList(utf8.encode(jsonEncode(value))));

  static Uint8List _encode(String magic, Uint8List payload) {
    final output = Uint8List(headerBytes + payload.length);
    output.setRange(0, 4, ascii.encode(magic));
    ByteData.sublistView(output, 4, 8).setUint32(0, payload.length, Endian.little);
    output.setRange(headerBytes, output.length, payload);
    return output;
  }
}
