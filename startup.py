"""当前用户的登录启动项；程序副本放固定目录，解压位置变化不影响启动。"""
import os
import shutil
import sys
from pathlib import Path
from runtime_paths import DATA_DIR, FROZEN

KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
NAME = 'WXTodoReminder'


def startup_enabled():
    if os.name != 'nt':
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as key:
            value = winreg.QueryValueEx(key, NAME)[0]
        return isinstance(value, str) and 'WXTodoReminder.exe' in value
    except OSError:
        return False


def set_startup(enabled, executable=None, data_dir=None, database=None):
    if type(enabled) is not bool:
        raise ValueError('启动开关无效')
    if os.name != 'nt' or (enabled and not FROZEN and executable is None):
        raise ValueError('请在打包的软件中设置开机启动')
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, KEY) as key:
        if not enabled:
            try:
                winreg.DeleteValue(key, NAME)
            except FileNotFoundError:
                pass
            return
        source = Path(executable or sys.executable).resolve()
        target = Path(data_dir or DATA_DIR) / 'program/WXTodoReminder.exe'
        target.parent.mkdir(parents=True, exist_ok=True)
        if source != target.resolve():
            try:
                shutil.copy2(source, target)
            except PermissionError:
                raise ValueError('旧的开机启动程序仍在运行，请从托盘退出后重新打开新版软件') from None
        command = '"' + str(target.resolve()) + '" --no-browser'
        if database is not None:
            command += ' --data "' + str(Path(database).resolve()) + '"'
        if len(command) > 260:
            raise ValueError('启动路径过长，请使用较短的用户数据路径')
        winreg.SetValueEx(key, NAME, 0, winreg.REG_SZ, command)
