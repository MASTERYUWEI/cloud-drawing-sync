"""Generate two genuine DWGs and compare deliberate architectural changes.

Only synthetic geometry is created. Never attaches to the user's AutoCAD UI.
Run: python tests/create_architecture_demo.py
"""
from pathlib import Path
import hashlib
import json
import os
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
import dwg_preview as preview
from drawing_diff import compare_previews
from pdf_viewport import render_pdf_viewport


def entity(kind, groups, obsolete=False):
    data = " ".join(groups)
    expression = f'(entmakex \'((0 . "{kind}") (8 . "0") {data}))'
    return f'(setq cdsold (cons {expression} cdsold))\n' if obsolete else expression + "\n"


def line(x1, y1, x2, y2, obsolete=False):
    return entity("LINE", [f"(10 {x1} {y1} 0)", f"(11 {x2} {y2} 0)"], obsolete)


def rect(x1, y1, x2, y2, obsolete=False):
    return (line(x1, y1, x2, y1, obsolete) + line(x2, y1, x2, y2, obsolete)
            + line(x2, y2, x1, y2, obsolete) + line(x1, y2, x1, y1, obsolete))


def text_at(x, y, text, height=160):
    return entity("TEXT", [f"(10 {x} {y} 0)", f"(40 . {height})", f'(1 . "{text}")'])


def door(x, y, width=900, obsolete=False):
    return (line(x, y, x, y + width, obsolete)
            + entity("ARC", [f"(10 {x} {y} 0)", f"(40 . {width})",
                             "(50 . 0.0)", "(51 . 1.5707963267948966)"], obsolete))


def create_pair(work):
    engine = preview.find_core_console()
    if not engine:
        raise RuntimeError("The architectural demo requires local AutoCAD.")
    templates = sorted((Path(os.environ["LOCALAPPDATA"]) / "Autodesk").glob(
        "AutoCAD */R*/*/Template/acadiso.dwt"))
    if not templates:
        raise RuntimeError("AutoCAD metric template not found.")
    old, new = work / "Office_Old.dwg", work / "Office_New.dwg"
    script = '''(setvar "FILEDIA" 0)
(setvar "CMDDIA" 0)
(setvar "TILEMODE" 1)
(setvar "INSUNITS" 4)
(setvar "OSMODE" 0)
(setq cdsold nil)
(command "_.UCS" "_World")
(command "_.PLAN" "_World")
'''
    # 18 m x 12 m office envelope; paired outlines are 220 mm exterior walls.
    for inset in (0, 220):
        script += line(inset, inset, 8200, inset) + line(9800, inset, 18000-inset, inset)
        script += line(inset, inset, inset, 12000-inset)
        script += line(inset, 12000-inset, 18000-inset, 12000-inset)
        script += line(18000-inset, inset, 18000-inset, 12000-inset)
    script += line(8200, 0, 8200, 220) + line(9800, 0, 9800, 220)
    # A central corridor and six large room bays, with real doorway gaps.
    for y in (4900, 5100):
        for start, end in ((220, 2400), (3300, 8200), (9100, 14300), (15200, 17780)):
            script += line(start, y, end, y)
    for y in (6900, 7100):
        for start, end in ((220, 1800), (2700, 4000), (4900, 8400), (9300, 14500), (15400, 17780)):
            script += line(start, y, end, y)
    for x in (6000, 6200, 11800, 12000):
        script += line(x, 220, x, 4900) + line(x, 7100, x, 11780)
    for x in (8400, 14500):
        script += door(x, 7100)
    # Structural columns, glazing and repeated furnishings keep a substantial
    # common drawing visible so false red/green registration is easy to spot.
    for x in (220, 5800, 11600, 17380):
        for y in (220, 11200):
            script += rect(x, y, x+400, y+400)
            script += line(x, y, x+400, y+400) + line(x+400, y, x, y+400)
    for x in range(900, 16800, 1800):
        script += rect(x, 11900, x+1200, 12100)
        script += line(x, 12000, x+1200, 12000)
    for base_x in (800, 6800, 12800):
        for y in (9000, 10400):
            script += rect(base_x, y, base_x+1600, y+650)
            script += rect(base_x+1800, y, base_x+3400, y+650)
            for x in (base_x+500, base_x+2300):
                script += rect(x, y-450, x+500, y-50)
    script += rect(7500, 1800, 10400, 3400)
    for x in (7800, 8700, 9600):
        script += rect(x, 1200, x+500, 1700) + rect(x, 3500, x+500, 4000)
    script += rect(900, 1000, 2300, 1700) + rect(3800, 1000, 5200, 1700)
    for x, label in ((1200, "WORKSPACE A"), (7200, "WORKSPACE B"), (13200, "WORKSPACE C")):
        script += text_at(x, 8250, label, 210)
    for x, label in ((1000, "COLLABORATION"), (7400, "MEETING ROOM"), (13200, "FLEX SPACE")):
        script += text_at(x, 4200, label, 210)
    script += text_at(7200, 5850, "CENTRAL CORRIDOR", 230)
    # Dimension chains and title are identical in both revisions.
    script += line(0, -750, 18000, -750)
    for x in (0, 6000, 12000, 18000):
        script += line(x, -950, x, -200) + line(x-90, -840, x+90, -660)
    for x in (2500, 8500, 14500):
        script += text_at(x, -620, "6000", 170)
    script += line(-750, 0, -750, 12000)
    script += text_at(-1700, 5900, "12000", 160)
    script += text_at(0, -1650, "OFFICE FLOOR PLAN / REVISION COMPARISON DEMO", 270)
    script += text_at(0, -2050, "SYNTHETIC TEST DRAWING - NOT FOR CONSTRUCTION - UNITS: mm", 145)
    # Old-only: low-left partition; upper-left doorway at x=1800.
    script += rect(2940, 220, 3060, 3800, obsolete=True)
    script += door(1800, 7100, obsolete=True)
    for y in (6900, 7100):
        script += line(4000, y, 4900, y, obsolete=True)
    script += '(command "_.ZOOM" "_Extents")\n'
    script += '(command "_.SAVEAS" "2018" ' + preview._lisp_string(str(old)) + ')\n'
    script += '(foreach cdsentity cdsold (entdel cdsentity))\n'
    # New-only: move the upper-left door, and add a lower-right partition/door.
    script += door(4000, 7100)
    for y in (6900, 7100):
        script += line(1800, y, 2700, y)
    script += rect(12000, 2540, 14500, 2660) + rect(15400, 2540, 17780, 2660)
    script += door(14500, 2660)
    script += '(command "_.SAVEAS" "2018" ' + preview._lisp_string(str(new)) + ')\n'
    script += '(command "_.QUIT" "_No")\n'
    preview._run_console(engine, templates[0], script, work)
    if not old.is_file() or not new.is_file():
        raise RuntimeError(f"DWG creation failed; inspect logs in {work}")
    return old, new


def main():
    work = Path(__file__).resolve().parents[1] / ".preview-test" / ("architecture_" + uuid.uuid4().hex)
    work.mkdir(parents=True)
    old, new = create_pair(work)
    sources = (old, new)
    before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in sources]
    result = preview.render_dwg_pair(old, new, work, log=print)
    after = [hashlib.sha256(path.read_bytes()).hexdigest() for path in sources]
    assert before == after, "Comparison changed source DWGs"
    with Image.open(result["old_png"]) as left, Image.open(result["new_png"]) as right:
        changes = compare_previews(left.convert("RGB"), right.convert("RGB"), tolerance=0)
        reference_height = left.height
    assert changes["added_pixels"] > 1000 and changes["removed_pixels"] > 1000
    assert len(changes["regions"]) >= 3, changes["regions"]
    pdfs = tuple(Path(result[key]).read_bytes() for key in ("old_pdf", "new_pdf"))
    size = (1600, 1132)
    scale = min(size[0]/4096, size[1]/reference_height) * 0.97
    offset = ((size[0]-4096*scale)/2, (size[1]-reference_height*scale)/2)
    views = tuple(render_pdf_viewport(pdf, size, scale, offset) for pdf in pdfs)
    for name, view in zip(("old_overview.png", "new_overview.png"), views):
        view.save(work / name)
    compare_previews(*views, tolerance=0, include_regions=False)["overlay"].save(work / "changes_overview.png")
    region = next(region for region in changes["regions"] if region["kind"] == "changed")
    x0, y0, x1, y1 = region["bbox"]
    detail_scale = min(8, 1200 / max(100, (x1-x0)*1.3), 800 / max(100, (y1-y0)*1.3))
    detail_offset = (600-(x0+x1)*detail_scale/2, 400-(y0+y1)*detail_scale/2)
    details = tuple(render_pdf_viewport(pdf, (1200, 800), detail_scale, detail_offset) for pdf in pdfs)
    compare_previews(*details, tolerance=0, include_regions=False)["overlay"].save(work / "changes_detail.png")
    report = {"old_dwg": str(old), "new_dwg": str(new), **result,
              "sources_unchanged": before == after, "regions": changes["regions"],
              "added_pixels": changes["added_pixels"], "removed_pixels": changes["removed_pixels"],
              "overview": str(work / "changes_overview.png"), "detail": str(work / "changes_detail.png"),
              "intentional_changes": ["Remove lower-left partition", "Relocate upper-left door",
                                      "Add lower-right partition and door"]}
    (work / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
