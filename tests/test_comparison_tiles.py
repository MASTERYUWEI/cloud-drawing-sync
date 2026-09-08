"""Tile registration, seams, mode-specific work, reuse and bounded memory."""
import threading
import unittest
from unittest.mock import patch
from PIL import ImageChops

from comparison_tiles import ComparisonTileCache, TileRenderCancelled
from comparison_view import render_comparison_frame
from drawing_diff import compare_previews, compare_visible_layers
from pdf_viewport import render_pdf_viewport
from test_pdf_viewport import make_pdf


class TileCacheTests(unittest.TestCase):
    def setUp(self):
        common = "0 0 0 RG 0.6 w 0 40 m 700 600 l S 200 100 350 280 re S BT /F1 16 Tf 100 430 Td (TILE BOUNDARY DETAIL) Tj ET "
        self.pdfs = (make_pdf(common+"110 330 m 380 330 l S"),
                     make_pdf(common+"110 350 m 380 350 l S"))

    def test_tiles_match_continuous_render_without_internal_seams(self):
        cache = ComparisonTileCache(self.pdfs, reference_width=1000, tile_size=128)
        for quality in (1, 2):
            for offset in ((0, 0), (-137, 19), (100, -200)):
                with self.subTest(quality=quality, offset=offset):
                    _, tiled = cache.render((500, 350), .8, offset, quality=quality)
                    _, full = render_comparison_frame(self.pdfs, (500, 350), .8, offset,
                                                       reference_width=1000, quality=quality)
                    diff = ImageChops.difference(tiled["overlay"], full["overlay"]).crop((4, 4, 496, 346))
                    # PDFium can slightly vary grey antialiasing at device clip
                    # boundaries. Geometry classification must remain identical;
                    # bound raster variation rather than require byte identity.
                    for key in ("added_mask", "removed_mask"):
                        self.assertIsNone(ImageChops.difference(tiled[key], full[key]).getbbox())
                    histogram = diff.convert("L").histogram()
                    changed = diff.width*diff.height - histogram[0]
                    self.assertLess(changed, diff.width*diff.height*0.002)
                    self.assertLessEqual(max(high for low, high in diff.getextrema()), 80)
                    r, g, b = diff.split()
                    self.assertLessEqual(ImageChops.difference(r, g).getextrema()[1], 2)
                    self.assertLessEqual(ImageChops.difference(r, b).getextrema()[1], 2)

    def test_interaction_reuses_sharp_tiles_without_downgrading_cache(self):
        cache = ComparisonTileCache(self.pdfs, reference_width=1000, tile_size=128)
        _, sharp = cache.render((400, 300), .8, (0, 0), quality=2)
        _, fast = cache.render((400, 300), .8, (-5, 0), quality=1)
        self.assertEqual(fast["cache_stats"]["misses"], 0)
        self.assertEqual(fast["cache_stats"]["entries"], sharp["cache_stats"]["entries"])

    def test_tiny_wall_change_resolves_at_deep_zoom_with_bounded_tiles(self):
        # A 0.08 reference-pixel change is invisible in the full-page preview,
        # but separates into red/green strokes after vector rerendering at 128x.
        common = "0 0 0 RG 0.01 w 100 400 m 110 400 l S "
        old = make_pdf(common + "100 399.8 m 110 399.8 l S")
        new = make_pdf(common + "100 399.72 m 110 399.72 l S")
        cache = ComparisonTileCache((old, new), reference_width=1000, tile_size=128)
        _, result = cache.render((500, 180), 128, (-12800, -38370))
        self.assertGreater(result["added_pixels"], 0)
        self.assertGreater(result["removed_pixels"], 0)
        self.assertLessEqual(result["cache_stats"]["misses"], 15)
        same = ComparisonTileCache((old, old), reference_width=1000)
        _, identical = same.render((500, 180), 128, (-12800, -38370))
        self.assertEqual(identical["added_pixels"], 0)
        self.assertEqual(identical["removed_pixels"], 0)

    def test_pan_only_renders_new_tiles_and_returning_is_all_hits(self):
        cache = ComparisonTileCache(self.pdfs, reference_width=1000, tile_size=128)
        _, first = cache.render((400, 300), .8, (0, 0))
        _, moved = cache.render((400, 300), .8, (-80, 0))
        _, returned = cache.render((400, 300), .8, (0, 0))
        self.assertGreater(moved["cache_stats"]["hits"], 0)
        self.assertLess(moved["cache_stats"]["misses"], first["cache_stats"]["misses"])
        self.assertEqual(returned["cache_stats"]["misses"], 0)
        self.assertEqual(returned["cache_stats"]["native_tiles"], 0)
        self.assertIsNone(ImageChops.difference(first["overlay"], returned["overlay"]).getbbox())

    def test_raw_mode_never_renders_other_version_or_runs_diff(self):
        cache = ComparisonTileCache(self.pdfs, reference_width=1000, tile_size=128)
        with patch("comparison_tiles.compare_visible_layers") as detector:
            views, result = cache.render((400, 300), .8, (0, 0), mode="old")
        detector.assert_not_called()
        self.assertIsNotNone(views[0])
        self.assertIsNone(views[1])
        self.assertEqual(result["cache_stats"]["native_calls"], 1)
        self.assertGreater(result["cache_stats"]["misses"], 1)

    def test_cold_fullscreen_zoom_batches_native_traversals(self):
        cache = ComparisonTileCache(self.pdfs, reference_width=1000)
        _, frame = cache.render((1918, 1002), 2, (-499, -1200), mode="swipe", quality=1)
        self.assertGreater(frame["cache_stats"]["misses"], 20)
        self.assertEqual(frame["cache_stats"]["native_calls"], 2)
        self.assertEqual(frame["cache_stats"]["classified_batches"], 1)

    def test_batches_cover_only_misses_and_bound_temporary_pixels(self):
        cache = ComparisonTileCache(self.pdfs)
        missing = [(x,y) for x in range(-12,15) for y in range(-3,9) if (x+y)%5 != 0]
        for quality in (1,2):
            rectangles = cache._batches(missing, quality)
            covered = []
            for x0,y0,x1,y1 in rectangles:
                self.assertLessEqual(((x1-x0)*256+8)*((y1-y0)*256+8)*quality**2,4_000_000)
                covered.extend((x,y) for x in range(x0,x1) for y in range(y0,y1))
            self.assertEqual(sorted(covered), sorted(missing))

    def test_mode_specific_layers_equal_full_comparator(self):
        images = tuple(render_pdf_viewport(p, (500, 350), .5, (0, 0), reference_width=1000) for p in self.pdfs)
        full = compare_previews(*images, tolerance=0, include_regions=False)
        for mode, keys in (("overlay", ("overlay",)), ("swipe", ("old_overlay", "new_overlay"))):
            result = compare_visible_layers(*images, mode=mode)
            for key in keys:
                self.assertIsNone(ImageChops.difference(result[key], full[key]).getbbox())
            self.assertNotIn("old_overlay" if mode == "overlay" else "overlay", result)

    def test_memory_limit_and_pair_isolation(self):
        cache = ComparisonTileCache(self.pdfs, tile_size=64, max_bytes=64*64*5)
        _, result = cache.render((250, 200), .1, (0, 0))
        self.assertLessEqual(result["cache_stats"]["bytes"], cache.max_bytes)
        self.assertLessEqual(len(cache.tiles), 1)
        identical = ComparisonTileCache((self.pdfs[0], self.pdfs[0]), reference_width=1000)
        _, same = identical.render((400, 300), .8, (0, 0))
        self.assertEqual(same["added_pixels"], 0)
        self.assertEqual(same["removed_pixels"], 0)

    def test_cancelled_frame_keeps_finished_tiles_for_next_request(self):
        cache = ComparisonTileCache(self.pdfs, reference_width=1000, tile_size=128)

        class PartialCancellation:
            def is_set(self):
                return len(cache.tiles) >= 3

        with self.assertRaises(TileRenderCancelled):
            cache.render((500, 400), .8, (0, 0), cancel_event=PartialCancellation())
        self.assertGreater(len(cache.tiles), 0)
        _, result = cache.render((500, 400), .8, (0, 0))
        self.assertGreater(result["cache_stats"]["hits"], 0)
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(TileRenderCancelled):
            cache.render((500, 400), .8, (0, 0), cancel_event=cancelled)
