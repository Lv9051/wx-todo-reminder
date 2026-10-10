# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_dynamic_libs
root = Path(SPECPATH)
source = root / 'vendor/wechatauto-replica'
datas = [(str(root / 'web'), 'web'), (str(root / 'assets'), 'assets'),
         (str(root / 'LICENSE'), '.'), (str(root / 'THIRD_PARTY_NOTICES.md'), '.')]
for name in ('param.py','rhythm.py','uia_driver.py'):
    datas.append((str(source / 'wechatauto' / name), 'vendor/wechatauto-replica/wechatauto'))
datas.append((str(source / 'LICENSE'), 'vendor/wechatauto-replica'))
a = Analysis([str(root / 'desktop.py')], pathex=[str(root)], binaries=collect_dynamic_libs("uiautomation"), datas=datas,
             hiddenimports=['difflib','uiautomation','comtypes','win32gui','win32con','win32api','win32process','win32clipboard','pystray._win32'],
             excludes=['numpy','cv2','onnxruntime','wx4py','tkinter'], noarchive=False, optimize=1)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='WXTodoReminder', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False,
          icon=str(root / 'assets/icon.ico'))
