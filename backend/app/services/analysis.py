"""分析方案业务规则：状态流转、字段校验与结论启用都收在这里。

分析方案的启用不是把状态改成「已启用」：它必须引用同一实验版本、同一结论版本
和同一核心指标口径版本。核心指标换版或样本量不足时不允许启用，也不允许把旧
显著性结果复制到新方案里。
"""
from __future__ import annotations

from typing import Any

from app.services import conclusion as conclusion_flow
from app.store import store

MODULE = "analysis"
REQUIRED_FIELDS = ["方案编号", "分析主题", "数据集范围"]
STATUS_ORDER = ["草稿", "方案评审中", "已启用", "已归档"]
ACTION_RULES = {"提交方案评审": "方案评审中", "启用方案": "已启用", "归档方案": "已归档"}
NEGATIVE_ACTIONS = []
PENDING_KEY = conclusion_flow.PENDING_KEY


def _linked_experiment(entry: dict[str, Any]) -> dict[str, Any] | None:
    experiment_code = str(entry.get("关联实验编号") or "").strip()
    if not experiment_code:
        return None
    return store.find_by("abtest", "方案编号", experiment_code)


def _pending(entry: dict[str, Any], message: str) -> dict[str, Any]:
    items = [message]
    entry[PENDING_KEY] = items
    entry["pending"] = True
    entry["abnormal"] = True
    return entry


def validate_enable(entry: dict[str, Any]) -> str | None:
    """返回不能启用的原因；None 表示可以启用。"""
    missing = [field for field in REQUIRED_FIELDS if not str(entry.get(field) or "").strip()]
    if missing:
        return f"分析方案缺少必填字段：{'、'.join(missing)}"

    experiment = _linked_experiment(entry)
    if experiment is None:
        linked = str(entry.get("关联实验编号") or "").strip()
        if linked:
            return f"关联实验 {linked} 不存在或已归档"
        # 未关联实验的方案按普通方案启用，不参与结论沉淀。
        return None

    if experiment.get("status") != "已结项":
        return f"关联实验 {experiment.get('方案编号')} 尚未结项，不能启用分析方案"

    experiment_id = int(experiment.get("id") or 0)
    conclusion = conclusion_flow._latest_conclusion(experiment_id)
    if conclusion is None:
        return f"关联实验 {experiment.get('方案编号')} 未沉淀分析结论"

    bound_version = str(entry.get("结论版本") or "").strip()
    if bound_version and bound_version != str(conclusion.get("结论版本")):
        return (
            f"分析方案绑定结论版本 {bound_version}，实验当前结论版本为 "
            f"{conclusion.get('结论版本')}，需重新生成方案版本"
        )

    metric_code = str(conclusion.get("核心指标") or "").strip()
    metric = conclusion_flow.latest_metric(metric_code)
    current_version = conclusion_flow._metric_version(metric)
    frozen_version = conclusion_flow._version_number(conclusion.get("指标口径版本"))
    if metric is None:
        return f"核心指标 {metric_code} 未在指标口径中登记，无法确认口径版本"
    if _metric_newer(current_version, frozen_version):
        return (
            f"核心指标 {metric_code} 口径已由 {conclusion.get('指标口径版本')} 换版为 "
            f"{current_version}，旧显著性结果不可复制，需重新计算"
        )

    output = conclusion_flow._find_output(conclusion.get("分析输出编号"))
    if output is None:
        return f"结论 {conclusion.get('结论编号')} 缺少分析输出，需重新计算"
    if str(output.get("status")) != "有效":
        return (
            f"分析输出 {output.get('输出编号')} 已失效"
            f"（{output.get('失效原因') or '需重新计算'}），不能直接启用"
        )

    min_sample = conclusion_flow.parse_int(conclusion.get("最小样本量"))
    actual_sample = conclusion_flow.parse_int(conclusion.get("实际样本量"))
    if min_sample is not None and (actual_sample is None or actual_sample < min_sample):
        return (
            f"实际样本量 {actual_sample if actual_sample is not None else '缺失'} "
            f"未达最小样本量 {min_sample}，不能启用分析方案"
        )
    return None


def _metric_newer(current: str | None, frozen_number: int) -> bool:
    return conclusion_flow._version_number(current) > frozen_number


def sync_stale_versions() -> None:
    """把引用了已换版核心指标的分析输出、方案与实验统一标成待重算。

    任何读取路径（实验详情、分析输出、审计回查）都先跑一遍，保证换版发生在
    结项之后时，列表和详情也不会继续显示旧的显著性结果或「已启用」状态。
    本函数幂等，重复调用不会新增记录。
    """
    for output in store.rows(conclusion_flow.OUTPUT_MODULE):
        if str(output.get("status")) != "有效":
            continue
        metric = conclusion_flow.latest_metric(str(output.get("核心指标") or ""))
        current = conclusion_flow._metric_version(metric)
        frozen = conclusion_flow._version_number(output.get("指标口径版本"))
        if metric is None or not _metric_newer(current, frozen):
            continue
        reason = (
            f"核心指标口径由 {output.get('指标口径版本')} 变更为 {current}，"
            "旧显著性结果不可复用"
        )
        output["status"] = "已失效"
        output["失效原因"] = reason
        output["abnormal"] = True
        output[PENDING_KEY] = [f"{reason}，需重新计算"]

    for plan in store.rows(MODULE):
        output_code = str(plan.get("分析输出编号") or "")
        if not output_code:
            continue
        output = conclusion_flow._find_output(output_code)
        if output is None or str(output.get("status")) == "有效":
            continue
        plan["分析输出状态"] = "已失效"
        plan["分析状态"] = "已失效待重算"
        plan["abnormal"] = True
        plan[PENDING_KEY] = list(output.get(PENDING_KEY) or []) or [
            output.get("失效原因") or "分析输出已失效，需重新计算"
        ]
        if str(plan.get("status")) == "已启用":
            # 已启用的方案在换版后必须退回评审中，不能继续对外显示为生效。
            plan["status"] = "方案评审中"
            plan["pending"] = True

    for experiment in store.rows("abtest"):
        output_code = str(experiment.get("分析输出编号") or "")
        if not output_code:
            continue
        output = conclusion_flow._find_output(output_code)
        if output is None or str(output.get("status")) == "有效":
            continue
        experiment["分析输出状态"] = "已失效"
        experiment["分析状态"] = "已失效待重算"
        experiment[PENDING_KEY] = list(output.get(PENDING_KEY) or []) or [
            output.get("失效原因") or "分析输出已失效，需重新计算"
        ]
        experiment["abnormal"] = True
        experiment["pending"] = True


class AnalysisService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("方案编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"分析方案 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于分析方案可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"

        if target == "已启用":
            reason = validate_enable(entry)
            if reason is not None:
                _pending(entry, f"待补充分析项：{reason}")
                entry["分析状态"] = "启用失败"
                if str(entry.get("status")) == "已启用":
                    # 之前启用过、但核心指标已换版：必须退回评审中，
                    # 不能继续显示成已启用而让旧显著性结果看起来仍然生效。
                    entry["status"] = "方案评审中"
                conclusion_flow.record_audit(
                    "启用分析方案",
                    "失败",
                    experiment=_linked_experiment(entry),
                    plan=entry,
                    output=conclusion_flow._find_output(entry.get("分析输出编号")),
                    note=reason,
                )
                return entry, f"分析方案启用失败：{reason}"

        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        if target == "已启用":
            entry["分析状态"] = "已启用"
            entry[PENDING_KEY] = []
            entry["pending"] = False
            entry["abnormal"] = False
            conclusion_flow.record_audit(
                "启用分析方案",
                "成功",
                experiment=_linked_experiment(entry),
                plan=entry,
                output=conclusion_flow._find_output(entry.get("分析输出编号")),
                note=f"分析方案 {entry.get('方案编号')} 已启用",
            )
        return entry, f"分析方案已{action}"
