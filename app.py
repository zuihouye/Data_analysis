"""应用入口。

用法：
    python app.py                        # 用 .env 里的 DB_NAME（默认 taobao_user）
    python app.py --db taobao_rfm_demo   # 临时切到演示库，不改 .env

支持 --db 的原因：PowerShell 里 `$env:DB_NAME='x'; python app.py` 只在同一个
终端会话生效，用 PyCharm「运行」按钮 / 其他 shell 启动时环境变量带不进去，
会静默连回 .env 里的库；写成参数就不依赖 shell 了。
"""
import os
import sys


def _apply_argv():
    """解析 --db / --db=xxx，在 import config 之前写入环境变量。"""
    argv = sys.argv[1:]
    for i, arg in enumerate(argv):
        if arg == '--db' and i + 1 < len(argv):
            os.environ['DB_NAME'] = argv[i + 1]
        elif arg.startswith('--db=') and len(arg) > 5:
            os.environ['DB_NAME'] = arg[5:]


_apply_argv()

from views import create_app  # noqa: E402  （必须在设置 DB_NAME 之后导入）

app = create_app()

if __name__ == '__main__':
    print(f'[*] 数据库: {os.getenv("DB_NAME") or "taobao_user"}')
    print('[*] 地址:   http://127.0.0.1:5000')
    app.run()
