"""实验设计接口：维护实验方案，覆盖提交方案评审、启动实验、结项实验等动作。

结项动作会顺带沉淀分析结论：核心指标达到最小样本量才生成分析输出，并把同一
结论交给分析方案启用。实验详情、分析输出与审计记录可按版本互查。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services import conclusion as conclusion_flow
from app.services.abtest import AbtestService
from app.services.analysis import sync_stale_versions

router = APIRouter(prefix="/api/abtest", tags=["实验设计"])

service = AbtestService()

LIST_FIELDS = [
    "方案编号",
    "实验名称",
    "对照组定义",
    "实验组定义",
    "分流比例",
    "核心指标",
    "最小样本量",
    "预计周期",
    "实验版本",
    "结论版本",
    "分析方案编号",
    "分析输出编号",
    "显著性结果",
    "分析状态",
    "待补充分析项",
]
STATUSES = ["草稿", "方案评审中", "实验中", "已结项", "已取消"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按方案编号检索"),
    status: str | None = Query(default=None, description="草稿、方案评审中、实验中、已结项、已取消"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按方案编号与状态过滤实验设计列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    sync_stale_versions()
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出实验设计清单：返回当前过滤条件下的全量数据。"""
    sync_stale_versions()
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "abtest", "total": total, "items": items}


@router.get("/trace", response_model=dict)
def trace_versions(
    experiment_code: str | None = Query(default=None, description="实验编号，如 ABTE-0003"),
    experiment_version: str | None = Query(default=None, description="实验版本，如 V1"),
    conclusion_code: str | None = Query(default=None, description="结论编号，如 CONC-0001"),
    conclusion_version: str | None = Query(default=None, description="结论版本"),
    plan_code: str | None = Query(default=None, description="分析方案编号，如 ANAL-0002"),
    plan_version: str | None = Query(default=None, description="方案版本"),
    output_code: str | None = Query(default=None, description="分析输出编号，如 OUTP-0001"),
    output_version: str | None = Query(default=None, description="输出版本"),
) -> dict:
    """按版本互查实验、结论、分析方案、分析输出与审计记录；条件为空时返回空集合。"""
    sync_stale_versions()
    return conclusion_flow.trace(
        experiment_code=experiment_code,
        experiment_version=experiment_version,
        conclusion_code=conclusion_code,
        conclusion_version=conclusion_version,
        plan_code=plan_code,
        plan_version=plan_version,
        output_code=output_code,
        output_version=output_version,
    )


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条实验方案明细；不存在时给出可读的错误说明。"""
    if service.get_entry(entry_id) is None:
        raise HTTPException(status_code=404, detail=f"实验方案 {entry_id} 不存在或已归档")
    sync_stale_versions()
    return service.get_entry(entry_id)


@router.get("/{entry_id}/detail", response_model=dict)
def get_detail(entry_id: int) -> dict:
    """实验详情：带结论版本、分析输出、关联分析方案与审计记录，供按版本互查。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"实验方案 {entry_id} 不存在或已归档")
    sync_stale_versions()
    return conclusion_flow.experiment_bundle(entry)


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条实验方案，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="实验方案已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """执行提交方案评审、启动实验、结项实验；结项会同时生成并启用分析方案。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message, settlement = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    extra = {"settlement": settlement} if settlement is not None else None
    return ActionResult(ok=True, message=message, entry=entry, extra=extra)
