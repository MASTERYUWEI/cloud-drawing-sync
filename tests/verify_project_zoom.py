"""Read-only, local verification of deep zoom on an explicitly supplied pair."""
from pathlib import Path
import hashlib
import json
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from comparison_session import load_comparison_session
from comparison_tiles import ComparisonTileCache
from drawing_diff import compare_previews
from dwg_preview import render_dwg_pair


def main():
    paths = load_comparison_session(sys.argv[1])
    before = [hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths]
    folder = Path(__file__).resolve().parents[1] / ".preview-test" / ("project_zoom_" + uuid.uuid4().hex)
    folder.mkdir(parents=True)
    result = render_dwg_pair(*paths, folder, log=lambda value: print(value, flush=True))
    with Image.open(result["old_png"]) as old, Image.open(result["new_png"]) as new:
        full = compare_previews(old.convert("RGB"), new.convert("RGB"), tolerance=0)
        reference_size = old.size
    cache = ComparisonTileCache(tuple(Path(result[k]).read_bytes() for k in ("old_pdf", "new_pdf")),
                                reference_width=reference_size[0])
    # Review the last detected region (matching the lower wall in the supplied
    # screenshot); the resulting JSON records exact bounds for visual checking.
    region = full["regions"][-1] if full["regions"] else {"bbox": [0, 0, *reference_size]}
    x0, y0, x1, y1 = region["bbox"]
    size = (1060, 600)
    frames = []
    for scale in (8, 32, 64):
        offset = (size[0]/2 - (x0+x1)/2*scale, size[1]/2 - (y0+y1)/2*scale)
        _, detail = cache.render(size, scale, offset)
        output = folder / f"detail_{scale*100}percent.png"
        detail["overlay"].save(output)
        frames.append({"scale": scale, "offset": offset, "image": str(output),
                       "added_pixels": detail["added_pixels"], "removed_pixels": detail["removed_pixels"]})
    after = [hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths]
    if before != after:
        raise RuntimeError("Source drawings changed during verification.")
    report = {**result, "reference_size": reference_size, "regions": full["regions"],
              "detail_region": region, "frames": frames, "sources_unchanged": True}
    report_file = folder / "verification.json"
    report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"verification": str(report_file), "sources_unchanged": True,
                      "regions": len(full["regions"]), "frames": frames}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
