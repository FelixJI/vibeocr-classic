"""VibeOCR 应用程序入口点"""

import os
import sys
import time as _time
from pathlib import Path

_PROCESS_START = _time.perf_counter()


def _configure_standard_streams() -> None:
    """统一冻结入口的文本输出编码，避免非 UTF-8 重定向阻断启动。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            # GUI 启动时标准流可能已关闭或不支持重配置，保持静默即可。
            pass


_configure_standard_streams()

# ============================================================
# 重要：必须在导入任何其他模块之前设置以下环境变量
# 这些设置解决 Windows + PaddlePaddle + NumPy 环境下的常见崩溃问题
# ============================================================

# 解决 OpenMP 库冲突 (libiomp5md.dll 重复加载导致 0xC0000005 崩溃)
# 当多个库（PaddlePaddle、NumPy、Intel MKL）各自捆绑不同版本的 OpenMP 时会发生冲突
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

# 兜底关闭 Paddle 的 oneDNN 全局 FLAGS（eager/动态图路径用）。
# 重要：PaddleOCR 推理走 paddle.inference.Config（AnalysisConfig），其 mkldnn 开关
# 由构造函数 enable_mkldnn 参数控制（见 OCRService._decide_enable_mkldnn），
# 这俩 FLAGS 对推理路径【不生效】——paddleocr/paddlex 零处读取它们。保留只为
# 保险地关闭任何 eager 路径的 oneDNN（某些 CPU 指令集不兼容会崩溃）。
# 真正的 CPU mkldnn 决策（含 paddle 3.3 黑名单）见 _decide_enable_mkldnn。
os.environ.setdefault("FLAGS_enable_onednn_backend", "0")
os.environ.setdefault("FLAGS_use_mkldnn", "0")

# CPU 线程数自适应：paddle/OpenMP 默认仅用 1 线程，浪费多核 CPU。
# 按逻辑核数设置，避免 i9-14900KF 这类 32 线程 CPU 仅单核推理。
# 用户可用 VIBEOCR_CPU_THREADS 显式覆盖。
try:
    from vibeocr.backend.utils.cpu_info import get_cpu_thread_count

    _cpu_threads = str(get_cpu_thread_count())
    os.environ.setdefault("OMP_NUM_THREADS", _cpu_threads)
    os.environ.setdefault("FLAGS_omp_num_threads", _cpu_threads)
    os.environ.setdefault("FLAGS_cpu_threads", _cpu_threads)
except Exception:
    pass

# 设置环境变量以抑制不必要的警告
# 禁用 PaddleX 的模型源连接检查
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

# 导入环境管理模块
from vibeocr.backend import env_manager  # noqa: E402
from vibeocr.classic.app_paths import get_install_root  # noqa: E402
from vibeocr.classic.runtime_installation import (  # noqa: E402
    RuntimeInstallerClient,
    RuntimeInstallerClientError,
)

# ============================================================
# 启动里程碑记录：T0（进程入口）和 T1（运行时就绪）
# ============================================================
from vibeocr.classic.startup_metrics import (  # noqa: E402 — 必须在 env_manager 之后
    StartupEvent,
    flush_startup,
    record_startup,
    set_startup_origin,
)

set_startup_origin(_PROCESS_START)
record_startup(StartupEvent.PROCESS_START, 0.0)  # T0：进程入口基准（0.0）
record_startup(StartupEvent.RUNTIME_READY)  # T1：env_manager 已就绪


def _finish_t3_smoke(app) -> None:
    """Flush the rendered T3 milestone and terminate before background startup."""
    app.processEvents()
    flush_startup()
    os._exit(0)


def _startup_lock_names() -> tuple[str, str | None]:
    """Return stable production locks or process-unique T6 smoke locks."""
    if os.environ.get("VIBEOCR_SELF_TEST_SMOKE") == "t6":
        process_id = os.getpid()
        return (
            f"VibeOCR-SelfTest-{process_id}",
            rf"Local\VibeOCR.Frontend.Exclusive.SelfTest.{process_id}",
        )
    return "VibeOCR", None


def check_production_dependencies() -> bool:
    """验证产品绑定的 Installer；Runtime 安装必须由 GUI 征得用户同意。"""
    smoke_python = os.environ.get("VIBEOCR_SELF_TEST_PYTHON")
    if (
        getattr(sys, "frozen", False)
        and os.environ.get("VIBEOCR_SELF_TEST_SMOKE") == "t6"
        and smoke_python
        and Path(smoke_python).is_file()
    ):
        # Artifact verifier 会在解压目录内从绑定 wheel 建一个隔离 import 根。
        # 仅冻结态+t6 双门禁生效，生产启动始终必须通过 Runtime Installer inspect。
        return True
    client = RuntimeInstallerClient(get_install_root())
    try:
        inspection = client.inspect()
        if not inspection.ready:
            print(
                "[VibeOCR] Runtime 未就绪，等待用户在安装向导中选择推理后端: "
                f"{inspection.accelerator} / {inspection.integrity}"
            )
    except RuntimeInstallerClientError as exc:
        print(f"[VibeOCR] Runtime Installer 验证失败: {exc}")
        return False
    return True


def _resolve_replacer_module_dir() -> Path | None:
    """定位 update_replacer.py 所在目录，用于动态 import（cleanup_leftover_old_exes 等）。

    优先级：
    1. 打包态：``sys._MEIPASS``（update_replacer.py 由 --add-data 打入 _internal/ 根）。
    2. 开发态：仓库根下的 ``scripts/``（与 main.py 的相对位置回溯）。

    找不到返回 None（调用方据此报错退出）。
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is not None and (Path(meipass) / "update_replacer.py").exists():
        return Path(meipass)
    # 开发态：物理拆包后通过统一项目根定位 scripts/。
    dev_scripts = env_manager.get_project_root() / "scripts"
    if (dev_scripts / "update_replacer.py").exists():
        return dev_scripts
    return None


def _cleanup_update_artifacts(app_dir: Path) -> None:
    """后台清理上次更新的残留产物（成功路径下 updater 不再清理，移交本函数）。

    新架构下 updater 启动新主程序后立即退出，不做 cleanup（避免 55s I/O 阻塞
    关键路径）。本函数由新主程序在后台 daemon 线程调用，清理：
    - data/cache/update/tmp/（解压临时目录，数百 MB）
    - data/cache/update/_backup/（备份目录，防御性兜底）
    - data/cache/update/*.zip + *.sha256（更新包）
    - data/cache/update/updater.exe（暂存的新 updater，此刻已退出不锁）
    - data/cache/update/updater.ready（就绪信号）
    - *.exe.old（由 _cleanup_leftover_old_exes 单独负责）

    保留 data/cache/update/progress.json（关于页读取展示"上次更新各阶段耗时"）。

    幂等：多次调用无副作用。失败仅 log，绝不阻断启动。
    """
    try:
        cache_dir = app_dir / "data" / "cache" / "update"
        if not cache_dir.is_dir():
            return

        import shutil

        # tmp/ 解压目录
        tmp_dir = cache_dir / "tmp"
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)

        # 非更新启动时清理历史备份。由 updater 启动的新版进程必须保留本轮备份，
        # 直到窗口发布健康信号、updater 提交事务。
        backup_dir = cache_dir / "_backup"
        recovery_marker = cache_dir / "manual-recovery-required.json"
        if (
            backup_dir.exists()
            and not os.environ.get("VIBEOCR_UPDATE_HEALTH_FILE")
            and not recovery_marker.is_file()
        ):
            shutil.rmtree(backup_dir, ignore_errors=True)

        # zip + sha256 + 暂存 updater + ready（保留 progress.json）
        for item in cache_dir.iterdir():
            if item.name == "progress.json":
                continue
            if not item.is_file():
                continue
            if item.name.endswith((".zip", ".sha256")) or item.name in (
                "updater.exe",
                "updater.ready",
            ):
                try:
                    item.unlink(missing_ok=True)
                except OSError:
                    pass

        # 空 update 目录可删（progress.json 不在时）
        if cache_dir.exists() and not any(cache_dir.iterdir()):
            try:
                cache_dir.rmdir()
            except OSError:
                pass
    except Exception as e:
        print(f"[VibeOCR] 清理更新残留失败（不影响启动）: {e}")


def _publish_update_health(app_dir: Path) -> None:
    """向替换器确认新版已通过 Runtime 检查并显示主窗口。"""
    configured = os.environ.get("VIBEOCR_UPDATE_HEALTH_FILE")
    if not configured:
        return
    try:
        health_file = Path(configured).resolve()
        update_cache = (app_dir / "data" / "cache" / "update").resolve()
        if (
            not health_file.is_relative_to(update_cache)
            or health_file.name != "startup.health"
        ):
            raise ValueError("更新健康信号路径越出产品更新缓存")
        health_file.parent.mkdir(parents=True, exist_ok=True)
        health_file.write_text("ready\n", encoding="utf-8")
    except Exception as error:
        print(f"[VibeOCR] 发布更新健康信号失败: {error}")


def _cleanup_leftover_old_exes() -> None:
    """清理上次更新残留的 ``*.exe.old``（主程序启动入口）。

    背景：updater.exe / VibeOCR.exe 更新时把运行中的自己改名为 ``.old`` 后继续运行，
    Windows 禁止删运行中 exe（PE 映射锁），所以改名后的旧进程映像在 updater 的
    cleanup 阶段**必然删不掉**。updater 侧有 ``MoveFileEx(MOVEFILE_DELAY_UNTIL_REBOOT)``
    标记重启清理，但：旧版 updater（如 v0.4.13）没有 MoveFileEx 兜底、用户可能从不重启
    （笔记本常态）→ ``.old`` 永久堆积（8-9MB/个）。

    本函数是兜底的兜底：主程序每次启动（此刻旧进程已退出、锁已释放），清掉残留。
    复用 ``update_replacer.cleanup_leftover_old_exes``（同 module 的动态 import 路径
    与 _resolve_replacer_module_dir 一致），保证行为统一。任何异常仅打印、绝不阻断启动——
    清理残留是「锦上添花」，不能因它让应用起不来。
    """
    try:
        replacer_dir = _resolve_replacer_module_dir()
        if replacer_dir is None:
            return
        if str(replacer_dir) not in sys.path:
            sys.path.insert(0, str(replacer_dir))
        from update_replacer import (  # pyright: ignore[reportMissingImports]
            cleanup_leftover_old_exes,
        )

        app_dir = env_manager.get_project_root()
        cleanup_leftover_old_exes(app_dir)
    except Exception as e:
        # 清理失败不影响启动；残留最多占点空间，下次启动再试。
        print(f"[VibeOCR] 清理上次更新残留失败（不影响启动）: {e}")


def _create_tray_icon(app, window, app_settings):
    """创建系统托盘图标

    Args:
        app: QApplication 实例
        window: MainWindow 实例
        app_settings: AppSettings 实例

    Returns:
        QSystemTrayIcon 实例，如果不支持返回 None
    """
    from PySide6.QtGui import QAction, QIcon
    from PySide6.QtWidgets import QMenu, QSystemTrayIcon

    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("[VibeOCR] 系统不支持托盘图标")
        return None

    # 使用应用默认图标，如果没有则创建简单的彩色图标
    icon = app.windowIcon()
    if icon.isNull():
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QColor, QPixmap

        pixmap = QPixmap(QSize(64, 64))
        pixmap.fill(QColor("#0078d4"))
        icon = QIcon(pixmap)

    tray = QSystemTrayIcon(icon, app)
    tray.setToolTip("VibeOCR")

    # 上下文菜单
    menu = QMenu()

    action_show = QAction("显示主窗口", menu)
    action_show.triggered.connect(lambda: _show_main_window(window))
    menu.addAction(action_show)

    action_settings = QAction("设置", menu)
    action_settings.triggered.connect(lambda: _show_tray_settings(window))
    menu.addAction(action_settings)

    menu.addSeparator()

    action_quit = QAction("退出", menu)
    action_quit.triggered.connect(lambda: _quit_app(app, window))
    menu.addAction(action_quit)

    tray.setContextMenu(menu)

    # 点击托盘图标切换主窗口显示
    tray.activated.connect(lambda reason: _on_tray_activated(reason, window))

    tray.show()
    return tray


def _show_main_window(window):
    """显示并激活主窗口"""
    window.showNormal()
    window.activateWindow()
    window.raise_()


def _show_tray_settings(parent):
    """从托盘菜单打开主窗口设置标签页"""
    _show_main_window(parent)
    # 切换到设置标签页
    if hasattr(parent, "_ui") and hasattr(parent._ui, "tabWidget"):
        tab_widget = parent._ui.tabWidget
        for i in range(tab_widget.count()):
            if tab_widget.tabText(i) == "设置":
                tab_widget.setCurrentIndex(i)
                break


def _quit_app(app, window):
    """完全退出应用"""
    # 标记为真正退出（而非最小化到托盘）
    window._force_quit = True
    window.close()
    app.quit()


def _on_tray_activated(reason, window):
    """托盘图标激活事件"""
    from PySide6.QtWidgets import QSystemTrayIcon

    if reason == QSystemTrayIcon.ActivationReason.Trigger:
        if window.isVisible() and not window.isMinimized():
            window.hide()
        else:
            _show_main_window(window)


def _resolve_app_icon_path() -> Path | None:
    """解析应用图标路径，兼容开发态与 PyInstaller 打包态。

    打包态（PyInstaller --onedir）resources 目录由 ``--add-data`` 打入
    ``sys._MEIPASS``（即 ``_internal/resources``），而非 exe 同级——
    exe 同级只放运行时创建的可写目录。统一走
    ``env_manager.get_bundled_resources_dir()`` 定位，避免在 exe 同级找不到
    图标导致窗口/任务栏/托盘图标不显示。

    Returns:
        图标文件路径；找不到时返回 None。
    """
    icon = env_manager.get_bundled_resources_dir() / "app_icon.ico"
    return icon if icon.exists() else None


def _setup_app_icon(app) -> None:
    """为 QApplication 设置应用图标（窗口标题栏、任务栏、托盘共用）。

    图标必须在主窗口创建之前设置，窗口才会继承；缺失时不抛错，仅记录警告。
    """
    from PySide6.QtGui import QIcon

    icon_path = _resolve_app_icon_path()
    if icon_path is None:
        print("[VibeOCR] 未找到应用图标 resources/app_icon.ico，跳过设置")
        return

    icon = QIcon(str(icon_path))
    if icon.isNull():
        print(f"[VibeOCR] 图标加载失败: {icon_path}")
        return

    app.setWindowIcon(icon)
    app.setApplicationName("VibeOCR")


def _create_splash(app):
    """创建启动 splash 屏，让用户在 exe 加载 + 主窗口构造期间立刻看到品牌反馈。

    解决"双击 exe 后长时间无反应"的观感问题：PyInstaller bootloader 加载
    ``_internal/`` 与 MainWindow 构造（~1.5s）合计可达数秒~数十秒（含杀软扫描），
    此前用户只能看到空白/无响应。splash 在 QApplication 建好后立即 show，
    远早于主窗口。

    使用 resources/icon_512.png（512x512，frozen/dev 通用，走
    ``env_manager.get_bundled_resources_dir()``）。缺失时返回 None，不阻塞启动。

    渲染要点（修复用户反馈的三点问题）：
      1. LOGO 太大 → 不直接用 512px 源图，缩放到 ~200px 并放到 320px 卡片上。
      2. 四角黑边 → 源图标四角是全透明 (0,0,0,0)，Windows 上 frameless +
         WA_TranslucentBackground 会把透明像素合成成黑边（见 toolbar.py 注释）。
         这里改为把图标合成到**完全不透明**的白色卡片 pixmap 上，整窗无透明像素，
         从根源消除黑边；因此也不再设 WA_TranslucentBackground。
      3. 始终置顶 → 不再传 WindowStaysOnTopHint（QSplashScreen 本身已具备
         无边框/无任务栏图标的 splash 语义，无需额外置顶）。

    Args:
        app: QApplication 实例。

    Returns:
        QSplashScreen 实例；资源缺失时返回 None。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPainter, QPixmap
    from PySide6.QtWidgets import QSplashScreen

    splash_path = env_manager.get_bundled_resources_dir() / "icon_512.png"
    if not splash_path.is_file():
        return None

    src = QPixmap(str(splash_path))
    if src.isNull():
        return None

    # 卡片整体不透明（白色），图标居中缩小，避免 512px 满屏 + 杜绝四角黑边
    card_size = 320
    logo_size = 200
    canvas = QPixmap(card_size, card_size)
    canvas.fill(QColor("#ffffff"))

    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    logo = src.scaled(
        logo_size,
        logo_size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    logo_x = (card_size - logo.width()) // 2
    logo_y = (card_size - logo.height()) // 2 - 8  # 略上移，给底部版本号留空
    painter.drawPixmap(logo_x, logo_y, logo)
    painter.end()

    # 不传 WindowStaysOnTopHint → 不再置顶；不设 WA_TranslucentBackground → 无黑边
    splash = QSplashScreen(canvas)
    # 版本号显示在图标下方（白底卡片，用深色文字保证可读）
    try:
        from vibeocr.classic import __version__

        splash.showMessage(
            f"  VibeOCR  v{__version__}\n  正在启动…",
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
            QColor("#6b7280"),
        )
    except Exception:
        pass
    splash.show()
    app.processEvents()  # 强制立即绘制，不等事件循环
    return splash


def _install_qt_translations(app, locale: str | None = None) -> None:
    """加载 Qt 自带中文翻译，使标准对话框（QColorDialog/QFileDialog/QMessageBox
    等的 OK/Cancel/基本颜色 等按钮文案）显示为中文。

    PySide6 随包附带官方 .qm 翻译文件，其中 qtbase 覆盖基础模块（含 QColorDialog）。
    找不到时静默跳过（不抛错），此时控件退回 Qt 默认英文文案，不影响功能。
    翻译器需保留引用以防被回收。

    Args:
        app: QApplication 实例。
        locale: 显式指定 locale 名（如 "zh_CN"），默认取系统 locale。便于测试。
    """
    from pathlib import Path

    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator

    if locale is None:
        locale = QLocale.system().name()  # 如 "zh_CN"
    # 仅加载中文；其它语言保留 Qt 默认。
    if not locale.startswith("zh"):
        return

    translations_dir = Path(
        QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    )
    # PySide6 的 TranslationsPath 未必含 qtbase，回退到包内 translations 目录。
    if not (translations_dir / f"qtbase_{locale}.qm").is_file():
        try:
            import PySide6

            translations_dir = Path(PySide6.__file__).parent / "translations"
        except Exception:
            return

    # 保留在 app 上的引用，避免被垃圾回收导致翻译失效
    if not hasattr(app, "_qt_translators"):
        app._qt_translators = []  # type: ignore[attr-defined]

    for base in ("qtbase", "qt"):
        qm = translations_dir / f"{base}_{locale}.qm"
        if not qm.is_file():
            continue
        translator = QTranslator(app)
        if translator.load(str(qm)):
            app.installTranslator(translator)
            app._qt_translators.append(translator)  # type: ignore[attr-defined]


def _show_another_product_running_dialog() -> None:
    """检测到另一套 VibeOCR 产品（WinUI）运行时，提示用户退出后重试。

    不转发参数、不激活对方、不连接对方的 WorkerHost（ADR §6.2）。
    """
    from PySide6.QtWidgets import QMessageBox

    QMessageBox.warning(
        None,
        "VibeOCR",
        "另一套 VibeOCR（WinUI 版）正在运行。\n请先退出它，再重试。",
    )


def launch_application() -> int:
    """启动应用程序"""
    from PySide6.QtWidgets import QApplication

    from vibeocr.classic import __version__
    from vibeocr.classic.managers.config_manager import ConfigManager
    from vibeocr.classic.utils.app_settings import AppSettings
    from vibeocr.classic.utils.qt_async import (
        DelayedAsyncTask,
        create_qasync_event_loop,
    )
    from vibeocr.classic.views.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("VibeOCR")
    app.setApplicationVersion(__version__)
    record_startup(StartupEvent.SHELL_CREATED)  # T2：Qt 壳创建

    # 加载 Qt 标准对话框的中文翻译（颜色选择对话框等）。必须在创建对话框前安装。
    _install_qt_translations(app)

    # 单实例守卫：第二个实例启动时通知本实例提到前台后自身退出。
    # 必须在 QApplication 创建之后调用（QLocalServer 依赖 Qt 事件循环）。
    # socket 名固定为 "VibeOCR"（不绑版本），保证升级后新旧版本互认同一应用。
    from vibeocr.classic.utils.single_instance import SingleInstanceGuard

    single_instance_name, exclusive_mutex_name = _startup_lock_names()
    guard = SingleInstanceGuard(single_instance_name)
    if not guard.try_lock():
        # 已有实例在运行，本实例静默退出。
        return 0

    # 跨产品互斥：确保同一登录会话内 PySide Classic 与 WinUI Next 不同时运行。
    # 在同产品单实例通过后、任何后端/WorkerHost 初始化前获取；失败时提示退出，
    # 不启动第二个 WorkerHost。Mutex 由 OS 在前端崩溃时自动释放（ADR §6）。
    from vibeocr.classic.utils.frontend_exclusive_lock import FrontendExclusiveLock

    exclusive_lock = (
        FrontendExclusiveLock(name=exclusive_mutex_name)
        if exclusive_mutex_name is not None
        else FrontendExclusiveLock()
    )
    if not exclusive_lock.try_acquire():
        _show_another_product_running_dialog()
        return 1
    app.aboutToQuit.connect(exclusive_lock.release)

    # 注册退出清理：协作式取消残留的 InstallWorker 并 kill 其子进程。
    # 作为 closeEvent 的兜底——若用户在安装进行中直接退出应用（而非关闭对话框），
    # 避免留下孤儿 pip 子进程（main.py 末尾的 os._exit 不会回收它们）。
    def _cleanup_install_workers_on_quit() -> None:
        from vibeocr.classic.utils.dialog_workers import request_dialog_workers_shutdown

        request_dialog_workers_shutdown()

    app.aboutToQuit.connect(_cleanup_install_workers_on_quit)

    # 设置应用图标（必须在主窗口创建之前，窗口才能继承图标）
    _setup_app_icon(app)

    # 启动 splash 屏：在 MainWindow（~1.5s 构造）期间立即给用户视觉反馈，
    # 消除"双击 exe 后无反应"的观感。主窗口 show 后由 finish 衔接，无闪烁。
    splash = _create_splash(app)

    # 全局浅色主题 QSS 暂时禁用：实际观感不如 Qt 原生控件风格。
    # theme.py token 模块与各文件的 token 化迁移均保留，便于日后调整配色后重试。
    # from vibeocr.classic.ui import theme
    # app.setStyleSheet(theme.global_qss())

    # 初始化统一配置管理器
    project_root = env_manager.get_project_root()
    cm = ConfigManager.instance(project_root)

    # 初始化 OCR 偏好设置单例（必须在 UI 创建之前，否则所有选项读写均静默失败）
    from vibeocr.classic.utils.ocr_preferences import OCRPreferences

    OCRPreferences.instance(cm)

    # 加载应用设置
    app_settings = AppSettings(cm)

    # 创建 qasync 事件循环（整合 Qt 和 asyncio）
    loop = create_qasync_event_loop(app)

    window = MainWindow()
    window.set_app_settings(app_settings)
    window.show()
    record_startup(StartupEvent.FIRST_WINDOW)  # T3：首窗可见

    # Perf-gate smoke mode: process one paint turn, persist T3, then terminate
    # before tray/update/background startup work begins.  A delayed QTimer exit
    # raced constructor-started native threads on Windows hosted runners and
    # could report 0xC0000374 after the window had already reached T3.
    # Production runs never set this environment variable.
    if os.environ.get("VIBEOCR_SELF_TEST_SMOKE") in {"1", "t3"}:
        _finish_t3_smoke(app)

    # 主窗口已显示，splash 衔接关闭（finish 会等窗口完全绘制后再隐藏 splash，避免闪空）
    if splash is not None:
        splash.finish(window)
        splash.close()

    # 窗口已可见后延迟预热结果页 WebEngine：把 Chromium 冷启动成本
    # 从「首次截图显示结果时」前移到「启动空闲片段」，避免首次结果前的多次闪烁。
    from PySide6.QtCore import QTimer

    # 至少经过一次 Qt 事件循环和首帧绘制窗口后再提交更新事务。若主窗口在首次
    # paint/事件分发阶段崩溃，updater 收不到健康信号并会回滚。
    QTimer.singleShot(250, lambda: _publish_update_health(project_root))
    QTimer.singleShot(0, window.prewarm_result_webengine)

    # 第二实例通知提到前台时，恢复并激活主窗口。
    guard.raise_requested.connect(window.bring_to_front)

    # 打包环境下延迟检查更新
    if getattr(sys, "frozen", False):
        from vibeocr.classic.pyside.update import UpdateService

        async def _check_update():
            import logging

            log = logging.getLogger(__name__)
            try:
                service = UpdateService(
                    project_root,
                    status_callback=window.statusBar().showMessage,  # noqa: F821
                )
                # manual=False：启动自动检查。命中「稍后提醒」暂缓窗口则静默跳过，
                # 不弹窗（用户在 UpdateDialog 点「稍后提醒」后 1 天内）。
                await service.check_and_prompt(window, manual=False)  # noqa: F821
            except Exception:
                # ensure_future 会静默吞掉协程异常，这里必须显式捕获，
                # 否则"检查更新失败"对用户和开发者都不可见。
                log.exception("启动检查更新失败")

        # timer 与 coroutine 都成为 MainWindow 关闭状态机的显式 owner。关闭边界
        # 会取消未触发 timer、协作取消下载，再由 AsyncTaskRunner + tracked native
        # probe 等待真实终态，避免 asyncio.to_thread 取消后仍访问已销毁窗口。
        window._startup_update_task = DelayedAsyncTask(
            loop,
            5,
            _check_update,
            should_start=lambda: not window._closing,  # noqa: F821
            request_cancel=UpdateService.request_cancel,
        )

    # 创建系统托盘图标
    tray = _create_tray_icon(app, window, app_settings)
    if tray:
        window.set_tray_icon(tray)
        # 仅在启用最小化到托盘时阻止关闭窗口退出程序
        app.setQuitOnLastWindowClosed(not app_settings.minimize_to_tray)

    # 使用 qasync 事件循环运行应用
    try:
        loop.run_forever()
    except Exception as e:
        print(f"[VibeOCR] 应用异常退出: {e}")
        return 1

    # 事件循环退出后，显式清理原生资源，避免解释器关闭阶段 DLL 卸载崩溃 (0xC0000409)
    try:
        app.processEvents()
        import gc

        gc.collect()
        del window
        del app
        gc.collect()
    except Exception:
        pass

    os._exit(0)


def main() -> int:
    """应用程序入口点

    启动流程：
    1. 检测生产环境依赖（PySide6, Pillow）
    2. 失败 → 控制台错误提示，退出
    3. 通过 → 启动后台清理线程（tmp/zip/sha/暂存 updater/ready + *.exe.old）
    4. 启动GUI
    5. GUI启动后 → 异步检测嵌入式OCR依赖
    """

    # 1. 检查生产环境依赖
    if not check_production_dependencies():
        # PyInstaller 的 windowed 构建没有控制台，sys.stdin 会是 None。
        # 仅在标准输入可用时保留开发态的“按回车退出”提示。
        if sys.stdin is not None:
            try:
                input("\n按回车键退出...")
            except (EOFError, OSError, RuntimeError, ValueError):
                pass
        return 1

    # 清理上次更新残留（后台 daemon 线程，不阻塞启动）。
    # 新架构：updater 不再做 cleanup，移交本主程序后台完成。包含：
    # - tmp/zip/sha256/暂存 updater/ready（_cleanup_update_artifacts）
    # - *.exe.old（_cleanup_leftover_old_exes）
    # 用 daemon 线程让出资源，避免抢 UI 冷启动；launch_application 以 os._exit
    # 结尾从不返回，故线程必须在其之前启动（与 Qt 事件循环并行后台跑）。
    import threading

    def _background_cleanup() -> None:
        try:
            app_dir = env_manager.get_project_root()
            _cleanup_update_artifacts(app_dir)
            _cleanup_leftover_old_exes()
        except Exception as e:
            print(f"[VibeOCR] 后台清理异常（不影响启动）: {e}")

    cleanup_thread = threading.Thread(target=_background_cleanup, daemon=True)
    cleanup_thread.start()

    # 2. 启动应用
    print("[VibeOCR] 启动应用...")
    return launch_application()


if __name__ == "__main__":
    sys.exit(main())
