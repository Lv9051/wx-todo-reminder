"""1.1 持久化发送队列；桌面通知与微信分别执行，按会话汇总。"""
import base64
import json
import math
import sqlite3
import tempfile
import threading
import uuid
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from app import ReminderApp as LegacyApp, DEFAULTS, RetryableSendError, transport, validate_task, now, iso, parse_time, next_allowed, clock

EXTRA_DEFAULTS = dict(wait_idle=False, idle_seconds=1, sound_enabled=True, compact=True,widget_enabled=False,widget_position=None,widget_scope='mine')
PENDING = ('queued', 'waiting', 'retry', 'sending')


def validate_config(cfg):
    if cfg['mode'] not in ('wechat','preview'):
        raise ValueError('提醒方式无效')
    for key in ('enabled','weekdays','wait_idle','sound_enabled','compact','widget_enabled'):
        if type(cfg[key]) is not bool:
            raise ValueError('开关参数无效')
    if clock(cfg['start']) == clock(cfg['end']):
        raise ValueError('提醒开始和结束时间不能相同')
    if type(cfg['daily_limit']) is not int or not 0 <= cfg['daily_limit'] <= 240:
        raise ValueError('每日上限需为 0～240')
    if type(cfg['idle_seconds']) is not int or not 1 <= cfg['idle_seconds'] <= 600:
        raise ValueError('空闲检测需为 1～600 秒')
    position=cfg['widget_position']
    if cfg['widget_scope'] not in ('mine','all'):
        raise ValueError('桌面小窗查看范围无效')
    if position is not None and (not isinstance(position,list) or len(position)!=2 or any(type(n) is not int or abs(n)>100000 for n in position)):
        raise ValueError('悬浮窗位置无效')


def names(value):
    if isinstance(value, str):
        value = value.splitlines()
    if not isinstance(value, list) or len(value) > 30:
        raise ValueError('每行填写一个对象，最多 30 人')
    result = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item.strip()) > 100:
            raise ValueError('对象名称不能为空，且最多 100 字')
        if item.strip() not in result:
            result.append(item.strip())
    return result


def prepare_task(body):
    body = dict(body)
    kind = body.get('target_type', 'group')
    recipients = []
    if kind in ('group', 'contact'):
        recipients = names(body.get('recipients', body.get('member' if kind == 'group' else 'group_name', '')))
        if not recipients:
            raise ValueError('请填写提醒对象')
        body['member' if kind == 'group' else 'group_name'] = recipients[0]
    unit = body.get('interval_unit', 'hours')
    if unit not in ('hours', 'days', 'weeks'):
        raise ValueError('周期单位无效')
    factor = {'hours': 1, 'days': 24, 'weeks': 168}[unit]
    try:
        amount = float(body['interval_value']) if 'interval_value' in body else float(body.get('interval_hours', 3)) / factor
        hours = amount * factor
        if not math.isfinite(hours) or not .1 <= hours <= 720:
            raise ValueError()
        if unit != 'hours' and (amount < 1 or amount != int(amount)):
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('周期需为 0.1 小时至 30 天；天和周请填写正整数') from None
    body['interval_hours'] = hours
    scope = body.get('owner_scope', 'delegated' if kind in ('group','contact') else 'mine')
    if scope not in ('mine','delegated'):
        raise ValueError('负责人类型无效')
    owner = body.get('owner_name', '')
    if not isinstance(owner, str) or len(owner.strip()) > 100:
        raise ValueError('负责人最多 100 字')
    body.update(owner_scope=scope, owner_name=owner.strip())
    validate_task(body)
    return body, recipients, unit, amount


class ReminderApp(LegacyApp):
    def __init__(self, path, sender=transport, notifier=None, idle_probe=None):
        super().__init__(path, sender, notifier)
        if idle_probe is None:
            from interaction_guard import waiting_reason
            idle_probe = waiting_reason
        self.idle_probe = idle_probe
        self.wx_thread = None
        with self.connect() as db:
            needs_backup = 'deleted_at' not in {r[1] for r in db.execute('PRAGMA table_info(tasks)')} and db.execute('SELECT count(*) FROM tasks').fetchone()[0]>0
        if needs_backup:
            self.backup()
        with self.lock, self.connect() as db:
            columns = {r[1] for r in db.execute('PRAGMA table_info(tasks)')}
            for name, spec in (('recipients', "TEXT NOT NULL DEFAULT '[]'"), ('revision', 'INTEGER NOT NULL DEFAULT 1'),
                               ('interval_unit', "TEXT NOT NULL DEFAULT 'hours'"), ('interval_value', 'REAL'),
                               ('deleted_at','TEXT'),('deleted_status','TEXT'),('owner_scope','TEXT'),
                               ('owner_name',"TEXT NOT NULL DEFAULT ''")):
                if name not in columns:
                    db.execute(f'ALTER TABLE tasks ADD COLUMN {name} {spec}')
            db.execute('UPDATE tasks SET interval_value=interval_hours WHERE interval_value IS NULL')
            db.execute("UPDATE tasks SET owner_scope=CASE WHEN target_type IN ('group','contact') THEN 'delegated' ELSE 'mine' END WHERE owner_scope IS NULL")
            for row in db.execute("SELECT * FROM tasks WHERE recipients='[]' AND target_type IN ('contact','group')").fetchall():
                recipients = [row['member'] if row['target_type'] == 'group' else row['group_name']]
                db.execute('UPDATE tasks SET recipients=? WHERE id=?', (json.dumps(recipients, ensure_ascii=False), row['id']))
            db.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL, revision INTEGER NOT NULL,
                    route TEXT NOT NULL, target_type TEXT NOT NULL, members TEXT NOT NULL, mode TEXT NOT NULL,
                    occurrence TEXT NOT NULL, automatic INTEGER NOT NULL, event_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued', ready_at TEXT NOT NULL, created TEXT NOT NULL,
                    unique_key TEXT NOT NULL UNIQUE);
                CREATE INDEX IF NOT EXISTS jobs_pending ON jobs(status,ready_at);
            ''')
            db.execute("UPDATE jobs SET status='unknown' WHERE status='sending'")
            cfg = {**EXTRA_DEFAULTS, **self.config(db)}
            if cfg.get('interaction_version',1)<3:
                cfg.update(wait_idle=False, interaction_version=3)
            db.execute('UPDATE settings SET value=? WHERE id=1', (json.dumps(cfg),))

    def snapshot(self):
        result = super().snapshot()
        with self.lock, self.connect() as db:
            for task in result['tasks']:
                task['recipients'] = json.loads(task['recipients'])
                jobs = db.execute("SELECT j.status,e.detail FROM jobs j JOIN events e ON e.id=j.event_id "
                                  "WHERE j.task_id=? AND j.revision=? AND j.status IN ('queued','waiting','retry','sending') ORDER BY j.id",
                                  (task['id'], task['revision'])).fetchall()
                task['queue_status'] = jobs[0]['status'] if jobs else ''
                task['queue_detail'] = jobs[0]['detail'] if jobs else ''
            result['queue_count'] = db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','waiting','retry','sending')").fetchone()[0]
        from startup import startup_enabled
        result['startup_enabled'] = startup_enabled()
        result['data_path'] = str(self.path.resolve())
        widget = getattr(self, 'widget', None)
        result['widget_state'] = ('failed' if widget.error else 'visible' if widget.visible else 'hidden') if widget else 'unavailable'
        return result

    def save_settings(self, body):
        with self.lock, self.connect() as db:
            cfg = self.config(db)
            cfg.update({key: body[key] for key in (*DEFAULTS, *EXTRA_DEFAULTS) if key in body})
            validate_config(cfg)
            if 'startup_enabled' in body:
                from startup import set_startup
                set_startup(body['startup_enabled'],database=self.path)
            if cfg['mode'] != self.config(db)['mode']:
                # 取消旧模式的未发送作业；切换成预览绝不能留下已排队的实际发送。
                ids = [r[0] for r in db.execute("SELECT event_id FROM jobs WHERE status IN ('queued','waiting','retry')")]
                db.execute("UPDATE jobs SET status='cancelled' WHERE status IN ('queued','waiting','retry')")
                for event_id in ids:
                    db.execute("UPDATE events SET status='cancelled',detail='提醒方式已修改，取消旧模式排队项' WHERE id=?", (event_id,))
            db.execute('UPDATE settings SET value=? WHERE id=1', (json.dumps(cfg),))

    def create(self, body):
        normalized, recipients, unit, amount = prepare_task(body)
        with self.lock:
            task_id = super().create(normalized)
            with self.connect() as db:
                db.execute('UPDATE tasks SET recipients=?,interval_unit=?,interval_value=?,owner_scope=?,owner_name=? WHERE id=?',
                           (json.dumps(recipients, ensure_ascii=False), unit, amount, normalized['owner_scope'], normalized['owner_name'], task_id))
        return task_id

    def update(self, task_id, body):
        recipients = unit = amount = None
        if body.get('action') == 'edit':
            body, recipients, unit, amount = prepare_task(body)
        with self.lock:
            if body.get('action') in ('delete','undelete'):
                self.delete_many([task_id], restore=body['action']=='undelete')
                return
            with self.connect() as db:
                row = db.execute('SELECT status FROM tasks WHERE id=?',(task_id,)).fetchone()
                if not row or row['status']=='deleted':
                    raise ValueError('待办已删除或不存在')
            super().update(task_id, body)
            with self.connect() as db:
                if recipients is not None:
                    db.execute('UPDATE tasks SET recipients=?,interval_unit=?,interval_value=?,owner_scope=?,owner_name=? WHERE id=?',
                               (json.dumps(recipients, ensure_ascii=False), unit, amount, body['owner_scope'], body['owner_name'], task_id))
                db.execute('UPDATE tasks SET revision=revision+1 WHERE id=?', (task_id,))
                jobs = db.execute("SELECT event_id FROM jobs WHERE task_id=? AND status IN ('queued','waiting','retry')", (task_id,)).fetchall()
                db.execute("UPDATE jobs SET status='cancelled' WHERE task_id=? AND status IN ('queued','waiting','retry')", (task_id,))
                for row in jobs:
                    db.execute("UPDATE events SET status='cancelled',detail='待办已完成、暂停或修改，取消旧排队项' WHERE id=?", (row[0],))

    def delete_many(self, task_ids, restore=False):
        if not isinstance(task_ids,list) or not task_ids or len(task_ids)>1000 or any(type(t) is not int for t in task_ids):
            raise ValueError('请选择要删除或恢复的待办')
        with self.lock, self.connect() as db:
            rows = [db.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone() for tid in dict.fromkeys(task_ids)]
            if any(row is None for row in rows):
                raise ValueError('待办不存在')
            for row in rows:
                tid=row['id']
                if restore:
                    if row['status']!='deleted':continue
                    status='done' if row['deleted_status']=='done' else 'active' if row['target_type']=='local' else 'paused'
                    db.execute('UPDATE tasks SET status=?,deleted_at=NULL,deleted_status=NULL,revision=revision+1 WHERE id=?',(status,tid))
                else:
                    if row['status']=='deleted':continue
                    db.execute("UPDATE tasks SET deleted_status=status,status='deleted',deleted_at=?,revision=revision+1 WHERE id=?",(iso(now()),tid))
                    jobs=db.execute("SELECT event_id FROM jobs WHERE task_id=? AND status IN ('queued','waiting','retry')",(tid,)).fetchall()
                    db.execute("UPDATE jobs SET status='cancelled' WHERE task_id=? AND status IN ('queued','waiting','retry')",(tid,))
                    for job in jobs:
                        db.execute("UPDATE events SET status='cancelled',detail='待办已删除，取消尚未发送的提醒' WHERE id=?",(job[0],))

    def enqueue(self, task_ids=None, automatic=False, at=None):
        value = at or now()
        if task_ids is not None and (not isinstance(task_ids, list) or len(task_ids) > 1000 or any(type(t) is not int for t in task_ids)):
            raise ValueError('选择的待办无效')
        added = 0
        with self.lock, self.connect() as db:
            cfg = self.config(db)
            if automatic and not cfg['enabled']:
                return 0
            rows = db.execute("SELECT * FROM tasks WHERE status='active' AND target_type!='local' ORDER BY next_due,id").fetchall()
            occurrence = 'manual:' + uuid.uuid4().hex
            for row in rows:
                task = dict(row)
                if task_ids is not None and task['id'] not in task_ids:
                    continue
                if automatic and (task['reminder_finished'] or parse_time(task['next_due']) > value):
                    continue
                allowed = next_allowed(value, cfg)
                if allowed > value:
                    if automatic:
                        db.execute('UPDATE tasks SET next_due=? WHERE id=?', (iso(allowed), task['id']))
                        continue
                    raise ValueError('当前不在允许提醒时段内')
                # 待办的旧发生项仍未处理时只保留一份，长时间离线不积攒刷屏。
                if db.execute("SELECT 1 FROM jobs WHERE task_id=? AND revision=? AND status IN ('queued','waiting','retry','sending')",
                              (task['id'], task['revision'])).fetchone():
                    continue
                mode = 'desktop' if task['target_type'] == 'self' and cfg['mode'] != 'preview' else cfg['mode']
                recipients = json.loads(task['recipients'])
                routes = recipients if task['target_type'] == 'contact' else [task['group_name'] if task['target_type'] == 'group' else 'self']
                instance = task['next_due'] if automatic else occurrence
                for route in routes:
                    key = f"{task['id']}:{task['revision']}:{route}:{mode}:{instance}"
                    if db.execute('SELECT 1 FROM jobs WHERE unique_key=?', (key,)).fetchone():
                        continue
                    targets = recipients if task['target_type'] == 'group' else []
                    content = task['title'] + ('\n对象：' + ('、'.join(targets) if targets else route) if task['target_type'] in ('contact','group') else '')
                    event_id = db.execute("INSERT INTO events(task_id,at,mode,status,content,detail) VALUES(?,?,?,'queued',?,'已排队，自动处理')",
                                          (task['id'], iso(value), mode, content)).lastrowid
                    db.execute('INSERT INTO jobs(task_id,revision,route,target_type,members,mode,occurrence,automatic,event_id,ready_at,created,unique_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                               (task['id'], task['revision'], route, task['target_type'], json.dumps(targets, ensure_ascii=False), mode, instance,
                                int(automatic), event_id, iso(value), iso(value), key))
                    added += 1
                if automatic:
                    interval = timedelta(hours=task['interval_hours'])
                    due = parse_time(task['next_due'])
                    steps = max(1, math.floor((value - due) / interval) + 1)
                    db.execute('UPDATE tasks SET next_due=? WHERE id=?', (iso(next_allowed(due + interval * steps, cfg)), task['id']))
        return added

    def _finish(self, db, jobs, status, detail, value,retry_delay=60):
        for job in jobs:
            db.execute('UPDATE jobs SET status=?,ready_at=? WHERE id=?',
                       ('done' if status in ('operated','notified','preview') else status, iso(value + timedelta(seconds=retry_delay)), job['id']))
            db.execute('UPDATE events SET status=?,detail=? WHERE id=?', (status, detail, job['event_id']))
            if status == 'unknown':
                db.execute("UPDATE tasks SET status='paused' WHERE id=? AND revision=? AND status='active'", (job['task_id'], job['revision']))
            if status in ('operated','notified'):
                db.execute('UPDATE tasks SET last_reminded=? WHERE id=? AND revision=?', (iso(value), job['task_id'], job['revision']))
                pending = db.execute("SELECT 1 FROM jobs WHERE task_id=? AND revision=? AND occurrence=? AND status!='done'", (job['task_id'], job['revision'], job['occurrence'])).fetchone()
                if job['automatic'] and not pending:
                    db.execute('UPDATE tasks SET reminder_finished=CASE WHEN repeat=0 THEN 1 ELSE 0 END WHERE id=? AND revision=?', (job['task_id'], job['revision']))
                    task = db.execute('SELECT next_due,interval_hours,repeat FROM tasks WHERE id=? AND revision=?', (job['task_id'],job['revision'])).fetchone()
                    if task and task['repeat'] and parse_time(task['next_due']) <= value:
                        interval = timedelta(hours=task['interval_hours'])
                        due = parse_time(task['next_due'])
                        steps = math.floor((value-due)/interval)+1
                        db.execute('UPDATE tasks SET next_due=? WHERE id=?', (iso(next_allowed(due+interval*steps,self.config(db))),job['task_id']))

    def process_queue(self, desktop=False, at=None):
        value = at or now()
        if not desktop and not self.dispatch_lock.acquire(blocking=False):
            return False
        try:
            with self.lock, self.connect() as db:
                cfg = self.config(db)
                if not cfg['enabled']:
                    return False
                rows = db.execute("SELECT j.*,t.title,t.status AS task_status,t.revision AS task_revision,t.repeat,t.interval_hours,t.next_due "
                                  "FROM jobs j JOIN tasks t ON j.task_id=t.id WHERE j.status IN ('queued','waiting','retry') AND j.ready_at<=? "
                                  "AND " + ("j.target_type='self'" if desktop else "j.target_type!='self'") + ' ORDER BY j.id', (iso(value),)).fetchall()
                candidates = []
                for row in rows:
                    job = dict(row)
                    if job['task_status'] != 'active' or job['revision'] != job['task_revision']:
                        self._finish(db, [job], 'cancelled', '待办状态已变化，取消排队项', value)
                        continue
                    expected = 'desktop' if job['target_type'] == 'self' and cfg['mode'] != 'preview' else cfg['mode']
                    if job['mode'] != expected:
                        self._finish(db, [job], 'cancelled', '提醒方式已变化，取消排队项', value)
                        continue
                    allowed = next_allowed(value, cfg)
                    if allowed > value:
                        db.execute('UPDATE jobs SET ready_at=? WHERE id=?', (iso(allowed), job['id']))
                        continue
                    if cfg['daily_limit']:
                        counts = db.execute("SELECT count(DISTINCT j.occurrence) FROM jobs j JOIN events e ON e.id=j.event_id WHERE j.task_id=? AND j.mode=? "
                                            "AND substr(e.at,1,10)=? AND j.status IN ('done','unknown','sending') AND j.occurrence!=?",
                                            (job['task_id'], job['mode'], value.date().isoformat(), job['occurrence'])).fetchone()[0]
                        if counts >= cfg['daily_limit']:
                            tomorrow = next_allowed((value + timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0), cfg)
                            db.execute("UPDATE jobs SET ready_at=?,status='waiting' WHERE id=?", (iso(tomorrow), job['id']))
                            db.execute("UPDATE events SET status='waiting',detail='已达今日提醒上限，等待下一允许时段' WHERE id=?", (job['event_id'],))
                            continue
                    candidates.append(job)
                if not candidates:
                    return False
                first = candidates[0]
                jobs = []
                size = 0
                for job in candidates:
                    if (job['mode'], job['target_type'], job['route']) != (first['mode'], first['target_type'], first['route']):
                        continue
                    length = len(job['title']) + len(job['members']) + 60
                    if jobs and (size + length > 2600 or len(jobs) >= 20):
                        break
                    jobs.append(job)
                    size += length
                if first['mode'] == 'wechat' and cfg['wait_idle']:
                    try:
                        reason = self.idle_probe(cfg['idle_seconds'])
                    except Exception:
                        reason = '暂时无法核对空闲状态'
                    if reason:
                        for job in jobs:
                            db.execute("UPDATE jobs SET status='waiting',ready_at=? WHERE id=?", (iso(value + timedelta(seconds=1)), job['id']))
                            db.execute("UPDATE events SET status='waiting',detail=? WHERE id=?", (reason, job['event_id']))
                        return True
                for job in jobs:
                    db.execute("UPDATE jobs SET status='sending' WHERE id=?", (job['id'],))
                    db.execute("UPDATE events SET status='sending',at=?,detail='正在发送汇总提醒' WHERE id=?", (iso(value), job['event_id']))
            members = list(dict.fromkeys(member for job in jobs for member in json.loads(job['members'])))
            text = '\n\n'.join(job['title'] + ('（' + '、'.join(json.loads(job['members'])) + '）' if job['target_type']=='group' else '') for job in jobs)
            title = f'待办提醒 · {len(jobs)} 件' if len(jobs)>1 else '待办提醒'
            payload = dict(id=first['task_id'], title=jobs[0]['title'], target_type=first['target_type'], group_name=first['route'],
                           member=members[0] if members else '', members=members, message='【待办提醒】\n'+text+'\n完成后请告知我。',
                           _data_dir=str(self.path.parent.resolve()), _avoid_interruption=cfg['wait_idle'], _idle_seconds=cfg['idle_seconds'],
                           _database=str(self.path.resolve()), _expected_mode=first['mode'], _task_versions=[[j['task_id'],j['revision']] for j in jobs])
            retry_delay=60
            try:
                if first['mode'] == 'desktop':
                    if self.notifier is None:
                        raise RetryableSendError('桌面通知尚未就绪')
                    self.notifier(jobs[0]['title'] if len(jobs)==1 else text, title)
                    result = dict(status='notified',detail=f'已提交桌面通知，汇总 {len(jobs)} 件待办')
                else:
                    result = self.sender(payload, first['mode'])
                status, detail = result['status'], result['detail']
            except RetryableSendError as exc:
                retry_delay=exc.retry_delay
                status, detail = 'retry', str(exc) + ('；尚未发送，操作停下后自动重试' if retry_delay==1 else '；尚未发送，一分钟后自动重试')
            except Exception as exc:
                status, detail = 'unknown', str(exc) + '；发送结果待核对，暂停相关待办以免重复'
            with self.lock, self.connect() as db:
                self._finish(db, jobs, status, detail, at or now(),retry_delay)
            return True
        finally:
            if not desktop:
                self.dispatch_lock.release()

    def tick(self, at=None):
        self.enqueue(automatic=True, at=at)
        self.process_queue(desktop=True, at=at)

    def run(self):
        def wechat_worker():
            while not self.stop.wait(1):
                try:
                    self.process_queue()
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception('微信队列处理失败，稍后重试')
        self.wx_thread = threading.Thread(target=wechat_worker, daemon=True, name='wechat-queue')
        self.wx_thread.start()
        while not self.stop.wait(1):
            try:
                self.tick()
            except Exception:
                import logging
                logging.getLogger(__name__).exception('待办调度失败，稍后重试')

    def backup(self):
        folder = self.path.parent / 'backups'
        folder.mkdir(exist_ok=True)
        target = folder / ('reminders-' + now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6] + '.db')
        with self.lock, self.connect() as src, closing(sqlite3.connect(target)) as dst:
            src.backup(dst)
        return target

    def restore(self, encoded):
        try:
            content = base64.b64decode(encoded, validate=True)
        except Exception:
            raise ValueError('备份文件无效') from None
        if not content.startswith(b'SQLite format 3\x00') or len(content)>20*1024*1024:
            raise ValueError('请选择不超过 20 MB 的待办数据库备份')
        if not self.dispatch_lock.acquire(blocking=False):
            raise ValueError('微信正在发送，请发送结束后恢复备份')
        try:
            with tempfile.TemporaryDirectory(dir=self.path.parent) as directory:
                source = Path(directory) / 'restore.db'
                source.write_bytes(content)
                with closing(sqlite3.connect(source)) as incoming:
                    if incoming.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise ValueError('备份文件校验失败')
                    tables = {r[0] for r in incoming.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if not {'tasks','settings','events'} <= tables:
                        raise ValueError('不是本软件的待办备份')
                    if incoming.execute("SELECT count(*) FROM sqlite_master WHERE type IN ('trigger','view')").fetchone()[0]:
                        raise ValueError('备份包含不支持的数据库对象')
                    columns = {r[1] for r in incoming.execute('PRAGMA table_info(tasks)')}
                    required = {'id','title','group_name','member','interval_hours','status','next_due','last_reminded','created'}
                    if not required <= columns:
                        raise ValueError('备份结构不完整')
                    records = [dict(zip([col[0] for col in incoming.execute('SELECT * FROM tasks').description], row)) for row in incoming.execute('SELECT * FROM tasks')]
                    cfg = {**DEFAULTS, **EXTRA_DEFAULTS, **json.loads(incoming.execute('SELECT value FROM settings WHERE id=1').fetchone()[0])}
                    cfg.update(wait_idle=False,interaction_version=3)
                    validate_config(cfg)
                    uncertain_ids = {r[0] for r in incoming.execute("SELECT task_id FROM events WHERE status IN ('sending','unknown')")}
                    for task in records:
                        validate_task({**task,'repeat':bool(task.get('repeat',1))})
                        parse_time(task['next_due'])
                        if 'recipients' in task:
                            names(json.loads(task['recipients']))
                        if task['status'] not in ('active','paused','done','deleted'):
                            raise ValueError('备份待办状态无效')
                        if task.get('owner_scope','mine') not in ('mine','delegated'):
                            raise ValueError('备份负责人类型无效')
                        if not isinstance(task.get('owner_name',''),str) or len(task.get('owner_name',''))>100:
                            raise ValueError('备份负责人无效')
                    with self.lock:
                        self.backup()
                        with self.connect() as db:
                            db.execute('DELETE FROM jobs')
                            db.execute('DELETE FROM events')
                            db.execute('DELETE FROM tasks')
                            for task in records:
                                if task['id'] in uncertain_ids and task['status']=='active':
                                    task['status']='paused'
                                recipients = task.get('recipients') or json.dumps([task['member'] if task.get('target_type','group')=='group' else task['group_name']] if task.get('target_type','group') in ('group','contact') else [])
                                db.execute('INSERT INTO tasks(id,title,group_name,member,interval_hours,status,next_due,last_reminded,created,target_type,completed_at,repeat,reminder_finished,recipients,revision,interval_unit,interval_value) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                           tuple(task[k] for k in ('id','title','group_name','member','interval_hours','status','next_due','last_reminded','created')) +
                                           (task.get('target_type','group'), task.get('completed_at'), task.get('repeat',1), task.get('reminder_finished',0), recipients, task.get('revision',1)+1, task.get('interval_unit','hours'), task.get('interval_value',task['interval_hours'])))
                                db.execute('UPDATE tasks SET owner_scope=?,owner_name=?,deleted_at=?,deleted_status=? WHERE id=?',
                                           (task.get('owner_scope','delegated' if task.get('target_type') in ('group','contact') else 'mine'),task.get('owner_name',''),task.get('deleted_at'),task.get('deleted_status'),task['id']))
                            event_columns = ('id','task_id','at','mode','status','content','detail')
                            for event in incoming.execute('SELECT '+','.join(event_columns)+' FROM events'):
                                event = list(event)
                                if event[4] in ('sending','queued','waiting','retry'):
                                    event[4] = 'cancelled'
                                    event[6] = '恢复备份时取消旧排队记录，以当前待办重新调度'
                                db.execute('INSERT INTO events('+','.join(event_columns)+') VALUES(?,?,?,?,?,?,?)',event)
                            db.execute('UPDATE settings SET value=? WHERE id=1', (json.dumps(cfg),))
        except (sqlite3.Error,TypeError,KeyError,AttributeError):
            raise ValueError('无法读取备份文件') from None
        finally:
            self.dispatch_lock.release()
