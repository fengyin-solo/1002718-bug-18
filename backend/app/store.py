"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。

写入侧的两条底线都收在这一层：
- ``transaction``：一次业务动作涉及的多张表要么整份落库、要么整份回滚，
  不会留下“单据写好了、箱位却丢了”的半截数据；
- 每张模块表配一把锁：并发动作（例如一边确认绑扎、一边拆除绑扎）被串行化，
  谁先拿到锁谁先落库，后到的动作拿到的一定是最新状态。
"""
from __future__ import annotations

import threading
from copy import deepcopy
from typing import Any, Iterator

from app.seed import SEED_ROWS


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        # 每个模块一把锁；绑扎提交会同时写任务、箱位、台账、流水几张表，
        # 统一挂在业务模块（lashing）的锁上即可。
        self._locks: dict[str, threading.RLock] = {}
        # 每个模块一个自增序号，用作提交凭证与动作凭证，单调递增、永不复用。
        self._sequences: dict[str, int] = {}

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def lock(self, module: str) -> threading.RLock:
        """返回模块对应的锁，同一模块的所有写入必须串行。"""
        if module not in self._locks:
            self._locks[module] = threading.RLock()
        return self._locks[module]

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def next_id(self, module: str) -> int:
        """在锁内分配下一个主键：不允许复用历史 id，重接旧任务也不会顶号。"""
        current = self._sequences.get(module, 0)
        if current == 0:
            current = max((int(row.get("id", 0)) for row in self.rows(module)), default=0)
        current += 1
        self._sequences[module] = current
        return current

    def transaction(self, *modules: str) -> "Transaction":
        """开启一次覆盖若干模块表的原子提交。

        事务内对 ``rows`` 的改动都落在深拷贝快照上，``commit`` 时整份替换，
        任何一步抛异常都会丢弃快照，正式表里不留半点痕迹。
        """
        return Transaction(self, modules)

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


class Transaction:
    """多表原子写：快照上改，提交时一次性整体替换。"""

    def __init__(self, store: Store, modules: tuple[str, ...]) -> None:
        self._store = store
        self._modules = modules or ("lashing",)
        self._locks: list[threading.RLock] = []
        self._snapshot: dict[str, list[dict[str, Any]]] = {}
        self._committed = False

    def __enter__(self) -> "Transaction":
        # 排序后取锁，避免多事务交叉取锁时互相等待。
        self._locks = [store.lock(name) for name in sorted(set(self._modules))]
        for lock in self._locks:
            lock.acquire()
        try:
            self._snapshot = {
                name: deepcopy(self._store.rows(name)) for name in self._modules
            }
        except BaseException:
            self._release()
            raise
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        try:
            if exc_type is None and not self._committed:
                # with 块正常结束但忘记 commit：视同失败，丢弃快照。
                return
        finally:
            self._release()

    def _release(self) -> None:
        for lock in reversed(self._locks):
            lock.release()
        self._locks = []

    def rows(self, module: str) -> list[dict[str, Any]]:
        if module not in self._snapshot:
            raise KeyError(f"模块 {module} 不在本次原子提交范围内")
        return self._snapshot[module]

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def find_by(self, module: str, key: str, value: Any) -> dict[str, Any] | None:
        for row in self.rows(module):
            if row.get(key) == value:
                return row
        return None

    def next_id(self, module: str) -> int:
        return self._store.next_id(module)

    def commit(self) -> None:
        """整份快照落库：任务、箱位、台账、流水在这一次赋值里一起可见。"""
        for name, rows in self._snapshot.items():
            self._store._tables[name] = rows
        self._committed = True


store = Store()
