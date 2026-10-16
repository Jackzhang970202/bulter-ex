# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = [('D:/ccworkcreat/ai教程/butler/butler/web', 'web')]
binaries = [('D:/conda/envs/agent-framework/Library/bin/libssl-3-x64.dll', '.'), ('D:/conda/envs/agent-framework/Library/bin/libcrypto-3-x64.dll', '.')]
hiddenimports = []
hiddenimports += collect_submodules('uvicorn')
tmp_ret = collect_all('anyio')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['D:/ccworkcreat/ai教程/butler/butler_build.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy', 'IPython', 'jedi', 'parso', 'prompt_toolkit', 'zmq', 'tornado', 'tkinter', '_tkinter', 'pytest', 'py', 'watchfiles'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='butler',
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
    name='butler',
)
