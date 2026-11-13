"""结项到分析方案的验收用例。

每个用例都覆盖正向链路与至少一个反向案例，反向案例对应需求里列出的
「报错、不生效、残留、对不上、显示成旧值、重复显示」现象。

运行：cd backend && .venv/bin/python -m tests.test_conclusion_flow
"""
from __future__ import annotations

import sys
import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.services import conclusion as flow
from app.store import store


class ConclusionFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        store.reset()
        self.client = TestClient(app)

    # ---- 辅助 -------------------------------------------------------------
    def conclude(self, entry_id: int, **values: object) -> dict:
        response = self.client.post(
            f"/api/abtest/{entry_id}/actions",
            json={"values": {"action": "结项实验", **values}},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def settle(self, entry_id: int, **values: object) -> dict:
        payload = self.conclude(entry_id, **values)
        self.assertIsNotNone(payload["extra"], payload)
        return payload["extra"]["settlement"]

    def add_metric_version(self, entry_id: int, version: str) -> dict:
        response = self.client.post(
            f"/api/metric/{entry_id}/versions",
            json={"values": {"口径版本": version}},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["entry"]

    # ---- 1. 达到最小样本量才生成分析输出 ----------------------------------
    def test_below_min_sample_blocks_output(self) -> None:
        result = self.settle(
            3, 实际样本量=8000, 样本范围="搜索分流 2026-10", p值=0.01
        )
        self.assertEqual(result["status"], "blocked")
        self.assertIn("未达最小样本量", result["message"])
        self.assertEqual(result["experiment"]["status"], "已结项")
        self.assertIn(
            "实际样本量 8000 未达最小样本量 20000",
            " ".join(result["experiment"][flow.PENDING_KEY]),
        )
        # 反向案例：不能留下空的或占位的分析输出，也不能把它当已启用
        self.assertEqual(self.client.get("/api/analysis/outputs").json()["total"], 0)
        self.assertEqual(result["experiment"]["分析状态"], "待补充")

    # ---- 2. 达标后记录显著性、对比基准与版本 ------------------------------
    def test_conclusion_records_significance_baseline_and_versions(self) -> None:
        result = self.settle(
            3, 实际样本量="25,000 人", 样本范围="搜索分流 2026-10", p值=0.03
        )
        self.assertEqual(result["status"], "generated")
        conclusion = result["conclusion"]
        self.assertEqual(conclusion["核心指标"], "METR-0003")
        self.assertEqual(conclusion["指标口径版本"], "V1")
        self.assertEqual(conclusion["显著性结果"], "显著")
        self.assertEqual(conclusion["对比基准"], "原搜索排序")
        self.assertEqual(conclusion["样本范围"], "搜索分流 2026-10")
        self.assertEqual(conclusion["实验版本"], "V1")
        output = result["output"]
        self.assertEqual(output["显著性结果"], "显著")
        self.assertEqual(output["指标口径版本"], "V1")
        plan = result["analysis_plan"]
        self.assertEqual(plan["status"], "已启用")
        self.assertEqual(plan["结论版本"], conclusion["结论版本"])
        self.assertEqual(plan["关联实验版本"], conclusion["实验版本"])

    def test_metric_meets_minimum_uses_registered_threshold(self) -> None:
        # 反向案例：阈值不生效。p 值 0.08 高于方案阈值 0.05，必须判为不显著。
        result = self.settle(
            1,
            实际样本量=15000,
            样本范围="结算页分流 2026-09",
            p值=0.08,
            分析方案编号="ANAL-0001",
        )
        self.assertEqual(result["conclusion"]["显著性结果"], "不显著")
        self.assertEqual(result["conclusion"]["显著性阈值"], 0.05)
        self.assertEqual(result["analysis_plan"]["方案编号"], "ANAL-0001")
        self.assertEqual(result["analysis_plan"]["显著性阈值"], "0.05")

    # ---- 3. 重复结项只生成一个方案版本 -------------------------------------
    def test_duplicate_conclusion_keeps_single_version(self) -> None:
        first = self.settle(
            3, 实际样本量=25000, 样本范围="搜索分流 2026-10", p值=0.01
        )
        second = self.settle(3)
        self.assertEqual(second["status"], "unchanged")
        details = self.client.get("/api/abtest/3/detail").json()
        self.assertEqual(len(details["conclusions"]), 1)
        self.assertEqual(len(details["outputs"]), 1)
        self.assertEqual(first["conclusion"]["结论版本"], "V1")
        self.assertEqual(second["conclusion"]["结论编号"], first["conclusion"]["结论编号"])
        # 反向案例：重复结项不能给方案列表多出一行
        self.assertEqual(
            len([p for p in details["analysis_plans"]]), 1, details["analysis_plans"]
        )
        self.assertEqual(details["analysis_plans"][0]["方案版本"], "V1")

    # ---- 4. 两个实验引用同一核心指标时样本范围和历史结论分开 --------------
    def test_two_experiments_sharing_metric_keep_separate_history(self) -> None:
        store.find("abtest", 1)["核心指标"] = "METR-0001"
        store.find("abtest", 1)["最小样本量"] = "10000"
        store.find("abtest", 2)["核心指标"] = "METR-0001"
        store.find("abtest", 2)["最小样本量"] = "8000"

        first = self.settle(
            1, 实际样本量=12000, 样本范围="结算页 A 流量 2026-09", p值=0.01
        )
        second = self.settle(
            2, 实际样本量=9000, 样本范围="优惠券 B 流量 2026-10", p值=0.20
        )
        self.assertEqual(first["conclusion"]["核心指标"], second["conclusion"]["核心指标"])
        self.assertNotEqual(
            first["conclusion"]["结论编号"], second["conclusion"]["结论编号"]
        )
        self.assertEqual(first["conclusion"]["样本范围"], "结算页 A 流量 2026-09")
        self.assertEqual(second["conclusion"]["样本范围"], "优惠券 B 流量 2026-10")
        self.assertNotEqual(
            first["analysis_plan"]["方案编号"], second["analysis_plan"]["方案编号"]
        )
        self.assertEqual(first["analysis_plan"]["对比基准"], "展示原结算页")
        self.assertEqual(second["analysis_plan"]["对比基准"], "不弹优惠券")

        # 反向案例：A 的结论不能串到 B 的方案或输出里
        trace_a = self.client.get(
            "/api/abtest/trace", params={"experiment_code": "ABTE-0001"}
        ).json()
        trace_b = self.client.get(
            "/api/abtest/trace", params={"experiment_code": "ABTE-0002"}
        ).json()
        self.assertEqual([o["样本范围"] for o in trace_a["outputs"]], ["结算页 A 流量 2026-09"])
        self.assertEqual([o["样本范围"] for o in trace_b["outputs"]], ["优惠券 B 流量 2026-10"])
        self.assertEqual(len(trace_b["outputs"]), 1)

    def test_reusing_other_experiments_plan_does_not_borrow_baseline(self) -> None:
        # 反向案例：显式引用别的实验的方案，不能借用它的旧基准和历史输出。
        store.find("abtest", 3)["核心指标"] = "METR-0003"
        other = store.find("analysis", 2)
        other["关联实验编号"] = "ABTE-0002"
        other["核心指标"] = "METR-0002"
        result = self.settle(
            3,
            实际样本量=25000,
            样本范围="搜索分流 2026-10",
            p值=0.02,
            分析方案编号="ANAL-0002",
        )
        self.assertEqual(result["analysis_plan"]["对比基准"], "原搜索排序")
        self.assertEqual(result["analysis_plan"]["核心指标"], "METR-0003")
        self.assertNotEqual(result["analysis_plan"]["方案编号"], "ANAL-0002")
        self.assertEqual(other.get("结论版本"), None)
        self.assertTrue(
            any("未借用" in audit["说明"] for audit in result["audits"]),
            result["audits"],
        )

    # ---- 5. 换版后旧输出保留原版本，新方案重新计算 ------------------------
    def test_metric_bump_full_flow(self) -> None:
        first = self.settle(
            3, 实际样本量=25000, 样本范围="搜索分流 2026-10", p值=0.011
        )
        old_output_code = first["output"]["输出编号"]
        metric_row = store.find_by("metric", "指标编号", "METR-0003")
        self.add_metric_version(int(metric_row["id"]), "V2")

        # 反向案例：换版后不重新计算就直接再结项，不能复制旧显著性结果。
        blocked = self.settle(3)
        self.assertEqual(blocked["status"], "blocked")
        self.assertIn("旧分析输出已失效", blocked["message"])
        self.assertEqual(blocked["experiment"]["status"], "已结项")
        outputs = self.client.get("/api/analysis/outputs").json()["items"]
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0]["输出编号"], old_output_code)
        self.assertEqual(outputs[0]["输出版本"], "V1")
        self.assertEqual(outputs[0]["指标口径版本"], "V1")
        self.assertEqual(outputs[0]["显著性结果"], "显著")
        self.assertEqual(outputs[0]["status"], "已失效")

        # 重新计算后得到 V2 输出，V1 仍在，且两个版本各自保留原显著性结果。
        recomputed = self.settle(
            3, 实际样本量=30000, 样本范围="搜索分流 2026-10-31", p值=0.30
        )
        self.assertEqual(recomputed["output"]["指标口径版本"], "V2")
        self.assertEqual(recomputed["output"]["输出版本"], "V2")
        self.assertEqual(recomputed["output"]["显著性结果"], "不显著")
        self.assertEqual(recomputed["conclusion"]["结论编号"], first["conclusion"]["结论编号"])
        self.assertEqual(recomputed["conclusion"]["结论版本"], "V2")
        details = self.client.get("/api/abtest/3/detail").json()
        versions = [
            (o["输出版本"], o["指标口径版本"], o["显著性结果"], o["status"])
            for o in details["outputs"]
        ]
        self.assertEqual(
            versions,
            [("V1", "V1", "显著", "已失效"), ("V2", "V2", "不显著", "有效")],
        )

    def test_metric_bump_after_settlement_invalidates_on_read(self) -> None:
        # 反向案例：换版发生在结项之后，再查实验详情/输出/审计时不能还显示旧值。
        self.settle(3, 实际样本量=25000, 样本范围="搜索分流 2026-10", p值=0.011)
        metric_row = store.find_by("metric", "指标编号", "METR-0003")
        self.add_metric_version(int(metric_row["id"]), "V2")

        trace = self.client.get(
            "/api/abtest/trace", params={"experiment_code": "ABTE-0003"}
        ).json()
        self.assertEqual([(o["输出版本"], o["status"]) for o in trace["outputs"]], [("V1", "已失效")])
        self.assertEqual(trace["outputs"][0]["显著性结果"], "显著")

        detail = self.client.get("/api/abtest/3/detail").json()
        self.assertEqual(detail["experiment"]["status"], "已结项")
        self.assertEqual(detail["experiment"]["分析状态"], "已失效待重算")
        self.assertTrue(detail["experiment"][flow.PENDING_KEY])

        listing = [
            row for row in self.client.get("/api/abtest").json()["items"] if row["id"] == 3
        ][0]
        self.assertEqual(listing["分析状态"], "已失效待重算")
        self.assertTrue(listing[flow.PENDING_KEY])

    def test_enabling_old_plan_after_bump_is_refused(self) -> None:
        first = self.settle(
            3, 实际样本量=25000, 样本范围="搜索分流 2026-10", p值=0.011
        )
        plan_id = first["analysis_plan"]["id"]
        metric_row = store.find_by("metric", "指标编号", "METR-0003")
        self.add_metric_version(int(metric_row["id"]), "V2")
        # 换版发生在结项之后：直接启用旧方案必须失败并给出待补充分析项。
        response = self.client.post(
            f"/api/analysis/{plan_id}/actions", json={"values": {"action": "启用方案"}}
        ).json()
        self.assertTrue(response["ok"])
        self.assertIn("启用失败", response["message"])
        self.assertIn("需重新计算", response["message"])
        self.assertNotEqual(response["entry"]["status"], "已启用")
        self.assertTrue(response["entry"][flow.PENDING_KEY])

    # ---- 6. 启用失败时实验仍保持已结项，但标出待补充分析项 ----------------
    def test_enable_failure_keeps_concluded_and_flags_pending(self) -> None:
        archived = store.find("analysis", 1)
        archived["status"] = "已归档"
        archived["关联实验编号"] = "ABTE-0001"
        result = self.settle(
            1,
            实际样本量=15000,
            样本范围="结算页分流 2026-09",
            p值=0.01,
            分析方案编号="ANAL-0001",
        )
        self.assertEqual(result["status"], "enable_failed")
        experiment = result["experiment"]
        self.assertEqual(experiment["status"], "已结项")
        self.assertEqual(experiment["分析状态"], "启用失败")
        self.assertTrue(experiment[flow.PENDING_KEY])
        self.assertTrue(any("已归档" in item for item in experiment[flow.PENDING_KEY]))
        # 反向案例：失败不能把实验退回实验中，也不能悄悄显示成已启用
        self.assertNotEqual(experiment["status"], "实验中")
        self.assertNotEqual(experiment["分析状态"], "已启用")
        self.assertTrue(any(a["结果"] == "失败" for a in result["audits"]))

    def test_enable_failure_is_retried_on_conclusion_retry(self) -> None:
        archived = store.find("analysis", 1)
        archived["status"] = "已归档"
        archived["关联实验编号"] = "ABTE-0001"
        first = self.settle(
            1, 实际样本量=15000, 样本范围="结算页分流 2026-09", p值=0.01,
            分析方案编号="ANAL-0001",
        )
        self.assertEqual(first["status"], "enable_failed")
        archived["status"] = "草稿"
        retry = self.settle(1)
        self.assertEqual(retry["status"], "unchanged")
        self.assertEqual(retry["analysis_plan"]["方案编号"], "ANAL-0001")
        self.assertEqual(retry["analysis_plan"]["status"], "已启用")
        self.assertEqual(retry["experiment"][flow.PENDING_KEY], [])
        details = self.client.get("/api/abtest/1/detail").json()
        self.assertEqual(len(details["conclusions"]), 1)

    # ---- 7. 实验详情、分析输出、审计记录按版本互查 ------------------------
    def test_cross_lookup_by_version(self) -> None:
        result = self.settle(
            3, 实际样本量=25000, 样本范围="搜索分流 2026-10", p值=0.03
        )
        conclusion = result["conclusion"]
        output = result["output"]

        detail = self.client.get("/api/abtest/3/detail").json()
        self.assertEqual(detail["versions"]["结论版本"], conclusion["结论版本"])
        self.assertEqual(detail["versions"]["输出版本"], output["输出版本"])
        self.assertEqual(len(detail["audits"]), 2)

        output_detail = self.client.get(
            f"/api/analysis/outputs/{output['输出编号']}"
        ).json()
        self.assertEqual(output_detail["conclusion"]["结论编号"], conclusion["结论编号"])
        self.assertEqual(output_detail["experiment"]["方案编号"], "ABTE-0003")
        self.assertEqual(output_detail["analysis_plan"]["方案编号"], result["analysis_plan"]["方案编号"])
        self.assertTrue(output_detail["audits"])

        plan_detail = self.client.get(
            f"/api/analysis/{result['analysis_plan']['id']}/detail"
        ).json()
        self.assertEqual(plan_detail["versions"]["结论版本"], conclusion["结论版本"])
        self.assertEqual(plan_detail["experiment"]["方案编号"], "ABTE-0003")

        trace = self.client.get(
            "/api/abtest/trace",
            params={
                "conclusion_code": conclusion["结论编号"],
                "conclusion_version": conclusion["结论版本"],
            },
        ).json()
        self.assertEqual(len(trace["outputs"]), 1)
        self.assertEqual(trace["outputs"][0]["输出编号"], output["输出编号"])
        self.assertEqual(trace["experiment"]["方案编号"], "ABTE-0003")

        audits = self.client.get(
            "/api/analysis/audits",
            params={"experiment_code": "ABTE-0003", "conclusion_version": "V1"},
        ).json()
        self.assertEqual(audits["total"], 2)

    def test_unknown_trace_returns_empty_not_error(self) -> None:
        trace = self.client.get(
            "/api/abtest/trace", params={"experiment_code": "ABTE-9999"}
        )
        self.assertEqual(trace.status_code, 200)
        body = trace.json()
        self.assertIsNone(body["experiment"])
        self.assertEqual(body["outputs"], [])

    # ---- 8. 核心指标缺失 / 未登记的反向案例 --------------------------------
    def test_missing_metric_definition_blocks_output(self) -> None:
        store.find("abtest", 3)["核心指标"] = "METR-9999"
        result = self.settle(
            3, 实际样本量=25000, 样本范围="搜索分流 2026-10", p值=0.01
        )
        self.assertEqual(result["status"], "blocked")
        self.assertIn("未在指标口径中登记", result["message"])
        self.assertEqual(self.client.get("/api/analysis/outputs").json()["total"], 0)
        self.assertEqual(result["experiment"]["status"], "已结项")

    def test_metric_version_is_not_inherited_from_old_plan(self) -> None:
        # 反向案例：ANAL-0002 是 ABTE-0002 的方案，不能把它的指标版本带进 ABTE-0003。
        store.find("analysis", 2)["关联实验编号"] = "ABTE-0002"
        store.find("analysis", 2)["核心指标"] = "METR-0002"
        store.find("analysis", 2)["方案版本"] = "V3"
        result = self.settle(
            3,
            实际样本量=25000,
            样本范围="搜索分流 2026-10",
            p值=0.02,
            分析方案编号="ANAL-0002",
        )
        self.assertEqual(result["analysis_plan"]["方案版本"], "V1")
        self.assertEqual(result["output"]["指标口径版本"], "V1")


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False)
    sys.exit(0 if result.result.wasSuccessful() else 1)
