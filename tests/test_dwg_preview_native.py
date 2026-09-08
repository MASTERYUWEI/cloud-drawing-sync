"""Opt-in native preview check using generated geometry, never user drawings.

Run: python tests/test_dwg_preview_native.py
Requires AutoCAD, Pillow, pypdfium2. Outputs are confined to .preview-test/.
"""

from pathlib import Path
import hashlib
import os
import shutil
import sys
import threading
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dwg_preview as preview
from PIL import Image, ImageChops
from pdf_viewport import render_pdf_viewport


def main():
    engine = preview.find_core_console()
    if not engine:
        raise SystemExit("AutoCAD is not installed; native check skipped.")
    templates = list((Path(os.environ["LOCALAPPDATA"]) / "Autodesk").glob(
        "AutoCAD */R*/*/Template/acadiso.dwt"))
    if not templates:
        raise SystemExit("AutoCAD metric template missing; native check skipped.")
    work = Path(__file__).resolve().parents[1] / ".preview-test" / uuid.uuid4().hex
    work.mkdir(parents=True)
    old, new = work / "old.dwg", work / "new.dwg"
    script = '''(setvar "FILEDIA" 0)
(setvar "CMDDIA" 0)
(setvar "TILEMODE" 1)
(setvar "INSUNITS" 4)
(setvar "OSMODE" 0)
(command "_.UCS" "_World")
(command "_.PLAN" "_World")
(command "_.RECTANG" '(0 0) '(100 60))
(command "_.LINE" '(0 0) '(100 60) "")
(command "_.CIRCLE" '(20 30) 8)
(entmake '((0 . "TEXT") (10 45 45 0) (40 . 0.25) (1 . "REVISION DETAIL 012345")))
(command "_.ZOOM" "_Extents")
'''
    script += '(command "_.SAVEAS" "2018" ' + preview._lisp_string(str(old)) + ')\n'
    script += '(command "_.CIRCLE" \'(130 30) 12)\n'
    script += '(command "_.SAVEAS" "2018" ' + preview._lisp_string(str(new)) + ')\n'
    script += '(command "_.QUIT" "_No")\n'
    preview._run_console(engine, templates[0], script, work)
    if not old.is_file() or not new.is_file():
        raise AssertionError(f"Synthetic source creation failed, inspect {work}")
    before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in (old, new)]
    result = preview.render_dwg_pair(old, new, work, log=print)
    after = [hashlib.sha256(path.read_bytes()).hexdigest() for path in (old, new)]
    assert before == after, "Source drawings were modified"
    with Image.open(result["old_png"]) as old_img, Image.open(result["new_png"]) as new_img:
        assert old_img.size == new_img.size
        difference = ImageChops.difference(old_img.convert("RGB"), new_img.convert("RGB"))
        changed = difference.getbbox()
        assert changed is not None, "Different drawings rendered identically"
        # The only changed geometry is the new circle right of the old rectangle.
        # Independent zoom-to-extents would shift all shared geometry and fail.
        assert changed[0] > old_img.width * 0.65, f"Shared geometry shifted: {changed}"
        print({"size": old_img.size, "difference_bounds": changed,
               "sources_unchanged": before == after, **result})
        old_pdf = Path(result["old_pdf"]).read_bytes()
        new_pdf = Path(result["new_pdf"]).read_bytes()
        vector_reference = render_pdf_viewport(old_pdf, old_img.size, 1, (0, 0))
        assert ImageChops.difference(old_img.convert("RGB"), vector_reference).getbbox() is None, "PDF viewport and original PNG coordinates differ"
        ink = old_img.convert("L").point(lambda value: 255 if value < 220 else 0).getbbox()
        assert ink is not None
        offset = (-ink[0] * 8 + 50, -ink[1] * 8 + 50)
        old_zoom = render_pdf_viewport(old_pdf, (900, 600), 8, offset)
        new_zoom = render_pdf_viewport(new_pdf, (900, 600), 8, offset)
        assert old_zoom.getextrema()[0][0] < 100, "Native vector high-zoom tile is empty"
        assert ImageChops.difference(old_zoom, new_zoom).getbbox() is None, "Shared native geometry is misaligned at high zoom"
        old_zoom.save(work / "vector_detail_8x.png")
        print({"vector_pdf_reference_alignment": True, "native_vector_zoom": "8x, 900x600 visible tile", "vector_detail": str(work / "vector_detail_8x.png")})

    # A missing XREF must produce an explicit warning, not silently claim a
    # complete historical rendering. All files below are generated test data.
    dependency, host = work / "xref_source.dwg", work / "xref_host.dwg"
    shutil.copy2(old, dependency)
    xref_script = '(setvar "FILEDIA" 0)\n(setvar "CMDDIA" 0)\n'
    xref_script += '(command "_.-XREF" "_Attach" ' + preview._lisp_string(str(dependency)) + ' \'(200 0) 1 1 0)\n'
    xref_script += '(command "_.SAVEAS" "2018" ' + preview._lisp_string(str(host)) + ')\n'
    xref_script += '(command "_.QUIT" "_No")\n'
    preview._run_console(engine, old, xref_script, work)
    assert host.is_file(), "XREF test host was not generated"
    dependency.rename(dependency.with_suffix(".temporarily_moved"))
    text = preview._run_console(engine, host, preview._measurement_script(preview._find_pdf_plotter(engine)), work)
    metadata = preview._parse_measurement(text, "測試")
    assert any("未載入" in item for item in metadata["warnings"]), metadata
    print({"missing_xref_warning": metadata["warnings"]})

    # Interrupt only the background process started by this call; the user's
    # interactive AutoCAD process is never selected or contacted.
    event = threading.Event()
    timer = threading.Timer(0.75, event.set)
    timer.start()
    try:
        preview._run_console(engine, old, '(while T (setq cdsbusy 1))\n', work, event, timeout=10)
        raise AssertionError("Cancellation was ignored")
    except preview.PreviewCancelled:
        print("native cancellation: passed")
    finally:
        timer.cancel()

    invalid = work / "invalid.dwg"
    invalid.write_bytes(b"Not a DWG: intentionally invalid native-test fixture")
    try:
        preview.render_dwg_pair(invalid, old, work)
        raise AssertionError("Invalid DWG was accepted")
    except preview.PreviewError:
        print("invalid DWG rejection: passed")


if __name__ == "__main__":
    main()
