"""反压采样数据结果业务规则：把反压指标、告警规则、并行度建议串成一条可核对的数据链路。

链路口径：
- 读取 BACK 反压指标的阻塞算子、缓冲区水位、消费延迟，结合 ALAR 告警规则校验严重级别；
- 校验通过后写入反压采样数据结果，结果里保留建议并行度、告警待办和采样版本；
- 同一算子的并发采样只保留最新版本（写入侧维护采样版本标识，过期采样直接拒收）；
- 严重反压只生成一条告警待办；恢复后采样结果与告警状态在同一次写入里一起更新；
- 告警待办校验只依据最终确认的反压采样数据结果，已恢复算子不允许继续显示严重待办。
"""
from __future__ import annotations

import re
import threading
from datetime import date, datetime
from typing import Any

from app.store import store

RESULT_MODULE = "bpsample"
TODO_MODULE = "bptodo"
BACKPRESS_MODULE = "backpress"
ALARM_MODULE = "alarm"

RESULT_STATUSES = ["正常", "轻微反压", "严重反压", "已恢复"]
SEVERITY_TO_STATUS = {"正常": "正常", "轻微": "轻微反压", "严重": "严重反压", "已恢复": "已恢复"}

# 建议并行度 = 水位因子 + 延迟因子；82% 水位记 3、12000ms 延迟记 3，合计 6。
BUFFER_TIERS = ((80.0, 3), (60.0, 2), (40.0, 1))
DELAY_TIERS = ((10000.0, 3), (5000.0, 2), (1000.0, 1))

_CONDITION_RE = re.compile(
    r"(缓冲区水位|水位|buffer|消费延迟|延迟|delay)\s*(>=|<=|==|>|<|=)\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"(\d+(?:\.\d+)?)")
_ALERT_NO_RE = re.compile(r"alert-(\d+)")

# 同一算子的并发采样串行化，保证只保留最新版本。
_write_lock = threading.Lock()


def parse_buffer_pct(value: Any) -> float | None:
    """从「82%」「82」这类写法里解析缓冲区水位百分比。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _NUMBER_RE.search(str(value))
    return float(match.group(1)) if match else None


def parse_delay_ms(value: Any) -> float | None:
    """从「12000ms」「12s」这类写法里解析消费延迟，统一折算成毫秒。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower()
    match = _NUMBER_RE.search(text)
    if not match:
        return None
    number = float(match.group(1))
    if "ms" in text or "毫秒" in text:
        return number
    if text.endswith("s") or "秒" in text:
        return number * 1000.0
    return number


def suggest_parallelism(buffer_pct: float | None, delay_ms: float | None) -> int:
    """按水位因子 + 延迟因子给建议并行度，最低为 1。"""
    buffer_factor = 0
    if buffer_pct is not None:
        for threshold, factor in BUFFER_TIERS:
            if buffer_pct >= threshold:
                buffer_factor = factor
                break
    delay_factor = 0
    if delay_ms is not None:
        for threshold, factor in DELAY_TIERS:
            if delay_ms >= threshold:
                delay_factor = factor
                break
    return max(1, buffer_factor + delay_factor)


def _parse_sample_time(value: Any) -> datetime | None:
    """采样时间容错解析：支持日期或日期时间字符串，解析不了就返回空。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _condition_matches(condition: Any, buffer_pct: float | None, delay_ms: float | None) -> bool:
    """把告警条件里的「水位>80」「延迟>=10000」这类表达式逐项核对，全部命中才算匹配。"""
    text = str(condition or "")
    matches = _CONDITION_RE.findall(text)
    if not matches:
        return False
    for field, op, raw_number in matches:
        field_key = field.lower()
        number = float(raw_number)
        if field_key in ("缓冲区水位", "水位", "buffer"):
            actual = buffer_pct
        else:
            actual = delay_ms
        if actual is None:
            return False
        if op in (">",) and not actual > number:
            return False
        if op == ">=" and not actual >= number:
            return False
        if op == "<" and not actual < number:
            return False
        if op == "<=" and not actual <= number:
            return False
        if op in ("=", "==") and not actual == number:
            return False
    return True


class BpsampleService:
    """反压采样数据结果的写入与核对都收在这里，路由层不做业务判断。"""

    # ---------- 读取侧 ----------

    def list_results(
        self,
        *,
        keyword: str | None = None,
        severity: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(RESULT_MODULE)
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("指标编号", "")) or keyword in str(row.get("阻塞算子", ""))
            ]
        if severity:
            rows = [row for row in rows if row.get("严重级别") == severity]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_result(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(RESULT_MODULE, entry_id)

    def list_todos(self) -> list[dict[str, Any]]:
        """列出告警待办，并逐条对照最终确认的反压采样数据结果校验状态。

        最新采样已恢复而较早的严重反压告警仍待处理时，以采样结果为准：
        已恢复算子的待办直接核销，不允许继续显示严重待办。
        """
        todos = store.rows(TODO_MODULE)
        for todo in todos:
            result = self._find_result_by_operator(str(todo.get("阻塞算子", "")))
            if result is None:
                continue
            todo["采样版本"] = result.get("采样版本", todo.get("采样版本"))
            if result.get("严重级别") in ("正常", "已恢复") and todo.get("status") == "待处理":
                todo["status"] = "已恢复"
                todo["pending"] = False
                todo["abnormal"] = False
        return todos

    def overview(self) -> dict[str, Any]:
        """概览信息：采样结果总量、严重算子、待处理告警与已恢复算子一屏核对。"""
        results = store.rows(RESULT_MODULE)
        todos = self.list_todos()
        cards = [
            {"label": "采样结果总数", "value": len(results)},
            {"label": "严重反压算子", "value": sum(1 for row in results if row.get("严重级别") == "严重")},
            {"label": "待处理告警待办", "value": sum(1 for row in todos if row.get("status") == "待处理")},
            {"label": "已恢复算子", "value": sum(1 for row in results if row.get("严重级别") == "已恢复")},
        ]
        return {
            "cards": cards,
            "最高采样版本": max((int(row.get("采样版本", 0)) for row in results), default=0),
        }

    # ---------- 写入侧 ----------

    def sample_metrics(
        self,
        *,
        keyword: str | None = None,
        metric_id: int | None = None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """对反压指标执行采样：不指定条件时采样全部指标。

        返回 (写入的结果列表, 逐条说明)，过期采样不会覆盖更新版本。
        """
        metrics = store.rows(BACKPRESS_MODULE)
        if metric_id is not None:
            metrics = [row for row in metrics if int(row.get("id", 0)) == metric_id]
        elif keyword:
            metrics = [row for row in metrics if keyword in str(row.get("指标编号", ""))]
        if not metrics:
            return [], ["没有匹配的反压指标可采样"]
        results: list[dict[str, Any]] = []
        messages: list[str] = []
        for metric in metrics:
            result, message = self.sample_metric(metric)
            if result is not None:
                results.append(result)
            messages.append(message)
        return results, messages

    def sample_metric(self, metric: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """对单条反压指标采样并写入结果；同一算子只保留最新版本。"""
        operator = str(metric.get("阻塞算子") or "").strip()
        if not operator:
            return None, f"反压指标 {metric.get('指标编号', '?')} 缺少阻塞算子，无法采样"
        metric_no = str(metric.get("指标编号", ""))
        with _write_lock:
            existing = self._find_result_by_operator(operator)
            if existing is not None and self._is_stale(metric, existing):
                return existing, (
                    f"算子 {operator} 已存在更新版本 v{existing.get('采样版本')}，过期采样已忽略"
                )
            severity = self._validate_severity(metric)
            parallelism = suggest_parallelism(
                parse_buffer_pct(metric.get("缓冲区水位")),
                parse_delay_ms(metric.get("消费延迟")),
            )
            version = int(existing.get("采样版本", 0)) + 1 if existing else 1
            record = existing if existing is not None else self._new_result_record()
            todo_no = self._sync_todo(operator, metric_no, severity, record, version)
            record.update({
                "指标编号": metric_no,
                "流作业名": metric.get("流作业名"),
                "阻塞算子": operator,
                "缓冲区水位": metric.get("缓冲区水位"),
                "消费延迟": metric.get("消费延迟"),
                "采样时间": metric.get("采样时间"),
                "严重级别": severity,
                "建议并行度": parallelism,
                "告警待办": todo_no,
                "采样版本": version,
                "status": SEVERITY_TO_STATUS[severity],
                "pending": severity in ("轻微", "严重"),
                "abnormal": severity == "严重",
            })
            return record, (
                f"算子 {operator} 采样完成：严重级别{severity}，建议并行度 {parallelism}，"
                f"采样版本 v{version}" + (f"，告警待办 {todo_no}" if todo_no else "")
            )

    # ---------- 内部 helpers ----------

    def _new_result_record(self) -> dict[str, Any]:
        rows = store.rows(RESULT_MODULE)
        record = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        record["结果编号"] = f"BPS-{record['id']:04d}"
        rows.append(record)
        return record

    def _find_result_by_operator(self, operator: str) -> dict[str, Any] | None:
        if not operator:
            return None
        for row in store.rows(RESULT_MODULE):
            if str(row.get("阻塞算子", "")) == operator:
                return row
        return None

    def _is_stale(self, metric: dict[str, Any], existing: dict[str, Any]) -> bool:
        """采样时间早于已确认结果的采样视为过期，不允许覆盖最新版本。"""
        incoming = _parse_sample_time(metric.get("采样时间"))
        current = _parse_sample_time(existing.get("采样时间"))
        return incoming is not None and current is not None and incoming < current

    def _validate_severity(self, metric: dict[str, Any]) -> str:
        """校验严重级别：先看恢复，再核对已启用的反压告警规则，最后回落到指标状态。"""
        status = str(metric.get("status") or "")
        if status == "已恢复":
            return "已恢复"
        buffer_pct = parse_buffer_pct(metric.get("缓冲区水位"))
        delay_ms = parse_delay_ms(metric.get("消费延迟"))
        matched_levels: list[str] = []
        for rule in store.rows(ALARM_MODULE):
            if str(rule.get("status")) != "已启用":
                continue
            if "反压" not in str(rule.get("监控指标", "")):
                continue
            if _condition_matches(rule.get("告警条件"), buffer_pct, delay_ms):
                matched_levels.append(str(rule.get("告警级别", "")))
        if any("严重" in level for level in matched_levels):
            return "严重"
        if status == "严重反压":
            return "严重"
        if matched_levels:
            return "轻微"
        if status == "轻微反压":
            return "轻微"
        return "正常"

    def _sync_todo(
        self,
        operator: str,
        metric_no: str,
        severity: str,
        record: dict[str, Any],
        version: int,
    ) -> str:
        """维护告警待办：严重只生成一条，恢复后与采样结果一起更新。"""
        todos = store.rows(TODO_MODULE)
        open_todo = next(
            (row for row in todos if row.get("阻塞算子") == operator and row.get("status") == "待处理"),
            None,
        )
        any_todo = open_todo or next(
            (row for row in reversed(todos) if row.get("阻塞算子") == operator),
            None,
        )
        if severity == "严重":
            if open_todo is not None:
                return str(open_todo.get("待办编号", ""))
            todo = {
                "id": max((int(row.get("id", 0)) for row in todos), default=0) + 1,
                "待办编号": self._next_todo_no(todos),
                "指标编号": metric_no,
                "阻塞算子": operator,
                "严重级别": "严重",
                "结果编号": record.get("结果编号", ""),
                "采样版本": version,
                "status": "待处理",
                "pending": True,
                "abnormal": True,
            }
            todos.append(todo)
            return str(todo["待办编号"])
        if open_todo is not None:
            open_todo["status"] = "已恢复"
            open_todo["pending"] = False
            open_todo["abnormal"] = False
            open_todo["采样版本"] = version
        return str(any_todo.get("待办编号", "")) if any_todo else ""

    def _next_todo_no(self, todos: list[dict[str, Any]]) -> str:
        """待办编号全局递增（alert-1、alert-2……），写入侧统一维护。"""
        highest = 0
        for row in todos:
            match = _ALERT_NO_RE.fullmatch(str(row.get("待办编号", "")))
            if match:
                highest = max(highest, int(match.group(1)))
        return f"alert-{highest + 1}"


bpsample_service = BpsampleService()
