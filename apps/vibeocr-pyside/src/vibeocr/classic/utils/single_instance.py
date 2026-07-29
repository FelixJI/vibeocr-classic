"""单实例守卫（QLocalServer / QLocalSocket 实现）

确保同一时刻只有一个 VibeOCR 主进程在运行。第二个实例启动时通过本地 socket
通知已运行实例"把主窗口提到前台"，随后自身静默退出；避免重复进程各自拉起
OCR 子进程、WebEngine、nvidia-smi 探测等重资源。

实现要点：
- Windows 下 QLocalServer 由 Qt 用命名管道实现，无残留 socket 文件；
  ``QLocalServer.removeServer`` 仍调用作跨平台清理（Unix 下清理残留文件）。
- 必须在 ``QApplication`` 创建之后调用（QLocalServer 依赖 Qt 事件循环分发
  ``newConnection``）。
- socket 名固定为 ``VibeOCR``（不绑版本），保证升级后新旧版本互认同为同一应用。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

logger = logging.getLogger(__name__)

# 第二实例通知主实例的指令载荷：要求把主窗口提到前台。
# 预留为字节常量，便于将来扩展（如带文件路径打开）。
_CMD_RAISE = b"RAISE"

# 服务端确认字节：读完客户端指令后回写，客户端据此确认服务端已收到再退出，
# 避免客户端提前断开导致服务端读不到数据（不依赖时间猜测）。
_ACK = b"K"

# 连接/读写等待超时（毫秒）。第二实例退出路径不应长时间阻塞。
_TIMEOUT_MS = 1000


@dataclass
class _ServerConnectionState:
    """主实例侧单条连接的异步协议状态。"""

    buffer: bytearray = field(default_factory=bytearray)
    timer: QTimer | None = None
    response_started: bool = False


class SingleInstanceGuard(QObject):
    """QLocalServer/QLocalSocket 单实例守卫。

    用法::

        guard = SingleInstanceGuard("VibeOCR")
        if not guard.try_lock():
            # 已有实例在运行，本实例退出
            return 0
        # 本实例为主，连接 raise_requested 到窗口恢复逻辑
        guard.raise_requested.connect(window.bring_to_front)
    """

    # 收到第二实例的"提到前台"请求时发射（主线程）。
    raise_requested = Signal()

    def __init__(self, app_id: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._app_id = app_id
        self._server: QLocalServer | None = None
        # QLocalServer 不替应用持有 pending socket 的 Python wrapper；必须一直
        # 保活到 ACK 写完或连接断开，同时为每条连接保存分片缓冲和超时 timer。
        self._connections: dict[QLocalSocket, _ServerConnectionState] = {}

    def try_lock(self) -> bool:
        """尝试成为主实例。

        Returns:
            True  —— 本实例成功占位（成为主实例），应继续启动；
            False —— 已有实例在运行（本实例已通知其提到前台），应静默退出。
        """
        # 1) 先尝试连接已运行实例。能连上说明已有主实例。
        socket = QLocalSocket()
        socket.connectToServer(self._app_id)
        if socket.waitForConnected(_TIMEOUT_MS):
            # 已有实例：发送 RAISE 指令，等服务端回写 ACK 确认收到后再退出。
            # 用 ACK 闭环而非定时等待——避免客户端提前断开导致服务端读不到数据。
            socket.write(_CMD_RAISE)
            socket.flush()
            socket.waitForBytesWritten(_TIMEOUT_MS)
            # 阻塞等待服务端 ACK（最多 _TIMEOUT_MS）；超时也直接退出，
            # 不阻断第二实例退出（服务端可能在忙，但指令字节已入 OS 缓冲）。
            socket.waitForReadyRead(_TIMEOUT_MS)
            socket.disconnectFromServer()
            logger.debug("[SingleInstance] 检测到已运行实例，已通知其提到前台，本实例退出")
            return False

        # 2) 无运行实例：清理可能残留的 socket（上次崩溃未释放），再创建 server。
        #    Windows 用命名管道，removeServer 为空操作；Unix 清理 socket 文件。
        QLocalServer.removeServer(self._app_id)
        self._server = QLocalServer()
        if not self._server.listen(self._app_id):
            logger.warning(
                f"[SingleInstance] 创建本地服务失败: {self._server.errorString()}"
            )
            # 监听失败不阻断启动——退化为允许多实例（宁可重复启动也不启动不了）。
            self._server = None
            return True

        self._server.newConnection.connect(self._on_new_connection)
        logger.debug("[SingleInstance] 已成为主实例，监听本地服务")
        return True

    def _on_new_connection(self) -> None:
        """接纳所有 pending 连接；读取/回写完全由 Qt 信号驱动。"""
        if self._server is None:
            return
        while self._server.hasPendingConnections():
            conn = self._server.nextPendingConnection()
            if conn is None:
                break
            self._track_connection(conn)

    def _track_connection(self, conn: QLocalSocket) -> None:
        state = _ServerConnectionState()
        timer = QTimer(conn)
        timer.setSingleShot(True)
        timer.setInterval(_TIMEOUT_MS)
        timer.timeout.connect(lambda conn=conn: self._abort_connection(conn))
        state.timer = timer
        self._connections[conn] = state

        conn.readyRead.connect(lambda conn=conn: self._on_connection_ready_read(conn))
        conn.bytesWritten.connect(
            lambda _count, conn=conn: self._on_connection_bytes_written(conn)
        )
        conn.disconnected.connect(lambda conn=conn: self._cleanup_connection(conn))
        conn.errorOccurred.connect(
            lambda _error, conn=conn: self._abort_connection(conn)
        )
        timer.start()

        # newConnection 与 readyRead 可能在同一事件循环轮次合并；接纳时已有
        # 数据则主动消费一次，仍然不做任何阻塞等待。
        if conn.bytesAvailable() > 0:
            self._on_connection_ready_read(conn)

    def _on_connection_ready_read(self, conn: QLocalSocket) -> None:
        state = self._connections.get(conn)
        if state is None or state.response_started:
            return
        state.buffer.extend(bytes(conn.readAll()))  # type: ignore[arg-type]
        # 当前协议为固定 5 字节命令。少于完整帧时继续等待后续 readyRead；
        # 超时或客户端提前断开会走清理，不阻塞 GUI。
        if len(state.buffer) < len(_CMD_RAISE):
            return

        state.response_started = True
        if state.timer is not None:
            # 进入 ACK drain 是一个新的异步阶段，重新给完整超时预算；不能
            # 沿用读取阶段可能只剩几毫秒的 deadline，也不能停掉 timer 后让
            # bytesToWrite 永久非零的异常连接一直留在 _connections。
            state.timer.start(_TIMEOUT_MS)
        if bytes(state.buffer[: len(_CMD_RAISE)]) == _CMD_RAISE:
            self.raise_requested.emit()

        # 完整帧（含未知命令）均回 ACK，避免异常客户端一直等待；ACK 写入由
        # bytesWritten 推进到断开状态，不在回调中 waitForBytesWritten。
        if conn.write(_ACK) < 0:
            self._abort_connection(conn)
            return
        conn.flush()
        if conn.bytesToWrite() == 0:
            self._finish_connection(conn)

    def _on_connection_bytes_written(self, conn: QLocalSocket) -> None:
        state = self._connections.get(conn)
        if state is not None and state.response_started and conn.bytesToWrite() == 0:
            self._finish_connection(conn)

    def _finish_connection(self, conn: QLocalSocket) -> None:
        if conn not in self._connections:
            return
        conn.disconnectFromServer()
        if conn.state() == QLocalSocket.LocalSocketState.UnconnectedState:
            self._cleanup_connection(conn)

    def _abort_connection(self, conn: QLocalSocket) -> None:
        if conn not in self._connections:
            return
        conn.abort()
        self._cleanup_connection(conn)

    def _cleanup_connection(self, conn: QLocalSocket) -> None:
        state = self._connections.pop(conn, None)
        if state is not None and state.timer is not None:
            state.timer.stop()
        conn.deleteLater()

    def close(self) -> None:
        """停止监听并断开所有仍在途的第二实例连接。"""
        server = self._server
        self._server = None
        if server is not None:
            server.close()
            server.deleteLater()
        for conn in tuple(self._connections):
            conn.abort()
            self._cleanup_connection(conn)
        QLocalServer.removeServer(self._app_id)
