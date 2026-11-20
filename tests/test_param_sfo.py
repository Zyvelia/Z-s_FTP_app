import hashlib
import unittest

from test_remote_management import MemoryBackend
from zftp.services.param_sfo import HEADER, INDEX, INTEGER, TEXT, UINT32, ParamSfo
from zftp.services.remote_management import RemoteConflictError, safe_save_remote_bytes


def fixture():
    keys = b'TITLE\0ATTRIBUTE\0OPAQUE\0'
    index = b''.join([
        INDEX.pack(0, TEXT, 5, 16, 0),
        INDEX.pack(6, INTEGER, 4, 4, 16),
        INDEX.pack(16, 0x0004, 4, 4, 20),
    ])
    data = b'Test\0' + b'\x00' * 11 + UINT32.pack(7) + b'\x00\xff\xfe\x00'
    key_offset = HEADER.size + len(index)
    return HEADER.pack(b'\0PSF', 0x101, key_offset, key_offset + len(keys), 3) + index + keys + data


class ParamSfoTests(unittest.TestCase):
    def test_edits_keep_fixed_slots_and_opaque_bytes(self):
        raw = fixture()
        document = ParamSfo(raw)
        self.assertEqual(document.edited({}), raw)
        self.assertEqual(document.edited({'ATTRIBUTE': '0x07'}), raw)
        result = document.edited({'TITLE': 'Café', 'ATTRIBUTE': '0xff'})
        revised = ParamSfo(result)
        self.assertEqual([field.value for field in revised.fields], ['Café', '255', '00 ff fe 00'])
        self.assertEqual(result[-4:], raw[-4:])
        self.assertEqual(len(result), len(raw))

    def test_edit_limits(self):
        document = ParamSfo(fixture())
        for key, value in (('TITLE', 'é' * 8), ('TITLE', 'bad\0text'),
                           ('ATTRIBUTE', '-1'), ('ATTRIBUTE', '4294967296'), ('OPAQUE', '00')):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                document.edited({key: value})
        self.assertEqual(ParamSfo(document.edited({'TITLE': ''})).fields[0].value, '')

    def test_corrupt_offsets_and_overlaps_rejected(self):
        raw = fixture()
        with self.assertRaises(ValueError):
            ParamSfo(raw[:-1])
        malformed = bytearray(raw)
        UINT32.pack_into(malformed, HEADER.size + INDEX.size + 12, 0)
        with self.assertRaisesRegex(ValueError, 'overlapping'):
            ParamSfo(malformed)

    def test_save_backs_up_binary_and_rejects_conflict(self):
        backend = MemoryBackend()
        name = 'PARAM.SFO'
        raw = fixture()
        backend.files[name] = raw
        digest = hashlib.sha256(raw).hexdigest()
        revised = ParamSfo(raw).edited({'TITLE': 'New Title'})
        backup = safe_save_remote_bytes(backend, name, digest, revised)
        self.assertEqual(backend.files[backup], raw)
        self.assertEqual(backend.files[name], revised)
        with self.assertRaises(RemoteConflictError):
            safe_save_remote_bytes(backend, name, digest, raw)
