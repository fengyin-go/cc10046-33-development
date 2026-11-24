"""采集区间与批次追踪 HTTP 接口测试。"""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.main import app  # noqa: E402
from app.trace_ledger import ledger  # noqa: E402


@pytest.fixture()
def client():
    ledger.reset()
    with TestClient(app) as test_client:
        yield test_client
    ledger.reset()


class TestTraceApi:
    def test_split_rejects_zero_width_and_reversed(self, client):
        zero = client.post("/api/trace/split-windows", json={
            "start": "10:00", "end": "10:00", "count": 2,
        })
        assert zero.status_code == 200
        body = zero.json()
        assert body["executable"] is False
        assert body["reason"] == "zero_width"
        assert body["windows"] == []

        reversed_ = client.post("/api/trace/split-windows", json={
            "start": "12:00", "end": "09:00", "count": 2,
        })
        assert reversed_.json()["reason"] == "reversed"

    def test_empty_receipt_on_invalid_interval_returns_422(self, client):
        resp = client.post("/api/trace/empty-receipts", json={
            "round_id": "R-1", "source": "src-a",
            "start": "10:00", "end": "10:00",
        })
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "invalid_interval"
        assert detail["kind"] == "zero_width"
        # 非法区间没有产生任何回执。
        assert ledger.list_receipts() == []

    def test_full_round_flow_and_reopen(self, client):
        # 开轮：上一轮 08:00~10:00 两个窗口。
        opened = client.post("/api/trace/rounds", json={
            "round_id": "R-1", "source": "src-a",
            "windows": [
                {"start": "08:00", "end": "09:00", "index": 0},
                {"start": "09:00", "end": "10:00", "index": 1},
            ],
        }).json()
        assert opened["reopened"] is False

        # 两个窗口真实零条，确认空回执（含一次重复确认）。
        r1 = client.post("/api/trace/empty-receipts", json={
            "round_id": "R-1", "source": "src-a",
            "start": "08:00", "end": "09:00",
        }).json()
        assert r1["status"] == "已确认无数据"
        dup = client.post("/api/trace/empty-receipts", json={
            "round_id": "R-1", "source": "src-a",
            "start": "08:00", "end": "09:00",
        }).json()
        assert dup["duplicate"] is True
        assert dup["receipt_id"] == r1["receipt_id"]

        client.post("/api/trace/empty-receipts", json={
            "round_id": "R-1", "source": "src-a",
            "start": "09:00", "end": "10:00",
        })

        # 下一轮从 10:00 开始。
        client.post("/api/trace/rounds", json={
            "round_id": "R-2", "source": "src-a",
            "windows": [
                {"start": "10:00", "end": "11:00", "index": 0},
                {"start": "11:00", "end": "12:00", "index": 1},
            ],
        })

        # 10:00 有效页送两次。
        page = client.post("/api/trace/pages", json={
            "round_id": "R-2", "source": "src-a",
            "marker": "10:00", "page_id": "PG-1000",
        }).json()
        assert page["first_valid_of_round"] is True
        assert page["front_correction"] is False
        again = client.post("/api/trace/pages", json={
            "round_id": "R-2", "source": "src-a",
            "marker": "10:00", "page_id": "PG-1000",
        }).json()
        assert again["duplicate"] is True

        # 另一入口重新打开 R-1：空回执仍可回查，且不重取。
        reopen = client.post("/api/trace/rounds/reopen", json={
            "round_id": "R-1", "source": "src-a",
        }).json()
        assert len(reopen["empty_receipts"]) == 2
        assert reopen["next_task"]["will_refetch"] is False

        # 迟到页落入前轮空窗 → 前轮更正。
        late = client.post("/api/trace/pages", json={
            "round_id": "R-2", "source": "src-a",
            "marker": "08:30", "page_id": "PG-LATE",
        }).json()
        assert late["front_correction"] is True
        r1_task = client.post("/api/trace/next-task", json={
            "round_id": "R-1", "source": "src-a",
        }).json()
        assert [w["start"] for w in r1_task["pending_windows"]] == ["08:00"]

        # 后轮缺口：缺 11:00~12:00。
        r2_task = client.post("/api/trace/next-task", json={
            "round_id": "R-2", "source": "src-a",
        }).json()
        assert [w["start"] for w in r2_task["pending_windows"]] == ["11:00"]
        assert r2_task["next_from"] == "11:00"

    def test_get_receipts_and_pages_endpoints(self, client):
        client.post("/api/trace/rounds", json={
            "round_id": "R-9", "source": "src-b",
            "windows": [{"start": "09:00", "end": "10:00", "index": 0}],
        })
        client.post("/api/trace/empty-receipts", json={
            "round_id": "R-9", "source": "src-b",
            "start": "09:00", "end": "10:00",
        })
        client.post("/api/trace/pages", json={
            "round_id": "R-9", "source": "src-b",
            "marker": "09:20", "page_id": "PG-9",
        })
        receipts = client.get(
            "/api/trace/rounds/R-9/receipts", params={"source": "src-b"},
        ).json()["items"]
        pages = client.get(
            "/api/trace/rounds/R-9/pages", params={"source": "src-b"},
        ).json()["items"]
        assert len(receipts) == 1
        # 真实页会把刚登记的空回执更正。
        assert receipts[0]["status"] == "已被真实页更正"
        assert len(pages) == 1
        assert pages[0]["page_id"] == "PG-9"
