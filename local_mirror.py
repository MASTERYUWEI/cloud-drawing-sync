# -*- coding: utf-8 -*-
"""本機資料夾 → 目標資料夾（通常是 NAS）單向鏡像。

規格沿用 PowerShell 版「MEGA同步到NAS.ps1」：
- 以來源為準完全鏡像（來源刪除，目標也刪），實際搬檔交給 robocopy /MIR。
- 同步前比對兩邊（大小＋修改時間 2 秒容差）列出新增/更新/刪除。
- 排除 CAD 備份/鎖定檔、Revit 自動備份、系統暫存與 MEGA 的 .debris；
  目標上已存在的這類檔也清掉（robocopy /MIR 不會刪被排除的檔）。
- 安全檢查：來源不存在或是空的就不同步；目標上層連不到就跳過。
- 被別人開著刪不掉的檔不算失敗，標示後下次再試。
- 與 PowerShell 版共用同一個系統互斥鎖，兩邊不會同時寫同一個 NAS。
"""

import os
import csv
import ctypes
import fnmatch
import subprocess
from datetime import datetime, timedelta

EXCLUDE_FILES = (
    '*.bak', '*.dwl', '*.dwl2', '*.sv$', '*.ac$', '*.err', 'plot.log',   # AutoCAD
    '*.0???.rvt', '*.0???.rfa',                                          # Revit 自動備份
    'desktop.ini', 'Thumbs.db', '~$*', '*.tmp',                          # 系統/Office 暫存
)
DEBRIS_DIR = '.debris'                     # MEGA 回收桶
# NAS 上業主的雲端資料夾，與本機鏡像無關，絕對不可被鏡像刪除
PROTECTED_DIR_NAMES = ('套圖雲端自動同步(勿改名)', '業主送審雲端自動同步(勿改名)')
MUTEX_NAME = 'Global\\MEGA_to_NAS_Sync'    # 與 PowerShell 版相同
MTIME_TOLERANCE = 2.0                      # NAS 時間精度較低
KEEP_LOG_DAYS = 30

_CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


class MirrorError(Exception):
    """無法同步（安全檢查未過、來源/目標不可用）。訊息可直接給使用者看。"""


# ──────────────────────────── 路徑工具 ────────────────────────────

def is_junk(name):
    low = name.lower()
    return any(fnmatch.fnmatchcase(low, pat.lower()) for pat in EXCLUDE_FILES)


def to_unc(path):
    """網路磁碟機代號（Z:\\…）轉成 UNC（\\\\server\\share\\…）；本機磁碟或失敗則原樣。"""
    p = os.path.abspath(path)
    drive, rest = os.path.splitdrive(p)
    if len(drive) == 2 and drive[1] == ':' and os.name == 'nt':
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.c_ulong(len(buf))
            if ctypes.windll.mpr.WNetGetConnectionW(drive, buf, ctypes.byref(size)) == 0 and buf.value:
                return buf.value.rstrip('\\') + rest
        except Exception:
            pass
    return p


def norm(path):
    return os.path.normcase(to_unc(path)).rstrip('\\/')


def paths_overlap(a, b):
    """兩路徑相同或互相包含（含網路磁碟機 ↔ UNC 對照）。"""
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    return na == nb or na.startswith(nb + os.sep) or nb.startswith(na + os.sep)


def normalize_exclude_dirs(source, items):
    """排除資料夾一律存成「相對於來源」的路徑；接受絕對路徑或相對路徑輸入。"""
    out = []
    src = os.path.abspath(source)
    for raw in items or []:
        s = str(raw).strip().strip('"').strip()
        if not s:
            continue
        if os.path.isabs(s):
            try:
                rel = os.path.relpath(os.path.abspath(s), src)
            except ValueError:
                continue
            if rel.startswith('..'):
                continue
        else:
            rel = s
        rel = os.path.normpath(rel).strip('\\/')
        if rel and rel != '.' and rel not in out:
            out.append(rel)
    return out


def _under(rel_key, excl_keys):
    return any(rel_key == k or rel_key.startswith(k + os.sep) for k in excl_keys)


# ──────────────────────────── 掃描 / 比對 ────────────────────────────

def scan_source(source, exclude_rel):
    """{normcase rel: (rel, size, mtime)}，略過 .debris、排除資料夾與垃圾檔。"""
    excl = [os.path.normcase(e) for e in exclude_rel]
    out = {}
    for cur, dirs, files in os.walk(source):
        rel_dir = os.path.relpath(cur, source)
        rel_dir = '' if rel_dir == '.' else rel_dir
        keep = []
        for d in dirs:
            rd = os.path.join(rel_dir, d) if rel_dir else d
            if d.lower() == DEBRIS_DIR or _under(os.path.normcase(rd), excl):
                continue
            keep.append(d)
        dirs[:] = keep
        for fn in files:
            if is_junk(fn):
                continue
            rel = os.path.join(rel_dir, fn) if rel_dir else fn
            try:
                st = os.stat(os.path.join(cur, fn))
            except OSError:
                continue
            out[os.path.normcase(rel)] = (rel, st.st_size, st.st_mtime)
    return out


def scan_dest(dest, exclude_rel, protected_hits=None):
    """回傳 (正常檔 {key:(rel,size,mtime)}, 垃圾檔 [完整路徑])；略過受保護資料夾與排除資料夾。

    protected_hits 若給 list，會收集目標內任何深度的業主雲端資料夾路徑。
    （robocopy /MIR 會把「來源沒有的上層資料夾」整棵刪掉，/XD 擋不住，
    所以只要目標內任何位置有受保護資料夾就必須整組停止。）"""
    excl = [os.path.normcase(e) for e in exclude_rel]
    protected = {n.lower() for n in PROTECTED_DIR_NAMES}
    files_out, junk = {}, []
    if not os.path.isdir(dest):
        return files_out, junk
    for cur, dirs, files in os.walk(dest):
        rel_dir = os.path.relpath(cur, dest)
        rel_dir = '' if rel_dir == '.' else rel_dir
        keep = []
        for d in dirs:
            rd = os.path.join(rel_dir, d) if rel_dir else d
            if d.lower() in protected:
                if protected_hits is not None:
                    protected_hits.append(os.path.join(cur, d))
                continue
            if d.lower() == DEBRIS_DIR or _under(os.path.normcase(rd), excl):
                continue
            keep.append(d)
        dirs[:] = keep
        for fn in files:
            full = os.path.join(cur, fn)
            if is_junk(fn):
                junk.append(full)
                continue
            rel = os.path.join(rel_dir, fn) if rel_dir else fn
            try:
                st = os.stat(full)
            except OSError:
                continue
            files_out[os.path.normcase(rel)] = (rel, st.st_size, st.st_mtime)
    return files_out, junk


def compute_diff(src_map, dst_map):
    """[{action:'新增'|'更新'|'刪除', rel, kb, mtime}]"""
    diff = []
    for key, (rel, size, mtime) in src_map.items():
        d = dst_map.get(key)
        if d is None:
            act = '新增'
        elif d[1] != size or abs(d[2] - mtime) > MTIME_TOLERANCE:
            act = '更新'
        else:
            continue
        diff.append({'action': act, 'rel': rel, 'kb': round(size / 1024), 'mtime': mtime})
    for key, (rel, size, mtime) in dst_map.items():
        if key not in src_map:
            diff.append({'action': '刪除', 'rel': rel, 'kb': round(size / 1024), 'mtime': mtime})
    order = {'新增': 0, '更新': 1, '刪除': 2}
    diff.sort(key=lambda x: (order[x['action']], x['rel'].lower()))
    return diff


# ──────────────────────────── 安全檢查 ────────────────────────────

def find_protected_inside(dest, max_depth=4):
    """目標底下若含業主雲端資料夾，回傳其路徑（鏡像會把它刪掉，必須擋下）。"""
    if not os.path.isdir(dest):
        return None
    protected = {n.lower() for n in PROTECTED_DIR_NAMES}
    base_depth = os.path.abspath(dest).rstrip('\\/').count(os.sep)
    for cur, dirs, _files in os.walk(dest):
        if cur.rstrip('\\/').count(os.sep) - base_depth >= max_depth:
            dirs[:] = []
            continue
        for d in dirs:
            if d.lower() in protected:
                return os.path.join(cur, d)
    return None


def check_ready(source, dest, other_roots=()):
    """同步前安全檢查；不通過就丟 MirrorError（不動任何檔案）。"""
    if not source or not os.path.isdir(source):
        raise MirrorError(f'找不到來源資料夾：{source}')
    try:
        entries = [e for e in os.listdir(source) if e.lower() not in (DEBRIS_DIR, 'desktop.ini')]
    except OSError as e:
        raise MirrorError(f'無法讀取來源資料夾：{source}（{e}）')
    if not entries:
        raise MirrorError(f'來源資料夾是空的，為安全起見不同步（避免把目標鏡像成空的）：{source}')
    if not dest:
        raise MirrorError('未設定目標資料夾')
    d_abs = os.path.abspath(dest)
    if os.path.splitdrive(d_abs)[1].strip('\\/') == '' or d_abs.rstrip('\\/').count(os.sep) < 1:
        raise MirrorError(f'目標不可以是磁碟或分享的根目錄：{dest}')
    if paths_overlap(source, dest):
        raise MirrorError('來源與目標相同或互相包含，無法鏡像')
    for other in other_roots:
        if other and paths_overlap(dest, other):
            raise MirrorError(f'目標與其他同步資料夾重疊，鏡像會刪到它的檔案：{other}')
    parent = os.path.dirname(d_abs.rstrip('\\/'))
    if not os.path.isdir(parent):
        raise MirrorError(f'連不到目標位置：{parent}（請確認網路 / VPN / NAS 登入）')
    hit = find_protected_inside(dest)
    if hit:
        raise MirrorError(f'目標資料夾內含業主雲端資料夾，鏡像會刪除它，已停止：{hit}')


# ──────────────────────────── 互斥鎖 ────────────────────────────

class SyncLock:
    """與 PowerShell 版共用的系統互斥鎖。acquire() 失敗代表另一邊正在同步。"""

    def __init__(self, name=MUTEX_NAME):
        self.name = name
        self.handle = None
        self.owned = False

    def acquire(self):
        if os.name != 'nt':
            self.owned = True
            return True
        try:
            k32 = ctypes.windll.kernel32
            k32.CreateMutexW.restype = ctypes.c_void_p
            h = k32.CreateMutexW(None, False, self.name)
            if not h:
                self.owned = True          # 建不出鎖就不擋（不讓同步卡死）
                return True
            self.handle = h
            r = k32.WaitForSingleObject(ctypes.c_void_p(h), 0)
            self.owned = r in (0, 0x80)    # WAIT_OBJECT_0 / WAIT_ABANDONED
            return self.owned
        except Exception:
            self.owned = True
            return True

    def release(self):
        try:
            if self.handle:
                k32 = ctypes.windll.kernel32
                if self.owned:
                    k32.ReleaseMutex(ctypes.c_void_p(self.handle))
                k32.CloseHandle(ctypes.c_void_p(self.handle))
        except Exception:
            pass
        self.handle = None
        self.owned = False


# ──────────────────────────── robocopy ────────────────────────────

def robocopy_args(source, dest, exclude_rel, log_path):
    xd = [DEBRIS_DIR, *PROTECTED_DIR_NAMES]
    for rel in exclude_rel:
        xd.append(os.path.join(source, rel))
        xd.append(os.path.join(dest, rel))   # 目標端同名資料夾也不刪
    return ['robocopy', source, dest,
            '/MIR', '/COPY:DAT', '/DCOPY:T', '/FFT', '/MT:8',
            '/R:2', '/W:5', '/XJ',
            '/XD', *xd,
            '/XF', *EXCLUDE_FILES,
            '/NP', '/NDL', f'/UNILOG:{log_path}']


def run_robocopy(source, dest, exclude_rel, log_path):
    proc = subprocess.run(robocopy_args(source, dest, exclude_rel, log_path),
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          creationflags=_CREATE_NO_WINDOW)
    return proc.returncode


# ──────────────────────────── 主流程 ────────────────────────────

def mirror(pair, log_dir, log=print, other_roots=()):
    """執行一組鏡像。回傳 dict：
    diff（同步前比對的變動清單）、code（robocopy 結束碼）、ok、stuck（刪不掉的舊檔數）、
    failed（應複製卻沒成功的檔數）、junk_removed、detail_log。
    安全檢查未過丟 MirrorError。"""
    source = pair['source_path']
    dest = pair['download_path']
    exclude_rel = normalize_exclude_dirs(source, pair.get('exclude_dirs'))
    check_ready(source, dest, other_roots)

    os.makedirs(log_dir, exist_ok=True)
    log('  比對來源與目標…')
    src_map = scan_source(source, exclude_rel)
    hits = []
    dst_map, _ = scan_dest(dest, exclude_rel, protected_hits=hits)
    if hits:
        raise MirrorError(f'目標資料夾內含業主雲端資料夾，鏡像會刪除它，已停止：{hits[0]}')
    diff = compute_diff(src_map, dst_map)

    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    safe_name = ''.join(c if c not in '\\/:*?"<>|' else '_' for c in pair.get('name') or 'mirror')
    detail = os.path.join(log_dir, f'robocopy_{stamp}_{safe_name}.log')
    # 沒有檔案差異時仍要跑：空資料夾的增刪也靠 robocopy 處理
    code = run_robocopy(source, dest, exclude_rel, detail)

    # 事後複查：比日誌字串可靠（robocopy 訊息會隨系統語系翻譯）
    post_map, junk = scan_dest(dest, exclude_rel)
    stuck = sum(1 for k in post_map if k not in src_map)
    failed = sum(1 for k, (_r, size, mt) in src_map.items()
                 if k not in post_map or post_map[k][1] != size
                 or abs(post_map[k][2] - mt) > MTIME_TOLERANCE)

    junk_removed = 0
    for path in junk:
        try:
            os.remove(path)
            junk_removed += 1
        except OSError:
            pass

    return {'diff': diff, 'code': code, 'ok': code < 8 and failed == 0,
            'stuck': stuck, 'failed': failed, 'junk_removed': junk_removed,
            'detail_log': detail, 'src_count': len(src_map)}


# ──────────────────────────── 紀錄 / 通知 ────────────────────────────

CSV_HEADER = ['同步時間', '資料夾', '動作', '檔名', '路徑', 'KB', '檔案修改時間']


def append_csv(log_dir, pair_name, diff, when=None):
    """與 PowerShell 版相同欄位的同步紀錄（Excel 可直接開）。被開著時另存待合併檔。"""
    if not diff:
        return None
    when = when or datetime.now()
    rows = [[when.strftime('%Y-%m-%d %H:%M'), pair_name, d['action'],
             os.path.basename(d['rel']), d['rel'], d['kb'],
             datetime.fromtimestamp(d['mtime']).strftime('%Y-%m-%d %H:%M')] for d in diff]
    path = os.path.join(log_dir, '同步紀錄.csv')
    for target in (path, os.path.join(log_dir, f"同步紀錄_待合併_{when.strftime('%Y%m%d_%H%M%S')}.csv")):
        try:
            new = not os.path.exists(target)
            with open(target, 'a', encoding='utf-8-sig' if new else 'utf-8', newline='') as f:
                w = csv.writer(f)
                if new:
                    w.writerow(CSV_HEADER)
                w.writerows(rows)
            return target
        except OSError:
            continue
    return None


def record_path(log_dir, pair_name):
    """某組的更新清單檔路徑（通知點開的就是它）。"""
    safe = ''.join(c if c not in '\\/:*?"<>|' else '_' for c in str(pair_name))
    return os.path.join(log_dir, f'{safe}_更新紀錄.txt')


def open_updates(uri, log_dir):
    """通知點擊的處理：只開 log_dir 內、指定組別的 _更新紀錄.txt（不接受任意路徑）。"""
    from urllib.parse import urlsplit, parse_qs
    try:
        name = parse_qs(urlsplit(uri).query).get('name', [''])[0]
    except ValueError:
        name = ''
    path = record_path(log_dir, name) if name else ''
    log_root = os.path.normcase(os.path.realpath(log_dir))
    if (path and os.path.isfile(path)
            and os.path.normcase(os.path.dirname(os.path.realpath(path))) == log_root):
        subprocess.Popen(['notepad.exe', path], close_fds=True)
    elif os.path.isdir(log_dir):
        os.startfile(log_dir)  # noqa: P204  找不到該組清單時至少打開紀錄資料夾


def write_notify_record(log_dir, pair_name, changed_rels, when=None):
    """更新清單文字檔，最新的放最上面；回傳路徑。"""
    when = when or datetime.now()
    path = record_path(log_dir, pair_name)
    block = [f"==== {when.strftime('%Y-%m-%d %H:%M')}  新增/更新 {len(changed_rels)} 個檔 ===="]
    block += [f'  {r}' for r in changed_rels] + ['']
    old = ''
    if os.path.exists(path):
        try:
            with open(path, encoding='utf-8-sig') as f:
                old = f.read()
        except OSError:
            old = ''
    with open(path, 'w', encoding='utf-8-sig') as f:
        f.write('\n'.join(block) + '\n' + old)
    return path


def show_toast(title, lines, launch_uri):
    """Windows 右下角通知，點擊時啟動 launch_uri。用 PowerShell 的 WinRT API，免額外套件。

    注意：launch_uri 不可用 file:///（Windows 不允許從通知開啟 file: 連結，點了會沒反應），
    要用已註冊的自訂協定（見 drive_sync_gui.register_open_protocol）。"""
    if os.name != 'nt':
        return
    import base64
    enc = base64.b64encode(toast_script(title, lines, launch_uri).encode('utf-16-le')).decode('ascii')
    try:
        subprocess.Popen(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                          '-WindowStyle', 'Hidden', '-EncodedCommand', enc],
                         creationflags=_CREATE_NO_WINDOW, close_fds=True)
    except OSError:
        pass


def toast_script(title, lines, launch_uri):
    def q(s):
        return str(s).replace("'", "''")

    body = ''.join(f"<text>$([Security.SecurityElement]::Escape('{q(t)}'))</text>"
                   for t in [l for l in lines if l][:2])
    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null;"
        f"$uri=[Security.SecurityElement]::Escape('{q(launch_uri)}');"
        f"$t=[Security.SecurityElement]::Escape('{q(title)}');"
        "$xml=\"<toast activationType='protocol' launch='$uri' duration='long'><visual><binding template='ToastGeneric'><text>$t</text>"
        f"{body}"
        "</binding></visual><actions><action content='開啟更新紀錄' activationType='protocol' arguments='$uri'/></actions></toast>\";"
        "$d=New-Object Windows.Data.Xml.Dom.XmlDocument;$d.LoadXml($xml);"
        "$id='{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id).Show((New-Object Windows.UI.Notifications.ToastNotification $d))"
    )
    return ps


def cleanup_old_logs(log_dir, days=KEEP_LOG_DAYS):
    if not os.path.isdir(log_dir):
        return
    cutoff = (datetime.now() - timedelta(days=days)).timestamp()
    for fn in os.listdir(log_dir):
        if fn.startswith('robocopy_') and fn.endswith('.log'):
            p = os.path.join(log_dir, fn)
            try:
                if os.path.getmtime(p) < cutoff:
                    os.remove(p)
            except OSError:
                pass
