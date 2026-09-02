from vibeocr.classic.runtime_installation import RuntimeComponentDescriptor
from vibeocr.classic.runtime_status_messages import (
    accelerator_display,
    accelerator_framework,
    cuda_requirement_label,
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


def test_gpu_profile_display_carries_cuda_version() -> None:
    components = (
        RuntimeComponentDescriptor(
            "paddleocr-cuda", "PaddleOCR（CUDA）", desired_state="ready"
        ),
    )
    assert accelerator_display("nvidia_cuda", "win-x64-cu126", components) == (
        "GPU（NVIDIA CUDA 12.6）"
    )
    assert accelerator_framework("nvidia_cuda", components) == "gpu"
    assert cuda_requirement_label("win-x64-cu126") == "NVIDIA CUDA 12.6"


def test_cpu_profile_requires_advanced_framework_components() -> None:
    installed = (
        RuntimeComponentDescriptor(
            "paddleocr-cpu", "PaddleOCR（CPU）", desired_state="ready"
        ),
        RuntimeComponentDescriptor(
            "mineru-cpu", "MinerU（CPU）", desired_state="ready"
        ),
    )
    assert accelerator_display("cpu", "win-x64-cpu", installed) == "CPU"
    assert accelerator_framework("cpu", installed) == "cpu"


def test_base_only_runtime_is_distinguished_from_cpu_profile() -> None:
    """Host 对基础 Runtime 也回报 accelerator=cpu；组件证据区分"未选择框架"。

    回归：旧实现把一切非 nvidia_cuda 值折叠成 "CPU"，导致仅装了基础 Runtime 的
    机器显示"加速方案：CPU"，与事实不符。
    """
    base_only = (
        RuntimeComponentDescriptor("rapidocr-base", "快速 OCR", desired_state="ready"),
        RuntimeComponentDescriptor(
            "paddleocr-cpu", "PaddleOCR（CPU）", desired_state="not_required"
        ),
        RuntimeComponentDescriptor(
            "mineru-cpu", "MinerU（CPU）", desired_state="not_required"
        ),
    )
    assert accelerator_framework("cpu", base_only) is None
    assert accelerator_display("cpu", "win-x64-base", base_only) == (
        "基础 Runtime（未选择高级 OCR 框架）"
    )


def test_profile_id_is_the_authoritative_framework_evidence() -> None:
    """profile 与 accelerator 一一对应（backend runtime_manifest 是事实来源）。

    Host 对 win-x64-base 也回报 accelerator=cpu；按 profile 判定后，
    即使组件证据缺失（None）也不会把基础 Runtime 伪装成"已选择 CPU"。
    """
    assert accelerator_framework("cpu", None, "win-x64-base") is None
    assert accelerator_framework("cpu", None, "win-x64-cpu") == "cpu"
    assert accelerator_framework("cpu", None, "win-x64-cu126") == "gpu"
    assert accelerator_display("cpu", "win-x64-base", None) == (
        "基础 Runtime（未选择高级 OCR 框架）"
    )
    # profile 优先级高于 accelerator 字段与组件证据。
    assert accelerator_framework("nvidia_cuda", (), "win-x64-base") is None


def test_unknown_profile_falls_back_to_component_evidence() -> None:
    # 未知 profile id：回退到 accelerator + 组件 desired_state 的启发式。
    assert accelerator_framework("cpu", None, "win-x64-tpu") == "cpu"


def test_missing_component_info_falls_back_to_binary_labels() -> None:
    # 旧 payload 无组件信息：保守回退为 CPU，保持既有显示行为。
    assert accelerator_framework("cpu", None) == "cpu"
    assert accelerator_display("cpu", "win-x64-cpu", None) == "CPU"
    assert accelerator_display("cpu", "win-x64-cpu", ()) == "CPU"
    # 未知值不再伪装成 CPU。
    assert accelerator_display("unknown_x", None, None) == "未知（unknown_x）"
    # 非 GPU profile 无 CUDA 需求。
    assert cuda_requirement_label("win-x64-cpu") == ""
    assert cuda_requirement_label(None) == ""
