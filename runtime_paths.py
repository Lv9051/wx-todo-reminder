"""源码与Windows打包运行时的资源和数据路径。"""
import sys
import os
from pathlib import Path
VERSION = '1.4.3'
FROZEN = bool(getattr(sys, 'frozen', False))
RESOURCE_DIR = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'WXTodoReminder' if FROZEN else APP_DIR / 'data'


def default_database(portable=False, app_dir=None, data_dir=None):
    """首次升级复制便携数据库；后续始终读取固定位置，不覆盖已有清单。"""
    import sqlite3
    from contextlib import closing
    from datetime import datetime
    from urllib.parse import quote
    source_dir = Path(app_dir or APP_DIR)
    destination_dir = source_dir / 'data' if portable else Path(data_dir or DATA_DIR)
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / 'reminders.db'
    legacy = source_dir / 'data/reminders.db'
    if not destination.exists() and legacy.exists() and legacy.resolve() != destination.resolve():
        temporary = destination_dir / 'migration.tmp.db'
        backup_dir = destination_dir / 'backups'
        backup_dir.mkdir(exist_ok=True)
        with closing(sqlite3.connect('file:' + quote(legacy.resolve().as_posix(), safe='/:') + '?mode=ro', uri=True)) as src:
            with closing(sqlite3.connect(temporary)) as dst:
                src.backup(dst)
                if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('旧清单校验失败，未迁移或覆盖数据')
            import shutil
            shutil.copy2(temporary, backup_dir / ('before-migration-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.db'))
        temporary.replace(destination)
    return destination
