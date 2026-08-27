"""Harden baostock's persistent TCP client.

Upstream ``send_msg`` loops on ``recv()`` until it sees ``<![CDATA[]]>\\n``.
When the server closes the socket (idle timeout after hours, typically around
the 15:30 daily snapshot), ``recv()`` returns ``b""`` immediately and that
loop spins at 100% CPU. The GIL stays busy, stock search 504s, and Docker's
json log can tear with ``\\x00``.
"""

from __future__ import annotations

import logging
import socket
from typing import Any

logger = logging.getLogger(__name__)

DELIMITER = b"<![CDATA[]]>\n"
RECV_SIZE = 8192
RECV_TIMEOUT_SECONDS = 30.0
MAX_RESPONSE_BYTES = 32 * 1024 * 1024

_patched = False


def recv_delimited(
    sock: socket.socket,
    *,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> bytes:
    """Read until baostock's frame delimiter. Empty recv means the peer closed."""
    receive = b""
    while True:
        chunk = sock.recv(RECV_SIZE)
        if not chunk:
            raise ConnectionError("baostock socket closed before delimiter")
        receive += chunk
        if len(receive) > max_bytes:
            raise ConnectionError(
                f"baostock response exceeded {max_bytes} bytes without delimiter"
            )
        if receive.endswith(DELIMITER):
            return receive


def drop_baostock_socket() -> None:
    """Close the process-wide baostock socket if one exists."""
    try:
        import baostock.common.context as context
    except ImportError:
        return
    sock = getattr(context, "default_socket", None)
    if sock is None:
        return
    try:
        sock.close()
    except Exception:  # noqa: BLE001 - best-effort teardown
        logger.debug("baostock socket close failed", exc_info=True)
    setattr(context, "default_socket", None)


def _patched_connect(self: Any) -> None:
    import baostock.common.contants as cons
    import baostock.common.context as context

    drop_baostock_socket()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(RECV_TIMEOUT_SECONDS)
    try:
        sock.connect((cons.BAOSTOCK_SERVER_IP, cons.BAOSTOCK_SERVER_PORT))
    except OSError:
        sock.close()
        raise
    setattr(context, "default_socket", sock)


def _patched_send_msg(msg: str) -> str | None:
    import zlib

    import baostock.common.contants as cons
    import baostock.common.context as context

    if not hasattr(context, "default_socket"):
        logger.warning("baostock send_msg skipped: not logged in")
        return None
    sock = getattr(context, "default_socket")
    if sock is None:
        return None
    try:
        sock.sendall(bytes(msg + "\n", encoding="utf-8"))
        receive = recv_delimited(sock)
        head_bytes = receive[0 : cons.MESSAGE_HEADER_LENGTH]
        head_str = bytes.decode(head_bytes)
        head_arr = head_str.split(cons.MESSAGE_SPLIT)
        if head_arr[1] in cons.COMPRESSED_MESSAGE_TYPE_TUPLE:
            head_inner_length = int(head_arr[2])
            body_start = cons.MESSAGE_HEADER_LENGTH
            body = receive[body_start : body_start + head_inner_length]
            body_str = bytes.decode(zlib.decompress(body))
            return head_str + body_str
        return bytes.decode(receive)
    except Exception:
        logger.warning("baostock recv failed; dropping socket", exc_info=True)
        drop_baostock_socket()
        raise


def install_baostock_socket_patch() -> None:
    """Replace baostock's connect/send_msg. Idempotent."""
    global _patched
    if _patched:
        return
    import baostock.util.socketutil as socketutil

    socketutil.SocketUtil.connect = _patched_connect
    socketutil.send_msg = _patched_send_msg
    _patched = True
