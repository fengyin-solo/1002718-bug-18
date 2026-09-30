"""绑扎加固接口：维护绑扎任务，覆盖开始绑扎、确认绑扎、拆除绑扎等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.lashing import LashingService

router = APIRouter(prefix="/api/lashing", tags=["绑扎加固"])

service = LashingService()

LIST_FIELDS = ["绑扎编号", "对应船舶", "箱位范围", "绑扎方式", "绑扎材料", "绑扎班组", "绑扎耗时", "绑扎状态"]
STATUSES = ["待绑扎", "绑扎中", "已绑扎", "已拆除"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按绑扎编号检索"),
    status: str | None = Query(default=None, description="待绑扎、绑扎中、已绑扎、已拆除"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按绑扎编号与状态过滤绑扎加固列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


# 注意：/export 是静态路径，必须声明在 /{entry_id} 之前，否则会被当成 entry_id 解析。
@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出绑扎加固清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "lashing", "total": total, "items": items}


@router.get("/board/summary")
def board_summary() -> dict[str, Any]:
    """绑扎看板：已绑箱数随单据实时重算。"""
    return service.board_summary()


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条绑扎任务明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"绑扎任务 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条绑扎任务，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="绑扎任务已登记", entry=entry)


@router.post("/submit", response_model=ActionResult)
def submit_entry(payload: EntryPayload) -> ActionResult:
    """原子绑扎提交：单据、箱位、台账同事务落库；断网重提接回原单，不产生第二条。

    values.request_id 为客户端幂等键（可选）；同一请求重放只返回首次结果。
    """
    values = dict(payload.values or {})
    request_id = values.pop("request_id", None)
    try:
        entry, message = service.submit_entry(values, request_id)
    except ValueError as exc:
        return ActionResult(ok=False, message=str(exc))
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条绑扎任务执行开始绑扎、确认绑扎、拆除绑扎；不允许的动作会被拦下并说明原因。

    values.expected_version 用于并发冲突控制：与库内版本不一致时以先落库者为准并重算。
    """
    values = dict(payload.values or {})
    action = str(values.pop("action", "") or "").strip()
    raw_version = values.pop("expected_version", None)
    try:
        expected_version = int(raw_version) if raw_version not in (None, "") else None
    except (TypeError, ValueError):
        return ActionResult(ok=False, message="expected_version 必须是整数版本号")
    entry, message = service.run_action(
        entry_id, action, expected_version=expected_version, values=values or None
    )
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
