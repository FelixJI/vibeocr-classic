"""主实例 QLocalServer 的非阻塞连接状态机回归测试。"""

from __future__ import annotations

from uuid import uuid4

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalSocket

from tests.qt_responsiveness import assert_qt_event_loop_responsive
from vibeocr.classic.utils.single_instance import _ACK, _CMD_RAISE, SingleInstanceGuard


@pytest.fixture
def guard(qapp):
    instance = SingleInstanceGuard(f"VibeOCR-test-{uuid4().hex}")
    assert instance.try_lock()
    yield instance
    instance.close()


def _connect(qtbot, guard: SingleInstanceGuard) -> QLocalSocket:
    socket = QLocalSocket()
    socket.connectToServer(guard._app_id)
    qtbot.waitUntil(
        lambda: socket.state() == QLocalSocket.LocalSocketState.ConnectedState
    )
    qtbot.waitUntil(lambda: bool(guard._connections))
    return socket


def _collect_reply(socket: QLocalSocket) -> bytearray:
    reply = bytearray()
    socket.readyRead.connect(lambda: reply.extend(bytes(socket.readAll())))
    return reply


def test_idle_or_empty_client_does_not_block_gui_and_close_disconnects(
    guard, qtbot
):
    socket = _connect(qtbot, guard)

    assert_qt_event_loop_responsive(
        qtbot,
        in_flight=lambda: bool(guard._connections),
    )

    guard.close()
    qtbot.waitUntil(
        lambda: socket.state() == QLocalSocket.LocalSocketState.UnconnectedState
    )
    assert not guard._connections


def test_fragmented_command_is_buffered_then_acknowledged_once(guard, qtbot):
    socket = _connect(qtbot, guard)
    reply = _collect_reply(socket)
    raised: list[bool] = []
    guard.raise_requested.connect(lambda: raised.append(True))

    socket.write(_CMD_RAISE[:2])
    socket.flush()
    qtbot.wait(20)
    assert not raised

    socket.write(_CMD_RAISE[2:])
    socket.flush()
    qtbot.waitUntil(lambda: bool(raised))
    qtbot.waitUntil(lambda: bytes(reply) == _ACK)
    qtbot.waitUntil(lambda: socket not in guard._connections)

    assert raised == [True]


def test_partial_and_invalid_clients_are_isolated_from_next_request(guard, qtbot):
    raised: list[bool] = []
    guard.raise_requested.connect(lambda: raised.append(True))

    partial = _connect(qtbot, guard)
    partial.write(b"RA")
    partial.flush()
    partial.abort()
    qtbot.waitUntil(lambda: partial not in guard._connections)

    invalid = _connect(qtbot, guard)
    invalid_reply = _collect_reply(invalid)
    invalid.write(b"NOPE!")
    invalid.flush()
    qtbot.waitUntil(lambda: bytes(invalid_reply) == _ACK)
    qtbot.waitUntil(lambda: invalid not in guard._connections)
    assert not raised

    valid = _connect(qtbot, guard)
    valid_reply = _collect_reply(valid)
    valid.write(_CMD_RAISE)
    valid.flush()
    qtbot.waitUntil(lambda: raised == [True])
    qtbot.waitUntil(lambda: bytes(valid_reply) == _ACK)


def test_concurrent_connections_each_receive_ack_without_gui_stall(guard, qtbot):
    raised: list[bool] = []
    guard.raise_requested.connect(lambda: raised.append(True))
    sockets = [_connect(qtbot, guard) for _ in range(4)]
    replies = [_collect_reply(socket) for socket in sockets]

    for index, socket in enumerate(sockets):
        split = 1 + index
        socket.write(_CMD_RAISE[:split])
        socket.flush()
    assert_qt_event_loop_responsive(
        qtbot,
        in_flight=lambda: len(guard._connections) == len(sockets),
    )
    for index, socket in enumerate(sockets):
        split = 1 + index
        socket.write(_CMD_RAISE[split:])
        socket.flush()

    qtbot.waitUntil(lambda: len(raised) == len(sockets))
    qtbot.waitUntil(lambda: all(bytes(reply) == _ACK for reply in replies))
    qtbot.waitUntil(lambda: not guard._connections)


class _StuckAckSocket(QObject):
    readyRead = Signal()
    bytesWritten = Signal(int)
    disconnected = Signal()
    errorOccurred = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.aborted = False
        self.written = bytearray()

    def bytesAvailable(self) -> int:
        return 0

    def readAll(self) -> bytes:
        return _CMD_RAISE

    def write(self, data: bytes) -> int:
        self.written.extend(data)
        return len(data)

    def flush(self) -> bool:
        return True

    def bytesToWrite(self) -> int:
        return 1

    def abort(self) -> None:
        self.aborted = True
        self.disconnected.emit()


def test_stuck_ack_drain_times_out_without_blocking_gui(
    guard, qtbot, monkeypatch
):
    monkeypatch.setattr("vibeocr.classic.utils.single_instance._TIMEOUT_MS", 80)
    socket = _StuckAckSocket()
    guard._track_connection(socket)  # type: ignore[arg-type]

    socket.readyRead.emit()

    assert bytes(socket.written) == _ACK
    assert socket in guard._connections
    assert guard._connections[socket].timer.isActive()
    assert_qt_event_loop_responsive(
        qtbot,
        in_flight=lambda: socket in guard._connections and not socket.aborted,
    )
    qtbot.waitUntil(lambda: socket.aborted, timeout=500)
    assert socket not in guard._connections
