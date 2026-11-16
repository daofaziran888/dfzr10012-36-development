"""业务模块路由汇总。

这里统一按别名导入再暴露 ROUTERS：模块名有可能和内置名撞车（某个业务模块就叫 dict、list
这种名字时），按名字直接 import 会把内置类型覆盖掉，函数注解在运行时求值就会报
'module' object is not subscriptable。
"""
from __future__ import annotations

from app.routers import streamin as router_streamin
from app.routers import operator as router_operator
from app.routers import window as router_window
from app.routers import state as router_state
from app.routers import checkpoint as router_checkpoint
from app.routers import backpress as router_backpress
from app.routers import bpsample as router_bpsample
from app.routers import latency as router_latency
from app.routers import jobops as router_jobops
from app.routers import sink as router_sink
from app.routers import schema as router_schema
from app.routers import watermark as router_watermark
from app.routers import parallel as router_parallel
from app.routers import metric as router_metric
from app.routers import alarm as router_alarm
from app.routers import udf as router_udf
from app.routers import log as router_log

ROUTERS = [router_streamin, router_operator, router_window, router_state, router_checkpoint, router_backpress, router_bpsample, router_latency, router_jobops, router_sink, router_schema, router_watermark, router_parallel, router_metric, router_alarm, router_udf, router_log]
