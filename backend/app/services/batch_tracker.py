"""批次追踪领域服务：空区间确认回执与真实页面分开消费、分别可回查。"""
from __future__ import annotations

from app.services.base import BaseModuleService
from app.trace_ledger import ledger


class BatchTrackerService(BaseModuleService):
    MODULE = "batch_tracker"
    ENTITY = "采集批次"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["批次标识", "调度任务", "开始时间", "结束时间", "采集总数", "失败行数", "水位标记", "批次状态"]
    REQUIRED_FIELDS = ["批次标识", "调度任务", "开始时间"]
    STATUS_ORDER = ["执行中", "部分成功", "全部成功", "采集失败"]
    ACTION_RULES = {"重采失败行": "部分成功", "标记完成": "全部成功"}
    NEGATIVE_ACTIONS = []

    def domain_track_batch(self, batch_id: str, schedule_id: str) -> dict:
        """追踪采集批次执行状态。"""
        from app.store import store
        import time
        rows = store.rows(self.MODULE)
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        entry = {
            "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
            "批次标识": batch_id,
            "调度任务": schedule_id,
            "开始时间": now,
            "结束时间": None,
            "采集总数": "0",
            "失败行数": "0",
            "水位标记": now,
            "批次状态": "执行中",
            "status": "执行中",
            "pending": True,
            "abnormal": False,
        }
        rows.append(entry)
        return entry

    # ----------------------------------------------------------- 空回执 / 真实页
    def domain_open_round(
        self,
        round_id: str,
        source: str,
        windows: list[dict],
        *,
        note: str = "",
    ) -> dict:
        """开轮或重新打开一轮采集；重新打开时历史空回执与页面全部保留。"""
        return ledger.open_round(round_id, source, windows, note=note)

    def domain_confirm_empty(
        self,
        round_id: str,
        source: str,
        start: str,
        end: str,
        *,
        observed_rows: int = 0,
        note: str = "",
    ) -> dict:
        """登记空区间确认回执；零宽/逆序区间会抛出 IntervalError。"""
        return ledger.confirm_empty(
            round_id, source, start, end,
            observed_rows=observed_rows, note=note,
        )

    def domain_ingest_page(
        self,
        round_id: str,
        source: str,
        marker: str,
        page_id: str,
        *,
        payload: dict | None = None,
        note: str = "",
    ) -> dict:
        """消费一张真实页面，自动判定首条有效页/前轮更正并处理重复送达。"""
        return ledger.ingest_page(
            round_id, source, marker, page_id,
            payload=payload, note=note,
        )

    def domain_reopen(self, round_id: str, source: str) -> dict:
        """另一入口重新打开批次追踪，回查前轮空回执、已落库页面与下次任务。"""
        return ledger.reopen_round(round_id, source)

    def domain_next_task(self, round_id: str, source: str) -> dict:
        """重新领取任务：已确认无数据区间不重取，并指出剩余缺口。"""
        return ledger.next_task(round_id, source)
