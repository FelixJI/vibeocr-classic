"""MinerU 连接设置的 Qt-free 投影、校验与会话状态。

Classic 只收集用户输入、按 Backend 契约校验，并把结果写进 Supervisor
settings 的 ``extra.mineru_connection``；远程解析完全由 Backend 执行，
前端不发起任何远程 HTTP 连接。目标服务是自部署 MinerU 4 的 HTTP API
（/v1 完整解析），不是云端 token 套餐或 OpenAI 兼容推理接口。

API Key 是凭据：只能写入 settings 快照，不得进入日志、状态文案或诊断
信息；UI 永不回显已保存的 Key。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

#: settings ``extra`` 中承载连接配置的键。
MINERU_CONNECTION_EXTRA_KEY = "mineru_connection"

MINERU_CONNECTION_MODE_LOCAL = "local"
MINERU_CONNECTION_MODE_REMOTE = "remote"

_MINERU_CONNECTION_MODES = frozenset(
    {MINERU_CONNECTION_MODE_LOCAL, MINERU_CONNECTION_MODE_REMOTE}
)


def validate_mineru_api_url(raw_url: str) -> str | None:
    """校验自部署 MinerU 服务根地址；非法时返回错误文案，合法返回 None。

    与 Backend 契约一致：仅接受 http/https 根地址，禁止空白字符、
    userinfo、query 与 fragment；路径保留（支持反向代理前缀）。
    """

    if not isinstance(raw_url, str) or not raw_url.strip():
        return "服务根地址不能为空"
    if any(character.isspace() for character in raw_url):
        return "服务根地址不能包含空白字符"
    try:
        parsed = urlsplit(raw_url)
        host = parsed.hostname
        parsed.port  # malformed ports raise ValueError
    except ValueError:
        return "服务根地址无效"
    if parsed.scheme not in {"http", "https"}:
        return "服务根地址必须以 http:// 或 https:// 开头"
    if not parsed.netloc or not host:
        return "服务根地址缺少主机名"
    if parsed.username is not None or parsed.password is not None:
        return "服务根地址不能包含用户名或密码"
    if parsed.query:
        return "服务根地址不能包含查询参数（?）"
    if parsed.fragment:
        return "服务根地址不能包含锚点（#）"
    return None


def validate_mineru_api_key(raw_key: str) -> str | None:
    """校验 API Key；非法时返回错误文案，合法返回 None。

    仅按 Backend 契约禁止 CR/LF，不设置无契约依据的长度上限。
    """

    if not isinstance(raw_key, str):
        return "API Key 无效"
    if "\r" in raw_key or "\n" in raw_key:
        return "API Key 不能包含换行符"
    return None


def parse_mineru_connection(
    extra: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """从 settings ``extra`` 读取连接配置的规范化视图。

    容错优先：缺失或结构非法时回退本地默认视图，不抛异常。视图中
    ``api_key`` 只用于保存时保留旧值与判断是否已配置，禁止回显到
    UI 或写入日志。
    """

    view: dict[str, Any] = {
        "mode": MINERU_CONNECTION_MODE_LOCAL,
        "api_url": "",
        "api_key": None,
        "has_api_key": False,
    }
    raw = extra.get(MINERU_CONNECTION_EXTRA_KEY) if isinstance(extra, Mapping) else None
    if not isinstance(raw, Mapping):
        return view
    mode = raw.get("mode")
    if mode not in _MINERU_CONNECTION_MODES:
        return view
    view["mode"] = mode
    api_url = raw.get("api_url")
    if mode == MINERU_CONNECTION_MODE_REMOTE and (
        not isinstance(api_url, str) or validate_mineru_api_url(api_url) is not None
    ):
        return {
            "mode": MINERU_CONNECTION_MODE_LOCAL,
            "api_url": "",
            "api_key": None,
            "has_api_key": False,
        }
    if isinstance(api_url, str):
        view["api_url"] = api_url
    api_key = raw.get("api_key")
    if isinstance(api_key, str) and api_key:
        view["api_key"] = api_key
        view["has_api_key"] = True
    return view


def build_mineru_connection(
    mode: str,
    raw_url: str,
    raw_key: str,
    *,
    clear_key: bool = False,
    previous: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """把 UI 输入投影为 ``extra.mineru_connection`` 载荷。

    返回 ``(payload, error)``；``error`` 非 None 时 ``payload`` 为
    None，调用方不得发送任何请求。``raw_key`` 为空且未显式清除时保留
    ``previous``（parse 视图或已存 dict）中已保存的 Key，避免用户仅
    修改 URL 时意外丢失凭据；只有 ``clear_key`` 才删除已保存 Key。
    """

    if mode not in _MINERU_CONNECTION_MODES:
        return None, "未知连接模式"
    if mode == MINERU_CONNECTION_MODE_LOCAL:
        # 本地模式仅保存 mode：显式丢弃 URL/Key，不残留过时凭据。
        return {"mode": MINERU_CONNECTION_MODE_LOCAL}, None
    url_error = validate_mineru_api_url(raw_url)
    if url_error is not None:
        return None, url_error
    payload: dict[str, Any] = {
        "mode": MINERU_CONNECTION_MODE_REMOTE,
        "api_url": raw_url,
    }
    stored_key: str | None = None
    if clear_key:
        stored_key = None
    elif raw_key:
        key_error = validate_mineru_api_key(raw_key)
        if key_error is not None:
            return None, key_error
        stored_key = raw_key
    else:
        previous_key = (previous or {}).get("api_key")
        stored_key = (
            previous_key if isinstance(previous_key, str) and previous_key else None
        )
    if stored_key:
        payload["api_key"] = stored_key
    return payload, None


# Classic 的一个 Supervisor 会话只持有一份生效的 MinerU 连接模式。
# 提交端（单文件/批量/PDF 共用的 OCROptions 投影）据此决定是否携带
# 旧 hybrid-engine 引擎选项；由设置页在回读 settings 后发布。
_active_connection_mode: str | None = MINERU_CONNECTION_MODE_LOCAL


def set_active_mineru_connection_mode(mode: str | None) -> None:
    """发布当前生效的连接模式（settings 回读后由设置页调用）。"""

    global _active_connection_mode
    _active_connection_mode = (
        mode
        if mode in _MINERU_CONNECTION_MODES or mode is None
        else MINERU_CONNECTION_MODE_LOCAL
    )


def active_mineru_connection_mode() -> str | None:
    """返回当前生效模式；None 表示尚未读到当前 Supervisor 设置。"""

    return _active_connection_mode


def mineru_remote_active() -> bool:
    """远程 MinerU API 连接是否生效。"""

    return _active_connection_mode == MINERU_CONNECTION_MODE_REMOTE


__all__ = [
    "MINERU_CONNECTION_EXTRA_KEY",
    "MINERU_CONNECTION_MODE_LOCAL",
    "MINERU_CONNECTION_MODE_REMOTE",
    "active_mineru_connection_mode",
    "build_mineru_connection",
    "mineru_remote_active",
    "parse_mineru_connection",
    "set_active_mineru_connection_mode",
    "validate_mineru_api_key",
    "validate_mineru_api_url",
]
