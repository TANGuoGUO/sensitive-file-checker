# -*- mode: python ; coding: utf-8 -*-
import os


a = Analysis(
    ['app.py'],
    pathex=['vendor'],
    binaries=[],
    datas=[],
    hiddenimports=['pypdf', 'pythoncom', 'pywintypes', 'win32com.client'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
# Qt 6.10 uses Windows' unversioned ICU API. A development PATH containing
# Poppler can make PyInstaller collect a different ICU DLL with *_78 exports,
# which prevents QtCore from loading. These are Windows platform libraries;
# resolve them from System32 rather than bundling unrelated native runtimes.
platform_dlls = {'icuuc.dll', 'icudt78.dll', 'ucrtbase.dll'}
a.binaries = [entry for entry in a.binaries
              if os.path.basename(entry[0].replace('\\', '/')).lower() not in platform_dlls]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='SensitiveFileChecker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\app.ico'],
)
