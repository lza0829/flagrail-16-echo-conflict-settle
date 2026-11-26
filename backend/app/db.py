import os
import sqlite3
import threading
from pathlib import Path

数据目录 = Path(os.environ.get("数据目录", Path(__file__).resolve().parent.parent / "data"))
夹具目录 = Path(os.environ.get("夹具目录", Path(__file__).resolve().parent.parent.parent / "fixtures"))
库路径 = 数据目录 / "灰度轨.db"

# 单 uvicorn 进程内串行化「冲突写 + 裁定物化」，与 SQLite 事务一起保证
# 放量/核证读不到裁定中间态。多 worker 时仍由单条投影行的事务原子性兜底。
写锁 = threading.RLock()


def 连接() -> sqlite3.Connection:
    数据目录.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(库路径, timeout=30.0, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("PRAGMA busy_timeout = 30000")
    return c
