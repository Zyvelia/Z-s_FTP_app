import http.client
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from zftp.servers.phone_drop import PhoneDrop


class PhoneDropTests(unittest.TestCase):
    def test_every_file_requires_approval_and_existing_file_is_preserved(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'fixture.bin').write_bytes(b'original')
            drop = PhoneDrop('127.0.0.1', root)
            try:
                def call(method, suffix, body=None, content_type=None):
                    conn = http.client.HTTPConnection(drop.host, timeout=3)
                    headers = {'Origin': 'http://' + drop.host}
                    if content_type is not None:
                        headers['Content-Type'] = content_type
                    conn.request(method, f'/{drop.code}/{suffix}', body=body, headers=headers)
                    result = conn.getresponse()
                    payload = result.read()
                    conn.close()
                    return result.status, payload

                self.assertEqual(call('GET', '')[0], 200)
                status, response = call('POST', 'request', b'{"name":"fixture.bin","size":5}', 'application/json')
                self.assertEqual(status, 201)
                rid = json.loads(response)['id']
                self.assertEqual(call('POST', f'request/{rid}/file', b'bytes')[0], 403)
                self.assertEqual(list(root.iterdir()), [root / 'fixture.bin'])
                self.assertTrue(drop.decide(rid, True))
                self.assertEqual(call('POST', f'request/{rid}/file', b'bad')[0], 403)
                self.assertEqual(call('POST', f'request/{rid}/file', b'bytes')[0], 201)
                self.assertEqual((root / 'fixture.bin').read_bytes(), b'original')
                self.assertEqual((root / 'fixture (2).bin').read_bytes(), b'bytes')
                self.assertEqual(call('POST', f'request/{rid}/file', b'again')[0], 403)
                self.assertEqual(call('POST', 'request', b'{"name":"../bad","size":1}', 'application/json')[0], 400)
            finally:
                drop.stop()
