"""Exercise the app entry point without importing sync configuration or tokens."""
import ast
from pathlib import Path
import tempfile
import revision_archive
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
previous_entry = next(node for node in app_class.body
                      if isinstance(node, ast.FunctionDef) and node.name == '_compare_previous_version')
namespace['revision_archive'] = revision_archive
exec(compile(ast.Module(body=[previous_entry], type_ignores=[]), str(source), 'exec'), namespace)
open_previous = namespace['_compare_previous_version']


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

    def test_one_click_archive_comparison_preserves_old_new_direction(self):
        window = Mock()
        window.winfo_exists.return_value = True
        open_compare(SimpleNamespace(_dwg_compare_window=window), 'current.dwg', 'saved-old.dwg', True)
        window.set_pair_paths.assert_called_once_with('saved-old.dwg', 'current.dwg', auto_start=True)
        window.set_new_path.assert_not_called()

    def test_saved_revision_lookup_dispatches_actual_paths_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            current = Path(tmp)/'plan.dwg'
            staged = Path(tmp)/'download.tmp'
            current.write_bytes(b'old')
            staged.write_bytes(b'new')
            saved = revision_archive.install_revision(staged,current,tmp)
            app = SimpleNamespace(is_syncing=False, config={'sync_pairs':[{'download_path':tmp}]},
                                  _resolve_today_file=Mock(return_value=str(current)),
                                  _open_dwg_compare=Mock(), _open_in_explorer=Mock())
            open_previous(app,'folder','plan.dwg')
            app._open_dwg_compare.assert_called_once_with(initial_new_path=str(current),
                                                         initial_old_path=saved,auto_start=True)
            open_previous(app,'folder','plan.dwg',open_folder=True)
            app._open_in_explorer.assert_called_once_with(saved)
            app._open_dwg_compare.reset_mock()
            app.is_syncing=True
            open_previous(app,'folder','plan.dwg')
            app._open_dwg_compare.assert_not_called()


if __name__ == "__main__":
    unittest.main()
