# -*- coding: utf-8 -*-
"""
一次性：把各整合圖資料夾底下的子圖，複製一份到整合圖那層，
讓整合圖裡 ".\\檔名.dwg" 形式的外部參考(XREF)能解析得到。

- 直接重用 drive_sync_gui.py 內已測試的 flatten_xrefs()（單一來源）。
- 只複製、不修改任何原始 DWG；☆ 開頭的整合圖主檔永不被複製覆蓋。
- 讀取 config.json 的 download_path 作為目標資料夾。
- 用法：  python flatten_xref_once.py            → 執行攤平
         python flatten_xref_once.py --clean    → 反向清除本工具產生的複製檔
"""
import os
import sys
import types

# 這支腳本不需要 GUI，先用 stub 頂替 customtkinter 以便載入函式
if 'customtkinter' not in sys.modules:
    _stub = types.ModuleType('customtkinter')

    class _Any:
        def __init__(self, *a, **k):
            pass

        def __call__(self, *a, **k):
            return _Any()

        def __getattr__(self, n):
            return _Any()

    _stub.__getattr__ = lambda name: _Any
    sys.modules['customtkinter'] = _stub

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import drive_sync_gui as g  # noqa: E402

def main():
    enabled = '--clean' not in sys.argv
    cfg = g.load_config()
    dl_path = cfg.get('download_path', '')
    if not dl_path or not os.path.isdir(dl_path):
        print(f'找不到下載資料夾（config.json 的 download_path）：{dl_path}')
        return 1

    print(f'目標資料夾：{dl_path}')
    print('模式：' + ('攤平複製' if enabled else '清除本工具產生的複製檔'))
    print('-' * 60)
    stats = g.flatten_xrefs(dl_path, log=print, enabled=enabled)
    print('-' * 60)
    print(f"完成：複製 {stats['copied']}、移除失效 {stats['removed']}、"
          f"略過(既有同名) {stats['skipped']}、同名衝突 {len(stats['conflicts'])}")
    if stats['conflicts']:
        print('\n⚠ 以下子圖同名衝突，已「不複製」，請清除重複或改名後再執行：')
        for c in stats['conflicts']:
            print(f"  ● {c['folder']}\\{c['name']}")
            for s in c['sources']:
                print(f"        來源: {s}")
    return 0

if __name__ == '__main__':
    sys.exit(main())
