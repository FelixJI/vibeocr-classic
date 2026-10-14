"""EngineEnvDialog 构造回归测试。

``tests/views/test_engine_env_flow.py`` 用假对话框替换 ``EngineEnvDialog``，
真实构造路径此前未被任何测试覆盖：``QTextEdit`` 没有
``setMaximumBlockCount``（那是 ``QTextDocument``/``QPlainTextEdit`` 的 API），
曾在启动维护弹窗时直接 AttributeError 崩溃。
"""

from vibeocr.classic.engine_envs import EngineEnvManager
from vibeocr.classic.widgets.engine_env_dialog import EngineEnvDialog


def test_engine_env_dialog_constructs(qapp, tmp_path):
    manager = EngineEnvManager(tmp_path / "envs")
    dialog = EngineEnvDialog(manager, install_ids=["paddle-cpu"], remove_ids=[])
    assert dialog._log_view.document().maximumBlockCount() == 2000
    dialog.deleteLater()
