"""Colour visual line changes between two already registered drawing previews.

This compares raster ink, not CAD entities: counts are pixels / visual regions,
never lines, blocks or objects. Inputs must have the same world-to-pixel mapping.
The caller owns every returned image and should close it when no longer needed.
"""
from collections import deque
import math

from PIL import Image, ImageChops, ImageFilter


ADDED_COLOUR = (0, 145, 65)
REMOVED_COLOUR = (220, 40, 50)
COMMON_GREY = 110
INK_THRESHOLD = 40
MAX_REGIONS = 200


def _ink(image):
    """Use darkest RGB channel so yellow/cyan drawing lines are not discarded."""
    if image.mode != 'RGB':
        if 'A' in image.getbands():
            rgba = image.convert('RGBA')
            white = Image.new('RGBA', image.size, 'white')
            white.alpha_composite(rgba)
            image = white.convert('RGB')
        else:
            image = image.convert('RGB')
    red, green, blue = image.split()
    return ImageChops.invert(ImageChops.darker(ImageChops.darker(red, green), blue))


def _foreground(ink):
    # Very faint fringe pixels are usually plot/raster antialiasing. Bright
    # saturated CAD colours still have strong ink in at least one channel.
    return ink.point([0 if value < INK_THRESHOLD else 255 for value in range(256)])


def _neutral(ink):
    return ink.point([255 - round(value * (255 - COMMON_GREY) / 255)
                      for value in range(256)]).convert('RGB')


def _regions(added, removed):
    """Connected components on a <=256-ish-cell-wide occupancy grid.

    Pillow reduction keeps even a single changed pixel (cell area <=256).
    Full-resolution label histograms provide exact counts without a Python
    loop over millions of pixels. Region boxes are grid-rounded image pixels.
    Only the list is capped; neither change mask is filtered or capped.
    """
    width, height = added.size
    cell = max(4, min(16, math.ceil(max(width, height) / 256)))
    occupancy = ImageChops.lighter(added, removed).reduce(cell)
    # One empty grid cell may separate segments of the same local change.
    # Bridge that gap for a manageable navigation list, not for the ink masks.
    occupied = occupancy.point([0] + [255] * 255)
    neighbours = occupied.filter(ImageFilter.MaxFilter(3))
    grid_width, grid_height = occupancy.size
    active = occupied.tobytes()
    pending = bytearray(neighbours.tobytes())
    components = []
    for start in range(len(pending)):
        if not pending[start]:
            continue
        queue = deque([start])
        pending[start] = 0
        cells = []
        while queue:
            index = queue.popleft()
            if active[index]:
                cells.append(index)
            x, y = index % grid_width, index // grid_width
            for ny in range(max(0, y - 1), min(grid_height, y + 2)):
                for nx in range(max(0, x - 1), min(grid_width, x + 2)):
                    neighbour = ny * grid_width + nx
                    if pending[neighbour]:
                        pending[neighbour] = 0
                        queue.append(neighbour)
        if cells:
            components.append(cells)

    total_regions = len(components)
    # Prefer spatially substantial changes if a very fragmented drawing has
    # more entries than the navigation UI can sensibly display.
    components.sort(key=lambda cells: (-len(cells), min(cells)))
    selected = components[:MAX_REGIONS]
    selected.sort(key=min)
    labels = bytearray(grid_width * grid_height)
    regions = []
    for label, cells in enumerate(selected, 1):
        xs = [index % grid_width for index in cells]
        ys = [index // grid_width for index in cells]
        for index in cells:
            labels[index] = label
        regions.append({
            'bbox': (min(xs) * cell, min(ys) * cell,
                     min(width, (max(xs) + 1) * cell),
                     min(height, (max(ys) + 1) * cell)),
        })
    if regions:
        full_labels = Image.frombytes('L', occupied.size, bytes(labels)).resize(
            (grid_width * cell, grid_height * cell), Image.Resampling.NEAREST)
        if full_labels.size != added.size:
            full_labels = full_labels.crop((0, 0, width, height))
        added_counts = ImageChops.multiply(full_labels, added).histogram()
        removed_counts = ImageChops.multiply(full_labels, removed).histogram()
        for label, region in enumerate(regions, 1):
            plus, minus = added_counts[label], removed_counts[label]
            region.update({
                'kind': 'changed' if plus and minus else ('added' if plus else 'removed'),
                'added_pixels': plus,
                'removed_pixels': minus,
            })
    return regions, total_regions


def compare_previews(old_image, new_image, tolerance=1, *, include_regions=True):
    """Return red removals, green additions, neutral common linework.

    ``tolerance`` is the permitted pixel-neighbourhood mismatch (default 1).
    It ignores antialiased fringes and sub-pixel plotting jitter; geometry
    changes no wider than this tolerance may be hidden. Use 0 for exact ink
    positions. Line colour/intensity changes alone are intentionally ignored.
    ``regions`` are approximate visual-change locations, not CAD entities.
    Set ``include_regions=False`` for interactive viewports to skip clustering.
    ``added_pixels`` / ``removed_pixels`` count binary ink-mask pixels.
    """
    if old_image.size != new_image.size:
        raise ValueError('兩張預覽必須使用相同尺寸與座標範圍。')
    if min(old_image.size) < 1:
        raise ValueError('預覽尺寸不得為零。')
    if isinstance(tolerance, bool) or not isinstance(tolerance, int) or not 0 <= tolerance <= 8:
        raise ValueError('像素容差必須是 0 到 8 的整數。')

    old_ink, new_ink = _ink(old_image), _ink(new_image)
    old_binary, new_binary = _foreground(old_ink), _foreground(new_ink)
    if tolerance:
        kernel = ImageFilter.MaxFilter(2 * tolerance + 1)
        old_near = old_binary.filter(kernel)
        new_near = new_binary.filter(kernel)
    else:
        old_near, new_near = old_binary, new_binary
    added = ImageChops.subtract(new_binary, old_near)
    removed = ImageChops.subtract(old_binary, new_near)

    old_overlay, new_overlay = _neutral(old_ink), _neutral(new_ink)
    overlay = _neutral(ImageChops.lighter(old_ink, new_ink))
    old_overlay.paste(REMOVED_COLOUR, mask=removed)
    new_overlay.paste(ADDED_COLOUR, mask=added)
    overlay.paste(REMOVED_COLOUR, mask=removed)
    overlay.paste(ADDED_COLOUR, mask=added)
    regions, total_regions = _regions(added, removed) if include_regions else ([], 0)
    return {
        'overlay': overlay,
        'old_overlay': old_overlay,
        'new_overlay': new_overlay,
        'added_mask': added,
        'removed_mask': removed,
        'regions': regions,
        'added_pixels': added.histogram()[255],
        'removed_pixels': removed.histogram()[255],
        'total_regions': total_regions,
        'omitted_regions': total_regions - len(regions),
        'regions_truncated': total_regions > len(regions),
        'region_limit': MAX_REGIONS,
        'tolerance_pixels': tolerance,
        'ink_threshold': INK_THRESHOLD,
        'comparison_kind': 'visual_ink_regions',
    }


def compare_visible_layers(old_image, new_image, mode="overlay"):
    """Only colour the currently visible mode, with exact ink classification.

    No region clustering, hidden original views, or unused mode images are made.
    Full-drawing analysis still uses compare_previews independently.
    """
    if mode not in ("overlay", "swipe") or old_image.size != new_image.size:
        raise ValueError("Invalid comparison mode or mismatched image sizes.")
    old_ink, new_ink = _ink(old_image), _ink(new_image)
    old_binary, new_binary = _foreground(old_ink), _foreground(new_ink)
    added = ImageChops.subtract(new_binary, old_binary)
    removed = ImageChops.subtract(old_binary, new_binary)
    result = {"added_mask": added, "removed_mask": removed}
    if mode == "overlay":
        overlay = _neutral(ImageChops.lighter(old_ink, new_ink))
        overlay.paste(REMOVED_COLOUR, mask=removed)
        overlay.paste(ADDED_COLOUR, mask=added)
        result["overlay"] = overlay
    else:
        old_overlay, new_overlay = _neutral(old_ink), _neutral(new_ink)
        old_overlay.paste(REMOVED_COLOUR, mask=removed)
        new_overlay.paste(ADDED_COLOUR, mask=added)
        result.update(old_overlay=old_overlay, new_overlay=new_overlay)
    return result
