"""部署版本信息：release tag + git SHA + schema 版本。

线上靠 ``/version`` 自报身份，不再靠文件 mtime 反推「跑的是哪一版」。
``version`` / ``git_sha`` 在镜像构建期由 ``Dockerfile.backend`` 用构建参数
（``APP_VERSION`` / ``APP_GIT_SHA``）写入环境变量；本地直接 ``uvicorn`` 起、
没经过 Docker 构建时回退到 ``dev`` / ``unknown``，而不是报错。
"""

from __future__ import annotations

import os

from backend.schemas import SCHEMA_VERSION


def build_version_info() -> dict[str, str]:
    """组装当前进程的版本身份（请求期读取，便于测试 monkeypatch 注入）。"""
    return {
        "version": os.getenv("APP_VERSION", "dev"),
        "git_sha": os.getenv("APP_GIT_SHA", "unknown"),
        "schema_version": SCHEMA_VERSION,
    }


__all__ = ["build_version_info"]
