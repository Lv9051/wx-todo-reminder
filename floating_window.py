"""轻量 Win32 悬浮清单；悬停不激活窗口，数据直接来自同一份待办库。"""
import ctypes
import logging
import threading
import time
from ctypes import wintypes


def clamp_position(position, area, width, height):
    left,top,right,bottom=area
    x,y=position
    return max(left,min(x,right-width)),max(top,min(y,bottom-height))


def unfinished(tasks):
    return [t for t in tasks if t['status'] not in ('done','deleted')]


class FloatingTodo:
    def __init__(self,app,open_list):
        self.app,self.open_list=app,open_list
        self.hwnd=None
        self.ready=threading.Event()
        self.thread=threading.Thread(target=self._run,daemon=True,name='floating-todo')
        self.tasks=[]
        self.expanded=True
        self.error=None
        self.reset_requested=False
        self.scope='mine'
        self.visible=False
        self.offset=0
        self.anchor=(20,120)
        self.drag=None
        self.dragged=False
        self.outside_since=None
        self.last_poll=0
        self.signature=None

    def start(self):
        self.thread.start()

    def stop(self):
        if self.hwnd:
            import win32gui,win32con
            win32gui.PostMessage(self.hwnd,win32con.WM_CLOSE,0,0)

    def _run(self):
        try:
            self._create()
        except Exception as exc:
            self.error=str(exc)
            logging.getLogger(__name__).exception('桌面小窗未启动')
            self.ready.set()

    def reset_position(self):
        self.reset_requested=True
        self.app.save_settings(dict(widget_enabled=True))

    def _create(self):
        import win32gui as gui,win32api as api,win32con as con
        self.gui,self.api,self.con=gui,api,con
        self.user=ctypes.WinDLL('user32',use_last_error=True)
        self.user.SetTimer.argtypes=(wintypes.HWND,ctypes.c_size_t,wintypes.UINT,ctypes.c_void_p)
        self.user.SetTimer.restype=ctypes.c_size_t
        self.user.KillTimer.argtypes=(wintypes.HWND,ctypes.c_size_t)
        font=gui.LOGFONT()
        font.lfFaceName='Microsoft YaHei UI'
        font.lfHeight=-14
        font.lfWeight=400
        self.font=gui.CreateFontIndirect(font)
        font.lfHeight=-12
        self.small_font=gui.CreateFontIndirect(font)
        self.background=gui.CreateSolidBrush(api.RGB(250,252,248))
        self.line=gui.CreateSolidBrush(api.RGB(228,235,225))
        name='WXTodoFloating-'+str(api.GetCurrentProcessId())
        instance=api.GetModuleHandle(None)
        cls=gui.WNDCLASS()
        cls.hInstance=instance
        cls.lpszClassName=name
        cls.lpfnWndProc=self._proc
        cls.hCursor=gui.LoadCursor(None,con.IDC_ARROW)
        cls.hbrBackground=self.background
        try:
            gui.RegisterClass(cls)
            self.hwnd=gui.CreateWindowEx(con.WS_EX_TOPMOST|con.WS_EX_TOOLWINDOW|con.WS_EX_NOACTIVATE,name,'强芯待办 · 桌面清单',con.WS_POPUP|con.WS_BORDER,20,120,350,176,0,0,instance,None)
            self._poll(initial=True)
            if not self.user.SetTimer(self.hwnd,1,150,None):
                raise OSError('悬浮窗刷新计时器未启动')
            self.ready.set()
            gui.PumpMessages()
        except Exception as exc:
            self.error=str(exc)
            logging.getLogger(__name__).exception('悬浮清单未启动；主清单仍可使用')
            self.ready.set()
        finally:
            if self.hwnd and gui.IsWindow(self.hwnd):
                gui.DestroyWindow(self.hwnd)
            self.hwnd=None
            for handle in (self.font,self.small_font,self.background,self.line):
                gui.DeleteObject(handle)
            try:gui.UnregisterClass(name,instance)
            except Exception:pass

    def _area(self):
        monitor=self.api.MonitorFromPoint(self.anchor,self.con.MONITOR_DEFAULTTONEAREST)
        return self.api.GetMonitorInfo(monitor)['Work']

    def _size(self):
        return (350,96+40*max(2,min(8,len(self.tasks)))) if self.expanded else (124,38)

    def _layout(self):
        width,height=self._size()
        position=self.anchor
        x,y=clamp_position(position,self._area(),width,height)
        self.gui.SetWindowPos(self.hwnd,self.con.HWND_TOPMOST,x,y,width,height,self.con.SWP_NOACTIVATE)
        self.gui.InvalidateRect(self.hwnd,None,True)

    def _poll(self,initial=False):
        with self.app.lock,self.app.connect() as db:
            cfg=self.app.config(db)
            self.scope=cfg.get('widget_scope','mine')
            tasks=[dict(row) for row in db.execute("SELECT id,title,status,target_type FROM tasks WHERE status NOT IN ('done','deleted') AND (?='all' OR owner_scope='mine') ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END,id DESC",(self.scope,))]
        if initial:
            self.anchor=tuple(cfg.get('widget_position') or (self._area()[2]-374,120))
        if self.reset_requested:
            area=self._area()
            self.anchor=(area[0]+(area[2]-area[0]-350)//2,area[1]+80)
            self.expanded=True
            self.reset_requested=False
            self.app.save_settings(dict(widget_position=list(self.anchor)))
        self.anchor=clamp_position(self.anchor,self._area(),*self._size())
        signature=(cfg['widget_enabled'],self.scope, self.anchor,[(t['id'],t['title'],t['status']) for t in tasks])
        self.tasks=tasks
        self.offset=max(0,min(self.offset,max(0,len(tasks)-8)))
        if cfg['widget_enabled']!=self.visible:
            self.visible=cfg['widget_enabled']
            self.expanded=True
            self.gui.ShowWindow(self.hwnd,self.con.SW_SHOWNOACTIVATE if self.visible else self.con.SW_HIDE)
        if signature!=self.signature or initial:
            self.signature=signature
            self._layout()
        self.last_poll=time.monotonic()

    def _text(self,dc,text,rect,small=False,color=None):
        old=self.gui.SelectObject(dc,self.small_font if small else self.font)
        self.gui.SetTextColor(dc,color or self.api.RGB(35,83,61))
        self.gui.DrawText(dc,text,-1,rect,self.con.DT_LEFT|self.con.DT_VCENTER|self.con.DT_SINGLELINE|self.con.DT_END_ELLIPSIS|self.con.DT_NOPREFIX)
        self.gui.SelectObject(dc,old)

    def _paint(self,hwnd):
        dc,paint=self.gui.BeginPaint(hwnd)
        try:
            rect=self.gui.GetClientRect(hwnd)
            self.gui.FillRect(dc,rect,self.background)
            self.gui.SetBkMode(dc,self.con.TRANSPARENT)
            if not self.expanded:
                self._text(dc,'✓ 待办 '+str(len(self.tasks)),(12,0,120,36))
                return
            self._text(dc,f'待办 · {len(self.tasks)} 项',(16,4,205,40))
            self._text(dc,'我的' if self.scope=='mine' else '全部',(211,4,261,40),small=True)
            self._text(dc,'−',(279,4,308,40))
            self._text(dc,'×',(320,4,345,40))
            self.gui.FillRect(dc,(14,43,334,44),self.line)
            for i,task in enumerate(self.tasks[self.offset:self.offset+8]):
                y=50+i*40
                self._text(dc,'□',(17,y,37,y+34))
                self._text(dc,task['title'],(43,y,329,y+34),color=self.api.RGB(126,139,127) if task['status']=='paused' else None)
            if not self.tasks:
                self._text(dc,'清单已清空，轻松一点。',(16,44,330,75),small=True)
            height=rect[3]
            self.gui.FillRect(dc,(14,height-43,334,height-42),self.line)
            self._text(dc,'打开清单 →',(16,height-39,150,height-4),small=True)
            hint='拖动标题可移动' if len(self.tasks)<=8 else f'{self.offset+1}–{min(self.offset+8,len(self.tasks))} / {len(self.tasks)} · 滚轮翻看'
            self._text(dc,hint,(155,height-39,334,height-4),small=True)
        finally:self.gui.EndPaint(hwnd,paint)

    def _hide(self):
        self.app.save_settings(dict(widget_enabled=False))
        self._poll()

    def _proc(self,hwnd,message,wparam,lparam):
        try:
            return self._handle(hwnd,message,wparam,lparam)
        except Exception:
            logging.getLogger(__name__).exception('悬浮窗操作失败')
            return self.gui.DefWindowProc(hwnd,message,wparam,lparam)

    def _handle(self,hwnd,message,wparam,lparam):
        g,c=self.gui,self.con
        if message==c.WM_PAINT:
            self._paint(hwnd)
            return 0
        if message==c.WM_MOUSEACTIVATE:
            return c.MA_NOACTIVATE
        if message==c.WM_ERASEBKGND:return 1
        if message==c.WM_TIMER:
            if self.app.stop.is_set():
                g.DestroyWindow(hwnd)
                return 0
            stamp=time.monotonic()
            if stamp-self.last_poll>=1:self._poll()
            return 0
        if message==c.WM_MOUSEMOVE:
            if self.drag:
                point,anchor=self.drag
                mouse=g.GetCursorPos()
                dx,dy=mouse[0]-point[0],mouse[1]-point[1]
                self.dragged=self.dragged or abs(dx)+abs(dy)>3
                self.anchor=clamp_position((anchor[0]+dx,anchor[1]+dy),self._area(),*self._size())
                self._layout()
            return 0
        if message==c.WM_LBUTTONDOWN:
            x,y=lparam&0xffff,(lparam>>16)&0xffff
            if self.expanded and x>=315 and y<43:self._hide()
            elif self.expanded and 275<=x<315 and y<43:self.expanded=False;self._layout()
            elif self.expanded and 205<=x<275 and y<43:
                self.app.save_settings(dict(widget_scope='all' if self.scope=='mine' else 'mine'));self.offset=0;self._poll()
            elif not self.expanded and y<43:
                self.expanded=True;self._layout()
            elif y<43:
                self.drag=(g.GetCursorPos(),self.anchor);self.dragged=False;g.SetCapture(hwnd)
            elif self.expanded:
                height=self._size()[1]
                if y>=height-43:self.open_list()
                elif y>=50:
                    index=self.offset+(y-50)//40
                    if index<len(self.tasks):
                        if x<40:
                            self.app.update(self.tasks[index]['id'],dict(action='complete'));self._poll()
                        else:self.open_list()
            return 0
        if message==c.WM_LBUTTONUP and self.drag:
            g.ReleaseCapture()
            self.drag=None
            if self.dragged:self.app.save_settings(dict(widget_position=list(self.anchor)))
            return 0
        if message==c.WM_CAPTURECHANGED:
            self.drag=None
            return 0
        if message==c.WM_MOUSEWHEEL and self.expanded:
            delta=ctypes.c_short(wparam>>16).value
            self.offset=max(0,min(max(0,len(self.tasks)-8),self.offset+(-1 if delta>0 else 1)))
            g.InvalidateRect(hwnd,None,True)
            return 0
        if message==c.WM_RBUTTONUP:
            self._hide()
            return 0
        if message==c.WM_CLOSE:
            g.DestroyWindow(hwnd)
            return 0
        if message==c.WM_DESTROY:
            self.user.KillTimer(hwnd,1)
            g.PostQuitMessage(0)
            return 0
        return g.DefWindowProc(hwnd,message,wparam,lparam)
