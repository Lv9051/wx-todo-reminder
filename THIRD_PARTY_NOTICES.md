# Third-party notices

WX Todo Reminder is licensed under Apache-2.0.

The Windows WeChat UI Automation adapter uses unmodified files from
[wechatauto-replica](https://github.com/fanyuantaier/wechatauto-replica),
commit fbfb02677f8f5c8d52be166dc2a662a836f1354d (version 1.2.5.1), Apache-2.0.
The bundled files are param.py, rhythm.py and uia_driver.py. The upstream license
is retained in vendor/wechatauto-replica/LICENSE. Database decryption modules
are not bundled or used.

Runtime dependencies and their licenses:

- Python 3.12: Python Software Foundation License.
- pywin32: Python Software Foundation License.
- uiautomation: Apache-2.0.
- comtypes: MIT.
- Pillow: MIT-CMU.
- pystray and six: LGPL-3.0 and MIT respectively.
- PyInstaller: GPL-2.0 with the bootloader exception permitting distribution of
  bundled applications under their own license.

Dependency source references:
https://github.com/mhammond/pywin32
https://github.com/yinkaisheng/Python-UIAutomation-for-Windows
https://github.com/enthought/comtypes
https://github.com/python-pillow/Pillow
https://github.com/moses-palmer/pystray
https://github.com/benjaminp/six
https://github.com/pyinstaller/pyinstaller

The release ZIP includes available dependency license texts in LICENSES/.
