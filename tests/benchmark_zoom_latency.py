"""Compare cold zoom redraw paths on saved local vector previews, not screen FPS."""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import statistics
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
import comparison_tiles
from comparison_tiles import ComparisonTileCache
from drawing_diff import compare_visible_layers
from pdf_viewport import PdfViewportSession


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("report")
    parser.add_argument("--output")
    parser.add_argument("--runs", type=int, default=2)
    options = parser.parse_args()
    report = json.loads(Path(options.report).read_text(encoding="utf-8"))
    # These saved previews predate the user's old/new correction. Order does
    # not affect speed, and no chronological claims are made by this benchmark.
    pdfs = tuple(Path(report[k]).read_bytes() for k in ("old_pdf", "new_pdf"))
    size = (1918, 1002)
    records = []
    for scale in (1.5, 8, 64):
        offset = (size[0]/2-296*scale, size[1]/2-1864*scale)
        for quality in (1, 2):
            values = []
            native_seconds = classify_seconds = 0
            native = PdfViewportSession.render
            classify = comparison_tiles.compare_visible_layers

            def timed_native(*args, **kwargs):
                nonlocal native_seconds
                start = time.perf_counter()
                try:
                    return native(*args, **kwargs)
                finally:
                    native_seconds += time.perf_counter()-start

            def timed_classify(*args, **kwargs):
                nonlocal classify_seconds
                start = time.perf_counter()
                try:
                    return classify(*args, **kwargs)
                finally:
                    classify_seconds += time.perf_counter()-start

            for _ in range(options.runs):
                cache = ComparisonTileCache(pdfs)
                start = time.perf_counter()
                with patch.object(PdfViewportSession, "render", timed_native), \
                        patch.object(comparison_tiles, "compare_visible_layers", timed_classify):
                    _, data = cache.render(size, scale, offset, mode="swipe", quality=quality)
                values.append((time.perf_counter()-start)*1000)
            record = {"scale": scale, "quality": quality,
                      "cache_cold_median_ms": round(statistics.median(values), 2),
                      "native_ms": round(native_seconds*1000/options.runs, 2),
                      "classify_ms": round(classify_seconds*1000/options.runs, 2),
                      "cache_stats": data["cache_stats"]}
            start = time.perf_counter()
            with ExitStack() as stack:
                sessions = [stack.enter_context(PdfViewportSession(p)) for p in pdfs]
                views = [s.render(tuple(n*quality for n in size), scale*quality,
                                   tuple(round(n)*quality for n in offset)) for s in sessions]
                layers = compare_visible_layers(*views, mode="swipe")
                if quality == 2:
                    layers = {k: (v.resize(size, Image.Resampling.LANCZOS) if not k.endswith("mask")
                                   else v.reduce(quality)) for k, v in layers.items()}
                record["single_viewport_ms"] = round((time.perf_counter()-start)*1000, 2)
            records.append(record)
            print(json.dumps(record), flush=True)
    if options.output:
        Path(options.output).write_text(json.dumps({"size": size, "records": records}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
