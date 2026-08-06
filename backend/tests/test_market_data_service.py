from app.services.market_data_service import _parse_sina_realtime_quotes


def test_parse_sina_realtime_quotes() -> None:
    raw = (
        'var hq_str_sh600519="贵州茅台,100.00,99.00,101.00,102.00,98.00,101.00,101.01,100,1000,'
        '0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,2026-08-05,10:05:01,00";\n'
    )
    data = _parse_sina_realtime_quotes(raw, ["sh600519", "sz000001"])

    assert data["sh600519"]["price"] == 101.0
    assert data["sh600519"]["quote_date"] == "2026-08-05"
    assert data["sh600519"]["is_halted"] is False
    assert data["sz000001"]["price"] is None
    assert data["sz000001"]["is_halted"] is True
