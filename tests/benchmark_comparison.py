"""Measure a generated DWG pair; no user drawings or GUI automation."""
from pathlib import Path
import json
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from drawing_diff import compare_previews
from pdf_viewport import render_pdf_viewport
from dwg_compare_window import render_viewport
from comparison_view import reproject_image, render_comparison_frame
from comparison_tiles import ComparisonTileCache


def measure(fn, count=12):
    values = []
    result = None
    for _ in range(count):
        start = time.perf_counter()
        result = fn()
        values.append((time.perf_counter() - start) * 1000)
    return result, {"median_ms": round(statistics.median(values), 2),
                    "max_ms": round(max(values), 2)}


def main():
    report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    pdfs = [Path(report[key]).read_bytes() for key in ("old_pdf", "new_pdf")]
    size, scale, offset = (1060, 600), 0.2, (10, 10)
    views, vector = measure(lambda: tuple(render_pdf_viewport(p, size, scale, offset) for p in pdfs))
    changes, diff = measure(lambda: compare_previews(*views, tolerance=0, include_regions=False))
    _, warp = measure(lambda: render_viewport(changes["overlay"], size, 1.05, (-20, -15)))
    _, fast_zoom = measure(lambda: reproject_image(changes["overlay"], size, 1, (0, 0), 1.05, (-20, -15)))
    _, still = measure(lambda: render_comparison_frame(pdfs, size, scale, offset), count=5)
    cache = ComparisonTileCache(pdfs)
    cold_start = time.perf_counter()
    _, cold = cache.render(size, scale, offset)
    cold_ms = (time.perf_counter()-cold_start)*1000
    (_, warm), warm_time = measure(lambda: cache.render(size, scale, offset))
    pan_start = time.perf_counter()
    _, panned = cache.render(size, scale, (offset[0]-80, offset[1]))
    pan_ms = (time.perf_counter()-pan_start)*1000
    pan_start = time.perf_counter()
    _, exposed = cache.render(size, scale, (offset[0]-400, offset[1]))
    exposed_ms = (time.perf_counter()-pan_start)*1000
    pan_start = time.perf_counter()
    _, interactive = cache.render(size, scale, (offset[0]-450, offset[1]), quality=1)
    interactive_ms = (time.perf_counter()-pan_start)*1000
    print(json.dumps({"size": size, "vector_pair": vector, "pixel_diff": diff, "old_cached_warp": warp,
                      "interactive_zoom": fast_zoom, "antialiased_still_frame": still,
                      "tiles_cold": {"ms":round(cold_ms,2), **cold["cache_stats"]},
                      "tiles_revisit": {**warm_time, **warm["cache_stats"]},
                      "tiles_pan_80px": {"ms":round(pan_ms,2), **panned["cache_stats"]},
                      "tiles_pan_400px_new_area": {"ms":round(exposed_ms,2), **exposed["cache_stats"]},
                      "tiles_interactive_pan": {"ms":round(interactive_ms,2), **interactive["cache_stats"]}}, indent=2))


if __name__ == "__main__":
    main()
