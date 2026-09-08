"""Tk workflow regressions with synthetic vector data and no sync/user state."""
from pathlib import Path
import gc
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import customtkinter as ctk
from PIL import ImageChops

from dwg_compare_window import DwgCompareWindow
from pdf_viewport import render_pdf_viewport
from test_pdf_viewport import make_pdf


class CompareWindowWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = ctk.CTk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.raise_patch = patch.object(DwgCompareWindow, "_raise_window", lambda self: None)
        self.raise_patch.start()
        self.window = DwgCompareWindow(self.root)
        self.window.withdraw()
        self.window._canvas_size = (720, 480)
        self.temp = tempfile.TemporaryDirectory()
        for name in ("old.dwg", "new.dwg"):
            (Path(self.temp.name) / name).write_bytes(b"synthetic input path")
        self.window._old_path.set(str(Path(self.temp.name) / "old.dwg"))
        self.window._new_path.set(str(Path(self.temp.name) / "new.dwg"))
        self.callback_errors = []
        self.window.report_callback_exception = lambda *error: self.callback_errors.append(error)

    def tearDown(self):
        self.window.destroy()
        self.window._view_executor.shutdown(wait=True)
        if self.window._worker:
            self.window._worker.join(timeout=5)
        self.temp.cleanup()
        self.raise_patch.stop()
        self.window = None
        # Tk objects must be collected on this owning thread, not incidentally
        # by the next test's PDF worker during a large Pillow allocation.
        gc.collect()
        self.assertFalse(self.callback_errors, self.callback_errors)

    def pump_until(self, predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if predicate():
                return
            time.sleep(0.01)
        self.fail(f"Window did not settle: {self.window._status.get()}")

    def render_fixture(self, old, new, directory, **kwargs):
        common = "0 0 0 RG 1 w 100 100 400 300 re S\n"
        pdfs = (make_pdf(common + "120 170 m 320 170 l S"),
                make_pdf(common + "120 300 m 320 300 l S"))
        result = {"warnings": []}
        for name, data in zip(("old", "new"), pdfs):
            pdf = Path(directory) / f"{name}.pdf"
            png = Path(directory) / f"{name}.png"
            pdf.write_bytes(data)
            render_pdf_viewport(data, (512, 359), 1, (0, 0), reference_width=512).save(png)
            result[f"{name}_pdf"], result[f"{name}_png"] = str(pdf), str(png)
        self.generated_directory = Path(directory)
        return result

    def load_fixture(self):
        with patch("dwg_preview.render_dwg_pair", side_effect=self.render_fixture):
            self.window._start_render()
            self.pump_until(lambda: self.window._display_signature == self.window._view_signature())

    def test_worker_loads_vectors_detects_both_directions_and_cleans_temps(self):
        self.load_fixture()
        self.assertEqual(self.window._mode.get(), "差異疊圖")
        self.assertGreater(self.window._viewport_diff["added_pixels"], 0)
        self.assertGreater(self.window._viewport_diff["removed_pixels"], 0)
        self.assertEqual(len(self.window._region_tree.get_children()), len(self.window._regions))
        self.assertFalse(self.generated_directory.exists())
        self.assertTrue(all(self.window._pdfs))

    def test_swap_reverses_red_and_green_and_preserves_vectors(self):
        self.load_fixture()
        old_pdfs = self.window._pdfs
        old_cache = self.window._tile_cache
        before = self.window._viewport_diff
        self.window._swap_files()
        self.pump_until(lambda: self.window._display_signature == self.window._view_signature())
        self.assertEqual(self.window._pdfs, old_pdfs[::-1])
        self.assertIsNot(self.window._tile_cache, old_cache)
        self.assertIsNone(ImageChops.difference(before["added_mask"], self.window._viewport_diff["removed_mask"]).getbbox())
        self.assertIsNone(ImageChops.difference(before["removed_mask"], self.window._viewport_diff["added_mask"]).getbbox())

    def test_region_selection_zoom_and_rapid_pan_use_latest_view(self):
        self.load_fixture()
        self.window._region_tree.selection_set("0")
        self.pump_until(lambda: self.window._selected_region == 0)
        for index in range(12):
            self.window._zoom(1.03, (200 + index, 220))
        self.pump_until(lambda: self.window._display_signature == self.window._view_signature()
                        and self.window._display_signature[5] == 2)
        self.assertEqual(self.window._selected_region, 0)
        self.assertEqual(self.window._viewport_diff["overlay"].size, (720, 480))
        self.window._fit()
        self.pump_until(lambda: self.window._display_signature == self.window._view_signature())
        self.assertIsNone(self.window._selected_region)

    def test_view_modes_and_new_file_clear_obsolete_result(self):
        self.load_fixture()
        for mode in ("左右滑桿", "舊版原圖", "新版原圖", "差異疊圖"):
            self.window._mode.set(mode)
            self.window._on_mode(mode)
            self.pump_until(lambda: self.window._frame_matches(self.window._view_signature()))
            self.window._draw()
            self.assertIsNotNone(self.window._photo)
            self.assertEqual(self.window._viewport_diff["mode"], self.window._mode_key())
            if mode == "差異疊圖":
                self.assertNotIn("old_overlay", self.window._viewport_diff)
            if mode in ("舊版原圖", "新版原圖"):
                self.assertNotIn("overlay", self.window._viewport_diff)
                self.assertIsNone(self.window._views[1 if mode == "舊版原圖" else 0])
        self.window.set_new_path(str(Path(self.temp.name) / "third.dwg"))
        self.assertIsNone(self.window._pdfs)
        self.assertIsNone(self.window._viewport_diff)
        self.assertIsNone(self.window._tile_cache)
        self.assertFalse(self.window._regions)

    def test_panning_moves_cached_photo_without_waiting_for_worker(self):
        self.load_fixture()
        self.window._draw()
        cached = self.window._sharp_photo
        signature = self.window._display_signature
        # Simulate a slow in-flight render: interaction must not wait for it.
        self.window._viewport_pending = signature
        self.window._on_press(SimpleNamespace(x=160, y=100))
        start = time.perf_counter()
        self.window._on_drag(SimpleNamespace(x=210, y=130))
        self.window._draw()
        elapsed = (time.perf_counter() - start) * 1000
        self.assertIs(self.window._photo, cached)
        self.assertEqual(self.window._canvas.coords(self.window._image_item), [50.0, 30.0])
        self.assertEqual(self.window._display_signature, signature)
        print(f"\nCached pan handler + draw: {elapsed:.2f} ms")

    def test_smooth_zoom_accumulates_ticks_and_preserves_cursor_anchor(self):
        self.load_fixture()
        scale, offset = self.window._scale, self.window._offset
        anchor = (250, 230)
        point = tuple((a-o)/scale for a, o in zip(anchor, offset))
        for _ in range(4):
            self.window._zoom(1.18, anchor, smooth=True)
        self.assertAlmostEqual(self.window._zoom_target[0], scale * 1.18**4)
        self.assertAlmostEqual(self.window._scale, scale)  # No immediate jump.
        self.pump_until(lambda: self.window._zoom_target is None)
        for p, a, o in zip(point, anchor, self.window._offset):
            self.assertAlmostEqual(p*self.window._scale + o, a, places=6)
        self.pump_until(lambda: self.window._display_signature == self.window._view_signature())

    def test_difference_navigation_wraps_and_numbers_are_drawn(self):
        self.load_fixture()
        self.window._step_region(1)
        self.pump_until(lambda: self.window._selected_region == 0)
        self.window._step_region(-1)
        self.pump_until(lambda: self.window._selected_region == len(self.window._regions)-1)
        self.pump_until(lambda: self.window._display_signature == self.window._view_signature())
        self.window._draw()
        labels = [self.window._canvas.itemcget(item, "text")
                  for item in self.window._canvas.find_withtag("overlay")
                  if self.window._canvas.type(item) == "text"]
        self.assertIn(f"{len(self.window._regions):02d}", labels)

    def test_pan_uses_fast_tiles_then_refines_after_release(self):
        self.load_fixture()
        self.window._on_press(SimpleNamespace(x=160, y=100))
        self.window._on_drag(SimpleNamespace(x=90, y=120))
        self.pump_until(lambda: self.window._display_signature == self.window._view_signature())
        self.assertEqual(self.window._display_signature[5], 1)
        self.assertEqual(self.window._viewport_diff["render_quality"], 1)
        self.window._on_release(SimpleNamespace(x=90, y=120))
        self.pump_until(lambda: self.window._display_signature == self.window._view_signature()
                        and self.window._display_signature[5] == 2)

    def test_rapid_mode_switch_and_cancel_do_not_display_obsolete_layers(self):
        self.load_fixture()
        self.window._mode.set("左右滑桿")
        self.window._on_mode()
        self.window._draw()
        self.window._mode.set("新版原圖")
        self.window._on_mode()
        self.assertTrue(self.window._viewport_cancel.is_set())
        self.pump_until(lambda: self.window._frame_matches(self.window._view_signature()))
        self.window._draw()
        self.assertEqual(self.window._viewport_diff["mode"], "new")
        self.assertIsNone(self.window._views[0])
        self.assertIsNotNone(self.window._views[1])

    def test_zoom_beyond_800_percent_keeps_anchor_and_renders_sharp_frame(self):
        self.load_fixture()
        anchor = (250, 230)
        point = tuple((a-o)/self.window._scale for a, o in zip(anchor, self.window._offset))
        self.window._zoom(64 / self.window._scale, anchor, smooth=True)
        self.pump_until(lambda: self.window._zoom_target is None)
        self.assertAlmostEqual(self.window._scale, 64)
        for p, a, o in zip(point, anchor, self.window._offset):
            self.assertAlmostEqual(p*self.window._scale + o, a, places=6)
        self.pump_until(lambda: self.window._frame_matches(self.window._view_signature())
                        and self.window._display_signature[5] == 2)
        self.assertIsNone(self.window._failed_signature)

    def test_manual_percentage_and_invalid_input(self):
        self.load_fixture()
        self.window._zoom_text.set("3,200%")
        self.window._on_zoom_entry()
        self.pump_until(lambda: self.window._zoom_target is None)
        self.assertAlmostEqual(self.window._scale, 32)
        for value in ("oops", "0", "-100", "nan", "inf"):
            self.window._zoom_text.set(value)
            self.window._on_zoom_entry()
            self.assertAlmostEqual(self.window._scale, 32)
            self.assertIn("請輸入", self.window._status.get())

    def test_zoom_safety_limit_accounts_for_refinement_and_tall_drawings(self):
        from pdf_viewport import max_view_scale, MAX_RENDER_SPAN
        self.load_fixture()
        self.window._zoom(1e100, (250, 230))
        self.assertEqual(self.window._scale, max_view_scale(self.window._originals[0].size))
        self.assertLess(self.window._scale * max(self.window._originals[0].size) * 2, MAX_RENDER_SPAN)
        self.assertIn("安全放大", self.window._status.get())
        self.assertLess(max_view_scale((4096, 8000)), max_view_scale((4096, 2896)))

    def test_small_region_focus_no_longer_caps_at_800_percent(self):
        self.load_fixture()
        self.window._regions = [{"bbox": [100, 100, 104, 104], "kind": "added",
                                 "added_pixels": 1, "removed_pixels": 0}]
        self.window._populate_regions()
        self.window._region_tree.selection_set("0")
        self.window._on_region()
        self.assertGreater(self.window._scale, 8)

    def test_fullscreen_hides_chrome_reuses_pair_cache_and_restores_layout(self):
        self.load_fixture()
        win = self.window
        win._zoom(2)
        self.pump_until(lambda: win._frame_matches(win._view_signature()))
        paths = (win._old_path.get(), win._new_path.get())
        pdfs, cache, generation = win._pdfs, win._tile_cache, win._generation
        win._mode.set("左右滑桿")
        win._on_mode()
        scale = win._scale
        point = tuple((n/2-o)/scale for n, o in zip(win._canvas_size, win._offset))
        with patch.object(win, "attributes") as attributes, patch.object(win, "state", return_value="normal"), \
                patch.object(win, "geometry", return_value="1320x900+50+60") as geometry:
            win.set_fullscreen(True)
            attributes.assert_called_with("-fullscreen", True)
            for panel in (win._header, win._files_panel, win._toolbar, win._modes_panel,
                          win._footer, win._sidebar, win._swipe_bar):
                self.assertEqual(panel.winfo_manager(), "")
            self.assertEqual(win._focus_bar.winfo_manager(), "grid")
            self.assertEqual(win._focus_status.winfo_manager(), "grid")
            win._on_resize(SimpleNamespace(width=1920, height=1000))
            self.assertAlmostEqual(win._scale, scale)
            for p, n, o in zip(point, win._canvas_size, win._offset):
                self.assertAlmostEqual(p*scale+o, n/2)
            win._toggle_fullscreen_sidebar()
            self.assertEqual(win._sidebar.winfo_manager(), "grid")
            self.assertEqual(win._focus_sidebar_button.cget("text"), "收合清單")
            win._exit_fullscreen()
            attributes.assert_called_with("-fullscreen", False)
            geometry.assert_called_with("1320x900+50+60")
            for panel in (win._header, win._files_panel, win._toolbar, win._modes_panel,
                          win._footer, win._sidebar, win._swipe_bar):
                self.assertEqual(panel.winfo_manager(), "grid")
            self.assertEqual(win._focus_bar.winfo_manager(), "")
        self.assertEqual((win._old_path.get(), win._new_path.get()), paths)
        self.assertIs(win._pdfs, pdfs)
        self.assertIs(win._tile_cache, cache)
        self.assertEqual(win._generation, generation)

    def test_fullscreen_navigation_zoom_and_mode_switch_work_without_sidebar(self):
        self.load_fixture()
        win = self.window
        with patch.object(win, "attributes"):
            win.set_fullscreen(True)
            win._step_region(1)
            self.pump_until(lambda: win._selected_region == 0)
            self.assertTrue(win._focus_region_label.cget("text").startswith("1 /"))
            win._zoom_text.set("3200%")
            win._on_zoom_entry()
            self.pump_until(lambda: win._zoom_target is None)
            self.assertAlmostEqual(win._scale, 32)
            win._mode.set("左右滑桿")
            win._on_mode()
            self.assertEqual(win._swipe_bar.winfo_manager(), "")
            self.pump_until(lambda: win._frame_matches(win._view_signature()))
            self.assertIn("old_overlay", win._viewport_diff)
            win._on_slider(.7)
            self.assertAlmostEqual(win._fraction, .7)
            win._toggle_fullscreen()
            self.assertFalse(win._fullscreen)
            self.assertEqual(win._swipe_bar.winfo_manager(), "grid")

    def test_fullscreen_escape_noop_and_repeat_preserve_maximized_state(self):
        win = self.window
        self.assertIsNone(win._exit_fullscreen())
        with patch.object(win, "attributes") as attributes, patch.object(win, "state", return_value="zoomed") as state:
            win.set_fullscreen(True)
            win.set_fullscreen(True)
            self.assertEqual(attributes.call_count, 1)
            win._exit_fullscreen()
            state.assert_called_with("zoomed")
            self.assertEqual(win._focus_zoom_entry.cget("state"), "disabled")

    def test_cancel_is_available_while_loading_in_fullscreen(self):
        win = self.window
        win._busy = True
        win._set_controls()
        self.assertEqual(win._focus_cancel.winfo_manager(), "pack")
        win._cancel_render()
        self.assertTrue(win._cancel_event.is_set())
        self.assertEqual(win._focus_cancel.cget("state"), "disabled")
        win._busy = False
        win._set_controls()
        self.assertEqual(win._focus_cancel.winfo_manager(), "")

    def test_zoom_requests_destination_before_animation_finishes(self):
        self.load_fixture()
        win = self.window
        win._zoom(1.18, (250,230), smooth=True)
        target_scale, target_offset = win._zoom_target
        self.assertNotEqual(win._scale, target_scale)
        desired = win._render_signature()
        self.assertEqual(desired[2], target_scale)
        self.assertEqual(desired[3], tuple(round(n) for n in target_offset))
        self.assertEqual(desired[5], 1)
        win._last_refine = 0
        with patch.object(win, "_request_viewport") as request:
            win._draw()
            request.assert_called_with(desired)

    def test_idle_jump_still_gets_readable_vectors_before_antialias_refinement(self):
        self.load_fixture()
        win = self.window
        win._scale *= 2
        win._last_interaction = 0
        self.assertEqual(win._view_signature()[5], 2)
        self.assertEqual(win._render_signature()[5], 1)
        win._display_signature = win._render_signature()
        self.assertEqual(win._render_signature()[5], 2)

    def test_fast_pass_failure_is_reported_instead_of_retrying_forever(self):
        self.load_fixture()
        win = self.window
        win._scale *= 2
        win._last_interaction = 0
        signature = win._render_signature()
        win._events.put((win._job_id, "viewport", (signature,None,None,"test render failure")))
        win.after_cancel(win._poll_id)
        win._poll_id = None
        win._poll_worker()
        self.assertEqual(win._failed_signature, win._view_signature())

    def test_readable_vectors_are_displayed_while_high_quality_pass_is_pending(self):
        from comparison_tiles import ComparisonTileCache
        self.load_fixture()
        win = self.window
        high_quality_started = threading.Event()
        release = threading.Event()
        original = ComparisonTileCache.render

        def controlled(cache, size, scale, offset, mode="overlay", quality=2, cancel_event=None):
            if quality == 2:
                high_quality_started.set()
                release.wait(timeout=5)
            return original(cache, size, scale, offset, mode, quality, cancel_event)

        with patch.object(ComparisonTileCache, "render", controlled):
            try:
                win._scale *= 2
                win._last_interaction = 0
                win._schedule_render()
                self.pump_until(lambda: high_quality_started.is_set())
                self.assertEqual(win._display_signature[:5], win._view_signature()[:5])
                self.assertEqual(win._display_signature[5], 1)
                self.assertIsNotNone(win._photo)
            finally:
                release.set()
            self.pump_until(lambda: win._frame_matches(win._view_signature()))


if __name__ == "__main__":
    unittest.main()
