"""Keep recoverable local predecessors before atomically installing downloads."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import uuid


def enabled(pair):
    return bool(pair.get('archive_updates', pair.get('name') == '主要圖資'))


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def _relative(root, destination):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    relative = destination.relative_to(root)
    if not relative.parts or '回收區' in relative.parts:
        raise ValueError('更新路徑不可位於回收區或同步根目錄。')
    return root, destination, relative


def _bucket(root, relative, recycle_date=None):
    # A short deterministic key avoids exceeding Windows path limits.
    key = hashlib.sha256(str(relative).casefold().encode('utf-8')).hexdigest()[:24]
    base = root / '回收區'
    if recycle_date is not None:
        base /= recycle_date
    path = base / '更新留存' / key
    path.resolve().relative_to(root)  # Reject a junction pointing outside root.
    return path


def _write_record(path, record):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def install_revision(temporary, destination, root, log=lambda text: None):
    """Move the predecessor into a dated archive before installing a download.

    Records are prepared before install and committed after it. If committing
    metadata fails, a reader can recover a prepared record by matching the
    current file's hash to the planned new version. No cloud/state writes here.
    """
    root, destination, relative = _relative(root, destination)
    if not destination.exists():
        os.replace(temporary, destination)
        return None
    old_hash, new_hash = digest(destination), digest(temporary)
    if old_hash == new_hash:
        os.replace(temporary, destination)
        return None
    now = datetime.now().astimezone()
    stamp = now.isoformat(timespec='microseconds')
    directory = _bucket(root, relative, now.strftime('%Y-%m-%d')) / (now.strftime('%Y%m%d-%H%M%S-%f') + '-' + uuid.uuid4().hex[:8])
    directory.mkdir(parents=True, exist_ok=False)
    backup = directory / destination.name
    record = dict(relative_path=str(relative), filename=destination.name, time=stamp,
                  old_sha256=old_hash, new_sha256=new_hash, state='prepared')
    manifest = directory / 'revision.json'
    _write_record(manifest, record)
    # Prepare recoverable metadata before moving anything away from its path.
    if digest(destination) != old_hash:
        raise OSError('舊檔在安裝新版前變動；已停止覆蓋，請稍後重試。')
    os.rename(destination, backup)
    try:
        if digest(backup) != old_hash:
            raise OSError('舊檔在移至回收區時變動；已停止安裝新版。')
        # On Windows rename refuses an existing target, including one created
        # by another program after the predecessor was moved out of the way.
        if destination.exists():
            raise FileExistsError('原路徑出現另一份檔案；已停止安裝新版。')
        os.rename(temporary, destination)
    except OSError as install_error:
        try:
            if destination.exists():
                raise FileExistsError('原路徑已存在檔案，不可覆蓋還原。')
            os.rename(backup, destination)
        except OSError as restore_error:
            raise OSError(f'新版安裝失敗，舊檔仍安全留存在：{backup}\n原路徑未還原：{restore_error}') from install_error
        raise
    record['state'] = 'committed'
    try:
        _write_record(manifest, record)
    except OSError:
        log('  舊版已留存；紀錄確認未完成，開啟比對時會核對目前檔案。')
    log(f'  舊版留存: {backup}')
    return str(backup)


def _revision_directories(root, relative):
    buckets = [_bucket(root, relative)]  # v1.2.12: no date folder above the key.
    recycle = root / '回收區'
    if recycle.is_dir():
        for date_dir in recycle.iterdir():
            try:
                datetime.strptime(date_dir.name, '%Y-%m-%d')
                if date_dir.is_dir():
                    buckets.append(_bucket(root, relative, date_dir.name))
            except (OSError, ValueError):
                continue
    directories = []
    for bucket in buckets:
        if bucket.is_dir():
            directories.extend(bucket.iterdir())
    return sorted(directories, key=lambda path: path.name, reverse=True)


def latest_revision(root, destination):
    root, destination, relative = _relative(root, destination)
    for directory in _revision_directories(root, relative):
        if not directory.is_dir():
            continue
        try:
            record = json.loads((directory / 'revision.json').read_text(encoding='utf-8'))
            if not isinstance(record, dict):
                continue
            if record.get('relative_path') != str(relative) or record.get('filename') != destination.name:
                continue
            backup = (directory / destination.name).resolve()
            backup.relative_to(root)
            if not backup.is_file() or digest(backup) != record['old_sha256']:
                continue
            if record.get('state') != 'committed':
                if record.get('state') != 'prepared' or not destination.is_file() or digest(destination) != record['new_sha256']:
                    continue
            return dict(record, old_path=str(backup), new_path=str(destination), archive_folder=str(directory))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None
