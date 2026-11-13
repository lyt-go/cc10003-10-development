"""实验结项到分析方案的结论沉淀。

收口三件事，保证实验方案与分析方案共享同一份核心指标与结论版本：

1. 结项时先看核心指标是否达到最小样本量，未达到不生成分析输出；
2. 生成结论与分析输出时，把核心指标口径版本、对比基准、样本范围一起做快照；
3. 同一实验重复结项只保留一个方案版本；核心指标换版时旧输出保留原版本并标记
   失效，新结论必须重新计算，绝不复制旧显著性结果。

审计记录只追加不修改；实验、分析方案、分析输出之间靠版本字段互相定位。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.store import INTERNAL_MODULES as INTERNAL_TABLES
from app.store import store

METRIC_MODULE = "metric"
ABTEST_MODULE = "abtest"
ANALYSIS_MODULE = "analysis"
CONCLUSION_MODULE = "experiment_conclusion"
OUTPUT_MODULE = "analysis_output"
AUDIT_MODULE = "analysis_audit"

INTERNAL_MODULES = set(INTERNAL_TABLES)

DEFAULT_THRESHOLD = 0.05
PENDING_KEY = "待补充分析项"
PLAN_REQUIRED_FIELDS = ("方案编号", "分析主题", "数据集范围")


# ---------------------------------------------------------------------------
# 基础解析：示例数据里的样本量常写成「20000 人」「20,000」这类文本
# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def parse_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"\d[\d,]*(?:\.\d+)?", str(value))
    if match is None:
        return None
    return int(float(match.group(0).replace(",", "")))


def parse_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"\d+(?:\.\d+)?", str(value))
    if match is None:
        return None
    return float(match.group(0))


def _version_number(value: Any) -> int:
    match = re.search(r"(\d+)", str(value or ""))
    return int(match.group(1)) if match else 0


def _next_version(value: Any) -> str:
    return f"V{_version_number(value) + 1}"


def _effective_metric_version(metric: dict[str, Any] | None) -> str | None:
    if metric is None:
        return None
    version = str(metric.get("口径版本") or "").strip()
    return version or "未标注"


def _metric_version(metric: dict[str, Any] | None) -> str | None:
    return _effective_metric_version(metric)


def _latest_plan_by_code(plan_code: str) -> dict[str, Any] | None:
    rows = [row for row in store.rows(ANALYSIS_MODULE) if row.get("方案编号") == plan_code]
    if not rows:
        return None
    return max(rows, key=lambda row: int(row.get("id", 0)))


def latest_metric(metric_code: str) -> dict[str, Any] | None:
    """按指标编号取最新登记的一版口径；同编号多行时以版本号最大、登记最晚的为准。"""
    rows = [row for row in store.rows(METRIC_MODULE) if row.get("指标编号") == metric_code]
    if not rows:
        return None
    return max(
        rows,
        key=lambda row: (
            _version_number(_effective_metric_version(row)),
            int(row.get("id", 0)),
        ),
    )


def _next_analysis_code() -> str:
    numbers = [
        int(match.group(1))
        for row in store.rows(ANALYSIS_MODULE)
        if (match := re.search(r"(\d+)\s*$", str(row.get("方案编号") or "")))
    ]
    return f"ANAL-{max(numbers, default=0) + 1:04d}"


def _conclusions_for(experiment_id: int) -> list[dict[str, Any]]:
    rows = [
        row
        for row in store.rows(CONCLUSION_MODULE)
        if int(row.get("实验id") or 0) == experiment_id
    ]
    return sorted(rows, key=lambda row: (_version_number(row.get("结论版本")), int(row.get("id", 0))))


def _latest_conclusion(experiment_id: int) -> dict[str, Any] | None:
    rows = _conclusions_for(experiment_id)
    return rows[-1] if rows else None


def _outputs_for(experiment_id: int) -> list[dict[str, Any]]:
    rows = [
        row
        for row in store.rows(OUTPUT_MODULE)
        if int(row.get("实验id") or 0) == experiment_id
    ]
    return sorted(rows, key=lambda row: (_version_number(row.get("输出版本")), int(row.get("id", 0))))


def _find_output(output_id: Any) -> dict[str, Any] | None:
    if not output_id:
        return None
    for row in store.rows(OUTPUT_MODULE):
        if str(row.get("输出编号")) == str(output_id):
            return row
    return None


def _analysis_rows_for_experiment(experiment_code: str) -> list[dict[str, Any]]:
    return [
        row
        for row in store.rows(ANALYSIS_MODULE)
        if row.get("关联实验编号") == experiment_code
    ]


def _plans_for_codes(codes: set[str]) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    for code in sorted(codes):
        plan = _latest_plan_by_code(code)
        if plan is not None:
            plans.append(plan)
    return plans


# ---------------------------------------------------------------------------
# 审计：只追加，不覆盖历史
# ---------------------------------------------------------------------------
def record_audit(
    action: str,
    result: str,
    *,
    experiment: dict[str, Any] | None = None,
    conclusion: dict[str, Any] | None = None,
    plan: dict[str, Any] | None = None,
    output: dict[str, Any] | None = None,
    note: str = "",
) -> dict[str, Any]:
    entry = {
        "id": store.next_id(AUDIT_MODULE),
        "status": result,
        "pending": False,
        "abnormal": result == "失败",
        "action": action,
        "动作": action,
        "结果": result,
        "实验id": (experiment or {}).get("id"),
        "实验编号": (experiment or {}).get("方案编号"),
        "实验版本": (experiment or {}).get("实验版本"),
        "结论编号": (conclusion or {}).get("结论编号"),
        "结论版本": (conclusion or {}).get("结论版本"),
        "分析方案id": (plan or {}).get("id"),
        "分析方案编号": (plan or {}).get("方案编号"),
        "方案版本": (plan or {}).get("方案版本"),
        "分析输出编号": (output or {}).get("输出编号"),
        "输出版本": (output or {}).get("输出版本"),
        "说明": note,
        "时间": _now(),
    }
    store.append(AUDIT_MODULE, entry)
    return entry


# ---------------------------------------------------------------------------
# 版本互查
# ---------------------------------------------------------------------------
def _audits_for(**filters: Any) -> list[dict[str, Any]]:
    rows = list(store.rows(AUDIT_MODULE))
    for field, value in filters.items():
        if value not in (None, ""):
            rows = [row for row in rows if str(row.get(field)) == str(value)]
    return rows


def _experiment_by_code(code: str) -> dict[str, Any] | None:
    return store.find_by(ABTEST_MODULE, "方案编号", code)


def _plan_by_code(code: str) -> dict[str, Any] | None:
    return _latest_plan_by_code(code)


def experiment_bundle(experiment: dict[str, Any]) -> dict[str, Any]:
    """实验详情 + 结论 + 分析输出 + 关联分析方案 + 审计，全部带版本字段。"""
    experiment_id = int(experiment.get("id") or 0)
    experiment_code = str(experiment.get("方案编号") or "")
    conclusions = _conclusions_for(experiment_id)
    outputs = _outputs_for(experiment_id)
    plan_codes = {
        str(row.get("分析方案编号"))
        for row in conclusions + outputs + [experiment]
        if row.get("分析方案编号")
    }
    plans = _plans_for_codes(plan_codes)
    audits = _audits_for(实验编号=experiment_code)
    return {
        "experiment": experiment,
        "conclusions": conclusions,
        "outputs": outputs,
        "analysis_plans": plans,
        "audits": audits,
        "versions": {
            "实验版本": experiment.get("实验版本"),
            "结论版本": experiment.get("结论版本"),
            "方案版本": experiment.get("方案版本"),
            "输出版本": (outputs[-1] or {}).get("输出版本") if outputs else None,
            "核心指标": experiment.get("核心指标"),
            "指标口径版本": experiment.get("核心指标版本"),
            PENDING_KEY: list(experiment.get(PENDING_KEY) or []),
        },
    }


def analysis_bundle(plan: dict[str, Any]) -> dict[str, Any]:
    """分析方案 + 结论 + 输出 + 实验 + 审计。"""
    plan_code = str(plan.get("方案编号") or "")
    outputs = [
        row
        for row in store.rows(OUTPUT_MODULE)
        if row.get("分析方案编号") == plan_code
    ]
    outputs.sort(key=lambda row: (_version_number(row.get("输出版本")), int(row.get("id", 0))))
    conclusion_ids = {str(row.get("结论编号")) for row in outputs}
    conclusions = [
        row
        for row in store.rows(CONCLUSION_MODULE)
        if str(row.get("结论编号")) in conclusion_ids
    ]
    experiment_code = str(plan.get("关联实验编号") or "")
    experiment = _experiment_by_code(experiment_code) if experiment_code else None
    audits = _audits_for(分析方案编号=plan_code)
    return {
        "analysis_plan": plan,
        "outputs": outputs,
        "conclusions": conclusions,
        "experiment": experiment,
        "audits": audits,
        "versions": {
            "方案版本": plan.get("方案版本"),
            "结论版本": plan.get("结论版本"),
            "实验版本": plan.get("关联实验版本"),
            "核心指标": plan.get("核心指标"),
            "指标口径版本": plan.get("指标口径版本"),
            PENDING_KEY: list(plan.get(PENDING_KEY) or []),
        },
    }


def output_bundle(output: dict[str, Any]) -> dict[str, Any]:
    conclusion = None
    for row in store.rows(CONCLUSION_MODULE):
        if (
            str(row.get("结论编号")) == str(output.get("结论编号"))
            and str(row.get("结论版本")) == str(output.get("结论版本"))
        ):
            conclusion = row
            break
    if conclusion is None:
        conclusion = store.find_by(CONCLUSION_MODULE, "结论编号", output.get("结论编号"))
    plan = _plan_by_code(str(output.get("分析方案编号") or ""))
    experiment = _experiment_by_code(str(output.get("实验编号") or ""))
    audits = _audits_for(分析输出编号=output.get("输出编号"))
    return {
        "output": output,
        "conclusion": conclusion,
        "analysis_plan": plan,
        "experiment": experiment,
        "audits": audits,
    }


def find_output(identifier: Any) -> dict[str, Any] | None:
    """按输出编号或自增 id 读取分析输出。"""
    for row in store.rows(OUTPUT_MODULE):
        if str(row.get("输出编号")) == str(identifier) or str(row.get("id")) == str(identifier):
            return row
    return None


def find_conclusion(identifier: Any) -> dict[str, Any] | None:
    """按结论编号或自增 id 读取结论；同一编号多版本时返回最新一版。"""
    matches = [
        row
        for row in store.rows(CONCLUSION_MODULE)
        if str(row.get("结论编号")) == str(identifier) or str(row.get("id")) == str(identifier)
    ]
    if not matches:
        return None
    return max(
        matches,
        key=lambda row: (_version_number(row.get("结论版本")), int(row.get("id", 0))),
    )


def trace(
    *,
    experiment_code: str | None = None,
    experiment_version: str | None = None,
    conclusion_code: str | None = None,
    conclusion_version: str | None = None,
    plan_code: str | None = None,
    plan_version: str | None = None,
    output_code: str | None = None,
    output_version: str | None = None,
) -> dict[str, Any]:
    """按版本互查：任一版本条件都能把实验、结论、方案、输出与审计串起来。"""
    experiment = _experiment_by_code(experiment_code) if experiment_code else None
    conclusion: dict[str, Any] | None = None
    if conclusion_code:
        conclusion = find_conclusion(conclusion_code)
        if experiment is None and conclusion is not None:
            experiment = store.find(ABTEST_MODULE, int(conclusion.get("实验id") or 0))
    elif experiment is not None:
        for row in reversed(_conclusions_for(int(experiment.get("id") or 0))):
            if not conclusion_version or row.get("结论版本") == conclusion_version:
                conclusion = row
                break
    if conclusion is None and conclusion_version:
        for row in store.rows(CONCLUSION_MODULE):
            if row.get("结论版本") == conclusion_version:
                conclusion = row
                break

    outputs = list(store.rows(OUTPUT_MODULE))
    if experiment_code:
        outputs = [row for row in outputs if row.get("实验编号") == experiment_code]
    if experiment_version:
        outputs = [row for row in outputs if row.get("实验版本") == experiment_version]
    if conclusion_code:
        outputs = [row for row in outputs if row.get("结论编号") == conclusion_code]
    if conclusion_version:
        outputs = [row for row in outputs if row.get("结论版本") == conclusion_version]
    if plan_code:
        outputs = [row for row in outputs if row.get("分析方案编号") == plan_code]
    if plan_version:
        outputs = [row for row in outputs if row.get("方案版本") == plan_version]
    if output_code:
        outputs = [row for row in outputs if row.get("输出编号") == output_code]
    if output_version:
        outputs = [row for row in outputs if row.get("输出版本") == output_version]

    if plan_code is None and outputs:
        plan_code = str(outputs[-1].get("分析方案编号") or "") or None
    plan = _plan_by_code(plan_code) if plan_code else None

    audits = _audits_for(
        实验编号=experiment_code,
        实验版本=experiment_version,
        结论编号=conclusion_code,
        结论版本=conclusion_version,
        分析方案编号=plan_code,
        方案版本=plan_version,
        分析输出编号=output_code,
        输出版本=output_version,
    )
    return {
        "experiment": experiment,
        "conclusion": conclusion,
        "analysis_plan": plan,
        "outputs": outputs,
        "audits": audits,
    }


# ---------------------------------------------------------------------------
# 结项沉淀
# ---------------------------------------------------------------------------
def _blockers(
    *,
    experiment: dict[str, Any],
    metric: dict[str, Any] | None,
    metric_code: str,
    min_sample: int | None,
    actual_sample: int | None,
    sample_range: str,
    p_value: float | None,
) -> list[str]:
    blockers: list[str] = []
    if not metric_code:
        blockers.append("实验未配置核心指标，无法沉淀分析结论")
    elif metric is None:
        blockers.append(f"核心指标 {metric_code} 未在指标口径中登记，无法确认口径版本")
    elif metric.get("status") == "已下线":
        blockers.append(f"核心指标 {metric_code} 已下线，不能据此生成分析输出")
    if not str(experiment.get("对照组定义") or "").strip():
        blockers.append("实验未定义对照组，无法确定对比基准")
    if min_sample is None:
        blockers.append("实验未配置最小样本量，无法判断是否达到分析门槛")
    if actual_sample is None:
        blockers.append("未提供实际样本量，无法校验最小样本量")
    elif min_sample is not None and actual_sample < min_sample:
        blockers.append(
            f"实际样本量 {actual_sample} 未达最小样本量 {min_sample}，暂不生成分析输出"
        )
    if not sample_range:
        blockers.append("未提供样本范围，分析输出无法标注统计口径")
    if p_value is None:
        blockers.append("缺少显著性检验结果（p值），需重新计算后才能生成分析输出")
    return blockers


def _plan_missing_fields(plan: dict[str, Any]) -> list[str]:
    return [
        field
        for field in PLAN_REQUIRED_FIELDS
        if not str(plan.get(field) or "").strip()
    ]


def _create_plan(
    experiment: dict[str, Any],
    metric_code: str,
    *,
    config: dict[str, Any] | None = None,
    note_source: str | None = None,
) -> dict[str, Any]:
    """新建一份独立分析方案。

    只沿用阈值、方法这类方案配置；对比基准与样本范围一律取当前实验的，
    避免新实验借用其它实验的旧基准。
    """
    config = config or {}
    control = str(experiment.get("对照组定义") or "").strip()
    plan = {
        "id": store.next_id(ANALYSIS_MODULE),
        "status": "草稿",
        "pending": True,
        "abnormal": False,
        "方案编号": _next_analysis_code(),
        "分析主题": str(config.get("分析主题") or "").strip()
        or f"{experiment.get('实验名称') or experiment.get('方案编号')}结论分析",
        "数据集范围": str(config.get("数据集范围") or "").strip()
        or str(experiment.get("样本范围") or "").strip(),
        "分析方法": str(config.get("分析方法") or "").strip() or "双样本检验",
        "对比基准": control,
        "显著性阈值": str(config.get("显著性阈值") or DEFAULT_THRESHOLD),
        "输出图表类型": str(config.get("输出图表类型") or "").strip() or "柱状图",
        "调度周期": str(config.get("调度周期") or "").strip() or "一次性",
        "核心指标": metric_code,
        "关联实验编号": experiment.get("方案编号"),
        PENDING_KEY: [],
    }
    if note_source:
        plan["来源方案"] = note_source
    store.append(ANALYSIS_MODULE, plan)
    return plan


def _resolve_plan(
    experiment: dict[str, Any],
    metric_code: str,
    requested_plan: str,
    previous: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, str | None, list[str]]:
    """确定本次结论交付的分析方案。

    返回 (方案, 启用错误, 说明)。启用错误只在方案缺失/归档/字段不全时出现；
    方案被其它实验占用或核心指标不一致时改为复制或新建，避免借用旧基准。
    """
    experiment_code = str(experiment.get("方案编号") or "")
    notes: list[str] = []

    if requested_plan:
        plan = _plan_by_code(requested_plan)
        if plan is None:
            return None, f"分析方案 {requested_plan} 不存在", notes
        if str(plan.get("status")) == "已归档":
            return plan, f"分析方案 {requested_plan} 已归档，无法启用", notes
        missing = _plan_missing_fields(plan)
        if missing:
            return plan, f"分析方案 {requested_plan} 缺少必填字段：{'、'.join(missing)}", notes
        bound_to = str(plan.get("关联实验编号") or "").strip()
        plan_metric = str(plan.get("核心指标") or "").strip()
        if bound_to and bound_to != experiment_code:
            fork = _create_plan(
                experiment,
                metric_code,
                config=plan,
                note_source=requested_plan,
            )
            if plan_metric and plan_metric != metric_code:
                notes.append(
                    f"分析方案 {requested_plan} 属于实验 {bound_to} 且核心指标不一致，"
                    f"未借用其对比基准，已新建 {fork['方案编号']}"
                )
            else:
                notes.append(
                    f"分析方案 {requested_plan} 已绑定实验 {bound_to}，"
                    f"为保证样本范围与历史结论隔离，未沿用其对比基准，"
                    f"已新建独立方案 {fork['方案编号']}"
                )
            return fork, None, notes
        if plan_metric and plan_metric != metric_code:
            fork = _create_plan(experiment, metric_code, note_source=requested_plan)
            notes.append(
                f"分析方案 {requested_plan} 核心指标为 {plan_metric}，与实验核心指标 "
                f"{metric_code} 不一致，未借用其对比基准，已新建 {fork['方案编号']}"
            )
            return fork, None, notes
        return plan, None, notes

    linked = _analysis_rows_for_experiment(experiment_code)
    if linked:
        plan = max(linked, key=lambda row: int(row.get("id", 0)))
        if str(plan.get("status")) == "已归档":
            return plan, f"分析方案 {plan.get('方案编号')} 已归档，无法启用", notes
        missing = _plan_missing_fields(plan)
        if missing:
            return plan, (
                f"分析方案 {plan.get('方案编号')} 缺少必填字段：{'、'.join(missing)}"
            ), notes
        return plan, None, notes

    previous_plan_code = str((previous or {}).get("分析方案编号") or "").strip()
    if previous_plan_code:
        plan = _plan_by_code(previous_plan_code)
        if plan is not None:
            if str(plan.get("status")) == "已归档":
                return plan, f"分析方案 {previous_plan_code} 已归档，无法启用", notes
            return plan, None, notes

    plan = _create_plan(experiment, metric_code)
    notes.append(f"已按实验结论新建分析方案 {plan['方案编号']}")
    return plan, None, notes


def _apply_experiment_state(
    experiment: dict[str, Any],
    *,
    pending_items: list[str],
    **fields: Any,
) -> None:
    experiment.update(fields)
    experiment["status"] = "已结项"
    experiment[PENDING_KEY] = pending_items
    experiment["pending"] = bool(pending_items)
    experiment["abnormal"] = bool(pending_items)


def _invalidate_old(
    *,
    experiment: dict[str, Any],
    previous: dict[str, Any],
    metric_code: str,
    old_version: str | None,
    new_version: str | None,
) -> dict[str, Any]:
    """核心指标换版：旧输出保留原版本与旧显著性结果，仅标记失效。"""
    old_output = _find_output(previous.get("分析输出编号"))
    if old_output is not None:
        old_output["status"] = "已失效"
        old_output["失效原因"] = (
            f"核心指标口径由 {old_version} 变更为 {new_version}，旧显著性结果不可复用"
        )
    previous["status"] = "历史"
    previous["失效原因"] = old_output.get("失效原因") if old_output else "核心指标已换版"
    old_plan = _plan_by_code(str(previous.get("分析方案编号") or ""))
    if old_plan is not None:
        old_plan["分析输出状态"] = "已失效"
        old_plan["分析状态"] = "待重算"
        old_plan["abnormal"] = True
        old_plan[PENDING_KEY] = [
            f"核心指标 {metric_code} 口径已换版，旧显著性结果不可复用，需重新计算"
        ]
    audit = record_audit(
        "指标换版失效",
        "成功",
        experiment=experiment,
        conclusion=previous,
        plan=old_plan,
        output=old_output,
        note=(
            f"核心指标 {metric_code} 口径由 {old_version} 变更为 {new_version}，"
            f"旧输出 {previous.get('分析输出编号')} 保留原版本并标记失效"
        ),
    )
    return audit


def settle_experiment(
    experiment: dict[str, Any], values: dict[str, Any] | None = None
) -> dict[str, Any]:
    """结项时沉淀结论；调用方负责把实验状态置为已结项，这里只处理分析侧。"""
    values = values or {}
    experiment_id = int(experiment.get("id") or 0)
    experiment_code = str(experiment.get("方案编号") or "")
    metric_code = str(experiment.get("核心指标") or "").strip()
    min_sample = parse_int(experiment.get("最小样本量"))
    actual_sample = parse_int(values.get("实际样本量"))
    if actual_sample is None:
        actual_sample = parse_int(experiment.get("实际样本量"))
    sample_range = str(values.get("样本范围") or experiment.get("样本范围") or "").strip()
    p_value = parse_float(values.get("p值"))
    requested_plan = str(
        values.get("分析方案编号") or experiment.get("分析方案编号") or ""
    ).strip()
    notes: list[str] = []

    previous = _latest_conclusion(experiment_id)
    metric = latest_metric(metric_code)
    current_metric_version = _metric_version(metric)

    # ---- 重复结项且口径未换版：沿用已有版本，不生成第二个方案版本 -------------
    if previous is not None and metric is not None and previous.get("指标口径版本") == current_metric_version:
        pending: list[str] = []
        # 首个结论因启用失败未交付方案时，可在重复结项时重试，但仍然复用同一结论版本。
        retry_plan: dict[str, Any] | None = None
        retrying = previous.get("status") == "待启用"
        if retrying:
            resolved, enable_error, plan_notes = _resolve_plan(
                experiment, metric_code, requested_plan, previous
            )
            notes.extend(plan_notes)
            if resolved is not None and enable_error is None:
                retry_plan = resolved
                retry_plan["status"] = "已启用"
                retry_plan["pending"] = False
                retry_plan["abnormal"] = False
                retry_plan["分析状态"] = "已启用"
                retry_plan[PENDING_KEY] = []
                previous["分析方案编号"] = resolved.get("方案编号")
                previous["status"] = "生效"
            output = _find_output(previous.get("分析输出编号"))
            if output is not None and enable_error is None:
                output["status"] = "有效"
                output["pending"] = False
                output["abnormal"] = False
                output[PENDING_KEY] = []
            previous_plan_code = str(previous.get("分析方案编号") or "")
            plan = _plan_by_code(previous_plan_code) if previous_plan_code else None
        else:
            output = _find_output(previous.get("分析输出编号"))
            plan = _plan_by_code(str(previous.get("分析方案编号") or ""))
            enable_error = None
            if plan is None:
                enable_error = f"分析方案 {previous.get('分析方案编号')} 不存在"
            elif str(plan.get("status")) == "已归档":
                enable_error = f"分析方案 {plan.get('方案编号')} 已归档，无法启用"
            elif str(output.get("status") if output else "") != "有效":
                enable_error = f"分析输出 {previous.get('分析输出编号')} 已失效，需重新计算"
            if enable_error is None and plan is not None and str(plan.get("status")) != "已启用":
                plan["status"] = "已启用"
                plan["pending"] = False
                plan["abnormal"] = False
                plan["分析状态"] = "已启用"
        if enable_error is not None:
            pending = [f"分析方案启用失败：{enable_error}"]
        _apply_experiment_state(
            experiment,
            pending_items=pending,
            **{
                "实验版本": previous.get("实验版本") or experiment.get("实验版本"),
                "核心指标版本": previous.get("指标口径版本"),
                "显著性结果": previous.get("显著性结果"),
                "对比基准": previous.get("对比基准"),
                "样本范围": previous.get("样本范围"),
                "分析状态": "启用失败" if enable_error else "已启用",
            },
        )
        if retrying:
            record_audit(
                "重试启用分析方案",
                "失败" if enable_error else "成功",
                experiment=experiment,
                conclusion=previous,
                plan=retry_plan or _plan_by_code(str(previous.get("分析方案编号") or "")),
                output=output,
                note=enable_error or "重复结项时补启用了首轮未交付的分析方案",
            )
        record_audit(
            "重复结项",
            "待补充" if enable_error else "跳过",
            experiment=experiment,
            conclusion=previous,
            plan=plan,
            output=output,
            note=(
                "实验已结项且核心指标未换版，沿用已有结论版本，不生成新方案版本"
                + (f"；{enable_error}" if enable_error else "")
            ),
        )
        return {
            "status": "enable_failed" if enable_error else "unchanged",
            "message": (
                f"实验已结项，但分析方案启用失败：{enable_error}"
                if enable_error
                else "实验已结项且核心指标未换版，沿用已有结论版本"
            ),
            "experiment": experiment,
            "conclusion": previous,
            "output": output,
            "analysis_plan": plan or retry_plan,
            PENDING_KEY: pending,
            "audits": _audits_for(实验编号=experiment_code),
        }

    # ---- 已有结论但指标定义缺失：无法校验版本，旧输出原样保留 ---------------
    if previous is not None and metric is None:
        pending = [
            f"核心指标 {metric_code or '（未配置）'} 未登记，无法确认口径版本，旧输出保持原版本"
        ]
        _apply_experiment_state(
            experiment, pending_items=pending, **{"分析状态": "待补充"}
        )
        record_audit(
            "重复结项",
            "待补充",
            experiment=experiment,
            conclusion=previous,
            note="核心指标缺失，无法校验是否换版",
        )
        return {
            "status": "blocked",
            "message": pending[0],
            "experiment": experiment,
            "conclusion": previous,
            "output": _find_output(previous.get("分析输出编号")),
            "analysis_plan": _plan_by_code(str(previous.get("分析方案编号") or "")),
            PENDING_KEY: pending,
            "audits": _audits_for(实验编号=experiment_code),
        }

    metric_changed = (
        previous is not None
        and current_metric_version is not None
        and previous.get("指标口径版本") != current_metric_version
    )
    if metric_changed:
        _invalidate_old(
            experiment=experiment,
            previous=previous,
            metric_code=metric_code,
            old_version=previous.get("指标口径版本"),
            new_version=current_metric_version,
        )
        notes.append(
            f"核心指标 {metric_code} 口径由 {previous.get('指标口径版本')} 变更为 "
            f"{current_metric_version}，旧分析输出保留原版本并已标记失效"
        )

    # ---- 门槛校验：样本量、样本范围、显著性结果缺一不可 ---------------------
    blockers = _blockers(
        experiment=experiment,
        metric=metric,
        metric_code=metric_code,
        min_sample=min_sample,
        actual_sample=actual_sample,
        sample_range=sample_range,
        p_value=p_value,
    )
    if blockers:
        if metric_changed:
            blockers.insert(
                0,
                f"核心指标 {metric_code} 口径由 {previous.get('指标口径版本')} "
                f"变更为 {current_metric_version}，旧分析输出已失效，需重新计算",
            )
        # 样本量/样本范围不达标时，旧方案里可能还挂着上一版的“已启用”，
        # 这里一并标成待补充，避免列表继续显示成生效状态。
        stale_plan: dict[str, Any] | None = None
        if previous is not None:
            stale_plan = _plan_by_code(str(previous.get("分析方案编号") or ""))
            if stale_plan is not None:
                stale_plan["分析状态"] = "已失效待重算" if metric_changed else "待补充"
                stale_plan[PENDING_KEY] = blockers
                stale_plan["abnormal"] = True
                if metric_changed or str(stale_plan.get("status")) == "已启用":
                    stale_plan["status"] = "方案评审中"
        _apply_experiment_state(
            experiment,
            pending_items=blockers,
            **{
                "实际样本量": actual_sample,
                "样本范围": sample_range or experiment.get("样本范围"),
                "分析状态": "已失效待重算" if metric_changed else "待补充",
                "分析输出状态": "已失效" if metric_changed else "未生成",
                "结项时间": _now(),
            },
        )
        record_audit(
            "结项沉淀结论",
            "待补充",
            experiment=experiment,
            conclusion=previous,
            plan=stale_plan,
            note="；".join(blockers),
        )
        return {
            "status": "blocked",
            "message": "；".join(blockers),
            "experiment": experiment,
            "conclusion": previous,
            "output": _find_output(previous.get("分析输出编号")) if previous else None,
            "analysis_plan": stale_plan,
            PENDING_KEY: blockers,
            "audits": _audits_for(实验编号=experiment_code),
        }

    # ---- 确定分析方案；启用错误只影响启用，不影响实验已结项 -----------------
    # 先把本轮样本范围落到实验上，新建方案时数据集范围才有内容可引用。
    experiment["样本范围"] = sample_range
    plan, enable_error, plan_notes = _resolve_plan(
        experiment, metric_code, requested_plan, previous
    )
    notes.extend(plan_notes)

    plan_bound_before = bool(
        plan is not None
        and str(plan.get("关联实验编号") or "").strip() == experiment_code
    )
    plan_baseline = str(plan.get("对比基准") or "").strip() if plan is not None else ""
    baseline = plan_baseline if plan_bound_before and plan_baseline else str(
        experiment.get("对照组定义") or ""
    ).strip()
    if plan is not None and plan_baseline and plan_baseline != baseline:
        notes.append(
            f"分析方案原对比基准「{plan_baseline}」未沿用，改用实验对照组「{baseline}」"
        )
        record_audit(
            "对比基准校正",
            "成功",
            experiment=experiment,
            plan=plan,
            note=notes[-1],
        )

    threshold = parse_float(plan.get("显著性阈值")) if plan is not None else None
    if threshold is None:
        threshold = DEFAULT_THRESHOLD
    significance = "显著" if p_value <= threshold else "不显著"

    # ---- 生成结论与输出快照 -------------------------------------------------
    conclusion_version = _next_version(previous.get("结论版本")) if previous is not None else "V1"
    experiment_version = (
        _next_version(previous.get("实验版本") or experiment.get("实验版本"))
        if previous is not None
        else "V1"
    )
    plan_version = str((plan or {}).get("方案版本") or "V1")
    if metric_changed:
        plan_version = _next_version(plan_version)
    output_version = f"V{len(_outputs_for(experiment_id)) + 1}"

    conclusion_id = store.next_id(CONCLUSION_MODULE)
    conclusion_code = str(previous.get("结论编号")) if previous is not None else (
        f"CONC-{conclusion_id:04d}"
    )
    output_id = store.next_id(OUTPUT_MODULE)
    output_code = f"OUTP-{output_id:04d}"
    now = _now()
    plan_code = (plan or {}).get("方案编号")
    output_status = "有效"
    if enable_error is not None:
        output_status = "待启用"

    remaining_pending: list[str] = []
    if enable_error is not None:
        remaining_pending = [f"分析方案启用失败：{enable_error}"]

    conclusion = {
        "id": conclusion_id,
        "status": "待启用" if enable_error is not None else "生效",
        "pending": False,
        "abnormal": False,
        "结论编号": conclusion_code,
        "结论版本": conclusion_version,
        "实验id": experiment_id,
        "实验编号": experiment_code,
        "实验版本": experiment_version,
        "核心指标": metric_code,
        "指标口径版本": current_metric_version,
        "最小样本量": min_sample,
        "实际样本量": actual_sample,
        "样本范围": sample_range,
        "显著性阈值": threshold,
        "p值": p_value,
        "显著性结果": significance,
        "对比基准": baseline,
        "结论摘要": f"{metric_code} 相对{baseline}的差异{significance}",
        "分析方案编号": plan_code,
        "方案版本": plan_version if plan is not None else None,
        "关联实验版本": experiment_version,
        "分析输出编号": output_code,
        "生成时间": now,
    }
    store.append(CONCLUSION_MODULE, conclusion)

    output = {
        "id": output_id,
        "status": output_status,
        "pending": enable_error is not None,
        "abnormal": enable_error is not None,
        "输出编号": output_code,
        "输出版本": output_version,
        "实验id": experiment_id,
        "实验编号": experiment_code,
        "实验版本": experiment_version,
        "结论编号": conclusion_code,
        "结论版本": conclusion_version,
        "分析方案id": (plan or {}).get("id"),
        "分析方案编号": plan_code,
        "方案版本": plan_version if plan is not None else None,
        "核心指标": metric_code,
        "指标口径版本": current_metric_version,
        "最小样本量": min_sample,
        "实际样本量": actual_sample,
        "样本范围": sample_range,
        "显著性阈值": threshold,
        "p值": p_value,
        "显著性结果": significance,
        "对比基准": baseline,
        "生成时间": now,
        "失效原因": None,
        PENDING_KEY: remaining_pending,
    }
    store.append(OUTPUT_MODULE, output)

    if plan is not None:
        plan.update(
            {
                "方案版本": plan_version,
                "核心指标": metric_code,
                "指标口径版本": current_metric_version,
                "关联实验编号": experiment_code,
                "关联实验版本": experiment_version,
                "结论编号": conclusion_code,
                "结论版本": conclusion_version,
                "显著性结果": significance,
                "样本范围": sample_range,
                "对比基准": baseline,
                "分析输出编号": output_code,
                "分析输出状态": output_status,
                "分析状态": "启用失败" if enable_error else "已启用",
                PENDING_KEY: remaining_pending,
                "pending": enable_error is not None,
                "abnormal": enable_error is not None,
            }
        )
        if enable_error is None:
            plan["status"] = "已启用"

    experiment_fields = {
        "实验版本": experiment_version,
        "实际样本量": actual_sample,
        "样本范围": sample_range,
        "核心指标版本": current_metric_version,
        "结论编号": conclusion_code,
        "结论版本": conclusion_version,
        "显著性结果": significance,
        "对比基准": baseline,
        "分析方案编号": plan_code,
        "方案版本": plan_version if plan is not None else None,
        "分析输出编号": output_code,
        "分析输出状态": output_status,
        "分析状态": "启用失败" if enable_error else "已启用",
        "结项时间": now,
    }
    _apply_experiment_state(
        experiment, pending_items=remaining_pending, **experiment_fields
    )

    record_audit(
        "生成分析输出",
        "成功",
        experiment=experiment,
        conclusion=conclusion,
        plan=plan,
        output=output,
        note="；".join(
            [
                f"实际样本量 {actual_sample} 达到最小样本量 {min_sample}",
                f"显著性结果 {significance}（p={p_value}，阈值={threshold}）",
                *notes,
            ]
        ),
    )
    if enable_error is not None:
        record_audit(
            "启用分析方案",
            "失败",
            experiment=experiment,
            conclusion=conclusion,
            plan=plan,
            output=output,
            note=enable_error,
        )
        return {
            "status": "enable_failed",
            "message": f"实验已结项，但分析方案启用失败：{enable_error}",
            "experiment": experiment,
            "conclusion": conclusion,
            "output": output,
            "analysis_plan": plan,
            PENDING_KEY: remaining_pending,
            "audits": _audits_for(实验编号=experiment_code),
        }

    record_audit(
        "启用分析方案",
        "成功",
        experiment=experiment,
        conclusion=conclusion,
        plan=plan,
        output=output,
        note=f"同一结论已交付分析方案 {plan_code} 启用",
    )
    return {
        "status": "generated",
        "message": "实验已结项，结论已沉淀并启用分析方案",
        "experiment": experiment,
        "conclusion": conclusion,
        "output": output,
        "analysis_plan": plan,
        PENDING_KEY: [],
        "audits": _audits_for(实验编号=experiment_code),
    }
