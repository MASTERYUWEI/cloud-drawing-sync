"""Open a saved local DWG pair without starting synchronization or login."""
import argparse
import json
from pathlib import Path


def load_comparison_session(path):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("比對工作階段必須包含舊版與新版路徑。")
    paths = []
    for key in ("old_dwg", "new_dwg"):
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"比對工作階段缺少 {key}。")
        target = Path(value)
        if not target.is_absolute() or target.suffix.lower() != ".dwg":
            raise ValueError("請使用 DWG 的完整絕對路徑。")
        if not target.is_file():
            raise ValueError(f"無法讀取圖檔，請確認網路磁碟連線：\n{target}")
        paths.append(str(target))
    return tuple(paths)


def main(version, args):
    import customtkinter as ctk
    from tkinter import messagebox
    from dwg_compare_window import DwgCompareWindow

    parser = argparse.ArgumentParser(description="開啟已儲存的本機 DWG 比對工作階段")
    parser.add_argument("session", help="包含 old_dwg / new_dwg 完整路徑的 JSON")
    parser.add_argument("--fullscreen", action="store_true", help="直接以全螢幕看圖模式開啟")
    options = parser.parse_args(args)
    ctk.set_appearance_mode("light")
    root = ctk.CTk()
    root.withdraw()
    try:
        old, new = load_comparison_session(options.session)
    except (OSError, ValueError) as exc:
        messagebox.showerror("無法載入比對工作階段", str(exc), parent=root)
        root.destroy()
        return
    window = DwgCompareWindow(root, new)
    window.title(f"DWG 圖面變更比對 v{version} · 大型圖面測試")
    window._old_path.set(old)
    window._set_controls()
    window.protocol("WM_DELETE_WINDOW", root.destroy)
    if options.fullscreen:
        window.after(200, lambda: window.set_fullscreen(True))
    window.after(400, window._start_render)
    root.mainloop()
