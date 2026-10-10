"""源码与Windows打包运行时的资源和数据路径。"""
import sys
from pathlib import Path
VERSION = '1.0.0'
FROZEN = bool(getattr(sys, 'frozen', False))
RESOURCE_DIR = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
DATA_DIR = APP_DIR / 'data'
