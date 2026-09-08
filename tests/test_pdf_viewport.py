"""PDF-vector viewport geometry, sharpness, memory bounds and threading checks."""

from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch

from PIL import Image, ImageChops
import pypdfium2 as pdfium

from pdf_viewport import PDFIUM_LOCK, render_pdf_viewport


def make_pdf(commands, width=1000, height=700):
    """Small in-memory vector PDF fixture; no optional PDF authoring library."""
    stream = commands.encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
         "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>").encode("ascii"),
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    result = b"%PDF-1.4\n"
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(result)
    result += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets[1:]:
        result += f"{offset:010} 00000 n \n".encode()
    result += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
               f"startxref\n{xref}\n%%EOF\n").encode()
    return result


class PdfViewportTests(unittest.TestCase):
    def setUp(self):
        self.pdf = make_pdf("0 0 0 rg 100 400 50 80 re f\n0 0 0 RG 0.2 w 0 650 m 1000 650 l S")

    def test_full_page_matches_native_reference_raster(self):
        with PDFIUM_LOCK, pdfium.PdfDocument(self.pdf) as doc:
            page = doc[0]
            bitmap = page.render(scale=4.096)
            expected = bitmap.to_pil().convert("RGB").copy()
            bitmap.close()
            page.close()
        actual = render_pdf_viewport(self.pdf, expected.size, 1, (0, 0))
        self.assertIsNone(ImageChops.difference(expected, actual).getbbox())

    def test_negative_offset_crop_matches_full_vector_render(self):
        full = render_pdf_viewport(self.pdf, (2000, 1400), 2, (0, 0), reference_width=1000)
        cropped = render_pdf_viewport(self.pdf, (300, 250), 2, (-180, -380), reference_width=1000)
        self.assertIsNone(ImageChops.difference(full.crop((180, 380, 480, 630)), cropped).getbbox())

    def test_positive_offset_is_white_padding(self):
        result = render_pdf_viewport(self.pdf, (400, 400), 1, (40, 60), reference_width=1000)
        self.assertEqual(result.getpixel((0, 0)), (255, 255, 255))
        self.assertEqual(result.getpixel((140, 280)), (0, 0, 0))
        self.assertEqual(result.crop((0, 0, 40, 400)).getextrema(), ((255, 255),) * 3)

    def test_high_zoom_only_allocates_viewport_bitmap(self):
        allocations = []
        original = pdfium.PdfBitmap.new_native

        def traced(width, height, **kwargs):
            allocations.append((width, height))
            return original(width, height, **kwargs)

        with patch.object(pdfium.PdfBitmap, "new_native", side_effect=traced):
            result = render_pdf_viewport(self.pdf, (640, 480), 1000, (-409600, -901120))
        self.assertEqual(result.size, (640, 480))
        self.assertEqual(allocations, [(640, 480)])

    def test_tiny_text_is_rerendered_sharply_at_high_zoom(self):
        text = make_pdf("0 0 0 rg BT /F1 0.8 Tf 20 550 Td (Revision 2 - DETAIL) Tj ET")
        actual = render_pdf_viewport(text, (600, 150), 40, (-720, -5920), reference_width=1000)
        base = render_pdf_viewport(text, (1000, 700), 1, (0, 0), reference_width=1000)
        enlarged = base.transform((600, 150), Image.Transform.AFFINE,
                                  (1 / 40, 0, 18, 0, 1 / 40, 148),
                                  Image.Resampling.BICUBIC, fillcolor="white")
        # Vector glyphs have resolved, black interiors. Enlarging the subpixel
        # overview glyphs cannot recover those interiors or separated strokes.
        actual_black = sum(count for count, value in actual.convert("L").getcolors(256) if value < 32)
        enlarged_black = sum(count for count, value in enlarged.convert("L").getcolors(256) if value < 32)
        self.assertGreater(actual_black, 300)
        self.assertGreater(actual_black, enlarged_black * 3)

    def test_close_parallel_lines_resolve_at_high_zoom(self):
        pdf = make_pdf("0 0 0 RG 0.05 w 20 550 m 30 550 l S 20 549.75 m 30 549.75 l S")
        result = render_pdf_viewport(pdf, (500, 120), 40, (-760, -5980), reference_width=1000)
        gray = result.convert("L")
        rows = [gray.getpixel((120, y)) for y in range(10, 40)]
        self.assertLess(min(rows[:15]), 64)
        self.assertLess(min(rows[15:]), 64)
        self.assertGreater(rows[15], 240)

    def test_two_versions_keep_common_geometry_at_high_zoom(self):
        old = make_pdf("0 0 0 rg 100 400 50 80 re f")
        new = make_pdf("0 0 0 rg 100 400 50 80 re f 160 420 3 3 re f")
        first = render_pdf_viewport(old, (800, 600), 8, (-750, -1700), reference_width=1000)
        second = render_pdf_viewport(new, (800, 600), 8, (-750, -1700), reference_width=1000)
        difference = ImageChops.difference(first, second).getbbox()
        self.assertEqual(difference, (530, 516, 554, 540))

    def test_offscreen_and_extreme_zoom_boundaries(self):
        result = render_pdf_viewport(self.pdf, (20, 30), 1, (-1e100, -1e100))
        self.assertEqual(result.getextrema(), ((255, 255),) * 3)
        tiny = render_pdf_viewport(self.pdf, (20, 30), 1e-15, (0, 0))
        self.assertEqual(tiny.getextrema(), ((255, 255),) * 3)
        for scale in (0, -1, float("inf"), float("nan"), 1e100):
            with self.subTest(scale=scale), self.assertRaises(ValueError):
                render_pdf_viewport(self.pdf, (20, 30), scale, (0, 0))
        with self.assertRaises(ValueError):
            render_pdf_viewport(self.pdf, (100000, 100000), 1, (0, 0))

    def test_multiple_threads_return_independent_images(self):
        expected = render_pdf_viewport(self.pdf, (320, 200), 2, (-180, -380), reference_width=1000).tobytes()
        with ThreadPoolExecutor(max_workers=4) as pool:
            images = list(pool.map(lambda _: render_pdf_viewport(
                self.pdf, (320, 200), 2, (-180, -380), reference_width=1000), range(8)))
        for image in images:
            self.assertEqual(image.tobytes(), expected)


if __name__ == "__main__":
    unittest.main()
