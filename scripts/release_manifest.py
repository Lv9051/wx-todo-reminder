"""公开源码白名单：用户数据、凭据、调试截图与日志不参与分发。"""
from pathlib import Path
FILES=['app.py','v11_app.py','floating_window.py','interaction_guard.py','startup.py','wechat_adapter.py','runtime_paths.py','desktop.py','desktop_runtime.py',
       'start.bat','requirements.txt','requirements-build.txt','setup.ps1','README.md','LICENSE',
       'THIRD_PARTY_NOTICES.md','WXTodoReminder.spec','.gitignore',
       'web/index.html','web/main.js','web/style.css','assets/icon.png','assets/icon.ico','assets/reminder-soft.wav',
       'native_window.py','scripts/build_release.py','scripts/release_manifest.py',
       'vendor/wechatauto-replica/LICENSE']
FILES += ['vendor/wechatauto-replica/wechatauto/'+name for name in ('param.py','rhythm.py','uia_driver.py')]


def source_files(root):
    for name in FILES:
        path=Path(root)/name
        if not path.is_file():raise FileNotFoundError(name)
        yield name,path
