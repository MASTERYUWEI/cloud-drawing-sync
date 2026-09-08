"""Bounded, world-anchored tile cache for the visible comparison mode only."""
from collections import OrderedDict
from contextlib import ExitStack
import math
import threading
from itertools import groupby

from PIL import Image
from drawing_diff import compare_visible_layers
from pdf_viewport import PdfViewportSession, MAX_VIEWPORT_EDGE, MAX_VIEWPORT_PIXELS


class TileRenderCancelled(Exception):
    """A newer interaction superseded this frame; completed tiles remain cached."""


class ComparisonTileCache:
    """One immutable DWG pair, serialized worker access, no retained native pages.

    Cache accounting covers rendered pixel buffers (96 MiB default). PDF pages
    are opened lazily once per cache-miss frame and always closed before return.
    Tile coordinates are anchored to the drawing, not to the current viewport.
    """
    def __init__(self, pdfs, reference_width=4096, tile_size=256, max_bytes=96*1024*1024):
        if len(pdfs) != 2 or tile_size < 16 or max_bytes < 0 or reference_width <= 0:
            raise ValueError("Invalid tile cache configuration.")
        self.pdfs = tuple(bytes(data) for data in pdfs)
        self.reference_width = reference_width
        self.tile_size = tile_size
        self.max_bytes = max_bytes
        self.cache_bytes = 0
        self.tiles = OrderedDict()
        self._lock = threading.Lock()

    def _batches(self, missing, quality):
        """Merge contiguous misses: one PDF traversal per strip, not per tile.

        Cap each native batch at four million sampled pixels. This bounds
        temporary buffers and lets a new zoom interrupt 2x work between strips.
        """
        tile, bleed = self.tile_size, 4
        budget = 4_000_000
        max_columns = max(1, min((MAX_VIEWPORT_EDGE//quality-2*bleed)//tile,
                                 int((budget/(quality**2*(tile+2*bleed))-2*bleed)//tile)))
        rectangles, active = [], {}
        for y, row in groupby(sorted(missing, key=lambda point: (point[1], point[0])), key=lambda p: p[1]):
            columns = [p[0] for p in row]
            spans = []
            for _, contiguous in groupby(enumerate(columns), key=lambda value: value[1]-value[0]):
                run = [x for _, x in contiguous]
                for index in range(0, len(run), max_columns):
                    part = run[index:index+max_columns]
                    spans.append((part[0], part[-1]+1))
            for x0, x1 in spans:
                previous = active.get((x0, x1))
                if previous is not None:
                    rectangle = rectangles[previous]
                    width = (x1-x0)*tile+2*bleed
                    height = (y+1-rectangle[1])*tile+2*bleed
                    if (rectangle[3] == y and width*height*quality**2 <= budget
                            and max(width, height)*quality <= MAX_VIEWPORT_EDGE):
                        rectangle[3] = y+1
                        continue
                active[(x0, x1)] = len(rectangles)
                rectangles.append([x0, y, x1, y+1])
        return rectangles

    def _remember(self, key, layers):
        cost = sum(im.width*im.height*len(im.getbands()) for im in layers.values())
        self.tiles[key] = (layers, cost)
        self.cache_bytes += cost
        while self.cache_bytes > self.max_bytes and self.tiles:
            _, (_, removed) = self.tiles.popitem(last=False)
            self.cache_bytes -= removed

    def render(self, size, scale, offset, mode="overlay", quality=2, cancel_event=None):
        if (mode not in ("overlay", "swipe", "old", "new") or quality not in (1, 2)
                or len(size) != 2 or any(type(n) is not int or n < 1 for n in size)
                or max(size) > MAX_VIEWPORT_EDGE or size[0]*size[1] > MAX_VIEWPORT_PIXELS
                or not math.isfinite(scale) or scale <= 0
                or len(offset) != 2 or not all(math.isfinite(n) for n in offset)):
            raise ValueError("Invalid tile viewport.")
        quality = quality if size[0]*size[1]*quality*quality <= 8_000_000 else 1
        scale = round(scale, 12)
        if scale <= 0:
            raise ValueError("Preview zoom is too small.")
        offset = tuple(round(value) for value in offset)
        with self._lock, ExitStack() as stack:
            sessions = {}
            hits = misses = native_tiles = classifications = classified_batches = 0

            def check_cancel():
                if cancel_event is not None and cancel_event.is_set():
                    raise TileRenderCancelled()

            def render_pdf(index, render_size, render_offset):
                nonlocal native_tiles
                check_cancel()
                if index not in sessions:
                    sessions[index] = stack.enter_context(PdfViewportSession(self.pdfs[index]))
                native_tiles += 1
                return sessions[index].render(render_size, scale*quality, render_offset,
                                               reference_width=self.reference_width)

            keys = {"overlay": ("overlay", "added_mask", "removed_mask"),
                    "swipe": ("old_overlay", "new_overlay", "added_mask", "removed_mask"),
                    "old": ("old_view",), "new": ("new_view",)}[mode]
            output = {key: Image.new("L" if key.endswith("mask") else "RGB", size,
                                      0 if key.endswith("mask") else "white") for key in keys}
            tile = self.tile_size
            bleed = 4
            missing = []

            def paste(layers, tx, ty):
                position = (tx*tile+offset[0], ty*tile+offset[1])
                for name, im in layers.items():
                    output[name].paste(im, position)

            for ty in range(math.floor(-offset[1]/tile), math.ceil((size[1]-offset[1])/tile)):
                for tx in range(math.floor(-offset[0]/tile), math.ceil((size[0]-offset[0])/tile)):
                    check_cancel()
                    key = (scale, quality, mode, tx, ty)
                    cached = self.tiles.get(key)
                    # A sharp tile also satisfies an interaction-quality request;
                    # panning must not redraw previously visited tiles at 1x.
                    if cached is None and quality == 1:
                        sharp_key = (scale, 2, mode, tx, ty)
                        cached = self.tiles.get(sharp_key)
                        if cached is not None:
                            key = sharp_key
                    if cached is not None:
                        hits += 1
                        layers = cached[0]
                        self.tiles.move_to_end(key)
                        paste(layers, tx, ty)
                    else:
                        misses += 1
                        missing.append((tx, ty))
            for x0, y0, x1, y1 in self._batches(missing, quality):
                check_cancel()
                extent = ((x1-x0)*tile+2*bleed, (y1-y0)*tile+2*bleed)
                render_size = tuple(n*quality for n in extent)
                render_offset = ((-x0*tile+bleed)*quality, (-y0*tile+bleed)*quality)
                if mode in ("old", "new"):
                    batch = {keys[0]: render_pdf(0 if mode == "old" else 1, render_size, render_offset)}
                else:
                    old = render_pdf(0, render_size, render_offset)
                    new = render_pdf(1, render_size, render_offset)
                    check_cancel()
                    batch = compare_visible_layers(old, new, mode)
                    classifications += (x1-x0)*(y1-y0)
                    classified_batches += 1
                    del old, new
                for name, im in batch.items():
                    check_cancel()
                    if quality > 1:
                        batch[name] = (im.reduce(quality).point([0]+[255]*255) if name.endswith("mask")
                                       else im.resize(extent, Image.Resampling.LANCZOS))
                # Split the completed strip back into world-anchored tiles so
                # later pans still reuse precisely the visible cached areas.
                for ty in range(y0, y1):
                    for tx in range(x0, x1):
                        check_cancel()
                        left, top = bleed+(tx-x0)*tile, bleed+(ty-y0)*tile
                        layers = {name: im.crop((left, top, left+tile, top+tile)) for name, im in batch.items()}
                        key = (scale, quality, mode, tx, ty)
                        self._remember(key, layers)
                        paste(layers, tx, ty)
                del batch
            check_cancel()
            views = (output.pop("old_view", None), output.pop("new_view", None))
            for kind in ("added", "removed"):
                if f"{kind}_mask" in output:
                    output[f"{kind}_pixels"] = output[f"{kind}_mask"].histogram()[255]
            output.update(render_quality=quality, mode=mode,
                          cache_stats={"hits": hits, "misses": misses, "native_tiles": native_tiles,
                                       "native_calls": native_tiles, "classified_batches": classified_batches,
                                       "classified_tiles": classifications, "bytes": self.cache_bytes,
                                       "entries": len(self.tiles), "limit_bytes": self.max_bytes})
            return views, output
