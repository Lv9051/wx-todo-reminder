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
    if getattr(sys, 'frozen', False):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        stream = (DATA_DIR / 'runtime.log').open('a', encoding='utf-8', buffering=1)
        sys.stdout = stream
        sys.stderr = stream
    from app import main as launch
    launch(desktop=True)


if __name__ == '__main__':
    main()
