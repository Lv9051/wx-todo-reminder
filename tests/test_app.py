"""验证真正的调度边界，使用假发送器，不操作微信。"""
import tempfile
import threading
import unittest
import io
from unittest.mock import patch
from datetime import datetime, timedelta
from pathlib import Path

from app import DEFAULTS, TZ, ReminderApp, iso, next_allowed, now, validate_task, RetryableSendError, serve, main


class ReminderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.sent = []

        def sender(task, mode):
            self.sent.append((task, mode))
            return dict(status="preview" if mode == "preview" else "operated", detail="测试")

        self.app = ReminderApp(Path(self.temp.name) / "test.db", sender=sender)
        self.app.save_settings(dict(mode="preview", start="00:00", end="23:59", enabled=True))
        self.task = self.app.create(dict(title="整理实验结果", group_name="测试群", member="小王", interval_hours=3))

    def tearDown(self):
        self.temp.cleanup()

    def task_row(self):
        return self.app.snapshot()["tasks"][0]

    def due(self):
        value = now().replace(hour=12, minute=0, second=0, microsecond=0)
        with self.app.connect() as db:
            db.execute("UPDATE tasks SET next_due=?", (iso(value),))
        return value

    def test_schedule_no_early_no_duplicate_and_repeats(self):
        due = self.due()
        self.app.tick(due - timedelta(seconds=1))
        self.assertEqual(self.sent, [])
        self.app.tick(due)
        self.app.tick(due + timedelta(seconds=5))
        self.assertEqual(len(self.sent), 1)
        self.app.tick(due + timedelta(hours=3))
        self.assertEqual(len(self.sent), 2)

    def test_completed_and_paused_tasks_stop(self):
        due = self.due()
        self.app.update(self.task, dict(action="pause"))
        self.app.tick(due + timedelta(days=1))
        self.assertEqual(self.sent, [])
        self.app.update(self.task, dict(action="resume"))
        self.app.update(self.task, dict(action="complete"))
        self.app.tick(due + timedelta(days=2))
        self.assertEqual(self.sent, [])

    def test_master_pause(self):
        self.app.save_settings(dict(enabled=False))
        self.app.tick(self.due())
        self.assertEqual(self.sent, [])

    def test_daily_limit_including_manual(self):
        due = self.due()
        self.app.save_settings(dict(daily_limit=1))
        self.app.dispatch(self.task, manual=True, at=due)
        self.app.tick(due + timedelta(hours=3))
        self.assertEqual(len(self.sent), 1)
        self.assertNotEqual(self.task_row()["next_due"][:10], due.date().isoformat())
        with self.assertRaisesRegex(ValueError, "上限"):
            self.app.dispatch(self.task, manual=True, at=due)

    def test_quiet_hours_and_weekend(self):
        cfg = {**DEFAULTS, "start": "09:00", "end": "21:00", "weekdays": True}
        friday = datetime(2026, 10, 9, 22, 30, tzinfo=TZ)
        monday = datetime(2026, 10, 12, 9, tzinfo=TZ)
        self.assertEqual(next_allowed(friday, cfg), monday)
        overnight = {**DEFAULTS, "start": "21:00", "end": "06:00"}
        self.assertEqual(next_allowed(friday, overnight), friday)
        afternoon = friday.replace(hour=14)
        self.assertEqual(next_allowed(afternoon, overnight), friday.replace(hour=21, minute=0))

    def test_failed_send_pauses_without_retries(self):
        def failing(task, mode):
            self.sent.append(task)
            raise TimeoutError("发送状态不明")
        self.app.sender = failing
        due = self.due()
        self.app.tick(due)
        self.app.tick(due + timedelta(hours=5))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.task_row()["status"], "paused")
        self.assertEqual(self.app.snapshot()["events"][0]["status"], "unknown")

    def test_real_send_uses_saved_plan_without_per_send_confirmation(self):
        due = self.due()
        self.app.save_settings(dict(daily_limit=1))
        self.app.dispatch(self.task, manual=True, at=due)
        self.app.save_settings(dict(mode="wechat"))
        self.app.dispatch(self.task, manual=True, at=due)
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.sent[-1][1], "wechat")

    def test_restart_preserves_enabled_and_recovers_uncertain_inflight_task(self):
        with self.app.connect() as db:
            db.execute("INSERT INTO events(task_id,at,mode,status,content,detail) VALUES(?,?,?,'sending','x','x')",
                       (self.task, iso(now()), "wechat"))
        restarted = ReminderApp(self.app.path)
        state = restarted.snapshot()
        self.assertTrue(state["settings"]["enabled"])
        self.assertEqual(state["tasks"][0]["title"], "整理实验结果")
        self.assertEqual(state["tasks"][0]["status"], "paused")
        self.assertEqual(state["events"][0]["status"], "unknown")

    def test_parallel_send_and_settings_do_not_deadlock_or_duplicate(self):
        started, release = threading.Event(), threading.Event()
        def blocked(task, mode):
            self.sent.append(task)
            started.set()
            release.wait(3)
            return dict(status="preview", detail="测试")
        self.app.sender = blocked
        send_thread = threading.Thread(target=lambda: self.app.dispatch(self.task, manual=True, at=self.due()))
        send_thread.start()
        self.assertTrue(started.wait(2))
        self.assertFalse(self.app.dispatch(self.task, manual=True))
        settings_thread = threading.Thread(target=lambda: self.app.save_settings(dict(enabled=False)))
        settings_thread.start()
        release.set()
        send_thread.join(4)
        settings_thread.join(4)
        self.assertFalse(send_thread.is_alive())
        self.assertFalse(settings_thread.is_alive())
        self.assertEqual(len(self.sent), 1)

    def test_reject_bad_intervals_and_time(self):
        for value in ("nan", "inf", -1, 0, "abc"):
            with self.assertRaises(ValueError):
                validate_task(dict(title="x", group_name="x", member="x", interval_hours=value))
        with self.assertRaises(ValueError):
            self.app.save_settings(dict(start="09:00", end="09:00"))

    def test_private_contact_without_member_and_preview_has_no_at(self):
        task_id = self.app.create(dict(title="交实验报告", target_type="contact", group_name="小王", interval_hours=4))
        self.app.dispatch(task_id, manual=True, at=self.due())
        task, mode = self.sent[-1]
        self.assertEqual(task["target_type"], "contact")
        self.assertEqual(task["member"], "")
        record = self.app.snapshot()["events"][0]["content"]
        self.assertIn("私聊「小王」", record)
        self.assertNotIn("@", record)

    def test_edit_group_to_contact_preserves_task(self):
        self.app.update(self.task, dict(action="edit", title="整理实验结果", target_type="contact", group_name="测试联系人", interval_hours=4))
        task = self.task_row()
        self.assertEqual(task["id"], self.task)
        self.assertEqual(task["target_type"], "contact")
        self.assertEqual(task["group_name"], "测试联系人")
        self.assertEqual(task["member"], "")
        with self.assertRaises(ValueError):
            validate_task(dict(title="x", target_type="group", group_name="群", interval_hours=3))

    def test_local_todos_are_never_sent(self):
        local = self.app.create(dict(title="我自己整理数据", target_type="local"))
        self.app.update(self.task, dict(action="complete"))
        self.app.tick(now() + timedelta(days=5))
        self.assertEqual(self.sent, [])
        with self.assertRaisesRegex(ValueError, "仅记录"):
            self.app.dispatch(local, manual=True)

    def test_save_enables_automatic_send_and_accepts_first_due(self):
        self.app.save_settings(dict(enabled=False))
        due = now() + timedelta(minutes=2)
        task = self.app.create(dict(title="报告", target_type="contact", group_name="文件传输助手",
                                    interval_hours=1, first_due=iso(due), auto_start=True))
        self.assertEqual(self.app.snapshot()["settings"]["mode"], "wechat")
        self.app.tick(due - timedelta(seconds=1))
        self.assertEqual(self.sent, [])
        self.app.tick(due)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][0]["id"], task)

    def test_confirmed_presend_failure_retries_and_then_repeats(self):
        due = self.due()
        def temporary(task, mode):
            self.sent.append(task)
            if len(self.sent) == 1:
                raise RetryableSendError("微信暂时不可用")
            return dict(status="operated", detail="已发送")
        self.app.sender = temporary
        self.app.tick(due)
        self.assertEqual(self.task_row()["status"], "active")
        self.assertIsNone(self.task_row()["last_reminded"])
        self.app.tick(due + timedelta(seconds=30))
        self.assertEqual(len(self.sent), 1)
        self.app.tick(due + timedelta(minutes=1))
        self.assertEqual(len(self.sent), 2)
        self.app.tick(due + timedelta(hours=3, minutes=1))
        self.assertEqual(len(self.sent), 3)

    def test_unlimited_default_does_not_stop_after_four_reminders(self):
        due = self.due()
        self.app.save_settings(dict(daily_limit=0))
        for i in range(6):
            self.app.dispatch(self.task, manual=True, at=due + timedelta(minutes=i))
        self.assertEqual(len(self.sent), 6)

    def test_restart_continues_healthy_due_tasks(self):
        due = self.due()
        restarted = ReminderApp(self.app.path, sender=self.app.sender)
        restarted.tick(due)
        self.assertEqual(len(self.sent), 1)

    def test_second_server_cannot_bind_same_port(self):
        server=serve(None,0)
        try:
            with self.assertRaises(OSError):
                serve(None,server.server_address[1])
        finally:
            server.server_close()

    def test_mode_change_keeps_first_due(self):
        due=self.task_row()["next_due"]
        self.app.save_settings(dict(mode="wechat"))
        self.assertEqual(self.task_row()["next_due"],due)

    def test_self_notification_once_keeps_task_active_until_completed(self):
        delivered=[]
        self.app.notifier=lambda text,title:delivered.append((text,title))
        self.app.save_settings(dict(mode="wechat"))
        due=now().replace(microsecond=0)+timedelta(minutes=1)
        task=self.app.create(dict(title="整理我的实验记录",target_type="self",first_due=iso(due),repeat=False))
        self.app.update(self.task,dict(action="complete"))
        self.app.tick(due);self.app.tick(due+timedelta(hours=10))
        self.assertEqual(delivered,[("整理我的实验记录","待办提醒")])
        self.assertEqual(self.sent,[])
        row=next(t for t in self.app.snapshot()["tasks"] if t["id"]==task)
        self.assertEqual(row["status"],"active")
        self.assertEqual(row["reminder_finished"],1)
        self.assertEqual(self.app.snapshot()["events"][0]["status"],"notified")
        restarted=ReminderApp(self.app.path,notifier=self.app.notifier)
        restarted.tick(due+timedelta(days=1))
        self.assertEqual(len(delivered),1)

    def test_complete_records_history_time_and_reopen_clears_it(self):
        self.app.update(self.task,dict(action="complete"))
        self.assertEqual(self.task_row()["status"],"done")
        self.assertIsNotNone(self.task_row()["completed_at"])
        self.app.update(self.task,dict(action="resume"))
        self.assertEqual(self.task_row()["status"],"active")
        self.assertIsNone(self.task_row()["completed_at"])

    def test_self_notification_waits_when_tray_not_ready(self):
        self.app.save_settings(dict(mode="wechat"))
        due=now()+timedelta(minutes=1)
        task=self.app.create(dict(title="我自己的事",target_type="self",first_due=iso(due),repeat=False))
        self.app.update(self.task,dict(action="complete"))
        self.app.tick(due)
        self.assertEqual(self.app.snapshot()["events"][0]["status"],"retry")
        row=next(t for t in self.app.snapshot()["tasks"] if t["id"]==task)
        self.assertEqual(row["status"],"active")
        self.assertEqual(row["reminder_finished"],0)

    def test_private_once_does_not_repeat_but_explicit_extra_reminder_works(self):
        self.app.update(self.task,dict(action="complete"))
        due=now()+timedelta(minutes=1)
        task=self.app.create(dict(title="交一次报告",target_type="contact",group_name="小王",first_due=iso(due),repeat=False,auto_start=True))
        self.app.tick(due);self.app.tick(due+timedelta(days=1))
        self.assertEqual(len(self.sent),1)
        self.app.dispatch(task,manual=True,at=due+timedelta(minutes=2))
        self.assertEqual(len(self.sent),2)

    def test_first_due_without_year_picker_uses_china_timezone(self):
        data=validate_task(dict(title="我的事",target_type="self",first_due="2026-10-10T17:30:00",repeat=False))
        self.assertEqual(data["first_due"],datetime(2026,10,10,17,30,tzinfo=TZ))


class LauncherTests(unittest.TestCase):
    def test_repeat_launch_opens_existing_service_without_touching_tasks(self):
        for no_browser in (False, True):
            with self.subTest(no_browser=no_browser), patch('sys.argv', ['app.py'] + (['--no-browser'] if no_browser else [])), \
                 patch('app.serve', side_effect=OSError('address in use')), \
                 patch('app.build_opener') as opener, patch('app.ReminderApp') as database, \
                 patch('app.webbrowser.open') as browser:
                opener.return_value.open.return_value.__enter__.return_value = io.StringIO('{"build":"auto-uia-20261010-v2"}')
                main()
                database.assert_not_called()
                if no_browser:
                    browser.assert_not_called()
                else:
                    browser.assert_called_once_with('http://127.0.0.1:8765')

    def test_other_service_on_port_is_not_opened_as_reminder(self):
        with patch('sys.argv', ['app.py']), patch('app.serve', side_effect=OSError('address in use')), \
             patch('app.build_opener') as opener, patch('app.ReminderApp') as database, \
             patch('app.webbrowser.open') as browser:
            opener.return_value.open.return_value.__enter__.return_value = io.StringIO('{"build":"other-service"}')
            main()
            database.assert_not_called()
            browser.assert_not_called()


if __name__ == "__main__":
    unittest.main()
