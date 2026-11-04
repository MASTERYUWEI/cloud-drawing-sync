"""Exercise the real download function without login or user configuration."""
import ast
import io
import os
import shutil
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import revision_archive

source = Path(__file__).resolve().parents[1] / 'drive_sync_gui.py'
tree = ast.parse(source.read_text(encoding='utf-8-sig'))
function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'download_file')

class Downloader:
    def __init__(self, stream, request):
        self.stream = stream
    def next_chunk(self):
        self.stream.write(b'new download')
        return None, True

namespace = dict(os=os, io=io, tempfile=tempfile, revision_archive=revision_archive,
                 GOOGLE_EXPORT_TYPES={}, MediaIoBaseDownload=Downloader)
exec(compile(ast.Module(body=[function],type_ignores=[]),str(source),'exec'),namespace)
download = namespace['download_file']

class DownloadRevisionTests(unittest.TestCase):
    def test_download_archives_existing_and_returns_unchanged_api_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)/'plan.dwg'
            target.write_bytes(b'old download')
            result = download(Mock(),'id','plan.dwg','dwg','',tmp,archive_updates=True)
            self.assertEqual(result,str(target))
            saved = revision_archive.latest_revision(tmp,target)
            self.assertEqual(Path(saved['old_path']).read_bytes(),b'old download')
            self.assertEqual(target.read_bytes(),b'new download')

    def test_backup_failure_protects_target_and_cleans_download_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)/'plan.dwg'
            target.write_bytes(b'old download')
            with patch.object(revision_archive.os,'rename',side_effect=OSError('target in use')):
                with self.assertRaises(OSError):
                    download(Mock(),'id','plan.dwg','dwg','',tmp,archive_updates=True)
            self.assertEqual(target.read_bytes(),b'old download')
            self.assertFalse(list(Path(tmp).glob('.download-*.tmp')))

    def test_other_pairs_keep_opt_out_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)/'plan.dwg'
            target.write_bytes(b'old')
            download(Mock(),'id','plan.dwg','dwg','',tmp)
            self.assertEqual(target.read_bytes(),b'new download')
            self.assertFalse((Path(tmp)/'回收區').exists())


class FlattenRevisionTests(unittest.TestCase):
    def run_case(self, fail=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_dir = root/'details'
            source_dir.mkdir()
            (source_dir/'plan.dwg').write_bytes(b'new detailed drawing')
            target = root/'plan.dwg'
            target.write_bytes(b'old')
            save = Mock()
            ns = dict(os=os, shutil=shutil, tempfile=tempfile, revision_archive=revision_archive,
                      RECYCLE_DIR='回收區', MASTER_PREFIX='☆', XREF_SKIP_TOKENS=(),
                      _load_xref_manifest=lambda path: {'plan.dwg'}, _save_xref_manifest=save,
                      _find_master_folders=lambda path: [path])
            nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name in ('flatten_xrefs', '_needs_copy')]
            exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),ns)
            if fail:
                with patch.object(revision_archive,'install_revision',side_effect=OSError('disk full')):
                    result = ns['flatten_xrefs'](tmp,log=lambda text: None,archive_updates=True)
                self.assertEqual(target.read_bytes(),b'old')
                self.assertEqual(result['removed'],0)
                save.assert_called_once_with(tmp,{'plan.dwg'})
            else:
                result = ns['flatten_xrefs'](tmp,log=lambda text: None,archive_updates=True)
                self.assertEqual(result['copied'],1)
                saved = revision_archive.latest_revision(tmp,target)
                self.assertEqual(Path(saved['old_path']).read_bytes(),b'old')
                self.assertEqual(target.read_bytes(),b'new detailed drawing')
            self.assertFalse(list(root.glob('.xref-*.tmp')))

    def test_flatten_archives_old_managed_copy(self):
        self.run_case()

    def test_failed_archive_does_not_cleanup_old_managed_copy(self):
        self.run_case(fail=True)
