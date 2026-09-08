"""Pixel regressions for DWG downsampling and shared swipe coordinates."""

import math
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageChops, ImageDraw

from dwg_compare_window import DwgCompareWindow, compose_swipe, render_viewport


class ViewportTests(unittest.TestCase):
    def test_fit_preserves_single_pixel_horizontal_line(self):
        # Real-preview regression: affine BICUBIC alone samples around other
        # source rows at this scale and makes the whole y=80 line disappear.
        drawing = Image.new("RGB", (4096, 2896), "white")
        ImageDraw.Draw(drawing).line((0, 80, 4095, 80), fill="black", width=1)
        view = render_viewport(drawing, (512, 362), 0.125, (0, 0))
        for x in (20, 150, 300, 490):
            self.assertLess(min(view.getpixel((x, y))[0] for y in range(6, 15)), 245)
        self.assertEqual(view.getpixel((200, 100)), (255, 255, 255))

    def test_fit_preserves_single_pixel_vertical_line_after_fractional_pan(self):
        drawing = Image.new("RGB", (1024, 768), "white")
        ImageDraw.Draw(drawing).line((81, 0, 81, 767), fill="black", width=1)
        view = render_viewport(drawing, (200, 150), 0.11, (17.25, 9.75))
        expected_x = 81 * 0.11 + 17.25
        for y in (20, 40, 60, 85):
            self.assertLess(min(view.getpixel((x, y))[0]
                                for x in range(math.floor(expected_x) - 3,
                                               math.ceil(expected_x) + 4)), 245)

    def test_rounded_downsample_dimensions_keep_both_versions_aligned(self):
        # Non-integral sizes force different rounded x/y resize factors. The
        # same source geometry must land in the same output pixels regardless
        # of which version/color is being viewed.
        old = Image.new("RGB", (503, 317), "white")
        new = Image.new("RGB", old.size, "white")
        ImageDraw.Draw(old).rectangle((240, 140, 272, 172), fill="black")
        ImageDraw.Draw(new).rectangle((240, 140, 272, 172), fill="red")
        scale, offset = 0.117, (15.375, 12.625)
        old_view = render_viewport(old, (100, 90), scale, offset)
        new_view = render_viewport(new, (100, 90), scale, offset)
        self.assertIsNone(ImageChops.difference(old_view.getchannel("G"),
                                                new_view.getchannel("G")).getbbox())
        bbox = old_view.getchannel("G").point(lambda v: 255 if v < 180 else 0).getbbox()
        self.assertIsNotNone(bbox)
        expected_center = (256 * scale + offset[0], 156 * scale + offset[1])
        self.assertAlmostEqual((bbox[0] + bbox[2]) / 2, expected_center[0], delta=1.1)
        self.assertAlmostEqual((bbox[1] + bbox[3]) / 2, expected_center[1], delta=1.1)

    def test_zoomed_view_is_canvas_sized_and_keeps_shared_anchor(self):
        drawing = Image.new("RGB", (64, 64), "white")
        ImageDraw.Draw(drawing).rectangle((20, 20, 23, 23), fill="black")
        view = render_viewport(drawing, (100, 80), 8.0, (-128, -128))
        self.assertEqual(view.size, (100, 80))
        self.assertEqual(view.getpixel((42, 42)), (0, 0, 0))
        self.assertEqual(view.getpixel((10, 10)), (255, 255, 255))

    def test_swipe_endpoints_and_boundary(self):
        old = Image.new("RGB", (100, 80), "red")
        new = Image.new("RGB", old.size, "blue")
        self.assertEqual(compose_swipe(old, new, 0).tobytes(), new.tobytes())
        self.assertEqual(compose_swipe(old, new, 1).tobytes(), old.tobytes())
        self.assertEqual(compose_swipe(old, new, -1).tobytes(), new.tobytes())
        self.assertEqual(compose_swipe(old, new, 2).tobytes(), old.tobytes())
        halfway = compose_swipe(old, new, 0.5)
        self.assertEqual(halfway.getpixel((49, 40)), (255, 0, 0))
        self.assertEqual(halfway.getpixel((50, 40)), (0, 0, 255))

    def test_mismatched_preview_sizes_are_rejected(self):
        with self.assertRaises(ValueError):
            compose_swipe(Image.new("RGB", (20, 20)), Image.new("RGB", (30, 20)), 0.5)


class ReusedWindowTests(unittest.TestCase):
    def make_window(self, busy=False):
        path_variable = Mock()
        path_variable.get.return_value = os.path.abspath("fixtures/current.dwg")
        return SimpleNamespace(_closed=False, _busy=busy, _new_path=path_variable,
                               _invalidate_preview=Mock())

    def test_new_path_updates_idle_window_and_invalidates_old_preview(self):
        window = self.make_window()
        self.assertTrue(DwgCompareWindow.set_new_path(window, "fixtures/another.dwg"))
        window._new_path.set.assert_called_once_with(os.path.normpath("fixtures/another.dwg"))
        window._invalidate_preview.assert_called_once_with()

    def test_same_path_keeps_loaded_preview(self):
        window = self.make_window()
        self.assertTrue(DwgCompareWindow.set_new_path(window, "fixtures/../fixtures/current.dwg"))
        window._new_path.set.assert_not_called()
        window._invalidate_preview.assert_not_called()

    @patch("dwg_compare_window.messagebox.showinfo")
    def test_busy_window_explains_wait_or_cancel_without_changing_selection(self, showinfo):
        window = self.make_window(busy=True)
        self.assertFalse(DwgCompareWindow.set_new_path(window, "fixtures/another.dwg"))
        window._new_path.set.assert_not_called()
        window._invalidate_preview.assert_not_called()
        showinfo.assert_called_once()
        self.assertIn("等待", showinfo.call_args.args[1])
        self.assertIn("取消轉換", showinfo.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
