"""复用开源 UIA 驱动，精确核对会话、成员和正文后发送。"""
from __future__ import annotations
import importlib.util
import json
import logging
import os
import sys
import time
import types
import argparse
from pathlib import Path
from runtime_paths import RESOURCE_DIR, DATA_DIR

ROOT = RESOURCE_DIR
SOURCE = ROOT / 'vendor/wechatauto-replica/wechatauto'
CACHE_DIR = DATA_DIR / 'wechat'


def invoke_control(control):
    """调用目标控件自己的动作；不把屏幕坐标当作目标，不点穿遮挡窗口。"""
    legacy = control.GetLegacyIAccessiblePattern()
    if legacy is not None and legacy.DefaultAction:
        legacy.DoDefaultAction()
        return True
    pattern = control.GetInvokePattern()
    if pattern is None:
        raise RuntimeError('目标控件暂时不支持直接调用，未进行鼠标点击')
    pattern.Invoke()
    return True


def load_uia():
    if os.name != 'nt':
        raise RuntimeError('微信发送需要 Windows')
    if 'wechatauto.uia_driver' not in sys.modules:
        package = types.ModuleType('wechatauto')
        package.__path__ = [str(SOURCE)]
        sys.modules['wechatauto'] = package
        logger = types.ModuleType('wechatauto.logger')
        logger.wxlog = logging.getLogger('wechat-reminder')
        sys.modules[logger.__name__] = logger
        for name in ('param', 'rhythm', 'uia_driver'):
            full = 'wechatauto.' + name
            spec = importlib.util.spec_from_file_location(full, SOURCE / (name + '.py'))
            mod = importlib.util.module_from_spec(spec)
            sys.modules[full] = mod
            setattr(package, name, mod)
            spec.loader.exec_module(mod)
        package.param.WxParam.ENABLE_FILE_LOGGER = False
    module = sys.modules['wechatauto.uia_driver']
    cache = CACHE_DIR
    cache.mkdir(parents=True, exist_ok=True)
    module.GATE_CACHE_FILE = str(cache / 'gate-cache.json')
    driver = module.WeChatUIA(timeout=12)
    # 禁止上游随机坐标点击，避免其他窗口覆盖微信时误点聊天记录。
    driver._click_ctrl = lambda control, right=False: invoke_control(control)
    if not driver.ensure_window():
        raise RuntimeError('微信尚未登录或控件接入失败')
    if driver._search_box(driver._win, expand=True) is None:
        driver.ensure_materialized(force=True)
        if not driver.ensure_window() or driver._search_box(driver._win, expand=True) is None:
            raise RuntimeError('暂时无法读取微信搜索控件')
    return driver


def open_target(body, driver):
    guard = body.get('_input_guard', lambda: None)
    guard()
    target, kind = body['group_name'], body.get('target_type', 'group')
    driver._activate(driver._win)
    if driver.current_chat() == target:
        return
    box = driver._search_box(driver._win, expand=True)
    if box is None:
        raise RuntimeError('无法找到微信搜索框')
    box.SetFocus()
    if not driver._set_text(box, target):
        raise RuntimeError('无法填写微信搜索框')
    # 微信异步刷新搜索列表，不能把上一轮的结果当作当前候选。
    time.sleep(.8)
    primary = '群聊' if kind == 'group' else '联系人'
    results = []
    for _ in range(4):
        results = [r for r in driver._collect_results(target)
                   if r['name'] == target and r['section'] in (primary, '最常使用', '最近使用', '功能')]
        if results:
            break
        time.sleep(.3)
    preferred = [r for r in results if r['section'] == primary]
    results = preferred or results
    unique = {r['aid']: r for r in results}
    if len(unique) != 1:
        raise RuntimeError('未找到唯一完整匹配的会话，请检查完整群名或联系人备注名')
    chosen = next(iter(unique.values()))
    # 点真实搜索结果控件，不用回车猜测搜索列表的当前选中项。
    guard()
    driver._click_ctrl(chosen['cell'])
    for _ in range(12):
        if driver.current_chat() == target:
            return
        time.sleep(.15)
    raise RuntimeError('实际打开的微信会话与目标不一致，未发送')


def mention_list(driver):
    pop = driver._win.WindowControl(AutomationId='MentionPopover')
    if not pop.Exists(.4):
        return None
    control = pop.ListControl(AutomationId='chat_mention_list')
    return control if control.Exists(.4) else None


def add_mention(driver, edit, member, prefix="", guard=lambda:None):
    guard()
    edit.SetFocus()
    # 实际按下 @，使微信创建成员选择菜单；纯文本 @ 不是提醒标记。
    edit.SendKeys('@', charMode=False, waitTime=.1)
    time.sleep(.3)
    listing = mention_list(driver)
    if listing is None:
        raise RuntimeError('微信未弹出群成员 @ 菜单')
    guard()
    edit.SendKeys(member, waitTime=.1)
    time.sleep(.4)
    listing = mention_list(driver)
    candidates = [] if listing is None else [c for c in listing.GetChildren() if c.Name == member]
    if len(candidates) != 1:
        raise RuntimeError('没有找到唯一完整匹配的群成员，请核对群昵称')
    guard()
    driver._click_ctrl(candidates[0])
    time.sleep(.3)
    value = driver._read_edit_value(edit)
    if value != prefix + '\ufffc' or mention_list(driver) is not None:
        raise RuntimeError('微信真实 @ 成员标记未生成，未发送')
    return value


class InputBusyError(RuntimeError):
    """用户正在操作或保留草稿；确定未发送，下一轮空闲检测即可重试。"""


def _send_via_uia(body, driver=None, phase=None):
    body = dict(body)
    previous_guard = body.get('_input_guard', lambda: None)
    def guard():
        previous_guard()
        if body.get('_database'):
            import sqlite3
            from urllib.parse import quote
            from contextlib import closing
            with closing(sqlite3.connect('file:' + quote(Path(body['_database']).as_posix(),safe='/:') + '?mode=ro', uri=True)) as db:
                import json
                cfg = json.loads(db.execute('SELECT value FROM settings WHERE id=1').fetchone()[0])
                if not cfg['enabled'] or cfg['mode'] != body.get('_expected_mode','wechat'):
                    raise RuntimeError('自动提醒已暂停或方式已改变，取消未发送操作')
                for task_id, revision in body.get('_task_versions', []):
                    row = db.execute('SELECT status,revision FROM tasks WHERE id=?',(task_id,)).fetchone()
                    if row != ('active',revision):
                        raise RuntimeError('待办已完成、暂停或修改，取消本次未发送操作')
    body['_input_guard'] = guard
    guard()
    driver = driver or load_uia()
    phase = phase if phase is not None else {'attempted': False}
    open_target(body, driver)
    guard = body.get('_input_guard', lambda: None)
    guard()
    target = body['group_name']
    edit = driver._chat_input()
    draft = driver._read_edit_value(edit)
    if draft is None:
        raise RuntimeError('无法读取微信输入框')
    if draft:
        raise InputBusyError('目标会话已有草稿，将等待草稿处理后自动重试')
    if body.get('operation') == 'check':
        return '已核对目标真实控件和空输入框；本次没有发送消息'
    group = body.get('target_type', 'group') == 'group'
    text = body['message']
    owned = ''
    mention_prefix = ''
    try:
        if group:
            # 保留微信富文本 @ 标记，追加正文；SetValue 会抹掉标记。
            owned = '@' + body['member']
            token = ''
            for member in body.get('members', [body['member']]):
                guard()
                if token:
                    edit.SendKeys('{End}{Space}', waitTime=.1)
                    token += ' '
                mention_prefix = token
                owned = token + '@' + member
                token = add_mention(driver, edit, member, token, guard)
                owned = token
            guard()
            old_clipboard = driver._clip_get()
            try:
                driver._clip_set(' ' + text)
                edit.SetFocus()
                edit.SendKeys('{End}{Ctrl}v', waitTime=.1)
                time.sleep(.3)
            finally:
                if not body.get('_input_changed',lambda:False)():
                    driver._clip_set(old_clipboard)
            owned = token + ' ' + text
        else:
            if not driver._set_text(edit, text):
                raise RuntimeError('无法写入微信输入框')
            owned = text
        guard()
        if driver.current_chat() != target or driver._read_edit_value(edit) != owned:
            raise RuntimeError('发送前实际会话或完整正文核对失败')
        edit.SetFocus()
        # 此后任何错误都视为发送状态不明，禁止自动重发。
        guard()
        phase['attempted'] = True
        edit.SendKeys('{Enter}', waitTime=.1)
        time.sleep(.8)
        if driver.current_chat() != target or driver._read_edit_value(edit) != '':
            raise RuntimeError('已按发送键但未确认结果，请核对微信后恢复任务')
        return '微信已执行发送；发送前核对了目标和完整正文' + ('及真实 @ 标记' if group else '') + '，输入框已清空；不代表对方已读'
    except Exception:
        # 仅撤回本次操作精确匹配的草稿；不清空用户内容。
        if not phase['attempted'] and driver.current_chat() == target:
            current = driver._read_edit_value(edit)
            if not body.get('_input_changed', lambda: False)() and current and current in (owned, mention_prefix+'@', '@' + body.get('member', ''), '\ufffc'):
                if group and mention_list(driver) is not None:
                    edit.SendKeys('{Esc}', waitTime=.1)
                driver._set_text(edit, '')
        raise


def send_via_uia(body, driver=None, phase=None):
    if not body.get('_avoid_interruption'):
        if driver is None:
            from interaction_guard import waiting_reason
            reason=waiting_reason(0)
            if reason:raise InputBusyError(reason)
        return _send_via_uia(body, driver, phase)
    from interaction_guard import waiting_reason, InputWatch
    reason = waiting_reason(body.get('_idle_seconds', 1))
    if reason:
        raise InputBusyError(reason)
    watch = InputWatch()
    if not watch.start():
        watch.stop()
        raise RuntimeError('无法检测操作干扰，等待重试')
    import win32gui
    original = win32gui.GetForegroundWindow()
    body = dict(body)
    def guard():
        if watch.changed.is_set():
            raise InputBusyError('检测到你重新操作键鼠，尚未发送，等待空闲')
    body['_input_guard'] = guard
    body['_input_changed'] = watch.changed.is_set
    try:
        guard()
        driver = driver or load_uia()
        return _send_via_uia(body, driver, phase)
    finally:
        if not watch.changed.is_set() and original and win32gui.IsWindow(original):
            try:
                foreground = win32gui.GetForegroundWindow()
                if driver is not None and foreground == driver._win.NativeWindowHandle:
                    win32gui.SetForegroundWindow(original)
            except Exception:
                pass
        watch.stop()


def private_via_uia(body, driver=None):
    return send_via_uia(dict(body, target_type='contact'), driver)


def run(body):
    global CACHE_DIR
    if body.get('_data_dir'):
        CACHE_DIR = Path(body['_data_dir']) / 'wechat'
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    phase = {'attempted': False}
    try:
        detail = send_via_uia(body, phase=phase)
        result = {'ok': True, 'detail': detail}
    except Exception as exc:
        result = {'ok': False, 'error': str(exc), 'retryable': not phase['attempted'], 'deferred':isinstance(exc,InputBusyError) and not phase['attempted']}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    body = json.loads(args.input.read_text(encoding='utf-8') if args.input else sys.stdin.read())
    result = json.dumps(run(body), ensure_ascii=False)
    if args.output:
        args.output.write_text(result, encoding='utf-8')
    else:
        print(result)


if __name__ == '__main__':
    main()
