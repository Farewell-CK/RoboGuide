import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('validate_archive_media', Path(__file__).parents[1] / 'validate_archive_media.py')
media = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(media)


def box(kind, payload):
    return struct.pack('>I4s', len(payload) + 8, kind.encode()) + payload


def movie(nal_size=2, chunk_offset=None):
    full = b'\0' * 4
    ftyp = box('ftyp', b'isom\0\0\0\0isomavc1')
    sample = struct.pack('>I', nal_size) + b'\x65\x88'
    mdat = box('mdat', sample * 4)
    visual = bytearray(78)
    struct.pack_into('>HH', visual, 24, 640, 480)
    avcc = box('avcC', b'\x01\x64\x00\x1f\xff\xe0\x00')
    stsd = box('stsd', full + struct.pack('>I', 1) + box('avc1', visual + avcc))
    stts = box('stts', full + struct.pack('>III', 1, 4, 100))
    stsz = box('stsz', full + struct.pack('>II', 0, 4) + struct.pack('>IIII', *([len(sample)] * 4)))
    stsc = box('stsc', full + struct.pack('>IIII', 1, 1, 4, 1))
    stco = box('stco', full + struct.pack('>II', 1, len(ftyp) + 8 if chunk_offset is None else chunk_offset))
    stbl = box('stbl', stsd + stts + stsz + stsc + stco)
    mdhd = box('mdhd', full + struct.pack('>IIII', 0, 0, 1000, 400) + b'\0' * 4)
    hdlr = box('hdlr', full + b'\0' * 4 + b'vide' + b'\0' * 12)
    return ftyp + mdat + box('moov', box('trak', box('mdia', mdhd + hdlr + box('minf', stbl))))


class MediaValidationTest(unittest.TestCase):
    def test_real_sample_tables_and_capture_index_are_compared(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'clip.mp4'
            path.write_bytes(movie())
            frames = [{'frame_number': i * 2, 'capture_elapsed_ns': 1000000000 + i * 100000000, 'pts_us': i * 100000} for i in range(4)]
            result = media.video_info(path, {'start_elapsed_ns': 1000000000, 'frames': frames})
            self.assertEqual((640, 480), (result['width'], result['height']))
            self.assertEqual(4, result['sample_count'])
            self.assertEqual(10, result['average_fps'])
            self.assertTrue(result['index_count_matches_samples'])
            self.assertEqual(0, result['mp4_pts_index_max_error_us'])
            self.assertFalse(result['decoded_frames_verified'])

    def test_corrupt_nal_length_and_out_of_mdat_offset_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'clip.mp4'
            for data in [movie(nal_size=200), movie(chunk_offset=0)]:
                path.write_bytes(data)
                with self.assertRaises(media.MediaError):
                    media.video_info(path)

    def test_truncated_box_and_jpeg_are_rejected(self):
        with self.assertRaises(media.MediaError):
            media.boxes(struct.pack('>I4s', 100, b'moov'))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'image.jpg'
            path.write_bytes(b'\xff\xd8\xff\xc0\x00\x11\x08\x01\xe0\x02\x80\x03' + b'\0' * 9 + b'\xff\xd9')
            self.assertEqual(640, media.jpeg_info(path)['width'])
            path.write_bytes(b'\xff\xd8broken')
            with self.assertRaises(media.MediaError):
                media.jpeg_info(path)


if __name__ == '__main__':
    unittest.main()
