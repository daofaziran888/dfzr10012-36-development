"""反压采样数据结果接口：采样写入、结果列表、告警待办与概览都走这里。

数据联动口径：读取 BACK 反压指标与 ALAR 告警规则，校验严重级别后写入反压采样数据结果，
结果中保留建议并行度、告警待办和采样版本；同一算子只保留最新版本。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.bpsample import bpsample_service

router = APIRouter(prefix="/api/bpsample", tags=["反压采样数据结果"])

service = bpsample_service

LIST_FIELDS = ["结果编号", "指标编号", "阻塞算子", "缓冲区水位", "消费延迟", "采样时间", "严重级别", "建议并行度", "告警待办", "采样版本"]
SEVERITIES = ["正常", "轻微", "严重", "已恢复"]


@router.get("/overview")
def sample_overview() -> dict[str, Any]:
    """概览信息：采样结果总量、严重算子、待处理告警与已恢复算子一屏核对。"""
    return service.overview()


@router.get("/todos")
def list_todos() -> dict[str, Any]:
    """列出告警待办；校验只依据最终确认的反压采样数据结果，已恢复算子不再显示严重待办。"""
    todos = service.list_todos()
    return {"items": todos, "total": len(todos)}


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出反压采样数据结果清单：返回当前全量结果，跨链路共用同一份。"""
    items, total = service.list_results(page=1, size=10000)
    return {"module": "bpsample", "total": total, "items": items}


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按指标编号或阻塞算子检索"),
    severity: str | None = Query(default=None, description="正常、轻微、严重、已恢复"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按指标编号、阻塞算子与严重级别过滤采样结果；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_results(keyword=keyword, severity=severity, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条反压采样数据结果；不存在时给出可读的错误说明。"""
    entry = service.get_result(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"反压采样数据结果 {entry_id} 不存在或已归档")
    return entry


@router.post("/sample", response_model=ActionResult)
def run_sample(payload: EntryPayload) -> ActionResult:
    """执行反压采样：可按指标编号或指标 id 指定单条，缺省时采样全部反压指标。

    同一算子的并发采样只保留最新版本；严重反压只生成一条告警待办；
    恢复后采样结果与告警状态一起更新。
    """
    keyword = str(payload.values.get("指标编号") or "").strip() or None
    metric_id = payload.values.get("指标id")
    try:
        metric_id = int(metric_id) if metric_id is not None else None
    except (TypeError, ValueError):
        return ActionResult(ok=False, message="指标id 必须是整数")
    results, messages = service.sample_metrics(keyword=keyword, metric_id=metric_id)
    if not results:
        return ActionResult(ok=False, message="；".join(messages))
    return ActionResult(ok=True, message="；".join(messages), entry={"采样结果数": len(results), "items": results})
