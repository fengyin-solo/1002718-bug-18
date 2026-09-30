"""绑扎加固：原子提交 / 幂等接续 / 冲突重算 / 看板重算 的回归测试。

直接跑：backend/.venv311/bin/python -m pytest backend/tests/test_lashing.py
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.seed import SEED_ROWS  # noqa: E402
from app.services import lashing as lashing_module  # noqa: E402
from app.services.lashing import (  # noqa: E402
    LEDGER_MODULE,
    MODULE,
    SLOT_MODULE,
    LashingService,
)
from app.store import store  # noqa: E402

service = LashingService()


@pytest.fixture(autouse=True)
def reset_store() -> None:
    """每个用例从种子数据重新开始，避免全局单例互相串数据。"""
    fresh = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}
    store._tables = fresh  # noqa: SLF001
    store._snapshot = None  # noqa: SLF001
    yield
    store._tables = {name: copy.deepcopy(rows) for name, rows in SEED_ROWS.items()}
    store._snapshot = None


def base_values(code: str, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "绑扎编号": code,
        "对应船舶": f"远洋轮-{code}",
        "箱位范围": f"{code}-B01,{code}-B02",
        "绑扎方式": "钢丝绑扎",
        "绑扎材料": "20mm 钢丝扣",
        "绑扎班组": "甲班",
    }
    values.update(overrides)
    return values


def find(code: str) -> dict:
    row = store.find_by_field(MODULE, "绑扎编号", code)
    assert row is not None, f"{code} 应当存在"
    return row


# ------------------------------------------------------------------ 原子性
def test_submit_is_atomic_and_leaves_no_trace_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    code = "LASH-T1"
    before = len(store.rows(MODULE))

    def boom(self, entry):  # noqa: ANN001
        raise RuntimeError("模拟写到箱位那一段网络中断")

    monkeypatch.setattr(LashingService, "_rebuild_slots", boom)
    with pytest.raises(RuntimeError):
        service.submit_entry(base_values(code), request_id=f"req-{code}")

    # 单据、箱位、台账三处都不留痕
    assert store.find_by_field(MODULE, "绑扎编号", code) is None
    assert store.find_by_field(SLOT_MODULE, "箱位编号", f"{code}-B01") is None
    assert store.find_by_field(LEDGER_MODULE, "绑扎编号", code) is None
    assert len(store.rows(MODULE)) == before


def test_resume_after_failure_continues_from_break_point(monkeypatch: pytest.MonkeyPatch) -> None:
    code = "LASH-T2"
    calls = {"n": 0}
    original = LashingService._rebuild_slots

    def boom_once(self, entry):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("模拟写到箱位那一段网络中断")
        return original(self, entry)

    monkeypatch.setattr(LashingService, "_rebuild_slots", boom_once)
    # 第一次：单据/箱位那一段抛错 → 事务整体回滚
    with pytest.raises(RuntimeError):
        service.submit_entry(base_values(code), request_id=f"req-{code}")

    # 故障恢复后原样重提：一次性落全，状态已绑扎
    entry, _ = service.submit_entry(base_values(code), request_id=f"req-{code}")
    assert entry["status"] == "已绑扎"
    assert store.find_by_field(SLOT_MODULE, "箱位编号", f"{code}-B01")["箱位状态"] == "绑扎占用"
    assert store.find_by_field(LEDGER_MODULE, "绑扎编号", code)["绑扎状态"] == "已绑扎"


# ------------------------------------------------------------------ 幂等
def test_same_code_resubmitted_by_another_user_keeps_one_row_and_material() -> None:
    code = "LASH-T3"
    service.submit_entry(base_values(code), request_id=f"req-{code}-a")
    count_after_first = len(store.rows(MODULE))

    # 换人、换 request_id 重提同一张单：不带材料，且更新船舶
    values = base_values(code, 对应船舶="远洋轮-改", 绑扎材料="")
    entry, message = service.submit_entry(values, request_id=f"req-{code}-b")

    assert len(store.rows(MODULE)) == count_after_first  # 不产生第二条
    assert entry["对应船舶"] == "远洋轮-改"  # 新值接续
    assert entry["绑扎材料"] == "20mm 钢丝扣"  # 已填绑扎材料保住
    assert "已处理" in message or "落库" in message


def test_same_request_id_replays_first_result_only() -> None:
    code = "LASH-T4"
    rid = "fixed-request-id"
    first, _ = service.submit_entry(base_values(code), request_id=rid)
    # 同一请求重放，即便内容不同也只返回首次结果
    second, message = service.submit_entry(
        base_values(code, 对应船舶="不应生效"), request_id=rid
    )
    assert second["id"] == first["id"]
    assert second["对应船舶"] == first["对应船舶"]
    assert "已处理" in message
    assert sum(1 for r in store.rows(MODULE) if r["绑扎编号"] == code) == 1


# ------------------------------------------------------------------ 耗时
def test_unlash_completes_lashing_duration() -> None:
    code = "LASH-T5"
    entry, _ = service.submit_entry(base_values(code), request_id=f"req-{code}")
    entry_id = int(entry["id"])
    assert "分钟" in str(entry["绑扎耗时"])

    result, _ = service.run_action(entry_id, "拆除绑扎", expected_version=int(entry["version"]))
    assert result is not None
    assert result["status"] == "已拆除"
    assert "分钟" in str(result["绑扎耗时"])
    # 台账同步
    assert store.find_by_field(LEDGER_MODULE, "绑扎编号", code)["绑扎状态"] == "已拆除"
    # 箱位已释放
    slots = [s for s in store.rows(SLOT_MODULE) if s["关联绑扎编号"] == code]
    assert slots and all(s["箱位状态"] == "已释放" for s in slots)


# ------------------------------------------------------------------ 列表/详情一致
def test_list_and_detail_share_same_truth() -> None:
    code = "LASH-T6"
    submitted, _ = service.submit_entry(base_values(code), request_id=f"req-{code}")
    items, _ = service.list_entries(keyword=code)
    assert len(items) == 1
    listed = items[0]
    detail = service.get_entry(int(submitted["id"]))
    for field in ("箱位范围", "绑扎状态", "绑扎材料", "绑扎耗时", "version"):
        assert listed[field] == detail[field]
    assert listed["绑扎状态"] == listed["status"] == "已绑扎"
    assert listed["箱量"] == 2


# ------------------------------------------------------------------ 并发冲突
def test_lashing_vs_unlashing_conflict_first_commit_wins() -> None:
    code = "LASH-T7"
    submitted, _ = service.submit_entry(base_values(code), request_id=f"req-{code}")
    entry_id = int(submitted["id"])
    v = int(submitted["version"])

    # 甲方先拆绑落库（版本 +1）
    unlashed, _ = service.run_action(entry_id, "拆除绑扎", expected_version=v)
    assert unlashed["status"] == "已拆除"

    # 乙方拿着旧版本再确认绑扎：不覆盖，以先落库为准，重算返回
    stale, message = service.run_action(entry_id, "确认绑扎", expected_version=v)
    assert stale["status"] == "已拆除"
    assert "先处理" in message or "重算" in message

    # 已拆除单据上再次提交绑扎也不覆盖
    again, _ = service.submit_entry(base_values(code), request_id=f"req-{code}-again")
    assert again["status"] == "已拆除"


# ------------------------------------------------------------------ 看板
def test_board_recounts_lashed_containers_and_counts_once() -> None:
    code = "LASH-T8"
    values = base_values(code, 箱位范围=f"{code}-B01,{code}-B02,{code}-B03")
    service.submit_entry(values, request_id=f"req-{code}-1")
    summary1 = service.board_summary()
    assert summary1["已绑箱数"] >= 3
    lashed_tasks_1 = summary1["已绑扎任务"]

    # 同一张单以相同内容重复提交：箱数与单数都不增加（只记一次）
    service.submit_entry(values, request_id=f"req-{code}-2")
    summary2 = service.board_summary()
    assert summary2["已绑扎任务"] == lashed_tasks_1
    assert summary2["已绑箱数"] == summary1["已绑箱数"]


def test_explicit_container_count_takes_precedence() -> None:
    code = "LASH-T9"
    entry, _ = service.submit_entry(base_values(code, 箱量="12"), request_id=f"req-{code}")
    assert entry["箱量"] == 12
    assert service.board_summary()["已绑箱数"] >= 12


def test_duration_is_computed_from_real_elapsed_time(monkeypatch: pytest.MonkeyPatch) -> None:
    code = "LASH-T10"
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(lashing_module.time, "time", lambda: clock["t"])

    entry, _ = service.submit_entry(base_values(code), request_id=f"req-{code}")
    entry_id = int(entry["id"])
    ver = int(entry["version"])

    clock["t"] += 25 * 60  # 绑扎进行了 25 分钟
    result, _ = service.run_action(entry_id, "拆除绑扎", expected_version=ver)
    assert result is not None
    assert result["绑扎耗时"] == "25 分钟"
