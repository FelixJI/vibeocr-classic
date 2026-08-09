"""User-facing Runtime status messages without Qt or installer dependencies."""

from collections.abc import Sequence

_ACCELERATOR_LABELS = {
    "cpu": "CPU",
    "nvidia_cuda": "NVIDIA CUDA",
}


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


__all__ = ["format_runtime_unavailable", "supervisor_start_failure_message"]
