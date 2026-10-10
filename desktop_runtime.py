"""Windows 托盘、桌面通知和应用窗口。"""
import os
import subprocess
import threading
import webbrowser
from pathlib import Path
from runtime_paths import RESOURCE_DIR


def open_window(url):
    candidates = [Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe',
                  Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'Microsoft/Edge/Application/msedge.exe']
    edge = next((p for p in candidates if p.exists()), None)
    if edge:
        subprocess.Popen([str(edge), '--app=' + url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        webbrowser.open(url)


class DesktopShell:
    def __init__(self, app, server, url):
        import pystray
        from PIL import Image
        self.app, self.server, self.url = app, server, url
        self.ready = threading.Event()
        self.icon = pystray.Icon('WXTodoReminder', Image.open(RESOURCE_DIR / 'assets/icon.png'), '待办提醒', menu=pystray.Menu(
            pystray.MenuItem('打开待办清单', lambda: open_window(self.url), default=True),
            pystray.MenuItem('暂停全部提醒', self.toggle, checked=lambda item: not self.app.snapshot()['settings']['enabled']),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('退出软件', self.quit)))

    def start(self):
        def setup(icon):
            icon.visible = True
            self.ready.set()
        threading.Thread(target=self.icon.run, kwargs={'setup':setup}, daemon=True, name='desktop-tray').start()

    def notify(self, content, title='待办提醒'):
        if not self.ready.wait(4):
            raise RuntimeError('Windows 托盘未就绪')
        if not self.icon.HAS_NOTIFICATION:
            raise RuntimeError('此系统不支持桌面通知')
        self.icon.notify(content[:220], title)

    def toggle(self, icon, item):
        self.app.save_settings({'enabled':not self.app.snapshot()['settings']['enabled']})
        icon.update_menu()

    def quit(self):
        self.app.stop.set()
        threading.Thread(target=self.server.shutdown, daemon=True).start()
        self.icon.stop()

    def stop(self):
        self.icon.stop()
