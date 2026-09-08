"""Cheap interactive reprojection and antialiased, vector-backed still frames."""
from PIL import Image


def reproject_image(image, size, source_scale, source_offset, scale, offset):
    """Move an already rendered frame immediately; never re-run the detector.

    Both versions use this identical affine transform. This transient frame is
    replaced by fresh vector output in the background, so it is not a zoom cap.
    """
    ratio = scale / source_scale
    dx = offset[0] - source_offset[0] * ratio
    dy = offset[1] - source_offset[1] * ratio
    if abs(ratio - 1) < 1e-8:
        result = Image.new("RGB", size, "white")
        result.paste(image, (round(dx), round(dy)))
        return result
    return image.transform(size, Image.Transform.AFFINE,
                           (1 / ratio, 0, -dx / ratio, 0, 1 / ratio, -dy / ratio),
                           resample=Image.Resampling.BILINEAR, fillcolor="white")


def render_comparison_frame(pdfs, size, scale, offset, reference_width=4096, quality=2):
    """Rasterize vectors at 2x then reduce colours with antialiasing.

    Detection is performed before reduction, preserving sub-screen-pixel detail.
    Bounded by visible canvas dimensions, not by the full drawing's zoom size.
    """
    from drawing_diff import compare_previews
    from pdf_viewport import render_pdf_viewport

    # Keep very large / multi-monitor windows within a predictable memory bound.
    factor = quality if size[0] * size[1] * quality * quality <= 8_000_000 else 1
    render_size = tuple(value * factor for value in size)
    render_offset = tuple(value * factor for value in offset)
    raw_views = tuple(render_pdf_viewport(data, render_size, scale * factor, render_offset,
                                          reference_width=reference_width) for data in pdfs)
    differences = compare_previews(*raw_views, tolerance=0, include_regions=False)
    if factor > 1:
        views = tuple(im.resize(size, Image.Resampling.LANCZOS) for im in raw_views)
        for key in ("overlay", "old_overlay", "new_overlay"):
            differences[key] = differences[key].resize(size, Image.Resampling.LANCZOS)
        # Keep binary masks in canvas coordinates for diagnostics and tests.
        for key in ("added_mask", "removed_mask"):
            differences[key] = differences[key].reduce(factor).point([0] + [255] * 255)
        for kind in ("added", "removed"):
            differences[f"sampled_{kind}_pixels"] = differences[f"{kind}_pixels"]
            differences[f"{kind}_pixels"] = differences[f"{kind}_mask"].histogram()[255]
    else:
        views = raw_views
    differences["render_quality"] = factor
    return views, differences
