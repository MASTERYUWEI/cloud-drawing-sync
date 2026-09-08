"""Fast regression checks for comparison failure handling and geometry bounds."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import dwg_preview as preview


class PreviewChecks(unittest.TestCase):
    def test_union_keeps_original_world_coordinates(self):
        bounds = preview._common_bounds((100, 200, 300, 250), (120, 190, 500, 210))
        self.assertEqual(bounds, (90, 180, 510, 260))

    def test_empty_or_nonfinite_bounds_fail(self):
        for coords in ("0|0|0|0", "nan|0|10|10", "5|5|-5|-5"):
            with self.subTest(coords=coords), self.assertRaises(preview.PreviewError):
                preview._parse_measurement(f"\nCDS_EXTENTS|{coords}\nCDS_MEASURE_DONE\n", "舊版")

    def test_localized_media_name_is_kept(self):
        text = '\n  "ISO full bleed A3 (297.00 x 420.00 公釐)"\n  "ISO full bleed A3 (420.00 x 297.00 公釐)"\n'
        self.assertEqual(preview._parse_media(text), "ISO full bleed A3 (420.00 x 297.00 公釐)")

    def test_cancel_before_any_files_or_processes(self):
        event = threading.Event()
        event.set()
        with patch.object(preview.subprocess, "Popen") as process:
            with self.assertRaises(preview.PreviewCancelled):
                preview.render_dwg_pair("old.dwg", "new.dwg", "unused", cancel_event=event)
            process.assert_not_called()

    def test_source_changed_between_measurement_and_render_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old, new = root / "old.dwg", root / "new.dwg"
            old.write_bytes(b"old")
            new.write_bytes(b"new")
            output = '\nCDS_EXTENTS|0|0|10|10\nCDS_UNITS|4\nCDS_MEASURE_DONE\n  "ISO A3 (420.00 x 297.00 MM)"\n'

            def fake_console(*args):
                new.write_bytes(b"updated during synchronization")
                return output

            with patch.object(preview, "find_core_console", return_value="engine"), \
                 patch.object(preview, "_find_pdf_plotter", return_value=Path("pdf.pc3")), \
                 patch.object(preview, "_run_console", side_effect=fake_console), \
                 self.assertRaisesRegex(preview.PreviewError, "比較期間已更新"):
                preview.render_dwg_pair(old, new, root)


if __name__ == "__main__":
    unittest.main()
