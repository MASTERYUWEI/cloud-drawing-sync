"""Opt-in regression: tiny SHX text on a large drawing must survive plotting.

Run directly with Python. Uses synthetic DWGs and read-only Core Console,
not the user's open AutoCAD session. All generated artifacts stay local.
"""
from pathlib import Path
import hashlib
import json
import math
import os
import re
import shutil
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dwg_preview as preview
from pdf_viewport import render_pdf_viewport
import pypdfium2 as pdfium
from PIL import Image, ImageChops, ImageFilter


def detail(pdf, bounds, center=(20045, 7005), span=160):
    data = Path(pdf).read_bytes()
    with pdfium.PdfDocument(data) as document:
        page = document[0]
        try:
            pw, ph = page.get_size()
        finally:
            page.close()
    width, height = 4096, math.ceil(ph/pw*4096)
    x0, y0, x1, y1 = bounds
    fit = min(width/(x1-x0), height/(y1-y0))
    px = (center[0]-x0)*fit+(width-(x1-x0)*fit)/2
    py = (y1-center[1])*fit+(height-(y1-y0)*fit)/2
    scale = 1200/(span*fit)
    return render_pdf_viewport(data, (1200, 400), scale, (600-px*scale, 200-py*scale))


def shape_similarity(actual, reference):
    a, b = (image.convert('L').point(lambda p: 255 if p < 220 else 0)
            for image in (actual, reference))
    counts = [image.histogram()[255] for image in (a, b)]
    if not all(counts):
        return 0
    # This score tests glyph shape, not the separately tested old/new world
    # registration. A local reference uses a different plot window; align ink
    # origins to remove its device rounding offset, without scaling either text.
    aa, bb = (image.crop(image.getbbox()) for image in (a, b))
    size = (max(aa.width, bb.width)+16, max(aa.height, bb.height)+16)
    a, b = Image.new('L', size), Image.new('L', size)
    a.paste(aa, (8, 8))
    b.paste(bb, (8, 8))
    # Allow a few display pixels for native device-grid/AA rounding, without
    # allowing erased glyphs or replacing an arc with a bounding rectangle.
    near_b = b.filter(ImageFilter.MaxFilter(9))
    near_a = a.filter(ImageFilter.MaxFilter(9))
    return (ImageChops.multiply(a, near_b).histogram()[255]/counts[0]
            + ImageChops.multiply(b, near_a).histogram()[255]/counts[1])/2


def main():
    engine = preview.find_core_console()
    if not engine:
        raise SystemExit('AutoCAD required; native font check skipped.')
    plotter = preview._find_pdf_plotter(engine)
    if plotter.name.lower() != 'autocad pdf (high quality print).pc3':
        raise SystemExit('High Quality Print preset required for this native test.')
    templates = list((Path(os.environ['LOCALAPPDATA'])/'Autodesk').glob(
        'AutoCAD */R*/*/Template/acadiso.dwt'))
    if not templates:
        raise SystemExit('AutoCAD template not found.')
    work = Path(__file__).resolve().parents[1]/'.preview-test'/('tiny_text_'+uuid.uuid4().hex)
    work.mkdir(parents=True)
    old, new = work/'old.dwg', work/'new.dwg'
    script = '''(setvar "FILEDIA" 0)
(setvar "CMDDIA" 0)
(setvar "INSUNITS" 4)
(setvar "OSMODE" 0)
(setvar "TILEMODE" 1)
(command "_.RECTANG" '(0 0) '(108000 27000))
(entmake '((0 . "STYLE") (100 . "AcDbSymbolTableRecord") (100 . "AcDbTextStyleTableRecord") (2 . "CDS_TINY_SHX") (70 . 0) (40 . 0.0) (41 . 1.0) (50 . 0.0) (71 . 0) (3 . "simplex.shx") (4 . "")))
(entmake '((0 . "TEXT") (10 20000 7000 0) (40 . 10.0) (1 . "CH6700mm") (7 . "CDS_TINY_SHX")))
'''
    script += '(command "_.SAVEAS" "2018" '+preview._lisp_string(str(old))+')\n(command "_.QUIT" "_No")\n'
    preview._run_console(engine, templates[0], script, work)
    shutil.copy2(old, new)
    before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (old, new, plotter)]
    result = preview.render_dwg_pair(old, new, work)
    measurement = preview._run_console(engine, old, preview._measurement_script(plotter), work)
    extent = preview._parse_measurement(measurement, 'test')['bounds']
    bounds = preview._common_bounds(extent, extent)
    small_bounds = (19965, 6950, 20125, 7060)
    a3 = next(n for n in re.findall(r'^\s+"([^"\r\n]+)"\s*$', measurement, re.M)
              if 'full bleed a3' in n.lower() and '420.00 x 297.00' in n)
    reference_pdf = work/'reference.pdf'
    legacy_pdf = work/'legacy.pdf'
    legacy_plotter = plotter.parent/'DWG To PDF.pc3'
    preview._run_console(engine, old, preview._plot_script(small_bounds, plotter, reference_pdf, a3), work)
    preview._run_console(engine, old, preview._plot_script(bounds, legacy_plotter, legacy_pdf, a3), work)
    reference = detail(reference_pdf, small_bounds)
    fixed = detail(result['old_pdf'], bounds)
    legacy = detail(legacy_pdf, bounds)
    scores = {'fixed': shape_similarity(fixed, reference), 'legacy': shape_similarity(legacy, reference)}
    for name, image in (('reference', reference), ('fixed', fixed), ('legacy', legacy)):
        image.save(work/(name+'.png'))
    assert scores['fixed'] > .8, scores
    assert scores['fixed'] > scores['legacy']+.2, scores
    assert before == [hashlib.sha256(p.read_bytes()).hexdigest() for p in (old, new, plotter)]
    assert ImageChops.difference(fixed, detail(result['new_pdf'], bounds)).getbbox() is None
    print(json.dumps({'scores': scores, 'sources_and_plotter_unchanged': True,
                      'identical_pair_aligned': True, 'artifacts': str(work)}, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
