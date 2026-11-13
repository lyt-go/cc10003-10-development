"""指标口径业务规则：状态流转、字段校验与版本登记都收在这里。

同一个指标编号可以登记多个口径版本；实验结项沉淀分析结论时固定引用登记时的
最新版本，之后指标再换版只会让旧分析输出失效，不会改写历史结论。
"""
from __future__ import annotations

import re
from typing import Any

from app.store import store

MODULE = "metric"
REQUIRED_FIELDS = ["指标编号", "指标名称", "计算公式"]
OPTIONAL_FIELDS = ["统计粒度", "时间窗口", "筛选条件", "精度位数", "口径版本"]
STATUS_ORDER = ["草稿", "口径评审中", "已发布", "已下线"]
ACTION_RULES = {"提交口径评审": "口径评审中", "发布指标": "已发布", "下线指标": "已下线"}
NEGATIVE_ACTIONS = ["下线指标"]


def _version_number(value: Any) -> int:
    match = re.search(r"(\d+)", str(value or ""))
    return int(match.group(1)) if match else 0


class MetricService:
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
            rows = [row for row in rows if keyword in str(row.get("指标编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        # 同编号多行时按版本号倒序，最新口径排在最前，避免列表停在旧版本。
        rows = sorted(
            rows,
            key=lambda row: (
                str(row.get("指标编号") or ""),
                -_version_number(row.get("口径版本")),
                -int(row.get("id", 0)),
            ),
        )
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
        for field in OPTIONAL_FIELDS:
            if field in values and values.get(field) is not None:
                entry[field] = values.get(field)
        metric_code = str(entry["指标编号"])
        same_code = [row for row in rows if row.get("指标编号") == metric_code]
        if not str(entry.get("口径版本") or "").strip():
            # 同编号再次登记视为换版，自动从最新版本递增，避免两行都叫同一个版本。
            latest = max(
                (_version_number(row.get("口径版本")) for row in same_code),
                default=0,
            )
            entry["口径版本"] = f"V{latest + 1}"
        entry["version_no"] = _version_number(entry["口径版本"])
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"指标定义 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于指标口径可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"指标定义已{action}"
