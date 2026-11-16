"""反压监控面板业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.bpsample import bpsample_service
from app.store import store

MODULE = "backpress"
REQUIRED_FIELDS = ["指标编号", "流作业名", "反压级别"]
STATUS_ORDER = ["正常", "轻微反压", "严重反压", "已恢复"]
ACTION_RULES = {"标记反压": "轻微反压", "扩容处理": "严重反压", "标记恢复": "已恢复"}
NEGATIVE_ACTIONS = []


class BackpressService:
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
        # 阻塞算子、缓冲区水位、消费延迟、采样时间是采样核对的依据，登记时必须一并保留。
        entry.update({key: value for key, value in values.items() if key != "action"})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"反压指标 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于反压监控面板可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        # 数据联动：状态流转后立即重采样，恢复时采样结果与告警待办在同一次请求里一起更新。
        _, sample_message = bpsample_service.sample_metric(entry)
        return entry, f"反压指标已{action}；{sample_message}"
