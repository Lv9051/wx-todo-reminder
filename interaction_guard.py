"""仅检测是否有人操作，不记录按键内容；微信实际发送前等待空闲。"""
import os
import ctypes
import threading
from ctypes import wintypes


def waiting_reason(idle_seconds=1, reject_wechat=False):
    if os.name != 'nt':
        return '微信发送需要 Windows 桌面'
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.OpenInputDesktop.restype = wintypes.HANDLE
    user.OpenInputDesktop.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    user.CloseDesktop.argtypes = (wintypes.HANDLE,)
    desktop = user.OpenInputDesktop(0, False, 0x0100)
    if not desktop:
        return '等待桌面解锁'
    user.CloseDesktop(desktop)
    if idle_seconds<=0 and not reject_wechat:
        return ''
    class LastInput(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.UINT), ('dwTime', wintypes.DWORD)]
    last = LastInput(ctypes.sizeof(LastInput), 0)
    if not user.GetLastInputInfo(ctypes.byref(last)):
        return '无法核对桌面空闲状态，等待重试'
    elapsed = (ctypes.windll.kernel32.GetTickCount() - last.dwTime) & 0xffffffff
    if elapsed < idle_seconds * 1000:
        return '等待键鼠空闲 ' + str(idle_seconds) + ' 秒'
    if reject_wechat:
        import win32gui, win32process
        import win32api, win32con
        hwnd = win32gui.GetForegroundWindow()
        if hwnd:
            try:
                pid = win32process.GetWindowThreadProcessId(hwnd)[1]
                handle = win32api.OpenProcess(0x1000, False, pid)
                try:
                    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                    kernel.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD))
                    buffer = ctypes.create_unicode_buffer(32768)
                    size = wintypes.DWORD(len(buffer))
                    if not kernel.QueryFullProcessImageNameW(int(handle),0,buffer,ctypes.byref(size)):
                        raise OSError(ctypes.get_last_error())
                    name = buffer.value.rsplit('\\',1)[-1].lower()
                finally:
                    handle.Close()
                if name in ('weixin.exe', 'wechat.exe'):
                    return '微信正在前台，离开微信后自动发送'
            except Exception:
                return '无法核对前台窗口，等待重试'
    return ''


class InputWatch:
    """短期低级钩子，只保存“有人操作”标记，忽略程序注入的键鼠事件。"""
    def __init__(self):
        self.changed = threading.Event()
        self.ready = threading.Event()
        self.failed = False
        self.thread_id = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()
        return self.ready.wait(3) and not self.failed

    def _run(self):
        user = ctypes.WinDLL('user32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        result_type = ctypes.c_ssize_t
        callback_type = ctypes.WINFUNCTYPE(result_type, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        user.SetWindowsHookExW.argtypes = (ctypes.c_int, callback_type, wintypes.HINSTANCE, wintypes.DWORD)
        user.SetWindowsHookExW.restype = wintypes.HANDLE
        user.CallNextHookEx.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        user.CallNextHookEx.restype = result_type
        user.UnhookWindowsHookEx.argtypes = (wintypes.HANDLE,)
        kernel.GetModuleHandleW.restype = wintypes.HMODULE
        class Key(ctypes.Structure):
            _fields_ = [('code', wintypes.DWORD), ('scan', wintypes.DWORD), ('flags', wintypes.DWORD), ('time', wintypes.DWORD), ('extra', ctypes.c_size_t)]
        class Mouse(ctypes.Structure):
            _fields_ = [('point', wintypes.POINT), ('data', wintypes.DWORD), ('flags', wintypes.DWORD), ('time', wintypes.DWORD), ('extra', ctypes.c_size_t)]
        def callback(code, wparam, lparam, kind):
            if code >= 0:
                record = ctypes.cast(lparam, ctypes.POINTER(Key if kind == 13 else Mouse)).contents
                if not record.flags & (0x10 if kind == 13 else 1):
                    self.changed.set()
            return user.CallNextHookEx(None, code, wparam, lparam)
        keyboard = callback_type(lambda c, w, l: callback(c, w, l, 13))
        mouse = callback_type(lambda c, w, l: callback(c, w, l, 14))
        self.thread_id = kernel.GetCurrentThreadId()
        module = kernel.GetModuleHandleW(None)
        hooks = [user.SetWindowsHookExW(13, keyboard, module, 0), user.SetWindowsHookExW(14, mouse, module, 0)]
        self.failed = not all(hooks)
        self.ready.set()
        try:
            msg = wintypes.MSG()
            while not self.failed and user.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user.TranslateMessage(ctypes.byref(msg))
                user.DispatchMessageW(ctypes.byref(msg))
        finally:
            for hook in hooks:
                if hook:
                    user.UnhookWindowsHookEx(hook)

    def stop(self):
        if self.thread_id:
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
            self.thread.join(2)
