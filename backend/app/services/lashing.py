"""绑扎加固业务规则。

同一张绑扎单据的提交要同时落四处：任务单据、箱位明细、绑扎台账、动作流水。
历史上这四处是分开写的，网络一断就只落前半截；再加上提交没有业务唯一约束，
同一条任务换个人重提又新建一条——两个毛病同根：**写入非原子、提交不幂等**。

这里的对策：

- ``submit`` 走两阶段：``prepare`` 只在暂存区（lashing_sessions）留凭证，
  ``commit`` 在一个事务里把四张表整份落库，任何一步失败整份回滚，正式表不留痕；
  再提交时凭原 token 或绑扎编号接回原来那条，已填的绑扎材料跟着凭证保留。
- ``run_action`` 在模块锁内执行，先落库者改状态、记流水；冲突动作（如已经
  拆除后又来确认绑扎）直接被拒，不做任何写入，并回头重算全部已有绑扎记录。
- 箱位范围永远由箱位明细投影得出，列表和详情走同一个序列化函数，
  看板的已绑箱数也由明细实时汇总，单据重复提交不会重复计数。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "lashing"
SLOT_MODULE = "lashing_slots"
SESSION_MODULE = "lashing_sessions"
EVENT_MODULE = "lashing_events"

REQUIRED_FIELDS = ["绑扎编号", "对应船舶", "箱位范围"]
EDITABLE_FIELDS = ["绑扎编号", "对应船舶", "箱位范围", "绑扎方式", "绑扎材料", "绑扎班组"]
STATUS_ORDER = ["待绑扎", "绑扎中", "已绑扎", "已拆除"]
# 动作 -> 目标状态；以及在哪些状态下执行才算合法。
ACTION_RULES: dict[str, dict[str, Any]] = {
    "开始绑扎": {"target": "绑扎中", "allowed": {"待绑扎"}, "time_field": "started_at"},
    "确认绑扎": {"target": "已绑扎", "allowed": {"绑扎中"}, "time_field": "lashed_at"},
    "拆除绑扎": {"target": "已拆除", "allowed": {"绑扎中", "已绑扎"}, "time_field": "removed_at"},
}
SLOT_STATUS_BY_ACTION = {"开始绑扎": "绑扎中", "确认绑扎": "已绑扎", "拆除绑扎": "已拆除"}

# 测试用故障注入点：'prepare' 模拟暂存已落、响应丢失；'commit' 模拟正式落库前断开。
fail_point: str | None = None


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_slots(raw: Any) -> list[str]:
    """把箱位范围文本拆成去重后的箱位列表。

    兼容逗号（中英文）、分号、空白、换行分隔，输出顺序与录入一致。
    """
    text = str(raw or "").replace("，", ",").replace("；", ",").replace(";", ",")
    slots: list[str] = []
    for chunk in text.replace("\n", ",").replace("\r", ",").split(","):
        slot_no = chunk.strip()
        if slot_no and slot_no not in slots:
            slots.append(slot_no)
    return slots


def join_slots(slots: list[dict[str, Any]]) -> str:
    """箱位范围的唯一投影口径：列表、详情、导出都从这里取。"""
    return ",".join(str(row.get("slot_no", "")) for row in slots)


def _events_of(tx, task_id: int) -> list[dict[str, Any]]:
    return [row for row in tx.rows(EVENT_MODULE) if int(row.get("task_id", 0)) == task_id]


def _recompute(tx, task: dict[str, Any]) -> None:
    """按箱位明细重算单条任务的派生字段，单据改了这里会被统一纠正。"""
    slots = [row for row in tx.rows(SLOT_MODULE) if int(row.get("task_id", 0)) == int(task["id"])]
    slots.sort(key=lambda row: str(row.get("slot_no", "")))
    status = str(task.get("status", STATUS_ORDER[0]))
    task["箱位范围"] = join_slots(slots)
    task["总箱数"] = len(slots)
    task["已绑箱数"] = sum(1 for row in slots if row.get("status") == "已绑扎")
    task["绑扎状态"] = status
    task["pending"] = status != STATUS_ORDER[-1]
    task["abnormal"] = False


def _recompute_all(tx) -> None:
    for task in tx.rows(MODULE):
        _recompute(tx, task)


def _serialize(task: dict[str, Any], tx=None) -> dict[str, Any]:
    """列表与详情共用的序列化：保证两处看到的箱位范围、计数完全一致。"""
    view = dict(task)
    if tx is not None:
        slots = [dict(row) for row in tx.rows(SLOT_MODULE)
                 if int(row.get("task_id", 0)) == int(task["id"])]
        slots.sort(key=lambda row: str(row.get("slot_no", "")))
        view["箱位范围"] = join_slots(slots)
        view["总箱数"] = len(slots)
        view["已绑箱数"] = sum(1 for row in slots if row.get("status") == "已绑扎")
    return view


class LashingService:
    # ---- 查询 ----------------------------------------------------------------

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        with store.lock(MODULE):
            rows = list(store.rows(MODULE))
            if keyword:
                rows = [row for row in rows if keyword in str(row.get("绑扎编号", ""))]
            if status:
                rows = [row for row in rows if row.get("status") == status]
            total = len(rows)
            start = max(page - 1, 0) * size
            page_rows = rows[start:start + size]
            # 箱位范围统一按明细投影，绝不直接相信单据上冗余的那份文本。
            return [self._view_from_store(row) for row in page_rows], total

    def _view_from_store(self, task: dict[str, Any]) -> dict[str, Any]:
        slots = [row for row in store.rows(SLOT_MODULE)
                 if int(row.get("task_id", 0)) == int(task["id"])]
        slots.sort(key=lambda row: str(row.get("slot_no", "")))
        view = dict(task)
        view["箱位范围"] = join_slots(slots)
        view["总箱数"] = len(slots)
        view["已绑箱数"] = sum(1 for row in slots if row.get("status") == "已绑扎")
        return view

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        with store.lock(MODULE):
            task = store.find(MODULE, entry_id)
            if task is None:
                return None
            slots = [dict(row) for row in store.rows(SLOT_MODULE)
                     if int(row.get("task_id", 0)) == entry_id]
            slots.sort(key=lambda row: str(row.get("slot_no", "")))
            events = [dict(row) for row in store.rows(EVENT_MODULE)
                      if int(row.get("task_id", 0)) == entry_id]
            events.sort(key=lambda row: int(row.get("id", 0)))
            view = self._view_from_store(task)
            view["箱位明细"] = slots
            view["动作流水"] = events
            return view

    def stats(self) -> dict[str, int]:
        """绑扎看板：状态计数与已绑箱数都由当前落库记录实时重算。"""
        with store.lock(MODULE):
            tasks = store.rows(MODULE)
            slots = store.rows(SLOT_MODULE)
            return {
                "待绑扎任务": sum(1 for row in tasks if row.get("status") == "待绑扎"),
                "绑扎中任务": sum(1 for row in tasks if row.get("status") == "绑扎中"),
                "已绑扎任务": sum(1 for row in tasks if row.get("status") == "已绑扎"),
                # 已拆除箱位不再计入，统计口径与单据状态联动。
                "已绑箱数": sum(1 for row in slots if row.get("status") == "已绑扎"),
            }

    # ---- 登记（旧入口，内部并入幂等提交，保证只有一条写库路径） ---------------

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        result = self.submit(values, token=None, operator=values.get("operator"))
        if not result["ok"]:
            return None, [result["message"]]
        return result["entry"], []

    # ---- 两阶段提交 ----------------------------------------------------------

    def prepare(
        self, values: dict[str, Any], token: str | None, operator: str | None = None
    ) -> dict[str, Any]:
        """第一阶段：校验并把已填内容暂存，正式表一律不动。

        带着旧 token 重提时直接接回原暂存，前端填过的绑扎材料原样保留。
        """
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return {"ok": False, "message": f"缺少必填字段：{'、'.join(missing)}"}
        token = str(token or "").strip() or None
        draft = {field: str(values.get(field) or "").strip() for field in EDITABLE_FIELDS}
        if not parse_slots(draft["箱位范围"]):
            return {"ok": False, "message": "箱位范围无法识别，请按「01-01-02,01-01-04」格式填写"}

        with store.transaction(SESSION_MODULE) as tx:
            if token is None:
                token = f"SUB-{tx.next_id(SESSION_MODULE)}"
            session = tx.find_by(SESSION_MODULE, "token", token)
            resumed = session is not None
            if session is None:
                session = {"token": token, "draft": draft, "operator": operator or "",
                           "prepared_at": _now()}
                tx.rows(SESSION_MODULE).append(session)
            else:
                # 接回断点：新内容补齐老暂存的空字段，老暂存里已填的（如绑扎材料）不丢。
                merged = dict(session.get("draft", {}))
                for field, value in draft.items():
                    if value:
                        merged[field] = value
                session["draft"] = merged
            staged = dict(session["draft"])
            tx.commit()

        global fail_point
        if fail_point == "prepare":
            fail_point = None
            raise RuntimeError("模拟网络中断：暂存已落库，但提交方没有收到响应")

        return {"ok": True, "phase": "prepared", "token": token, "resumed": resumed,
                "message": "绑扎内容已暂存，可继续提交", "draft": staged}

    def submit(
        self,
        values: dict[str, Any],
        token: str | None,
        operator: str | None = None,
        phase: str = "commit",
        retry: bool = False,
    ) -> dict[str, Any]:
        """绑扎提交：要么整段落库，要么正式表不留任何痕迹。

        断在 ``prepare`` 之后的，凭 token 接回暂存继续 commit；连 prepare 都没
          送到的，commit 时在同一事务里补建暂存，等于从没断的那一段接着走。
        同一条绑扎编号由第二个人重提：不新建，接回原任务并以新提交补齐字段，
          原已填的绑扎材料保留。``retry`` 表示这是断网后的补发，用于提示语区分。
        """
        if phase == "prepare":
            return self.prepare(values, token, operator)

        token = str(token or "").strip()
        incoming = {field: str(values.get(field) or "").strip() for field in EDITABLE_FIELDS}
        resumed = False
        merged_with_existing = False

        with store.transaction(MODULE, SLOT_MODULE, SESSION_MODULE, EVENT_MODULE) as tx:
            if not token:
                token = f"SUB-{tx.next_id(SESSION_MODULE)}"
            session = tx.find_by(SESSION_MODULE, "token", token)
            if session is None:
                # 前半段完全没到：校验通过后在事务内补暂存，再立刻落正式表，
                # 对外仍是一次完整提交。
                missing = [f for f in REQUIRED_FIELDS if not incoming.get(f)]
                if missing:
                    return {"ok": False, "token": token,
                            "message": f"缺少必填字段：{'、'.join(missing)}"}
                if not parse_slots(incoming["箱位范围"]):
                    return {"ok": False, "token": token,
                            "message": "箱位范围无法识别，请按「01-01-02,01-01-04」格式填写"}
                draft = incoming
            else:
                # prepare→commit 是两段式的正常第二段；带 retry 补发则提示断点续传。
                resumed = retry
                draft = dict(session.get("draft", {}))
                for field, value in incoming.items():
                    if value:
                        draft[field] = value

            code = draft["绑扎编号"]
            task = None
            for row in tx.rows(MODULE):
                if row.get("绑扎编号") == code:
                    task = row
                    break

            if task is None:
                task = {"id": tx.next_id(MODULE)}
                for field in EDITABLE_FIELDS:
                    task[field] = draft.get(field, "")
                task["status"] = STATUS_ORDER[0]
                task["绑扎耗时"] = ""
                task["started_at"] = task["lashed_at"] = task["removed_at"] = ""
                tx.rows(MODULE).append(task)
            else:
                # 重提接回原单据：只补不空改，保住此前已填的绑扎材料等内容。
                merged_with_existing = True
                for field in EDITABLE_FIELDS:
                    if draft.get(field):
                        task[field] = draft[field]

            self._sync_slots(tx, task, parse_slots(draft["箱位范围"]))

            if session is not None:
                tx.rows(SESSION_MODULE).remove(session)

            _recompute_all(tx)
            entry = _serialize(task, tx)

            global fail_point
            if fail_point == "commit":
                fail_point = None
                raise RuntimeError("模拟网络中断：正式落库前连接断开，本次写入全部回滚")

            tx.commit()

        message = "绑扎任务已提交"
        if merged_with_existing:
            message = "该绑扎编号已有任务，已接回原单据并补齐内容，未重复建单"
        elif resumed:
            message = "已从断点接回原提交，绑扎材料保留，单据整段落库"
        return {"ok": True, "token": token, "resumed": resumed or merged_with_existing,
                "message": message, "entry": entry}

    def _sync_slots(self, tx, task: dict[str, Any], slot_nos: list[str]) -> None:
        """按提交的箱位范围对齐明细：保留同箱位既有绑扎状态，新增箱位按单据当前状态起步。"""
        rows = tx.rows(SLOT_MODULE)
        existing = {str(row.get("slot_no")): row for row in rows
                    if int(row.get("task_id", 0)) == int(task["id"])}
        kept: set[str] = set()
        next_slot_id = max((int(row.get("id", 0)) for row in rows), default=0)
        for slot_no in slot_nos:
            kept.add(slot_no)
            if slot_no not in existing:
                next_slot_id += 1
                # 新建箱位跟随单据：绑扎中单据新增箱位即“绑扎中”，其余从待绑扎起步。
                init_status = task.get("status") if task.get("status") in ("绑扎中", "已绑扎", "已拆除") else "待绑扎"
                rows.append({"id": next_slot_id, "task_id": int(task["id"]),
                             "slot_no": slot_no, "status": init_status})
        for slot_no, row in list(existing.items()):
            if slot_no not in kept:
                rows.remove(row)

    # ---- 状态动作（开始/确认/拆除） ------------------------------------------

    def run_action(
        self,
        entry_id: int,
        action: str,
        *,
        operator: str | None = None,
        token: str | None = None,
        values: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """执行绑扎动作：先落库者为准，同动作凭证重放只返回既有结果。"""
        values = values or {}
        if action not in ACTION_RULES:
            return {"ok": False, "message": f"动作「{action}」不属于绑扎加固可执行范围"}
        rule = ACTION_RULES[action]
        target = rule["target"]

        with store.transaction(MODULE, SLOT_MODULE, SESSION_MODULE, EVENT_MODULE) as tx:
            task = tx.find(MODULE, entry_id)
            if task is None:
                return {"ok": False, "message": f"绑扎任务 {entry_id} 不存在或已归档"}

            # 动作凭证重放：同一个拆除/确认点两次，只认第一次落库的结果。
            if token:
                for event in _events_of(tx, entry_id):
                    if event.get("token") == token:
                        _recompute_all(tx)
                        entry = _serialize(task, tx)
                        tx.commit()
                        return {"ok": True, "token": token, "resumed": True,
                                "message": "该动作此前已落库，直接返回原结果，未重复执行",
                                "entry": entry}

            current = str(task.get("status"))
            if current == target:
                # 同一动作重复点：幂等成功，不重复记流水。
                tx.commit()
                return {"ok": True, "resumed": True,
                        "message": f"任务已是「{target}」，以先落库的记录为准，无需重复执行",
                        "entry": _serialize(task, tx)}
            if current not in rule["allowed"]:
                # 绑扎与拆绑撞车：拒绝后到的一方，不写任何数据。
                tx.commit()
                return {"ok": False, "conflict": True,
                        "message": f"任务当前为「{current}」，不能{action}；"
                                   f"绑扎与拆绑冲突时以先落库的「{current}」为准"}

            occurred_at = str(values.get("occurred_at") or "").strip() or _now()
            task["status"] = target
            task[rule["time_field"]] = occurred_at

            slot_target = SLOT_STATUS_BY_ACTION[action]
            for row in tx.rows(SLOT_MODULE):
                if int(row.get("task_id", 0)) == entry_id:
                    row["status"] = slot_target

            if action == "拆除绑扎":
                self._fill_lashing_duration(task, values)

            event_id = tx.next_id(EVENT_MODULE)
            tx.rows(EVENT_MODULE).append({
                "id": event_id, "task_id": entry_id, "action": action,
                "operator": operator or str(values.get("operator") or "值班管理员"),
                "occurred_at": occurred_at, "token": token or "",
            })

            # 动作落库后重算全部已有绑扎记录，看板与台账立即对齐。
            _recompute_all(tx)
            entry = _serialize(task, tx)
            tx.commit()

        return {"ok": True, "token": token, "message": f"绑扎任务已{action}", "entry": entry}

    @staticmethod
    def _fill_lashing_duration(task: dict[str, Any], values: dict[str, Any]) -> None:
        """拆绑时把绑扎耗时补全：优先用回填值，否则按开始/确认时间差算分钟数。"""
        manual = str(values.get("绑扎耗时") or "").strip()
        if manual:
            task["绑扎耗时"] = manual
            return
        start = _parse_time(str(task.get("lashed_at") or "")) or _parse_time(str(task.get("started_at") or ""))
        end = _parse_time(str(task.get("removed_at") or ""))
        if start and end and end >= start:
            minutes = int((end - start).total_seconds() // 60)
            task["绑扎耗时"] = f"{minutes}分钟"


def _parse_time(value: str) -> datetime | None:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None
