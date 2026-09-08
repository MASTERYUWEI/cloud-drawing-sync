"""Interactive frame registration and high-quality still rendering."""
import unittest
from PIL import Image, ImageDraw, ImageChops
from comparison_view import reproject_image, render_comparison_frame
from test_pdf_viewport import make_pdf


class ComparisonFrameTests(unittest.TestCase):
    def test_pan_uses_integer_translation(self):
        original = Image.new("RGB", (120, 100), "white")
        ImageDraw.Draw(original).rectangle((20, 20, 40, 40), fill="black")
        moved = reproject_image(original, (120, 100), 2, (-50, 20), 2, (-20, 30))
        self.assertEqual(moved.getpixel((50, 30)), (0, 0, 0))
        self.assertEqual(moved.getpixel((20, 20)), (255, 255, 255))

    def test_zoom_keeps_common_geometry_registered(self):
        old = Image.new("RGB", (120, 100), "white")
        new = old.copy()
        ImageDraw.Draw(old).line((10, 30, 110, 70), fill="red")
        ImageDraw.Draw(new).line((10, 30, 110, 70), fill="green")
        left = reproject_image(old, (200, 150), .25, (-12, 7), .41, (20, -13))
        right = reproject_image(new, (200, 150), .25, (-12, 7), .41, (20, -13))
        self.assertIsNone(ImageChops.difference(left.getchannel("B"), right.getchannel("B")).getbbox())

    def test_supersampling_smooths_diagonals_and_retains_changes(self):
        old = make_pdf("0 0 0 RG 1 w 20 20 m 100 100 l S")
        new = make_pdf("0 0 0 RG 1 w 20 20 m 100 100 l S 150 100 m 250 190 l S")
        views, result = render_comparison_frame((old, new), (400, 280), .4, (0, 0), reference_width=1000)
        self.assertEqual(result["render_quality"], 2)
        self.assertEqual(views[0].size, (400, 280))
        self.assertGreater(result["added_pixels"], 0)
        self.assertEqual(result["removed_pixels"], 0)
        self.assertEqual(result["added_pixels"], result["added_mask"].histogram()[255])
        colours = result["overlay"].getcolors(400*280)
        self.assertTrue(any(145 < green < 255 and red < green for _, (red, green, blue) in colours))

    def test_identical_vectors_never_become_red_green_when_zoomed(self):
        pdf = make_pdf("0 0 0 RG 0.1 w 20 100 m 800 400 l S")
        for scale, offset in ((.3, (10, 7)), (2.3, (-150, -450))):
            _, result = render_comparison_frame((pdf, pdf), (500, 350), scale, offset)
            self.assertEqual(result["added_pixels"], 0)
            self.assertEqual(result["removed_pixels"], 0)
