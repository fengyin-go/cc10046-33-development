"""时间区间工具：区分「无法执行的非法区间」和「可执行但确实为空的区间」。

约定全部使用半开区间 ``[start, end)``：
- ``start == end`` 为零宽区间，没有任何可采集时刻，无法执行；
- ``start > end`` 为逆序区间，时间方向错误，无法执行；
- ``start < end`` 为正常区间，实际抓取零条时由批次追踪登记「空区间确认回执」。

边界归属因此唯一：标记时刻恰好等于窗口下界的页面属于本窗口，
不属于上一个以该时刻为上界的窗口，边界页不会被两个窗口重复计数。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

NORMAL = "normal"
ZERO_WIDTH = "zero_width"
REVERSED = "reversed"

_KIND_TEXT = {
    NORMAL: "正常区间",
    ZERO_WIDTH: "零宽区间",
    REVERSED: "逆序区间",
}


class IntervalError(ValueError):
    """非法时间区间：零宽或逆序，调用方不得据此登记空回执。"""

    def __init__(self, kind: str, start: Any, end: Any) -> None:
        self.kind = kind
        self.start = start
        self.end = end
        super().__init__(f"{_KIND_TEXT.get(kind, kind)}：start={start!r}, end={end!r}，无法执行采集")


def _parse(ts: Any) -> tuple[datetime, str]:
    """把时间标记解析成可比较的 datetime，支持完整日期时间和 HH:MM 两种写法。"""
    text = str(ts).strip().replace("T", " ")
    try:
        return datetime.fromisoformat(text), "datetime"
    except ValueError:
        return datetime.strptime(text, "%H:%M"), "time"


def ts_key(ts: Any) -> datetime:
    """供排序/比较使用的时间键。"""
    return _parse(ts)[0]


def kind_text(kind: str) -> str:
    return _KIND_TEXT.get(kind, kind)


def classify_window(start: Any, end: Any) -> str:
    """返回区间类别：normal / zero_width / reversed。"""
    start_dt, _ = _parse(start)
    end_dt, _ = _parse(end)
    if start_dt == end_dt:
        return ZERO_WIDTH
    if start_dt > end_dt:
        return REVERSED
    return NORMAL


def _format(dt: datetime, style: str) -> str:
    if style == "time":
        return dt.strftime("%H:%M")
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def split_time_windows(start: Any, end: Any, count: int) -> list[dict[str, str]]:
    """把 ``[start, end)`` 等切成 ``count`` 个相邻的半开窗口。

    零宽/逆序区间直接抛 :class:`IntervalError`，不产出任何窗口，
    以便调度侧把「无法执行」与「执行后确认为空」分开处理。
    """
    if not isinstance(count, int) or count <= 0:
        raise ValueError("切分数量必须是正整数")
    kind = classify_window(start, end)
    if kind != NORMAL:
        raise IntervalError(kind, start, end)

    start_dt, style = _parse(start)
    end_dt, end_style = _parse(end)
    if style != end_style:
        raise ValueError("起止时间写法必须一致（同为完整日期时间或同为 HH:MM）")

    total_seconds = (end_dt - start_dt).total_seconds()
    step = total_seconds / count
    windows: list[dict[str, str]] = []
    for index in range(count):
        window_start = start_dt + timedelta(seconds=step * index)
        window_end = start_dt + timedelta(seconds=step * (index + 1))
        windows.append({
            "start": _format(window_start, style),
            "end": _format(window_end, style),
            "index": index,
        })
    # 末窗严格收口到原始 end，避免浮点误差产生缝隙或越界。
    windows[-1]["end"] = _format(end_dt, style)
    return windows


def contains(window: dict[str, Any], marker: Any) -> bool:
    """半开归属判断：``window.start <= marker < window.end``。"""
    marker_dt = ts_key(marker)
    return ts_key(window["start"]) <= marker_dt < ts_key(window["end"])


def is_lower_bound(window: dict[str, Any], marker: Any) -> bool:
    """标记是否恰好落在窗口下界（边界页判定）。"""
    return ts_key(marker) == ts_key(window["start"])
