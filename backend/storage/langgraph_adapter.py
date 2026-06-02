"""把 `CheckpointerProtocol` 适配成 LangGraph 的 `BaseCheckpointSaver`。

storage 层不硬依赖 langgraph（编排器选型不该污染存储抽象），所以本模块的
`import langgraph` 在函数体内做。Orchestrator 真接 langgraph 时调用：

    from backend.storage import build_storage
    from backend.storage.langgraph_adapter import to_langgraph_saver

    storage = build_storage(mode="postgres", ...)
    saver = to_langgraph_saver(storage.checkpointer)
    graph = builder.compile(checkpointer=saver)

未安装 langgraph 时调用 `to_langgraph_saver()` 会抛 `ImportError`，
storage 层其他部分仍可正常使用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .protocols import CheckpointerProtocol

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver  # type: ignore


def to_langgraph_saver(impl: CheckpointerProtocol) -> "BaseCheckpointSaver":
    """把 `CheckpointerProtocol` 实现包装成 langgraph 的 `BaseCheckpointSaver`。

    思路：动态子类化 `BaseCheckpointSaver`，把 a*-prefixed 方法委托给 impl。
    同步 `get_tuple` / `put` / `list` / `put_writes` 用 `asyncio.run` 兜底
    （langgraph 在 async context 下会优先调 a* 版本，不会触发同步路径）。
    """
    try:
        from langgraph.checkpoint.base import BaseCheckpointSaver  # type: ignore
    except ImportError as e:
        raise ImportError(
            "langgraph is not installed; install with `pip install langgraph>=0.2`"
        ) from e

    import asyncio

    class _Adapter(BaseCheckpointSaver):  # type: ignore[misc, valid-type]
        def __init__(self, inner: CheckpointerProtocol) -> None:
            super().__init__()
            self._inner = inner

        async def aget_tuple(self, config: Any) -> Any:
            return await self._inner.aget_tuple(config)

        async def aput(
            self,
            config: Any,
            checkpoint: Any,
            metadata: Any,
            new_versions: Any,
        ) -> Any:
            return await self._inner.aput(config, checkpoint, metadata, new_versions)

        def alist(
            self,
            config: Any,
            *,
            filter: Any = None,
            before: Any = None,
            limit: int | None = None,
        ) -> Any:
            # langgraph 0.2 alist 签名带 filter；我们忽略（v1 不支持过滤）
            return self._inner.alist(config, before=before, limit=limit)

        async def aput_writes(
            self,
            config: Any,
            writes: Any,
            task_id: str,
        ) -> None:
            await self._inner.aput_writes(config, writes, task_id)

        # 同步路径：兜底用 asyncio.run，正常 LangGraph async graph 不会走这
        def get_tuple(self, config: Any) -> Any:
            return asyncio.run(self._inner.aget_tuple(config))

        def put(
            self,
            config: Any,
            checkpoint: Any,
            metadata: Any,
            new_versions: Any,
        ) -> Any:
            return asyncio.run(
                self._inner.aput(config, checkpoint, metadata, new_versions)
            )

        def list(
            self,
            config: Any,
            *,
            filter: Any = None,
            before: Any = None,
            limit: int | None = None,
        ) -> Any:
            async def _collect() -> list:
                out = []
                async for t in self._inner.alist(config, before=before, limit=limit):
                    out.append(t)
                return out

            return iter(asyncio.run(_collect()))

        def put_writes(self, config: Any, writes: Any, task_id: str) -> None:
            asyncio.run(self._inner.aput_writes(config, writes, task_id))

    return _Adapter(impl)


__all__ = ["to_langgraph_saver"]
