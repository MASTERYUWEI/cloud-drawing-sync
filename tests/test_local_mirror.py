# -*- coding: utf-8 -*-
import os
import sys
import time
import shutil
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import local_mirror as lm  # noqa: E402


def _w(path, data=b'x', mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data)
    if mtime:
        os.utime(path, (mtime, mtime))


class LocalMirrorTest(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp(prefix='mirror_')
        self.src = os.path.join(self.work, 'MEGA', '06.施工圖')
        self.nas = os.path.join(self.work, 'NAS')
        self.dst = os.path.join(self.nas, '11_MEGA施工圖')
        self.logs = os.path.join(self.work, 'logs')
        self.pair = {'name': '施工圖', 'source_path': self.src,
                     'download_path': self.dst, 'exclude_dirs': ['雲端']}

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def test_mirror_diff_junk_exclude_and_locked(self):
        old = time.time() - 3600
        _w(os.path.join(self.src, 'a', 'A.dwg'), b'AAAA', old)
        _w(os.path.join(self.src, 'a', 'B.dwg'), b'BBBB-NEW', old)
        _w(os.path.join(self.src, 'b', 'C.dwg'), b'CCC')
        _w(os.path.join(self.src, 'a', 'A.bak'), b'junk')
        _w(os.path.join(self.src, '.debris', 'trash.dwg'))
        _w(os.path.join(self.src, '雲端', 'owner.dwg'))
        _w(os.path.join(self.dst, 'a', 'A.dwg'), b'AAAA', old + 1)
        _w(os.path.join(self.dst, 'a', 'B.dwg'), b'BBBB', old)
        _w(os.path.join(self.dst, 'a', 'Gone.dwg'))
        _w(os.path.join(self.dst, 'a', 'x.dwl'))
        _w(os.path.join(self.dst, '雲端', 'keep.dwg'))
        _w(os.path.join(self.dst, 'b', 'Locked.dwg'))

        with open(os.path.join(self.dst, 'b', 'Locked.dwg'), 'rb'):
            r = lm.mirror(self.pair, self.logs, log=lambda s: None)

        acts = {(d['action'], d['rel']) for d in r['diff']}
        self.assertEqual(acts, {('更新', os.path.join('a', 'B.dwg')),
                                ('新增', os.path.join('b', 'C.dwg')),
                                ('刪除', os.path.join('a', 'Gone.dwg')),
                                ('刪除', os.path.join('b', 'Locked.dwg'))})
        self.assertTrue(r['ok'])
        self.assertEqual(r['stuck'], 1)
        with open(os.path.join(self.dst, 'a', 'B.dwg'), 'rb') as f:
            self.assertEqual(f.read(), b'BBBB-NEW')
        self.assertFalse(os.path.exists(os.path.join(self.dst, 'a', 'Gone.dwg')))
        self.assertFalse(os.path.exists(os.path.join(self.dst, 'a', 'A.bak')))
        self.assertFalse(os.path.exists(os.path.join(self.dst, 'a', 'x.dwl')))
        self.assertFalse(os.path.exists(os.path.join(self.dst, '.debris')))
        self.assertFalse(os.path.exists(os.path.join(self.dst, '雲端', 'owner.dwg')))
        self.assertTrue(os.path.exists(os.path.join(self.dst, '雲端', 'keep.dwg')))

        r2 = lm.mirror(self.pair, self.logs, log=lambda s: None)
        self.assertEqual([d['rel'] for d in r2['diff']], [os.path.join('b', 'Locked.dwg')])
        self.assertEqual(r2['stuck'], 0)

    def test_empty_or_missing_source_never_touches_target(self):
        _w(os.path.join(self.dst, 'keep.dwg'))
        empty = os.path.join(self.work, 'empty')
        os.makedirs(os.path.join(empty, '.debris'))
        for src in (empty, os.path.join(self.work, 'missing')):
            with self.assertRaises(lm.MirrorError):
                lm.mirror(dict(self.pair, source_path=src), self.logs)
        self.assertTrue(os.path.exists(os.path.join(self.dst, 'keep.dwg')))

    def test_protected_owner_folder_blocks_mirror_at_any_depth(self):
        _w(os.path.join(self.src, 'x.dwg'))
        for rel in (('套圖雲端自動同步(勿改名)',),
                    ('a', 'b', 'c', 'd', 'e', '業主送審雲端自動同步(勿改名)')):
            dest = os.path.join(self.nas, 'case%d' % len(rel))
            owner = os.path.join(dest, *rel, 'owner.pdf')
            _w(owner)
            with self.assertRaises(lm.MirrorError):
                lm.mirror(dict(self.pair, download_path=dest), self.logs, log=lambda s: None)
            self.assertTrue(os.path.exists(owner))

    def test_overlap_with_other_sync_folder_is_refused(self):
        _w(os.path.join(self.src, 'x.dwg'))
        with self.assertRaises(lm.MirrorError):
            lm.mirror(self.pair, self.logs, other_roots=[os.path.join(self.dst, 'sub')])

    def test_lock_excludes_other_threads(self):
        name = 'Local\\test_mirror_lock_%d' % os.getpid()
        lock = lm.SyncLock(name)
        self.assertTrue(lock.acquire())
        got = []
        t = threading.Thread(target=lambda: got.append(lm.SyncLock(name).acquire()))
        t.start(); t.join()
        self.assertEqual(got, [False])
        lock.release()

    def test_normalize_exclude_dirs(self):
        self.assertEqual(
            lm.normalize_exclude_dirs(self.src, [os.path.join(self.src, '雲端'), '雲端', ' ', '02機電\\']),
            ['雲端', '02機電'])


if __name__ == '__main__':
    unittest.main()
