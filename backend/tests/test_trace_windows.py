"""采集时间区间与批次追踪台账测试。

覆盖需求：
1. 零宽、逆序、真实零条三类情况明确区分；
2. 空区间确认回执与真实页面分开消费，已确认无数据区间不重取，边界页只确认一次；
3. 重新打开批次追踪时，前轮空回执与已落库页面各自可回查；
4. 相邻时间窗口的迟到页能判定为前轮更正还是后轮首条有效页，并指出下次任务缺口；
5. 上一轮截止 10:00、下一轮从 10:00 开始，10:00 有效页重复送两次只落一次。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.intervals import (  # noqa: E402
    IntervalError,
    NORMAL,
    REVERSED,
    ZERO_WIDTH,
    classify_window,
    contains,
    is_lower_bound,
    split_time_windows,
)
from app.services.batch_tracker import BatchTrackerService  # noqa: E402
from app.services.crawl_schedule import CrawlScheduleService  # noqa: E402
from app.trace_ledger import (  # noqa: E402
    RECEIPT_CONFIRMED,
    RECEIPT_CORRECTED,
    TraceLedger,
)


@pytest.fixture()
def ledger():
    trace = TraceLedger()
    return trace


@pytest.fixture()
def batch(ledger, monkeypatch):
    import app.services.batch_tracker as bt
    monkeypatch.setattr(bt, "ledger", ledger)
    return BatchTrackerService()


@pytest.fixture()
def schedule():
    return CrawlScheduleService()


# ============================================================ 一、三类区间区分
class TestIntervalKinds:
    def test_zero_width_is_rejected_by_split(self):
        assert classify_window("10:00", "10:00") == ZERO_WIDTH
        with pytest.raises(IntervalError) as exc:
            split_time_windows("10:00", "10:00", 3)
        assert exc.value.kind == ZERO_WIDTH

    def test_reversed_is_rejected_by_split(self):
        assert classify_window("11:00", "09:00") == REVERSED
        with pytest.raises(IntervalError) as exc:
            split_time_windows("11:00", "09:00", 3)
        assert exc.value.kind == REVERSED

    def test_normal_window_is_executable(self):
        assert classify_window("09:00", "11:00") == NORMAL
        windows = split_time_windows("09:00", "11:00", 2)
        assert [w["start"] for w in windows] == ["09:00", "10:00"]
        assert [w["end"] for w in windows] == ["10:00", "11:00"]

    def test_adjacent_half_open_windows_share_boundary_without_gap(self):
        windows = split_time_windows("09:00", "12:00", 3)
        for left, right in zip(windows, windows[1:]):
            assert left["end"] == right["start"]
        # 边界标记只属于后一个窗口。
        assert not contains(windows[0], "10:00")
        assert contains(windows[1], "10:00")
        assert is_lower_bound(windows[1], "10:00")

    def test_schedule_service_reports_non_executable_without_windows(self, schedule):
        zero = schedule.domain_split_time_windows("10:00", "10:00", 2)
        assert zero["executable"] is False
        assert zero["reason"] == ZERO_WIDTH
        assert zero["windows"] == []

        reversed_ = schedule.domain_split_time_windows("12:00", "09:00", 2)
        assert reversed_["executable"] is False
        assert reversed_["reason"] == REVERSED
        assert reversed_["windows"] == []

    def test_schedule_service_opens_round_for_normal_interval(self, schedule):
        result = schedule.domain_split_time_windows(
            "09:00", "11:00", 2, round_id="R-0", source="src-a",
        )
        assert result["executable"] is True
        assert result["opened_round"]["reopened"] is False
        assert len(result["windows"]) == 2


# ============================================================ 二、零回执 vs 真实页
class TestEmptyReceiptsAndPages:
    def test_zero_width_cannot_get_empty_receipt(self, batch):
        # 无法执行的区间不得登记「确实为空」回执。
        with pytest.raises(IntervalError) as exc:
            batch.domain_confirm_empty("R-1", "src-a", "10:00", "10:00")
        assert exc.value.kind == ZERO_WIDTH
        assert batch.domain_reopen("R-1", "src-a")["empty_receipts"] == []

    def test_reversed_cannot_get_empty_receipt(self, batch):
        with pytest.raises(IntervalError) as exc:
            batch.domain_confirm_empty("R-1", "src-a", "11:00", "09:00")
        assert exc.value.kind == REVERSED

    def test_real_zero_rows_creates_confirmed_receipt(self, batch):
        batch.domain_open_round(
            "R-1", "src-a",
            [{"start": "09:00", "end": "10:00", "index": 0}],
        )
        receipt = batch.domain_confirm_empty("R-1", "src-a", "09:00", "10:00")
        assert receipt["status"] == RECEIPT_CONFIRMED
        assert receipt["observed_rows"] == 0
        assert receipt["duplicate"] is False

    def test_nonzero_observation_cannot_be_confirmed_empty(self, batch):
        with pytest.raises(ValueError):
            batch.domain_confirm_empty(
                "R-1", "src-a", "09:00", "10:00", observed_rows=3,
            )

    def test_empty_receipt_is_idempotent(self, batch):
        batch.domain_open_round(
            "R-1", "src-a", [{"start": "09:00", "end": "10:00", "index": 0}],
        )
        first = batch.domain_confirm_empty("R-1", "src-a", "09:00", "10:00")
        second = batch.domain_confirm_empty("R-1", "src-a", "09:00", "10:00")
        assert first["receipt_id"] == second["receipt_id"]
        assert second["duplicate"] is True
        assert len(batch.domain_reopen("R-1", "src-a")["empty_receipts"]) == 1

    def test_confirmed_empty_window_is_not_refetched(self, batch):
        batch.domain_open_round(
            "R-1", "src-a",
            [
                {"start": "09:00", "end": "10:00", "index": 0},
                {"start": "10:00", "end": "11:00", "index": 1},
            ],
        )
        batch.domain_confirm_empty("R-1", "src-a", "09:00", "10:00")
        task = batch.domain_next_task("R-1", "src-a")
        assert task["confirmed_empty"] == [{"start": "09:00", "end": "10:00"}]
        assert [w["start"] for w in task["pending_windows"]] == ["10:00"]
        assert task["next_from"] == "10:00"

    def test_real_page_cannot_be_denied_by_later_empty_claim(self, batch):
        batch.domain_open_round(
            "R-1", "src-a", [{"start": "09:00", "end": "10:00", "index": 0}],
        )
        batch.domain_ingest_page("R-1", "src-a", "09:30", "PG-1")
        with pytest.raises(ValueError):
            batch.domain_confirm_empty("R-1", "src-a", "09:00", "10:00")

    def test_boundary_page_confirmed_once_and_deduped(self, batch):
        batch.domain_open_round(
            "R-1", "src-a",
            [
                {"start": "09:00", "end": "10:00", "index": 0},
                {"start": "10:00", "end": "11:00", "index": 1},
            ],
        )
        first = batch.domain_ingest_page("R-1", "src-a", "10:00", "PG-10")
        assert first["owner_window"]["start"] == "10:00"
        assert first["first_valid_of_round"] is True
        assert first["duplicate"] is False

        # 同一标记重复送达两次：幂等返回同一条，不重复落库。
        again = batch.domain_ingest_page("R-1", "src-a", "10:00", "PG-10")
        third = batch.domain_ingest_page("R-1", "src-a", "10:00", "PG-10")
        assert again["duplicate"] is True
        assert third["duplicate"] is True
        pages = batch.domain_reopen("R-1", "src-a")["pages"]
        assert len(pages) == 1
        task = batch.domain_next_task("R-1", "src-a")
        # 边界页只覆盖归属的那一个窗口，另一个窗口仍待办。
        assert [w["start"] for w in task["covered_by_page"]] == ["10:00"]
        assert [w["start"] for w in task["pending_windows"]] == ["09:00"]


# ============================================================ 三、重新打开与回查
class TestReopenAndLookup:
    def test_reopen_keeps_receipts_and_pages_separately(self, batch):
        batch.domain_open_round(
            "R-1", "src-a",
            [
                {"start": "09:00", "end": "10:00", "index": 0},
                {"start": "10:00", "end": "11:00", "index": 1},
            ],
        )
        batch.domain_confirm_empty("R-1", "src-a", "09:00", "10:00")
        batch.domain_ingest_page("R-1", "src-a", "10:20", "PG-1")

        # 模拟另一个入口重新打开：换一个服务实例，底层台账仍在。
        another = BatchTrackerService()
        view = another.domain_reopen("R-1", "src-a")
        assert view["round"]["round_id"] == "R-1"
        assert len(view["empty_receipts"]) == 1
        assert view["empty_receipts"][0]["window"] == {"start": "09:00", "end": "10:00"}
        assert len(view["pages"]) == 1
        assert view["pages"][0]["page_id"] == "PG-1"
        # 空窗口不重取、页面窗口已覆盖，整个轮次没有剩余缺口。
        assert view["next_task"]["will_refetch"] is False
        assert view["next_task"]["next_from"] == "11:00"

        # 再开一次不复制账目。
        again = batch.domain_open_round(
            "R-1", "src-a",
            [
                {"start": "09:00", "end": "10:00", "index": 0},
                {"start": "10:00", "end": "11:00", "index": 1},
            ],
        )
        assert again["reopened"] is True
        assert len(batch.domain_reopen("R-1", "src-a")["empty_receipts"]) == 1


# ============================================================ 四、迟到页与相邻窗口
class TestLatePageAcrossRounds:
    def _two_adjacent_rounds(self, batch):
        # 上一轮截止 10:00，下一轮恰从 10:00 开始。
        batch.domain_open_round(
            "R-1", "src-a",
            [
                {"start": "08:00", "end": "09:00", "index": 0},
                {"start": "09:00", "end": "10:00", "index": 1},
            ],
        )
        batch.domain_open_round(
            "R-2", "src-a",
            [
                {"start": "10:00", "end": "11:00", "index": 0},
                {"start": "11:00", "end": "12:00", "index": 1},
            ],
        )

    def test_late_page_inside_confirmed_empty_window_is_front_correction(self, batch):
        self._two_adjacent_rounds(batch)
        batch.domain_confirm_empty("R-1", "src-a", "08:00", "09:00")
        batch.domain_confirm_empty("R-1", "src-a", "09:00", "10:00")

        # 一条标记 08:30 的原始页迟到，落在前轮已确认无数据的窗口内。
        late = batch.domain_ingest_page("R-2", "src-a", "08:30", "PG-LATE")
        assert late["front_correction"] is True
        assert late["first_valid_of_round"] is False
        assert late["corrected_receipts"] == ["ER-0001"]

        receipts = batch.domain_reopen("R-1", "src-a")["empty_receipts"]
        assert receipts[0]["status"] == RECEIPT_CORRECTED
        assert receipts[0]["corrected_by"] == "PG-LATE"
        assert receipts[1]["status"] == RECEIPT_CONFIRMED

        # 被更正的窗口重新进入待办，仍确认无数据的窗口不重取。
        task = batch.domain_next_task("R-1", "src-a")
        assert [w["start"] for w in task["pending_windows"]] == ["08:00"]
        assert [w["start"] for w in task["confirmed_empty"]] == ["09:00"]
        assert task["corrected_empty"][0]["receipt_id"] == "ER-0001"

    def test_boundary_page_at_10_is_first_valid_of_next_round_not_correction(self, batch):
        self._two_adjacent_rounds(batch)
        batch.domain_confirm_empty("R-1", "src-a", "08:00", "09:00")
        batch.domain_confirm_empty("R-1", "src-a", "09:00", "10:00")

        # 标记 10:00 的原始页：半开区间下属于 R-2 首窗，不属于 R-1 末窗。
        page = batch.domain_ingest_page("R-2", "src-a", "10:00", "PG-1000")
        assert page["front_correction"] is False
        assert page["first_valid_of_round"] is True
        assert page["owner_window"]["start"] == "10:00"
        assert page["owner_window"]["end"] == "11:00"
        assert page["corrected_receipts"] == []

        # 前轮两张空回执都不受影响。
        r1 = batch.domain_reopen("R-1", "src-a")
        assert all(r["status"] == RECEIPT_CONFIRMED for r in r1["empty_receipts"])
        assert r1["next_task"]["will_refetch"] is False

        # 后轮任务缺口：首窗已被有效页覆盖，仍缺 11:00~12:00。
        r2_task = batch.domain_next_task("R-2", "src-a")
        assert [w["start"] for w in r2_task["covered_by_page"]] == ["10:00"]
        assert [w["start"] for w in r2_task["pending_windows"]] == ["11:00"]
        assert r2_task["next_from"] == "11:00"

    def test_duplicate_10_o_clock_page_twice_stays_single_record(self, batch):
        self._two_adjacent_rounds(batch)
        first = batch.domain_ingest_page("R-2", "src-a", "10:00", "PG-1000")
        duplicate = batch.domain_ingest_page("R-2", "src-a", "10:00", "PG-1000")
        assert first["duplicate"] is False
        assert duplicate["duplicate"] is True
        assert duplicate["ledger_id"] == first["ledger_id"]
        pages = batch.domain_reopen("R-2", "src-a")["pages"]
        assert len(pages) == 1

    def test_late_correction_then_boundary_page_keeps_first_valid_flag(self, batch):
        # 题目主场景：先有迟到页更正前轮，10:00 边界页仍是后轮首条有效页。
        self._two_adjacent_rounds(batch)
        batch.domain_confirm_empty("R-1", "src-a", "08:00", "09:00")
        batch.domain_confirm_empty("R-1", "src-a", "09:00", "10:00")

        late = batch.domain_ingest_page("R-2", "src-a", "09:30", "PG-LATE")
        assert late["front_correction"] is True
        assert late["first_valid_of_round"] is False
        assert late["owner_window"] is None

        boundary = batch.domain_ingest_page("R-2", "src-a", "10:00", "PG-1000")
        assert boundary["front_correction"] is False
        assert boundary["first_valid_of_round"] is True

        # 迟到页不会被误标成首条有效页。
        pages = batch.domain_reopen("R-2", "src-a")["pages"]
        by_id = {p["page_id"]: p for p in pages}
        assert by_id["PG-LATE"]["first_valid_of_round"] is False
        assert by_id["PG-1000"]["first_valid_of_round"] is True

    def test_out_of_order_pages_still_mark_earliest_as_first_valid(self, batch):
        # 乱序送达：先到 10:30 后到 10:00，重算后 10:00 才是首条有效页。
        self._two_adjacent_rounds(batch)
        later = batch.domain_ingest_page("R-2", "src-a", "10:30", "PG-1030")
        assert later["first_valid_of_round"] is True
        earlier = batch.domain_ingest_page("R-2", "src-a", "10:00", "PG-1000")
        assert earlier["first_valid_of_round"] is True
        pages = batch.domain_reopen("R-2", "src-a")["pages"]
        by_id = {p["page_id"]: p for p in pages}
        assert by_id["PG-1000"]["first_valid_of_round"] is True
        assert by_id["PG-1030"]["first_valid_of_round"] is False

    def test_reclaim_reports_what_next_task_still_misses(self, batch):
        self._two_adjacent_rounds(batch)
        batch.domain_confirm_empty("R-1", "src-a", "08:00", "09:00")
        batch.domain_confirm_empty("R-1", "src-a", "09:00", "10:00")
        batch.domain_ingest_page("R-2", "src-a", "10:00", "PG-1000")
        batch.domain_ingest_page("R-2", "src-a", "11:30", "PG-1130")

        r2 = batch.domain_next_task("R-2", "src-a")
        assert r2["windows_total"] == 2
        assert r2["pending_windows"] == []
        assert r2["will_refetch"] is False
        # 全部窗口都有真实页，水位推进到轮次末端。
        assert r2["next_from"] == "12:00"


class TestOpenRoundRecordsInvalidWindows:
    def test_invalid_windows_are_segregated_in_round(self, ledger):
        result = ledger.open_round(
            "R-X", "src-a",
            [
                {"start": "09:00", "end": "10:00", "index": 0},
                {"start": "10:00", "end": "10:00", "index": 1},
                {"start": "11:00", "end": "10:00", "index": 2},
            ],
        )
        assert len(result["windows"]) == 1
        assert {w["kind"] for w in result["invalid_windows"]} == {ZERO_WIDTH, REVERSED}
        # 非法窗口不参与下次任务规划。
        task = ledger.next_task("R-X", "src-a")
        assert task["windows_total"] == 1
