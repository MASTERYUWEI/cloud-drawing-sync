"""Read-only, local AutoCAD model-space previews for revision comparison.

AutoCAD is an optional, separately installed dependency.  The GUI must run this
module on a worker thread: Core Console can take minutes on large drawings.
No commands are sent to an existing interactive AutoCAD session.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid

from pdf_viewport import PDFIUM_LOCK


class PreviewError(RuntimeError):
    """A preview cannot be produced; the message is suitable for the GUI."""


class PreviewCancelled(PreviewError):
    """The user cancelled preview generation."""


def find_core_console() -> str | None:
    """Find a locally installed full AutoCAD engine, without starting it."""
    explicit = os.environ.get("CLOUD_DRAWING_ACCORECONSOLE", "")
    if explicit and Path(explicit).is_file():
        return str(Path(explicit).resolve())
    candidates = []
    for root in (os.environ.get("ProgramW6432"), os.environ.get("ProgramFiles")):
        if root:
            candidates.extend(Path(root).glob("Autodesk/AutoCAD */accoreconsole.exe"))
    if candidates:
        return str(sorted(set(candidates), reverse=True)[0].resolve())
    found = shutil.which("accoreconsole.exe")
    return str(Path(found).resolve()) if found else None


def _find_pdf_plotter(engine: str) -> Path:
    year_match = re.search(r"AutoCAD (\d{4})", engine)
    year = year_match.group(1) if year_match else "*"
    roaming = Path(os.environ.get("APPDATA", "")) / "Autodesk"
    candidates = list(roaming.glob(f"AutoCAD {year}/R*/*/Plotters/*.pc3"))
    candidates.extend(Path(engine).parent.glob("**/Plotters/*.pc3"))
    # SHX text is flattened to device-grid geometry by the plotter. A small
    # page with the legacy 600-dpi preset destroys tiny letters before the
    # viewer ever sees them, regardless of subsequent vector zoom quality.
    preferred = ("autocad pdf (high quality print).pc3",
                 "autocad pdf (general documentation).pc3", "dwg to pdf.pc3")
    for name in preferred:
        for path in candidates:
            if path.name.lower() == name:
                return path.resolve()
    raise PreviewError("找不到 AutoCAD 的 PDF 出圖設定，請先啟動 AutoCAD 完成初始設定。")


def _lisp_string(value: str) -> str:
    """ASCII script expression, preserving Unicode and escaping Lisp syntax."""
    value = value.replace("\\", "/")
    pieces = []
    ascii_part = []
    for char in value:
        if 32 <= ord(char) < 127:
            ascii_part.append(char.replace('"', '\\"'))
        else:
            if ascii_part:
                pieces.append('"' + "".join(ascii_part) + '"')
                ascii_part.clear()
            pieces.append(f"(chr {ord(char)})")
    if ascii_part:
        pieces.append('"' + "".join(ascii_part) + '"')
    return pieces[0] if len(pieces) == 1 else "(strcat " + " ".join(pieces or ['""']) + ")"


def _decode_console(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    if data[:200].count(b"\x00") > 20:
        return data.decode("utf-16-le", errors="replace")
    return data.decode("utf-8", errors="replace")


def _check_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise PreviewCancelled("已取消版次預覽。")


def _run_console(engine, source, script, job, cancel_event=None, timeout=180):
    """Only terminate the process this invocation owns, never acad.exe."""
    _check_cancel(cancel_event)
    script_path = job / (uuid.uuid4().hex + ".scr")
    script_path.write_text(script.rstrip() + "\n", encoding="ascii")
    args = [engine, "/i", str(source), "/s", str(script_path), "/readonly",
            "/isolate", "CDSCompare" + job.name, str(job / "profile"), "/l", "en-US"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process_environment = os.environ.copy()
    temporary = job / "temp"
    temporary.mkdir(exist_ok=True)
    process_environment["TEMP"] = str(temporary)
    process_environment["TMP"] = str(temporary)
    output_path = script_path.with_suffix(".log")
    with output_path.open("wb") as stream:
        try:
            process = subprocess.Popen(args, cwd=str(job), stdin=subprocess.DEVNULL,
                                       stdout=stream, stderr=subprocess.STDOUT,
                                       creationflags=flags, env=process_environment)
        except OSError as exc:
            raise PreviewError(f"無法啟動 AutoCAD 預覽引擎：{exc}") from exc
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                _check_cancel(cancel_event)
                if time.monotonic() >= deadline:
                    raise PreviewError("AutoCAD 預覽逾時，請確認圖檔、外部參考與 AutoCAD 啟用狀態。")
                time.sleep(0.1)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
    text = _decode_console(output_path.read_bytes())
    if process.returncode != 0:
        raise PreviewError(f"AutoCAD 無法讀取「{source.name}」（代碼 {process.returncode}），請確認圖檔及 AutoCAD 啟用狀態。")
    return text


_SETUP = """(setvar "FILEDIA" 0)
(setvar "CMDDIA" 0)
(setvar "BACKGROUNDPLOT" 0)
(setvar "TILEMODE" 1)
(command "_.UCS" "_World")
(command "_.PLAN" "_World")
(command "_.REGEN")
(command "_.ZOOM" "_Extents")
"""

_MEASURE = _SETUP + """(setq cdsmin (getvar "EXTMIN") cdsmax (getvar "EXTMAX"))
(princ (strcat "\\nCDS_EXTENTS|" (rtos (car cdsmin) 2 12) "|" (rtos (cadr cdsmin) 2 12) "|" (rtos (car cdsmax) 2 12) "|" (rtos (cadr cdsmax) 2 12) "\\n"))
(princ (strcat "\\nCDS_UNITS|" (itoa (getvar "INSUNITS")) "\\n"))
(setq cdsblock (tblnext "BLOCK" T))
(while cdsblock (if (/= 0 (logand 4 (cdr (assoc 70 cdsblock)))) (princ (strcat "\\nCDS_XREF|" (itoa (cdr (assoc 70 cdsblock))) "|" (cdr (assoc 2 cdsblock)) "\\n"))) (setq cdsblock (tblnext "BLOCK")))
(princ "\\nCDS_MEASURE_DONE\\n")
"""


def _measurement_script(plotter):
    # Paper names include localized unit strings even when /l en-US is passed.
    # Ask the installed driver for its actual names instead of assuming "MM".
    return (_MEASURE + '(command "_.-PLOT" "_Yes" "" '
            + _lisp_string(str(plotter)) + ' "?")\n(command)\n'
            + '(command "_.QUIT" "_No")\n')


def _parse_media(text):
    """Prefer a large virtual sheet without enlarging the 4096-pixel preview.

    4A0 has ~5.7 times A3's linear device precision. Together with the high
    quality PDF preset this preserves small SHX strokes on large projects.
    Accept either orientation: -PLOT explicitly requests landscape. Only use
    recognized ISO dimensions so inch sizes/custom roll widths are not guessed.
    Keep the exact localized media label returned by the installed driver.
    """
    names = re.findall(r'^\s+"([^"\r\n]+)"\s*$', text, re.MULTILINE)
    for short, long in ((1682, 2378), (1189, 1682), (841, 1189),
                        (594, 841), (420, 594), (297, 420)):
        candidates = []
        for name in names:
            dimensions = re.search(r"\((\d+)[.,]00\s*x\s*(\d+)[.,]00\s+", name)
            if (name.lower().startswith("iso ") and dimensions
                    and sorted(map(int, dimensions.groups())) == [short, long]):
                candidates.append((name, int(dimensions.group(1)) >= int(dimensions.group(2))))
        if candidates:
            return min(candidates, key=lambda item: ("full bleed" not in item[0].lower(), not item[1]))[0]
    raise PreviewError("AutoCAD PDF 出圖設定沒有可使用的 ISO A3 或更大紙張。")


def _parse_measurement(text: str, label: str) -> dict:
    match = re.search(r"^CDS_EXTENTS\|([^\r\n]+)", text, re.MULTILINE)
    if "\nCDS_MEASURE_DONE" not in text or not match:
        raise PreviewError(f"無法取得{label}的圖面範圍；圖檔可能損壞、缺少支援元件，或 AutoCAD 尚未啟用。")
    try:
        bounds = tuple(float(value) for value in match.group(1).split("|"))
    except ValueError as exc:
        raise PreviewError(f"{label}的圖面範圍格式不正確。") from exc
    if (len(bounds) != 4 or not all(math.isfinite(v) for v in bounds)
            or bounds[2] < bounds[0] or bounds[3] < bounds[1]
            or max(bounds[2] - bounds[0], bounds[3] - bounds[1]) <= 0):
        raise PreviewError(f"{label}沒有可預覽的模型空間圖元。")
    units = re.search(r"^CDS_UNITS\|(\d+)", text, re.MULTILINE)
    xrefs = re.findall(r"^CDS_XREF\|(\d+)\|([^\r\n]+)", text, re.MULTILINE)
    warnings = []
    for flags, name in xrefs:
        if not int(flags) & 32:
            warnings.append(f"{label}的外部參考「{name}」未載入，預覽可能不完整。")
    if re.search(r"proxy|missing.*font|font.*not found|替代字型|找不到字型", text, re.I):
        warnings.append(f"{label}可能包含代理圖元或替代字型，請以 AutoCAD 開圖結果確認。")
    return {"bounds": bounds, "units": int(units.group(1)) if units else 0,
            "warnings": warnings, "has_xrefs": bool(xrefs)}


def _common_bounds(first, second):
    xmin, ymin = min(first[0], second[0]), min(first[1], second[1])
    xmax, ymax = max(first[2], second[2]), max(first[3], second[3])
    padding = max(xmax - xmin, ymax - ymin) * 0.025
    return xmin - padding, ymin - padding, xmax + padding, ymax + padding


def _plot_script(bounds, plotter: Path, destination: Path, media: str) -> str:
    xmin, ymin, xmax, ymax = bounds
    point1 = f"(list {xmin:.12f} {ymin:.12f})"
    point2 = f"(list {xmax:.12f} {ymax:.12f})"
    return _SETUP + (
        '(command "_.-PLOT" "_Yes" "" '
        + _lisp_string(str(plotter))
        + ' ' + _lisp_string(media) + ' "_Millimeters" "_Landscape" "_No" '
        + '"_Window" ' + point1 + ' ' + point2
        + ' "_Fit" "_Center" "_No" "." "_No" "_Wireframe" '
        + _lisp_string(str(destination)) + ' "_No" "_Yes")\n'
        + '(princ "\\nCDS_PLOT_DONE\\n")\n'
        + '(command "_.QUIT" "_No")\n'
    )


def _rasterize(pdf: Path, png: Path, max_side=4096):
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise PreviewError("缺少 PDF 預覽元件 pypdfium2，請重新安裝完整版同步工具。") from exc
    try:
        with PDFIUM_LOCK, pdfium.PdfDocument(str(pdf)) as document:
            if len(document) != 1:
                raise PreviewError("AutoCAD 預覽輸出頁數不正確。")
            page = document[0]
            bitmap = page.render(scale=max_side / max(page.get_size()))
            pil_image = bitmap.to_pil()
            pil_image.save(str(png))
            pil_image.close()
            bitmap.close()
            page.close()
    except PreviewError:
        raise
    except Exception as exc:
        raise PreviewError(f"無法轉換 AutoCAD 預覽：{exc}") from exc


def render_dwg_pair(old_path, new_path, work_dir, log=None, cancel_event=None) -> dict:
    """Return {old_png, new_png, old_pdf, new_pdf, warnings} aligned previews.

    The source files and active AutoCAD session are never modified.  Output and
    isolated Core Console settings live in a unique subdirectory of work_dir.
    The caller owns cache removal once the viewer no longer uses these files.
    """
    report = log or (lambda message: None)
    _check_cancel(cancel_event)
    sources = [Path(old_path).resolve(), Path(new_path).resolve()]
    for source in sources:
        if source.suffix.lower() != ".dwg" or not source.is_file():
            raise PreviewError(f"找不到 DWG 圖檔：{source}")
    engine = find_core_console()
    if not engine:
        raise PreviewError("DWG 預覽需要本機安裝並啟用 AutoCAD。")
    plotter = _find_pdf_plotter(engine)
    try:
        import pypdfium2  # noqa: F401 -- fail early before starting AutoCAD
    except ImportError as exc:
        raise PreviewError("缺少 PDF 預覽元件 pypdfium2，請重新安裝完整版同步工具。") from exc
    job = Path(work_dir).resolve() / ("dwg_" + uuid.uuid4().hex)
    job.mkdir(parents=True, exist_ok=False)
    fingerprints = [(source.stat().st_size, source.stat().st_mtime_ns) for source in sources]

    def check_sources():
        for source, fingerprint in zip(sources, fingerprints):
            try:
                current = (source.stat().st_size, source.stat().st_mtime_ns)
            except OSError as exc:
                raise PreviewError(f"比較中的圖檔已移動或無法讀取：{source.name}") from exc
            if current != fingerprint:
                raise PreviewError(f"「{source.name}」在比較期間已更新，請等同步或存檔完成後重新比較。")

    measured = []
    warnings = []
    media = None
    for source, label in zip(sources, ("舊版", "新版")):
        report(f"正在讀取{label}圖面範圍：{source.name}")
        check_sources()
        text = _run_console(engine, source, _measurement_script(plotter), job, cancel_event)
        measurement = _parse_measurement(text, label)
        media = media or _parse_media(text)
        measured.append(measurement)
        warnings.extend(measurement["warnings"])
    report(f"細字保真轉換：{plotter.name}；虛擬紙張 {media}（不變更 DWG 或印表機設定）。")
    if plotter.name.lower() != "autocad pdf (high quality print).pc3":
        warnings.append("未找到高品質 PDF 出圖設定，已使用可用設定；大型圖面的細字精度可能較低。")
    if not re.search(r"\b4A0\b", media, re.I):
        warnings.append(f"出圖設定未提供 4A0 虛擬紙張，已改用 {media}；極小文字放大後仍可能失真。")
    if measured[0]["units"] != measured[1]["units"]:
        raise PreviewError("兩版 DWG 的圖面單位不同，請先在 AutoCAD 確認單位後再比較。")
    bounds = _common_bounds(measured[0]["bounds"], measured[1]["bounds"])
    if any(item["has_xrefs"] for item in measured):
        warnings.append("外部參考使用各圖檔目前可找到的版本；若要比較歷史套圖，請連同當時的外部參考一起保留。")
    paths = []
    for source, stem, label in zip(sources, ("old", "new"), ("舊版", "新版")):
        _check_cancel(cancel_event)
        check_sources()
        report(f"正在產生{label}預覽：{source.name}")
        pdf, png = job / f"{stem}.pdf", job / f"{stem}.png"
        text = _run_console(engine, source, _plot_script(bounds, plotter, pdf, media), job, cancel_event)
        if not pdf.is_file() or pdf.stat().st_size < 100 or "\nCDS_PLOT_DONE" not in text:
            raise PreviewError(f"{label}出圖失敗，請確認 AutoCAD 的 PDF 出圖設定。")
        _check_cancel(cancel_event)
        _rasterize(pdf, png)
        paths.append(str(png))
    _check_cancel(cancel_event)
    check_sources()
    return {"old_png": paths[0], "new_png": paths[1],
            "old_pdf": str(job / "old.pdf"), "new_pdf": str(job / "new.pdf"),
            "warnings": warnings}
