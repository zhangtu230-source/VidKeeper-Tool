# -*- mode: python ; coding: utf-8 -*-
import os
import sys
import tkinterdnd2

tkinterdnd2_path = os.path.dirname(tkinterdnd2.__file__)
tkdnd_bin_dir = os.path.join(tkinterdnd2_path, 'tkdnd', 'win-x64')
if not os.path.isdir(tkdnd_bin_dir):
    tkdnd_bin_dir = os.path.join(tkinterdnd2_path, 'tkdnd')

# 图标文件
icon_file = os.path.join(os.path.dirname(os.path.abspath(SPEC)), 'VaultPress.ico')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        (tkdnd_bin_dir, os.path.join('tkinterdnd2', 'tkdnd', 'win-x64')),
        (icon_file, '.') if os.path.exists(icon_file) else ('', '.'),
    ],
    hiddenimports=['tkinterdnd2', 'tkinterdnd2.TkinterDnD'],
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
    name='VaultPress',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_file if os.path.exists(icon_file) else None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='VaultPress',
)
