#!/usr/bin/env python3
"""Read-only structural validation of recorded MP4, JPEG and lossless Z16 archive files.

Uses Python stdlib. H.264 NAL structure and MP4 sample tables are checked; this is
not a video decoder or a visual-quality assessment. No image content is rendered.
"""
from __future__ import annotations
import argparse
from array import array
from collections import Counter, defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import statistics
import struct
import sys


class MediaError(ValueError):
    pass


def u32(data, offset):
    return struct.unpack_from('>I', data, offset)[0]


def boxes(data, start=0, end=None):
    end = len(data) if end is None else end
    offset = start
    result = []
    while offset < end:
        if end - offset < 8:
            raise MediaError('Truncated MP4 box header')
        size, kind = struct.unpack_from('>I4s', data, offset)
        header = 8
        if size == 1:
            if end - offset < 16:
                raise MediaError('Truncated extended MP4 box header')
            size = struct.unpack_from('>Q', data, offset + 8)[0]
            header = 16
        elif size == 0:
            size = end - offset
        if size < header or offset + size > end:
            raise MediaError('MP4 box size outside parent')
        result.append((kind.decode('ascii', errors='replace'), offset, offset + header, offset + size))
        offset += size
    return result


def child(data, parent, kind):
    found = [box for box in boxes(data, parent[2], parent[3]) if box[0] == kind]
    if len(found) != 1:
        raise MediaError(f'Expected one {kind} box, found {len(found)}')
    return found[0]


def duration_fields(data, box):
    p = box[2]
    version = data[p]
    if version == 0:
        return u32(data, p + 12), u32(data, p + 16)
    if version == 1:
        return u32(data, p + 20), struct.unpack_from('>Q', data, p + 24)[0]
    raise MediaError('Unsupported media header version')


def table(data, box, fields, signed_last=False):
    count = u32(data, box[2] + 4)
    size = fields * 4
    if box[2] + 8 + count * size != box[3]:
        raise MediaError(f'Malformed {box[0]} table')
    fmt = '>' + ('I' * (fields - 1)) + ('i' if signed_last else 'I')
    return list(struct.iter_unpack(fmt, data[box[2] + 8:box[3]]))


def video_info(path: Path, event_metadata=None):
    data = path.read_bytes()
    top = boxes(data)
    moov = next((b for b in top if b[0] == 'moov'), None)
    if not moov or not any(b[0] == 'mdat' for b in top):
        raise MediaError('MP4 lacks sealed moov/mdat')
    tracks = [b for b in boxes(data, moov[2], moov[3]) if b[0] == 'trak']
    video = []
    audio_count = 0
    for track in tracks:
        mdia = child(data, track, 'mdia')
        hdlr = child(data, mdia, 'hdlr')
        handler = data[hdlr[2] + 8:hdlr[2] + 12]
        if handler == b'vide':
            video.append((track, mdia))
        elif handler == b'soun':
            audio_count += 1
    if len(video) != 1:
        raise MediaError('Expected exactly one video track')
    track, mdia = video[0]
    scale, duration = duration_fields(data, child(data, mdia, 'mdhd'))
    if not scale or not duration:
        raise MediaError('Invalid video duration/timescale')
    stbl = child(data, child(data, mdia, 'minf'), 'stbl')
    stsd = child(data, stbl, 'stsd')
    if u32(data, stsd[2] + 4) != 1:
        raise MediaError('Multiple sample descriptions unsupported')
    entries = boxes(data, stsd[2] + 8, stsd[3])
    if len(entries) != 1 or entries[0][0] not in {'avc1', 'avc3'}:
        raise MediaError('Expected H.264 AVC visual sample entry')
    entry = entries[0]
    width, height = struct.unpack_from('>HH', data, entry[2] + 24)
    configs = [b for b in boxes(data, entry[2] + 78, entry[3]) if b[0] == 'avcC']
    if len(configs) != 1:
        raise MediaError('Missing AVCDecoderConfigurationRecord')
    config = data[configs[0][2]:configs[0][3]]
    if len(config) < 7 or config[0] != 1:
        raise MediaError('Invalid AVC configuration')
    nal_length = (config[4] & 3) + 1
    stsz = child(data, stbl, 'stsz')
    fixed, sample_count = struct.unpack_from('>II', data, stsz[2] + 4)
    if fixed:
        sizes = [fixed] * sample_count
    else:
        if stsz[2] + 12 + 4 * sample_count != stsz[3]:
            raise MediaError('Invalid stsz table')
        sizes = [r[0] for r in struct.iter_unpack('>I', data[stsz[2] + 12:stsz[3]])]
    timing = table(data, child(data, stbl, 'stts'), 2)
    if sum(n for n, _ in timing) != sample_count:
        raise MediaError('Sample time/count mismatch')
    dts, cursor = [], 0
    for count, step in timing:
        if step <= 0:
            raise MediaError('Non-increasing decode timestamps')
        for _ in range(count):
            dts.append(cursor)
            cursor += step
    ctts = [b for b in boxes(data, stbl[2], stbl[3]) if b[0] == 'ctts']
    offsets = []
    if ctts:
        for count, offset in table(data, ctts[0], 2, signed_last=data[ctts[0][2]] == 1):
            offsets.extend([offset] * count)
        if len(offsets) != sample_count:
            raise MediaError('Composition time/count mismatch')
    else:
        offsets = [0] * sample_count
    pts = [t + o for t, o in zip(dts, offsets)]
    stsc = table(data, child(data, stbl, 'stsc'), 3)
    chunk_boxes = [b for b in boxes(data, stbl[2], stbl[3]) if b[0] in {'stco', 'co64'}]
    if len(chunk_boxes) != 1 or not stsc:
        raise MediaError('Missing sample-to-chunk mapping')
    chunks = chunk_boxes[0]
    count = u32(data, chunks[2] + 4)
    fmt = '>Q' if chunks[0] == 'co64' else '>I'
    chunk_offsets = [r[0] for r in struct.iter_unpack(fmt, data[chunks[2] + 8:chunks[3]])]
    if len(chunk_offsets) != count:
        raise MediaError('Invalid chunk offset table')
    mdat_ranges = [(b[2], b[3]) for b in top if b[0] == 'mdat']
    nal_types = Counter()
    sample_index = 0
    sample_ranges = []
    rule = 0
    for chunk_num, file_offset in enumerate(chunk_offsets, 1):
        while rule + 1 < len(stsc) and stsc[rule + 1][0] <= chunk_num:
            rule += 1
        first, per_chunk, description = stsc[rule]
        if first > chunk_num or description != 1:
            raise MediaError('Invalid sample-to-chunk entry')
        for _ in range(per_chunk):
            if sample_index >= sample_count:
                raise MediaError('More mapped samples than declared')
            length = sizes[sample_index]
            stop = file_offset + length
            if not any(start <= file_offset <= stop <= end for start, end in mdat_ranges):
                raise MediaError('AVC sample points outside mdat')
            sample_ranges.append((file_offset, stop))
            position = file_offset
            has_slice = False
            while position < stop:
                if stop - position < nal_length:
                    raise MediaError('Truncated AVC NAL length prefix')
                n = int.from_bytes(data[position:position + nal_length], 'big')
                position += nal_length
                if n < 1 or position + n > stop:
                    raise MediaError('NAL size leaves AVC sample')
                header = data[position]
                if header & 0x80:
                    raise MediaError('AVC forbidden_zero_bit set')
                kind = header & 0x1f
                nal_types[kind] += 1
                has_slice |= kind in {1, 5}
                position += n
            if not has_slice:
                raise MediaError('Video sample contains no slice NAL')
            file_offset = stop
            sample_index += 1
    if sample_index != sample_count:
        raise MediaError('Unmapped video samples')
    ordered = sorted(sample_ranges)
    if any(a[1] > b[0] for a, b in zip(ordered, ordered[1:])):
        raise MediaError('Overlapping AVC sample byte ranges')
    seconds = duration / scale
    result = {
        'path': str(path), 'file_bytes': len(data), 'codec': entries[0][0],
        'avc_profile_idc': config[1], 'avc_level_idc': config[3],
        'width': width, 'height': height, 'duration_seconds': seconds,
        'sample_count': sample_count, 'average_fps': sample_count / seconds,
        'file_bitrate_mbps': len(data) * 8 / seconds / 1e6,
        'coded_video_bitrate_mbps': sum(sizes) * 8 / seconds / 1e6,
        'pts_monotonic': all(b > a for a, b in zip(pts, pts[1:])),
        'sample_bytes_total': sum(sizes), 'nal_types': dict(sorted(nal_types.items())),
        'nal_structure_valid': True, 'decoded_frames_verified': False,
        'audio_track_count': audio_count,
    }
    metadata = event_metadata or {}
    frames = metadata.get('frames')
    if frames:
        result['indexed_frame_count'] = len(frames)
        result['index_count_matches_samples'] = len(frames) == sample_count
        result['capture_ns_monotonic'] = all(b['capture_elapsed_ns'] > a['capture_elapsed_ns'] for a, b in zip(frames, frames[1:]))
        result['frame_number_monotonic'] = all(b['frame_number'] > a['frame_number'] for a, b in zip(frames, frames[1:]))
        result['capture_pts_max_error_us'] = max(abs((f['capture_elapsed_ns'] - metadata['start_elapsed_ns']) / 1000 - f['pts_us']) for f in frames)
        if len(frames) == len(pts):
            # MP4 media timescale can round integer microsecond input to its tick.
            result['mp4_pts_index_max_error_us'] = max(abs(t / scale * 1e6 - f['pts_us']) for t, f in zip(pts, frames))
        intervals = [(b['capture_elapsed_ns'] - a['capture_elapsed_ns']) / 1e6 for a, b in zip(frames, frames[1:])]
        result['capture_interval_ms'] = {'min': min(intervals), 'median': statistics.median(intervals), 'max': max(intervals)}
    return result


def jpeg_info(path: Path):
    data = path.read_bytes()
    if not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'):
        raise MediaError('JPEG lacks SOI/EOI')
    p = 2
    while p < len(data):
        if data[p] != 0xff:
            raise MediaError('JPEG marker boundary mismatch')
        while p < len(data) and data[p] == 0xff:
            p += 1
        marker = data[p]
        p += 1
        if marker in {0xd8, 0xd9} or 0xd0 <= marker <= 0xd7:
            continue
        length = int.from_bytes(data[p:p + 2], 'big')
        if length < 2 or p + length > len(data):
            raise MediaError('Invalid JPEG segment')
        if marker in {0xc0, 0xc1, 0xc2}:
            precision, height, width, components = struct.unpack_from('>BHHB', data, p + 2)
            return {'width': width, 'height': height, 'precision_bits': precision, 'components': components, 'file_bytes': len(data), 'sof_marker': marker}
        if marker == 0xda:
            break
        p += length
    raise MediaError('JPEG dimension SOF not found')


def depth_info(path: Path, meta):
    raw = gzip.decompress(path.read_bytes())
    width, height, stride = (int(meta[k]) for k in ['width', 'height', 'stride_bytes'])
    unit = float(meta['depth_unit_m'])
    if stride < width * 2 or len(raw) != stride * height:
        raise MediaError('Z16 shape/stride mismatch')
    values = array('H')
    for y in range(height):
        row = array('H')
        row.frombytes(raw[y * stride:y * stride + width * 2])
        if sys.byteorder != 'little':
            row.byteswap()
        values.extend(row)
    valid = [v for v in values if v]
    result = {'width': width, 'height': height, 'stride_bytes': stride, 'depth_unit_m': unit,
              'uncompressed_bytes': len(raw), 'compressed_bytes': path.stat().st_size,
              'compressed_ratio': path.stat().st_size / len(raw),
              'valid_pixels': len(valid), 'valid_ratio': len(valid) / len(values),
              'gzip_crc_valid': True, 'decoded_uint16_exact_bytes': True}
    if valid:
        result.update({'min_valid_m': min(valid) * unit, 'median_valid_m': statistics.median(valid) * unit, 'max_valid_m': max(valid) * unit})
    return result


def validate(root: Path):
    manifest = json.loads((root / 'manifest.json').read_text())
    events = []
    torn = 0
    event_paths = sorted(root.glob('events*.jsonl'))
    for path in event_paths:
        for line in path.open():
            try:
                events.append(json.loads(line))
            except ValueError:
                torn += 1
    metadata = {r['data']['path']: r['data']['metadata'] for r in events if r.get('type') == 'artifact'}
    files = manifest['files']
    sha_errors = []
    for entry in files:
        path = root / entry['path']
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if path.stat().st_size != entry['size'] or actual != entry['sha256']:
            sha_errors.append(entry['path'])
    videos, depths, images, failures = [], [], [], []
    groups = defaultdict(dict)
    for entry in files:
        kind, relative = entry['type'], entry['path']
        meta = {**entry.get('metadata', {}), **metadata.get(relative, {})}
        try:
            if kind == 'navigation_video':
                info = video_info(root / relative, meta)
                info['path'] = relative
                videos.append(info)
            elif kind in {'inference_raw_depth', 'inference_aligned_depth'}:
                info = depth_info(root / relative, meta)
                info.update({'path': relative, 'type': kind, 'inference_id': meta.get('inference_id'), 'capture': meta.get('capture')})
                depths.append(info)
                groups[meta.get('inference_id')][kind] = info
            elif kind == 'inference_rgb':
                info = jpeg_info(root / relative)
                info.update({'path': relative, 'inference_id': meta.get('inference_id'), 'capture': meta.get('capture')})
                images.append(info)
                groups[meta.get('inference_id')][kind] = info
            elif kind in {'semantic_labels', 'semantic_confidence'}:
                groups[meta.get('inference_id')][kind] = {'path': relative, 'metadata': meta}
        except Exception as error:
            failures.append({'path': relative, 'error': str(error)})
    pairs = []
    for inference, group in groups.items():
        available = sorted(group)
        captures = [v['capture'] for v in group.values() if v.get('capture')]
        pair = {'inference_id': inference, 'available_types': available,
                'input_triplet_present': all(t in group for t in ['inference_rgb', 'inference_raw_depth', 'inference_aligned_depth']),
                'semantic_result_present': all(t in group for t in ['semantic_labels', 'semantic_confidence'])}
        if captures:
            pair.update({'input_capture_metadata_identical': all(c == captures[0] for c in captures),
                         'pair_skew_ms': captures[0].get('pair_skew_ms'),
                         'same_time_domain': captures[0].get('rgb_time_domain') == captures[0].get('depth_time_domain'),
                         'calibration_present': all(k in captures[0] for k in ['color_intrinsic', 'raw_depth_intrinsic', 'depth_to_color_rotation_column_major', 'depth_to_color_translation_m'])})
        pairs.append(pair)
    video_seconds = sum(v['duration_seconds'] for v in videos)
    video_bytes = sum(v['file_bytes'] for v in videos)
    raw_depth = [d for d in depths if d['type'] == 'inference_raw_depth']
    aligned_depth = [d for d in depths if d['type'] == 'inference_aligned_depth']
    aggregate = {
        'video_files': len(videos), 'video_seconds': video_seconds, 'video_bytes': video_bytes,
        'video_average_fps': sum(v['sample_count'] for v in videos) / video_seconds if video_seconds else None,
        'video_file_bitrate_mbps': video_bytes * 8 / video_seconds / 1e6 if video_seconds else None,
        'rgb_keyframes': len(images), 'raw_depth_frames': len(raw_depth), 'aligned_depth_frames': len(aligned_depth),
        'raw_depth_compressed_bytes': sum(d['compressed_bytes'] for d in raw_depth),
        'aligned_depth_compressed_bytes': sum(d['compressed_bytes'] for d in aligned_depth),
        'depth_pairs_compressed_bytes': sum(d['compressed_bytes'] for d in depths),
        'input_triplets': sum(p['input_triplet_present'] for p in pairs),
        'triplets_with_semantic_result': sum(p['input_triplet_present'] and p['semantic_result_present'] for p in pairs),
        'codec_profiles': sorted({v['avc_profile_idc'] for v in videos}),
        'codec_levels': sorted({v['avc_level_idc'] for v in videos}),
    }
    return {'session_id': manifest['session_id'], 'source': str(root), 'source_state': manifest['state'],
            'scope': 'Real D455 phone capture copied while recording; only files in this snapshot manifest are validated. Not final task coverage.',
            'verified_manifest_files': len(files), 'manifest_hash_or_size_errors': sha_errors,
            'events_scanned': len(events), 'event_json_parse_failures': torn,
            'video_decoder_available': False, 'video_validation': 'MP4 box/sample timing and H.264 NAL structure only; no frame decode or visual-quality claim.',
            'videos': videos, 'depths': depths, 'rgb_keyframes': images, 'inference_pairing': pairs, 'media_errors': failures,
            'aggregate': aggregate}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--json-output', type=Path)
    args = parser.parse_args()
    report = validate(args.archive)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_output:
        args.json_output.write_text(output + '\n')
    else:
        print(output)
    return int(bool(report['manifest_hash_or_size_errors'] or report['media_errors']))


if __name__ == '__main__':
    raise SystemExit(main())
