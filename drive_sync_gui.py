"""
雲端圖資同步工具 - GUI 版本
============================
莫蘭迪色系 · 一鍵同步 · 今日更新摘要 · 複製到 LINE
"""

import os
import sys
import io
import re
import json
import shutil
import base64
import hashlib
import tempfile
import subprocess

import queue
import threading
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import customtkinter as ctk

# ==================== 版本 / 自動更新來源 ====================

APP_VERSION = '1.0.2'
GITHUB_OWNER = 'MASTERYUWEI'
GITHUB_REPO = 'cloud-drawing-sync'
UPDATE_API_URL = f'https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest'

# ==================== 程式目錄 ====================

def get_app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def get_bundled_path():
    """PyInstaller 打包時，內嵌資源的解壓路徑"""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))

def get_data_dir():
    """使用者資料目錄（%APPDATA%\\雲端圖資同步工具）。

    設定/token/狀態檔一律存這裡，而非 exe 旁：安裝到 Program Files 或
    LocalAppData\\Programs 後，exe 目錄不宜寫入，且升級覆蓋不會動到使用者資料。
    """
    base = os.environ.get('APPDATA') or os.path.expanduser('~')
    d = os.path.join(base, '雲端圖資同步工具')
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        d = get_app_dir()  # 極端情況退回舊行為
    return d

APP_DIR = get_app_dir()
BUNDLE_DIR = get_bundled_path()
DATA_DIR = get_data_dir()
CONFIG_FILE = os.path.join(DATA_DIR, 'config.json')
STATE_FILE = os.path.join(DATA_DIR, 'sync_state.json')
HISTORY_FILE = os.path.join(DATA_DIR, 'sync_history.json')

_LEGACY_DATA_FILES = ('config.json', 'sync_state.json', 'sync_history.json',
                      'xref_flat_manifest.json', 'token.json')

def migrate_legacy_data_files():
    """把舊版存在 exe 旁的資料檔搬到 DATA_DIR（一次性，已存在者不覆蓋）。"""
    if os.path.normcase(APP_DIR) == os.path.normcase(DATA_DIR):
        return
    for fn in _LEGACY_DATA_FILES:
        old = os.path.join(APP_DIR, fn)
        new = os.path.join(DATA_DIR, fn)
        if os.path.isfile(old) and not os.path.exists(new):
            try:
                shutil.move(old, new)
            except OSError:
                try:
                    shutil.copy2(old, new)
                except OSError:
                    pass

migrate_legacy_data_files()

# ==================== 莫蘭迪色彩系統 ====================

class Colors:
    # 參考 Steep 設計系統：明亮、暖色、低彩度點綴
    PRIMARY       = "#17191C"   # Ink — 主要按鈕
    PRIMARY_HOVER = "#33363C"
    PRIMARY_LIGHT = "#D3E3FC"   # Sky wash
    ACCENT        = "#3B6DB4"   # 日誌「新增」

    SUCCESS       = "#3F7D5A"
    SUCCESS_HOVER = "#346A4C"
    WARNING       = "#B06E2A"
    DANGER        = "#B4453A"

    RUST          = "#5D2A1A"   # 強調色
    APRICOT       = "#FBE1D1"   # 杏桃底色
    APRICOT_HOVER = "#F6D3BD"

    BG            = "#F7F7F8"   # Fog
    SURFACE       = "#FFFFFF"
    SURFACE_ALT   = "#ECEAE6"
    HEADER_BG     = "#FBE1D1"   # 標題列 = 杏桃暖底

    TEXT          = "#17191C"
    TEXT_SEC      = "#777B86"   # Graphite
    TEXT_MUTED    = "#A3A6AF"   # Dove
    TEXT_WHITE    = "#FFFFFF"

    LOG_DARK_BG   = "#F7F7F8"   # 日誌改為淺色底
    LOG_DARK_FG   = "#4C4C4C"

    TREE_BG       = "#FFFFFF"
    TREE_FG       = "#17191C"
    TREE_ALT_BG   = "#FAF9F7"
    TREE_SEL_BG   = "#FBE1D1"
    TREE_SEL_FG   = "#5D2A1A"
    TREE_HEAD_BG  = "#F1EFEC"
    TREE_HEAD_FG  = "#4C4C4C"

    TAB_ACTIVE_BG     = "#FBE1D1"
    TAB_ACTIVE_TEXT   = "#5D2A1A"
    TAB_INACTIVE_BG   = "#F1EFEC"
    TAB_INACTIVE_TEXT = "#777B86"
    TAB_HOVER_BG      = "#ECE9E4"

# ==================== 設定管理 ====================

DEFAULT_CONFIG = {
    'folder_id': '',
    'download_path': '',
    # 整合圖(套圖)的 XREF 多存成 ".\檔名.dwg"（找同一層），但子圖被分類在
    # 01建築圖、05機電設備… 等子資料夾裡，導致打開整合圖時外部參考全部遺失。
    # 開啟本選項後，每次同步會把子資料夾裡的子圖「複製一份」到整合圖那層，
    # 讓 ".\檔名.dwg" 直接對得上。只複製、不更動任何原始圖檔。
    'flatten_xrefs': True,
    # 雲端已刪/改名後本機殘留的孤兒檔，是否自動移到「回收區」（可還原，非刪除）。
    'auto_recycle_orphans': True,
    # 啟動時自動到 GitHub Releases 檢查新版本
    'auto_update_check': True,
}

XREF_FLAT_FILE = os.path.join(DATA_DIR, 'xref_flat_manifest.json')
# 主圖(整合圖)判斷：檔名以 ☆ 開頭且含「整合圖」
MASTER_PREFIX = '☆'
MASTER_KEYWORD = '整合圖'
# 這些不是被參考的底圖，不需要攤平複製（備份/還原/複製副本）
XREF_SKIP_TOKENS = ('_recover', '複製', '副本')
# 回收區資料夾名稱：孤兒檔會被移到 <原資料夾>/回收區/<日期>/ 底下
RECYCLE_DIR = '回收區'

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
        except (json.JSONDecodeError, OSError):
            pass  # 設定檔損壞時以預設值啟動，不讓程式開不起來
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass  # 狀態檔損壞時視同首次同步（重新比對下載，不會遺失雲端資料）
    return {'files': {}}

def save_state(state):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

MAX_HISTORY_SESSIONS = 400

def load_history():
    """同步歷史：sessions = 每次同步/重試的紀錄，pending = 尚未成功覆蓋的檔案"""
    history = {'sessions': [], 'pending': []}
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                if isinstance(data.get('sessions'), list):
                    history['sessions'] = data['sessions']
                if isinstance(data.get('pending'), list):
                    history['pending'] = data['pending']
        except (json.JSONDecodeError, OSError):
            pass
    return history

def save_history(history):
    history['sessions'] = history.get('sessions', [])[-MAX_HISTORY_SESSIONS:]
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

# ==================== Google Drive API ====================

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
    HAS_API = True
except ImportError:
    HAS_API = False

SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

GOOGLE_EXPORT_TYPES = {
    'application/vnd.google-apps.document': ('application/pdf', '.pdf'),
    'application/vnd.google-apps.spreadsheet': (
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '.xlsx'),
    'application/vnd.google-apps.presentation': ('application/pdf', '.pdf'),
    'application/vnd.google-apps.drawing': ('image/png', '.png'),
}

def get_credentials():
    creds = None
    token_path = os.path.join(DATA_DIR, 'token.json')
    # 先找外部 credentials.json，找不到就用內嵌的
    cred_path = os.path.join(APP_DIR, 'credentials.json')
    if not os.path.exists(cred_path):
        cred_path = os.path.join(BUNDLE_DIR, 'credentials.json')
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(cred_path):
                raise FileNotFoundError("找不到 credentials.json")
            flow = InstalledAppFlow.from_client_secrets_file(cred_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, 'w') as f:
            f.write(creds.to_json())
    return creds

# ==================== 自動更新（GitHub Releases） ====================

def parse_version(v):
    """'v1.2.10' → (1, 2, 10)；解析失敗回 (0,)。"""
    nums = []
    for part in re.split(r'[.\-+_]', str(v).strip().lstrip('vV')):
        if part.isdigit():
            nums.append(int(part))
        else:
            break
    return tuple(nums) if nums else (0,)


def fetch_latest_release(timeout=10):
    """查 GitHub 最新 Release。回傳 {'version', 'msi_url', 'msi_name', 'notes'}；無 MSI 資產時 msi_url=None。"""
    import urllib.request
    req = urllib.request.Request(UPDATE_API_URL, headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': f'{GITHUB_REPO}/{APP_VERSION}',
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    msi = next((a for a in data.get('assets', [])
                if str(a.get('name', '')).lower().endswith('.msi')), None)
    return {
        'version': str(data.get('tag_name') or '').lstrip('vV'),
        'msi_url': msi.get('browser_download_url') if msi else None,
        'msi_name': msi.get('name') if msi else None,
        'msi_size': msi.get('size') if msi else None,
        'msi_digest': msi.get('digest') if msi else None,  # 'sha256:...'，GitHub 較新資產才有
        'notes': (data.get('body') or '').strip(),
    }


def download_update_msi(url, dest_dir, log=print, expected_size=None, expected_digest=None):
    """下載新版 MSI 到 dest_dir 並驗證完整性，回傳完整路徑。

    - urllib 在連線中斷時 read() 只會回空 bytes、不丟例外，
      所以必須自行核對 Content-Length / asset size，避免把半截檔當成功。
    - GitHub asset 若附 sha256 digest 則一併驗證。
    """
    import urllib.request
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, 'update.msi')
    part = dest + '.part'
    req = urllib.request.Request(url, headers={'User-Agent': f'{GITHUB_REPO}/{APP_VERSION}'})
    sha = hashlib.sha256()
    got = 0
    with urllib.request.urlopen(req, timeout=60) as resp, open(part, 'wb') as f:
        total = int(resp.headers.get('Content-Length') or 0) or (expected_size or 0)
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            sha.update(chunk)
            got += len(chunk)
            if total:
                log(f'  下載更新 {got * 100 // total}%')
    try:
        if total and got != total:
            raise IOError(f'下載不完整（{got}/{total} bytes），可能是連線中斷')
        if expected_digest and str(expected_digest).startswith('sha256:'):
            if sha.hexdigest().lower() != expected_digest.split(':', 1)[1].lower():
                raise IOError('下載檔案 SHA-256 校驗不符，已放棄安裝')
    except Exception:
        try:
            os.remove(part)
        except OSError:
            pass
        raise
    os.replace(part, dest)
    return dest


def launch_msi_upgrade(msi_path):
    """啟動靜默升級並在完成後重啟本程式（僅打包版有效）。呼叫端應隨即結束程式。

    以 PowerShell -EncodedCommand（base64/UTF-16LE）執行，完全不受系統 ANSI
    語系影響（cmd 批次檔在非中文 Windows 寫不進中文路徑）。流程：
    1. 輪詢等待本程式的 exe 檔案解鎖（PyInstaller onefile 的 bootloader
       父行程清理暫存可能比視窗關閉晚數秒），最多等 60 秒
    2. msiexec /passive 升級並寫詳細記錄檔，檢查結束碼（0/3010 才算成功）
    3. 成功則刪除下載的 MSI，最後重啟程式（失敗時舊版仍在，重啟舊版）
    """
    exe = sys.executable

    def q(s):  # PowerShell 單引號字串跳脫
        return s.replace("'", "''")

    ps = (
        "$ErrorActionPreference='Continue';"
        f"$exe='{q(exe)}';$msi='{q(msi_path)}';"
        "for($i=0;$i -lt 120;$i++){"
        "try{$fs=[IO.File]::Open($exe,'Open','ReadWrite','None');$fs.Close();break}"
        "catch{Start-Sleep -Milliseconds 500}};"
        "$p=Start-Process msiexec -ArgumentList"
        " ('/i',('\"'+$msi+'\"'),'/passive','/norestart','/l*v',('\"'+$msi+'.log\"'))"
        " -Wait -PassThru;"
        "if($p.ExitCode -eq 0 -or $p.ExitCode -eq 3010){"
        "Remove-Item -LiteralPath $msi -Force -ErrorAction SilentlyContinue};"
        "Start-Process -FilePath $exe"
    )
    enc = base64.b64encode(ps.encode('utf-16-le')).decode('ascii')
    subprocess.Popen(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                      '-WindowStyle', 'Hidden', '-EncodedCommand', enc],
                     creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP |
                                    getattr(subprocess, 'CREATE_NO_WINDOW', 0)),
                     close_fds=True)


def list_files_recursive(service, folder_id, folder_path="", log=print):
    all_files = []
    page_token = None
    while True:
        try:
            q = f"'{folder_id}' in parents and trashed = false"
            resp = service.files().list(
                q=q, spaces='drive',
                fields='nextPageToken, files(id, name, mimeType, modifiedTime, size)',
                pageToken=page_token
            ).execute()
            for item in resp.get('files', []):
                if item['mimeType'] == 'application/vnd.google-apps.folder':
                    sub = os.path.join(folder_path, item['name'])
                    log(f"  掃描子資料夾: {sub}")
                    all_files.extend(list_files_recursive(service, item['id'], sub, log))
                else:
                    item['folderPath'] = folder_path
                    all_files.append(item)
            page_token = resp.get('nextPageToken')
            if not page_token:
                break
        except Exception as e:
            log(f"API 錯誤: {e}")
            break
    return all_files

def download_file(service, file_id, file_name, mime_type, folder_path, download_path, log=print):
    local_folder = os.path.join(download_path, folder_path)
    os.makedirs(local_folder, exist_ok=True)
    local_path = os.path.join(local_folder, file_name)

    if mime_type in GOOGLE_EXPORT_TYPES:
        export_mime, ext = GOOGLE_EXPORT_TYPES[mime_type]
        request = service.files().export_media(fileId=file_id, mimeType=export_mime)
        if not os.path.splitext(file_name)[1]:
            local_path += ext
    elif mime_type == 'application/vnd.google-apps.folder':
        return None
    else:
        request = service.files().get_media(fileId=file_id)

    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
        if status:
            log(f"  下載中 {int(status.progress() * 100)}%")
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile('wb', delete=False, dir=local_folder, prefix='.download-', suffix='.tmp') as f:
            temp_path = f.name
            fh.seek(0)
            f.write(fh.read())
        os.replace(temp_path, local_path)
    except Exception:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise
    return local_path

# ==================== 整合圖 XREF 攤平（複製子圖到整合圖那層） ====================

def _load_xref_manifest():
    """讀取「本工具產生的複製檔」清單（相對 download_path 的路徑，正斜線）。"""
    if os.path.exists(XREF_FLAT_FILE):
        try:
            with open(XREF_FLAT_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get('copies'), list):
                return set(data['copies'])
        except (json.JSONDecodeError, OSError):
            pass
    return set()


def _save_xref_manifest(copies):
    try:
        with open(XREF_FLAT_FILE, 'w', encoding='utf-8') as f:
            json.dump({'copies': sorted(copies)}, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _find_master_folders(dl_path):
    """找出所有直接含有 ☆整合圖 的資料夾，只保留最上層者（避免巢狀重複攤平）。"""
    targets = []
    for cur, dirs, filenames in os.walk(dl_path):
        dirs[:] = [d for d in dirs if d != RECYCLE_DIR]  # 不掃回收區
        if any(fn.startswith(MASTER_PREFIX) and MASTER_KEYWORD in fn
               and fn.lower().endswith('.dwg') for fn in filenames):
            targets.append(cur)
    tops = []
    for t in targets:
        if not any(t != o and t.startswith(o + os.sep) for o in targets):
            tops.append(t)
    return tops


def flatten_xrefs(dl_path, log=print, enabled=True):
    """把各整合圖資料夾底下子圖複製一份到整合圖那層，讓 '.\\檔名.dwg' 解析得到。

    - 只複製、不修改任何原始 DWG；☆ 開頭的整合圖本身永不被複製。
    - 以 manifest 記錄本工具建立的複製檔，僅刷新/清理這些檔，不動使用者原有檔案。
    - copy2 保留時間戳，來源未變則跳過（冪等，不重抄）。
    - enabled=False 時只執行清理（移除先前建立的複製檔）。
    回傳 dict: copied / removed / skipped / conflicts。
    """
    old = _load_xref_manifest()
    managed = set()          # 本次仍應存在的複製檔（相對路徑）
    copied = removed = skipped = 0
    conflicts = []

    if enabled and os.path.isdir(dl_path):
        for tf in _find_master_folders(dl_path):
            # 先收集每個檔名在各子資料夾出現的所有位置
            sources = {}     # 檔名 -> [來源完整路徑, ...]
            for cur, dirs, filenames in os.walk(tf):
                dirs[:] = sorted(d for d in dirs if d != RECYCLE_DIR)  # 排序且不掃回收區
                if os.path.normcase(cur) == os.path.normcase(tf):
                    continue  # 略過整合圖那層本身
                for fn in sorted(filenames):
                    if not fn.lower().endswith('.dwg'):
                        continue
                    if fn.startswith(MASTER_PREFIX):
                        continue  # 不散播整合圖主檔
                    if any(tok in fn for tok in XREF_SKIP_TOKENS):
                        continue
                    sources.setdefault(fn, []).append(os.path.join(cur, fn))

            for fn, srcs in sources.items():
                if len(srcs) > 1:
                    # 同名衝突：多個子資料夾有同名檔，程式無法判斷該用哪個。
                    # 不自動挑選、不複製，改為回報讓使用者自行清除重複或改名。
                    conflicts.append({
                        'name': fn,
                        'folder': os.path.relpath(tf, dl_path),
                        'sources': [os.path.relpath(s, dl_path) for s in srcs],
                    })
                    continue
                src = srcs[0]
                dest = os.path.join(tf, fn)
                rel = os.path.relpath(dest, dl_path).replace('\\', '/')
                if os.path.exists(dest) and rel not in old:
                    # 這層本來就有同名檔（整合圖主檔或使用者原有檔），不覆蓋
                    skipped += 1
                    continue
                try:
                    if _needs_copy(src, dest):
                        shutil.copy2(src, dest)
                        copied += 1
                        log(f'  套圖連結: 複製 {rel}')
                    managed.add(rel)
                except OSError as e:
                    log(f'  套圖連結複製失敗: {rel} | {e}')

    # 清理：先前建立、但這次不再需要的複製檔（來源被刪/改名，或功能關閉）
    for rel in (old - managed):
        p = os.path.join(dl_path, rel.replace('/', os.sep))
        if os.path.isfile(p) and p.lower().endswith('.dwg'):
            try:
                os.remove(p)
                removed += 1
                log(f'  套圖連結: 移除失效複製 {rel}')
            except OSError:
                managed.add(rel)  # 刪不掉就保留在清單，下次再試

    _save_xref_manifest(managed)
    return {'copied': copied, 'removed': removed,
            'skipped': skipped, 'conflicts': conflicts}


def _needs_copy(src, dest):
    """來源不存在時視同需要（交給呼叫端 try）；否則比對大小與修改時間。"""
    if not os.path.exists(dest):
        return True
    try:
        s, d = os.stat(src), os.stat(dest)
    except OSError:
        return True
    if s.st_size != d.st_size:
        return True
    return int(s.st_mtime) > int(d.st_mtime)


# ==================== 孤兒檔偵測（雲端已刪/改名，本機殘留） ====================

def find_orphans(state_files, cloud_ids, old_paths, dl_path):
    """找出『曾從雲端下載、但雲端已刪除或改名/搬移』而殘留在本機的檔案。

    - 刪除：fileId 已不在雲端清單，但本機仍有當初下載的檔。
    - 改名/搬移(本次同步)：同 fileId 仍在雲端，但下載位置改變，舊位置檔案殘留。
    只回報「確實存在於磁碟、且不是任何現存 fileId 目前對應檔案」的路徑；
    因此絕不會誤報使用者本機自建的檔或攤平複製檔（它們不在 state 內）。
    回傳 list[dict]: {name, path(絕對), rel}。
    """
    valid = set()
    for fid in cloud_ids:
        info = state_files.get(fid)
        if info and info.get('localPath'):
            valid.add(os.path.normcase(os.path.abspath(info['localPath'])))

    seen = set()
    orphans = []

    def add(path):
        if not path:
            return
        ap = os.path.abspath(path)
        key = os.path.normcase(ap)
        if key in valid or key in seen or not os.path.isfile(ap):
            return
        seen.add(key)
        orphans.append({'name': os.path.basename(ap), 'path': ap,
                        'rel': os.path.relpath(ap, dl_path)})

    # 刪除：state 內、但雲端已無的 fileId
    for fid, info in state_files.items():
        if fid not in cloud_ids:
            add(info.get('localPath'))
    # 改名/搬移(本次)：同 fileId 下載位置改變，舊位置殘留
    for fid in cloud_ids:
        old = old_paths.get(fid)
        new = (state_files.get(fid) or {}).get('localPath')
        if old and new and os.path.normcase(os.path.abspath(old)) != os.path.normcase(os.path.abspath(new)):
            add(old)
    return orphans


def recycle_orphans(orphans, date_str):
    """把孤兒檔移到其所在資料夾的 回收區/<date_str>/ 底下（移動、非刪除，可還原）。

    回傳 (moved, failed)：
      moved  = list[dict] {name, rel(原相對, 由呼叫端補), src, dest, dest_dir}
      failed = list[dict] {name, src, error}
    """
    moved, failed = [], []
    for o in orphans:
        src = o['path']
        if not os.path.isfile(src):
            continue
        parent = os.path.dirname(src)
        dest_dir = os.path.join(parent, RECYCLE_DIR, date_str)
        dest = os.path.join(dest_dir, o['name'])
        base, ext = os.path.splitext(o['name'])
        n = 1
        try:
            os.makedirs(dest_dir, exist_ok=True)
            while os.path.exists(dest):          # 同名不覆蓋
                dest = os.path.join(dest_dir, f"{base}_{n}{ext}")
                n += 1
            shutil.move(src, dest)
            moved.append({'name': o['name'], 'rel': o.get('rel', ''),
                          'src': src, 'dest': dest, 'dest_dir': dest_dir})
        except OSError as e:
            failed.append({'name': o['name'], 'src': src, 'error': str(e)})
    return moved, failed


# ==================== GUI 應用程式 ====================

class DriveSyncApp(ctk.CTk):

    FONT_FAMILY = "Microsoft JhengHei UI"
    MONO_FONT   = "Consolas"
    TAB_NAMES   = ["同步日誌", "檔案清單", "今日更新", "未同步檔案", "更新紀錄"]

    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")

        self.title(f"雲端圖資同步工具 v{APP_VERSION}")
        # 視窗/工作列圖示（default= 讓後續 Toplevel 一併套用）；
        # 打包版在 BUNDLE_DIR 根、開發模式在 installer/ 下
        for ico in (os.path.join(BUNDLE_DIR, 'app.ico'),
                    os.path.join(APP_DIR, 'installer', 'app.ico')):
            if os.path.exists(ico):
                try:
                    self.iconbitmap(default=ico)
                except Exception:
                    pass
                break
        self.geometry("1120x820")
        self.minsize(920, 650)
        self.configure(fg_color=Colors.BG)

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # State
        self.msg_queue = queue.Queue()
        self.is_syncing = False
        self.service = None
        self.config = load_config()
        self._all_files = []
        self._today_new = []
        self._today_updated = []
        self._today_last_ts = None
        self._tab_buttons = {}
        self._tab_frames = {}
        self._current_tab = None

        # Variables
        self.v_fid = ctk.StringVar(value=self.config.get('folder_id', ''))
        self.v_path = ctk.StringVar(value=self.config.get('download_path', ''))
        self.v_status = ctk.StringVar(value='尚未連線')
        self.v_last = ctk.StringVar(value='--')
        self.v_count = ctk.StringVar(value='0 個')
        self.v_search = ctk.StringVar()

        self._build_ui()
        self._refresh_files()
        self._restore_from_history()
        self._reload_history_views()
        self._poll()
        self.after(1000, self._auto_connect)
        if self.config.get('auto_update_check', True):
            self.after(4000, self._auto_check_update)
        self.protocol('WM_DELETE_WINDOW', self._on_close)

    # ══════════════════════════════════════════════
    #  Build UI
    # ══════════════════════════════════════════════

    def _build_ui(self):
        self._build_header()
        self._build_body()

    def _build_header(self):
        hdr = ctk.CTkFrame(self, corner_radius=0, fg_color=Colors.HEADER_BG, height=80)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_propagate(False)
        hdr.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            hdr, text="雲端圖資同步工具",
            font=ctk.CTkFont(family=self.FONT_FAMILY, size=21, weight="bold"),
            text_color=Colors.RUST,
        ).grid(row=0, column=0, padx=30, pady=(20, 2), sticky="w")

        ctk.CTkLabel(
            hdr, text=f"一鍵同步 Google Drive 圖資檔案　·　v{APP_VERSION}",
            font=ctk.CTkFont(family=self.FONT_FAMILY, size=12),
            text_color="#9A6A52",
        ).grid(row=1, column=0, padx=30, pady=(0, 18), sticky="w")

        right = ctk.CTkFrame(hdr, fg_color="transparent")
        right.grid(row=0, column=2, rowspan=2, sticky="e", padx=(0, 30))

        self.btn_oauth = ctk.CTkButton(
            right, text="登入 Google",
            font=ctk.CTkFont(family=self.FONT_FAMILY, size=12, weight="bold"),
            fg_color="#FFFFFF", hover_color="#FDF2E9",
            text_color=Colors.TEXT,
            border_width=1, border_color="#E9CDB9",
            height=34, width=140, corner_radius=17,
            command=self._manual_oauth,
        )
        self.btn_oauth.pack(side="left", padx=(0, 16))

        self._status_dot = ctk.CTkLabel(right, text="●", font=ctk.CTkFont(size=14),
                                         text_color=Colors.TEXT_MUTED)
        self._status_dot.pack(side="left", padx=(0, 6))

        ctk.CTkLabel(right, textvariable=self.v_status,
                     font=ctk.CTkFont(family=self.FONT_FAMILY, size=12),
                     text_color="#4C4C4C").pack(side="left")

    def _build_body(self):
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=24, pady=(20, 24))
        body.grid_rowconfigure(1, weight=1)
        body.grid_columnconfigure(0, weight=1)

        # Top: 設定 + 狀態
        top = ctk.CTkFrame(body, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", pady=(0, 18))
        top.grid_columnconfigure(0, weight=3)
        top.grid_columnconfigure(1, weight=2)
        self._build_settings(top)
        self._build_status(top)

        # Bottom: 操作區
        bottom = ctk.CTkFrame(body, corner_radius=14, fg_color=Colors.SURFACE,
                              border_width=1, border_color=Colors.SURFACE_ALT)
        bottom.grid(row=1, column=0, sticky="nsew")
        bottom.grid_rowconfigure(2, weight=1)
        bottom.grid_columnconfigure(0, weight=1)

        self._build_controls(bottom)
        self._build_custom_tabs(bottom)

    # ── 設定卡片 ──

    def _build_settings(self, parent):
        card = ctk.CTkFrame(parent, corner_radius=14, fg_color=Colors.SURFACE,
                            border_width=1, border_color=Colors.SURFACE_ALT)
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(card, text="同步設定",
                     font=ctk.CTkFont(family=self.FONT_FAMILY, size=15, weight="bold"),
                     text_color=Colors.TEXT).grid(row=0, column=0, columnspan=3, sticky="w", padx=25, pady=(20, 6))
        ctk.CTkFrame(card, height=1, fg_color=Colors.SURFACE_ALT).grid(
            row=1, column=0, columnspan=3, sticky="ew", padx=25, pady=(0, 14))

        # 資料夾 ID / 連結
        self._label(card, "資料夾 ID 或連結", 2)
        self.entry_fid = ctk.CTkEntry(card, textvariable=self.v_fid, height=36, corner_radius=16,
                                       font=ctk.CTkFont(family=self.FONT_FAMILY, size=12),
                                       placeholder_text="貼上 Google Drive 資料夾連結或 ID",
                                       border_color=Colors.SURFACE_ALT, fg_color=Colors.BG)
        self.entry_fid.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(0, 25), pady=6)

        # 下載路徑
        self._label(card, "下載路徑", 3)
        self.entry_path = ctk.CTkEntry(card, textvariable=self.v_path, height=36, corner_radius=16,
                                        font=ctk.CTkFont(family=self.FONT_FAMILY, size=12),
                                        border_color=Colors.SURFACE_ALT, fg_color=Colors.BG)
        self.entry_path.grid(row=3, column=1, sticky="ew", padx=(0, 8), pady=6)
        ctk.CTkButton(card, text="瀏覽", width=64, height=36, corner_radius=17,
                      font=ctk.CTkFont(family=self.FONT_FAMILY, size=12),
                      fg_color="transparent", border_width=1, border_color=Colors.SURFACE_ALT,
                      text_color=Colors.TEXT_SEC, hover_color=Colors.BG,
                      command=self._browse).grid(row=3, column=2, padx=(0, 25), pady=6)

        # 儲存
        ctk.CTkButton(card, text="儲存設定", height=36, corner_radius=17,
                      font=ctk.CTkFont(family=self.FONT_FAMILY, size=13, weight="bold"),
                      fg_color=Colors.PRIMARY, hover_color=Colors.PRIMARY_HOVER,
                      command=self._save_settings).grid(row=4, column=1, columnspan=2,
                                                         sticky="e", padx=(0, 25), pady=(6, 20))

    def _label(self, parent, text, row):
        ctk.CTkLabel(parent, text=text, font=ctk.CTkFont(family=self.FONT_FAMILY, size=13),
                     text_color=Colors.TEXT_SEC).grid(row=row, column=0, sticky="w", padx=25, pady=6)

    # ── 狀態卡片 ──

    def _build_status(self, parent):
        card = ctk.CTkFrame(parent, corner_radius=14, fg_color=Colors.SURFACE,
                            border_width=1, border_color=Colors.SURFACE_ALT)
        card.grid(row=0, column=1, sticky="nsew", padx=(12, 0))

        ctk.CTkLabel(card, text="同步狀態",
                     font=ctk.CTkFont(family=self.FONT_FAMILY, size=15, weight="bold"),
                     text_color=Colors.TEXT).grid(row=0, column=0, columnspan=2, sticky="w", padx=25, pady=(20, 6))
        ctk.CTkFrame(card, height=1, fg_color=Colors.SURFACE_ALT).grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=25, pady=(0, 14))

        for idx, (label, var) in enumerate([("上次同步", self.v_last), ("追蹤檔案", self.v_count)], start=2):
            ctk.CTkLabel(card, text=label, font=ctk.CTkFont(family=self.FONT_FAMILY, size=13),
                         text_color=Colors.TEXT_SEC).grid(row=idx, column=0, sticky="w", padx=25, pady=7)
            ctk.CTkLabel(card, textvariable=var,
                         font=ctk.CTkFont(family=self.FONT_FAMILY, size=15, weight="bold"),
                         text_color=Colors.TEXT).grid(row=idx, column=1, sticky="w", padx=(8, 25), pady=7)

    # ── 控制按鈕 (精簡版) ──

    def _build_controls(self, parent):
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 12))

        self.btn_sync = ctk.CTkButton(
            bar, text="立即同步", width=160, height=44, corner_radius=22,
            font=ctk.CTkFont(family=self.FONT_FAMILY, size=14, weight="bold"),
            fg_color=Colors.PRIMARY, hover_color=Colors.PRIMARY_HOVER,
            command=self._do_sync,
        )
        self.btn_sync.pack(side="left")

        ctk.CTkButton(
            bar, text="重新載入", width=120, height=38, corner_radius=19,
            font=ctk.CTkFont(family=self.FONT_FAMILY, size=12),
            fg_color="transparent", border_width=1, border_color=Colors.SURFACE_ALT,
            text_color=Colors.TEXT_SEC, hover_color=Colors.BG,
            command=self._refresh_files,
        ).pack(side="right")

    # ══════════════════════════════════════════════
    #  Custom Tab Switcher
    # ══════════════════════════════════════════════

    def _build_custom_tabs(self, parent):
        tab_bar = ctk.CTkFrame(parent, fg_color="transparent", height=44)
        tab_bar.grid(row=1, column=0, sticky="ew", padx=24, pady=(4, 0))

        for name in self.TAB_NAMES:
            btn = ctk.CTkButton(
                tab_bar, text=name, width=130, height=38, corner_radius=19,
                font=ctk.CTkFont(family=self.FONT_FAMILY, size=13, weight="bold"),
                fg_color=Colors.TAB_INACTIVE_BG, hover_color=Colors.TAB_HOVER_BG,
                text_color=Colors.TAB_INACTIVE_TEXT,
                command=lambda n=name: self._switch_tab(n),
            )
            btn.pack(side="left", padx=(0, 6))
            self._tab_buttons[name] = btn

        content = ctk.CTkFrame(parent, fg_color="transparent")
        content.grid(row=2, column=0, sticky="nsew", padx=20, pady=(8, 20))
        content.grid_rowconfigure(0, weight=1)
        content.grid_columnconfigure(0, weight=1)

        for name in self.TAB_NAMES:
            frame = ctk.CTkFrame(content, fg_color="transparent")
            frame.grid(row=0, column=0, sticky="nsew")
            self._tab_frames[name] = frame

        self._build_tab_log(self._tab_frames["同步日誌"])
        self._build_tab_files(self._tab_frames["檔案清單"])
        self._build_tab_today(self._tab_frames["今日更新"])
        self._build_tab_pending(self._tab_frames["未同步檔案"])
        self._build_tab_history(self._tab_frames["更新紀錄"])
        self._switch_tab("同步日誌")

    def _switch_tab(self, name):
        self._current_tab = name
        for n, btn in self._tab_buttons.items():
            if n == name:
                btn.configure(fg_color=Colors.TAB_ACTIVE_BG, text_color=Colors.TAB_ACTIVE_TEXT,
                              hover_color=Colors.APRICOT_HOVER)
            else:
                btn.configure(fg_color=Colors.TAB_INACTIVE_BG, text_color=Colors.TAB_INACTIVE_TEXT,
                              hover_color=Colors.TAB_HOVER_BG)
        for n, frame in self._tab_frames.items():
            if n == name:
                frame.tkraise()

    # ── Tab: 同步日誌 ──

    def _build_tab_log(self, tab):
        tab.grid_rowconfigure(0, weight=1)
        tab.grid_columnconfigure(0, weight=1)

        self.log_text = tk.Text(tab, font=(self.MONO_FONT, 10), wrap="word",
                                bg=Colors.LOG_DARK_BG, fg=Colors.LOG_DARK_FG,
                                insertbackground=Colors.TEXT, relief="flat",
                                padx=18, pady=18, state="disabled",
                                bd=0, highlightthickness=1,
                                highlightbackground=Colors.SURFACE_ALT,
                                highlightcolor=Colors.SURFACE_ALT)
        self.log_text.grid(row=0, column=0, sticky="nsew")

        scroll = ctk.CTkScrollbar(tab, command=self.log_text.yview,
                                   button_color=Colors.PRIMARY_LIGHT,
                                   button_hover_color=Colors.PRIMARY)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)

        self.log_text.tag_configure("time",    foreground=Colors.TEXT_MUTED)
        self.log_text.tag_configure("info",    foreground=Colors.LOG_DARK_FG)
        self.log_text.tag_configure("success", foreground=Colors.SUCCESS)
        self.log_text.tag_configure("warning", foreground=Colors.WARNING)
        self.log_text.tag_configure("error",   foreground=Colors.DANGER)
        self.log_text.tag_configure("new",     foreground=Colors.ACCENT)
        self.log_text.tag_configure("update",  foreground="#7B5FAE")

    # ── Tab: 檔案清單 ──

    def _build_tab_files(self, tab):
        tab.grid_rowconfigure(1, weight=1)
        tab.grid_columnconfigure(0, weight=1)

        bar = ctk.CTkFrame(tab, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ctk.CTkLabel(bar, text="搜尋:", font=ctk.CTkFont(family=self.FONT_FAMILY, size=13),
                     text_color=Colors.TEXT).pack(side="left", padx=(8, 10))
        self.v_search.trace_add('write', lambda *_: self._filter_files())
        ctk.CTkEntry(bar, textvariable=self.v_search, width=280, height=34, corner_radius=16,
                     font=ctk.CTkFont(family=self.FONT_FAMILY, size=12),
                     border_color=Colors.SURFACE_ALT, fg_color=Colors.BG,
                     text_color=Colors.TEXT).pack(side="left")
        self.lbl_fcount = ctk.CTkLabel(bar, text="", font=ctk.CTkFont(family=self.FONT_FAMILY, size=11),
                                        text_color=Colors.TEXT_SEC)
        self.lbl_fcount.pack(side="right", padx=12)

        # Treeview
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("HD.Treeview", font=(self.FONT_FAMILY, 10), rowheight=32,
                         background=Colors.TREE_BG, fieldbackground=Colors.TREE_BG,
                         foreground=Colors.TREE_FG, borderwidth=1, relief="solid")
        style.configure("HD.Treeview.Heading", font=(self.FONT_FAMILY, 10, "bold"),
                         background=Colors.TREE_HEAD_BG, foreground=Colors.TREE_HEAD_FG,
                         padding=6, relief="flat", borderwidth=0)
        style.map("HD.Treeview", background=[("selected", Colors.TREE_SEL_BG)],
                  foreground=[("selected", Colors.TREE_SEL_FG)])
        style.layout("HD.Treeview", [('HD.Treeview.treearea', {'sticky': 'nswe'})])

        tf = ctk.CTkFrame(tab, fg_color="transparent")
        tf.grid(row=1, column=0, sticky="nsew")
        tf.grid_rowconfigure(0, weight=1)
        tf.grid_columnconfigure(0, weight=1)

        cols = ('folder', 'name', 'modified', 'status')
        self.tree = ttk.Treeview(tf, columns=cols, show='headings', selectmode='browse', style="HD.Treeview")
        for cid, heading, w in [('folder', '資料夾', 250), ('name', '檔案名稱', 400),
                                 ('modified', '修改時間', 160), ('status', '狀態', 80)]:
            a = 'w' if cid != 'status' else 'center'
            self.tree.heading(cid, text=heading, anchor=a)
            self.tree.column(cid, width=w, minwidth=100, anchor=a)
        self.tree.tag_configure('oddrow',  background=Colors.TREE_ALT_BG)
        self.tree.tag_configure('evenrow', background=Colors.TREE_BG)

        ts = ctk.CTkScrollbar(tf, command=self.tree.yview, button_color=Colors.PRIMARY_LIGHT,
                               button_hover_color=Colors.PRIMARY)
        ts.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=ts.set)
        self.tree.grid(row=0, column=0, sticky="nsew")

    # ── Tab: 今日更新 ──

    def _build_tab_today(self, tab):
        tab.grid_rowconfigure(1, weight=1)
        tab.grid_columnconfigure(0, weight=1)

        toolbar = ctk.CTkFrame(tab, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        self.lbl_today_title = ctk.CTkLabel(toolbar, text="今日尚未同步",
                                             font=ctk.CTkFont(family=self.FONT_FAMILY, size=14, weight="bold"),
                                             text_color=Colors.TEXT)
        self.lbl_today_title.pack(side="left", padx=(8, 0))

        self.btn_copy = ctk.CTkButton(toolbar, text="複製到剪貼簿", height=34, width=160, corner_radius=17,
                                       font=ctk.CTkFont(family=self.FONT_FAMILY, size=12, weight="bold"),
                                       fg_color=Colors.PRIMARY, hover_color=Colors.PRIMARY_HOVER,
                                       command=self._copy_today)
        self.btn_copy.pack(side="right", padx=(0, 8))

        self.today_text = tk.Text(tab, font=(self.FONT_FAMILY, 12), wrap="word",
                                  bg="#FFFFFF", fg=Colors.TEXT, relief="flat", bd=0,
                                  padx=18, pady=18, state="disabled", highlightthickness=1,
                                  highlightbackground=Colors.SURFACE_ALT,
                                  highlightcolor=Colors.SURFACE_ALT,
                                  selectbackground=Colors.PRIMARY_LIGHT)
        self.today_text.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))
        self.today_text.configure(state="normal")
        self.today_text.insert("1.0", "點擊「立即同步」後，將在此顯示更新摘要。\n\n可複製到剪貼簿直接貼到 LINE 群組。")
        self.today_text.configure(state="disabled")

    # ── Tab: 未同步檔案（可重試） ──

    def _build_tab_pending(self, tab):
        tab.grid_rowconfigure(1, weight=1)
        tab.grid_columnconfigure(0, weight=1)

        toolbar = ctk.CTkFrame(tab, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        self.lbl_pending_title = ctk.CTkLabel(
            toolbar, text="目前沒有未同步的檔案",
            font=ctk.CTkFont(family=self.FONT_FAMILY, size=14, weight="bold"),
            text_color=Colors.TEXT)
        self.lbl_pending_title.pack(side="left", padx=(8, 0))

        self.btn_retry = ctk.CTkButton(
            toolbar, text="重試全部", height=34, width=150, corner_radius=17,
            font=ctk.CTkFont(family=self.FONT_FAMILY, size=12, weight="bold"),
            fg_color=Colors.SUCCESS, hover_color=Colors.SUCCESS_HOVER,
            command=self._do_retry, state="disabled")
        self.btn_retry.pack(side="right", padx=(0, 8))

        ctk.CTkButton(
            toolbar, text="移除選取", height=34, width=120, corner_radius=17,
            font=ctk.CTkFont(family=self.FONT_FAMILY, size=12),
            fg_color="transparent", border_width=1, border_color=Colors.SURFACE_ALT,
            text_color=Colors.TEXT_SEC, hover_color=Colors.BG,
            command=self._remove_selected_pending).pack(side="right", padx=(0, 8))

        tf = ctk.CTkFrame(tab, fg_color="transparent")
        tf.grid(row=1, column=0, sticky="nsew")
        tf.grid_rowconfigure(0, weight=1)
        tf.grid_columnconfigure(0, weight=1)

        cols = ('file', 'reason', 'time')
        self.pending_tree = ttk.Treeview(tf, columns=cols, show='headings',
                                          selectmode='extended', style="HD.Treeview")
        for cid, heading, w, anchor in [('file', '檔案', 420, 'w'),
                                         ('reason', '失敗原因', 400, 'w'),
                                         ('time', '失敗時間', 140, 'center')]:
            self.pending_tree.heading(cid, text=heading, anchor=anchor)
            self.pending_tree.column(cid, width=w, minwidth=100, anchor=anchor)
        self.pending_tree.tag_configure('oddrow',  background=Colors.TREE_ALT_BG)
        self.pending_tree.tag_configure('evenrow', background=Colors.TREE_BG)

        ps = ctk.CTkScrollbar(tf, command=self.pending_tree.yview,
                               button_color=Colors.PRIMARY_LIGHT,
                               button_hover_color=Colors.PRIMARY)
        ps.grid(row=0, column=1, sticky="ns")
        self.pending_tree.configure(yscrollcommand=ps.set)
        self.pending_tree.grid(row=0, column=0, sticky="nsew")

    # ── Tab: 更新紀錄（每日歷史） ──

    def _build_tab_history(self, tab):
        tab.grid_rowconfigure(1, weight=1)
        tab.grid_columnconfigure(0, weight=1)

        toolbar = ctk.CTkFrame(tab, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        self.lbl_hist_title = ctk.CTkLabel(
            toolbar, text="每日更新紀錄",
            font=ctk.CTkFont(family=self.FONT_FAMILY, size=14, weight="bold"),
            text_color=Colors.TEXT)
        self.lbl_hist_title.pack(side="left", padx=(8, 0))

        ctk.CTkButton(
            toolbar, text="重新整理", height=34, width=120, corner_radius=17,
            font=ctk.CTkFont(family=self.FONT_FAMILY, size=12),
            fg_color="transparent", border_width=1, border_color=Colors.SURFACE_ALT,
            text_color=Colors.TEXT_SEC, hover_color=Colors.BG,
            command=self._render_history).pack(side="right", padx=(0, 8))

        tf = ctk.CTkFrame(tab, fg_color="transparent")
        tf.grid(row=1, column=0, sticky="nsew")
        tf.grid_rowconfigure(0, weight=1)
        tf.grid_columnconfigure(0, weight=1)

        self.hist_tree = ttk.Treeview(tf, show='tree', selectmode='browse', style="HD.Treeview")
        self.hist_tree.column('#0', width=940, stretch=True)
        self.hist_tree.tag_configure('date',    font=(self.FONT_FAMILY, 10, 'bold'))
        self.hist_tree.tag_configure('session', foreground=Colors.RUST)
        self.hist_tree.tag_configure('newf',    foreground=Colors.SUCCESS)
        self.hist_tree.tag_configure('updf',    foreground="#7B5FAE")
        self.hist_tree.tag_configure('errf',    foreground=Colors.DANGER)

        hs = ctk.CTkScrollbar(tf, command=self.hist_tree.yview,
                               button_color=Colors.PRIMARY_LIGHT,
                               button_hover_color=Colors.PRIMARY)
        hs.grid(row=0, column=1, sticky="ns")
        self.hist_tree.configure(yscrollcommand=hs.set)
        self.hist_tree.grid(row=0, column=0, sticky="nsew")

    def _set_tab_badge(self, name, count):
        btn = self._tab_buttons.get(name)
        if btn:
            btn.configure(text=f"{name} ({count})" if count else name)

    # ══════════════════════════════════════════════
    #  核心邏輯
    # ══════════════════════════════════════════════

    def _log(self, msg, tag='info'):
        self.msg_queue.put((tag, msg))

    def _poll(self):
        while True:
            try:
                tag, msg = self.msg_queue.get_nowait()
                self.log_text.configure(state='normal')
                ts = datetime.now().strftime('%H:%M:%S')
                self.log_text.insert('end', f'[{ts}] ', 'time')
                self.log_text.insert('end', f'{msg}\n', tag)
                self.log_text.see('end')
                self.log_text.configure(state='disabled')
            except queue.Empty:
                break
        self.after(100, self._poll)

    def _browse(self):
        p = filedialog.askdirectory(title='選擇下載資料夾',
                                    initialdir=self.v_path.get() or os.path.expanduser('~'))
        if p:
            self.v_path.set(p)

    def _save_settings(self):
        fid = self.v_fid.get().strip()
        dl  = self.v_path.get().strip()
        if not fid:
            messagebox.showerror('錯誤', '請輸入 Google Drive 資料夾 ID 或連結'); return
        fid = self._parse_folder_id(fid)
        self.v_fid.set(fid)
        if not dl:
            messagebox.showerror('錯誤', '請設定下載路徑'); return
        os.makedirs(dl, exist_ok=True)
        self.config.update({'folder_id': fid, 'download_path': dl})
        save_config(self.config)
        self._log('設定已儲存', 'success')

    @staticmethod
    def _parse_folder_id(text):
        match = re.search(r'folders/([a-zA-Z0-9_-]+)', text)
        return match.group(1) if match else text

    # ── 自動更新 ──

    def _auto_check_update(self):
        threading.Thread(target=self._check_update_worker, daemon=True).start()

    def _check_update_worker(self):
        try:
            info = fetch_latest_release()
        except Exception as e:
            self._log(f'更新檢查略過（{type(e).__name__}）', 'info')
            return
        latest = info.get('version') or ''
        if parse_version(latest) <= parse_version(APP_VERSION):
            self._log(f'目前已是最新版本 v{APP_VERSION}', 'info')
            return
        if not info.get('msi_url'):
            self._log(f'偵測到新版本 v{latest}，但該版 Release 未附 MSI 安裝檔', 'warning')
            return
        self._log(f'偵測到新版本 v{latest}（目前 v{APP_VERSION}）', 'warning')
        try:
            self.after(0, lambda: self._prompt_update(info))
        except Exception:
            pass  # 視窗已關閉

    def _prompt_update(self, info):
        notes = (info.get('notes') or '').strip()
        if len(notes) > 600:
            notes = notes[:600] + '…'
        msg = (f"發現新版本 v{info['version']}（目前 v{APP_VERSION}）。\n\n"
               + (f"更新內容：\n{notes}\n\n" if notes else "")
               + "要立即更新嗎？\n更新時程式會自動關閉，安裝完成後自動重新開啟。")
        if not messagebox.askyesno('發現新版本', msg):
            self._log('已略過本次更新，可稍後重開程式再更新', 'info')
            return
        # 更新流程展開後不再接受新的同步（進行中的同步會等它跑完才升級）
        try:
            self.btn_sync.configure(state='disabled', text='更新中...')
        except Exception:
            pass
        threading.Thread(target=self._do_update_worker,
                         args=(info,), daemon=True).start()

    def _do_update_worker(self, info):
        try:
            self._log('正在下載更新...', 'info')
            msi = download_update_msi(
                info['msi_url'],
                os.path.join(tempfile.gettempdir(), '雲端圖資同步工具_update'),
                self._log,
                expected_size=info.get('msi_size'),
                expected_digest=info.get('msi_digest'))
        except Exception as e:
            self._log(f'更新下載失敗: {e}', 'error')
            try:
                self.after(0, self._update_failed_reset)
                self.after(0, lambda: messagebox.showerror(
                    '更新失敗', f'下載新版本失敗，請稍後再試。\n\n{e}'))
            except Exception:
                pass
            return
        if not getattr(sys, 'frozen', False):
            self._log(f'開發模式：已下載 {msi}，請自行安裝', 'warning')
            try:
                self.after(0, self._update_failed_reset)
            except Exception:
                pass
            return
        self._log('下載完成，等待同步結束後安裝並重新啟動...', 'success')
        try:
            self.after(0, lambda m=msi: self._launch_update(m))
        except Exception:
            pass

    def _update_failed_reset(self):
        try:
            self.btn_sync.configure(state='normal', text='立即同步')
        except Exception:
            pass

    def _launch_update(self, msi):
        if self.is_syncing:
            # 同步進行中不可殺程式（daemon 執行緒寫到一半會弄壞狀態檔），等它完成
            self.after(1000, lambda m=msi: self._launch_update(m))
            return
        try:
            launch_msi_upgrade(msi)
        except Exception as e:
            self._update_failed_reset()
            messagebox.showerror('更新失敗', f'無法啟動安裝程式：\n{e}')
            return
        self.destroy()

    # ── OAuth ──

    def _auto_connect(self):
        if not HAS_API:
            self._log('缺少 Google API 套件，請執行: pip install -r requirements.txt', 'error')
            self.v_status.set('缺少套件')
            self._status_dot.configure(text_color=Colors.DANGER)
            return
        self._log('正在連線 Google Drive API...', 'info')
        threading.Thread(target=self._connect, daemon=True).start()

    def _connect(self):
        try:
            creds = get_credentials()
            self.service = build('drive', 'v3', credentials=creds)
            self._log('已成功連線 Google Drive', 'success')
            self.after(0, lambda: self.v_status.set('已連線'))
            self.after(0, lambda: self._status_dot.configure(text_color=Colors.SUCCESS))
            self.after(0, lambda: self.btn_oauth.configure(
                text="已登入", fg_color=Colors.SUCCESS, hover_color=Colors.SUCCESS_HOVER,
                text_color="#FFFFFF", border_color=Colors.SUCCESS))
        except FileNotFoundError:
            self._log('找不到 credentials.json，請參閱 README.md', 'error')
            self.after(0, lambda: self.v_status.set('缺少憑證'))
            self.after(0, lambda: self._status_dot.configure(text_color=Colors.DANGER))
        except Exception as e:
            self._log(f'連線失敗: {e}', 'error')
            self.after(0, lambda: self.v_status.set('連線失敗'))
            self.after(0, lambda: self._status_dot.configure(text_color=Colors.DANGER))

    def _manual_oauth(self):
        if not HAS_API:
            messagebox.showerror('錯誤', '缺少 Google API 套件。\n請執行: pip install -r requirements.txt'); return
        token_path = os.path.join(DATA_DIR, 'token.json')
        if os.path.exists(token_path):
            os.remove(token_path)
            self._log('已清除舊 Token，正在重新認證...', 'warning')
        else:
            self._log('正在開啟 Google 登入頁面...', 'info')
        self.btn_oauth.configure(text="認證中...", state="disabled")
        threading.Thread(target=self._oauth_worker, daemon=True).start()

    def _oauth_worker(self):
        try:
            creds = get_credentials()
            self.service = build('drive', 'v3', credentials=creds)
            self._log('Google OAuth 認證成功', 'success')
            self.after(0, lambda: self.v_status.set('已連線'))
            self.after(0, lambda: self._status_dot.configure(text_color=Colors.SUCCESS))
            self.after(0, lambda: self.btn_oauth.configure(
                text="已登入", state="normal",
                fg_color=Colors.SUCCESS, hover_color=Colors.SUCCESS_HOVER,
                text_color="#FFFFFF", border_color=Colors.SUCCESS))
        except Exception as e:
            self._log(f'OAuth 認證失敗: {e}', 'error')
            self.after(0, lambda: self.v_status.set('認證失敗'))
            self.after(0, lambda: self._status_dot.configure(text_color=Colors.DANGER))
            self.after(0, lambda: self.btn_oauth.configure(
                text="登入 Google", state="normal",
                fg_color="#FFFFFF", hover_color="#FDF2E9",
                text_color=Colors.TEXT, border_color="#E9CDB9"))

    # ── Sync ──

    def _do_sync(self):
        if self.is_syncing:
            self._log('同步進行中，請稍候', 'warning'); return
        if not self.service:
            self._log('尚未連線', 'error'); return
        # 同步前自動讀取 UI 上的最新設定
        fid = self.v_fid.get().strip()
        dl  = self.v_path.get().strip()
        if fid:
            fid = self._parse_folder_id(fid)
            self.v_fid.set(fid)
        if not fid:
            messagebox.showerror('錯誤', '請輸入 Google Drive 資料夾 ID 或連結'); return
        if not dl:
            messagebox.showerror('錯誤', '請先設定下載路徑'); return
        # 自動儲存（用 update，避免把 flatten_xrefs 等其他設定鍵洗掉）
        os.makedirs(dl, exist_ok=True)
        self.config.update({'folder_id': fid, 'download_path': dl})
        save_config(self.config)
        self.is_syncing = True
        self.btn_sync.configure(state='disabled', text="同步中...")
        threading.Thread(target=self._sync_worker, daemon=True).start()

    @staticmethod
    def _sync_error_info(exc):
        winerror = getattr(exc, 'winerror', None)
        errno = getattr(exc, 'errno', None)

        if isinstance(exc, PermissionError) or errno == 13 or winerror in (5, 32, 33):
            return (
                '本機檔案被鎖定或沒有寫入權限',
                '請關閉正在開啟該檔案的 AutoCAD/檔案預覽窗，確認 Z 槽可寫入後再重新同步。'
            )
        if isinstance(exc, FileNotFoundError) or winerror == 3:
            return (
                '本機路徑不存在或網路磁碟暫時無法存取',
                '請確認 Z 槽已連線、下載路徑正確，必要時重新選擇下載資料夾。'
            )
        if errno == 28 or winerror == 112:
            return (
                '磁碟空間不足',
                '請清出本機或網路磁碟空間後再重新同步。'
            )
        if winerror == 206:
            return (
                '檔案路徑太長',
                '請縮短資料夾名稱或改用較短的下載路徑。'
            )
        return (
            f'{type(exc).__name__}: {exc}',
            '請稍後再同步；如果持續發生，請截圖這段錯誤訊息方便追查。'
        )

    def _show_sync_error_summary(self, errors):
        if not errors:
            return
        lines = [
            f'本次有 {len(errors)} 個檔案未完成同步。',
            '',
            '已成功的檔案不受影響；未成功的檔案下次會再嘗試。',
            '',
            '未同步檔案：'
        ]
        for item in errors[:5]:
            lines.append(f"- {item['file']}")
            lines.append(f"  原因: {item['reason']}")
        if len(errors) > 5:
            lines.append(f"...另有 {len(errors) - 5} 個，請看同步日誌。")
        lines.extend([
            '',
            '建議：先排除原因（例如關閉開啟中的 DWG/CAD 檔、檔案總管預覽窗），',
            '再到「未同步檔案」頁籤按「重試全部」，只會重新下載失敗的檔案。'
        ])
        messagebox.showwarning('同步完成，但有檔案未更新', '\n'.join(lines))

    def _show_sync_fatal_error(self, reason, hint, detail):
        messagebox.showerror(
            '同步無法繼續',
            f'同步流程已中止。\n\n原因: {reason}\n\n建議: {hint}\n\n原始錯誤: {detail}'
        )

    def _show_xref_conflicts(self, conflicts):
        """整合圖攤平時遇到同名子圖，跳出提示讓使用者自行清除重複/改名。"""
        lines = ["下列子圖在多個資料夾有『同名檔』，程式無法判斷該用哪一個，",
                 "已【不複製、不覆蓋】這些檔。請清除重複或改名後再同步：", ""]
        for c in conflicts[:15]:
            lines.append(f"● {c['name']}")
            for s in c['sources']:
                lines.append(f"      {s}")
        if len(conflicts) > 15:
            lines.append(f"…另有 {len(conflicts) - 15} 個")
        messagebox.showwarning("套圖連結：同名衝突需處理", "\n".join(lines))

    def _open_in_explorer(self, path):
        """在檔案總管中開啟並選取該檔（資料夾則直接開啟）。"""
        try:
            if os.path.isfile(path):
                subprocess.Popen(['explorer', '/select,', os.path.normpath(path)])
            elif os.path.isdir(path):
                os.startfile(path)  # noqa: P204
            else:
                messagebox.showinfo('檔案不存在', f'找不到：\n{path}\n（可能已被刪除）')
        except Exception as e:
            messagebox.showerror('無法開啟位置', str(e))

    def _show_orphans_panel(self, rows, recycled):
        """雲端已刪/改名的本機殘留檔面板。
        recycled=True：檔案已移入回收區，按鈕開到回收位置（可還原）。
        recycled=False：僅偵測未回收，按鈕開到原位置讓使用者自行處理。
        每個 row = {name, rel(原相對位置), open_path(要在檔案總管選取的檔)}。"""
        win = ctk.CTkToplevel(self)
        win.title('孤兒檔已移入回收區' if recycled else '雲端已移除的殘留檔案')
        win.geometry('760x560')
        win.configure(fg_color=Colors.BG)
        win.transient(self)

        head = ctk.CTkFrame(win, fg_color=Colors.HEADER_BG, corner_radius=0)
        head.pack(fill='x')
        if recycled:
            title = f'✅ 已把 {len(rows)} 個「雲端已刪除／改名」的殘留檔移入回收區'
            sub = f'檔案已移到各自資料夾的「{RECYCLE_DIR}／日期」底下（是移動、不是刪除，可還原）。按「開啟位置」去檢查或還原。'
            title_color = Colors.SUCCESS
        else:
            title = f'⚠ 發現 {len(rows)} 個「雲端已刪除／改名」後殘留在本機的檔案'
            sub = '雲端上已經沒有這些檔了，程式不會自動刪除。請按「開啟位置」進去檢查，自行決定是否刪除。'
            title_color = Colors.RUST
        ctk.CTkLabel(head, text=title, font=ctk.CTkFont(size=15, weight='bold'),
                     text_color=title_color).pack(anchor='w', padx=18, pady=(14, 2))
        ctk.CTkLabel(head, text=sub, text_color=Colors.TEXT_SEC,
                     wraplength=700, justify='left').pack(anchor='w', padx=18, pady=(0, 14))

        body = ctk.CTkScrollableFrame(win, fg_color=Colors.SURFACE)
        body.pack(fill='both', expand=True, padx=12, pady=(12, 6))

        shown = rows[:300]
        for o in shown:
            row = ctk.CTkFrame(body, fg_color=Colors.SURFACE_ALT, corner_radius=8)
            row.pack(fill='x', pady=4, padx=4)
            info = ctk.CTkFrame(row, fg_color='transparent')
            info.pack(side='left', fill='x', expand=True, padx=12, pady=8)
            ctk.CTkLabel(info, text=o['name'], anchor='w',
                         font=ctk.CTkFont(size=13, weight='bold'),
                         text_color=Colors.TEXT).pack(anchor='w')
            ctk.CTkLabel(info, text=('原位置：' if recycled else '') + o['rel'], anchor='w',
                         text_color=Colors.TEXT_MUTED).pack(anchor='w')
            ctk.CTkButton(row, text='📂 開啟位置', width=118,
                          fg_color=Colors.PRIMARY_LIGHT, text_color=Colors.TEXT,
                          hover_color=Colors.APRICOT_HOVER,
                          command=lambda p=o['open_path']: self._open_in_explorer(p)
                          ).pack(side='right', padx=(0, 10), pady=8)
        if len(rows) > len(shown):
            ctk.CTkLabel(body, text=f'…另有 {len(rows) - len(shown)} 個，詳見 update_log.txt',
                         text_color=Colors.TEXT_SEC).pack(anchor='w', padx=8, pady=6)

        foot = ctk.CTkFrame(win, fg_color='transparent')
        foot.pack(fill='x', padx=12, pady=(0, 12))
        ctk.CTkButton(foot, text='開啟下載資料夾',
                      fg_color=Colors.SURFACE_ALT, text_color=Colors.TEXT,
                      hover_color=Colors.APRICOT_HOVER,
                      command=lambda: self._open_in_explorer(self.config.get('download_path', ''))
                      ).pack(side='left')
        ctk.CTkButton(foot, text='關閉', width=90,
                      fg_color=Colors.PRIMARY, hover_color=Colors.PRIMARY_HOVER,
                      command=win.destroy).pack(side='right')
        win.after(60, win.lift)

    def _append_update_log(self, dl_path, kind, new_c, upd_c, err_c, errors):
        log_file = os.path.join(dl_path, 'update_log.txt')
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        try:
            with open(log_file, 'a', encoding='utf-8') as lf:
                lf.write(f"[{ts}] {kind}完成 - 新增: {new_c}, 更新: {upd_c}, 失敗: {err_c}\n")
                for item in errors:
                    lf.write(f"  未同步: {item.get('file', '')} | 原因: {item.get('reason', '')} | 路徑: {item.get('path', '')}\n")
        except Exception as e:
            reason, hint = self._sync_error_info(e)
            self._log(f'更新紀錄未寫入: {log_file}', 'error')
            self._log(f'  原因: {reason}', 'error')
            self._log(f'  建議: {hint}', 'warning')

    def _sync_worker(self):
        try:
            self._log('─' * 40, 'info')
            self._log('開始同步...', 'info')
            state = load_state()
            dl_path = self.config['download_path']
            os.makedirs(dl_path, exist_ok=True)
            # 記錄本次同步前每個 fileId 的下載位置，供事後偵測改名/搬移殘留
            old_paths = {fid: info.get('localPath')
                         for fid, info in state['files'].items()}

            self._log('正在掃描雲端資料夾...', 'info')
            files = list_files_recursive(self.service, self.config['folder_id'], log=self._log)
            if not files:
                self._log('資料夾中沒有檔案', 'warning'); return

            self._log(f'找到 {len(files)} 個檔案', 'info')
            new_c = upd_c = unch_c = err_c = 0
            session_new = []
            session_upd = []
            session_err = []
            session_ok_ids = set()

            for f in files:
                fid, fname, mime = f['id'], f['name'], f['mimeType']
                mod = f['modifiedTime']
                fpath = f.get('folderPath', '')
                display = os.path.join(fpath, fname) if fpath else fname
                if mime == 'application/vnd.google-apps.folder':
                    continue

                local_p = os.path.join(dl_path, fpath, fname)
                if mime in GOOGLE_EXPORT_TYPES and not os.path.splitext(fname)[1]:
                    local_p += GOOGLE_EXPORT_TYPES[mime][1]

                last_mod = state['files'].get(fid, {}).get('modifiedTime', '')

                action = None
                if not os.path.exists(local_p):
                    self._log(f'新增: {display}', 'new')
                    action = 'new'
                elif mod != last_mod:
                    self._log(f'更新: {display}', 'update')
                    action = 'update'
                else:
                    unch_c += 1; continue

                try:
                    result = download_file(self.service, fid, fname, mime, fpath, dl_path, self._log)
                except Exception as e:
                    err_c += 1
                    reason, hint = self._sync_error_info(e)
                    session_err.append({
                        'file': display,
                        'path': local_p,
                        'reason': reason,
                        'hint': hint,
                        'fileId': fid,
                        'fileName': fname,
                        'mimeType': mime,
                        'folderPath': fpath,
                        'modifiedTime': mod,
                        'action': action,
                        'time': datetime.now().isoformat(timespec='seconds'),
                    })
                    self._log(f'  未同步: {display}', 'error')
                    self._log(f'    原因: {reason}', 'error')
                    self._log(f'    建議: {hint}', 'warning')
                    self._log(f'    路徑: {local_p}', 'warning')
                    continue

                if result:
                    self._log(f'  完成: {fname}', 'success')
                    if action == 'new':
                        new_c += 1; session_new.append(display)
                    elif action == 'update':
                        upd_c += 1; session_upd.append(display)
                    state['files'][fid] = {
                        'name': fname, 'folderPath': fpath,
                        'modifiedTime': mod, 'localPath': result,
                    }
                    session_ok_ids.add(fid)

            save_state(state)

            # 整合圖 XREF 攤平：複製子圖到整合圖那層，讓外部參考解析得到
            try:
                fx = flatten_xrefs(dl_path, self._log,
                                   enabled=self.config.get('flatten_xrefs', True))
                conflicts = fx['conflicts']
                if fx['copied'] or fx['removed'] or conflicts:
                    self._log(
                        f"套圖連結整理完成  複製: {fx['copied']}  移除失效: {fx['removed']}"
                        + (f"  ⚠同名衝突: {len(conflicts)}" if conflicts else ''),
                        'warning' if conflicts else 'success')
                    with open(os.path.join(dl_path, 'update_log.txt'), 'a', encoding='utf-8') as lf:
                        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        lf.write(f"[{ts}] 套圖連結整理 - 複製: {fx['copied']}, "
                                 f"移除失效: {fx['removed']}, 同名衝突: {len(conflicts)}\n")
                        for c in conflicts:
                            lf.write(f"  同名衝突(未複製): {c['folder']}\\{c['name']} "
                                     f"← {'、'.join(c['sources'])}\n")
                for c in conflicts:
                    self._log(f"  ⚠同名衝突(未複製): {c['name']}｜{'、'.join(c['sources'])}", 'warning')
                if conflicts:
                    self.after(0, lambda cf=conflicts: self._show_xref_conflicts(cf))
            except Exception as e:
                self._log(f'套圖連結整理略過: {e}', 'warning')

            # 孤兒檔處理：雲端已刪/改名的本機殘留檔，移到「回收區」（可還原、非刪除）
            try:
                cloud_ids = {f['id'] for f in files}
                orphans = find_orphans(state['files'], cloud_ids, old_paths, dl_path)
                if orphans:
                    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    if self.config.get('auto_recycle_orphans', True):
                        date_str = datetime.now().strftime('%Y-%m-%d')
                        moved, failed = recycle_orphans(orphans, date_str)
                        self._log(
                            f'已回收 {len(moved)} 個雲端已移除的殘留檔到「{RECYCLE_DIR}/{date_str}」'
                            + (f'（{len(failed)} 個回收失敗）' if failed else ''), 'warning')
                        for mv in moved[:10]:
                            self._log(f'  回收: {mv["rel"]}', 'warning')
                        for fa in failed:
                            self._log(f'  回收失敗: {fa["name"]}｜{fa["error"]}', 'error')
                        with open(os.path.join(dl_path, 'update_log.txt'), 'a', encoding='utf-8') as lf:
                            lf.write(f"[{ts}] 孤兒檔回收: 移入回收區 {len(moved)} 個, 失敗 {len(failed)} 個\n")
                            for mv in moved:
                                lf.write(f"  回收: {mv['rel']} → {os.path.relpath(mv['dest'], dl_path)}\n")
                        rows = [{'name': mv['name'], 'rel': mv['rel'], 'open_path': mv['dest']}
                                for mv in moved]
                        if rows:
                            self.after(0, lambda r=rows: self._show_orphans_panel(r, True))
                    else:
                        self._log(f'發現 {len(orphans)} 個雲端已移除的殘留檔（未回收）', 'warning')
                        with open(os.path.join(dl_path, 'update_log.txt'), 'a', encoding='utf-8') as lf:
                            lf.write(f"[{ts}] 雲端已移除殘留檔: {len(orphans)} 個（未回收）\n")
                            for o in orphans:
                                lf.write(f"  殘留: {o['rel']}\n")
                        rows = [{'name': o['name'], 'rel': o['rel'], 'open_path': o['path']}
                                for o in orphans]
                        self.after(0, lambda r=rows: self._show_orphans_panel(r, False))
            except Exception as e:
                self._log(f'孤兒檔處理略過: {e}', 'warning')

            # 寫入同步歷史：本次紀錄 + 更新未同步清單（成功的移除、失敗的更新）
            history = load_history()
            err_ids = {item.get('fileId') for item in session_err}
            history['pending'] = [
                p for p in history.get('pending', [])
                if p.get('fileId') not in session_ok_ids and p.get('fileId') not in err_ids
            ]
            history['pending'].extend(session_err)
            history['sessions'].append({
                'timestamp': datetime.now().isoformat(timespec='seconds'),
                'type': 'sync',
                'new': session_new, 'updated': session_upd, 'errors': session_err,
            })
            save_history(history)

            self._log('─' * 40, 'info')
            summary = f'同步完成  新增: {new_c}  更新: {upd_c}  未變更: {unch_c}'
            if err_c:
                summary += f'  失敗: {err_c}'
            self._log(summary, 'warning' if err_c else 'success')
            if session_err:
                self._log('未同步檔案摘要:', 'warning')
                for item in session_err[:10]:
                    self._log(f"  - {item['file']}｜{item['reason']}", 'warning')
                if len(session_err) > 10:
                    self._log(f'  ...另有 {len(session_err) - 10} 個，請先處理上方相同原因後再同步。', 'warning')
                self.after(0, lambda errors=session_err: self._show_sync_error_summary(errors))
            self.after(0, lambda: self.v_last.set(datetime.now().strftime('%H:%M:%S')))
            self.after(0, self._refresh_files)

            self._today_new.extend(session_new)
            self._today_updated.extend(session_upd)
            self._today_last_ts = datetime.now()
            self.after(0, self._render_today)
            self.after(0, self._reload_history_views)

            if new_c > 0 or upd_c > 0 or err_c > 0:
                self._append_update_log(dl_path, '同步', new_c, upd_c, err_c, session_err)
        except Exception as e:
            reason, hint = self._sync_error_info(e)
            detail = f'{type(e).__name__}: {e}'
            self._log(f'同步流程中止: {reason}', 'error')
            self._log(f'  建議: {hint}', 'warning')
            self._log(f'  原始錯誤: {detail}', 'error')
            self.after(0, lambda reason=reason, hint=hint, detail=detail: self._show_sync_fatal_error(reason, hint, detail))
        finally:
            self.is_syncing = False
            self.after(0, lambda: self.btn_sync.configure(state='normal', text="立即同步"))

    # ── 今日更新 ──

    @staticmethod
    def _group_update_paths(paths):
        groups = {}
        for path in paths:
            normalized = path.replace('/', os.sep)
            folder, name = os.path.split(normalized)
            folder = folder or '(根目錄)'
            name = name or normalized
            groups.setdefault(folder, []).append(name)

        return [
            (folder, sorted(names, key=str.casefold))
            for folder, names in sorted(groups.items(), key=lambda item: item[0].casefold())
        ]

    def _build_today_full_text(self, today_str, total_files, total_groups, sections, sync_ts):
        """完整純文字（供複製到 LINE，永遠完整、不受收合影響，格式與通知一致）。"""
        full = [f"【雲端圖資更新通知】{today_str}",
                f"本次共 {total_files} 個檔案｜{total_groups} 個資料夾", ""]
        for si, (title, groups, cnt) in enumerate(sections):
            if si:
                full.append("")
            full.append('=' * 36)
            full.append(f'{title} {cnt} 個檔案｜{len(groups)} 個資料夾')
            full.append('=' * 36)
            for folder, names in groups:
                full.append("")
                full.append(f'[{folder}] 共 {len(names)} 個')
                for idx, name in enumerate(names, 1):
                    full.append(f'  {idx:02d}. {name}')
        if not sections:
            full.append("本次同步無新增或更新。")
        full.extend(["", f"同步時間: {sync_ts.strftime('%H:%M')}"])
        return "\n".join(full)

    def _toggle_today_group(self, key):
        """點擊資料夾標題 → 收合/展開該資料夾的檔案清單（預設展開）。"""
        if not hasattr(self, '_today_collapsed'):
            self._today_collapsed = set()
        self._today_collapsed.discard(key) if key in self._today_collapsed \
            else self._today_collapsed.add(key)
        self._render_today(switch=False)

    def _render_today(self, switch=True):
        if not hasattr(self, '_today_collapsed'):
            self._today_collapsed = set()
        today_str = datetime.now().strftime('%Y/%m/%d')
        total_files = len(self._today_new) + len(self._today_updated)
        total_groups = len(self._group_update_paths(self._today_new + self._today_updated))
        sync_ts = self._today_last_ts or datetime.now()

        sections = []  # (標題, [(folder, [names])...], 檔案數)
        if self._today_new:
            sections.append(('新增', self._group_update_paths(self._today_new), len(self._today_new)))
        if self._today_updated:
            sections.append(('更新', self._group_update_paths(self._today_updated), len(self._today_updated)))

        # 供複製用的完整文字（永遠完整）
        self._today_full_text = self._build_today_full_text(
            today_str, total_files, total_groups, sections, sync_ts)

        # 顯示：資料夾可收合，預設展開
        txt = self.today_text
        try:
            yv = txt.yview()[0]
        except Exception:
            yv = 0.0
        txt.configure(state="normal")
        for t in txt.tag_names():
            if t.startswith("todayhdr_"):
                txt.tag_delete(t)
        txt.delete("1.0", "end")

        txt.insert("end", f"【雲端圖資更新通知】{today_str}\n", ("today_h1",))
        txt.insert("end", f"本次共 {total_files} 個檔案｜{total_groups} 個資料夾\n\n", ("today_dim",))

        gid = 0
        for si, (title, groups, cnt) in enumerate(sections):
            if si:
                txt.insert("end", "\n")
            txt.insert("end", f'　{title}　{cnt} 個檔案｜{len(groups)} 個資料夾\n', ("today_sec",))
            for folder, names in groups:
                key = f"{title}\x00{folder}"
                collapsed = key in self._today_collapsed
                arrow = "▶" if collapsed else "▼"
                htag = f"todayhdr_{gid}"
                gid += 1
                txt.insert("end", "\n")
                txt.insert("end", f'{arrow} [{folder}] 共 {len(names)} 個\n', ("today_folder", htag))
                txt.tag_bind(htag, "<Button-1>", lambda e, k=key: self._toggle_today_group(k))
                txt.tag_bind(htag, "<Enter>", lambda e, w=txt: w.configure(cursor="hand2"))
                txt.tag_bind(htag, "<Leave>", lambda e, w=txt: w.configure(cursor=""))
                if not collapsed:
                    for idx, name in enumerate(names, 1):
                        txt.insert("end", f'      {idx:02d}. {name}\n')
        if not sections:
            txt.insert("end", "本次同步無新增或更新。\n")
        txt.insert("end", f"\n同步時間: {sync_ts.strftime('%H:%M')}\n", ("today_dim",))

        txt.tag_configure("today_h1", font=(self.FONT_FAMILY, 14, "bold"), foreground=Colors.RUST)
        txt.tag_configure("today_sec", font=(self.FONT_FAMILY, 12, "bold"), foreground=Colors.RUST,
                          spacing1=6, spacing3=4)
        txt.tag_configure("today_folder", font=(self.FONT_FAMILY, 12, "bold"), foreground=Colors.ACCENT)
        txt.tag_configure("today_dim", foreground=Colors.TEXT_MUTED)
        txt.configure(state="disabled")
        try:
            txt.yview_moveto(yv)
        except Exception:
            pass

        count = total_files
        self.lbl_today_title.configure(
            text=f"今日更新 — {count} 個檔案 / {total_groups} 個資料夾" if count else "今日無更新")
        self._set_tab_badge("今日更新", count)
        if count > 0 and switch:
            self._switch_tab("今日更新")

    def _copy_today(self):
        # 用完整文字（收合中的資料夾也會完整複製），不受畫面收合影響
        text = getattr(self, '_today_full_text', '').strip()
        if not text:
            messagebox.showinfo('提示', '尚無更新內容可複製。\n請先執行同步。'); return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()
        orig = self.btn_copy.cget("text")
        self.btn_copy.configure(text="已複製!", fg_color=Colors.SUCCESS)
        self.after(2000, lambda: self.btn_copy.configure(text=orig, fg_color=Colors.PRIMARY))

    # ── 歷史紀錄 / 未同步重試 ──

    def _restore_from_history(self):
        """啟動時還原今日更新面板與上次同步時間，關閉程式不再遺失"""
        history = load_history()
        sessions = history.get('sessions', [])
        today = datetime.now().strftime('%Y-%m-%d')
        last_today_ts = None
        for s in sessions:
            ts = str(s.get('timestamp', ''))
            if ts[:10] == today:
                self._today_new.extend(s.get('new', []))
                self._today_updated.extend(s.get('updated', []))
                last_today_ts = ts
        if sessions:
            try:
                dt = datetime.fromisoformat(str(sessions[-1].get('timestamp', '')))
                self.v_last.set(dt.strftime('%Y/%m/%d %H:%M'))
            except ValueError:
                pass
        if self._today_new or self._today_updated:
            if last_today_ts:
                try:
                    self._today_last_ts = datetime.fromisoformat(last_today_ts)
                except ValueError:
                    pass
            self._render_today(switch=False)

    def _reload_history_views(self):
        self._render_pending()
        self._render_history()

    @staticmethod
    def _pending_rows(pending):
        """為每筆未同步項目產生穩定且唯一的列 ID"""
        rows = []
        used = set()
        for i, p in enumerate(pending):
            iid = p.get('fileId') or f'noid-{i}'
            if iid in used:
                iid = f'dup-{i}'
            used.add(iid)
            rows.append((iid, p))
        return rows

    def _render_pending(self):
        pending = load_history().get('pending', [])
        self.pending_tree.delete(*self.pending_tree.get_children())
        for i, (iid, p) in enumerate(self._pending_rows(pending)):
            ts = str(p.get('time', ''))
            try:
                t_disp = datetime.fromisoformat(ts).strftime('%m/%d %H:%M')
            except ValueError:
                t_disp = ts[:16]
            tag = 'oddrow' if i % 2 else 'evenrow'
            self.pending_tree.insert('', 'end', iid=iid, tags=(tag,),
                                     values=(p.get('file', ''), p.get('reason', ''), t_disp))
        n = len(pending)
        if n:
            self.lbl_pending_title.configure(
                text=f"未同步檔案 — {n} 個（先排除原因，再按「重試全部」重新下載覆蓋）")
        else:
            self.lbl_pending_title.configure(text="目前沒有未同步的檔案")
        self.btn_retry.configure(state="normal" if (n and not self.is_syncing) else "disabled")
        self._set_tab_badge("未同步檔案", n)

    WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日']

    def _render_history(self):
        sessions = load_history().get('sessions', [])
        self.hist_tree.delete(*self.hist_tree.get_children())
        if not sessions:
            self.hist_tree.insert('', 'end', text='尚無同步紀錄，執行「立即同步」後會自動記錄。')
            return

        by_date = {}
        for s in sessions:
            by_date.setdefault(str(s.get('timestamp', ''))[:10], []).append(s)

        today = datetime.now().strftime('%Y-%m-%d')
        for d in sorted(by_date.keys(), reverse=True)[:90]:
            day_sessions = by_date[d]
            n = sum(len(s.get('new', [])) for s in day_sessions)
            u = sum(len(s.get('updated', [])) for s in day_sessions)
            e = sum(len(s.get('errors', [])) for s in day_sessions)
            try:
                dt = datetime.fromisoformat(d)
                d_disp = f"{dt.strftime('%Y/%m/%d')}（週{self.WEEKDAYS[dt.weekday()]}）"
            except ValueError:
                d_disp = d
            date_id = self.hist_tree.insert(
                '', 'end', open=(d == today), tags=('date',),
                text=f"{d_disp}　新增 {n}｜更新 {u}｜失敗 {e}｜同步 {len(day_sessions)} 次")
            for s in reversed(day_sessions):
                ts = str(s.get('timestamp', ''))
                t_disp = ts[11:16] if len(ts) >= 16 else ts
                kind = '重試' if s.get('type') == 'retry' else '同步'
                sn, su, se = s.get('new', []), s.get('updated', []), s.get('errors', [])
                if sn or su or se:
                    txt = f"{t_disp} {kind} — 新增 {len(sn)}、更新 {len(su)}、失敗 {len(se)}"
                else:
                    txt = f"{t_disp} {kind} — 無變更"
                sid = self.hist_tree.insert(date_id, 'end', text=txt, tags=('session',))
                self._insert_history_files(sid, '新增', sn, 'newf')
                self._insert_history_files(sid, '更新', su, 'updf')
                self._insert_history_files(
                    sid, '失敗',
                    [f"{item.get('file', '?')}｜{item.get('reason', '')}" for item in se],
                    'errf')

    def _insert_history_files(self, parent, label, items, tag):
        LIMIT = 200
        for it in items[:LIMIT]:
            self.hist_tree.insert(parent, 'end', text=f"{label}　{it}", tags=(tag,))
        if len(items) > LIMIT:
            self.hist_tree.insert(parent, 'end', tags=(tag,),
                                  text=f"…另有 {len(items) - LIMIT} 個")

    def _do_retry(self):
        if self.is_syncing:
            self._log('同步進行中，請稍候', 'warning'); return
        if not self.service:
            self._log('尚未連線，無法重試', 'error'); return
        if not load_history().get('pending'):
            self._log('沒有需要重試的檔案', 'info'); return
        dl = self.v_path.get().strip() or self.config.get('download_path', '')
        if not dl:
            messagebox.showerror('錯誤', '請先設定下載路徑'); return
        self.is_syncing = True
        self.btn_sync.configure(state='disabled')
        self.btn_retry.configure(state='disabled', text="重試中...")
        threading.Thread(target=self._retry_worker, args=(dl,), daemon=True).start()

    def _retry_worker(self, dl_path):
        try:
            self._log('─' * 40, 'info')
            self._log('開始重試未同步檔案...', 'info')
            state = load_state()
            history = load_history()
            pending = history.get('pending', [])
            ok_new, ok_upd, still = [], [], []

            for p in pending:
                fid   = p.get('fileId')
                fname = p.get('fileName') or os.path.basename(str(p.get('file', '')))
                mime  = p.get('mimeType', '')
                fpath = p.get('folderPath', '')
                display = p.get('file') or fname
                if not fid:
                    p2 = dict(p)
                    p2['reason'] = '缺少重試資訊，請直接按「立即同步」'
                    still.append(p2)
                    continue
                self._log(f'重試: {display}', 'update')
                try:
                    result = download_file(self.service, fid, fname, mime, fpath, dl_path, self._log)
                except Exception as e:
                    reason, hint = self._sync_error_info(e)
                    p2 = dict(p)
                    p2.update({'reason': reason, 'hint': hint,
                               'time': datetime.now().isoformat(timespec='seconds')})
                    still.append(p2)
                    self._log(f'  仍未同步: {display}', 'error')
                    self._log(f'    原因: {reason}', 'error')
                    self._log(f'    建議: {hint}', 'warning')
                    continue
                if result:
                    self._log(f'  完成: {fname}', 'success')
                    state['files'][fid] = {
                        'name': fname, 'folderPath': fpath,
                        'modifiedTime': p.get('modifiedTime', ''), 'localPath': result,
                    }
                    (ok_new if p.get('action') == 'new' else ok_upd).append(display)

            save_state(state)
            history['pending'] = still
            history['sessions'].append({
                'timestamp': datetime.now().isoformat(timespec='seconds'),
                'type': 'retry',
                'new': ok_new, 'updated': ok_upd, 'errors': still,
            })
            save_history(history)

            ok_c = len(ok_new) + len(ok_upd)
            self._log('─' * 40, 'info')
            self._log(f'重試完成  成功: {ok_c}  仍失敗: {len(still)}',
                      'warning' if still else 'success')
            if ok_c or still:
                self._append_update_log(dl_path, '重試', len(ok_new), len(ok_upd), len(still), still)

            self._today_new.extend(ok_new)
            self._today_updated.extend(ok_upd)
            if ok_c:
                self._today_last_ts = datetime.now()
            self.after(0, lambda: self.v_last.set(datetime.now().strftime('%H:%M:%S')))
            self.after(0, self._refresh_files)
            self.after(0, lambda: self._render_today(switch=False))
            self.after(0, self._reload_history_views)
        except Exception as e:
            reason, hint = self._sync_error_info(e)
            self._log(f'重試流程中止: {reason}', 'error')
            self._log(f'  建議: {hint}', 'warning')
        finally:
            self.is_syncing = False
            self.after(0, lambda: self.btn_sync.configure(state='normal', text="立即同步"))
            self.after(0, lambda: self.btn_retry.configure(text="重試全部"))
            self.after(0, self._render_pending)

    def _remove_selected_pending(self):
        if self.is_syncing:
            self._log('同步進行中，請稍候再調整清單', 'warning'); return
        sel = set(self.pending_tree.selection())
        if not sel:
            messagebox.showinfo('提示', '請先在清單中選取要移除的項目。'); return
        if not messagebox.askyesno(
                '移除確認',
                f'確定從未同步清單移除選取的 {len(sel)} 個項目？\n\n'
                '移除只是不再追蹤提醒；若雲端之後有新版本，\n下次「立即同步」仍會重新下載。'):
            return
        history = load_history()
        pending = history.get('pending', [])
        kept = [p for iid, p in self._pending_rows(pending) if iid not in sel]
        history['pending'] = kept
        save_history(history)
        self._log(f'已從未同步清單移除 {len(pending) - len(kept)} 個項目', 'info')
        self._render_pending()

    # ── File List ──

    def _refresh_files(self):
        state = load_state()
        dl = self.config.get('download_path', '')
        self._all_files = []
        for fid, info in state.get('files', {}).items():
            name = info.get('name', '')
            folder = info.get('folderPath', '')
            mod = info.get('modifiedTime', '')
            try:
                dt = datetime.fromisoformat(mod.replace('Z', '+00:00'))
                ts = dt.strftime('%Y-%m-%d %H:%M')
            except Exception:
                ts = mod[:19] if mod else '--'
            # 用目前下載路徑檢查，而非 state 裡的舊路徑
            if dl:
                expected = os.path.join(dl, folder, name)
                exists = os.path.exists(expected)
            else:
                exists = False
            self._all_files.append((folder, name, ts, '已同步' if exists else '遺失'))
        self._all_files.sort(key=lambda x: (x[0], x[1]))
        self.v_count.set(f'{len(self._all_files)} 個')
        self._show_files(self._all_files)

    def _show_files(self, files):
        self.tree.delete(*self.tree.get_children())
        for i, (folder, name, mod, st) in enumerate(files):
            tag = 'oddrow' if i % 2 else 'evenrow'
            self.tree.insert('', 'end', values=(folder or '(根目錄)', name, mod, st), tags=(tag,))
        self.lbl_fcount.configure(text=f'顯示 {len(files)} / {len(self._all_files)} 個檔案')

    def _filter_files(self):
        kw = self.v_search.get().strip().lower()
        if not kw:
            self._show_files(self._all_files); return
        self._show_files([f for f in self._all_files if kw in f[0].lower() or kw in f[1].lower()])

    # ── Close ──

    def _on_close(self):
        self.destroy()


if __name__ == '__main__':
    app = DriveSyncApp()
    app.mainloop()
