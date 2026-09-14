"""Only outer clouds: preserve concavity, suppress holes and nested islands."""
import math
import unittest
from PIL import Image, ImageDraw
from change_outline import change_contours, cloud_paths


def inside(point, contour):
    x, y = point
    odd = False
    for (a,b), (c,d) in zip(contour, contour[1:]):
        if (b > y) != (d > y) and x < (c-a)*(y-b)/(d-b)+a:
            odd = not odd
    return odd


def s_fixture():
    old, new = Image.new("L", (640, 640)), Image.new("L", (640, 640))
    points = [(320 + 170*math.sin((y-80)/480*2*math.pi), y) for y in range(80, 561)]
    ImageDraw.Draw(old).line(points, fill=255, width=2)
    ImageDraw.Draw(new).line([(x+5,y) for x,y in points], fill=255, width=2)
    return old, new, points


class ChangeOutlineTests(unittest.TestCase):
    def test_s_shape_wraps_strokes_without_filling_blank_corners(self):
        old, new, points = s_fixture()
        loops = change_contours(old, new)
        self.assertEqual(len(loops), 1)
        loop = loops[0]
        self.assertEqual(loop[0], loop[-1])
        for x,y in points[::8]:
            self.assertTrue(inside((x,y), loop))
            self.assertTrue(inside((x+5,y), loop))
        self.assertFalse(inside((160, 100), loop))
        self.assertFalse(inside((480, 540), loop))
        filled = sum(inside((x,y), loop) for x in range(130,520,10) for y in range(60,580,10))
        self.assertLess(filled / (39*52), .3)

    def test_hollow_ring_only_has_outer_boundary(self):
        mask = Image.new("L", (300, 300))
        ImageDraw.Draw(mask).rectangle((50,50,250,250), outline=255, width=2)
        loops = change_contours(mask, Image.new("L", mask.size))
        self.assertEqual(len(loops), 1)
        self.assertTrue(inside((150,150), loops[0]))
        self.assertEqual(sum(inside((50,150), loop) for loop in loops) % 2, 1)

    def test_nested_small_lines_and_multiple_inner_rings_have_no_extra_clouds(self):
        mask = Image.new("L", (640,640))
        draw = ImageDraw.Draw(mask)
        draw.rectangle((40,40,480,580), outline=255, width=2)
        draw.rectangle((100,100,400,500), outline=255, width=2)
        draw.line((160,300,340,300), fill=255, width=2)
        draw.line((560,100,560,160), fill=255, width=2)  # Independent outside change.
        before = mask.tobytes()
        loops = change_contours(mask, Image.new("L", mask.size))
        self.assertEqual(len(loops), 2)
        self.assertEqual(sum(inside((250,300), loop) for loop in loops), 1)
        self.assertEqual(sum(inside((560,130), loop) for loop in loops), 1)
        self.assertEqual(mask.tobytes(), before)

    def test_zoom_retains_outer_only_when_enclosing_ends_are_offscreen(self):
        from comparison_regions import ComparisonRegions
        reference = Image.new("L", (600,300))
        draw = ImageDraw.Draw(reference)
        draw.rectangle((50,50,550,250), outline=255, width=2)
        draw.line((130,150,250,150), fill=255, width=2)
        empty = Image.new("L", reference.size)
        index = ComparisonRegions.from_masks(reference, empty)
        size, scale, offset = (1000,1000), 4, (-300,-100)
        view = reference.transform(size, Image.Transform.AFFINE, (.25,0,75,0,.25,25),
                                   resample=Image.Resampling.NEAREST)
        hint = index.interior_hint(size, scale, offset)
        loops = change_contours(view, Image.new("L", size), interior_hint=hint)
        paths = [path for loop in loops for path in cloud_paths(loop,1,(0,0),size)]
        self.assertTrue(paths)
        # Only top/bottom exterior edges; no cloud around the small middle line.
        for path in paths:
            self.assertFalse(any(20 < x < 980 and 140 < y < 870 for x,y in path))

    def test_view_entirely_inside_outer_cloud_has_no_visible_inner_cloud(self):
        from comparison_regions import ComparisonRegions
        reference = Image.new("L", (600,300))
        draw = ImageDraw.Draw(reference)
        draw.rectangle((50,50,550,250), outline=255, width=2)
        draw.line((130,150,250,150), fill=255, width=2)
        index = ComparisonRegions.from_masks(reference, Image.new("L", reference.size))
        size, scale, offset = (800,600), 4, (-400,-250)
        view = reference.transform(size, Image.Transform.AFFINE, (.25,0,100,0,.25,62.5),
                                   resample=Image.Resampling.NEAREST)
        loops = change_contours(view, Image.new("L", size), interior_hint=index.interior_hint(size,scale,offset))
        self.assertFalse([path for loop in loops for path in cloud_paths(loop,1,(0,0),size)])

    def test_empty_and_separate_islands(self):
        empty = Image.new("L", (400,300))
        self.assertEqual(change_contours(empty, empty), [])
        added = empty.copy()
        added.putpixel((20,30), 255)
        added.putpixel((350,240), 255)
        loops = change_contours(added, empty)
        self.assertEqual(len(loops), 2)
        self.assertFalse(any(inside((200,150), loop) for loop in loops))

    def test_edges_pad_beyond_viewport_instead_of_false_visible_end_caps(self):
        mask = Image.new("L", (200,100))
        ImageDraw.Draw(mask).line((0,50,199,50), fill=255)
        loop, = change_contours(mask, Image.new("L", mask.size))
        self.assertLess(min(x for x,y in loop), 0)
        self.assertGreater(max(x for x,y in loop), 200)
        for x in range(200):
            self.assertTrue(inside((x,50), loop))

    def test_diagonal_touching_pixels_do_not_break_contour_tracing(self):
        mask = Image.new("L", (8,8))
        mask.putpixel((2,2),255)
        mask.putpixel((3,3),255)
        loops = change_contours(mask, Image.new("L", mask.size), step=1, padding=0)
        self.assertTrue(all(loop[0] == loop[-1] for loop in loops))
        self.assertEqual(len(loops), 2)

    def test_projection_clips_before_sampling_million_pixel_edges(self):
        loop = [(0,0), (1000,0), (1000,1000), (0,1000), (0,0)]
        paths = cloud_paths(loop, 2048, (-1_000_000, 30), (1920,1000))
        self.assertTrue(paths)
        self.assertLess(sum(map(len, paths)), 1000)
        for path in paths:
            for x,y in path:
                self.assertTrue(-12 <= x <= 1932 and -12 <= y <= 1012)

    def test_cloud_bulges_outward_and_is_continuous(self):
        loop = [(20,20), (200,20), (200,200), (20,200), (20,20)]
        paths = cloud_paths(loop, 1, (0,0), (300,300))
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0][0], paths[0][-1])
        self.assertLess(min(y for x,y in paths[0]), 20)
        self.assertGreater(max(x for x,y in paths[0]), 200)


if __name__ == "__main__":
    unittest.main()
