"""Bounded, manually shared DWG diagnostics; no credentials or drawing uploads."""
import datetime
import os
from pathlib import Path
import platform
import re
import sys
import traceback
import uuid


class ComparisonDiagnostics:
    def __init__(self, sources=()):
        self.events = []
        self.context = {}
        self.secrets = []
        for i, source in enumerate(sources):
            self.secrets.extend([(str(source), f"<DWG_{i+1}>"),
                                 (Path(source).name, f"<DWG_{i+1}>")])
        for name in ('USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'USERNAME', 'COMPUTERNAME'):
            value = os.environ.get(name)
            if value and len(value) > 2:
                self.secrets.append((value, f"<{name}>"))

    def redact(self, text):
        text = str(text)
        # Encoded AutoLISP strings can contain paths/names expressed as chr().
        text = '\n'.join('<encoded command omitted>' if '(chr ' in line.lower() else line
                         for line in text.splitlines())
        for value, replacement in sorted(self.secrets, key=lambda item: -len(item[0])):
            for variant in (value, value.replace('\\', '/'), value.replace('\\', '\\\\')):
                text = re.sub(re.escape(variant), lambda _: replacement, text, flags=re.I)
        text = re.sub(r'[\w.+-]{1,128}@[\w.-]{1,253}\.[A-Za-z]{2,24}', '<email>', text)
        text = re.sub(r'(?i)(?:[a-z]:[\\/]|\\\\)[^\r\n"<>|]*', '<path>', text)
        text = re.sub(r'(?im)^.*(?:authorization\s*:|access_token|refresh_token|client_secret|Bearer\s+).*$','<sensitive line omitted>', text)
        return text

    def note(self, **values):
        self.context.update({key: self.redact(value) for key, value in values.items()})

    def console(self, text):
        excerpt = text if len(text) <= 16000 else text[:2000] + '\n[中間紀錄已省略]\n' + text[-13500:]
        self.events.append(dict(self.context, console=self.redact(excerpt)))
        self.events = self.events[-4:]

    def report(self, error):
        version = getattr(sys.modules.get('__main__'), 'APP_VERSION', 'development')
        lines = [f'DWG 錯誤報告 / {uuid.uuid4().hex[:12]}',
                 f'時間：{datetime.datetime.now().astimezone().isoformat(timespec="seconds")}',
                 f'工具版本：{version}', f'系統：{platform.system()} {platform.release()} {platform.version()}',
                 f'Python：{platform.python_version()} / {platform.machine()}',
                 '僅供手動回傳；不含 DWG 附件、不讀取 Google token。',
                 '已遮蔽常見路徑、帳號與電子郵件；仍請確認未含專案名稱等敏感文字。',
                 '', '錯誤：' + self.redact(error), '最後階段：' + str(self.context)]
        for i, event in enumerate(self.events, 1):
            lines.extend(['', f'--- AutoCAD 呼叫 {i}（長紀錄保留開頭及結尾）---'])
            lines.extend(f'{key}: {value}' for key, value in event.items())
        lines.extend(['', '--- 程式堆疊 ---', self.redact(''.join(traceback.format_exception(error)))])
        return '\n'.join(lines)
