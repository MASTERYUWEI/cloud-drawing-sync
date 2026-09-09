"""Open the real comparison window with generated DWGs; no sync credentials."""
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import customtkinter as ctk
from dwg_compare_window import DwgCompareWindow


if __name__ == "__main__":
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    ctk.set_appearance_mode("light")
    root = ctk.CTk()
    root.withdraw()
    window = DwgCompareWindow(root, data["new_dwg"])
    window.title("DWG 圖面變更比對 v1.2.7 · 全螢幕看圖測試")
    window._old_path.set(data["old_dwg"])
    window._set_controls()
    window.protocol("WM_DELETE_WINDOW", root.destroy)
    window.after(400, window._start_render)
    root.mainloop()
