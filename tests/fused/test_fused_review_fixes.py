"""评审修复项的测试：进程内 PDF 承载、MinerU 探测、生产入口门禁。"""

from __future__ import annotations

import os



def test_mineru_availability_uses_injected_interpreter(tmp_path, monkeypatch) -> None:
    """MinerU 可用性优先看注入的独立解释器，而不是主进程 import。"""
    from vibeocr.backend.supervisor.composition import _mineru_available

    fake_python = tmp_path / "Scripts" / "python.exe"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_bytes(b"")

    monkeypatch.setenv("VIBEOCR_MINERU_PYTHON", str(fake_python))
    assert _mineru_available() is True

    monkeypatch.setenv("VIBEOCR_MINERU_PYTHON", str(tmp_path / "missing.exe"))
    # 回退主进程 import；当前环境未安装 mineru，应为 False。
    assert _mineru_available() is False


def test_production_dependencies_fused_uses_import_gate(monkeypatch) -> None:
    """融合形态：生产入口验证内置后端可导入，不再触碰 Runtime Installer。"""
    from vibeocr.classic import main as main_module

    monkeypatch.delenv("VIBEOCR_SELF_TEST_SMOKE", raising=False)
    monkeypatch.delenv("VIBEOCR_SUPERVISOR_SUBPROCESS", raising=False)

    class _Boom:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("融合形态不应构造 RuntimeInstallerClient")

    monkeypatch.setattr(
        "vibeocr.classic.main.RuntimeInstallerClient", _Boom
    )
    assert main_module.check_production_dependencies() is True


def test_production_dependencies_fused_fails_on_broken_import(monkeypatch) -> None:
    from vibeocr.classic import main as main_module

    monkeypatch.delenv("VIBEOCR_SELF_TEST_SMOKE", raising=False)
    monkeypatch.delenv("VIBEOCR_SUPERVISOR_SUBPROCESS", raising=False)

    import builtins

    real_import = builtins.__import__

    def _broken(name, *args, **kwargs):
        if name == "vibeocr.backend.supervisor.app":
            raise ImportError("boom")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _broken)
    assert main_module.check_production_dependencies() is False


def test_pdf_backend_inprocess_thread_serves_health(monkeypatch, tmp_path) -> None:
    """VIBEOCR_PDF_INPROCESS=1 时 PDF 后端以线程承载并提供健康检查。"""
    from vibeocr.backend.services.pdf_backend_client import PdfBackendClient

    monkeypatch.setenv("VIBEOCR_PDF_INPROCESS", "1")
    client = PdfBackendClient()
    try:
        client.start()
        assert client._base_url.startswith("http://127.0.0.1:")

        import httpx

        response = httpx.get(f"{client._base_url}/health", timeout=10.0)
        assert response.status_code == 200
    finally:
        client.stop()
    assert client._inprocess_server is None
    assert client._inprocess_thread is None


def test_backend_host_sets_pdf_inprocess_when_frozen(monkeypatch, tmp_path) -> None:
    from vibeocr.classic.backend_host import InProcessBackendHost

    monkeypatch.delenv("VIBEOCR_PDF_INPROCESS", raising=False)
    monkeypatch.setattr("vibeocr.classic.backend_host.sys.frozen", True, raising=False)
    host = InProcessBackendHost(tmp_path)
    host._apply_environment(tmp_path / "stager")

    assert os.environ.get("VIBEOCR_PDF_INPROCESS") == "1"
