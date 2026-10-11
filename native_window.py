"""独立 WebView2 桌面窗口；关闭后调度与托盘继续运行。"""
import ctypes
import os
from pathlib import Path
from urllib.parse import urlsplit
from runtime_paths import RESOURCE_DIR


def run(url, data_dir):
    parsed=urlsplit(url)
    if parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or parsed.path not in ('','/'):
        raise ValueError('只允许打开本机待办窗口')
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('QiangxinTodo.Desktop')
    import webview
    window=webview.create_window('强芯待办',url,width=1180,height=800,min_size=(760,540),background_color='#f7f8f4')
    # 只访问本地 HTTP API，不将 Python 类作为网页可调用接口暴露。
    webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER']=True
    webview.settings['ALLOW_DOWNLOADS']=True
    webview.start(gui='edgechromium',icon=str(RESOURCE_DIR/'assets/icon.ico'),
                  private_mode=False,storage_path=str(Path(data_dir)/'webview'))


if __name__=='__main__':
    import sys
    run(sys.argv[1],sys.argv[2])
