"""Windows 托盘、桌面通知和应用窗口。"""
import os
import subprocess
import threading
import time
import sys
from pathlib import Path
from runtime_paths import RESOURCE_DIR


def open_window(url, data_dir=None):
    from runtime_paths import FROZEN,DATA_DIR
    command=[sys.executable] if FROZEN else [sys.executable,str(RESOURCE_DIR/'desktop.py')]
    return subprocess.Popen(command+['--desktop-window',url,str(data_dir or DATA_DIR)],
                            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW)


class DesktopShell:
    def __init__(self, app, server, url):
        import pystray
        from PIL import Image
        self.app, self.server, self.url = app, server, url
        self.ready = threading.Event()
        self._sound_lock = threading.Lock()
        self._last_sound = float('-inf')
        self.window_process=None
        self.window_lock=threading.Lock()
        from floating_window import FloatingTodo
        self.widget=FloatingTodo(app,self.open)
        self.app.widget=self.widget
        self.icon = pystray.Icon('WXTodoReminder', Image.open(RESOURCE_DIR / 'assets/icon.png'), '强芯待办', menu=pystray.Menu(
            pystray.MenuItem('打开待办清单', lambda: self.open(), default=True),
            pystray.MenuItem('暂停全部提醒', self.toggle, checked=lambda item: not self.app.snapshot()['settings']['enabled']),
            pystray.MenuItem('桌面悬浮清单',self.toggle_widget,checked=lambda item:self.app.snapshot()['settings']['widget_enabled']),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('退出软件', self.quit)))

    def start(self):
        self.widget.start()
        def setup(icon):
            icon.visible = True
            self.ready.set()
        threading.Thread(target=self.icon.run, kwargs={'setup':setup}, daemon=True, name='desktop-tray').start()

    def open(self):
        with self.window_lock:
            if self.window_process is not None and self.window_process.poll() is None:
                import win32gui,win32process,win32con
                handles=[]
                win32gui.EnumWindows(lambda hwnd,_:handles.append(hwnd) if win32process.GetWindowThreadProcessId(hwnd)[1]==self.window_process.pid and win32gui.GetWindowText(hwnd)=='强芯待办' else None,None)
                if handles:
                    win32gui.ShowWindow(handles[0],win32con.SW_RESTORE)
                    try:win32gui.SetForegroundWindow(handles[0])
                    except Exception:pass
                return
            self.window_process=open_window(self.url,self.app.path.parent)

    def notify(self, content, title='待办提醒'):
        if not self.ready.wait(4):
            raise RuntimeError('Windows 托盘未就绪')
        if not self.icon.HAS_NOTIFICATION:
            raise RuntimeError('此系统不支持桌面通知')
        # 关闭本条通知的系统声音，避免和柔和铃声叠加；不修改系统全局设置。
        from pystray._util import win32
        self.icon._message(win32.NIM_MODIFY, win32.NIF_INFO,
                           szInfo=content[:220], szInfoTitle=title[:48], dwInfoFlags=0x10)
        with self.app.lock, self.app.connect() as db:
            sound = self.app.config(db).get('sound_enabled', True)
        if sound:
            self.play_sound()

    def play_sound(self):
        import logging
        import winsound
        with self._sound_lock:
            stamp = time.monotonic()
            if stamp - self._last_sound < 3:
                return
            try:
                winsound.PlaySound(str(RESOURCE_DIR / 'assets/reminder-soft.wav'),
                                   winsound.SND_FILENAME | winsound.SND_ASYNC |
                                   winsound.SND_NODEFAULT | winsound.SND_NOSTOP)
                self._last_sound = stamp
            except (OSError, RuntimeError):
                # 声卡忙时保持已提交的通知结果，不因此重复提醒。
                logging.getLogger(__name__).warning('提示音未播放；桌面通知已提交')

    def toggle(self, icon, item):
        self.app.save_settings({'enabled':not self.app.snapshot()['settings']['enabled']})
        icon.update_menu()

    def quit(self):
        self.app.stop.set()
        self.widget.stop()
        self.close_window()
        threading.Thread(target=self.server.shutdown, daemon=True).start()
        self.icon.stop()

    def stop(self):
        self.widget.stop()
        self.icon.stop()
        self.close_window()

    def close_window(self):
        if self.window_process is not None and self.window_process.poll() is None:
            self.window_process.terminate()

    def toggle_widget(self,icon,item):
        self.app.save_settings({'widget_enabled':not self.app.snapshot()['settings']['widget_enabled']})
        icon.update_menu()
