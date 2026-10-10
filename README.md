# WX 待办提醒 v1.0.0

一个本地运行的 Windows 待办软件：提醒自己，也在微信里提醒别人。纯脚本调度，无 AI API 或 token 消耗。

## 下载与使用

从 GitHub Releases 下载 `WXTodoReminder-v1.0.0-Windows-x64.zip`，完整解压，再双击 `WXTodoReminder.exe`。无需安装 Python。Windows 10/11 64位；应用窗口优先使用系统 Microsoft Edge，无 Edge 时使用默认浏览器。

1. 新建待办，选择“提醒自己 · 桌面通知”“微信私聊”“群内 @”或“仅记录”。
2. 开始时间默认当前时间；需要晚些提醒时，直接修改日期和时间。
3. 可选择只提醒一次，或未完成时按间隔重复提醒。保存后自动执行。
4. 点击完成自动移入历史待办；可撤销完成或从历史重新开启。

关闭应用窗口后，程序继续驻留系统托盘。右键绿色勾图标可打开清单、暂停全部提醒或退出。重启会保留计划。电脑休眠/关机或退出软件时无法执行。

桌面通知不需要微信。Windows 的“请勿打扰”或通知设置可能把通知收进通知中心，可在设置页点“弹一条测试通知”检查。

微信提醒需要已登录的 Windows 微信以及解锁桌面。已在 **微信 4.1.13.65** 验证私聊与群内真正 @；其他版本请先用自己允许的测试对象验证。填写完整且唯一的联系人备注名、群名与 @ 菜单中的成员名称。无需置顶。发送时会临时将微信切到目标会话。

用户草稿不会被覆盖。确定尚未发送的临时错误一分钟后自动重试；已经按发送键但状态不明、子进程超时或发送途中退出时暂停该任务，核对后恢复以免重复发送。“微信已执行发送”不代表对方已读。

## 数据与隐私

数据保存在软件旁的 `data/reminders.db`，可复制整个 data 文件夹备份。不要把软件放在没有写权限的目录。分发的新安装包从空清单开始，不含开发者个人待办、联系人、聊天记录、账号凭据或日志。只监听本机 127.0.0.1，无云同步；当前不自动解析聊天或同步钉钉待办。

微信驱动依赖 UI Automation 与 Qt 无障碍控件，会按上游实现启用微信运行时的无障碍状态以暴露控件树；不读取或解密微信聊天数据库。

## 从源码启动

推荐 Windows + Python 3.12 64位：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe desktop.py
```

或安装依赖后双击 `start.bat`。重复启动打开已有页面，不重置任务。开发时可运行 `app.py --no-browser --data work/test.db --port 8770`，这个无托盘服务不会弹桌面通知。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe scripts/build_release.py
```

构建输出在 `dist/`。Windows 可执行文件需在 Windows 上构建。

## 来源与反馈

页面、待办调度、通知和软件入口为本项目代码。微信真实控件驱动复用 [wechatauto-replica](https://github.com/fanyuantaier/wechatauto-replica) 的 Apache-2.0 源码，以控件默认动作选择会话和成员，不按屏幕坐标点击，也不扫描其他聊天记录窗口。许可证和依赖来源见 `THIRD_PARTY_NOTICES.md`。

欢迎在 GitHub Issues 提交使用反馈：操作步骤、微信版本、预期结果、实际结果。提供截图前可先遮住联系人和聊天内容。
