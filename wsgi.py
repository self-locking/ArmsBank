"""WSGI 入口：gunicorn / Render / Docker 都从这里起。"""
import os

from backend.app import app  # noqa: F401  Flask 实例

# 首次启动自动建基础表（幂等）。数据库还没就绪时不让进程挂掉，
# 可用 ARMSBANK_AUTO_BOOTSTRAP=0 关掉。
if os.environ.get("ARMSBANK_AUTO_BOOTSTRAP", "1") != "0":
    try:
        from backend.bootstrap import bootstrap

        bootstrap(verbose=False)
    except Exception as exc:  # noqa: BLE001
        print("bootstrap skipped:", exc, flush=True)

application = app
