import json
from pathlib import Path
import tempfile
import unittest

from comparison_session import load_comparison_session


class ComparisonSessionTests(unittest.TestCase):
    def test_preserves_old_new_order_and_does_not_modify_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            old, new = folder / "舊版 圖面.dwg", folder / "新版 圖面.dwg"
            old.write_bytes(b"old source")
            new.write_bytes(b"new source")
            session = folder / "pair.json"
            session.write_text(json.dumps({"old_dwg": str(old), "new_dwg": str(new)}, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(load_comparison_session(session), (str(old), str(new)))
            self.assertEqual(old.read_bytes(), b"old source")
            self.assertEqual(new.read_bytes(), b"new source")

    def test_missing_relative_wrong_extension_and_malformed_sessions_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "pair.json"
            for data in ([], {}, {"old_dwg": "relative.dwg"},
                         {"old_dwg": str(Path(directory) / "missing.dwg")},
                         {"old_dwg": str(Path(directory) / "wrong.pdf")}):
                session.write_text(json.dumps(data), encoding="utf-8")
                with self.subTest(data=data), self.assertRaises(ValueError):
                    load_comparison_session(session)
