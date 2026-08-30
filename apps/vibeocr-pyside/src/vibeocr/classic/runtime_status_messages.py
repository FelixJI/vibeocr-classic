"""User-facing Runtime status messages without Qt or installer dependencies."""

from __future__ import annotations

from collections.abc import Sequence

_ACCELERATOR_LABELS = {
    "cpu": "CPU",
    "nvidia_cuda": "NVIDIA CUDA",
}

# Runtime manifest 的 profile id 携带 CUDA 变体；显示层据此给出确切版本，
# 而不是把所有非 CPU 值折叠成同一个 "NVIDIA CUDA"。
_PROFILE_CUDA_VERSIONS = {
    "win-x64-cu126": "12.6",
}

# 完整 CPU/GPU profile 才包含的高级 OCR 框架组件；base profile 不要求它们。
# 组件 desired_state 全为 not_required 即「仅基础 Runtime，用户未选择框架」。
_ADVANCED_FRAMEWORK_PREFIXES = ("paddleocr-", "mineru-")

#: 未随 profile 携带 CUDA 变体信息时的兜底显示。
_GENERIC_GPU_LABEL = "NVIDIA CUDA（版本未知）"

_BASE_RUNTIME_LABEL = "基础 Runtime（未选择高级 OCR 框架）"


def _advanced_framework_desired_states(components: object) -> list[str] | None:
    """Collect desired states of advanced-framework components; ``None`` if unknown."""

    if components is None:
        return None
    try:
        items = list(components)
    except TypeError:
        return None
    states: list[str] = []
    for component in items:
        component_id = getattr(component, "component_id", None)
        if not isinstance(component_id, str) or not component_id.startswith(
            _ADVANCED_FRAMEWORK_PREFIXES
        ):
            continue
        desired = getattr(component, "desired_state", None)
        states.append(desired if isinstance(desired, str) else "")
    return states or None


def accelerator_framework(accelerator: object, components: object = None) -> str | None:
    """Classify the installed profile: ``"gpu"`` / ``"cpu"`` / ``None`` (base only).

    Runtime Host 对仅安装基础 Runtime 的机器同样回报 ``accelerator="cpu"``，
    但用户从未选择 CPU 框架；组件 desired_state 是区分两者的唯一证据。
    组件信息缺失时保守回退为 ``"cpu"``（保持既有显示行为）。
    """

    if accelerator == "nvidia_cuda":
        return "gpu"
    if accelerator != "cpu":
        return None
    states = _advanced_framework_desired_states(components)
    if states is None:
        return "cpu"
    return None if all(state == "not_required" for state in states) else "cpu"


def accelerator_display(
    accelerator: object, profile: object = None, components: object = None
) -> str:
    """User-facing acceleration label distinguishing GPU/CPU/base-only profiles."""

    if accelerator == "nvidia_cuda":
        version = cuda_requirement_version(profile)
        return (
            f"GPU（NVIDIA CUDA {version}）"
            if version
            else f"GPU（{_GENERIC_GPU_LABEL}）"
        )
    if accelerator == "cpu":
        if accelerator_framework("cpu", components) is None:
            return _BASE_RUNTIME_LABEL
        return "CPU"
    value = accelerator if isinstance(accelerator, str) else str(accelerator)
    return f"未知（{value}）"


def cuda_requirement_version(profile: object) -> str:
    """Bare CUDA version (e.g. "12.6") implied by the profile id; empty otherwise."""

    if isinstance(profile, str):
        return _PROFILE_CUDA_VERSIONS.get(profile, "")
    return ""


def cuda_requirement_label(profile: object) -> str:
    """CUDA requirement implied by the bound profile id; empty for CPU/base."""

    version = cuda_requirement_version(profile)
    return f"NVIDIA CUDA {version}" if version else ""


def format_runtime_unavailable(reasons: Sequence[str]) -> str:
    not_installed: list[str] = []
    for reason in reasons:
        accelerator, separator, integrity = reason.partition(":")
        if not separator or integrity.strip() != "not-installed":
            break
        label = _ACCELERATOR_LABELS.get(accelerator.strip(), accelerator.strip())
        not_installed.append(label)
    else:
        if not_installed:
            return f"Runtime 未安装：{'、'.join(not_installed)}"

    detail = "、".join(reasons)
    return f"Runtime 不可用：{detail}" if detail else "Runtime 不可用"


def supervisor_start_failure_message() -> str:
    return (
        "OCR Supervisor 子进程未能完成启动和就绪握手。\n\n"
        "可能原因：\n"
        "1. 当前 Runtime profile 未完成安装或验证\n"
        "2. 子进程被安全软件拦截或异常退出\n"
        "3. 本地通信初始化失败\n\n"
        "模型尚未开始按需加载，因此通常不是模型下载问题。\n\n"
        "请查看控制台日志了解详情。"
    )


__all__ = [
    "accelerator_display",
    "accelerator_framework",
    "cuda_requirement_label",
    "cuda_requirement_version",
    "format_runtime_unavailable",
    "supervisor_start_failure_message",
]
