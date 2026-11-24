"""采集调度领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.intervals import (
    REVERSED,
    ZERO_WIDTH,
    classify_window,
    kind_text,
    split_time_windows,
)
from app.services.base import BaseModuleService
from app.trace_ledger import ledger


class CrawlScheduleService(BaseModuleService):
    MODULE = "crawl_schedule"
    ENTITY = "调度任务"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["任务标识", "关联配置", "调度周期", "起始水位", "截止水位", "分片策略", "上次执行", "下次执行"]
    REQUIRED_FIELDS = ["任务标识", "关联配置", "调度周期"]
    STATUS_ORDER = ["待启动", "运行中", "已完成", "调度失败"]
    ACTION_RULES = {"启动调度": "待启动", "取消调度": "调度失败"}
    NEGATIVE_ACTIONS = []

    def domain_create_schedule(self, config_name: str, period: str = "daily") -> dict:
        """生成采集调度计划。"""
        from app.store import store
        import time
        rows = store.rows(self.MODULE)
        task_id = f"SCHED-{max((int(r.get('id', 0)) for r in rows), default=0) + 1}"
        plan = {
            "task_id": task_id,
            "config_name": config_name,
            "period": period,
            "shard_count": 4,
            "start_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "watermark_start": "2026-01-01",
            "watermark_end": time.strftime("%Y-%m-%d"),
        }
        return plan

    def domain_split_time_windows(
        self,
        start: str,
        end: str,
        count: int,
        *,
        round_id: str | None = None,
        source: str | None = None,
    ) -> dict:
        """按时间切分采集窗口，把无法执行的区间与可执行区间区分开。

        返回 ``executable=False`` 时只报告零宽/逆序原因，不产出窗口、不登记批次；
        正常区间才切出半开窗口，并可选择直接在批次台账里开轮。
        """
        kind = classify_window(start, end)
        if kind in (ZERO_WIDTH, REVERSED):
            return {
                "executable": False,
                "reason": kind,
                "reason_text": kind_text(kind),
                "start": str(start),
                "end": str(end),
                "windows": [],
                "message": f"{kind_text(kind)}无法执行采集，不会生成空区间确认回执",
            }

        windows = split_time_windows(start, end, count)
        opened = None
        if round_id and source:
            opened = ledger.open_round(round_id, source, windows)
        return {
            "executable": True,
            "reason": "normal",
            "reason_text": kind_text("normal"),
            "start": str(start),
            "end": str(end),
            "windows": windows,
            "opened_round": opened,
        }
