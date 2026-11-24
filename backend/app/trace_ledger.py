"""批次追踪台账：分别消费「空区间确认回执」与「真实页面」。

两类凭据分开存放、各自可回查：
- 空回执 ``empty_receipts``：实际抓取到零条后才允许登记；零宽/逆序区间拒绝登记；
- 真实页面 ``pages``：实际抓到并落库的页面，按 (轮次, 来源, 时间标记) 幂等去重。

同一边界页只会被确认一次：半开区间 ``[start, end)`` 让边界标记只归属一个窗口，
重复送达同一标记时返回既有记录而不是再落一条。

迟到页会自动更正历史空回执：真实页面落入此前确认无数据的区间时，
对应回执转为 ``已被真实页更正``，后续任务不再把该区间当作已确认无数据而跳过重取。
"""
from __future__ import annotations

from typing import Any

from app.intervals import (
    IntervalError,
    ZERO_WIDTH,
    REVERSED,
    classify_window,
    contains,
    kind_text,
    ts_key,
)

# 空回执状态
RECEIPT_CONFIRMED = "已确认无数据"
RECEIPT_CORRECTED = "已被真实页更正"


class TraceLedger:
    def __init__(self) -> None:
        self._rounds: list[dict[str, Any]] = []
        self._receipts: list[dict[str, Any]] = []
        self._pages: list[dict[str, Any]] = []
        self._receipt_seq = 0
        self._page_seq = 0

    def reset(self) -> None:
        self._rounds.clear()
        self._receipts.clear()
        self._pages.clear()
        self._receipt_seq = 0
        self._page_seq = 0

    # ------------------------------------------------------------------ 轮次
    def open_round(
        self,
        round_id: str,
        source: str,
        windows: list[dict[str, Any]],
        *,
        note: str = "",
    ) -> dict[str, Any]:
        """登记/重新打开一轮采集的时间窗口；已存在时直接返回，历史账目保留。"""
        existing = self.get_round(round_id, source)
        if existing is not None:
            return {**existing, "reopened": True}

        validated: list[dict[str, Any]] = []
        invalid: list[dict[str, Any]] = []
        for window in windows:
            kind = classify_window(window["start"], window["end"])
            item = {
                "index": window.get("index", len(validated) + len(invalid)),
                "start": str(window["start"]),
                "end": str(window["end"]),
                "kind": kind,
                "kind_text": kind_text(kind),
            }
            if kind == "normal":
                validated.append(item)
            else:
                invalid.append(item)

        round_row = {
            "round_id": round_id,
            "source": source,
            "note": note,
            "windows": validated,
            "invalid_windows": invalid,
        }
        self._rounds.append(round_row)
        return {**round_row, "reopened": False}

    def get_round(self, round_id: str, source: str) -> dict[str, Any] | None:
        for row in self._rounds:
            if row["round_id"] == round_id and row["source"] == source:
                return row
        return None

    def list_rounds(self, source: str | None = None) -> list[dict[str, Any]]:
        if source is None:
            return list(self._rounds)
        return [row for row in self._rounds if row["source"] == source]

    # ------------------------------------------------------------- 空回执登记
    def confirm_empty(
        self,
        round_id: str,
        source: str,
        start: Any,
        end: Any,
        *,
        observed_rows: int = 0,
        note: str = "",
    ) -> dict[str, Any]:
        """确认一个时间区间实际抓取为零条，返回空区间确认回执。

        零宽/逆序区间无法执行采集，直接抛 :class:`IntervalError`，不产生回执；
        同轮同源同区间重复确认只返回既有回执（幂等，边界/区间只确认一次）；
        区间内已经落过真实页面时不允许再确认无数据。
        """
        kind = classify_window(start, end)
        if kind in (ZERO_WIDTH, REVERSED):
            raise IntervalError(kind, start, end)
        if observed_rows != 0:
            raise ValueError(
                f"实际观测到 {observed_rows} 条记录，不能登记空区间确认回执"
            )

        window = {"start": str(start), "end": str(end)}

        # 幂等：同一轮同一来源的同一区间只确认一次。
        for receipt in self._receipts:
            if (receipt["round_id"] == round_id and receipt["source"] == source
                    and receipt["window"] == window):
                return {**receipt, "duplicate": True}

        # 已有真实页落在该区间，不能再声称无数据。
        for page in self._pages:
            if (page["round_id"] == round_id and page["source"] == source
                    and contains(window, page["marker"])):
                raise ValueError(
                    f"区间 {window['start']}~{window['end']} 已存在真实页面 "
                    f"{page['page_id']}（标记 {page['marker']}），不能确认空区间"
                )

        self._receipt_seq += 1
        receipt = {
            "receipt_id": f"ER-{self._receipt_seq:04d}",
            "round_id": round_id,
            "source": source,
            "window": window,
            "observed_rows": 0,
            "status": RECEIPT_CONFIRMED,
            "note": note,
            "duplicate": False,
        }
        self._receipts.append(receipt)
        return {**receipt}

    # ------------------------------------------------------------- 真实页消费
    def ingest_page(
        self,
        round_id: str,
        source: str,
        marker: Any,
        page_id: str,
        *,
        payload: dict[str, Any] | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """消费一张真实页面并落账，返回带角色判定的页面记录。

        - 同一标记重复送达：幂等返回既有记录，``duplicate=True``，不重复落库；
        - 标记落在历史空回执区间：把对应回执更正为「已被真实页更正」，
          该页面标记为前轮更正（``front_correction=True``）；
        - 标记落在本轮窗口下界：它是本轮首条有效页候选（``first_valid_of_round``）。
        """
        marker_text = str(marker)
        for page in self._pages:
            if (page["round_id"] == round_id and page["source"] == source
                    and page["marker"] == marker_text):
                return {**page, "duplicate": True}

        round_row = self.get_round(round_id, source)
        owner_window = None
        if round_row is not None:
            for window in round_row["windows"]:
                if contains(window, marker_text):
                    owner_window = window
                    break

        # 迟到页更正：任何历史轮次（含本轮先确认后补页）的空回执被推翻。
        corrected_receipts: list[str] = []
        front_correction = False
        for receipt in self._receipts:
            if (receipt["source"] != source
                    or receipt["status"] != RECEIPT_CONFIRMED):
                continue
            if contains(receipt["window"], marker_text):
                receipt["status"] = RECEIPT_CORRECTED
                receipt["corrected_by"] = page_id
                receipt["corrected_in_round"] = round_id
                corrected_receipts.append(receipt["receipt_id"])
                if receipt["round_id"] != round_id:
                    front_correction = True

        self._page_seq += 1
        page_row = {
            "page_id": page_id,
            "ledger_id": f"PG-{self._page_seq:04d}",
            "round_id": round_id,
            "source": source,
            "marker": marker_text,
            "owner_window": owner_window,
            "first_valid_of_round": False,
            "front_correction": front_correction,
            "corrected_receipts": corrected_receipts,
            "payload": payload or {},
            "note": note,
            "duplicate": False,
        }
        self._pages.append(page_row)

        # 重算本轮首条有效页：只统计落在本轮窗口内的页面，
        # 迟到/前轮更正页（owner_window 为空）不参与，乱序送达也保持一致。
        owned = [
            p for p in self._pages
            if p["round_id"] == round_id and p["source"] == source
            and p.get("owner_window")
        ]
        if owned:
            earliest = min(ts_key(p["marker"]) for p in owned)
            for p in owned:
                p["first_valid_of_round"] = ts_key(p["marker"]) == earliest

        return {**page_row, "first_valid_of_round": page_row["first_valid_of_round"]}

    # ------------------------------------------------------------------ 回查
    def list_receipts(
        self,
        round_id: str | None = None,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        result = self._receipts
        if round_id is not None:
            result = [r for r in result if r["round_id"] == round_id]
        if source is not None:
            result = [r for r in result if r["source"] == source]
        return [dict(r, duplicate=False) for r in result]

    def list_pages(
        self,
        round_id: str | None = None,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        result = self._pages
        if round_id is not None:
            result = [p for p in result if p["round_id"] == round_id]
        if source is not None:
            result = [p for p in result if p["source"] == source]
        return [dict(p, duplicate=False) for p in result]

    def reopen_round(self, round_id: str, source: str) -> dict[str, Any]:
        """在另一个入口重新打开批次追踪：前轮空回执与已落库真实页各自回查。"""
        round_row = self.get_round(round_id, source)
        receipts = self.list_receipts(round_id, source)
        pages = self.list_pages(round_id, source)
        return {
            "round": round_row,
            "empty_receipts": receipts,
            "pages": pages,
            "next_task": self.next_task(round_id, source),
        }

    # ------------------------------------------------------------- 下次任务规划
    def next_task(self, round_id: str, source: str) -> dict[str, Any]:
        """计算下一轮/重新领取时还缺什么。

        已确认无数据的窗口不会重取；已被真实页更正的窗口需要重取；
        已落真实页的窗口不重取（边界页按半开归属只计一次）。
        """
        round_row = self.get_round(round_id, source)
        if round_row is None:
            return {
                "round_id": round_id,
                "source": source,
                "windows_total": 0,
                "confirmed_empty": [],
                "covered_by_page": [],
                "corrected_empty": [],
                "pending_windows": [],
            }

        receipts = self.list_receipts(round_id, source)
        pages = self.list_pages(round_id, source)

        confirmed_empty = [
            r["window"] for r in receipts if r["status"] == RECEIPT_CONFIRMED
        ]
        corrected_empty = [
            {**r["window"], "receipt_id": r["receipt_id"],
             "corrected_by": r.get("corrected_by")}
            for r in receipts if r["status"] == RECEIPT_CORRECTED
        ]
        covered_by_page = [
            w for w in round_row["windows"]
            if any(contains(w, p["marker"]) for p in pages)
        ]

        def window_key(window: dict[str, Any]) -> tuple[str, str]:
            return window["start"], window["end"]

        confirmed_keys = {(w["start"], w["end"]) for w in confirmed_empty}
        covered_keys = {window_key(w) for w in covered_by_page}

        pending: list[dict[str, Any]] = []
        for window in round_row["windows"]:
            key = window_key(window)
            if key in confirmed_keys or key in covered_keys:
                continue
            pending.append(window)

        # 继续抓取的起点：优先最早待办窗口下界，否则整体水位推进到窗口末端。
        if pending:
            next_from = min(pending, key=lambda w: ts_key(w["start"]))["start"]
        else:
            next_from = max(
                (w["end"] for w in round_row["windows"]),
                default=None,
            )

        return {
            "round_id": round_id,
            "source": source,
            "windows_total": len(round_row["windows"]),
            "confirmed_empty": confirmed_empty,
            "covered_by_page": covered_by_page,
            "corrected_empty": corrected_empty,
            "pending_windows": pending,
            "next_from": next_from,
            "will_refetch": len(pending) > 0,
        }


ledger = TraceLedger()
