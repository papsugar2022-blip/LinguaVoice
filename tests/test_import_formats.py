import tempfile
import unittest
import io
import json
import zipfile
from pathlib import Path
from linguavoice.import_formats import normalize_raw_pack
from linguavoice.storage import Store

class RawPackTests(unittest.TestCase):
    def test_reference_manifest_cannot_read_outside_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(directory)
            for path in ('../outside.wav','C:/outside.wav','/outside.wav'):
                archive=io.BytesIO()
                with zipfile.ZipFile(archive,'w') as z:
                    z.writestr('voice.json',json.dumps({'format':'linguavoice-qwen-reference','schema_version':1,
                        'reference':{'audio':path,'language':'zh','text':'测试参考'}}))
                archive.seek(0)
                with self.assertRaisesRegex(ValueError,'路径无效'):
                    store.import_zip(archive,trusted=True)

    def test_single_family_rounds_and_filename_transcript(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'sample-e5.ckpt').write_bytes(b'first')
            (root/'sample-e10.ckpt').write_bytes(b'selected')
            (root/'sample_e2_s94.pth').write_bytes(b'08weight')
            (root/'这是一段参考台词.wav').write_bytes(b'not-read-in-format-test')
            normalize_raw_pack(root)
            self.assertEqual((root/'models/gpt.ckpt').read_bytes(),b'selected')
            self.assertIn('这是一段参考台词',(root/'reference.list').read_text(encoding='utf-8'))

    def test_multiple_families_are_not_guessed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in ('one-e5.ckpt','two-e10.ckpt','voice.pth'):
                (root/name).write_bytes(b'03weights')
            with self.assertRaisesRegex(ValueError,'多个声音'):
                normalize_raw_pack(root)

    def test_recording_number_is_not_a_transcript(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in ('voice.ckpt','voice.pth','001.wav'):
                (root/name).write_bytes(b'03weights')
            with self.assertRaisesRegex(ValueError,'台词标注'):
                normalize_raw_pack(root)
