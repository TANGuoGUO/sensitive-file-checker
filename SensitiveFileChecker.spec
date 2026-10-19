# -*- mode: python ; coding: utf-8 -*-
import os
import pefile

layout = os.environ.get('SFC_BUILD_LAYOUT', 'onefile')
if layout not in ('onefile', 'portable'):
    raise ValueError('SFC_BUILD_LAYOUT must be onefile or portable')


a = Analysis(
    ['launcher.py'],
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

# Prefer one consistent set of the newest collected VC runtimes. Python and
# Qt may ship different versions; all copies should satisfy the newer Qt ABI.
vc_names = {'vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140.dll',
            'msvcp140_1.dll', 'msvcp140_2.dll', 'concrt140.dll'}
def file_version(path):
    try:
        image = pefile.PE(path, fast_load=True)
        image.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_RESOURCE']])
        info = image.VS_FIXEDFILEINFO[0]
        result = (info.FileVersionMS, info.FileVersionLS)
        image.close()
        return result
    except Exception:
        return (0, 0)
vc_sources = {}
for destination, source, kind in a.binaries:
    name = os.path.basename(destination.replace('\\', '/')).lower()
    if name in vc_names:
        if name not in vc_sources or file_version(source) > file_version(vc_sources[name]):
            vc_sources[name] = source
a.binaries = [(destination, vc_sources.get(os.path.basename(destination.replace('\\', '/')).lower(), source), kind)
              for destination, source, kind in a.binaries]
root_names = {destination.lower() for destination, source, kind in a.binaries}
for name, source in vc_sources.items():
    if name not in root_names:
        a.binaries.append((name, source, 'BINARY'))
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries if layout == 'onefile' else [],
    a.datas if layout == 'onefile' else [],
    [],
    name='SensitiveFileChecker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\app.ico'],
    exclude_binaries=(layout == 'portable'),
)
if layout == 'portable':
    portable = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False,
                       name='SensitiveFileChecker-portable')
