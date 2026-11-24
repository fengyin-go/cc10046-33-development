"""采集时间区间与批次追踪路由。

把「无法执行的区间」（零宽/逆序，HTTP 422）与「可执行但为空」（正常登记空回执）
在接口层明确区分；空回执与真实页面分别有独立端点，台账持久在内存仓库中，
换一个入口重新打开轮次时两类账目都能各自回查。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.intervals import IntervalError
from app.services.batch_tracker import BatchTrackerService
from app.services.crawl_schedule import CrawlScheduleService
from app.trace_ledger import ledger

router = APIRouter(prefix="/api/trace", tags=["采集区间与批次追踪"])

_schedule = CrawlScheduleService()
_batch = BatchTrackerService()


class SplitPayload(BaseModel):
    start: str
    end: str
    count: int = Field(default=1, ge=1)
    round_id: str | None = None
    source: str | None = None


class RoundPayload(BaseModel):
    round_id: str
    source: str
    windows: list[dict[str, Any]] = Field(default_factory=list)
    note: str = ""


class EmptyPayload(BaseModel):
    round_id: str
    source: str
    start: str
    end: str
    observed_rows: int = 0
    note: str = ""


class PagePayload(BaseModel):
    round_id: str
    source: str
    marker: str
    page_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    note: str = ""


class RoundRef(BaseModel):
    round_id: str
    source: str


@router.post("/split-windows")
def split_windows(payload: SplitPayload) -> dict[str, Any]:
    result = _schedule.domain_split_time_windows(
        payload.start, payload.end, payload.count,
        round_id=payload.round_id, source=payload.source,
    )
    return result


@router.post("/rounds")
def open_round(payload: RoundPayload) -> dict[str, Any]:
    return _batch.domain_open_round(
        payload.round_id, payload.source, payload.windows, note=payload.note,
    )


@router.post("/empty-receipts")
def confirm_empty(payload: EmptyPayload) -> dict[str, Any]:
    try:
        return _batch.domain_confirm_empty(
            payload.round_id, payload.source, payload.start, payload.end,
            observed_rows=payload.observed_rows, note=payload.note,
        )
    except IntervalError as exc:
        # 无法执行 ≠ 确实为空：非法区间不产生回执。
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_interval",
                "kind": exc.kind,
                "start": str(exc.start),
                "end": str(exc.end),
                "message": str(exc),
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"error": "conflict", "message": str(exc)})


@router.post("/pages")
def ingest_page(payload: PagePayload) -> dict[str, Any]:
    try:
        return _batch.domain_ingest_page(
            payload.round_id, payload.source, payload.marker, payload.page_id,
            payload=payload.payload, note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"error": "conflict", "message": str(exc)})


@router.post("/rounds/reopen")
def reopen_round(payload: RoundRef) -> dict[str, Any]:
    return _batch.domain_reopen(payload.round_id, payload.source)


@router.post("/next-task")
def next_task(payload: RoundRef) -> dict[str, Any]:
    return _batch.domain_next_task(payload.round_id, payload.source)


@router.get("/rounds")
def list_rounds(source: str | None = None) -> dict[str, Any]:
    return {"items": ledger.list_rounds(source)}


@router.get("/rounds/{round_id}/receipts")
def list_receipts(round_id: str, source: str) -> dict[str, Any]:
    return {"items": ledger.list_receipts(round_id, source)}


@router.get("/rounds/{round_id}/pages")
def list_pages(round_id: str, source: str) -> dict[str, Any]:
    return {"items": ledger.list_pages(round_id, source)}
