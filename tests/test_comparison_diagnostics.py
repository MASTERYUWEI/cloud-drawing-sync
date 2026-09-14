from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock

from comparison_diagnostics import ComparisonDiagnostics
import dwg_preview as preview


class DiagnosticTests(unittest.TestCase):
    def test_common_private_fields_are_scrubbed_but_error_survives(self):
        diag = ComparisonDiagnostics((r'C:\Users\Alice\SecretJob\old.dwg', r'U:\new.dwg'))
        raw = ('AutoCAD 2024\nUnable to plot: invalid media\n'
               'C:\\Users\\Alice\\SecretJob\\old.dwg\n'
               '\\\\server\\private\\other.dwg\n'
               'alice@example.com\nrefresh_token=secret123\n'
               '(command (strcat "C:/" (chr 20000)))\n')
        diag.note(stage='舊版：PDF 出圖', media='ISO 4A0', exit_code=0)
        diag.console(raw)
        report = diag.report(RuntimeError('No PDF output'))
        for secret in ('Alice', 'SecretJob', 'old.dwg', 'server', 'alice@example.com', 'secret123', '(chr 20000)'):
            self.assertNotIn(secret, report)
        self.assertIn('invalid media', report)
        self.assertIn('AutoCAD 2024', report)
        self.assertIn('ISO 4A0', report)

    def test_reports_are_bounded_and_separate_per_comparison(self):
        first, second = ComparisonDiagnostics(), ComparisonDiagnostics()
        for i in range(10):
            first.console('x' * 30000 + f'END{i}')
        self.assertEqual(len(first.events), 4)
        self.assertLess(len(first.report(RuntimeError('test'))), 68000)
        self.assertIn('END9', first.report(RuntimeError('test')))
        self.assertFalse(second.events)

    def test_nonzero_console_exit_retains_output_before_temp_cleanup(self):
        diag = ComparisonDiagnostics()
        with tempfile.TemporaryDirectory() as tmp:
            def launch(*args, **kwargs):
                kwargs['stdout'].write('AutoCAD 2024\nPLOT ERROR: invalid configuration\n'.encode('utf-16'))
                return Mock(returncode=7, poll=Mock(return_value=7))
            with patch.object(preview.subprocess, 'Popen', side_effect=launch):
                with self.assertRaises(preview.PreviewError):
                    preview._run_console('engine.exe', Path(tmp)/'old.dwg', '(quit)', Path(tmp), diagnostics=diag)
        self.assertIn('invalid configuration', diag.events[0]['console'])
        self.assertEqual(diag.context['exit_code'], '7')

    def test_timeout_retains_tail_and_kills_only_owned_process(self):
        diag = ComparisonDiagnostics()
        process = Mock(returncode=-1, poll=Mock(return_value=None))
        with tempfile.TemporaryDirectory() as tmp:
            def launch(*args, **kwargs):
                kwargs['stdout'].write(b'Waiting for plot device')
                return process
            with patch.object(preview.subprocess, 'Popen', side_effect=launch):
                with self.assertRaisesRegex(preview.PreviewError, '逾時'):
                    preview._run_console('engine.exe', Path(tmp)/'old.dwg', '(quit)', Path(tmp), timeout=-1, diagnostics=diag)
        process.kill.assert_called_once()
        self.assertIn('Waiting for plot device', diag.events[0]['console'])

    def test_cancel_stays_cancelled(self):
        cancel = threading.Event()
        cancel.set()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(preview.PreviewCancelled):
                preview._run_console('engine.exe', Path(tmp)/'old.dwg', '(quit)', Path(tmp), cancel, diagnostics=ComparisonDiagnostics())

    def test_launch_failure_does_not_reuse_previous_success_status(self):
        diag = ComparisonDiagnostics()
        diag.note(exit_code=0, pdf_exists=True)
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(preview.subprocess, 'Popen', side_effect=OSError('launch denied')):
            with self.assertRaises(preview.PreviewError):
                preview._run_console('engine.exe', Path(tmp)/'old.dwg', '(quit)', Path(tmp), diagnostics=diag)
        self.assertEqual(diag.context['exit_code'], 'not started')
        self.assertEqual(diag.context['pdf_exists'], 'not checked')

    def test_missing_pdf_with_success_exit_retains_actionable_plot_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old, new = root/'old.dwg', root/'new.dwg'
            old.touch()
            new.touch()
            diag = ComparisonDiagnostics((old, new))
            calls = []
            def run(engine, source, script, job, cancel_event, timeout, diagnostics):
                calls.append(script)
                if 'CDS_PLOT_DONE' in script:
                    text = '\nInvalid plot device configuration\nCDS_PLOT_DONE\n'
                else:
                    text = '\nCDS_EXTENTS|0|0|10|10\nCDS_UNITS|4\nCDS_MEASURE_DONE\n  "ISO A3 (420.00 x 297.00 MM)"\n'
                (job / f'call{len(calls)}.log').write_bytes(text.encode('utf-16'))
                diagnostics.note(exit_code=0)
                return text
            with patch.object(preview,'find_core_console',return_value='AutoCAD 2024/accoreconsole.exe'), \
                    patch.object(preview,'_find_pdf_plotter',return_value=Path('DWG To PDF.pc3')), \
                    patch.object(preview,'_run_console_impl',side_effect=run):
                try:
                    preview.render_dwg_pair(old,new,root,diagnostics=diag)
                except preview.PreviewError as exc:
                    report = diag.report(exc)
                else:
                    self.fail('Missing PDF must fail')
        self.assertIn('Invalid plot device configuration', report)
        self.assertEqual(diag.context['pdf_exists'], 'False')
        self.assertEqual(diag.context['plot_done'], 'True')
        self.assertEqual(diag.context['exit_code'], '0')
        self.assertEqual(diag.context['stage'], '舊版：PDF 出圖')
