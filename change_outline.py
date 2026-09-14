"""Concave, offset change contours and viewport-bounded revision-cloud strokes.

No convex hull or bounding rectangle: concave exterior edges stay concave.
Only outer boundaries are drawn; holes and nested islands get no extra cloud.
Contour extraction runs on the render worker, using the displayed binary masks.
"""
import math
from collections import deque
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps


def outer_envelope(mask):
    """Fill enclosed voids for annotation only; never alter comparison ink."""
    padded = ImageOps.expand(mask, border=1, fill=0)
    ImageDraw.floodfill(padded, (0, 0), 127)
    return padded.point([0 if value == 127 else 255 for value in range(256)]).crop(
        (1, 1, mask.width + 1, mask.height + 1))


def _fill_known_interiors(mask, hint, border):
    """Retain enclosing context when the outer loop extends offscreen.

    Classify empty components inside the viewport, not through the artificial
    padding around it. A full-drawing enclosed-void hint can therefore fill an
    inner gap even when its closing end lies beyond the right/left screen edge.
    """
    width, height = hint.size
    view = mask.crop((border, border, border + width, border + height))
    active, seeds = view.tobytes(), hint.tobytes()
    pending, filled = bytearray(active), bytearray(len(active))
    for start in range(len(pending)):
        if pending[start] or not seeds[start]:
            continue
        queue, cells = deque([start]), []
        pending[start] = 255
        while queue:
            i = queue.popleft()
            cells.append(i)
            x, y = i % width, i // width
            for neighbour in ((i-1 if x else -1), (i+1 if x+1 < width else -1),
                              (i-width if y else -1), (i+width if y+1 < height else -1)):
                if neighbour >= 0 and not pending[neighbour]:
                    pending[neighbour] = 255
                    queue.append(neighbour)
        for i in cells:
            filled[i] = 255
    if not any(filled):
        return mask
    fill = Image.frombytes("L", (width, height), bytes(filled))
    expanded = ImageOps.expand(fill, border=border, fill=0)
    # Continue filled interiors beyond clipped edges, avoiding visible end caps.
    for crop, box in (
        ((0,0,width,1), (border,0,border+width,border)),
        ((0,height-1,width,height), (border,border+height,border+width,mask.height)),
        ((0,0,1,height), (0,border,border,border+height)),
        ((width-1,0,width,height), (border+width,border,mask.width,border+height)),
        ((0,0,1,1), (0,0,border,border)),
        ((width-1,0,width,1), (border+width,0,mask.width,border)),
        ((0,height-1,1,height), (0,border+height,border,mask.height)),
        ((width-1,height-1,width,height), (border+width,border+height,mask.width,mask.height)),
    ):
        expanded.paste(fill.crop(crop).resize((box[2]-box[0], box[3]-box[1]), Image.Resampling.NEAREST), box)
    return ImageChops.lighter(mask, expanded)


def change_contours(added, removed, *, step=4, padding=12, interior_hint=None):
    """Return closed contours in input-image coordinates, outside changed ink.

    Binary pooling preserves isolated pixels. Padding is applied *before*
    tracing, including beyond the viewport edges, so clipped lines do not gain
    a false end-cap inside the visible drawing. Typical error is <= step pixels.
    """
    if added.size != removed.size or not 1 <= step <= 16 or padding < 0:
        raise ValueError("Invalid change contour masks")
    mask = ImageChops.lighter(added, removed).reduce(step).point([0] + [255] * 255)
    radius = math.ceil(padding / step)
    border = radius + 1
    mask = ImageOps.expand(mask, border=border, fill=0)
    if radius:
        mask = mask.filter(ImageFilter.MaxFilter(2 * radius + 1))
    if interior_hint is not None:
        if interior_hint.size != added.size:
            raise ValueError("Interior hint must use viewport coordinates")
        # Require the whole pooled cell to be known interior, not just a fringe.
        hint = interior_hint.reduce(step).point([0] * 255 + [255])
        if hint.getbbox() is not None:
            mask = _fill_known_interiors(mask, hint, border)
    mask = outer_envelope(mask)
    # Extra zero border also handles filled interiors extending off the viewport.
    mask = ImageOps.expand(mask, border=1, fill=0)
    border += 1
    bounds = mask.getbbox()
    if bounds is None:
        return []
    width = mask.width
    data = mask.tobytes()
    edges = {}

    def edge(start, end):
        edges.setdefault(start, set()).add(end)

    for y in range(bounds[1], bounds[3]):
        for x in range(bounds[0], bounds[2]):
            i = y * width + x
            if not data[i]:
                continue
            # Directed edges have occupied area on their right (screen Y down).
            if not data[i - width]:
                edge((x, y), (x + 1, y))
            if not data[i + 1]:
                edge((x + 1, y), (x + 1, y + 1))
            if not data[i + width]:
                edge((x + 1, y + 1), (x, y + 1))
            if not data[i - 1]:
                edge((x, y + 1), (x, y))

    def direction(a, b):
        return {(1, 0): 0, (0, 1): 1, (-1, 0): 2, (0, -1): 3}[(b[0]-a[0], b[1]-a[1])]

    loops = []
    while edges:
        start = next(iter(edges))
        current, heading, points = start, 0, [start]
        while True:
            options = edges[current]
            # At a diagonal contact trace each boundary, never cross the loops.
            end = min(options, key=lambda p: ({1: 0, 0: 1, 3: 2, 2: 3}[(direction(current, p)-heading) % 4], p))
            heading = direction(current, end)
            options.remove(end)
            if not options:
                del edges[current]
            current = end
            if current == start:
                break
            points.append(current)
        # Drop collinear vertices; round only a fixed distance at corners.
        corners = []
        for i, point in enumerate(points):
            before, after = points[i-1], points[(i+1) % len(points)]
            if (point[0]-before[0], point[1]-before[1]) != (after[0]-point[0], after[1]-point[1]):
                corners.append(((point[0]-border)*step, (point[1]-border)*step))
        rounded = []
        for i, point in enumerate(corners):
            before, after = corners[i-1], corners[(i+1) % len(corners)]
            incoming, outgoing = math.dist(before, point), math.dist(after, point)
            radius = min(step * .7, incoming / 3, outgoing / 3)
            a = tuple(p + (b-p)*radius/incoming for p, b in zip(point, before))
            b = tuple(p + (n-p)*radius/outgoing for p, n in zip(point, after))
            for t in (0, .25, .5, .75, 1):
                rounded.append(tuple((1-t)**2*u + 2*t*(1-t)*v + t*t*w for u,v,w in zip(a, point, b)))
        if rounded:
            loops.append(rounded + [rounded[0]])
    return loops


def _clip(a, b, width, height, margin=8):
    """Liang-Barsky clip, returning fractions (no enormous offscreen polylines)."""
    dx, dy = b[0]-a[0], b[1]-a[1]
    low, high = 0., 1.
    for p, q in ((-dx, a[0]+margin), (dx, width+margin-a[0]),
                 (-dy, a[1]+margin), (dy, height+margin-a[1])):
        if p == 0:
            if q < 0:
                return None
        elif p < 0:
            low = max(low, q/p)
        else:
            high = min(high, q/p)
        if low > high:
            return None
    return low, high


def cloud_paths(contour, scale, offset, size):
    """Project a closed contour into small outward arcs, clipped before sampling.

    Arc spacing/height stay in screen pixels, not DWG units. This also bounds
    work at extreme zoom: a million-pixel offscreen edge is never tessellated.
    """
    paths, current = [], []
    for first, last in zip(contour, contour[1:]):
        a, b = [tuple(p*scale+o for p, o in zip(point, offset)) for point in (first, last)]
        clipped = _clip(a, b, *size)
        length = math.dist(a, b)
        if clipped is None or length < 1e-8:
            if current:
                paths.append(current)
                current = []
            continue
        nx, ny = (b[1]-a[1])/length, -(b[0]-a[0])/length
        waves = max(1, math.ceil(length / 18))
        samples = waves * 6
        low, high = clipped
        ts = [low] + [i/samples for i in range(math.floor(low*samples)+1, math.ceil(high*samples))] + [high]
        segment = []
        for t in ts:
            bulge = min(3., length / waves / 5) * math.sin(math.pi * ((t*waves) % 1))
            segment.append((a[0]+(b[0]-a[0])*t+nx*bulge, a[1]+(b[1]-a[1])*t+ny*bulge))
        if current and math.dist(current[-1], segment[0]) > .01:
            paths.append(current)
            current = []
        current.extend(segment)
    if current:
        paths.append(current)
    return paths
