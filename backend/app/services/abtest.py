"""实验设计业务规则：状态流转、字段校验与结项沉淀都收在这里。

结项不是单纯把状态改成「已结项」：核心指标达到最小样本量才会生成分析输出，
并把显著性结果、对比基准和实验版本交给分析方案启用。
"""
from __future__ import annotations

from typing import Any

from app.services import conclusion as conclusion_flow
from app.store import store

MODULE = "abtest"
REQUIRED_FIELDS = ["方案编号", "实验名称", "对照组定义"]
STATUS_ORDER = ["草稿", "方案评审中", "实验中", "已结项", "已取消"]
ACTION_RULES = {"提交方案评审": "方案评审中", "启动实验": "实验中", "结项实验": "已结项"}
NEGATIVE_ACTIONS = []
SETTLEMENT_KEY = conclusion_flow.PENDING_KEY


class AbtestService:
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

    def run_action(
        self, entry_id: int, action: str, values: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
        """执行状态流转。

        返回 (实验记录, 提示语, 结项沉淀结果)。结项时的沉淀结果带结论、分析输出
        和待补充分析项，路由层据此回显而不是只看实验状态。
        """
        values = values or {}
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"实验方案 {entry_id} 不存在或已归档", None
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于实验设计可执行范围", None
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里", None
        if target != "已结项":
            entry["status"] = target
            entry["pending"] = target != STATUS_ORDER[-1]
            entry["abnormal"] = action in NEGATIVE_ACTIONS
            return entry, f"实验方案已{action}", None

        settlement = conclusion_flow.settle_experiment(entry, values)
        return entry, settlement["message"], settlement
