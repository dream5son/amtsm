from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.engine.activity_log import emit, recent, reset_activity_log
from app.engine.scheduler import _on_job_error
from app.main import app

client = TestClient(app)


def setup_function() -> None:
    reset_activity_log()


def test_get_activity_snapshot() -> None:
    emit("info", "poll ok", job="market_polling")
    emit("error", "snapshot failed", job="daily_snapshot")
    response = client.get("/api/jobs/activity")
    assert response.status_code == 200
    data = response.json()
    polling_messages = [item["message"] for item in data["jobs"]["market_polling"]]
    snapshot_levels = [item["level"] for item in data["jobs"]["daily_snapshot"]]
    assert "poll ok" in polling_messages
    assert "error" in snapshot_levels
    assert all(
        item["message"] != "poll ok" for item in data["jobs"]["baseline_precompute"]
    )


def test_get_activity_by_job() -> None:
    emit("info", "one", job="daily_snapshot")
    emit("info", "two", job="daily_snapshot")
    emit("info", "other", job="market_polling")
    response = client.get(
        "/api/jobs/activity", params={"job": "daily_snapshot", "limit": 20}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["job"] == "daily_snapshot"
    messages = [item["message"] for item in data["items"]]
    assert "one" in messages
    assert "two" in messages
    assert "other" not in messages


def test_get_activity_unknown_job() -> None:
    response = client.get("/api/jobs/activity", params={"job": "not_a_job"})
    assert response.status_code == 400


def test_activity_snapshot_includes_scheduler_views() -> None:
    response = client.get("/api/jobs/activity")
    assert response.status_code == 200
    scheduler = response.json()["scheduler"]
    ids = [item["id"] for item in scheduler]
    assert ids == [
        "market_polling",
        "baseline_precompute",
        "daily_snapshot",
        "backtest_worker",
        "startup_market_data_catchup",
        "snapshot_bootstrap",
    ]
    polling = next(item for item in scheduler if item["id"] == "market_polling")
    assert polling["kind"] == "interval"
    assert polling["note"] == "含盘中快照"
    assert all(item["id"] != "intraday_snapshot" for item in scheduler)


def test_job_error_listener_emits_error() -> None:
    event = SimpleNamespace(
        job_id="market_polling",
        exception=RuntimeError("boom"),
    )
    _on_job_error(event)
    items = recent("market_polling")
    assert any(item["level"] == "error" and "boom" in item["message"] for item in items)


def test_bootstrap_job_error_goes_to_bootstrap_bucket() -> None:
    event = SimpleNamespace(
        job_id="snapshot_bootstrap_sh600519",
        exception=RuntimeError("timeout"),
    )
    _on_job_error(event)
    items = recent("snapshot_bootstrap")
    assert items[-1]["job"] == "snapshot_bootstrap"
    assert recent("market_polling") == []
