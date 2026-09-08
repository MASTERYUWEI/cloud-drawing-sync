"""Measure input-to-readable-vector latency in Tk with saved local previews.

Uses a hidden test window: timings include Tk processing/image upload but are
not screen FPS, OS mouse-to-display latency, or a substitute for visual QA.
"""
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import customtkinter as ctk
from dwg_compare_window import DwgCompareWindow, _read_preview


def main():
    report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    root = ctk.CTk()
    root.withdraw()
    window = DwgCompareWindow(root)
    window.withdraw()
    window._raise_window = lambda: None
    window._canvas_size = (1918, 1002)
    window._originals = tuple(_read_preview(report[k]) for k in ("new_png", "old_png"))
    window._pdfs = tuple(Path(report[k]).read_bytes() for k in ("new_pdf", "old_pdf"))
    window._mode.set("左右滑桿")
    window._generation += 1
    window._scale = 1.5
    window._offset = (959-296*1.5, 501-1864*1.5)
    window._fit_mode = False
    window._set_controls()
    window._schedule_render()

    def settled():
        return (window._zoom_target is None and window._frame_matches(window._view_signature())
                and window._display_signature[5] == 2)

    def pump(predicate, timeout=15):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            root.update()
            if window._failed_signature:
                raise RuntimeError(window._status.get())
            if predicate():
                return
            time.sleep(.002)
        raise RuntimeError("Zoom benchmark did not settle")

    try:
        pump(settled)
        for target in (1.77, 1.5, 8, 64):
            start = time.perf_counter()
            window._zoom(target/window._scale, (959, 501), smooth=True)
            pump(lambda: window._zoom_target is None and window._display_signature is not None
                 and window._display_signature[:5] == window._view_signature()[:5])
            readable = (time.perf_counter()-start)*1000
            quality = window._display_signature[5]
            pump(settled)
            print(json.dumps({"target_percent": target*100, "readable_ms": round(readable,2),
                              "first_quality": quality, "final_ms": round((time.perf_counter()-start)*1000,2)}), flush=True)
    finally:
        window.destroy()
        window._view_executor.shutdown(wait=True)
        root.destroy()


if __name__ == "__main__":
    main()
