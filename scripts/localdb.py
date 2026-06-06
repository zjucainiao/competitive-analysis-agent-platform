"""本地持久化数据库（不依赖 Docker / Homebrew）。

用嵌入式 PostgreSQL（pgserver 自带真 postgres 二进制）+ Redis（redislite 附带的
redis-server 二进制）跑一套**常驻、持久化**的数据层；数据写在仓库 ``.localdb/`` 下，
跨重启不丢。``pgserver`` 用 ``cleanup_mode=None`` 让 postgres 在本脚本退出后继续运行。

这是**本地开发用**的零安装方案；部署到服务器仍走 docker-compose.yml 的 pg+redis。

用法：
    .venv/bin/python scripts/localdb.py up       # 起 pg+redis，打印 DSN/URL
    .venv/bin/python scripts/localdb.py status    # 查状态
    .venv/bin/python scripts/localdb.py down       # 停掉（数据保留）
    .venv/bin/python scripts/localdb.py dsn         # 只打印 POSTGRES_DSN / REDIS_URL
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_LOCALDB = _REPO / ".localdb"
_PGDATA = _LOCALDB / "pgdata"
_REDIS_DIR = _LOCALDB / "redis"
_REDIS_PID = _REDIS_DIR / "redis.pid"
_REDIS_PORT = 6379
_REDIS_URL = f"redis://localhost:{_REDIS_PORT}/0"


def _dsn() -> str:
    """启动/复用持久化 postgres，返回 asyncpg DSN。"""
    import pgserver

    _PGDATA.mkdir(parents=True, exist_ok=True)
    # cleanup_mode=None：本进程退出后 postgres 继续常驻（持久化关键）
    srv = pgserver.get_server(_PGDATA, cleanup_mode=None)
    return srv.get_uri().replace("postgresql://", "postgresql+asyncpg://", 1)


def _redis_running() -> bool:
    bin_ = shutil.which("redis-cli") or str(Path(sys.executable).parent / "redis-cli")
    try:
        out = subprocess.run(
            [bin_, "-p", str(_REDIS_PORT), "ping"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return out.stdout.strip().upper() == "PONG"
    except Exception:
        return False


def _start_redis() -> None:
    if _redis_running():
        return
    redis_server = Path(sys.executable).parent / "redis-server"
    if not redis_server.exists():
        raise SystemExit(
            f"找不到 redis-server 二进制：{redis_server}（应随 redislite 安装）"
        )
    _REDIS_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(redis_server),
            "--dir", str(_REDIS_DIR),
            "--port", str(_REDIS_PORT),
            "--appendonly", "yes",
            "--daemonize", "yes",
            "--pidfile", str(_REDIS_PID),
        ],
        check=True,
    )


def cmd_up() -> None:
    dsn = _dsn()
    _start_redis()
    print("✓ 本地持久化数据库已就绪（数据在 .localdb/，重启不丢）\n")
    print(f"POSTGRES_DSN={dsn}")
    print(f"REDIS_URL={_REDIS_URL}")
    print("\n把上面两行写进 .env，并设 STORAGE_MODE=postgres，再重启后端。")


def cmd_dsn() -> None:
    print(f"POSTGRES_DSN={_dsn()}")
    print(f"REDIS_URL={_REDIS_URL}")


def cmd_status() -> None:
    pg_up = _PGDATA.exists() and (_PGDATA / "postmaster.pid").exists()
    print(f"postgres : {'RUNNING' if pg_up else 'stopped'}  ({_PGDATA})")
    print(f"redis    : {'RUNNING' if _redis_running() else 'stopped'}  ({_REDIS_DIR})")


def cmd_down() -> None:
    # 停 postgres
    try:
        import pgserver

        if _PGDATA.exists():
            srv = pgserver.get_server(_PGDATA, cleanup_mode=None)
            srv.cleanup()  # 停服务，保留数据目录
            print("✓ postgres 已停（数据保留在 .localdb/pgdata）")
    except Exception as e:  # noqa: BLE001
        print(f"停 postgres 出错（可能本就没跑）：{e}")
    # 停 redis
    if _REDIS_PID.exists():
        try:
            pid = int(_REDIS_PID.read_text().strip())
            import os
            import signal

            os.kill(pid, signal.SIGTERM)
            print("✓ redis 已停（数据保留在 .localdb/redis）")
        except Exception as e:  # noqa: BLE001
            print(f"停 redis 出错：{e}")


_COMMANDS = {
    "up": cmd_up,
    "dsn": cmd_dsn,
    "status": cmd_status,
    "down": cmd_down,
}


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in _COMMANDS:
        print(__doc__)
        raise SystemExit(2)
    _COMMANDS[sys.argv[1]]()


if __name__ == "__main__":
    main()
