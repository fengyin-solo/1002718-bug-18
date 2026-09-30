"""绑扎加固缺陷回归验证：

直接对应线上出过的两类事故及其修法：
1. 提交劈成两段、断网只落半截 -> 两阶段 + 事务原子提交 + 凭证断点续传；
2. 同任务换人重提生成两条 -> 业务编号幂等，重提接回原单、材料保留。

以及拆绑耗时补全、列表/详情箱位一致、动作冲突先落库为准、看板重算等口径。

运行：python -m tests.test_lashing（在 backend 目录下）
"""
from __future__ import annotations

import threading
import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.services import lashing as lashing_service
from app.services.lashing import LashingService
from app.store import store


class LashingServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        # 每个用例从干净的内存表开始。
        store._tables["lashing"] = []
        store._tables["lashing_slots"] = []
        store._tables["lashing_sessions"] = []
        store._tables["lashing_events"] = []
        store._sequences["lashing_sessions"] = 0
        store._sequences["lashing_events"] = 0
        store._sequences["lashing"] = 0
        store._sequences["lashing_slots"] = 0
        lashing_service.fail_point = None
        self.svc = LashingService()
        self.form = {
            "绑扎编号": "LASH-T1",
            "对应船舶": "测试船",
            "箱位范围": "01-01-02,01-01-04,01-01-06",
            "绑扎方式": "钢丝绳固定",
            "绑扎材料": "钢丝绳×6",
            "绑扎班组": "测试班组",
        }

    # 1. 原子提交：commit 阶段故障时正式表不留任何痕迹
    def test_commit_failure_leaves_no_trace(self) -> None:
        lashing_service.fail_point = "commit"
        with self.assertRaises(RuntimeError):
            self.svc.submit(dict(self.form), token="SUB-X1", operator="甲", phase="commit")
        self.assertEqual(store.rows("lashing"), [])
        self.assertEqual(store.rows("lashing_slots"), [])
        # 暂存也不该留下（它在事务内补建，随回滚一起消失）
        self.assertEqual(store.rows("lashing_sessions"), [])

    # 2. 断在 prepare 之后：暂存保留、正式表为空，重提从断点接走
    def test_resume_after_prepare_interrupt(self) -> None:
        lashing_service.fail_point = "prepare"
        with self.assertRaises(RuntimeError):
            self.svc.submit(dict(self.form), token="SUB-X2", operator="甲", phase="prepare")
        self.assertEqual(store.rows("lashing"), [])
        sessions = store.rows("lashing_sessions")
        self.assertEqual(len(sessions), 1)

        # 网络恢复后用同一个 token 直接 commit（带 retry，说明此前断过）
        result = self.svc.submit({}, token="SUB-X2", operator="甲", phase="commit", retry=True)
        self.assertTrue(result["ok"])
        self.assertTrue(result["resumed"])
        self.assertEqual(len(store.rows("lashing")), 1)
        self.assertEqual(len(store.rows("lashing_slots")), 3)
        self.assertEqual(store.rows("lashing_sessions"), [])
        entry = result["entry"]
        self.assertEqual(entry["绑扎材料"], "钢丝绳×6")
        self.assertEqual(entry["箱位范围"], "01-01-02,01-01-04,01-01-06")

    # 3. 连 prepare 都没送到：commit 一把成功（没断的那一段接着走）
    def test_direct_commit_without_prepare(self) -> None:
        result = self.svc.submit(dict(self.form), token=None, operator="甲", phase="commit")
        self.assertTrue(result["ok"])
        self.assertEqual(len(store.rows("lashing")), 1)
        self.assertTrue(result["token"])
        self.assertFalse(result["resumed"])
        self.assertEqual(result["message"], "绑扎任务已提交")

    # 3b. 正常 prepare→commit 两段式首次提交不算断点续传
    def test_normal_two_phase_first_submit(self) -> None:
        prepared = self.svc.submit(dict(self.form), token="SUB-TP", operator="甲", phase="prepare")
        self.assertTrue(prepared["ok"])
        committed = self.svc.submit(dict(self.form), token="SUB-TP", operator="甲", phase="commit")
        self.assertTrue(committed["ok"])
        self.assertFalse(committed["resumed"])
        self.assertEqual(committed["message"], "绑扎任务已提交")

    # 4. 同一编号换人重提：接回原单、不新建、材料保留并补齐新字段
    def test_resubmit_same_code_merges(self) -> None:
        first = self.svc.submit(dict(self.form), token="SUB-A", operator="甲", phase="commit")
        self.assertTrue(first["ok"])
        original_id = first["entry"]["id"]

        resubmit = {
            "绑扎编号": "LASH-T1",
            "对应船舶": "测试船",
            "箱位范围": "01-01-02,01-01-04,01-01-06,01-01-08",
            # 绑扎材料故意不填，必须保住甲填过的“钢丝绳×6”
            "绑扎班组": "夜班班组",
        }
        second = self.svc.submit(resubmit, token="SUB-B", operator="乙", phase="commit")
        self.assertTrue(second["ok"])
        self.assertTrue(second["resumed"])
        self.assertEqual(len(store.rows("lashing")), 1)
        self.assertEqual(second["entry"]["id"], original_id)
        self.assertEqual(second["entry"]["绑扎材料"], "钢丝绳×6")
        self.assertEqual(second["entry"]["绑扎班组"], "夜班班组")
        self.assertEqual(second["entry"]["总箱数"], 4)

    # 5. 同一张单据重复提交（同 token 重放）只记一次
    def test_same_token_replay_commits_once(self) -> None:
        first = self.svc.submit(dict(self.form), token="SUB-DUP", operator="甲", phase="commit")
        self.assertTrue(first["ok"])
        # commit 成功后暂存已清；同 token 再来时按编号接回，不新建
        second = self.svc.submit(dict(self.form), token="SUB-DUP", operator="甲", phase="commit")
        self.assertTrue(second["ok"])
        self.assertEqual(len(store.rows("lashing")), 1)
        self.assertEqual(len(store.rows("lashing_slots")), 3)

    # 6. 箱位范围在列表与详情两处一致，且始终来自明细投影
    def test_slot_range_list_detail_consistent(self) -> None:
        self.svc.submit(dict(self.form), token="SUB-C", operator="甲", phase="commit")
        items, total = self.svc.list_entries(page=1, size=20)
        self.assertEqual(total, 1)
        detail = self.svc.get_entry(items[0]["id"])
        self.assertEqual(items[0]["箱位范围"], detail["箱位范围"])
        # 人为把单据上的冗余文本改坏，投影仍以箱位明细为准
        task = store.rows("lashing")[0]
        task["箱位范围"] = "被劈丢的半截箱位"
        items2, _ = self.svc.list_entries(page=1, size=20)
        detail2 = self.svc.get_entry(task["id"])
        self.assertEqual(items2[0]["箱位范围"], "01-01-02,01-01-04,01-01-06")
        self.assertEqual(detail2["箱位范围"], "01-01-02,01-01-04,01-01-06")
        self.assertEqual([s["slot_no"] for s in detail2["箱位明细"]],
                         ["01-01-02", "01-01-04", "01-01-06"])

    # 7. 完整状态流转 + 拆除时绑扎耗时自动补全
    def test_action_flow_and_duration_backfill(self) -> None:
        self.svc.submit(dict(self.form), token="SUB-F", operator="甲", phase="commit")
        task_id = store.rows("lashing")[0]["id"]
        self.svc.run_action(task_id, "开始绑扎", operator="甲", token="ACT-1",
                            values={"occurred_at": "2026-09-30 08:00:00"})
        self.svc.run_action(task_id, "确认绑扎", operator="甲", token="ACT-2",
                            values={"occurred_at": "2026-09-30 10:00:00"})
        result = self.svc.run_action(task_id, "拆除绑扎", operator="乙", token="ACT-3",
                                     values={"occurred_at": "2026-09-30 11:30:00"})
        self.assertTrue(result["ok"])
        task = store.rows("lashing")[0]
        self.assertEqual(task["status"], "已拆除")
        # 确认绑扎 10:00 -> 拆除 11:30，自动补 90 分钟
        self.assertEqual(task["绑扎耗时"], "90分钟")
        self.assertTrue(all(s["status"] == "已拆除" for s in store.rows("lashing_slots")))
        # 手工回填耗时优先
        self.svc.run_action(task_id, "拆除绑扎", operator="乙", token="ACT-3",
                            values={"绑扎耗时": "120分钟"})
        self.assertEqual(store.rows("lashing")[0]["绑扎耗时"], "90分钟")  # 幂等重放不改

    # 8. 绑扎与拆绑冲突：先落库者为准，后者 409 且不落数据
    def test_conflict_first_commit_wins(self) -> None:
        self.svc.submit(dict(self.form), token="SUB-K", operator="甲", phase="commit")
        task_id = store.rows("lashing")[0]["id"]
        self.svc.run_action(task_id, "开始绑扎", operator="甲", token="A1")
        self.svc.run_action(task_id, "确认绑扎", operator="甲", token="A2")
        # 此时已绑扎；再来“开始绑扎”属于冲突
        rejected = self.svc.run_action(task_id, "开始绑扎", operator="乙", token="A3")
        self.assertFalse(rejected["ok"])
        self.assertTrue(rejected["conflict"])
        self.assertEqual(store.rows("lashing")[0]["status"], "已绑扎")
        events = store.rows("lashing_events")
        self.assertEqual([e["token"] for e in events], ["A1", "A2"])

        # 拆除成功后，迟到的“确认绑扎”同样被拒
        self.svc.run_action(task_id, "拆除绑扎", operator="乙", token="A4")
        late = self.svc.run_action(task_id, "确认绑扎", operator="甲", token="A5")
        self.assertFalse(late["ok"])
        self.assertTrue(late["conflict"])
        self.assertEqual(store.rows("lashing")[0]["status"], "已拆除")

    # 9. 同动作凭证重放：网络重试不重复执行，返回第一次落库的结果
    def test_action_token_replay(self) -> None:
        self.svc.submit(dict(self.form), token="SUB-R", operator="甲", phase="commit")
        task_id = store.rows("lashing")[0]["id"]
        r1 = self.svc.run_action(task_id, "开始绑扎", operator="甲", token="R1")
        r2 = self.svc.run_action(task_id, "开始绑扎", operator="甲", token="R1")
        self.assertTrue(r1["ok"])
        self.assertTrue(r2["ok"])
        self.assertTrue(r2["resumed"])
        self.assertEqual(len(store.rows("lashing_events")), 1)

    # 10. 看板统计随单据与箱位重算；重复提交不多计
    def test_board_stats_recomputed(self) -> None:
        stats0 = self.svc.stats()
        self.assertEqual(stats0["已绑箱数"], 0)
        self.svc.submit(dict(self.form), token="SUB-S1", operator="甲", phase="commit")
        task_id = store.rows("lashing")[0]["id"]
        self.svc.run_action(task_id, "开始绑扎", operator="甲", token="S1")
        self.svc.run_action(task_id, "确认绑扎", operator="甲", token="S2")
        stats1 = self.svc.stats()
        self.assertEqual(stats1["已绑扎任务"], 1)
        self.assertEqual(stats1["已绑箱数"], 3)
        # 同单重提（箱位不变）不新增箱数
        self.svc.submit(dict(self.form), token="SUB-S2", operator="乙", phase="commit")
        stats2 = self.svc.stats()
        self.assertEqual(stats2["已绑箱数"], 3)
        self.assertEqual(stats2["已绑扎任务"], 1)
        # 拆除后已绑箱数归零
        self.svc.run_action(task_id, "拆除绑扎", operator="乙", token="S3")
        stats3 = self.svc.stats()
        self.assertEqual(stats3["已绑箱数"], 0)

    # 11. 并发：绑扎中态下确认绑扎与拆除绑扎同时到，先落库者赢，后者冲突
    def test_concurrent_actions_serialized(self) -> None:
        self.svc.submit(dict(self.form), token="SUB-T", operator="甲", phase="commit")
        task_id = store.rows("lashing")[0]["id"]
        self.svc.run_action(task_id, "开始绑扎", operator="甲", token="T1")
        outcomes: list[tuple[str, bool]] = []
        barrier = threading.Barrier(2)

        def confirm() -> None:
            barrier.wait()
            r = self.svc.run_action(task_id, "确认绑扎", operator="甲", token="T2")
            outcomes.append(("confirm", r["ok"]))

        def remove() -> None:
            barrier.wait()
            # 已绑扎才能拆除：与确认绑扎正面对冲，谁先抢到锁谁落库
            r = self.svc.run_action(task_id, "拆除绑扎", operator="乙", token="T3")
            outcomes.append(("remove", r["ok"]))

        threads = [threading.Thread(target=confirm), threading.Thread(target=remove)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        applied = [name for name, ok in outcomes if ok]
        self.assertEqual(len(applied), 1, outcomes)
        final_status = store.rows("lashing")[0]["status"]
        if applied == ["confirm"]:
            self.assertEqual(final_status, "已绑扎")
        else:
            self.assertEqual(applied, ["remove"])
            self.assertEqual(final_status, "已拆除")
        # 箱位明细与单据状态永远一致，不存在单据已拆、箱位还挂已绑扎的半截状态
        expected_slot = "已拆除" if final_status == "已拆除" else "已绑扎"
        self.assertTrue(all(s["status"] == expected_slot for s in store.rows("lashing_slots")))


class LashingApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        store._tables["lashing"] = []
        store._tables["lashing_slots"] = []
        store._tables["lashing_sessions"] = []
        store._tables["lashing_events"] = []
        store._sequences["lashing_sessions"] = 0
        store._sequences["lashing_events"] = 0
        store._sequences["lashing"] = 0
        store._sequences["lashing_slots"] = 0
        lashing_service.fail_point = None
        self.client = TestClient(app)

    def test_full_api_flow(self) -> None:
        payload = {"values": {"token": "SUB-API", "绑扎编号": "LASH-A1",
                              "对应船舶": "接口船", "箱位范围": "01-01-00,01-01-02",
                              "绑扎材料": "绑扎带×4"}}
        resp = self.client.post("/api/lashing", json=payload)
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["ok"])
        entry_id = body["entry"]["id"]

        # 列表与详情一致
        listed = self.client.get("/api/lashing").json()["items"]
        detail = self.client.get(f"/api/lashing/{entry_id}").json()
        self.assertEqual(listed[0]["箱位范围"], detail["箱位范围"])

        # 看板
        stats = self.client.get("/api/lashing/stats").json()
        self.assertEqual(stats["待绑扎任务"], 1)

        # 动作流转
        self.client.post(f"/api/lashing/{entry_id}/actions",
                         json={"values": {"action": "开始绑扎", "token": "API-1"}})
        self.client.post(f"/api/lashing/{entry_id}/actions",
                         json={"values": {"action": "确认绑扎", "token": "API-2"}})
        stats2 = self.client.get("/api/lashing/stats").json()
        self.assertEqual(stats2["已绑箱数"], 2)

        # 冲突动作返回 409
        conflict = self.client.post(f"/api/lashing/{entry_id}/actions",
                                    json={"values": {"action": "开始绑扎", "token": "API-3"}})
        self.assertEqual(conflict.status_code, 409)

        # 刷新后重查：仍只有一条，id 不变
        again = self.client.get("/api/lashing").json()
        self.assertEqual(again["total"], 1)
        self.assertEqual(again["items"][0]["id"], entry_id)

        # 导出路由未被 /{entry_id} 吞掉
        export = self.client.get("/api/lashing/export")
        self.assertEqual(export.status_code, 200)
        self.assertEqual(export.json()["total"], 1)

    def test_resubmit_by_another_operator_api(self) -> None:
        first = self.client.post("/api/lashing", json={"values": {
            "token": "SUB-P1", "绑扎编号": "LASH-DUP", "对应船舶": "船舶",
            "箱位范围": "02-02-02", "绑扎材料": "花兰螺丝×2"}})
        self.assertEqual(first.status_code, 200)
        original_id = first.json()["entry"]["id"]
        second = self.client.post("/api/lashing", json={"values": {
            "token": "SUB-P2", "operator": "接手的同事", "绑扎编号": "LASH-DUP",
            "对应船舶": "船舶", "箱位范围": "02-02-02", "绑扎班组": "丙班"}})
        self.assertEqual(second.status_code, 200)
        body = second.json()
        self.assertTrue(body["resumed"])
        self.assertEqual(body["entry"]["id"], original_id)
        self.assertEqual(body["entry"]["绑扎材料"], "花兰螺丝×2")
        self.assertEqual(self.client.get("/api/lashing").json()["total"], 1)


if __name__ == "__main__":
    unittest.main()
