"""Exercise the app entry point without importing sync configuration or tokens."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


source = Path(__file__).resolve().parents[1] / "drive_sync_gui.py"
tree = ast.parse(source.read_text(encoding="utf-8-sig"))
app_class = next(node for node in tree.body
                 if isinstance(node, ast.ClassDef) and node.name == "DriveSyncApp")
entry = next(node for node in app_class.body
             if isinstance(node, ast.FunctionDef) and node.name == "_open_dwg_compare")
namespace = {"messagebox": Mock()}
exec(compile(ast.Module(body=[entry], type_ignores=[]), str(source), "exec"), namespace)
open_compare = namespace["_open_dwg_compare"]


class CompareEntryChecks(unittest.TestCase):
    def test_new_window_receives_selected_dwg(self):
        app, constructor = SimpleNamespace(), Mock()
        with patch.dict("sys.modules", {"dwg_compare_window": SimpleNamespace(DwgCompareWindow=constructor)}):
            open_compare(app, "new.dwg")
        constructor.assert_called_once_with(app, "new.dwg")
        self.assertIs(app._dwg_compare_window, constructor.return_value)

    def test_existing_window_receives_new_selection(self):
        window = Mock()
        window.winfo_exists.return_value = True
        open_compare(SimpleNamespace(_dwg_compare_window=window), "second.dwg")
        window.set_new_path.assert_called_once_with("second.dwg")
        window.lift.assert_called_once_with()

    def test_toolbar_keeps_existing_selection(self):
        window = Mock()
        window.winfo_exists.return_value = True
        open_compare(SimpleNamespace(_dwg_compare_window=window))
        window.set_new_path.assert_not_called()
        window.deiconify.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
