"""分析方案接口：维护分析方案，覆盖提交方案评审、启用方案、归档方案等动作。

启用方案会校验关联实验是否已结项、结论版本是否一致、核心指标口径是否换版、
样本量是否达标；失败时返回具体待补充分析项，而不是静默变成已启用。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services import conclusion as conclusion_flow
from app.services.analysis import AnalysisService, sync_stale_versions

router = APIRouter(prefix="/api/analysis", tags=["分析方案"])

service = AnalysisService()

LIST_FIELDS = [
    "方案编号",
    "分析主题",
    "数据集范围",
    "分析方法",
    "对比基准",
    "显著性阈值",
    "输出图表类型",
    "调度周期",
    "核心指标",
    "关联实验编号",
    "方案版本",
    "结论版本",
]
STATUSES = ["草稿", "方案评审中", "已启用", "已归档"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按方案编号检索"),
    status: str | None = Query(default=None, description="草稿、方案评审中、已启用、已归档"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按方案编号与状态过滤分析方案列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    sync_stale_versions()
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/outputs", response_model=PageResult[dict])
def list_outputs(
    experiment_code: str | None = Query(default=None, description="按实验编号过滤"),
    plan_code: str | None = Query(default=None, description="按分析方案编号过滤"),
    page: int = 1,
    size: int = 50,
) -> PageResult[dict]:
    """分析输出清单：换版后旧输出仍按原版本保留，只是状态标成已失效。"""
    sync_stale_versions()
    rows = list(conclusion_flow.store.rows(conclusion_flow.OUTPUT_MODULE))
    if experiment_code:
        rows = [row for row in rows if row.get("实验编号") == experiment_code]
    if plan_code:
        rows = [row for row in rows if row.get("分析方案编号") == plan_code]
    total = len(rows)
    start = max(page - 1, 0) * size
    return PageResult(items=rows[start:start + size], total=total, page=page, size=size)


@router.get("/outputs/{identifier}", response_model=dict)
def get_output(identifier: str) -> dict:
    """分析输出详情：同时带回结论、方案、实验与审计，便于按版本互查。"""
    sync_stale_versions()
    output = conclusion_flow.find_output(identifier)
    if output is None:
        raise HTTPException(status_code=404, detail=f"分析输出 {identifier} 不存在")
    return conclusion_flow.output_bundle(output)


@router.get("/audits", response_model=PageResult[dict])
def list_audits(
    experiment_code: str | None = Query(default=None, description="按实验编号过滤"),
    conclusion_version: str | None = Query(default=None, description="按结论版本过滤"),
    plan_code: str | None = Query(default=None, description="按分析方案编号过滤"),
    page: int = 1,
    size: int = 50,
) -> PageResult[dict]:
    """审计记录：只追加不修改，可按实验、结论版本、方案编号回查。"""
    sync_stale_versions()
    rows = list(conclusion_flow.store.rows(conclusion_flow.AUDIT_MODULE))
    if experiment_code:
        rows = [row for row in rows if row.get("实验编号") == experiment_code]
    if conclusion_version:
        rows = [row for row in rows if row.get("结论版本") == conclusion_version]
    if plan_code:
        rows = [row for row in rows if row.get("分析方案编号") == plan_code]
    total = len(rows)
    start = max(page - 1, 0) * size
    return PageResult(items=rows[start:start + size], total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出分析方案清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "analysis", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条分析方案明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"分析方案 {entry_id} 不存在或已归档")
    return entry


@router.get("/{entry_id}/detail", response_model=dict)
def get_detail(entry_id: int) -> dict:
    """分析方案详情：带结论、输出、实验版本与审计，供按版本互查。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"分析方案 {entry_id} 不存在或已归档")
    sync_stale_versions()
    return conclusion_flow.analysis_bundle(entry)


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条分析方案，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="分析方案已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条分析方案执行提交方案评审、启用方案、归档方案；不允许的动作会被拦下。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)

