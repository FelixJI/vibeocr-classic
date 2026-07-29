"""批量识别 worker 的协作式取消回归测试。

根因：旧实现 batch_cancel() 通过 WorkerManager.execute() 领取同一 busy worker，
最长等待 300 秒，直接冻结 GUI。修复后 batch_cancel() 直接写 SHM cancel flag
独立通道；底层独立通道由 service 层测试覆盖。
"""

from unittest.mock import MagicMock

from vibeocr.classic.views.batch_recognition_tab import BatchRecognitionWorker


class TestBatchCancel:
    """批量 worker 必须设置取消标志并通知 service。"""

    def test_cancel_sets_flag_and_delegates_to_service(self):
        """cancel 设置协作式标志，并把底层取消委托给 service。"""
        mock_service = MagicMock()
        worker = BatchRecognitionWorker.__new__(BatchRecognitionWorker)
        worker._cancelled = False
        worker._service = mock_service

        worker.cancel()

        assert worker._cancelled is True
        mock_service.batch_cancel.assert_called_once()

    def test_cancel_sets_cancelled_flag_even_if_service_raises(self):
        """service.batch_cancel 抛异常时 cancel flag 仍被设置"""
        mock_service = MagicMock()
        mock_service.batch_cancel.side_effect = RuntimeError("service error")
        worker = BatchRecognitionWorker.__new__(BatchRecognitionWorker)
        worker._cancelled = False
        worker._service = mock_service

        # 不应抛异常（cancel 内 suppress）
        worker.cancel()
        assert worker._cancelled is True
