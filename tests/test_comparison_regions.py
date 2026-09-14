"""Bounds must follow fine visible differences, not just the coarse thumbnail."""
import unittest
from PIL import Image, ImageDraw

from comparison_regions import ComparisonRegions
from comparison_tiles import ComparisonTileCache
from test_pdf_viewport import make_pdf


def mask(size=(1024, 512)):
    return Image.new("L", size, 0)


class ProgressiveRegionTests(unittest.TestCase):
    def test_long_wall_and_return_are_not_limited_to_initial_corner(self):
        added, removed = mask(), mask()
        added.putpixel((100, 100), 255)
        index = ComparisonRegions.from_masks(added, removed)
        plus, minus = mask((1600, 480)), mask((1600, 480))
        ImageDraw.Draw(plus).line([(0, 100), (1500, 100), (1500, 450)], fill=255)
        ImageDraw.Draw(minus).line([(0, 102), (1502, 102), (1502, 450)], fill=255)
        refined = index.refine(plus, minus, 8, (-800, -700))
        regions, total = refined.regions()
        self.assertEqual(total, 1)
        self.assertEqual(regions[0]["kind"], "changed")
        self.assertLessEqual(regions[0]["bbox"][0], 100)
        self.assertGreaterEqual(regions[0]["bbox"][2], (1503 + 800) / 8)
        self.assertGreaterEqual(regions[0]["bbox"][3], (451 + 700) / 8)
        self.assertLess(index.regions()[0][0]["bbox"][2], 110)  # Immutable snapshot.

    def test_pan_retains_offscreen_changes_and_merges_connected_wall(self):
        index = ComparisonRegions.from_masks(mask(), mask())
        plus, minus = mask((400, 200)), mask((400, 200))
        ImageDraw.Draw(plus).line((0, 50, 399, 50), fill=255)
        first = index.refine(plus, minus, 4, (-400, -350))
        second = first.refine(plus, minus, 4, (-800, -350))
        regions, total = second.regions()
        self.assertEqual(total, 1)
        self.assertLessEqual(regions[0]["bbox"][0], 100)
        self.assertGreaterEqual(regions[0]["bbox"][2], 300)
        self.assertIs(second.refine(plus, minus, 4, (-800, -350)), second)
        self.assertIs(second.refine(minus, minus, .5, (0, 0)), second)

    def test_isolated_changes_stay_separate_and_swap_reverses_kinds(self):
        plus, minus = mask(), mask()
        plus.putpixel((50, 50), 255)
        minus.putpixel((900, 450), 255)
        index = ComparisonRegions.from_masks(plus, minus)
        before, total = index.regions()
        after, _ = index.swapped().regions()
        self.assertEqual(total, 2)
        self.assertEqual([r["bbox"] for r in before], [r["bbox"] for r in after])
        self.assertEqual([r["kind"] for r in after], ["removed", "added"])

    def test_single_pixel_pooling_fractional_zoom_and_clipped_edges(self):
        index = ComparisonRegions.from_masks(mask((127, 97)), mask((127, 97)))
        plus, minus = mask((701, 499)), mask((701, 499))
        scale, offset = 5.3, (-12, -7)
        points = [(0, 0), (15, 15), (16, 16), (251, 202), (654, 490)]
        for point in points:
            plus.putpixel(point, 255)
        regions, _ = index.refine(plus, minus, scale, offset).regions()
        for px, py in points:
            x, y = (px-offset[0])/scale, (py-offset[1])/scale
            if x < 127 and y < 97:
                self.assertTrue(any(a <= x < c and b <= y < d for a,b,c,d in (r["bbox"] for r in regions)))
        for r in regions:
            self.assertLessEqual(r["bbox"][2], 127)
            self.assertLessEqual(r["bbox"][3], 97)

    def test_identical_or_outside_drawing_does_not_add_regions(self):
        index = ComparisonRegions.from_masks(mask(), mask())
        plus = Image.new("L", (200, 100), 255)
        self.assertIs(index.refine(plus, plus, 8, (300, 300)), index)
        self.assertIs(index.refine(mask(), mask(), 8, (0, 0)), index)

    def test_deep_zoom_vector_change_feeds_actual_display_masks_into_bounds(self):
        old = make_pdf("0 0 0 RG 0.01 w 100 399.8 m 110 399.8 l S")
        new = make_pdf("0 0 0 RG 0.01 w 100 399.72 m 110 399.72 l S")
        cache = ComparisonTileCache((old, new), reference_width=1000)
        index = ComparisonRegions.from_masks(mask((1000, 700)), mask((1000, 700)))
        for mode in ("overlay", "swipe"):
            _, frame = cache.render((500, 180), 128, (-12800, -38370), mode=mode)
            refined = index.refine(frame["added_mask"], frame["removed_mask"], 128, (-12800, -38370))
            regions, total = refined.regions()
            self.assertEqual(total, 1)
            self.assertEqual(regions[0]["kind"], "changed")
            a,b,c,d = regions[0]["bbox"]
            for name in ("added_mask", "removed_mask"):
                x0,y0,x1,y1 = frame[name].getbbox()
                self.assertLessEqual(a, (x0+12800)/128)
                self.assertLessEqual(b, (y0+38370)/128)
                self.assertGreaterEqual(c, (x1+12800)/128)
                self.assertGreaterEqual(d, (y1+38370)/128)


if __name__ == "__main__":
    unittest.main()
