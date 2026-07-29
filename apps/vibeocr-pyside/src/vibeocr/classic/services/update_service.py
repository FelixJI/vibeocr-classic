"""应用更新服务（UI-free 纯逻辑层）。

负责检测新版本、下载更新包、SHA256 校验、skip-version 管理。

本模块是 ADR Phase 4「去 Qt 化」拆分后保留的 backend 纯逻辑层——不含任何 Qt 依赖，
可在无 PySide6 环境下 import（供 ``env_manager`` 的同步下载编排复用 ``DOWNLOAD_REASON_*``
/ ``verify_sha256`` / ``_source_label`` 等）。Qt 对话框与更新流程编排（``UpdateDialog``、
``await_dialog``、``UpdateService`` 编排器）已移至 ``vibeocr.classic.pyside.update``——那是 Qt 平台
壳层，pyside→services 是 ADR 既定的合法依赖方向。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from pathlib import Path
    from typing import Any

logger = logging.getLogger(__name__)

# 发布仓库标识与下载源选择收敛到 env_config（SSOT）。
# 发布渠道：CNB 仅镜像代码；产物唯一源 GitHub。
# 客户端按 NetworkDetector 选源：国内走 gh 代理加速（gh-proxy / ghproxy）→ GitHub 裸连；
# 海外直连 GitHub。CNB OpenAPI 需 token 鉴权，客户端无法匿名访问，不用于更新。
from vibeocr.classic.update_config import (  # noqa: E402
    GITHUB_API_LATEST,
    build_asset_url_pairs,
)
from vibeocr.runtime_contracts.utils.http_log import (  # noqa: E402
    guess_request_size,
    guess_response_size,
    log_http_response,
)

# ---------------------------------------------------------------------------
# 版本比较与数据模型
# ---------------------------------------------------------------------------


def compare_versions(v1: str, v2: str) -> int:
    """比较两个语义化版本号

    Returns:
        1 if v1 > v2, 0 if v1 == v2, -1 if v1 < v2
    """
    parts1 = [int(x) for x in v1.lstrip("v").split(".")]
    parts2 = [int(x) for x in v2.lstrip("v").split(".")]
    for a, b in zip(parts1, parts2):
        if a > b:
            return 1
        if a < b:
            return -1
    if len(parts1) > len(parts2):
        return 1
    if len(parts1) < len(parts2):
        return -1
    return 0


def _log_http_exchange(
    method: str,
    url: str,
    resp: httpx.Response,
    *,
    request_bytes: int | None = None,
    start_time: float | None = None,
    response_bytes: int | None = None,
    stream: bool = False,
) -> None:
    """统一输出 HTTP 请求/响应摘要。"""
    elapsed_ms: float | None = None
    if start_time is not None:
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
    headers_obj = getattr(resp, "headers", None)
    try:
        headers = dict(headers_obj) if headers_obj is not None else None
    except Exception:
        headers = None
    # streaming 响应在 body 未消费前访问 ``.content`` 会抛
    # ``httpx.ResponseNotRead``——它继承 ``RuntimeError``，**非** ``AttributeError``，
    # 故 ``getattr(resp, "content", None)`` 的默认值兜不住，异常会冒泡。历史上
    # 这导致非 200 分支（GitHub 404 等）的 zip 下载被误报为「下载异常，换源」，
    # 三个镜像源全部失败、最终提示「所有更新包下载源均失败」。显式 try/except 才稳。
    # ``.text`` 同理（成功路径已通过 aiter_bytes 消费流，仅未消费分支会异常）。
    content: bytes | str | None = None
    try:
        content_obj = resp.content
        if isinstance(content_obj, (bytes, str)):
            content = content_obj
    except Exception:
        content = None
    resp_bytes = (
        response_bytes
        if response_bytes is not None
        else guess_response_size(headers, content)
    )
    reason = getattr(resp, "reason_phrase", None)
    if reason is None:
        text: object = None
        try:
            text = resp.text
        except Exception:
            text = None
        reason = text[:32] if isinstance(text, str) and text else None
    log_http_response(
        logger=logger,
        method=method,
        url=url,
        status_code=resp.status_code,
        reason=reason,
        elapsed_ms=elapsed_ms,
        request_bytes=request_bytes,
        response_bytes=resp_bytes,
        stream=stream,
    )


@dataclass
class UpdateInfo:
    """远程版本信息"""

    version: str
    download_url: str
    sha256_url: str
    changelog: str
    file_size: int = 0
    # release 真实 asset 文件名（如 VibeOCR-Classic-v0.4.34-win64.zip）。
    # download_update 用它拼代理 URL——早期版本硬编码 ``VibeOCR-v{version}-win64.zip``，
    # 在 v0.4.29+ 产物改名加 ``-Classic-`` 后会拼出 404 URL，导致所有源失败。
    # 现在从 release API 把真实文件名带下来，与发版产物解耦。
    zip_filename: str = ""
    sha256_filename: str = ""

    @classmethod
    def from_release(cls, release: dict) -> UpdateInfo:
        zip_name, zip_url = _find_asset(release, ".zip")
        sha_name, sha_url = _find_asset(release, ".sha256")
        return cls(
            version=release["tag_name"].lstrip("v"),
            download_url=zip_url,
            sha256_url=sha_url,
            changelog=release.get("body", ""),
            file_size=_find_asset_size(release, ".zip"),
            zip_filename=zip_name,
            sha256_filename=sha_name,
        )


def _asset_matches(name: str, suffix: str) -> bool:
    """判断 asset 名是否匹配目标类型（zip 主包 / sha256 校验文件）。

    主包（.zip）：必须 ``.zip`` 结尾，排除 ``.sha256`` 自身（避免把
    ``VibeOCR-...-win64.zip.sha256`` 当主包）和 ``-webengine-``（历史单独发布
    的 webengine 资源包，现已内置主包，保留排除作历史 release 防御）。
    校验文件（.sha256）：同理排除 webengine 的。
    """
    if suffix == ".zip":
        return name.endswith(".zip") and ".sha256" not in name and "-webengine-" not in name
    if suffix == ".sha256":
        return name.endswith(".sha256") and "-webengine-" not in name
    return False


def _find_asset(release: dict, suffix: str) -> tuple[str, str]:
    """从 release assets 中选出本模块要下载的 asset (name, url)。

    本模块（update_service.py）**只在 Classic（PySide6/Python）进程运行**——
    WinUI Next 是 C# 应用，有独立更新链路，不 import 本文件。故当 release 同时
    发布 Classic 与 Next 两个 zip 时（release.yml 双前端产物命名规则），
    必须选 ``-Classic-`` 命名的那个，否则会下到错误前端的包。

    选择规则：
    1. **优先**：名字含 ``-Classic-`` 且匹配 suffix（本运行态前端）。
    2. **回退**：任意匹配 suffix 的 asset（兼容历史 release v0.4.28 及之前无
       ``-Classic-`` 命名的产物，以及单元测试 fixture）。回退取第一个匹配项。

    找不到返回 ``("", "")``。
    """
    assets = release.get("assets", [])
    fallback: tuple[str, str] = ("", "")
    for asset in assets:
        name = asset["name"]
        if not _asset_matches(name, suffix):
            continue
        url = asset.get("browser_download_url") or asset.get("download_url", "")
        # 优先：Classic 命名（本模块运行态前端）。命中即返回，不继续。
        if "-Classic-" in name:
            return name, url
        # 回退：记下第一个匹配的非 Classic asset，循环结束若无 Classic 命中再用。
        if fallback == ("", ""):
            fallback = (name, url)
    return fallback


def _find_asset_url(release: dict, suffix: str) -> str:
    """薄封装：返回匹配 asset 的 URL（向后兼容现有调用点）。"""
    return _find_asset(release, suffix)[1]


def _find_asset_size(release: dict, suffix: str) -> int:
    """返回匹配 asset 的 size（与 _find_asset 同选择规则，保证 name/size/url 一致）。"""
    name = _find_asset(release, suffix)[0]
    if not name:
        return 0
    for asset in release.get("assets", []):
        if asset["name"] == name:
            return asset.get("size", 0)
    return 0


def read_local_version(version_json_path: Path) -> str:
    """读取本地 version.json 中的版本号

    打包态：便携 Python 独立运行，无法 import 主包的 __version__，只能读
    version.json（由 bump_version._generate_version_json 在打包时写入）。
    开发态：version.json 不存在时回退到 __version__，让本地也能正常检查
    更新（与 sync_client._app_version 的回退模式一致）。两条路径都失败
    才返回 "0.0.0"，调用方据此跳过更新检查。
    """
    if version_json_path.exists():
        try:
            data = json.loads(version_json_path.read_text(encoding="utf-8"))
            version = data.get("version")
            if version:
                return version
        except (json.JSONDecodeError, OSError):
            pass  # 损坏时落到下方 __version__ 回退
    try:
        from vibeocr.classic import __version__

        if __version__:
            return str(__version__)
    except Exception:
        pass
    return "0.0.0"


# ---------------------------------------------------------------------------
# 远程版本检查
# ---------------------------------------------------------------------------


async def _fetch_release(url: str, headers: dict | None = None) -> dict | None:
    """通用：获取单个 release API 端点的 JSON，失败返回 None。"""
    try:
        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, headers=headers or {})
            _log_http_exchange(
                "GET",
                url,
                resp,
                request_bytes=guess_request_size(getattr(resp.request, "content", None)),
                start_time=started,
            )
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        logger.debug(f"release API 请求失败: {url}")
    return None


def _detect_network_type() -> str:
    """读取 NetworkDetector 的网络类型；探测失败默认 international。"""
    try:
        from vibeocr.backend.env_manager import get_project_root
        from vibeocr.backend.network_detector import NetworkDetector

        return NetworkDetector(get_project_root()).network_type
    except Exception:
        return "international"


async def _probe_github_reachable(timeout: float = 3.0) -> bool:
    """快速探测 GitHub API（api.github.com）是否可达。

    .. todo::
        本探测只打 api.github.com（API host，国内常可达），不打 release download
        host（github.com/.../releases/download/...，国内更易被墙）。结果是国内
        用户即便探测「通过」、走国际分支直连 GitHub，下载时仍可能失败。完整修复
        应同时探测一个 release asset 的 HEAD（如 latest release 的 .sha256 文件，
        体积小）。非本次 bug 根因，留待后续改进。

    与 ``NetworkDetector`` 的国内/海外判定互补：那个判断用户所在网络环境（中国 vs
    海外），本函数判断「此刻能不能直连 GitHub」。典型场景：海外或代理环境下
    ``NetworkDetector`` 判 international（应直连 GitHub），但 GitHub 实际被墙/不稳定，
    此时下载应改走国内代理（gh-proxy / ghproxy）。仅在 international 分支调用：
    domestic 分支本就代理优先，无需再探测。

    用 HEAD 请求 + 3s 超时，失败（DNS/连接/SSL/超时/5xx）一律视为不可达。
    4xx（如 403 限流）仍视为可达——说明能连上 GitHub，只是 rate limited。
    """
    try:
        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.head(
                GITHUB_API_LATEST,
                headers={"Accept": "application/vnd.github+json"},
            )
            _log_http_exchange(
                "HEAD",
                GITHUB_API_LATEST,
                resp,
                request_bytes=0,
                start_time=started,
            )
            return resp.status_code < 500
    except Exception as e:
        logger.debug(f"GitHub 可达性探测失败（视为不可达）: {e}")
        return False


async def check_for_updates(
    current_version: str,
) -> tuple[UpdateInfo | None, bool]:
    """检查是否有新版本（GitHub Release API）

    Returns:
        (update_info, fetch_ok)：
        - fetch_ok=False 表示请求 GitHub 失败（上层应提示手动下载）。
        - fetch_ok=True 且 update_info=None 表示已是最新。
    """
    sources = [
        (GITHUB_API_LATEST, {"Accept": "application/vnd.github+json"}),
    ]

    release: dict | None = None
    for url, headers in sources:
        release = await _fetch_release(url, headers)
        if release is not None:
            break

    if release is None:
        logger.info("无法获取远程版本信息（GitHub 失败）")
        return None, False

    remote = UpdateInfo.from_release(release)

    if compare_versions(remote.version, current_version) <= 0:
        logger.debug(f"当前版本 {current_version} 已是最新")
        return None, True

    if not remote.download_url:
        logger.warning("未找到下载链接")
        return None, True

    return remote, True


# ---------------------------------------------------------------------------
# 下载与校验
# ---------------------------------------------------------------------------


# 单个下载源的尝试结果。reason 用于汇总失败原因，让上层向用户呈现真实原因，
# 而不是一律甩锅「网络问题」。OK 时 reason == "ok"。
DOWNLOAD_REASON_OK = "ok"
DOWNLOAD_REASON_HTTP_ERROR = "http_error"  # zip 非 200 等
DOWNLOAD_REASON_SHA_MISSING = "sha_missing"  # 校验文件下不到（404/非 200）
DOWNLOAD_REASON_SHA_MISMATCH = "sha_mismatch"  # 校验文件在但哈希对不上
DOWNLOAD_REASON_EXCEPTION = "exception"
# 用户主动取消（点对话框「取消」按钮或标题栏 X）。与上面失败原因并列，
# 让 download_update 的 (zip_path, fail_reasons) 返回结构在取消路径上仍统一；
# 上层 _do_download_and_update 据此短路退出整个更新流程，不弹重试框。
DOWNLOAD_REASON_CANCELLED = "cancelled"


class SourceAttempt(NamedTuple):
    ok: bool
    reason: str


def verify_sha256(file_path: Path, sha256_file: Path) -> bool:
    if not sha256_file.exists():
        logger.warning(f"SHA256 文件不存在: {sha256_file}")
        return False

    expected_hash = sha256_file.read_text(encoding="utf-8").strip().split()[0].lower()
    # 分块流式计算（与 update_replacer.verify_sha256 一致）：避免一次性把 ~227MB+ 的
    # zip 读进内存造成瞬时峰值。此处虽已被 asyncio.to_thread 搬到线程池、不冻结 UI，
    # 但内存峰值仍会与并发任务叠加。8MB 块恒定峰值，速度持平或略快。
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 23), b""):  # 8MB
            h.update(chunk)
    actual_hash = h.hexdigest().lower()

    if actual_hash != expected_hash:
        logger.error(f"SHA256 校验失败: expected={expected_hash}, actual={actual_hash}")
        return False

    return True


def _valid_sha256_text(text: str) -> bool:
    """校验 release 校验文件的首字段，避免把代理错误页当成 SHA 文件。"""
    fields = text.strip().split()
    if not fields or len(fields[0]) != 64:
        return False
    try:
        int(fields[0], 16)
    except ValueError:
        return False
    return True


async def _download_zip_with_sha(
    client: httpx.AsyncClient,
    zip_url: str,
    sha_url: str,
    zip_path: Path,
    sha256_path: Path,
    progress_callback: Callable[[int, int], None] | None,
    cancel_event: asyncio.Event | None = None,
) -> SourceAttempt:
    """从单个源下载 zip + 对应 sha256 校验文件并校验。

    ``sha_url`` 由调用方从 release asset 列表精确匹配提供（同源同 tag），
    而非这里盲拼 ``{zip_url}.sha256``——后者可能下到无关/404 内容。

    ``cancel_event``：可选的协作式取消令牌。每写完一块就检查；被 set 时
    跳出流式循环，清理已落盘的 zip，返回 ``SourceAttempt(False, cancelled)``。
    这是下载链路里最细粒度的取消点——大文件分块下载时能在百 KB 级别响应取消。

    返回 SourceAttempt；失败时清理残留。供 download_update 在多源候选间逐个调用。
    """
    try:
        if cancel_event is not None and cancel_event.is_set():
            return SourceAttempt(False, DOWNLOAD_REASON_CANCELLED)

        # 先下载只有几十字节的 SHA 文件，作为当前源的低成本预检。旧流程先下完整
        # 170MB+ ZIP，最后才发现代理的 SHA 端点 404/错误页，换源前白等一整包。
        # SHA 先行能让坏源在数秒内失败，同时不削弱最终完整性校验。
        sha_started = time.perf_counter()
        sha_resp = await client.get(sha_url)
        _log_http_exchange(
            "GET",
            sha_url,
            sha_resp,
            request_bytes=0,
            start_time=sha_started,
        )
        if sha_resp.status_code != 200:
            logger.warning(f"sha256 下载失败({sha_resp.status_code})：{sha_url}")
            return SourceAttempt(False, DOWNLOAD_REASON_SHA_MISSING)
        if not _valid_sha256_text(sha_resp.text):
            logger.warning(f"sha256 响应格式无效，换源：{sha_url}")
            return SourceAttempt(False, DOWNLOAD_REASON_SHA_MISMATCH)
        sha256_path.write_text(sha_resp.text, encoding="utf-8")

        if cancel_event is not None and cancel_event.is_set():
            sha256_path.unlink(missing_ok=True)
            return SourceAttempt(False, DOWNLOAD_REASON_CANCELLED)

        # SHA 预检通过后再流式下载大 ZIP（带进度回调）
        cancelled = False
        zip_started = time.perf_counter()
        async with client.stream("GET", zip_url) as resp:
            if resp.status_code != 200:
                _log_http_exchange(
                    "GET",
                    zip_url,
                    resp,
                    request_bytes=0,
                    start_time=zip_started,
                    stream=True,
                )
                logger.warning(f"zip 下载失败({resp.status_code})：{zip_url}")
                sha256_path.unlink(missing_ok=True)
                return SourceAttempt(False, DOWNLOAD_REASON_HTTP_ERROR)
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(zip_path, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        progress_callback(downloaded, total)
                    # 每块写入后检查取消：用户点「取消」后无需等整包下完
                    if cancel_event is not None and cancel_event.is_set():
                        cancelled = True
                        logger.info(f"用户取消下载，清理残留：{zip_url}")
                        break
            if not cancelled:
                _log_http_exchange(
                    "GET",
                    zip_url,
                    resp,
                    request_bytes=0,
                    start_time=zip_started,
                    response_bytes=downloaded,
                    stream=True,
                )
        if cancelled:
            zip_path.unlink(missing_ok=True)
            sha256_path.unlink(missing_ok=True)
            return SourceAttempt(False, DOWNLOAD_REASON_CANCELLED)

        # verify_sha256 启动前检查取消：哈希计算是数秒级 CPU/IO 操作（线程池），
        # 启动后不强制中断（成本高且很快完成），但启动前拦截可避免这段等待。
        if cancel_event is not None and cancel_event.is_set():
            zip_path.unlink(missing_ok=True)
            sha256_path.unlink(missing_ok=True)
            return SourceAttempt(False, DOWNLOAD_REASON_CANCELLED)

        # 校验。verify_sha256 同步读整个 zip（~50MB+）入内存算哈希，在 qasync 事件
        # 循环里会冻结 UI 与取消响应（弱网/慢盘下尤甚，曾表现为「下载完成后无响应」）。
        # 用 asyncio.to_thread 把重 CPU/IO 搬到默认线程池，事件循环继续转。
        if not await asyncio.to_thread(verify_sha256, zip_path, sha256_path):
            logger.warning(f"SHA256 校验失败，换源：{zip_url}")
            zip_path.unlink(missing_ok=True)
            sha256_path.unlink(missing_ok=True)
            return SourceAttempt(False, DOWNLOAD_REASON_SHA_MISMATCH)
        return SourceAttempt(True, DOWNLOAD_REASON_OK)
    except Exception as e:
        logger.warning(f"下载异常，换源：{zip_url}: {e}")
        zip_path.unlink(missing_ok=True)
        sha256_path.unlink(missing_ok=True)
        return SourceAttempt(False, DOWNLOAD_REASON_EXCEPTION)


async def download_update(
    update_info: UpdateInfo,
    cache_dir: Path,
    progress_callback: Callable[[int, int], None] | None = None,
    source_switch_callback: Callable[[str, str], None] | None = None,
    cancel_event: asyncio.Event | None = None,
) -> tuple[Path | None, list[str]]:
    """下载更新包（按网络环境多源回退）。

    不直接用 update_info.download_url（API 返回的 GitHub 直链）下载——那样国内
    用户访问 github.com 会被墙。而是由 env_config.build_asset_url_pairs 按 tag
    + **真实 asset 文件名**重拼 (zip, sha256) 配对候选，逐个尝试，确保 GitHub
    来源在国内有 gh 代理加速（gh-proxy / ghproxy）。校验文件 URL 与 zip 同源同 tag
    精确匹配，不再盲拼 ``{zip}.sha256``。

    文件名取自 ``update_info.zip_filename`` / ``sha256_filename``（由 ``UpdateInfo.from_release``
    从 release API 的 assets 列表带下来，按当前运行态前端选 ``-Classic-`` 命名的 asset）。
    早期版本在此硬编码 ``VibeOCR-v{version}-win64.zip``，但 v0.4.29+ 发版产物改名加
    ``-Classic-``（区分双前端）后，硬编码名拼出的 URL 全部 404，导致更新全挂。

    Args:
        source_switch_callback: 某源失败时回调 ``(failed_source_name, reason)``，
            供进度框实时显示「源 X 校验失败，切换源 Y…」。
        cancel_event: 可选的协作式取消令牌。在「清理残留」前、每次换源前
            检查；被 set 时立即返回 ``(None, [cancelled])``，不再尝试后续源。
            流式块级取消由 ``_download_zip_with_sha`` 在内部检查。

    Returns:
        (zip_path, fail_reasons)：成功时 fail_reasons 为空列表；全失败时 zip_path
        为 None，fail_reasons 为各源失败原因（供上层分桶提示用户）。
    """
    if not update_info.download_url:
        logger.error("下载 URL 为空")
        return None, [DOWNLOAD_REASON_HTTP_ERROR]

    # 进入函数即检查取消（用户在进度框一弹出就点取消）
    if cancel_event is not None and cancel_event.is_set():
        logger.info("下载开始前用户已取消")
        return None, [DOWNLOAD_REASON_CANCELLED]

    cache_dir.mkdir(parents=True, exist_ok=True)

    # 清理残留文件
    for old_file in cache_dir.iterdir():
        if old_file.is_file():
            try:
                old_file.unlink()
            except OSError:
                pass

    # 真实文件名来自 release API（UpdateInfo.from_release 按 -Classic- 优先选 asset）。
    # 不再硬编码 ``VibeOCR-v{version}-win64.zip``——v0.4.29+ 产物改名后硬编码会拼出
    # 404 URL。sha256 文件名优先用 release 带下来的；缺失时退化为 ``{zip}.sha256``
    # （历史上 sha 文件名恒等于 zip 名加 .sha256，此退化路径仅作防御）。
    zip_filename = update_info.zip_filename
    sha_filename = update_info.sha256_filename or f"{zip_filename}.sha256"
    if not zip_filename:
        logger.error(
            "UpdateInfo 缺失真实 zip 文件名（release 无匹配 asset），无法拼下载 URL"
        )
        return None, [DOWNLOAD_REASON_HTTP_ERROR]
    zip_path = cache_dir / zip_filename
    sha256_path = cache_dir / sha_filename
    network_type = _detect_network_type()
    # 海外环境（NetworkDetector 判 international）默认直连 GitHub。但 GitHub 实际
    # 不可达时（被墙/不稳定），直连只会在所有源失败后才提示「网络问题」，体验差且
    # 浪费一次完整下载。此处主动探测 GitHub：不可达则降级走国内代理源序。
    # domestic 分支本就代理优先，无需探测（避免每次更新都多打一个请求）。
    if network_type == "international" and not await _probe_github_reachable():
        logger.info("GitHub 直连不可达，改用国内代理源序下载")
        network_type = "domestic"
    url_pairs = build_asset_url_pairs(
        network_type, update_info.version, zip_filename, sha_filename
    )

    fail_reasons: list[str] = []
    # connect/pool 失败要快速换源；大包只在连续 15 秒收不到任何字节时判定当前源
    # 卡住。httpx 的 read timeout 是“无数据间隔”而非整包总时限，正常慢速下载不会
    # 因包体较大被粗暴截断。
    timeout = httpx.Timeout(30.0, connect=5.0, read=15.0, write=15.0, pool=5.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for url, sha_url in url_pairs:
            # 换源间隙检查取消（前一源失败后、下一源开始前）
            if cancel_event is not None and cancel_event.is_set():
                logger.info("换源间隙用户已取消下载")
                break
            source_name = _source_label(url)
            logger.info(f"尝试下载源：{url}")
            # 用竞速包装包住单源尝试：cancel_event 一旦先 set，立即返回 cancelled 并
            # cancel 卡住的下载任务。覆盖 _download_zip_with_sha 内部协作取消点无法
            # 响应的窗口——SHA 预检 ``client.get``（httpx get 不接受协作取消，read
            # timeout 15s）、verify_sha256（to_thread 不可中断的 170MB 哈希）、流式
            # chunk 间隙（源慢/卡住时 aiter_bytes 阻塞到下一块或 15s read timeout）。
            # 任务被 cancel 时抛 CancelledError，会中断 httpx 请求并关闭连接；
            # _download_zip_with_sha 的 except 会清理半成品 zip/sha。
            attempt = await _attempt_or_cancel(
                _download_zip_with_sha(
                    client,
                    url,
                    sha_url,
                    zip_path,
                    sha256_path,
                    progress_callback,
                    cancel_event=cancel_event,
                ),
                cancel_event=cancel_event,
            )
            if attempt.ok:
                logger.info(f"更新包下载完成：{zip_path}")
                return zip_path, []
            fail_reasons.append(attempt.reason)
            # 用户取消：不再换源，直接跳出（reason 已是 cancelled）
            if attempt.reason == DOWNLOAD_REASON_CANCELLED:
                break
            logger.warning(f"更新包下载/校验失败，换源：{url}")
            if source_switch_callback:
                source_switch_callback(source_name, attempt.reason)

    logger.error("所有更新包下载源均失败（或用户已取消）")
    return None, fail_reasons


async def _attempt_or_cancel(
    attempt_coro: Coroutine[Any, Any, SourceAttempt],
    *,
    cancel_event: asyncio.Event | None = None,
) -> SourceAttempt:
    """单源下载尝试与取消事件的竞速包装。

    历史 bug：``_download_zip_with_sha`` 内部虽在多个检查点协作式取消（块级、
    单源开始前、SHA 预检后/verify 前），但 SHA 预检的 ``client.get(sha_url)`` 和
    verify_sha256 是不可中断的网络/CPU 阻塞段，用户在这些段期间点「取消下载」要
    等阻塞段自然结束（最坏 15s read timeout + 数秒哈希）才生效，按钮迟迟不变。

    修复：把单源尝试包成一个 Task，与 ``cancel_event.wait()`` 用 ``asyncio.wait``
    竞速。取消先到则立即返回 cancelled 并 cancel 下载 Task（CancelledError 中断
    httpx 请求、关闭连接；_download_zip_with_sha 的 except 清理半成品）。下载先
    正常完成则返回其 SourceAttempt。

    Args:
        attempt_coro: 单源下载协程（``_download_zip_with_sha(...)``）。
        cancel_event: 取消事件；None 时直接 await 协程（无竞速，保持原行为）。
    """
    if cancel_event is None:
        return await attempt_coro

    cancel_wait = asyncio.ensure_future(cancel_event.wait())
    attempt_task = asyncio.ensure_future(attempt_coro)
    try:
        done, _pending = await asyncio.wait(
            {attempt_task, cancel_wait}, return_when=asyncio.FIRST_COMPLETED
        )
    except BaseException:
        # 等待过程自身被 cancel（如上层关闭取消整个 download_update）：清理两个子任务。
        attempt_task.cancel()
        cancel_wait.cancel()
        raise

    if attempt_task in done:
        cancel_wait.cancel()
        return attempt_task.result()

    # cancel 先到：cancel 卡住的下载任务，让 _download_zip_with_sha 的 except 清理
    # 半成品 zip/sha。to_thread 的 verify_sha256 不可中断会在线程池跑完丢弃结果，
    # 但不阻塞本协程返回——用户感知的取消响应不受影响。
    cancel_wait.cancel()
    attempt_task.cancel()
    # 静默吞掉 CancelledError（任务被我们主动 cancel），不让它冒泡破坏编排器。
    try:
        await attempt_task
    except (asyncio.CancelledError, Exception):
        pass
    return SourceAttempt(False, DOWNLOAD_REASON_CANCELLED)


def _source_label(url: str) -> str:
    """从 URL 提取人类可读的源名，用于换源提示文案。

    同时被 env_manager.download_artifact_multi_source（同步多源下载编排器）复用，
    保持同步/异步两套下载链路的源名提示一致。修改此处会同时影响两者。
    """
    for label, marker in (
        ("gh-proxy", "gh-proxy.com"),
        ("ghproxy", "ghproxy.com"),
        ("GitHub", "github.com"),
    ):
        if marker in url:
            return label
    return url


# ---------------------------------------------------------------------------
# 跳过版本管理
# ---------------------------------------------------------------------------


def load_skip_version(settings_path: Path) -> str:
    if not settings_path.exists():
        return ""
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        return data.get("skip_version", "")
    except (json.JSONDecodeError, OSError):
        return ""


def save_skip_version(version: str, settings_path: Path) -> None:
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    data["skip_version"] = version
    settings_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def should_skip_version(version: str, settings_path: Path) -> bool:
    return load_skip_version(settings_path) == version


# ---------------------------------------------------------------------------
# 「稍后提醒」暂缓管理
# ---------------------------------------------------------------------------
#
# 历史 bug：UpdateDialog 的「稍后提醒」按钮只把 action 设为 "cancel" 并 reject，
# check_and_prompt 不处理该分支、没有任何持久化——下次检查（启动自动检查 / 手动
# 检查）立刻再次弹窗，按钮形同虚设。
#
# 修复：仿照 skip_version 三件套，用 remind_later_until（unix 时间戳，单调墙钟）
# 持久化暂缓到期时间。自动检查（manual=False）命中窗口则不弹；手动检查
# （manual=True）始终弹（用户主动请求，忽略暂缓）。
#
# 用 unix 时间戳而非 wall-clock ISO8601：判定逻辑是「now < until」的纯数值比较，
# 时间戳最简且无时区/格式歧义。time.time() 是本模块既定的时间来源（见
# _log_http_exchange 的 perf_counter 用于耗时，time.time 用于绝对时刻语义一致）。

# 「稍后提醒」暂缓窗口（秒）。1 天：足够给用户喘息，又不会让更新提醒长期沉默。
REMIND_LATER_SECONDS: int = 86400


def load_remind_later(settings_path: Path) -> float:
    """读取暂缓到期时间戳。

    Returns:
        到期 unix 时间戳；无设置 / 损坏 / 缺字段返回 0.0（视为从未暂缓）。
    """
    if not settings_path.exists():
        return 0.0
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        until = data.get("remind_later_until", 0.0)
        return float(until) if until else 0.0
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return 0.0


def save_remind_later(until_ts: float, settings_path: Path) -> None:
    """写入暂缓到期时间戳。

    与 save_skip_version 共用同一文件（update_settings.json），合并写回，互不覆盖。
    """
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    data["remind_later_until"] = until_ts
    settings_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def is_remind_later_active(
    settings_path: Path, *, now: float | None = None
) -> bool:
    """暂缓是否仍在窗口内。

    Args:
        now: 当前 unix 时间戳；None 则取 ``time.time()``。测试注入固定时刻用。
    """
    current = time.time() if now is None else now
    until = load_remind_later(settings_path)
    return until > current
