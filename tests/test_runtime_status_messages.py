from vibeocr.classic.runtime_status_messages import (
    format_runtime_unavailable,
    supervisor_start_failure_message,
)


def test_not_installed_runtime_has_a_clear_profile_label() -> None:
    assert format_runtime_unavailable(["cpu: not-installed"]) == ("Runtime 未安装：CPU")
    assert format_runtime_unavailable(["nvidia_cuda: not-installed"]) == (
        "Runtime 未安装：NVIDIA CUDA"
    )


def test_unknown_runtime_error_remains_visible() -> None:
    assert format_runtime_unavailable(["inspect timed out"]) == (
        "Runtime 不可用：inspect timed out"
    )


def test_supervisor_failure_does_not_claim_dependency_damage() -> None:
    message = supervisor_start_failure_message()
    assert "就绪握手" in message
    assert "当前 Runtime profile 未完成安装或验证" in message
    assert "依赖损坏" not in message
