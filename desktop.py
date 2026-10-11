"""软件入口；打包微信子进程使用文件通信，避免隐藏窗口没有标准输入输出。"""
import sys
from runtime_paths import DATA_DIR


def main():
    if sys.stdout is None or sys.stderr is None:
        import io
        if sys.stdout is None: sys.stdout = io.StringIO()
        if sys.stderr is None: sys.stderr = io.StringIO()
    if '--wechat-adapter' in sys.argv:
        sys.argv.remove('--wechat-adapter')
        from wechat_adapter import main as worker
        worker()
        return
    if '--desktop-window' in sys.argv:
        from native_window import run
        index=sys.argv.index('--desktop-window')
        try:
            run(sys.argv[index+1],sys.argv[index+2])
        except Exception as exc:
            from pathlib import Path
            folder=Path(sys.argv[index+2])
            folder.mkdir(parents=True,exist_ok=True)
            import traceback
            (folder/'window-error.log').write_text(traceback.format_exc(),encoding='utf-8')
            import ctypes
            ctypes.windll.user32.MessageBoxW(0,'桌面窗口未能启动。请检查 Microsoft WebView2 Runtime 是否已安装。\n错误详情保存在数据目录的 window-error.log。','强芯待办',0x10)
        return
    if getattr(sys, 'frozen', False):
        from pathlib import Path
        from runtime_paths import APP_DIR
        log_dir = DATA_DIR
        if '--portable' in sys.argv:
            log_dir = APP_DIR / 'data'
        if '--data' in sys.argv:
            log_dir = Path(sys.argv[sys.argv.index('--data')+1]).resolve().parent
        log_dir.mkdir(parents=True, exist_ok=True)
        stream = (log_dir / 'runtime.log').open('a', encoding='utf-8', buffering=1)
        sys.stdout = stream
        sys.stderr = stream
    from app import main as launch
    launch(desktop=True)


if __name__ == '__main__':
    main()
