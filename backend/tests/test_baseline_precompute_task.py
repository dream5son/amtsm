from unittest.mock import MagicMock, patch

from app.engine.tasks import baseline_precompute_task


@patch("app.engine.tasks.fetch_daily_bars")
@patch("app.engine.tasks.get_db")
def test_baseline_precompute_task_success(mock_get_db, mock_fetch):
    """Test successful execution with mocked data."""
    # Setup mock DB
    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)

    mock_row = {"stock_code": "600519", "stock_name": "贵州茅台", "effective_n": 60}
    mock_conn.execute.return_value.fetchall.return_value = [MagicMock(**{"__getitem__": lambda self, k: mock_row[k], "keys": lambda self: mock_row.keys()})]
    mock_get_db.return_value = mock_conn

    # Mock fetch_daily_bars
    bars = [{"date": f"2024-01-{i+1:02d}", "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0, "volume": 1000} for i in range(60)]
    mock_fetch.return_value = bars

    with patch("app.engine.tasks.create_job_log", return_value=1), \
         patch("app.engine.tasks.finish_job_log") as mock_finish, \
         patch("app.engine.tasks.insert_log_item"), \
         patch("app.engine.tasks.upsert_baseline"):
        baseline_precompute_task()
        mock_finish.assert_called_once()
        args = mock_finish.call_args[0]
        assert args[1] == "SUCCESS"


@patch("app.engine.tasks.fetch_daily_bars")
@patch("app.engine.tasks.get_db")
def test_baseline_precompute_task_individual_failure(mock_get_db, mock_fetch):
    """Test that individual stock failure doesn't stop others."""
    from app.services.market_data_service import StockDataFetchError

    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)

    rows = [
        {"stock_code": "600519", "stock_name": "贵州茅台", "effective_n": 60},
        {"stock_code": "000001", "stock_name": "平安银行", "effective_n": 60},
    ]

    class FakeRow:
        def __init__(self, data):
            self._data = data
        def __getitem__(self, k):
            return self._data[k]
        def keys(self):
            return self._data.keys()

    mock_conn.execute.return_value.fetchall.return_value = [FakeRow(r) for r in rows]
    mock_get_db.return_value = mock_conn

    call_count = [0]

    def side_effect(code, n):
        call_count[0] += 1
        if call_count[0] == 1:
            raise StockDataFetchError("timeout")
        return [{"date": "2024-01-01", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1000}] * 60

    mock_fetch.side_effect = side_effect

    with patch("app.engine.tasks.create_job_log", return_value=1), \
         patch("app.engine.tasks.finish_job_log") as mock_finish, \
         patch("app.engine.tasks.insert_log_item"), \
         patch("app.engine.tasks.upsert_baseline"):
        baseline_precompute_task()
        args = mock_finish.call_args[0]
        assert args[1] == "FAILED"
        assert args[3] == 1  # success_count
        assert args[4] == 1  # failed_count
