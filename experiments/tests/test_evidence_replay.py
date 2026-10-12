import json
from pathlib import Path
import tempfile
import unittest
import warnings
import zipfile
from experiments.evidence_replay import extract_verified
from waterguard_contracts.core import digest_bytes


class PortableReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def bundle(self, entries, manifest=None):
        path = self.root / 'evidence.zip'
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(path, 'w') as archive:
                for name, value in entries:
                    archive.writestr(name, value)
                if manifest is not None:
                    archive.writestr('evidence-manifest.json', json.dumps(manifest))
        return path

    def test_traversal_and_duplicate_members_are_rejected(self):
        for entries in [('../outside.txt', b'bad')], [('x', b'first'), ('x', b'second')]:
            with self.subTest(entries=entries), self.assertRaisesRegex(ValueError, 'Unsafe archive|Duplicate ZIP'):
                extract_verified(self.bundle(entries), self.root / 'review')
        self.assertFalse((self.root / 'outside.txt').exists())

    def test_hash_mismatch_and_unlisted_files_are_rejected(self):
        reference = 'artifact:result.json'
        manifest = dict(mode='live', live_export_allowed=True, artifacts=[dict(ref=reference, sha256=digest_bytes(b'original'))],
                        hashes={reference: digest_bytes(b'original')})
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            extract_verified(self.bundle([('result.json', b'altered')], manifest), self.root / 'first')
        with self.assertRaisesRegex(ValueError, 'outside the evidence inventory'):
            extract_verified(self.bundle([('result.json', b'original'), ('extra.json', b'{}')], manifest), self.root / 'second')


if __name__ == '__main__':
    unittest.main()
