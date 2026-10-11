"""不激活的原生桌面面板：固定尺寸、像素滚动和持久位置。"""
import ctypes
import logging
import os
import threading
import time
from ctypes import wintypes
from pathlib import Path

PADDING=18
PANEL_WIDTH=336
PANEL_HEIGHT=380
LIST_TOP=73
LIST_BOTTOM=331

def clamp_position(position, area, width, height):
    left,top,right,bottom=area
    x,y=position
    return max(left,min(x,right-width)),max(top,min(y,bottom-height))

def unfinished(tasks):
    return [t for t in tasks if t['status'] not in ('done','deleted')]

def scrollbar_geometry(content_height, offset, viewport=LIST_BOTTOM-LIST_TOP):
    maximum=max(0,content_height-viewport)
    length=viewport if not maximum else max(28,round(viewport*viewport/content_height))
    top=LIST_TOP+round((viewport-length)*min(max(offset,0),maximum)/maximum) if maximum else LIST_TOP
    return maximum,top,length

class FloatingTodo:
    def __init__(self,app,open_list):
        self.app,self.open_list=app,open_list
        self.hwnd=None;self.ready=threading.Event()
        self.thread=threading.Thread(target=self._run,daemon=True,name='floating-todo')
        self.tasks=[];self.rows=[];self.content_height=0
        self.expanded=True;self.error=None;self.reset_requested=False;self.scope='mine';self.visible=False
        self.offset=0;self.anchor=(20,120);self.drag=None;self.thumb_drag=None;self.dragged=False
        self.menu=False;self.hover=None;self.last_poll=0;self.signature=None;self.fonts={}

    def start(self):self.thread.start()

    def stop(self):
        if self.hwnd:
            import win32gui,win32con
            win32gui.PostMessage(self.hwnd,win32con.WM_CLOSE,0,0)

    def _run(self):
        try:self._create()
        except Exception as exc:
            self.error=str(exc);logging.getLogger(__name__).exception('桌面小窗未启动');self.ready.set()

    def reset_position(self):
        self.reset_requested=True;self.app.save_settings(dict(widget_enabled=True))

    def _create(self):
        import win32gui as gui,win32api as api,win32con as con
        self.gui,self.api,self.con=gui,api,con
        self.user=ctypes.WinDLL('user32',use_last_error=True)
        self.user.SetTimer.argtypes=(wintypes.HWND,ctypes.c_size_t,wintypes.UINT,ctypes.c_void_p)
        self.user.SetTimer.restype=ctypes.c_size_t;self.user.KillTimer.argtypes=(wintypes.HWND,ctypes.c_size_t)
        self.gdi=ctypes.WinDLL('gdi32',use_last_error=True)
        self.gdi.CreateDIBSection.argtypes=(wintypes.HDC,ctypes.c_void_p,wintypes.UINT,ctypes.POINTER(ctypes.c_void_p),wintypes.HANDLE,wintypes.DWORD)
        self.gdi.CreateDIBSection.restype=wintypes.HANDLE
        name='WXTodoFloating-'+str(api.GetCurrentProcessId());instance=api.GetModuleHandle(None)
        cls=gui.WNDCLASS();cls.hInstance=instance;cls.lpszClassName=name;cls.lpfnWndProc=self._proc
        cls.hCursor=gui.LoadCursor(None,con.IDC_ARROW)
        try:
            gui.RegisterClass(cls)
            self.hwnd=gui.CreateWindowEx(con.WS_EX_TOPMOST|con.WS_EX_TOOLWINDOW|con.WS_EX_NOACTIVATE|con.WS_EX_LAYERED,
                name,'强芯待办 · 桌面清单',con.WS_POPUP,20,120,*self._size(),0,0,instance,None)
            self._poll(initial=True)
            if not self.user.SetTimer(self.hwnd,1,150,None):raise OSError('小窗刷新计时器未启动')
            self.ready.set();gui.PumpMessages()
        finally:
            if self.hwnd and gui.IsWindow(self.hwnd):gui.DestroyWindow(self.hwnd)
            self.hwnd=None
            try:gui.UnregisterClass(name,instance)
            except Exception:pass

    def _area(self):
        monitor=self.api.MonitorFromPoint(self.anchor,self.con.MONITOR_DEFAULTTONEAREST)
        return self.api.GetMonitorInfo(monitor)['Work']

    def _size(self):
        return (PANEL_WIDTH+2*PADDING,PANEL_HEIGHT+2*PADDING) if self.expanded else (178+2*PADDING,50+2*PADDING)

    def _layout(self):
        self.anchor=clamp_position(self.anchor,self._area(),*self._size())
        self.gui.SetWindowPos(self.hwnd,self.con.HWND_TOPMOST,*self.anchor,*self._size(),self.con.SWP_NOACTIVATE)
        self._paint()

    def _poll(self,initial=False):
        with self.app.lock,self.app.connect() as db:
            cfg=self.app.config(db);scope=cfg.get('widget_scope','mine')
            tasks=[dict(row) for row in db.execute("SELECT id,title,status,target_type,next_due,reminder_finished FROM tasks WHERE status NOT IN ('done','deleted') AND (?='all' OR owner_scope='mine') ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END,id DESC",(scope,))]
        if initial:self.anchor=tuple(cfg.get('widget_position') or (self._area()[2]-self._size()[0]-10,102))
        if scope!=self.scope:self.offset=0
        self.scope=scope
        if self.reset_requested:
            area=self._area();self.expanded=True
            self.anchor=(area[0]+(area[2]-area[0]-self._size()[0])//2,area[1]+65)
            self.reset_requested=False;self.app.save_settings(dict(widget_position=list(self.anchor)))
        signature=(cfg['widget_enabled'],self.scope,self.anchor,tasks)
        if signature!=self.signature or initial:
            self.tasks=tasks;self._prepare_rows();self.offset=min(self.offset,scrollbar_geometry(self.content_height,self.offset)[0])
            if cfg['widget_enabled']!=self.visible:
                self.visible=cfg['widget_enabled'];self.expanded=True
                self.gui.ShowWindow(self.hwnd,self.con.SW_SHOWNOACTIVATE if self.visible else self.con.SW_HIDE)
            self.signature=signature;self._layout()
        self.last_poll=time.monotonic()

    def _font(self,size,bold=False):
        from PIL import ImageFont
        key=(size,bold)
        if key not in self.fonts:
            directory=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'
            choices=('msyhbd.ttc','msyh.ttc','segoeui.ttf') if bold else ('msyh.ttc','segoeui.ttf')
            font=next((directory/n for n in choices if (directory/n).exists()),None)
            self.fonts[key]=ImageFont.truetype(str(font),size*2) if font else ImageFont.load_default(size=size*2)
        return self.fonts[key]

    def _lines(self,text,width=245):
        font=self._font(13);lines=[];line=''
        for char in text:
            if char=='\n' or font.getlength(line+char)>width*2:
                lines.append(line);line='' if char=='\n' else char
                if len(lines)==2:break
            else:line+=char
        else:
            if line or not lines:lines.append(line)
            return lines
        last=lines[-1]
        while last and font.getlength(last+'…')>width*2:last=last[:-1]
        lines[-1]=last+'…';return lines

    def _prepare_rows(self):
        from app import parse_time,now
        rows=[];top=0
        for task in self.tasks:
            meta=''
            if task['target_type']!='local':
                if task['status']=='paused':meta='提醒已暂停'
                elif task['reminder_finished']:meta='已提醒，等待完成'
                else:
                    due=parse_time(task['next_due']);days=(due.date()-now().date()).days
                    prefix='今天 ' if days==0 else '明天 ' if days==1 else due.strftime('%m/%d ')
                    meta=prefix+due.strftime('%H:%M')+(' 提醒自己' if task['target_type']=='self' else ' 微信提醒')
            lines=self._lines(task['title']);height=max(51,20+len(lines)*21+(17 if meta else 0))
            rows.append(dict(task=task,top=top,height=height,lines=lines,meta=meta));top+=height
        self.rows=rows;self.content_height=top

    def _image(self):
        from PIL import Image,ImageDraw,ImageFilter
        width,height=self._size();s=2;image=Image.new('RGBA',(width*s,height*s))
        shadow=Image.new('RGBA',image.size);d=ImageDraw.Draw(shadow)
        pw,ph=(PANEL_WIDTH,PANEL_HEIGHT) if self.expanded else (178,50)
        d.rounded_rectangle(tuple(n*s for n in (PADDING,PADDING+4,PADDING+pw,PADDING+ph+4)),radius=16*s,fill=(35,67,45,42))
        image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(8*s)))
        panel=Image.new('RGBA',(pw*s,ph*s));d=ImageDraw.Draw(panel)
        def rect(bounds,fill,radius=0,outline=None):
            d.rounded_rectangle(tuple(int(n*s) for n in bounds),radius=int(radius*s),fill=fill,outline=outline,width=s)
        def text(x,y,value,size=13,color='#2b4035',bold=False):
            d.text((x*s,y*s),value,font=self._font(size,bold),fill=color,anchor='lt')
        rect((0,0,pw-1,ph-1),'#fbfdfb',16,'#e5ede5')
        y=19 if self.expanded else 10;rect((18,y,48,y+30),'#247451',9)
        d.line([(26*s,(y+16)*s),(32*s,(y+23)*s),(41*s,(y+8)*s)],fill='white',width=3*s,joint='curve')
        text(59,18 if self.expanded else 17,'我的待办' if self.scope=='mine' else '全部待办',15,bold=True)
        if not self.expanded:
            image.alpha_composite(panel,(PADDING*s,PADDING*s));return image.resize((width,height),Image.Resampling.LANCZOS)
        text(59,42,'拖动这里移动',10,'#93a396')
        for key,x in (('more',269),('close',300)):
            if self.hover==key:rect((x-4,20,x+24,48),'#eaf2e8',7)
        for x in (272,279,286):d.ellipse((x*s,33*s,(x+3)*s,36*s),fill='#7e9385')
        text(301,24,'×',18,'#7e9385')
        d.line((18*s,69*s,318*s,69*s),fill='#e7eee6',width=s)
        viewport=Image.new('RGBA',(PANEL_WIDTH*s,(LIST_BOTTOM-LIST_TOP)*s),'#fbfdfb');ld=ImageDraw.Draw(viewport)
        for row in self.rows:
            top=round(row['top']-self.offset)
            if top+row['height']<0 or top>=LIST_BOTTOM-LIST_TOP:continue
            if self.hover==row['task']['id']:ld.rounded_rectangle((13*s,top*s,315*s,(top+row['height']-2)*s),radius=9*s,fill='#edf5ed')
            cy=top+(row['height']-17)//2
            ld.rounded_rectangle((23*s,cy*s,40*s,(cy+17)*s),radius=5*s,outline='#b7cdbd',width=s)
            for i,line in enumerate(row['lines']):ld.text((52*s,(top+10+i*21)*s),line,font=self._font(13),fill='#8c9d91' if row['task']['status']=='paused' else '#2b4035',anchor='lt')
            if row['meta']:ld.text((52*s,(top+12+len(row['lines'])*21)*s),row['meta'],font=self._font(10),fill='#71907b',anchor='lt')
            ld.line((52*s,(top+row['height']-1)*s,307*s,(top+row['height']-1)*s),fill='#edf2eb',width=s)
        if not self.rows:ld.text((89*s,86*s),'事情做完了，歇一会。',font=self._font(12),fill='#90a494',anchor='lt')
        panel.alpha_composite(viewport,(0,LIST_TOP*s));d=ImageDraw.Draw(panel)
        maximum,top,length=scrollbar_geometry(self.content_height,self.offset)
        if maximum:
            rect((324,LIST_TOP,328,LIST_BOTTOM),'#edf2eb',2);rect((324,top,328,top+length),'#99b49e' if self.thumb_drag else '#bdcebe',2)
        d.line((18*s,337*s,318*s,337*s),fill='#e7eee6',width=s)
        text(19,352,'打开完整清单 ↗',11,'#427c55');text(236,353,'向下滚动' if maximum else '慢慢完成',10,'#9eac9f')
        if self.menu:
            rect((178,48,321,175),'#dce5dc',10);rect((177,46,320,173),'white',10,'#e0e9df')
            for i,(label,chosen) in enumerate((('我的待办',self.scope=='mine'),('全部待办',self.scope=='all'),('收起小窗',False))):text(190,59+i*37,('✓ ' if chosen else '   ')+label,12,'#247451' if chosen else '#657b6a')
        image.alpha_composite(panel,(PADDING*s,PADDING*s));return image.resize((width,height),Image.Resampling.LANCZOS)

    def _paint(self):
        if not self.hwnd:return
        image=self._image();bits=image.convert('RGBa').tobytes('raw','BGRa');width,height=image.size
        class BitmapInfo(ctypes.Structure):
            _fields_=[('size',wintypes.DWORD),('width',wintypes.LONG),('height',wintypes.LONG),('planes',wintypes.WORD),('bpp',wintypes.WORD),('compression',wintypes.DWORD),('bytes',wintypes.DWORD),('x',wintypes.LONG),('y',wintypes.LONG),('used',wintypes.DWORD),('important',wintypes.DWORD)]
        info=BitmapInfo(40,width,-height,1,32,0,len(bits),0,0,0,0)
        dc=self.gui.CreateCompatibleDC(0);address=ctypes.c_void_p()
        bitmap=self.gdi.CreateDIBSection(dc,ctypes.byref(info),0,ctypes.byref(address),None,0)
        if not bitmap:self.gui.DeleteDC(dc);raise ctypes.WinError(ctypes.get_last_error())
        old=self.gui.SelectObject(dc,bitmap)
        try:
            ctypes.memmove(address,bits,len(bits));self.gui.UpdateLayeredWindow(self.hwnd,0,self.anchor,(width,height),dc,(0,0),0,(0,0,255,1),2)
        finally:self.gui.SelectObject(dc,old);self.gui.DeleteObject(bitmap);self.gui.DeleteDC(dc)

    def _hide(self):
        self.app.save_settings(dict(widget_enabled=False));self.menu=False;self._poll()

    def _hit(self,x,y):
        if not self.expanded:return 'collapsed'
        if y<69:return 'close' if x>=296 else 'more' if x>=264 else 'header'
        if y>=337:return 'footer'
        if x>=319 and scrollbar_geometry(self.content_height,self.offset)[0]:return 'scroll'
        for row in self.rows:
            if row['top']<=y-LIST_TOP+self.offset<row['top']+row['height']:return row['task']['id']

    def _proc(self,hwnd,message,wparam,lparam):
        try:return self._handle(hwnd,message,wparam,lparam)
        except Exception:
            logging.getLogger(__name__).exception('小窗操作失败');return self.gui.DefWindowProc(hwnd,message,wparam,lparam)

    def _handle(self,hwnd,message,wparam,lparam):
        g,c=self.gui,self.con
        if message==c.WM_PAINT:
            _,paint=g.BeginPaint(hwnd);g.EndPaint(hwnd,paint);return 0
        if message==c.WM_MOUSEACTIVATE:return c.MA_NOACTIVATE
        if message==c.WM_ERASEBKGND:return 1
        if message==c.WM_TIMER:
            if self.app.stop.is_set():g.DestroyWindow(hwnd);return 0
            if time.monotonic()-self.last_poll>=1:self._poll()
            if self.hover is not None:
                px,py=g.ScreenToClient(hwnd,g.GetCursorPos())
                if not (PADDING<=px<self._size()[0]-PADDING and PADDING<=py<self._size()[1]-PADDING):self.hover=None;self._paint()
            return 0
        x,y=ctypes.c_short(lparam&0xffff).value-PADDING,ctypes.c_short((lparam>>16)&0xffff).value-PADDING
        if message==c.WM_MOUSEMOVE:
            if self.drag:
                point,anchor=self.drag;mouse=g.GetCursorPos();dx,dy=mouse[0]-point[0],mouse[1]-point[1];self.dragged=self.dragged or abs(dx)+abs(dy)>3
                self.anchor=clamp_position((anchor[0]+dx,anchor[1]+dy),self._area(),*self._size());self._layout()
            elif self.thumb_drag is not None:
                maximum,_,length=scrollbar_geometry(self.content_height,self.offset)
                self.offset=round(maximum*max(0,min(y-self.thumb_drag-LIST_TOP,LIST_BOTTOM-LIST_TOP-length))/max(1,LIST_BOTTOM-LIST_TOP-length));self._paint()
            else:
                hit=self._hit(x,y)
                if hit!=self.hover:self.hover=hit;self._paint()
            return 0
        if message==c.WM_LBUTTONDOWN:
            if not (0<=x<self._size()[0]-2*PADDING and 0<=y<self._size()[1]-2*PADDING):return 0
            if self.menu:
                self.menu=False
                if 177<=x<=320 and 46<=y<173:
                    index=(y-46)//37
                    if index<2:self.app.save_settings(dict(widget_scope='mine' if index==0 else 'all'));self.offset=0;self._poll()
                    else:self.expanded=False;self._layout()
                self._paint();return 0
            hit=self._hit(x,y)
            if hit=='close':self._hide()
            elif hit=='more':self.menu=True;self._paint()
            elif hit in ('header','collapsed'):self.drag=(g.GetCursorPos(),self.anchor);self.dragged=False;g.SetCapture(hwnd)
            elif hit=='footer':self.open_list()
            elif hit=='scroll':
                maximum,top,length=scrollbar_geometry(self.content_height,self.offset)
                if not top<=y<=top+length:
                    self.offset=max(0,min(maximum,self.offset+(-1 if y<top else 1)*(LIST_BOTTOM-LIST_TOP)));self._paint();_,top,length=scrollbar_geometry(self.content_height,self.offset)
                self.thumb_drag=max(0,min(length,y-top));g.SetCapture(hwnd)
            elif isinstance(hit,int):
                if x<47:self.app.update(hit,dict(action='complete'));self._poll()
                else:self.open_list()
            return 0
        if message==c.WM_LBUTTONUP:
            if self.drag:
                self.drag=None;g.ReleaseCapture()
                if self.dragged:self.app.save_settings(dict(widget_position=list(self.anchor)))
                elif not self.expanded:self.expanded=True;self._layout()
            if self.thumb_drag is not None:self.thumb_drag=None;g.ReleaseCapture();self._paint()
            return 0
        if message==c.WM_CAPTURECHANGED:self.drag=None;self.thumb_drag=None;return 0
        if message==c.WM_MOUSEWHEEL and self.expanded:
            delta=ctypes.c_short(wparam>>16).value;self.offset=max(0,min(scrollbar_geometry(self.content_height,self.offset)[0],self.offset-delta/120*52));self.menu=False;self._paint();return 0
        if message==c.WM_RBUTTONUP:self.menu=not self.menu;self._paint();return 0
        if message==c.WM_CLOSE:g.DestroyWindow(hwnd);return 0
        if message==c.WM_DESTROY:self.user.KillTimer(hwnd,1);g.PostQuitMessage(0);return 0
        return g.DefWindowProc(hwnd,message,wparam,lparam)
