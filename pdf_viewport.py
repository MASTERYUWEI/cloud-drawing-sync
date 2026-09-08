"""Render just the visible PDF area from vectors, at the current zoom.

Coordinates match the viewer's 4096-pixel-wide reference PNG: image (0, 0)
appears at ``offset`` and every reference pixel occupies ``scale`` canvas
pixels. No enlarged whole-page raster is allocated, even at high zoom.
"""

from __future__ import annotations

import math
import threading
from contextlib import nullcontext

from PIL import Image


# PDFium is not thread-safe, even for different documents. All library calls,
# including native object closing, use this lock; dwg_preview shares it.
PDFIUM_LOCK = threading.RLock()
MAX_VIEWPORT_PIXELS = 32 * 1024 * 1024
MAX_VIEWPORT_EDGE = 16384
MAX_RENDER_SPAN = 16 * 1024 * 1024


def max_view_scale(reference_size, quality=2):
    """Native-coordinate safety bound, not an arbitrary UI zoom percentage.

    Reserve 2x refinement headroom even while interacting at 1x. Rendering
    remains tile-sized; this limit protects PDFium coordinate precision, not
    the amount of bitmap memory allocated. Leave a rounding margin for tiles.
    """
    if (len(reference_size) != 2 or not all(math.isfinite(n) and n > 0 for n in reference_size)
            or quality not in (1, 2)):
        raise ValueError("Invalid reference size or render quality.")
    return (MAX_RENDER_SPAN - 16) / (max(reference_size) * quality)


def render_pdf_viewport(pdf_bytes, size, scale, offset, reference_width=4096, *, _page=None):
    """Return a detached, white-backed RGB image exactly ``size`` pixels.

    ``pdf_bytes`` contains one page produced by the read-only DWG renderer.
    The original reference height is ceil(PDF height / width * reference_width),
    matching its PNG export. Zoom and pan therefore remain registered to that
    reference. Pan is snapped to the nearest physical pixel for crisp strokes.

    Uses PDFium's bitmap clipping instead of PdfPage.render(crop=...), whose
    independently rounded crop margins can move or shrink the requested tile.
    The native render API accepts a page rectangle larger than the destination
    bitmap, then draws only its visible portion into the canvas-sized bitmap.

    Thread-safe with this module and dwg_preview. Raises ValueError for invalid
    inputs or unsupported page counts, and PDFium errors for invalid PDFs.
    """
    if (not isinstance(size, (tuple, list)) or len(size) != 2
            or any(isinstance(n, bool) or not isinstance(n, int) for n in size)):
        raise ValueError("Viewport size must contain two integer pixel dimensions.")
    width, height = size
    if (width < 1 or height < 1 or max(size) > MAX_VIEWPORT_EDGE
            or width * height > MAX_VIEWPORT_PIXELS):
        raise ValueError("Viewport dimensions exceed the supported canvas size.")
    try:
        scale = float(scale)
        x, y = (float(value) for value in offset)
        reference_width = float(reference_width)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("Preview scale and offset must be finite numbers.") from exc
    if (not all(math.isfinite(v) for v in (scale, x, y, reference_width))
            or scale <= 0 or reference_width <= 0):
        raise ValueError("Preview scale and reference width must be positive and finite.")
    if not isinstance(pdf_bytes, (bytes, bytearray, memoryview)) or not pdf_bytes:
        raise ValueError("PDF content must be non-empty bytes.")

    # Import and all native resource lifetimes remain under the same lock.
    with PDFIUM_LOCK:
        import pypdfium2 as pdfium
        import pypdfium2.raw as raw

        with (nullcontext(None) if _page is not None else pdfium.PdfDocument(bytes(pdf_bytes))) as document:
            if _page is None and len(document) != 1:
                raise ValueError("DWG comparison requires a single-page PDF preview.")
            page = _page if _page is not None else document[0]
            try:
                page_width, page_height = page.get_size()
                if (not math.isfinite(page_width) or not math.isfinite(page_height)
                        or min(page_width, page_height) <= 0):
                    raise ValueError("PDF page dimensions are invalid.")
                reference_height = math.ceil(page_height * reference_width / page_width)
                scaled_width = reference_width * scale
                scaled_height = reference_height * scale
                if (not math.isfinite(scaled_width) or not math.isfinite(scaled_height)
                        or max(scaled_width, scaled_height) > MAX_RENDER_SPAN):
                    raise ValueError("Preview zoom exceeds the supported range.")
                # An entirely invisible or subpixel page requires no PDF raster.
                if (x >= width or y >= height or x + scaled_width <= 0
                        or y + scaled_height <= 0
                        or min(scaled_width, scaled_height) < 0.5):
                    return Image.new("RGB", size, "white")
                target_width = max(1, round(scaled_width))
                target_height = max(1, round(scaled_height))
                bitmap = pdfium.PdfBitmap.new_native(
                    width, height, format=raw.FPDFBitmap_BGR, rev_byteorder=False)
                try:
                    # Keep the helper-independent native call compatible with
                    # pypdfium2 4.x/5.x fill_rect argument ordering differences.
                    raw.FPDFBitmap_FillRect(bitmap, 0, 0, width, height, 0xFFFFFFFF)
                    raw.FPDF_RenderPageBitmap(
                        bitmap, page, round(x), round(y), target_width, target_height,
                        0, raw.FPDF_ANNOT | raw.FPDF_RENDER_LIMITEDIMAGECACHE)
                    # Copy before releasing PDFium's memory: the returned PIL
                    # object can safely outlive its document and worker thread.
                    shared_image = bitmap.to_pil()
                    try:
                        return shared_image.convert("RGB").copy()
                    finally:
                        shared_image.close()
                finally:
                    bitmap.close()
            finally:
                if _page is None:
                    page.close()


class PdfViewportSession:
    """Reuse one parsed page across a frame's tiles; close on the worker thread."""
    def __init__(self, pdf_bytes):
        with PDFIUM_LOCK:
            import pypdfium2 as pdfium
            self.document = pdfium.PdfDocument(pdf_bytes)
            try:
                if len(self.document) != 1:
                    raise ValueError("DWG comparison requires a single-page PDF preview.")
                self.page = self.document[0]
            except Exception:
                self.document.close()
                raise
        self._closed = False

    def render(self, size, scale, offset, reference_width=4096):
        with PDFIUM_LOCK:
            if self._closed:
                raise ValueError("PDF viewport session is already closed.")
            return render_pdf_viewport(b"session", size, scale, offset,
                                       reference_width, _page=self.page)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        with PDFIUM_LOCK:
            if not self._closed:
                self._closed = True
                self.page.close()
                self.document.close()
