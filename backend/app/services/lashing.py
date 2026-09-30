"""绑扎加固业务规则：原子提交、幂等接续、版本冲突与状态流转都收在这里。

设计要点对应现场诉求：
- submit_entry：一次事务同时落"单据 + 箱位 + 台账"，中途失败整体回滚，要么全落要么不留痕；
- 业务键「绑扎编号」+ 请求键 request_id 双层幂等：断网重提接回原单、不产生第二条，
  且原单已填的绑扎材料不会被空值冲掉；
- 每条单据带 version：绑扎与拆绑并发时先落库者为准，后到者不覆盖、改为重算返回；
- 拆除绑扎按开始/完成时间补全绑扎耗时；
- 已绑箱数由单据上的 箱量 汇总，单据重算后看板跟着重算，同一张单重复提交只计一次。
"""
from __future__ import annotations

import re
import time
from typing import Any

from app.store import store

MODULE = "lashing"
SLOT_MODULE = "lashing_slot"
LEDGER_MODULE = "lashing_ledger"

REQUIRED_FIELDS = ["绑扎编号", "对应船舶", "箱位范围"]
DETAIL_FIELDS = ["绑扎方式", "绑扎班组", "绑扎耗时", "箱量"]
# 重提时这些字段只补缺、不覆盖：核心是保住已填的绑扎材料。
PROTECTED_FIELDS = ["绑扎材料"]
STATUS_ORDER = ["待绑扎", "绑扎中", "已绑扎", "已拆除"]
STATUS_WAITING = "待绑扎"
STATUS_WORKING = "绑扎中"
STATUS_DONE = "已绑扎"
STATUS_REMOVED = "已拆除"
ACTION_RULES = {"开始绑扎": STATUS_WORKING, "确认绑扎": STATUS_DONE, "拆除绑扎": STATUS_REMOVED}
NEGATIVE_ACTIONS: list[str] = []

SLOT_SPLIT_RE = re.compile(r"[,，;；、\s]+")
COUNT_RE = re.compile(r"(\d+)")


def _now_ts() -> float:
    return time.time()


def _clean(value: Any) -> str:
    return str(value if value is not None else "").strip()


def parse_container_count(entry: dict[str, Any]) -> int:
    """解析一张绑扎单据覆盖的箱数：优先取显式箱量，否则按箱位范围解析，兜底 1。"""
    raw = entry.get("箱量")
    if raw not in (None, ""):
        match = COUNT_RE.search(str(raw))
        if match and int(match.group(1)) > 0:
            return int(match.group(1))
    scope = _clean(entry.get("箱位范围"))
    if scope:
        parts = [part for part in SLOT_SPLIT_RE.split(scope) if part.strip()]
        if len(parts) > 1:
            return len(parts)
    return 1


class LashingService:
    # ------------------------------------------------------------------ 读
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = [self._present(row) for row in store.rows(MODULE)]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("绑扎编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self._present(entry) if entry else None

    def board_summary(self) -> dict[str, Any]:
        """绑扎看板：数量随单据实时重算，重复/半成品单据不会被重复计数。"""
        rows = store.rows(MODULE)
        summary = {
            STATUS_WAITING: 0,
            STATUS_WORKING: 0,
            STATUS_DONE: 0,
            STATUS_REMOVED: 0,
        }
        lashed_containers = 0
        for row in rows:
            status = str(row.get("status") or STATUS_WAITING)
            summary[status] = summary.get(status, 0) + 1
            if status == STATUS_DONE:
                lashed_containers += parse_container_count(row)
        return {
            "待绑扎任务": summary[STATUS_WAITING],
            "绑扎中任务": summary[STATUS_WORKING],
            "已绑扎任务": summary[STATUS_DONE],
            "已拆绑任务": summary[STATUS_REMOVED],
            "已绑箱数": lashed_containers,
        }

    # ------------------------------------------------------------------ 登记（兼容旧入口，同样走原子事务）
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not _clean(values.get(field))]
        if missing:
            return None, missing
        with store.transaction(MODULE):
            code = _clean(values.get("绑扎编号"))
            existing = store.find_by_field(MODULE, "绑扎编号", code)
            if existing is not None:
                # 同编号登记即重提：接回原单，不新建第二条。
                self._merge_fields(existing, values)
                return self._present(existing), []
            entry: dict[str, Any] = {"id": store.next_id(MODULE)}
            entry.update({field: _clean(values.get(field)) for field in REQUIRED_FIELDS})
            entry.update({field: _clean(values.get(field)) for field in DETAIL_FIELDS if _clean(values.get(field))})
            entry["status"] = STATUS_WAITING
            entry["version"] = 0
            entry["pending"] = True
            entry["abnormal"] = False
            store.insert(MODULE, entry)
            return self._present(entry), []

    # ------------------------------------------------------------------ 提交（核心）
    def submit_entry(self, values: dict[str, Any], request_id: str | None) -> tuple[dict[str, Any], str]:
        """原子完成一次绑扎提交；返回 (单据, 说明)。同编号/同请求重放都接回同一条。"""
        missing = [field for field in REQUIRED_FIELDS if not _clean(values.get(field))]
        if missing:
            raise ValueError(f"缺少必填字段：{'、'.join(missing)}")

        request_id = _clean(request_id)
        if request_id:
            cached = store.idempotent_get(MODULE, request_id)
            if cached is not None:
                return cached, "该绑扎提交已处理过，直接返回首次结果，未重复记单"

        code = _clean(values.get("绑扎编号"))
        with store.transaction(MODULE, SLOT_MODULE, LEDGER_MODULE):
            entry = store.find_by_field(MODULE, "绑扎编号", code)
            if entry is None:
                # 前半段没存成：这里就是首次落库，单据/箱位/台账一起落。
                entry = {"id": store.next_id(MODULE)}
                entry.update({field: _clean(values.get(field)) for field in REQUIRED_FIELDS})
                entry["status"] = STATUS_WAITING
                entry["version"] = 0
                entry["pending"] = True
                entry["abnormal"] = False
                store.insert(MODULE, entry)

            current_status = str(entry.get("status") or STATUS_WAITING)
            if current_status == STATUS_REMOVED:
                # 拆绑已先落库：以先落库为准，拒绝把已拆单据重新绑回去。
                present = self._present(entry)
                if request_id:
                    store.idempotent_save(MODULE, request_id, present)
                return present, "该单据已完成拆绑并先落库，本次绑扎提交不覆盖既有记录"

            # 接续：只补字段，不覆盖已有绑扎材料等已填内容（断线那段从断掉处接着走）。
            self._merge_fields(entry, values)

            started_at = entry.get("started_at") or _now_ts()
            entry["started_at"] = started_at
            entry["status"] = STATUS_DONE
            entry["finished_at"] = _now_ts()
            entry["绑扎耗时"] = self._format_duration(started_at, entry["finished_at"])
            entry["pending"] = False
            entry["abnormal"] = False
            entry["version"] = int(entry.get("version", 0)) + 1
            store.upsert(MODULE, entry)

            self._rebuild_slots(entry)
            self._rebuild_ledger(entry)

            present = self._present(entry)
            if request_id:
                store.idempotent_save(MODULE, request_id, present)
            return present, "绑扎提交已一次性落库（单据、箱位、台账一致）"

    # ------------------------------------------------------------------ 动作（开始/确认/拆除）
    def run_action(
        self,
        entry_id: int,
        action: str,
        *,
        expected_version: int | None = None,
        values: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于绑扎加固可执行范围"
        target = ACTION_RULES[action]
        with store.transaction(MODULE, SLOT_MODULE, LEDGER_MODULE):
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"绑扎任务 {entry_id} 不存在或已归档"

            # 乐观并发：绑扎/拆绑冲突时，先落库的为准，后到者不覆盖。
            current_version = int(entry.get("version", 0))
            if expected_version is not None and expected_version != current_version:
                self._recompute(entry)
                return self._present(entry), (
                    f"该单据已被其他人先处理（当前版本 {current_version}），"
                    "以先落库的为准并已按现状重算，本次动作未覆盖"
                )

            current_status = str(entry.get("status") or STATUS_WAITING)
            if values:
                self._merge_fields(entry, values)

            if action == "开始绑扎":
                if current_status in (STATUS_DONE, STATUS_REMOVED):
                    self._recompute(entry)
                    return self._present(entry), f"单据已是{current_status}，无需重复开始，已按现状重算"
                entry.setdefault("started_at", _now_ts())
                entry["status"] = STATUS_WORKING
                entry["pending"] = True
            elif action == "确认绑扎":
                if current_status == STATUS_REMOVED:
                    self._recompute(entry)
                    return self._present(entry), "单据已拆除，确认绑扎不覆盖，以先落库的拆绑为准"
                entry.setdefault("started_at", _now_ts())
                entry["finished_at"] = _now_ts()
                entry["绑扎耗时"] = self._format_duration(entry["started_at"], entry["finished_at"])
                entry["status"] = STATUS_DONE
                entry["pending"] = False
                self._rebuild_slots(entry)
            elif action == "拆除绑扎":
                if current_status != STATUS_DONE:
                    self._recompute(entry)
                    return self._present(entry), "仅已绑扎的单据可以拆除，当前状态未变更"
                entry["removed_at"] = _now_ts()
                entry["绑扎耗时"] = self._format_duration(entry.get("started_at"), entry["removed_at"])
                entry["status"] = STATUS_REMOVED
                entry["pending"] = False
                self._release_slots(entry)

            entry["abnormal"] = action in NEGATIVE_ACTIONS
            entry["version"] = current_version + 1
            store.upsert(MODULE, entry)
            self._rebuild_ledger(entry)
            return self._present(entry), f"绑扎任务已{action}"

    # ------------------------------------------------------------------ 内部规则
    def _merge_fields(self, entry: dict[str, Any], values: dict[str, Any]) -> None:
        """把提交内容接续到原单。

        普通字段以本次提交为准（允许修正船舶、箱位范围等）；绑扎材料等受保护字段
        只在原值为空时补入，断线重提/换人重提都不会冲掉已填材料。
        """
        updatable = REQUIRED_FIELDS + DETAIL_FIELDS
        for field in updatable:
            incoming = _clean(values.get(field))
            if incoming:
                entry[field] = incoming
        for field in PROTECTED_FIELDS:
            incoming = _clean(values.get(field))
            if incoming and not _clean(entry.get(field)):
                entry[field] = incoming

    def _recompute(self, entry: dict[str, Any]) -> None:
        """按现状重算已有绑扎记录的派生字段：耗时、箱量、箱位、台账。"""
        started = entry.get("started_at")
        finished = entry.get("finished_at") or entry.get("removed_at")
        status = str(entry.get("status") or STATUS_WAITING)
        if started and finished and status in (STATUS_DONE, STATUS_REMOVED):
            entry["绑扎耗时"] = self._format_duration(started, finished)
        if status == STATUS_DONE:
            self._rebuild_slots(entry)
        elif status == STATUS_REMOVED:
            self._release_slots(entry)
        self._rebuild_ledger(entry)

    def _rebuild_slots(self, entry: dict[str, Any]) -> None:
        """重算并占用本单据覆盖的箱位；箱位范围整段落库，列表与详情按同一来源读。"""
        scopes = [part for part in SLOT_SPLIT_RE.split(_clean(entry.get("箱位范围"))) if part] or [_clean(entry.get("箱位范围"))]
        for scope in scopes:
            slot = store.find_by_field(SLOT_MODULE, "箱位编号", scope)
            if slot is None:
                slot = {"id": store.next_id(SLOT_MODULE), "箱位编号": scope}
                store.insert(SLOT_MODULE, slot)
            slot["关联绑扎编号"] = entry["绑扎编号"]
            slot["箱位状态"] = "绑扎占用"
            slot["占用版本"] = int(entry.get("version", 0))
            store.upsert(SLOT_MODULE, slot)

    def _release_slots(self, entry: dict[str, Any]) -> None:
        code = entry.get("绑扎编号")
        for slot in list(store.rows(SLOT_MODULE)):
            if slot.get("关联绑扎编号") == code:
                slot["箱位状态"] = "已释放"
                slot["占用版本"] = int(entry.get("version", 0))
                store.upsert(SLOT_MODULE, slot)

    def _rebuild_ledger(self, entry: dict[str, Any]) -> None:
        """重算绑扎台账：单据与台账始终同状态，不允许台账停在绑扎中。"""
        record = store.find_by_field(LEDGER_MODULE, "绑扎编号", entry.get("绑扎编号"))
        if record is None:
            record = {"id": store.next_id(LEDGER_MODULE), "绑扎编号": entry.get("绑扎编号")}
            store.insert(LEDGER_MODULE, record)
        record["对应船舶"] = entry.get("对应船舶")
        record["箱位范围"] = entry.get("箱位范围")
        record["绑扎状态"] = entry.get("status")
        record["绑扎耗时"] = entry.get("绑扎耗时")
        record["绑扎材料"] = entry.get("绑扎材料")
        record["箱量"] = parse_container_count(entry)
        record["版本"] = int(entry.get("version", 0))
        store.upsert(LEDGER_MODULE, record)

    @staticmethod
    def _format_duration(started_at: Any, finished_at: Any) -> str:
        """拆除时补全绑扎耗时：返回分钟数口径，无法计算时回退占位。"""
        try:
            minutes = max(int(round((float(finished_at) - float(started_at)) / 60)), 0)
        except (TypeError, ValueError):
            return "—"
        return f"{minutes} 分钟"

    def _present(self, entry: dict[str, Any]) -> dict[str, Any]:
        """对外投影：列表与详情共用同一份字段口径（绑扎状态以 status 为准）。"""
        result = dict(entry)
        result["绑扎状态"] = entry.get("status")
        result["箱量"] = parse_container_count(entry)
        return result
