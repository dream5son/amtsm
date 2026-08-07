from unittest.mock import patch

from fastapi.testclient import TestClient

from app.engine.state import runtime_state
from app.main import app

client = TestClient(app)


@patch("app.api.jobs._get_latest_job_log", return_value=None)
def test_get_status_idle(mock_log):
    runtime_state.job_status = "IDLE"
    response = client.get("/api/jobs/daily-baseline/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "未开始"


@patch(
    "app.api.jobs._get_latest_job_log",
    return_value={"id": 1, "status": "SUCCESS", "trade_date": "2024-01-01"},
)
def test_get_latest_log_empty(mock_log):
    with patch("app.api.jobs._get_log_items", return_value=[]):
        response = client.get("/api/jobs/daily-baseline/logs/latest")
        assert response.status_code == 200
        data = response.json()
        assert data["job_log"] is not None
        assert data["items"] == []
