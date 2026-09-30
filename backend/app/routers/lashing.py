"""绑扎加固接口：维护绑扎任务，覆盖绑扎提交（两阶段、幂等）与开始/确认/拆除动作。"""
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


@router.get("/stats")
def lashing_stats() -> dict[str, int]:
    """绑扎看板：待绑扎/绑扎中/已绑扎任务数与已绑箱数，全部按单据实时重算。"""
    return service.stats()


# 注意：/stats、/export 要注册在 /{entry_id} 之前，否则会被当成绑扎编号去匹配。
@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出绑扎加固清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "lashing", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条绑扎任务明细（含箱位明细与动作流水）；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"绑扎任务 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def submit_entry(payload: EntryPayload) -> ActionResult:
    """绑扎提交：prepare 暂存 / commit 整段落库，同一编号或凭证重提只接回不新建。"""
    values = payload.values or {}
    phase = str(values.pop("phase", "commit")).strip()
    retry = str(values.pop("retry", "")).strip() in ("1", "true", "True", "yes")
    token = str(values.pop("token", "") or "").strip() or None
    operator = str(values.pop("operator", "") or "").strip() or None
    result = service.submit(values, token=token, operator=operator, phase=phase, retry=retry)
    return ActionResult(**result)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """开始绑扎、确认绑扎、拆除绑扎；动作幂等，冲突时以先落库者为准。"""
    values = payload.values or {}
    action = str(values.pop("action", "") or "").strip()
    token = str(values.pop("token", "") or "").strip() or None
    operator = str(values.pop("operator", "") or "").strip() or None
    result = service.run_action(entry_id, action, operator=operator, token=token, values=values)
    if not result["ok"] and result.get("conflict"):
        # 冲突（如已拆除又来确认绑扎）：409，正文里带可读说明，不落任何数据。
        raise HTTPException(status_code=409, detail=result["message"])
    return ActionResult(**result)
