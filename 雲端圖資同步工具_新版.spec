# -*- mode: python ; coding: utf-8 -*-
# onedir 模式：exe + _internal 資料夾，由 MSI 整包安裝。
# 不用 onefile 的原因：onefile 每次啟動要解壓到 %TEMP%\_MEIxxxx 再載入 python DLL，
# 在裝有嚴格防毒/AppLocker 的電腦（工地筆電常見）會被攔截而無法啟動，
# 且啟動較慢。onedir 完全不碰 Temp。

a = Analysis(
    ['drive_sync_gui.py'],
    pathex=[],
    binaries=[],
    datas=[('credentials.json', '.'), ('installer/app.ico', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='雲端圖資同步工具_新版',
    icon='installer/app.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='雲端圖資同步工具_新版',
)
