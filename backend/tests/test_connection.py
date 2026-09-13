from sqlalchemy import text

from app.config import settings
from app.db.connection import check_sqlite_integrity, get_engine
from app.db.init_db import init_db


def test_sqlite_busy_timeout_wal_and_integrity_ok(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "amtsm.db"))
    init_db()

    with get_engine().connect() as conn:
        timeout = conn.execute(text("PRAGMA busy_timeout")).scalar()
        journal = conn.execute(text("PRAGMA journal_mode")).scalar()

    assert timeout == 5000
    assert str(journal).lower() == "wal"
    assert check_sqlite_integrity() == "ok"
