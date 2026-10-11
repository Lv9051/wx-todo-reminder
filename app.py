"""本地微信待办提醒：网页设置、SQLite 存储、纯脚本调度。"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
import webbrowser
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener
from runtime_paths import RESOURCE_DIR, DATA_DIR, FROZEN, VERSION

ROOT = RESOURCE_DIR
BUILD = "auto-uia-" + VERSION
TZ = timezone(timedelta(hours=8))
DEFAULTS = dict(mode="wechat", enabled=True, start="00:00", end="23:59",
                weekdays=False, daily_limit=0)


class RetryableSendError(RuntimeError):
    """确定尚未按下发送键的失败，可自动重试。"""
    def __init__(self,message,retry_delay=60):
        super().__init__(message)
        self.retry_delay=retry_delay


def now():
    return datetime.now(TZ)


def iso(value):
    return value.isoformat(timespec="seconds")


def parse_time(value):
    return datetime.fromisoformat(value).astimezone(TZ)


def clock(value):
    try:
        if len(value) != 5 or value[2] != ":":
            raise ValueError()
        hour, minute = map(int, value.split(":"))
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError()
        return hour * 60 + minute
    except (TypeError, ValueError):
        raise ValueError("时间请填写为 HH:mm，例如 09:00") from None


def next_allowed(value, settings):
    """支持跨午夜时段，工作日以实际提醒日期判断。"""
    start, end = clock(settings["start"]), clock(settings["end"])
    for _ in range(9 * 24 * 60):
        minute = value.hour * 60 + value.minute
        hours_ok = (start <= minute < end) if start < end else (minute >= start or minute < end)
        if hours_ok and (not settings["weekdays"] or value.weekday() < 5):
            return value
        value = (value + timedelta(minutes=1)).replace(second=0, microsecond=0)
    raise ValueError("无法计算下次提醒时间")


def validate_task(body):
    target_type = body.get("target_type", "group")
    if target_type not in ("local", "self", "group", "contact"):
        raise ValueError("请选择仅记录、桌面通知、微信群或联系人私聊")
    result = {"target_type": target_type, "member": "", "group_name": ""}
    fields = [("title", "任务内容", 500)]
    if target_type in ("contact", "group"):
        fields.append(("group_name", "群名" if target_type == "group" else "联系人名称", 100))
    if target_type == "group":
        fields.append(("member", "成员群昵称", 100))
    for field, label, limit in fields:
        value = body.get(field)
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
            raise ValueError(f"{label}不能为空，且最多 {limit} 字")
        result[field] = value.strip()
    try:
        interval = float(body.get("interval_hours", 3))
        if not math.isfinite(interval) or not 0.1 <= interval <= 720:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("提醒间隔需为 0.1～720 小时") from None
    result["interval_hours"] = interval
    if type(body.get("repeat", True)) is not bool:
        raise ValueError("重复提醒参数无效")
    result["repeat"] = int(body.get("repeat", True))
    first_due = body.get("first_due")
    if first_due and target_type != "local":
        try:
            value = datetime.fromisoformat(first_due)
            result["first_due"] = value.replace(tzinfo=TZ) if value.tzinfo is None else value.astimezone(TZ)
        except (ValueError, TypeError):
            raise ValueError("首次提醒时间无效") from None
    return result


def message(task):
    return f"【待办提醒 #{task['id']}】{task['title']}\n请完成后联系任务管理员标记完成。"


def transport(task, mode):
    if mode == "preview":
        return {"status": "preview", "detail": "仅预览，未向微信发送"}
    if FROZEN:
        import tempfile
        with tempfile.TemporaryDirectory(prefix="wx-reminder-") as folder:
            source, result_file = Path(folder) / "input.json", Path(folder) / "output.json"
            source.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
            subprocess.run([sys.executable, "--wechat-adapter", "--input", str(source), "--output", str(result_file)],
                           capture_output=True, timeout=100, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                result = json.loads(result_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise RuntimeError("微信接入程序未返回结果") from None
        if not result.get("ok"):
            error = RetryableSendError if result.get("retryable") is True else RuntimeError
            if error is RetryableSendError:
                raise error(result.get('error','微信发送未确认'),retry_delay=1 if result.get('deferred') else 60)
            raise error(result.get("error", "微信发送未确认"))
        return {"status": "operated", "detail": result["detail"]}
    process = subprocess.run([sys.executable, '-X', 'utf8', str(ROOT / "wechat_adapter.py")],
                             input=json.dumps(task, ensure_ascii=False),
                             text=True, encoding="utf-8", capture_output=True,
                             timeout=100, cwd=ROOT)
    try:
        result = json.loads(process.stdout)
    except (ValueError, TypeError):
        raise RuntimeError("微信接入程序未返回有效结果，请检查本地运行日志") from None
    if not result.get("ok"):
        error = RetryableSendError if result.get("retryable") is True else RuntimeError
        if error is RetryableSendError:
            raise error(result.get('error','微信发送未确认'),retry_delay=1 if result.get('deferred') else 60)
        raise error(result.get("error", "微信发送未确认"))
    return {"status": "operated", "detail": result["detail"]}


class ReminderApp:
    def __init__(self, path, sender=transport, notifier=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.dispatch_lock = threading.Lock()
        self.sender = sender
        self.notifier = notifier
        self.stop = threading.Event()
        self.token = secrets.token_urlsafe(24)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                  group_name TEXT NOT NULL, member TEXT NOT NULL, interval_hours REAL NOT NULL,
                  status TEXT NOT NULL DEFAULT 'active', next_due TEXT NOT NULL,
                  last_reminded TEXT, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER,
                  at TEXT NOT NULL, mode TEXT NOT NULL, status TEXT NOT NULL,
                  content TEXT NOT NULL, detail TEXT NOT NULL);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(tasks)")}
            if "target_type" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN target_type TEXT NOT NULL DEFAULT 'group'")
            for name, definition in (("completed_at", "TEXT"), ("repeat", "INTEGER NOT NULL DEFAULT 1"),
                                     ("reminder_finished", "INTEGER NOT NULL DEFAULT 0")):
                if name not in columns:
                    db.execute(f"ALTER TABLE tasks ADD COLUMN {name} {definition}")
            row = db.execute("SELECT value FROM settings WHERE id=1").fetchone()
            config = {**DEFAULTS, **(json.loads(row[0]) if row else {})}
            if config.get("schedule_version", 1) < 2:
                # 升级旧版的默认四次上限；保留用户设置的其他上限和时段。
                if config["daily_limit"] == 4:
                    config["daily_limit"] = 0
                config["schedule_version"] = 2
            # 保留用户设置；正在发送时退出的任务单独恢复为状态待核对。
            db.execute("INSERT OR REPLACE INTO settings VALUES (1,?)", (json.dumps(config),))
            db.execute("UPDATE tasks SET status='paused' WHERE id IN "
                       "(SELECT task_id FROM events WHERE status='sending')")
            db.execute("UPDATE events SET status='unknown', detail='程序上次在发送途中退出；请核对微信后手动恢复任务' "
                       "WHERE status='sending'")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def config(self, db):
        return json.loads(db.execute("SELECT value FROM settings WHERE id=1").fetchone()[0])

    def snapshot(self, include_history=True):
        with self.lock, self.connect() as db:
            return dict(settings=self.config(db),
                        tasks=[dict(row) for row in db.execute("SELECT * FROM tasks " + ("" if include_history else "WHERE status NOT IN ('done','deleted') ") + "ORDER BY id DESC")],
                        events=[dict(row) for row in db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 100")],
                        today_count=db.execute("SELECT count(*) FROM events WHERE substr(at,1,10)=? "
                                               "AND status IN ('preview','operated','notified')", (now().date().isoformat(),)).fetchone()[0],
                        server_time=iso(now()), timezone="Asia/Shanghai", token=self.token, build=BUILD,
                        version=VERSION, desktop_notifications=self.notifier is not None)

    def save_settings(self, body):
        with self.dispatch_lock, self.lock, self.connect() as db:
            config = self.config(db)
            config.update({key: body[key] for key in DEFAULTS if key in body})
            if config["mode"] not in ("preview", "wechat"):
                raise ValueError("提醒方式无效")
            for key in ("enabled", "weekdays"):
                if type(config[key]) is not bool:
                    raise ValueError("开关参数无效")
            if clock(config["start"]) == clock(config["end"]):
                raise ValueError("提醒开始和结束时间不能相同")
            limit = config["daily_limit"]
            if type(limit) is not int or not 0 <= limit <= 240:
                raise ValueError("每日每任务上限需为 0～240 次，0 表示不限")
            db.execute("UPDATE settings SET value=? WHERE id=1", (json.dumps(config),))

    def create(self, body):
        data = validate_task(body)
        with self.lock, self.connect() as db:
            value = now()
            config = self.config(db)
            if data["target_type"] != "local" and body.get("auto_start") is True:
                config.update(mode="wechat", enabled=True)
                db.execute("UPDATE settings SET value=? WHERE id=1", (json.dumps(config),))
            due = next_allowed(data.get("first_due", value + timedelta(hours=data["interval_hours"])), config)
            cursor = db.execute("INSERT INTO tasks(title,group_name,member,interval_hours,next_due,created,target_type,repeat) "
                                "VALUES(?,?,?,?,?,?,?,?)", (data["title"], data["group_name"], data["member"],
                                data["interval_hours"], iso(due), iso(value), data["target_type"], data["repeat"]))
            return cursor.lastrowid

    def update(self, task_id, body):
        with self.lock, self.connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise ValueError("任务不存在")
            action = body.get("action")
            if action == "edit":
                data = validate_task(body)
                config = self.config(db)
                if data["target_type"] != "local" and body.get("auto_start") is True:
                    config.update(mode="wechat", enabled=True)
                    db.execute("UPDATE settings SET value=? WHERE id=1", (json.dumps(config),))
                due = next_allowed(data.get("first_due", now() + timedelta(hours=data["interval_hours"])), config)
                db.execute("UPDATE tasks SET title=?,group_name=?,member=?,interval_hours=?,next_due=?,target_type=?,repeat=?,reminder_finished=0 WHERE id=?",
                           (data["title"], data["group_name"], data["member"], data["interval_hours"], iso(due), data["target_type"], data["repeat"], task_id))
            elif action in ("pause", "resume", "complete"):
                status = {"pause": "paused", "resume": "active", "complete": "done"}[action]
                due = next_allowed(now() + timedelta(hours=row["interval_hours"]), self.config(db))
                completed = iso(now()) if action == "complete" else None
                db.execute("UPDATE tasks SET status=?,next_due=?,completed_at=?,reminder_finished=0 WHERE id=?",
                           (status, iso(due), completed, task_id))
            else:
                raise ValueError("未知任务操作")

    def dispatch(self, task_id, manual=False, live_confirmed=False, at=None):
        # 先取得发送锁；与设置修改采用相同锁顺序，保证模式不在途中变更。
        with self.lock:
            if not self.dispatch_lock.acquire(blocking=False):
                return False
        try:
            value = at or now()
            with self.lock, self.connect() as db:
                row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
                config = self.config(db)
                if not row or row["status"] != "active":
                    if manual:
                        raise ValueError("仅进行中的任务可立即提醒")
                    return False
                task = dict(row)
                if task["target_type"] == "local":
                    if manual:
                        raise ValueError("仅记录的待办不发送微信消息")
                    return False
                if not manual and task["reminder_finished"]:
                    return False
                if not manual and (not config["enabled"] or parse_time(task["next_due"]) > value):
                    return False
                allowed = next_allowed(value, config)
                if allowed > value:
                    if manual:
                        raise ValueError("当前不在允许提醒时段内")
                    db.execute("UPDATE tasks SET next_due=? WHERE id=?", (iso(allowed), task_id))
                    return False
                mode = "desktop" if task["target_type"] == "self" and config["mode"] != "preview" else config["mode"]
                count = db.execute("SELECT count(*) FROM events WHERE task_id=? AND mode=? AND substr(at,1,10)=? "
                                   "AND status IN ('sending','operated','notified','preview','unknown')",
                                   (task_id, mode, value.date().isoformat())).fetchone()[0]
                if config["daily_limit"] and count >= config["daily_limit"]:
                    if manual:
                        raise ValueError("这条任务今日已达到提醒上限")
                    tomorrow = (value + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                    due = next_allowed(tomorrow, config)
                    db.execute("UPDATE tasks SET next_due=? WHERE id=?", (iso(due), task_id))
                    return False
                task["message"] = message(task)
                task["_data_dir"] = str(self.path.parent.resolve())
                cursor = db.execute("INSERT INTO events(task_id,at,mode,status,content,detail) VALUES(?,?,?,'sending',?,?)",
                                    (task_id, iso(value), mode,
                                     ("提醒自己\n" if task["target_type"] == "self" else f"私聊「{task['group_name']}」\n" if task["target_type"] == "contact" else f"@{task['member']} ") + task["message"],
                                     "正在处理"))
                event_id = cursor.lastrowid
                due = next_allowed(value + timedelta(hours=task["interval_hours"]), config)
                db.execute("UPDATE tasks SET next_due=? WHERE id=?", (iso(due), task_id))
            try:
                if mode == "desktop":
                    if self.notifier is None:
                        raise RetryableSendError("桌面通知未就绪，请用桌面软件或 start.bat 启动")
                    self.notifier(task["title"], "待办提醒")
                    result = dict(status="notified", detail="已提交 Windows 桌面通知；可在待办清单中完成事项")
                else:
                    result = self.sender(task, mode)
                status, detail = result["status"], result["detail"]
            except RetryableSendError as exc:
                status, detail = "retry", f"{exc}；尚未发送，1 分钟后自动重试"
            except Exception as exc:
                status, detail = "unknown", f"{type(exc).__name__}: {exc}；已暂停该任务，请核对后恢复"
            with self.lock, self.connect() as db:
                db.execute("UPDATE events SET status=?,detail=? WHERE id=?", (status, detail, event_id))
                if status == "unknown":
                    db.execute("UPDATE tasks SET status='paused' WHERE id=? AND status='active'", (task_id,))
                elif status == "retry":
                    due = next_allowed(value + timedelta(minutes=1), config)
                    db.execute("UPDATE tasks SET next_due=? WHERE id=?", (iso(due), task_id))
                else:
                    db.execute("UPDATE tasks SET last_reminded=?,reminder_finished=? WHERE id=?",
                               (iso(value), int(not task["repeat"] and status != "preview"), task_id))
            return True
        finally:
            self.dispatch_lock.release()

    def tick(self, at=None):
        with self.lock, self.connect() as db:
            ids = [row[0] for row in db.execute("SELECT id FROM tasks WHERE status='active' AND target_type!='local' ORDER BY next_due")]
        for task_id in ids:
            if self.stop.is_set():
                break
            self.dispatch(task_id, at=at)

    def check_task(self, task_id):
        if not self.dispatch_lock.acquire(blocking=False):
            raise ValueError("正在操作微信，请稍后再检查")
        try:
            with self.lock, self.connect() as db:
                row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
                if row is None:
                    raise ValueError("任务不存在")
                task = dict(row)
                if task["target_type"] in ("local", "self"):
                    raise ValueError("这条待办无需连接微信")
                task["_data_dir"] = str(self.path.parent.resolve())
            task["operation"] = "check"
            try:
                return transport(task, "wechat")
            except Exception as exc:
                raise ValueError(str(exc)) from None
        finally:
            self.dispatch_lock.release()

    def run(self):
        while not self.stop.wait(5):
            try:
                self.tick()
            except Exception as exc:
                print(f"调度错误：{exc}", file=sys.stderr)


def diagnostic():
    """只检查环境，不操作微信、不读取聊天内容。"""
    deps = {name: bool(importlib.util.find_spec(name)) for name in ("uiautomation", "win32gui")}
    processes = []
    if os.name == "nt":
        result = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, timeout=10)
        import csv
        processes = [row[0] for row in csv.reader(result.stdout.decode("mbcs", errors="replace").splitlines())
                     if row and row[0].lower() in ("weixin.exe", "wechat.exe")]
    return dict(build=BUILD, dependencies=deps, windows=os.name == "nt", python=sys.version.split()[0],
                wechat_running=bool(processes), processes=processes,
                source_downloaded=(ROOT / "vendor/wechatauto-replica/wechatauto/uia_driver.py").exists(),
                detail="检查不会发送消息。真实 @ 发送需要已登录微信和解锁桌面；首次请用测试群。")


def serve(app, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def respond(self, value, status=200, content_type="application/json; charset=utf-8"):
            raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            app = self.server.reminders
            # 禁止外部域名通过 DNS rebinding 读取本地任务。
            if self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
                return self.respond({"error": "只允许本机访问"}, 403)
            path = urlsplit(self.path).path
            if path == "/api/state":
                return self.respond(app.snapshot(include_history=False))
            if path == '/api/history':
                from urllib.parse import parse_qs
                query = parse_qs(urlsplit(self.path).query)
                try:
                    return self.respond(app.history(status=query.get('status',['done'])[0], month=query.get('month',[''])[0],
                                                    search=query.get('search',[''])[0], offset=int(query.get('offset',['0'])[0])))
                except (ValueError, TypeError) as exc:
                    return self.respond({'error':str(exc)},400)
            if path == '/api/backup':
                return self.respond(app.backup().read_bytes(), content_type='application/octet-stream')
            if path == "/api/diagnostic":
                return self.respond(diagnostic())
            files = {"/": ("index.html", "text/html"), "/style.css": ("style.css", "text/css"),
                     "/main.js": ("main.js", "application/javascript"), "/icon.png": ("../assets/icon.png", "image/png")}
            if path not in files:
                return self.respond({"error": "页面不存在"}, 404)
            name, mime = files[path]
            self.respond((ROOT / "web" / name).read_bytes(), content_type=f"{mime}; charset=utf-8")

        def do_POST(self):
            app = self.server.reminders
            if self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}") or self.headers.get("X-Reminder-Token") != app.token:
                return self.respond({"error": "页面会话已过期，请刷新"}, 403)
            origin = self.headers.get("Origin")
            if origin and origin not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
                return self.respond({"error": "不允许跨站请求"}, 403)
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= (28*1024*1024 if urlsplit(self.path).path == '/api/data/restore' else 16384):
                    raise ValueError("请求内容过大或为空")
                body = json.loads(self.rfile.read(size).decode("utf-8"))
                if not isinstance(body, dict):
                    raise ValueError("请求格式无效")
                path = urlsplit(self.path).path
                if path == "/api/settings":
                    app.save_settings(body)
                elif path == "/api/tasks":
                    app.create(body)
                elif path == '/api/remind-all':
                    count = app.enqueue(body.get('task_ids'))
                    return self.respond({**app.snapshot(include_history=False), 'queued_added': count})
                elif path == '/api/data/restore':
                    app.restore(body.get('content', ''))
                elif path == '/api/tasks/delete-many':
                    app.delete_many(body.get('task_ids'),restore=body.get('restore') is True)
                elif path == '/api/window/open':
                    if not getattr(app,'open_window',None):
                        raise ValueError('请使用桌面软件启动')
                    app.open_window()
                elif path == '/api/widget/reset':
                    if not getattr(app,'widget',None):
                        raise ValueError('请使用桌面软件启动')
                    app.widget.reset_position()
                elif path == "/api/notifications/test":
                    if app.notifier is None:
                        raise ValueError("桌面通知尚未就绪，请用桌面软件或 start.bat 启动")
                    app.notifier("这是一条测试通知。以后到点会这样提醒你的待办。", "待办提醒")
                elif path.startswith("/api/tasks/"):
                    task_id = int(path.rsplit("/", 1)[-1])
                    if body.get("action") == "check":
                        return self.respond(app.check_task(task_id))
                    elif body.get("action") == "remind":
                        app.enqueue([task_id])
                    else:
                        app.update(task_id, body)
                else:
                    return self.respond({"error": "接口不存在"}, 404)
                self.respond(app.snapshot(include_history=False))
            except (ValueError, KeyError, TypeError) as exc:
                self.respond({"error": str(exc)}, 400)
            except Exception:
                self.respond({"error": "处理失败，请检查运行窗口"}, 500)

    class LocalServer(ThreadingHTTPServer):
        # Windows 的 SO_REUSEADDR 允许旧、新实例同时绑定，不能用于单实例服务。
        allow_reuse_address = os.name != "nt"
    server = LocalServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.reminders = app
    return server


def main(desktop=False):
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-tray", action="store_true")
    parser.add_argument("--data", type=Path)
    parser.add_argument('--portable', action='store_true')
    args = parser.parse_args()
    url = f"http://127.0.0.1:{args.port}"
    try:
        # 先占用端口，避免重复启动时意外修改正在运行的调度状态。
        server = serve(None, args.port)
    except OSError:
        # 再次双击启动时复用已运行的本项目服务，不初始化或重置数据库。
        try:
            opener = build_opener(ProxyHandler({}))
            with opener.open(url + "/api/state", timeout=3) as response:
                state = json.load(response)
            if isinstance(state, dict) and str(state.get("build", "")).startswith("auto-uia-"):
                print(f"待办提醒已经运行，打开页面：{url}", flush=True)
                if not args.no_browser:
                    if desktop:
                        from urllib.request import Request
                        req=Request(url+'/api/window/open',data=b'{}',headers={'Content-Type':'application/json','X-Reminder-Token':state['token']})
                        with opener.open(req,timeout=5):pass
                    else:
                        webbrowser.open(url)
                return
        except (OSError, ValueError):
            pass
        print(f"端口 {args.port} 已占用。若程序已启动，请访问 http://127.0.0.1:{args.port}；否则使用 --port 指定其他端口。")
        return
    from runtime_paths import default_database
    from v11_app import ReminderApp as QueuedReminderApp
    app = QueuedReminderApp(args.data or default_database(args.portable))
    if FROZEN and args.data is None and not args.portable:
        from startup import startup_enabled,set_startup
        if startup_enabled():
            try:
                set_startup(True,database=app.path)
            except (OSError,ValueError) as exc:
                import logging
                logging.getLogger(__name__).warning('自启动副本未更新：%s',exc)
    server.reminders = app
    shell = None
    if desktop and not args.no_tray:
        from desktop_runtime import DesktopShell
        shell = DesktopShell(app, server, url)
        shell.start()
        app.notifier = shell.notify
        app.open_window = shell.open
    threading.Thread(target=app.run, daemon=True).start()
    print(f"微信待办提醒已启动：{url}（{BUILD}）\n保存提醒任务后按计划自动执行；重启保留调度设置。关闭程序将停止调度。", flush=True)
    if not args.no_browser:
        if desktop:
            from desktop_runtime import open_window
            if shell:shell.open()
            else:open_window(url)
        else:
            webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop.set()
        server.server_close()
        if shell:
            shell.stop()


if __name__ == "__main__":
    main()
