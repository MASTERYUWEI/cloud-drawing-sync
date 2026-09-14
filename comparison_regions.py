"""Progressive navigation bounds from the very masks displayed by the viewer.

Keep a bounded, drawing-anchored occupancy atlas, not giant zoomed images.
Snapshots are immutable to the UI: workers return a new index, accepted only
with the matching drawing generation. Unvisited subpixel changes are unknown.
"""
import math

from PIL import Image, ImageChops, ImageFilter
from drawing_diff import _regions
from change_outline import outer_envelope


class ComparisonRegions:
    def __init__(self, size, cell, added, removed):
        self.size, self.cell = size, cell
        self.added, self.removed = added, removed
        self._interiors = None

    def interior_hint(self, size, scale, offset):
        """Project enclosed full-drawing voids to preserve nesting when zoomed."""
        if self._interiors is None:
            ink = ImageChops.lighter(self.added, self.removed).filter(ImageFilter.MaxFilter(3))
            self._interiors = ImageChops.subtract(outer_envelope(ink), ink)
        factor = self.cell * scale
        return self._interiors.transform(size, Image.Transform.AFFINE,
                                        (1/factor, 0, -offset[0]/factor,
                                         0, 1/factor, -offset[1]/factor),
                                        resample=Image.Resampling.NEAREST, fillcolor=0)

    @classmethod
    def from_masks(cls, added, removed):
        if added.size != removed.size:
            raise ValueError("Mismatched change masks")
        cell = max(4, min(16, math.ceil(max(added.size) / 256)))
        # At most 256 binary samples: even one changed pixel survives rounding.
        masks = [mask.reduce(cell).point([0] + [255] * 255)
                 for mask in (added, removed)]
        return cls(added.size, cell, *masks)

    def swapped(self):
        return ComparisonRegions(self.size, self.cell, self.removed, self.added)

    def refine(self, added, removed, scale, offset):
        """Conservatively retain every visible changed pixel in reference cells.

        Pool at most 16x16 screen pixels using binary max, not a resampling
        filter (which could erase thin lines). Map the entire pooled block to
        drawing cells. Extra margin is bounded by one block, not an arbitrary
        enlargement of region boxes. Zoom-out frames cannot erase discoveries.
        """
        if (added.size != removed.size or not math.isfinite(scale) or scale <= 0
                or len(offset) != 2 or not all(math.isfinite(n) for n in offset)):
            raise ValueError("Invalid region viewport")
        if scale <= 1:
            return self  # Initial full-page analysis is already more detailed.
        step = min(16, max(1, math.floor(scale * self.cell)))
        width, height = self.added.size
        changed = False
        updated = []
        for source, atlas in ((added, self.added), (removed, self.removed)):
            pooled = source.reduce(step)
            data = bytearray(atlas.tobytes())
            for i, value in enumerate(pooled.tobytes()):
                if not value:
                    continue
                x, y = (i % pooled.width) * step, (i // pooled.width) * step
                x0 = max(0, math.floor((x - offset[0]) / scale / self.cell))
                y0 = max(0, math.floor((y - offset[1]) / scale / self.cell))
                x1 = min(width, math.ceil((min(x + step, source.width) - offset[0]) / scale / self.cell))
                y1 = min(height, math.ceil((min(y + step, source.height) - offset[1]) / scale / self.cell))
                for gy in range(y0, y1):
                    for gx in range(x0, x1):
                        index = gy * width + gx
                        if not data[index]:
                            data[index] = 255
                            changed = True
            updated.append(Image.frombytes("L", atlas.size, bytes(data)))
        return ComparisonRegions(self.size, self.cell, *updated) if changed else self

    def regions(self):
        regions, total = _regions(self.added, self.removed, cell_size=1)
        for region in regions:
            x0, y0, x1, y1 = region["bbox"]
            region["bbox"] = (x0 * self.cell, y0 * self.cell,
                              min(self.size[0], x1 * self.cell),
                              min(self.size[1], y1 * self.cell))
            # These counts indicate occupancy, NOT pixels across zoom levels.
            region["added_cells"] = region.pop("added_pixels")
            region["removed_cells"] = region.pop("removed_pixels")
        return regions, total
