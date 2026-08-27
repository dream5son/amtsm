import pytest

from app.services.market_data.baostock_socket import (
    DELIMITER,
    recv_delimited,
)


class _FakeSock:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = list(chunks)
        self.recv_calls = 0

    def recv(self, _size: int) -> bytes:
        self.recv_calls += 1
        if not self.chunks:
            return b""
        return self.chunks.pop(0)


def test_recv_delimited_joins_chunks_until_delimiter() -> None:
    payload = b"header" + DELIMITER
    sock = _FakeSock([payload[:3], payload[3:]])
    assert recv_delimited(sock) == payload
    assert sock.recv_calls == 2


def test_recv_delimited_empty_recv_raises_instead_of_spinning() -> None:
    sock = _FakeSock([b"partial-without-delimiter", b""])
    with pytest.raises(ConnectionError, match="closed before delimiter"):
        recv_delimited(sock)
    assert sock.recv_calls == 2


def test_recv_delimited_immediate_eof_raises() -> None:
    sock = _FakeSock([])
    with pytest.raises(ConnectionError, match="closed before delimiter"):
        recv_delimited(sock)
    assert sock.recv_calls == 1


def test_recv_delimited_rejects_unbounded_payload() -> None:
    sock = _FakeSock([b"x" * 64])
    with pytest.raises(ConnectionError, match="exceeded"):
        recv_delimited(sock, max_bytes=32)
    assert sock.recv_calls == 1
