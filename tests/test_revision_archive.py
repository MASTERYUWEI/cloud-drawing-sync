from pathlib import Path
from datetime import datetime
import tempfile
import unittest
from unittest.mock import patch
import revision_archive as archive


class RevisionArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.dest = self.root/'drawings'/'plan.dwg'
        self.dest.parent.mkdir()
        self.dest.write_bytes(b'old drawing')
        self.download = self.dest.with_suffix('.tmp')
        self.download.write_bytes(b'new drawing')

    def tearDown(self):
        self.temp.cleanup()

    def test_move_old_then_install_and_find_after_restart(self):
        saved = archive.install_revision(self.download, self.dest, self.root)
        self.assertEqual(Path(saved).read_bytes(), b'old drawing')
        self.assertEqual(self.dest.read_bytes(), b'new drawing')
        record = archive.latest_revision(self.root, self.dest)
        self.assertEqual(record['old_path'], saved)
        self.assertEqual(record['new_path'], str(self.dest))
        self.assertEqual(Path(saved).relative_to(self.root).parts[:3],
                         ('回收區',datetime.now().strftime('%Y-%m-%d'),'更新留存'))

    def test_repeated_updates_keep_all_predecessors(self):
        first = archive.install_revision(self.download, self.dest, self.root)
        self.download.write_bytes(b'third drawing')
        second = archive.install_revision(self.download, self.dest, self.root)
        self.assertNotEqual(first, second)
        self.assertEqual(Path(first).read_bytes(), b'old drawing')
        self.assertEqual(Path(second).read_bytes(), b'new drawing')
        self.assertEqual(archive.latest_revision(self.root, self.dest)['old_path'], second)

    def test_backup_failure_never_overwrites_original(self):
        with patch.object(archive.os, 'rename', side_effect=OSError('target in use')):
            with self.assertRaises(OSError):
                archive.install_revision(self.download, self.dest, self.root)
        self.assertEqual(self.dest.read_bytes(), b'old drawing')
        self.assertIsNone(archive.latest_revision(self.root, self.dest))

    def test_failed_new_install_restores_old_and_does_not_publish_revision(self):
        original = archive.os.rename
        def locked(src, dst):
            if Path(src) == self.download:
                raise PermissionError('target in use')
            return original(src, dst)
        with patch.object(archive.os, 'rename', side_effect=locked):
            with self.assertRaises(PermissionError):
                archive.install_revision(self.download, self.dest, self.root)
        self.assertEqual(self.dest.read_bytes(), b'old drawing')
        self.assertIsNone(archive.latest_revision(self.root, self.dest))
        self.assertTrue(list(self.root.rglob('revision.json')))

    def test_commit_metadata_failure_recovers_from_new_hash(self):
        original = archive._write_record
        def fail_commit(path, record):
            if record['state'] == 'committed':
                raise OSError('metadata temporarily unavailable')
            return original(path, record)
        with patch.object(archive, '_write_record', side_effect=fail_commit):
            saved = archive.install_revision(self.download, self.dest, self.root)
        self.assertEqual(archive.latest_revision(self.root, self.dest)['old_path'], saved)

    def test_same_content_does_not_create_redundant_backup(self):
        self.download.write_bytes(self.dest.read_bytes())
        self.assertIsNone(archive.install_revision(self.download, self.dest, self.root))
        self.assertFalse((self.root/'回收區').exists())

    def test_identical_names_in_other_folders_are_separate(self):
        first = archive.install_revision(self.download, self.dest, self.root)
        other = self.root/'other'/'plan.dwg'
        other.parent.mkdir()
        other.write_bytes(b'other old')
        self.download.write_bytes(b'other new')
        second = archive.install_revision(self.download, other, self.root)
        self.assertNotEqual(first, second)
        self.assertEqual(Path(archive.latest_revision(self.root, other)['old_path']).read_bytes(), b'other old')

    def test_outside_root_and_recycle_targets_rejected(self):
        for target in (self.root.parent/'outside.dwg', self.root/'回收區'/'file.dwg'):
            with self.assertRaises(ValueError):
                archive.install_revision(self.download, target, self.root)

    def test_concurrent_change_aborts_install(self):
        original = archive._write_record
        def changing(path, record):
            result = original(path, record)
            self.dest.write_bytes(b'locally saved during backup')
            return result
        with patch.object(archive, '_write_record', side_effect=changing):
            with self.assertRaises(OSError):
                archive.install_revision(self.download, self.dest, self.root)
        self.assertEqual(self.dest.read_bytes(), b'locally saved during backup')

    def test_primary_default_and_explicit_setting(self):
        self.assertTrue(archive.enabled({'name':'主要圖資'}))
        self.assertFalse(archive.enabled({'name':'業主送審圖端'}))
        self.assertFalse(archive.enabled({'name':'主要圖資','archive_updates':False}))

    def test_invalid_manifest_is_ignored(self):
        saved = archive.install_revision(self.download, self.dest, self.root)
        (Path(saved).parent/'revision.json').write_text('[]', encoding='utf-8')
        self.assertIsNone(archive.latest_revision(self.root, self.dest))

    def test_old_moved_to_dated_folder_before_new_is_installed(self):
        original = archive.os.rename
        moves = []
        def observe(src, dst):
            moves.append((Path(src),Path(dst)))
            if Path(src) == self.download:
                self.assertFalse(self.dest.exists())
                self.assertEqual(moves[0][1].read_bytes(),b'old drawing')
                self.assertTrue((moves[0][1].parent/'revision.json').is_file())
            return original(src,dst)
        with patch.object(archive.os,'rename',side_effect=observe):
            saved = archive.install_revision(self.download,self.dest,self.root)
        self.assertEqual(moves,[(self.dest,Path(saved)),(self.download,self.dest)])

    def test_failed_restore_keeps_old_in_archive_and_reports_location(self):
        original = archive.os.rename
        def fail_new_and_restore(src,dst):
            if Path(dst) == self.dest:
                raise PermissionError('network interrupted')
            return original(src,dst)
        with patch.object(archive.os,'rename',side_effect=fail_new_and_restore):
            with self.assertRaisesRegex(OSError,'舊檔仍安全留存在'):
                archive.install_revision(self.download,self.dest,self.root)
        self.assertFalse(self.dest.exists())
        saved = list((self.root/'回收區').rglob('plan.dwg'))
        self.assertEqual(len(saved),1)
        self.assertEqual(saved[0].read_bytes(),b'old drawing')

    def test_legacy_archive_remains_readable_and_newer_dated_revision_wins(self):
        saved = Path(archive.install_revision(self.download,self.dest,self.root))
        legacy_bucket = archive._bucket(self.root,self.dest.relative_to(self.root))
        legacy_bucket.mkdir(parents=True)
        legacy_dir = legacy_bucket/saved.parent.name
        saved.parent.rename(legacy_dir)
        legacy_file = legacy_dir/self.dest.name
        self.assertEqual(archive.latest_revision(self.root,self.dest)['old_path'],str(legacy_file))
        self.download.write_bytes(b'third')
        newest = archive.install_revision(self.download,self.dest,self.root)
        self.assertEqual(archive.latest_revision(self.root,self.dest)['old_path'],newest)
        self.assertEqual(legacy_file.read_bytes(),b'old drawing')

    def test_revision_lookup_across_recycle_dates(self):
        with patch.object(archive,'datetime',wraps=datetime) as clock:
            clock.now.return_value = datetime(2026,9,13,10).astimezone()
            first = archive.install_revision(self.download,self.dest,self.root)
            self.download.write_bytes(b'third')
            clock.now.return_value = datetime(2026,9,14,10).astimezone()
            latest = archive.install_revision(self.download,self.dest,self.root)
        self.assertEqual(Path(first).relative_to(self.root).parts[1],'2026-09-13')
        self.assertEqual(Path(latest).relative_to(self.root).parts[1],'2026-09-14')
        self.assertEqual(archive.latest_revision(self.root,self.dest)['old_path'],latest)


if __name__ == '__main__':
    unittest.main()
