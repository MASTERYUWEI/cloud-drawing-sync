"""Visual change classification: these are raster regions, not CAD entities."""
from pathlib import Path
import re
import unittest

from PIL import Image, ImageDraw

from drawing_diff import ADDED_COLOUR, REMOVED_COLOUR, compare_previews


def blank(size=(128, 96)):
    return Image.new('RGB', size, 'white')


class DrawingDiffChecks(unittest.TestCase):
    def test_identical_has_no_changes(self):
        old = blank()
        ImageDraw.Draw(old).rectangle((10, 10, 80, 65), outline='black')
        result = compare_previews(old, old.copy())
        self.assertEqual(result['added_pixels'], 0)
        self.assertEqual(result['removed_pixels'], 0)
        self.assertEqual(result['regions'], [])

    def test_new_and_removed_one_pixel_lines_use_correct_colours(self):
        old, new = blank(), blank()
        ImageDraw.Draw(old).line((10, 15, 60, 15), fill='black')
        ImageDraw.Draw(new).line((10, 45, 60, 45), fill='black')
        result = compare_previews(old, new)
        self.assertEqual(result['added_pixels'], 51)
        self.assertEqual(result['removed_pixels'], 51)
        self.assertEqual(result['overlay'].getpixel((30, 15)), REMOVED_COLOUR)
        self.assertEqual(result['overlay'].getpixel((30, 45)), ADDED_COLOUR)
        self.assertEqual(result['old_overlay'].getpixel((30, 45)), (255, 255, 255))
        self.assertEqual(result['new_overlay'].getpixel((30, 15)), (255, 255, 255))

    def test_moved_line_marks_both_positions(self):
        old, new = blank(), blank()
        ImageDraw.Draw(old).line((20, 20, 90, 20), fill='black')
        ImageDraw.Draw(new).line((20, 27, 90, 27), fill='black')
        result = compare_previews(old, new)
        self.assertEqual(result['added_pixels'], 71)
        self.assertEqual(result['removed_pixels'], 71)
        self.assertEqual(len(result['regions']), 1)
        self.assertEqual(result['regions'][0]['kind'], 'changed')

    def test_new_crossing_line_keeps_old_line_neutral(self):
        old = blank()
        ImageDraw.Draw(old).line((10, 40, 90, 40), fill='black')
        new = old.copy()
        ImageDraw.Draw(new).line((50, 10, 50, 70), fill='black')
        result = compare_previews(old, new)
        self.assertEqual(result['removed_pixels'], 0)
        self.assertEqual(result['added_pixels'], 58)  # Existing stroke ±1 px.
        self.assertEqual(result['overlay'].getpixel((50, 20)), ADDED_COLOUR)
        self.assertEqual(result['added_mask'].getpixel((50, 40)), 0)

    def test_antialias_fringe_and_colour_only_changes_are_ignored(self):
        old, new = blank(), blank()
        ImageDraw.Draw(old).line((10, 40, 90, 40), fill=(0, 0, 0))
        draw = ImageDraw.Draw(new)
        draw.line((10, 39, 90, 39), fill=(210, 210, 210))
        draw.line((10, 40, 90, 40), fill=(255, 255, 0))
        draw.line((10, 41, 90, 41), fill=(235, 235, 235))
        result = compare_previews(old, new)
        self.assertEqual(result['added_pixels'], 0)
        self.assertEqual(result['removed_pixels'], 0)

    def test_extension_is_not_lost_with_endpoint_tolerance(self):
        old, new = blank(), blank()
        ImageDraw.Draw(old).line((10, 40, 40, 40), fill='black')
        ImageDraw.Draw(new).line((10, 40, 60, 40), fill='black')
        result = compare_previews(old, new)
        self.assertEqual(result['removed_pixels'], 0)
        self.assertEqual(result['added_pixels'], 19)
        self.assertEqual(result['overlay'].getpixel((60, 40)), ADDED_COLOUR)

    def test_exact_mode_preserves_single_pixel_extension(self):
        old, new = blank(), blank()
        ImageDraw.Draw(old).line((10, 40, 40, 40), fill='black')
        ImageDraw.Draw(new).line((10, 40, 41, 40), fill='black')
        result = compare_previews(old, new, tolerance=0)
        self.assertEqual(result['added_pixels'], 1)
        self.assertEqual(len(result['regions']), 1)

    def test_single_isolated_pixel_has_a_region_even_at_4096(self):
        old, new = blank((4096, 64)), blank((4096, 64))
        new.putpixel((2016, 31), (0, 0, 0))
        result = compare_previews(old, new)
        self.assertEqual(result['added_pixels'], 1)
        self.assertEqual(len(result['regions']), 1)
        x0, y0, x1, y1 = result['regions'][0]['bbox']
        self.assertTrue(x0 <= 2016 < x1 and y0 <= 31 < y1)
        self.assertEqual(result['regions'][0]['added_pixels'], 1)

    def test_region_counts_do_not_duplicate_nested_or_nearby_changes(self):
        old, new = blank((512, 512)), blank((512, 512))
        draw = ImageDraw.Draw(new)
        draw.rectangle((20, 20, 490, 490), outline='black')
        draw.rectangle((240, 240, 250, 250), fill='black')
        result = compare_previews(old, new)
        self.assertEqual(len(result['regions']), 2)
        self.assertEqual(sum(r['added_pixels'] for r in result['regions']), result['added_pixels'])

    def test_region_limit_does_not_discard_change_masks(self):
        old, new = blank((1024, 1024)), blank((1024, 1024))
        for y in range(16, 1000, 40):
            for x in range(16, 1000, 40):
                new.putpixel((x, y), (0, 0, 0))
        result = compare_previews(old, new)
        self.assertEqual(result['added_pixels'], 625)
        self.assertEqual(result['total_regions'], 625)
        self.assertEqual(len(result['regions']), 200)
        self.assertEqual(result['omitted_regions'], 425)

    def test_bad_size_or_tolerance_rejected(self):
        with self.assertRaises(ValueError):
            compare_previews(blank(), blank((100, 100)))
        for tolerance in (-1, 1.5, True, 9):
            with self.subTest(tolerance=tolerance), self.assertRaises(ValueError):
                compare_previews(blank(), blank(), tolerance=tolerance)

    def test_native_autocad_fixture_when_present(self):
        root = Path(__file__).resolve().parents[1] / '.preview-test'
        old_paths = sorted(path for path in root.glob('*/dwg_*/old.png')
                           if re.fullmatch(r'[0-9a-f]{32}', path.parents[1].name)) if root.exists() else []
        pair = next(((old, old.with_name('new.png')) for old in old_paths
                     if old.with_name('new.png').exists()), None)
        if pair is None:
            self.skipTest('Run test_dwg_preview_native.py to generate optional AutoCAD fixtures.')
        with Image.open(pair[0]) as old, Image.open(pair[1]) as new:
            result = compare_previews(old.convert('RGB'), new.convert('RGB'))
        self.assertGreater(result['added_pixels'], 0)
        self.assertEqual(result['removed_pixels'], 0)


if __name__ == '__main__':
    unittest.main()
