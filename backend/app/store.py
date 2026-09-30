"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。

绑扎加固要求"要么全落、要么不留痕"，因此这里补了最小可用的事务支持：
- transaction(*modules) 对涉及的表做快照，中途抛异常就整体回滚，正常退出才提交；
- 一把可重入锁串行化所有写操作，绑扎与拆绑并发时只有一方先落库；
- idempotency 表按 (模块, request_id) 记录首次结果，同一请求重放只记一次。
"""
from __future__ import annotations

import copy
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from app.seed import SEED_ROWS

IDEMPOTENCY_MODULE = "_idempotency"


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        self._lock = threading.RLock()
        # 当前活动事务所持有的快照：{模块: 提交前的深拷贝}
        self._snapshot: dict[str, list[dict[str, Any]]] | None = None

    # ------------------------------------------------------------------ 基础读
    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def find_by_field(self, module: str, field: str, value: Any) -> dict[str, Any] | None:
        """按业务键（例如绑扎编号）定位一条记录，供幂等接续使用。"""
        if value is None:
            return None
        target = str(value)
        for row in self.rows(module):
            if str(row.get(field, "")) == target:
                return row
        return None

    # ------------------------------------------------------------------ 事务写
    @contextmanager
    def transaction(self, *modules: str) -> Iterator[None]:
        """对指定模块开启原子区间：异常回滚，正常退出提交。

        锁是可重入的，嵌套调用复用同一个事务；只有最外层负责快照与提交，
        保证"单据、箱位、台账"要么一起落库，要么一起不留痕。
        """
        with self._lock:
            outer = self._snapshot is None
            snapshot: dict[str, list[dict[str, Any]]] = {}
            if outer:
                touched = set(modules) | {IDEMPOTENCY_MODULE}
                for name in touched:
                    snapshot[name] = copy.deepcopy(self.rows(name))
                self._snapshot = snapshot
            try:
                yield
            except Exception:
                if outer:
                    for name, rows in snapshot.items():
                        self._tables[name] = copy.deepcopy(rows)
                raise
            finally:
                if outer:
                    self._snapshot = None

    def insert(self, module: str, row: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.rows(module).append(row)
            return row

    def upsert(self, module: str, row: dict[str, Any]) -> dict[str, Any]:
        """按 id 原位替换，保证列表与详情读到的是同一条对象（同一 id 不新增）。"""
        with self._lock:
            rows = self.rows(module)
            entry_id = int(row.get("id", 0))
            for index, existing in enumerate(rows):
                if int(existing.get("id", 0)) == entry_id:
                    rows[index] = row
                    return row
            rows.append(row)
            return row

    def delete(self, module: str, entry_id: int) -> bool:
        with self._lock:
            rows = self.rows(module)
            for index, row in enumerate(rows):
                if int(row.get("id", 0)) == entry_id:
                    rows.pop(index)
                    return True
            return False

    def next_id(self, module: str) -> int:
        with self._lock:
            return max((int(row.get("id", 0)) for row in self.rows(module)), default=0) + 1

    # ------------------------------------------------------------------ 幂等
    def idempotent_get(self, module: str, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self.find_by_field(IDEMPOTENCY_MODULE, "request_id", f"{module}:{request_id}")
            return copy.deepcopy(record["result"]) if record else None

    def idempotent_save(self, module: str, request_id: str, result: dict[str, Any]) -> None:
        with self._lock:
            key = f"{module}:{request_id}"
            if self.find_by_field(IDEMPOTENCY_MODULE, "request_id", key) is None:
                self.rows(IDEMPOTENCY_MODULE).append({
                    "id": self.next_id(IDEMPOTENCY_MODULE),
                    "module": module,
                    "request_id": key,
                    "result": copy.deepcopy(result),
                })

    # ------------------------------------------------------------------ 看板
    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            if name.startswith("_"):
                continue
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


store = Store()
