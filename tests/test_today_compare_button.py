"""Real embedded Tk buttons, without loading sync configuration or credentials."""
import ast
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import tkinter as tk
import unittest
from unittest.mock import Mock

source = Path(__file__).resolve().parents[1]/'drive_sync_gui.py'
tree = ast.parse(source.read_text(encoding='utf-8-sig'))
app_class = next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name == 'DriveSyncApp')
render_node = next(n for n in app_class.body if isinstance(n,ast.FunctionDef) and n.name == '_render_today')
namespace = dict(tk=tk,datetime=datetime,Colors=SimpleNamespace(RUST='#63311e',ACCENT='#3B6DB4',TEXT_MUTED='#777777'))
exec(compile(ast.Module(body=[render_node],type_ignores=[]),str(source),'exec'),namespace)
render = namespace['_render_today']


class TodayCompareButtonTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.text = tk.Text(self.root)
        self.app = SimpleNamespace(
            today_text=self.text, FONT_FAMILY='Microsoft JhengHei',
            _today_new=[], _today_updated=['plan.dwg'], _today_last_ts=None,
            _today_collapsed=set(), _group_update_paths=lambda items: [('folder',items)],
            _build_today_full_text=Mock(return_value='Plain copied notification'),
            _toggle_today_group=Mock(), _compare_previous_version=Mock(),
            _open_today_file=Mock(),_show_today_file_menu=Mock(),
            lbl_today_title=Mock(),_set_tab_badge=Mock(),_switch_tab=Mock())

    def tearDown(self):
        self.root.destroy()

    def test_real_button_dispatches_compare_without_opening_dwg_link(self):
        render(self.app)
        buttons = self.app._today_compare_buttons
        self.assertEqual(len(buttons),1)
        self.assertIsInstance(buttons[0],tk.Button)
        self.assertEqual(buttons[0].cget('text'),'比對上一版')
        self.assertEqual(len(self.text.window_names()),1)
        self.assertNotIn('[比對上一版]',self.text.get('1.0','end'))
        self.assertTrue(self.text.tag_bind('todayfile_0','<Button-1>',None))
        buttons[0].invoke()
        self.app._compare_previous_version.assert_called_once_with('folder','plan.dwg')
        self.app._open_today_file.assert_not_called()

    def test_refresh_and_collapse_destroy_stale_buttons(self):
        render(self.app)
        first = self.app._today_compare_buttons[0]
        render(self.app)
        self.assertFalse(first.winfo_exists())
        self.assertEqual(len(self.text.window_names()),1)
        self.app._today_collapsed.add('更新\x00folder')
        render(self.app)
        self.assertEqual(self.app._today_compare_buttons,[])
        self.assertEqual(len(self.text.window_names()),0)

    def test_new_dwg_and_updated_pdf_have_no_compare_button(self):
        self.app._today_updated=['report.pdf']
        self.app._today_new=['new.dwg']
        render(self.app)
        self.assertEqual(self.app._today_compare_buttons,[])
