# Changelog

## [0.6.3] - 2026-07-28

### Fixed
- fix(ci): stop setup-python-buildshell from clobbering the release tag checkout

### Dependencies
- 升级:
  - 升级 vibeocr-contracts-py ==0.6.1 → ==0.6.3
  - 升级 vibeocr-client-py ==0.6.1 → ==0.6.3
  - 升级 vibeocr-backend ==0.6.1 → ==0.6.3
  - 升级 vibeocr-pyside ==0.6.1 → ==0.6.3

## [0.6.2] - 2026-07-28

### Fixed
- fix(screenshot): hide taskbar during region-pick overlay
- fix(screenshot): add right-click reset/cancel and too-small feedback
- fix(shell): honor forwarded --goto and stop hotkey window flicker
- fix(export): disable 导出当前 button when no result
- fix(batch): prevent frozen Start button when submit_recognition throws
- fix(single-tab): surface feedback for silent copy-image/paste/missing-file
- fix(copy): give feedback when result-area copy silently drops
- fix(env): raise paddleocr import-check timeout 30s -> 60s
- fix(update): stop ResponseNotRead aborting update downloads on non-200
- fix(update): persist remind-later window & immediate cancel-to-idle
- fix(release): build checkout uses github.ref on tag-push path (avoid double-v)

### Changed
- test: stop leaked background threads to harden Qt test stability
- test(update): patch update.py.check_for_updates binding in _mock_has_update
- ci(coverage): lower fail_under 81 -> 77 to match CI's real measurement
- test(update): mock _probe_github_reachable in all-sources-fail test
- lint: clear ruff backlog across test suite (140 violations)
- test(table-contract): align gate config assertions with current release.yml
- ci: checkout before local composite action in all Python jobs
- chore: remove dead ClipboardController and unused ChatWidget
- test(coverage): add pipeline_cache_manager branch coverage tests
- test(coverage): push pyside shutdown_jobs to 100%
- test(coverage): push pyside app_settings to 99% (Qt-free logic)
- test(coverage): push settings_runtime to 100%, concurrency_budget to 100%
- test(coverage): cover budgets.py negative device_vram_mb validation
- test(coverage): push concurrency_budget to 100%
- chore(coverage): raise fail_under baseline 80 → 81
- test(coverage): cover supervisor/process _drain_stream log parsing
- test(coverage): push tables/reducer to 85% (was 82%)
- test(coverage): cover pipeline_table pure helpers (_point_in_box/_parse_cell_box/_normalize_match_text)
- test(coverage): push pipeline_pp_structure to 87% (was 71%)
- test(coverage): push pipeline_paddlocr_vl to 83% (was 74%)
- test(coverage): push pipeline_ocr to 90% (was 65%)
- test(coverage): push env_config to 97% (was 46%)
- test(coverage): push ocr_service to 57% (was 40%) via synthetic output parsing
- chore(coverage): raise fail_under baseline 70 → 80
- test(coverage): push machine_cache to 98%
- test(coverage): push pdf_ocr_orchestrator to 89%
- test(coverage): push pipeline_cache_manager to ~88%
- test(pdf_backend_client): fix 3 failing coverage tests
- test(mineru): fix hanging startup_timeout test
- test(update_service): fix hanging test by pre-setting cancel_event
- test(coverage): push tables/reducer to 82% (cancellation + error paths)
- test(coverage): push ocr_service_base to 97%
- test(coverage): push http_log to 99%
- test(coverage): push qrcode_decode to 100%, qrcode_service to 98%
- test(coverage): push batch_budget+app_paths to 100%, subprocess_log to 99%
- test(coverage): push gpu_memory_monitor to 98%
- test(coverage): push pipeline_formula to 92% (was 77%)
- test(coverage): push client-py coverage up (env_manager/build_manifest/machine_cache/network_detector/supervisor clients)
- test(coverage): push backend/services coverage up (mineru/pdf/pdf_backend_process/update)
- test(coverage): push backend/supervisor/** to 100% line coverage
- test(coverage): push pipeline_status to 100%, logging_context/constants to 98%
- test(coverage): push ocr_facade to 100%
- test(coverage): push dependency_bootstrap to 100%
- test(coverage): push services/__init__ + client/ to 100%
- test(coverage): push pdf_coords+text_layout to 100%
- test(coverage): push indent_processor+markdown_converter to ~100%
- test(coverage): push job_object to 100%
- test(coverage): push cjk_font_resolver to 100%
- test(coverage): push ocr_sidecar to 100%
- test(coverage): push cpu_info to 99% (line 100%)
- test(coverage): push mime_types+system_memory to 100%
- test(coverage): push client models+migration+tables to 100%
- test(contracts): push vibeocr-contracts-py to 100% coverage
- test(update): cover _attempt_or_cancel branches & save_remind_later corrupt path
- docs(release): update stale header comment to self-contained trigger model
- docs: update release workflow references in README
- ci: remove scheduled-release.yml and ttl-diagnostics.yml (consolidated into release.yml)
- ci(release): use setup + wheels composite actions in build job
- ci(release): build job depends on quality-gates, drop 6 duplicate test steps
- ci(release): add prepare-release job (migrated from scheduled-release)
- ci(release): add quality-gates job reusing ci.yml
- ci(release): add detect job (migrated from scheduled-release)
- ci(release): merge triggers + concurrency from scheduled-release
- ci: add parity job (migrated from release)
- ci: use build-workspace-wheels composite in backend job
- ci: use setup-python-buildshell composite in 5 jobs
- ci: add build-workspace-wheels composite action
- ci: add setup-python-buildshell composite action
- docs(plan): ci/release workflow consolidation implementation plan
- docs(spec): ci/release workflow consolidation design
- ci: automate scheduled and manual releases

### Dependencies
- 升级:
  - 升级 vibeocr-contracts-py ==0.6.0 → ==0.6.2
  - 升级 vibeocr-client-py ==0.6.0 → ==0.6.2
  - 升级 vibeocr-backend ==0.6.0 → ==0.6.2
  - 升级 vibeocr-pyside ==0.6.0 → ==0.6.2

## [0.6.1] - 2026-07-27

### Added
- feat(tables): add canonical table contract
- feat(ui): structure runtime and PDF status feedback

### Fixed
- fix(export): preserve merged table semantics
- fix(ocr): normalize structured table pipelines
- fix(supervisor): expose bundled modules to portable Python
- fix(ci): enforce the coverage quality gate
- fix(logging): handle streaming HTTP response hooks
- fix(platform): await supervisor process termination
- fix(ci): install coverage and multipart test dependencies

### Changed
- ci: remove stale TTL diagnostic paths
- ci(tables): gate table contract artifacts
- test(release): verify frozen supervisor startup
- test(ci): stabilize responsiveness coverage gate
- docs(changelog): document v0.6.0 rebuild fixes
- perf(ocr): raise batch pixel budget to 64MP
- docs(changelog): clarify v0.6.0 user-facing changes


## [0.6.0] - 2026-07-27

### Added
- feat(ui): 状态栏固定展示服务、管道驻留、当前任务与最近结果，识别结果补充文本框、低置信数量和耗时
- feat(supervisor): complete job interface migration and diagnostics
- feat(logging): HTTP 日志增加中文状态说明、耗时、请求/响应数据量与敏感查询参数脱敏
- feat(inference-supervisor): 完成 Phase 8 迁移 — 删除 legacy、v2 为两端默认路径
- feat(pdf): PDF 会话迁移到 supervisor HTTP v2，GUI 不再直连 PdfBackendClient
- feat: unified inference supervisor rewrite (Phase 0-10)
- feat(pyside): 适配 OCR HTTP worker，移除 PDF IPC worker 依赖
- feat(client): 以 OcrHttpClient 替换 PDF 客户端模块
- feat(worker_host): 新增 OCR HTTP worker，替代 SHM/PDF IPC 通信

### Fixed
- fix(supervisor): 打包态为便携 Python 注入 `_internal` 源码路径，修复 Supervisor 无 ready envelope、OCR 服务无法启动
- fix(pdf): 文字层删除及批量变更完成后从权威模型同步格子状态，避免旋转、插页、删页、重排等终态不刷新
- fix(logging): Supervisor/PDF HTTP 响应日志兼容未消费的流式响应，避免 response hook 抛出 StreamError
- fix(pdf): 删除文字层返回空页索引时安全刷新，避免 `None` 参与页码比较
- fix(ocr_service_subprocess): set_pipeline_ttls 改为尽力而为，锁占用时快速失败
- fix(ocr_service): paddle 自带 CUDA runtime 时不再混入 torch/lib 避免 DLL 冲突
- fix(mineru): 移除模型预探测，修复 lang_list 空串导致 Language not supported
- fix(mineru): 模型探测/下载命令补 -m all，消除交互 prompt 误报

### Changed
- perf(ocr): 单批解码像素预算从 48MP 提升到 64MP，A4 300 DPI 页面通常可按 7+7+2 传输
- test: 补 worker_host/client/pdf_backend 低覆盖区测试（第七批）
- test: 补 html_tables/cpu_info 纯函数测试（第六批）
- test: 补 ocr_service staticmethod 纯逻辑测试（第五批）
- test: 补 pipeline_ocr/pp_structure 数据解析纯函数测试（第四批）
- test: 补 env_manager/ocr_worker/main 低覆盖区测试（第三批）
- test(env_manager): 补全 detect_cuda_version 分支覆盖
- test(mineru): 补全文本提取分支测试，fail_under 67→68
- ci(coverage): 新增 Python 测试覆盖率门禁（fail_under=67）

### Dependencies
- 升级:
  - 升级 vibeocr-contracts-py ==0.5.5 → ==0.6.0
  - 升级 vibeocr-client-py ==0.5.5 → ==0.6.0
  - 升级 vibeocr-backend ==0.5.5 → ==0.6.0
  - 升级 vibeocr-pyside ==0.5.5 → ==0.6.0

## [0.5.6] - 2026-07-23

### Fixed
- fix(cache): honor persistent and finite model TTL semantics (#1)

### Dependencies
- 升级:
  - 升级 vibeocr-contracts-py ==0.5.4 → ==0.5.6
  - 升级 vibeocr-client-py ==0.5.4 → ==0.5.6
  - 升级 vibeocr-backend ==0.5.4 → ==0.5.6
  - 升级 vibeocr-pyside ==0.5.4 → ==0.5.6

## [0.5.5] - 2026-07-22

### Added
- feat(log): 文件日志改为人可读文本格式

### Fixed
- fix(log): match asctime milliseconds in worker log forwarding
- fix(worker): execute_control lock_timeout 防止阻塞后续 OCR
- fix(cache): preserve pipeline_success + clear status label + fallback timeout
- fix(updater): 保留 logs 目录并增大文件释放余量


## [0.5.4] - 2026-07-21

### Added
- feat(ui): per-pipeline TTL ComboBox + split cache/pipeline status labels
- feat(config): per-pipeline TTL dict + legacy migration
- feat(contracts): add cache_kind metadata + paddle/mineru partition

### Fixed
- fix(about): scroll CHANGELOG to top after setMarkdown
- fix(worker): cache_status/set_ttls/release 绕过 worker busy 等待
- fix(cache): 读取驻留状态按钮的超时/反馈/文案
- fix(cache): pass ttls at cache_manager construction + adapt tests to per-pipeline TTL API
- fix(cache): misleading copy + real refresh_cache detection + machine_id warmup
- fix(config): repair preload_pipelines caller + reject bool legacy TTL value

### Changed
- chore: remove obsolete MinerU output and .ui exclusion rules
- diag(cache): add TTL watcher observability logs
- diag(ui): add TTL Combos creation logging for missing-ComboBox debugging
- refactor(protocol): upgrade TTL payload to per-pipeline dict (ttl_seconds -> pipeline_ttls)
- refactor(worker): drop lazy evict_idle, add shutdown, accept dict TTL payload
- test(cache): background tick thread wake/shutdown behavior
- refactor(cache): per-pipeline TTL + background tick thread + mineru cache_kind split
- docs(spec): correct TTL upgrade point list (preload has no ttl; settings.snapshot does; add composition.py)
- docs(plan): pipeline cache TTL redesign implementation plan
- docs(spec): add gate compliance section to TTL redesign
- docs(spec): pipeline cache TTL redesign + bug fixes


## [0.5.3] - 2026-07-21

### Fixed
- fix(ci): isolate table dependency tests from PaddleX
- fix(ci): make PDF render concurrency test deterministic

### Dependencies
- 升级:
  - 升级 vibeocr-contracts-py ==0.5.1 → ==0.5.3
  - 升级 vibeocr-client-py ==0.5.1 → ==0.5.3
  - 升级 vibeocr-backend ==0.5.1 → ==0.5.3
  - 升级 vibeocr-pyside ==0.5.1 → ==0.5.3

## [0.5.2] - 2026-07-21

### Fixed
- fix: harden updates and oneDNN fallback
- fix(release): make frozen startup smoke deterministic
- fix(pyside): route log settings through runtime boundary

### Changed
- refactor(pyside): eliminate UI thread blocking


## [0.5.1] - 2026-07-20

### Fixed
- fix(pyside): enforce UTF-8 standard streams
- fix(release): force UTF-8 startup smoke
- fix(release): avoid inherited smoke pipes
- fix(release): freeze merged Classic workspace
- fix(update): restore Classic relaunch after upgrade
- fix(release): validate physical wheel manifests

### Changed
- Improve OCR processing and update application workflows


## [0.5.0] - 2026-07-19

### Fixed
- fix(ocr): 截图识别路径异步化，消除 GUI 阻塞与重入/关闭竞态
- fix(release): 适配物理拆包后的 WinUI 三-wheel 发布清单验证与测试夹具
- fix(update): Classic 更新完成后回退启动 `VibeOCR.exe`，不再误报缺少 WinUI Bootstrapper
- fix(release): 合并 workspace namespace 后冻结 Classic，并执行真实 EXE 启动门禁

### Changed
- docs: record workspace split integration
- refactor: harden runtime and physically split workspace packages



## [0.4.37] - 2026-07-18

### Fixed
- fix(worker_host): 打包态用嵌入式 Python 启动 WorkerHost，避免 GUI 递归

### Changed
- refactor(pdf): PDF 后端下沉独立子进程 + 修 QThread 生命周期崩溃

## [0.4.36] - 2026-07-17

### Fixed
- fix(update): 拆分 update_service 的 Qt 表现层，修复架构门禁与 C408

### Changed
- docs(readme): 版本徽章改用动态 shields，打包脚本说明 Version 缺省读 pyproject

## [0.4.35] - 2026-07-17

### Fixed
- fix(update): 修复 WinUI 侧 owner 拼错与双产物崩溃
- fix(update): 修复 Classic 侧 v0.4.29+ 更新全挂——硬编码文件名改为从 release API 动态获取

## [0.4.34] - 2026-07-17

### Fixed
- fix(release): 恢复 Classic(PyInstaller) 发版路径，修通 v0.4.29+ 全挂

## [0.4.33] - 2026-07-17

### Fixed
- fix(test): main_window fixture 用 isValid 守卫 close，消除 teardown 竞态
- fix(release): 用 .NET SHA256 替换 Get-FileHash，修 PS 5.1 cmdlet 加载失败

### Changed
- test(pdf): 并行渲染提速断言阈值 1.05→1.0，抗 CI 共享 runner 噪声

## [0.4.32] - 2026-07-17

### Fixed
- fix(contracts): 补全 ocr.recognize_batch 的 .NET DTO + JSON 注册

### Changed
- test(pdf): 并行渲染提速断言改中位数采样 + 阈值降至 1.05 抗 CI 抖动
- ci: winui job 改测 Contracts+Platform 契约层 + 给所有 job 加 timeout-minutes

## [0.4.31] - 2026-07-16

### Fixed
- fix(release): sync workspace versions and dedupe CHANGELOG archive

### Dependencies
- 新增:
  - 新增 pynvml>=11.5.0

## [0.4.30] - 2026-07-16

### Added
- feat: optimize PDF OCR pipeline and improve about view

### Fixed
- fix(ci): stabilize Windows quality gates

### Changed
- build(deps): standardize NuGet lock updates

## [0.4.29] - 2026-07-16

### Added
- feat(preview): 滚轮缩放 + 多边形覆盖层 + 滚动漂移修复
- feat(pdf): 用 OCR 检测多边形方向替换 bbox 长宽比启发式判竖排
- feat: WinUI screenshot region picker + export table fallback
- feat(winui): dev profile launches WorkerHost from repo .venv
- feat: complete dual frontend WorkerHost migration
- feat(phase3): migrate single-image OCR execution to RPC
- feat(phase3): migrate PySide QR generate/decode to RPC (first vertical slice)
- feat(phase2): high-level Python BackendClient for WorkerHost RPC
- feat(phase2): generalize WorkerHost — frontend_id + production profile
- feat(phase1): cross-product exclusive Mutex for PySide/WinUI mutual exclusion
- feat(winui): add WindowLayoutStore for window geometry persistence
- feat(protocol): settings.switch_backend + install_dependency (parity --require-pass green)
- feat(update): safe WinUI cutover sequence (Phase 5.4)
- feat(migration): idempotent config migrator (Phase 5.1)
- feat(winui): add tray/hotkey/about/update shell (Task 4.5)
- feat(winui): add settings/backend tab (Task 4.4)
- feat(winui): add C# PDF tab (Task 4.3 part 4)
- feat(pdf): wire WorkerHost handlers for PDF session methods (Task 4.3 part 3)
- feat(protocol): extend PDF RPC methods (Task 4.3 part 2)
- feat(pdf): extract UI-free OCR orchestrator (Task 4.3 part 1)
- feat(winui): reach QR code parity
- feat(winui): reach batch recognition parity
- feat(winui): migrate preview editor and result rendering
- feat(winui): host secure WebView2 preview bridge
- feat(winui): implement cancellable single-image OCR
- feat(winui): validate required Windows desktop capabilities
- feat(winui): add side-by-side shell and diagnostics
- feat(winui): bootstrap framework and portable prerequisites
- feat(winui): connect to Python WorkerHost
- feat(protocol): share golden contracts with C sharp

### Fixed
- fix(pdf): 单字符文字层走主路径，修复单数字区域过小
- fix(deps): declare pynvml — the one undeclared backend import
- fix(contracts): sync C# DTOs with golden after dual-frontend migration
- fix(build): unblock local WinUI build — SDK 10.0.302 + PS 5.1 compat
- fix(winui): default 900x600 window + min size + geometry persistence
- fix(winui): declare PerMonitorV2 DPI awareness via app.manifest
- fix(winui): harden migration cutover and release gates

### Changed
- docs+fix(cpu): 澄清 oneDNN 控制路径——_decide_enable_mkldnn 是唯一真相
- docs(readme): 说明两条前端路线与开发状态，标注 WinUI Next 基本不可用
- ci(release): 用环境变量控制生成哪些安装包
- refactor(deps): single source of truth for backend wheel METADATA
- docs: record dual UI implementation completion
- docs: update README migration progress (allowlist 90→53)
- arch(phase4): treat models/ as shared DTO layer (not backend)
- docs: update progress — Phase 3 OCR execution migrated, allowlist 90→74
- docs: document dual-frontend architecture and migration progress in README
- refactor(phase3): move toolbar_icons from core/ to ui/ layer
- docs: update progress — Phase 3 allowlist 90→84 (QR + display formatters)
- refactor(phase3): move HTML table utilities to UI utils layer
- refactor(phase3): move TextBlockProcessor to UI utils layer
- arch(phase0): freeze dual-frontend boundary with architecture guards
- refactor(winui): drop orphan OnHideToTrayClicked; fix min-size enforcer doc (physical px)
- chore(winui): layout parity + DPI fix changelog
- refactor(winui): about page two-column cards, move shell options out
- refactor(winui): settings page grouped cards + migrate shell options
- refactor(winui): pdf page two-column layout (PySide6 parity)
- refactor(winui): qr code page two-column + pivot layout (PySide6 parity)
- refactor(winui): batch page three-column layout (PySide6 parity)
- refactor(winui): recognition page two-column layout (PySide6 parity)
- plan(winui): layout PySide6 parity + DPI fix implementation plan
- docs(winui): layout PySide6 parity + DPI fix design
- docs(release): real-env sign-off runbook + soak harness (Phase 5.5)
- test(settings): real GPU backend-switch integration (Phase 5.5 sign-off)
- perf(winui): real perf-gate data + manual sign-off checklist (Phase 5.5)
- release: Phase 5 WinUI cutover candidate checklist + changelog (Phase 5.5)
- perf(winui): release metrics comparison gate (Phase 5.3)
- build(winui): framework-dependent release layout (Phase 5.2)
- test(winui): enforce feature parity matrix (Task 4.6)
- docs(winui): mark PDF row PASS in parity matrix (Task 4.3 complete)
- test(winui): add PDF E2E spec (Task 4.3 part 5)
- docs: record WinUI migration handoff
- test(winui): close single-recognition parity loop
- docs(winui): align PDF migration with durable OCR flow
- build(winui): scaffold framework-dependent solution

### Dependencies
- 新增:
  - 新增 pynvml>=11.5.0

### Fixed — 布局对齐与 DPI 修复
- fix(winui): 声明 PerMonitorV2 DPI 感知（app.manifest），修复高 DPI 显示器界面发虚模糊
- fix(winui): 主窗口默认 900×600 + 最小尺寸（720×480）+ 记忆窗口几何（winui-layout.json）
- refactor(winui): 各功能页布局参照 PySide6 重新对齐（单图两栏 / 批量三栏 / 二维码两栏+Pivot / PDF 两栏 / 设置分组卡片 / 关于双栏卡片）
- refactor(winui): 热键、开机启动、隐藏到托盘选项从关于页迁移到设置页"应用设置"分组
- feat(winui): WindowLayoutStore 窗口几何持久化（4 单测）；占位控件统一灰色禁用 + "功能开发中"

### Added — Phase 4 完整功能对等
- feat(winui): 批量识别对等（BatchViewModel/Commands/Page + 14 测试 + E2E）
- feat(winui): 二维码/条码对等（QrCodeViewModel + is_url 跨语言契约 + 16 测试 + E2E）
- feat(pdf): UI-free OCR orchestrator（逐批 save+sidecar 续传、页边界取消、末尾压缩、写层错误聚合）
- feat(protocol): 8 个 PDF RPC 方法（close/render/rotate/delete/add_text_layer/delete_text_layers/save/start_ocr）
- feat(winui): PDF 标签页（PdfViewModel + 12 测试 + E2E；按钮语义以 main 为准）
- feat(winui): 设置/后端标签页（SettingsViewModel + GPU 检测 + 切换不自动重试 + 7 测试）
- feat(winui): 托盘/快捷键/关于/更新（ShellViewModel + UpdateViewModel + 9 测试）
- test(winui): 功能对等矩阵冻结（validate_matrix.py + schema + CI）

### Added — Phase 5 切换基础
- feat(migration): 幂等配置 migrator（schema_version + 哈希备份 + 原子替换；Python 9 测试 + C# 5 测试）
- build(winui): framework-dependent unpackaged 发布布局（build/verify 脚本 + 5 布局测试）
- perf(winui): 发布指标对比门禁（compare_release_metrics.py + 10 测试 + 基线文档）
- feat(update): 安全切换序列（verify→stop→replace→migrate→prereq→health→launch；失败只进修复页；12 测试）
- docs(release): WinUI 切换检查清单 + 真实环境签核 runbook + soak harness

### Fixed
- fix(winui): BatchViewModel.CancelAll 现在也取消 Pending 项（对齐 Python 批量队列）
- fix(winui): PdfViewModel OpenPathAsync/StartOcrAsync 重复递增 generation 导致结果被丢弃
- fix(cpu): oneDNN 决策澄清——`_decide_enable_mkldnn`（enable_mkldnn kwarg）为唯一真相；查明 `FLAGS_use_mkldnn`/`FLAGS_enable_onednn_backend` 对 PaddleOCR 推理路径（AnalysisConfig）不生效，paddleocr/paddlex 零处读取。`_reset()` 现清空 `_onednn_safe_cache`（修测试隔离泄漏）；registry 测试改 mock 探测、补 True 分支

## [0.4.28] - 2026-07-13

### Fixed
- fix(pdf): 文字层添加/删除/旋转/摆正四大问题修复

## [0.4.27] - 2026-07-13

### Fixed
- fix(build): 清理 _internal 下 __pycache__ 修复 CI 打包失败

## [0.4.26] - 2026-07-13

### Added
- feat(ui): 打开 PDF 时检测未完成 sidecar 并提示续传
- feat(ui): OCR 进行中格子 processing 态 + 预览自动刷新
- feat(ui): 文字层格子四态(processing/done/failed/none) + 失败感叹号
- feat(manager): start_ocr 按 sidecar 续传过滤已落盘页
- feat(manager): _run_ocr 逐批增量落盘 + sidecar + 末尾聚合压缩
- feat(client): add_text_layer_batch 加 save 参数透传后端
- feat(backend): add_text_layer_batch 路由支持 save 增量落盘
- feat(ipc): BatchAddTextLayerRequest 加 save 字段 + ProgressPhase.COMPRESS
- feat(pdf): 新增 save_incremental 增量落盘方法
- feat(sidecar): 新增 OCR 断点续传 sidecar 读写模块

### Fixed
- fix(pdf-ocr): emit ocr_done on all-saved short-circuit to reset UI
- fix(test): 恢复 TestThumbnailIncrementalUpdate 类声明（修复 Task 7 误删）
- fix(sidecar): mark_completed 保留已有 pages 不因校验失败而丢失
- fix(sidecar): path-keyed sidecar + growth validation, fixes incremental-save fingerprint drift
- fix(manager): _on_ocr_page_done_signal 增量落 model 消除预览滞后
- fix(pdf): 文字层用真实字形宽度替代硬编码启发式——数字/符号位置错位与 bbox 异常
- fix(pdf,update): 修复 PDF 大文件 OCR 结束崩溃与更新下载后卡死
- fix(worker): complete phase 1 worker host

### Changed
- docs(pdf-ocr): clarify sidecar not invalidated by in-memory edits
- test(ui): 覆盖文字层格子四态着色 + state-role 写入 + 续传提示
- docs(plan): PDF OCR 逐批增量落盘+断点续传+UI 进度细化 实现计划
- docs(spec): PDF OCR 逐批增量落盘+断点续传+UI 进度细化 设计

## [0.4.25] - 2026-07-12

### Added
- feat(worker): expose application services through WorkerHost
- feat(worker): add cancellable RPC task lifecycle
- feat(worker): transfer large payloads through shared memory
- feat(worker): secure control channel with named pipes
- feat(worker): implement protocol framing and DTOs
- feat(protocol): define version 1 worker contracts

### Fixed
- fix(worker): complete Phase 1 RPC loop, token authentication, deadlines, cancellation, and resource draining
- fix(worker): wire UI-free production services and enforce closed runtime method payloads
- fix(pdf): 预览已有文字层用 line 级 bbox——不相邻文本不再合并到一个框
- fix(pdf): 预览已有文字层补偿页面旋转——位置/角度不再错
- fix(pdf): 文字层 ink 宽度匹配 OCR bbox——morph 水平缩放
- fix(pdf): rotation=90 横向页文字层 ink 匹配 bbox——不再区域太小
- fix(pdf): 文字层 ink 区域匹配 OCR bbox——不再区域太小
- fix(ocr,pdf): 修复 Worker 预加载死锁与 PDF 添加文字层取消异常
- fix(pdf): 文字层补偿 CropBox 原点偏移——不再离文字很远
- fix(pdf): 文字层字号策略修正——写入位置/大小不再偏离
- fix(pdf): 修复文字层坐标偏离、缩略图不自动加载、插页后缩略图不刷新
- fix(worker): address Phase 1 review findings
- fix: clear pyright errors in worker_host tests

### Changed
- test(worker): add repeatable Python/C# Phase 1 contract gate
- test: 放宽渲染并行加速比阈值至 1.15x
- perf(batch): 批量识别 Tab 改用分小批 recognize_batch
- perf(pdf): 批量写文字层共享聚合子集字体
- perf(pdf): 渲染并行化——独立 fitz.Document 并发栅格化
- perf(pdf): runner 生命周期内复用渲染线程池与 httpx 连接
- refactor: 清理死代码并让 OCR batch_size 跟随显存动态估算

## [0.4.24] - 2026-07-12

### Added
- feat(ci): 发版成功后自动清理历史 GitHub Release，保留最近 5 个（仅删 Release 及资产，
  不删 git tag；按 semver 数值排序，单步失败仅告警不阻断发版）

### Fixed
- fix(ci): 修复 CI 打包报 `ModuleNotFoundError: No module named 'vibeocr'`——
  bump_version.py 启动时注入 src/ 到 sys.path（CI 仅装壳依赖，不安装 vibeocr 包）

## [0.4.23] - 2026-07-12

### Fixed
- fix(pdf): 保留缩略图 worker 所有权 + shutdown 防迟到信号
- fix: clear all pyright errors and tighten Phase 0 gate
- fix: drain PDF thumbnail workers cooperatively
- fix: cancel owned preload tasks during shutdown

### Changed
- refactor: expose UI-free application facades
- refactor: centralize portable application paths
- perf: add trustworthy startup milestones
- build: make portable artifacts reproducible
- test: define phase 0 quality gate
- style: clear 314 pre-existing ruff errors (lint baseline)
- chore: ignore .worktrees for isolated workspaces
- docs: add formal WinUI 3 migration plan
- docs: define WinUI 3 migration architecture

## [0.4.22] - 2026-07-11

### Added
- feat(update): 下载进度移至状态栏 + 修复 SHA/校验阶段取消失效

### Fixed
- fix(concurrency): Ruff 修复 — shutdown_coordinator B023 闭包变量绑定 + switch_dialog TC003 noqa
- fix(concurrency): 集中 ConcurrencyBudget 配置，启动时记录实际预算
- fix(concurrency): ShutdownCoordinator 统一有序 drain，os._exit 前尽力收拢任务
- fix(concurrency): AsyncTaskRunner 补 drain + on_error + async_slot 错误观测 + await_dialog 防护
- fix(concurrency): qasync 缺失时 fail-fast，不再返回不可用的标准 loop
- fix(concurrency): SHM interrupt 契约真正生效，退避循环检查 _stop_event
- fix(concurrency): PDF session close 获取 fitz_lock + CLOSING 状态等待 active op
- fix(concurrency): export cancel 真正生效，逐文件检查 cancel flag
- fix(concurrency): PDF runner 引入 task generation，旧任务迟到信号不污染新任务
- fix(concurrency): stop() 优雅关闭顺序（SHUTDOWN→wait→kill→清SHM→join reader）
- fix(concurrency): SwitchWorker 改用协作式取消，移除 QThread.terminate
- fix(concurrency): force_restart 与 restart_if_dead 语义分离
- fix(concurrency): worker 领取与 BUSY 标记原子化（_reserve_worker/_release_worker）
- fix(concurrency): worker 端后台轮询 cancel flag 并调用 mgr.cancel()
- fix(concurrency): 批量取消走独立 SHM cancel flag，不再阻塞 GUI
- fix(concurrency): SHM 头部新增 cancel 标志字节作为独立控制通道

### Changed
- test(concurrency): 批量取消 100ms 内返回的回归测试

## [0.4.21] - 2026-07-10

### Fixed
- fix(pdf): 摆正进度/提速复用 + 文字层旋转90° + 体积膨胀
- fix(result_view): 文本块处理选项在结果区可见且保留逐块可编辑性
- fix(update,settings): 更新日志精简展示 + 关闭时清理后台 GPU 检测

## [0.4.20] - 2026-07-09

### Added
- feat(window): 关于页移到标签栏末尾
- feat(about): 关于页改用左右两栏布局

### Fixed
- fix(result_view): 内置 qwebchannel.js 并修正文本块外边距
- fix(update): 模态对话框改用 await_dialog 非阻塞 await

## [0.4.19] - 2026-07-09

### Fixed
- fix(window): OCR 完成后重新把主窗口提到前台
- fix(env_manager): 依赖更新检测仅便携模式生效
- fix(update): 便携环境追踪 markdown + 检出 uv.lock 锁定版升级
- fix(update): 下载后 testzip/抽取 updater 经 asyncio.to_thread 派发，避免冻结事件循环
- fix(table): 回填 PaddleX IoU 失配漏掉的字，不再丢弃表内未吸收文本

### Changed
- chore(deps): 升级依赖版本下界与 uv.lock 锁定版
- refactor(preview): 移除画布表格网格编辑器，表格改在右侧结果视图编辑
- refactor(ocr_service): 修复 markdown 表格 <br>/实体丢失，移除未用的 grid 工具

### Dependencies
- 升级:
  - 升级 pillow >=12.2.0 → >=12.3.0
  - 升级 mineru >=3.4.0 → >=3.4.3
  - 升级 pymupdf >=1.27.2.3 → >=1.28.0
  - 升级 fonttools >=4.61.1 → >=4.63.0
  - 升级 python-docx >=1.1.0 → >=1.2.0
  - 升级 openpyxl >=3.1.0 → >=3.1.5
  - 升级 fastapi >=0.115.0 → >=0.139.0
  - 升级 uvicorn >=0.34.0 → >=0.51.0
  - 升级 pydantic >=2.11.0 → >=2.13.4

## [0.4.18] - 2026-07-09

### Added
- feat(update): 旧主程序新增 testzip + 抽取新 updater
- feat(update): updater 自动判断新旧路径并组装 self_exe_names
- feat(update): 新增 updater 路径自动判断（新架构基础设施）
- feat(pdf): load_done 触发缩略图渲染；文字行高度 28→18 收紧
- feat(pdf): 缩略图打开时先检测文字层，检测期间显示提示占位图

### Fixed
- fix(build): VibeOCR.exe 文件说明改为 VibeOCR
- fix(pdf): 结构性变更后缩略图不误进检测态（C1 回归）+ 检测中图标断言修正
- fix(pdf): 统一后端 fitz 调用加 fitz_lock，消除并发崩溃隐患

### Changed
- chore: gitignore 忽略 .zcode/ 工作区目录
- docs(update): final-review 清理 stale self-update 注释 + _backup 防御性清理
- docs(update): 清理死代码 + stale 注释（新架构收尾）
- refactor(update): 删除 self-update 子系统 + 新增后台清理线程
- refactor(update): 编排器走新架构 + 删除 self-update 兜底
- docs(update): 修正失败路径注释（成功路径已不做 cleanup）[minor]
- refactor(update): 成功路径移除 cleanup + verify_zip，清理移交新主程序
- test(pdf): 补缩略图与 load 并发的后端安全集成测试
- wip: 保存未提交的在途工作（依赖锁定版本检测、SHM 调优、启动闪屏、PDF OCR 编排等）

## [0.4.17] - 2026-07-08

### Fixed
- fix(pdf): 修复便携版 PDF 后端子进程启动失败（退出码1）
- fix(update): 主程序启动时清理残留 .exe.old，消除永久堆积

## [0.4.16] - 2026-07-08

### Added
- feat(update): 更新进度阶段计时 + 关于页展示各阶段耗时

### Changed
- perf(startup): 延迟重型 Tab 模块 import + 打包 optimize=2
- perf(startup): 启动加速——splash 屏 + 懒加载 Tab + 图标缓存

## [0.4.15] - 2026-07-07

### Added
- feat(deps): 设置页依赖树改造 + 批量重装 + 多项 bug 修复

## [0.4.14] - 2026-07-07

### Fixed
- fix(deps): 表格识别 paddlex[ocr] leaf 包缺失检测 + 单包重装

## [0.4.13] - 2026-07-07

### Fixed
- fix(deps): fonttools 依赖检测误报残缺安装致无限重装

### Changed
- perf(pdf): PDF文字层批量OCR + 缩略图/渲染并发

## [0.4.12] - 2026-07-06

### Fixed
- fix(update): 下载对话框支持取消与最小化 + 启动检查并发崩溃修复

## [0.4.11] - 2026-07-06

### Fixed
- fix(settings,deps): 设置界面问题 + 依赖安装/更新修复
- fix(update): 启动自动检查与关于页"检查更新"同时触发时不再崩溃。
- fix(update): 下载进度对话框支持取消、关闭按钮取消和最小化后台下载。

### Changed
- docs(readme): 更新至 v0.4.10 + 新增源码阅读辅助章节

## [0.4.10] - 2026-07-06

### Changed
- refactor(packaging): PDF 重依赖下沉子进程,主 exe 瘦身 ~55M

## [0.4.9] - 2026-07-06

### Fixed
- fix(packaging): pydantic 不应排除，并补齐主进程延迟 import 的 hidden imports

### Changed
- refactor(log): 统一三套子进程日志通道

### Dependencies
- 新增:
  - 新增 fastapi>=0.115.0
  - 新增 uvicorn>=0.34.0
  - 新增 pydantic>=2.11.0

## [0.4.8] - 2026-07-05

### Added
- feat(pdf): PDF 后端 FastAPI 子进程 + 主进程 httpx 客户端(端到端冒烟通过)
- feat(ipc): PDF 后端 IPC 共享 schema(pydantic)+ 加 fastapi/uvicorn 依赖
- feat(update): 依赖更新替换能力增强 — PEP 440 全格式 + 精确锁/降级 + 失败重试提示 + 移除清理
- feat(ui): 文字层网格已纠偏橙色角标
- feat(ui): 自动摆正按钮 + handler + 回调
- feat(manager): PdfSessionManager.auto_deskew_async + signals
- feat(worker): PdfDeskewWorker 方向检测+旋转+文字层同步
- feat(model): PdfPageInfo 新增 deskewed 字段

### Fixed
- fix(pdf): 缩略图宽度自适应面板宽度，消除右侧空白
- fix(pdf): 移除文件后UI残留 + 缩略图滚动条回弹 + page_ready 重入守卫
- fix(pdf): 缩略图静默死亡 + doc_lock 饥饿 + 预热日志矛盾 + OCR 队列提示
- fix: 自动摆正无OCR服务时按钮卡死 + 补充文字层重写测试
- fix: qt_async 在无事件循环时兜底新建（修复 3.13 测试崩溃）

### Changed
- perf(pdf): 打开大文件渐进展示 — open 立即返回页数,load 流式逐页染色
- chore(lint): 修复进程化新代码的 F 类 lint(import 清理 + QThread import)
- test(main_window): ScreenCaptureOverlay 测试在 offscreen 下跳过
- test(pdf): OCR/摆正编排链路测试(mock OCR + 真实后端)
- refactor(pdf): 删除 8 个废弃 worker 源文件 + 清理死引用
- test(pdf): 适配进程化架构 — pdf_tab/export/e2e/session 测试全绿
- test(pdf): 更新 manager 测试适配进程化架构 + 删除废弃 worker 测试
- refactor(pdf): PdfTab 改造走 IPC + 缩略图 IPC worker + generation 校验
- refactor(pdf): PdfSessionManager 重塑为进程化 + IPC worker + model bridge
- refactor(pdf): 阶段0预备 — 折回 fitz 泄漏到 PdfService + PdfPageInfo 加 rect
- docs(pdf): PDF 模块进程化重构设计文档
- chore(deps): uv.lock 依赖版本升级
- refactor(timeout): 超时设置全面收敛 + 死参数清理 + 取消机制
- refactor(cache): cache.json 收敛写入入口 + 原子写 + TTL 抽检 + 一致性校验
- perf(pdf): 文字层检测轻量化 + text_layers 延迟加载 + 缩略图平滑缩放
- perf(pdf): 消除大文件打开/滚动卡顿 — GIL 争用 + 锁忙等
- refactor(pdf): 解耦合并分支 — worker 基类化 + deskew 并入 mutate + session 错位修复

### Dependencies
- 新增:
  - 新增 fastapi>=0.115.0
  - 新增 uvicorn>=0.34.0
  - 新增 pydantic>=2.11.0

## [0.4.7] - 2026-07-03

### Added
- feat(ui): Toast 通知 + 桌面/开始菜单快捷方式 + 预览区缩放/图例/原图查看
- feat(ui): bbox 类型标识改为颜色编码 + 右上角图例，避免遮挡框选内容

### Fixed
- fix(perf): 表格/公式识别卡顿 — mark_pipeline_success 漏标 TABLE/FORMULA
- fix(ci): 生成版本信息文件前确保 dist 目录存在

### Changed
- perf(pdf): 缩略图按需渲染(lazy render)，修复大文件(655页)缩略图无显示+卡顿
- perf(pdf): 批量导入异步化 + 缩略图列表虚拟化，消除导入卡顿和滚动卡顿

## [0.4.6] - 2026-07-02

### Added
- feat(result-view): 工具栏新增复制MD/导出Word/导出Excel按钮
- feat(single-tab): 识别成功后自动折叠选项面板
- feat(widget): TextBlockOptionsWidget 改用 CollapsibleGroupBox
- feat(widget): PreprocessOptionsWidget 改用 CollapsibleGroupBox
- feat(widget): 新增可折叠 CollapsibleGroupBox 组件
- feat(table): 表格 bbox 修复 + 双击编辑 + 复制去底纹/单元格拖选
- feat(toolbar): 工具栏快捷管道截图、表格去重、复制按钮与打包版本信息

### Fixed
- fix: 结果区编辑/公式渲染/通用管道编辑/表格双击/设置后端/截图闪屏
- fix: gate only WebEngine-dependent UI on WebEngine availability; add export-failure test
- fix(tests): 消除 paddle+torch 同进程 DLL 冲突导致的 pytest 0xc0000139 崩溃
- fix(bump): _collect_commits 加 --no-merges，剔除 CHANGELOG 的分支合并噪音
- fix(update): 修复主程序未退出导致文件锁冲突、失败无提示、临时产物残留

### Changed
- style(ruff): 修正分支新增文件的 import 排序与多余 noqa 指令
- test(result-view): 导出 Word/Excel 集成测试
- chore(deps): 显式声明 python-docx、openpyxl 依赖
- docs(update): 修正 webengine_manager 残留注释
- chore(release): CI 不再上传 webengine zip，README 改为内置打包
- docs(result_view): 更新 WebEngine 注释为内置打包语义
- refactor(main_window): 删除 WebEngine DLL 补丁与安装分支
- refactor(install): 删除 InstallWorker 的 WebEngine 下载步骤
- refactor: 删除 webengine_manager 模块与 env_config 路径函数
- refactor(update): 删除 _sync_webengine 及调用点
- refactor(build): 删除 WebEngine 拆分逻辑，始终内置打包
- style: 修复 main.py / update_service.py 既有 lint 问题

## [0.4.5] - 2026-07-02

### Added
- feat(i18n): 加载 Qt 标准对话框中文翻译，颜色选择对话框中文化

### Fixed
- fix(table): 修复表格识别误读 parsing_res_list 导致未识别到文字
- fix(preload): 修复预加载管道列表不一致与 OCR 预加载死锁超时
- fix(ui): 移除截图覆盖层内按钮的 tooltip，规避 QToolTip 黑底

## [0.4.4] - 2026-07-01

### Added
- feat(ui): 单识别标签页接入文本块处理选项
- feat(prefs): OCRPreferences 持久化文本块处理选项
- feat(ocr): 新增文本块处理选项数据模型与后处理器

### Fixed
- fix(ui): 修复截图界面颜色选择对话框黑底
- fix(ui): 修复截图覆盖层 QToolTip 黑底，统一为浅色主题
- fix(主窗口): 截图结束后按操作类型分类恢复主窗口状态
- fix(tests): webengine _frozen fixture 锚定 get_project_root 至临时目录
- fix(tests): 移除 download_artifact 测试对 sys.modules 的伪造注入污染
- fix(ocr): 修复表格/公式按钮走文字识别 + 截图选项页按管道分组 + 状态栏三态提示

### Changed
- test(主窗口): 补充截图窗口恢复逻辑的测试用例
- refactor(settings): 合并 WebEngine 下载入安装流程，下载源收敛为 GitHub 唯一源

## [Unreleased]

### Changed
- refactor(webengine): 结果渲染组件（WebEngine）下载并入安装流程，复用 InstallDialog
  统一对话框（标题/进度条/日志/取消），删除 main_window 独立的 WebEngine 下载对话框与 worker
- refactor(download): 移除所有下载路径的 Gitee 源（env_config 下载源序、env_manager
  回退 _source_label），产物下载彻底 GitHub 唯一源（国内走 gh 代理加速）；关于页保留 Gitee 仓库主页链接

## [0.4.3] - 2026-07-01

### Changed
- refactor(update): 移除 Gitee 作为更新/下载/发版源，产物唯一源 GitHub

## [0.4.2] - 2026-06-30

### Added
- feat(update): updater 坏时主程序 self-update 兜底 + 三态握手
- feat(update): 校验文件同源配对 + 失败原因结构化 + 重试/换源提示

### Fixed
- fix(ci): Gitee 单附件墙钟 8min→55min，重试 2→1 次
- fix(ci): 修复 prune_github 编码崩溃 + Gitee/CNB 镜像假失败
- fix(ci): Gitee 代码镜像移到 Release 同步前 + REST 鉴权改 Header
- fix(bump): tag 推送按上游 remote 自动探测，不再硬编码 origin

### Changed
- refactor(download): 整合 WebEngine 与 Python 运行时同步下载编排
- ci(release): git push 镜像加 --progress，CI 网页可见传输进度

## [0.4.1] - 2026-06-30

### Fixed
- fix(ci): Gitee 上传改流式分块 + 进度日志 + 墙钟超时，修复发版卡死

### Changed
- refactor(bump): 回归单 main 分支发版，删除 develop→main 快照链


## [0.4.0] - 2026-06-30

### Added
- feat(env_config): 新增发布仓库 SSOT 常量 + gh 代理候选工厂
- feat(ui): 保存/另存为/批量导出改异步 + 加载进度提示
- feat(ui): PdfTab 连接 manager 异步信号 + 删除文字层改异步
- feat(manager): export_all_async 异步批量导出
- feat(manager): save_async/delete_text_layers_async 等 mutate 异步编排
- feat(manager): start_ocr 改为 render+ocr 流式编排
- feat(worker): PdfExportWorker 跨 session 批量导出
- feat(worker): PdfOcrWorker 支持 queue 流式消费模式
- feat(worker): PdfRenderWorker 后台逐页渲染 + queue 背压
- feat(worker): PdfMutateWorker 支持 ROTATE/DELETE/REORDER/INSERT/SAVE
- feat(worker): PdfMutateWorker 核心框架 + DELETE_TEXT_LAYER 任务
- feat(pdf-service): save_with_rewrite 按结构改动分流保存策略
- feat(model): PdfDocument 增加 has_structural_change 标志

### Fixed
- fix(ocr): NVML 不可用时 PDF 批量识别退化为逐张
- fix(pdf-service): 删除文字层改为词级 redact + 循环验证至清零
- fix(pdf-service): 文档方向分类 90/270 文字层逆变换公式写反
- fix(ci): 修复 Gitee Release 附件上传从未成功 + 失败被静默吞掉

### Changed
- refactor(about): 关于页链接指向仓库主页（新增 *_REPO_BASE SSOT）
- docs(ci): 仓库常量注释指向 env_config SSOT
- refactor(about): URL 改用 env_config SSOT，链接指向 releases 页
- test(update): 直接覆盖 _download_zip_with_sha 的各分支与清理逻辑
- refactor(update): 仓库标识收敛 + download_update 多源 gh 代理回退
- refactor(webengine): 下载源选择改用 env_config SSOT 工厂函数
- docs: 重新添加项目 README（v0.3.1，重写功能/安装/使用说明）
- perf(startup): 启动期 GPU 探测改后台线程 + 单实例机制
- perf(pdf-service): OCR 文字层保存全量压缩 + 整文档共享子集字体，消除体积膨胀
- perf(pdf-service): open_doc 砍掉主线程 rotation 遍历
- test(pdf-service): 结构性操作置位 has_structural_change 的回归测试
- docs(plan): PDF 异步化与性能优化实现计划
- docs(spec): PDF 处理界面异步化与性能优化设计

### Added
- feat(worker): 新增 PdfRenderWorker / PdfOcrWorker / PdfMutateWorker /
  PdfExportWorker，PDF 渲染/识别/结构操作/批量导出改为后台线程 + queue 背压
- feat(manager): pdf_session_manager 异步编排——start_ocr（render+ocr 流式）、
  save_async / delete_text_layers_async / export_all_async 等 mutate 异步化
- feat(ui): PdfTab 接入 manager 异步信号；保存/另存为/批量导出/删除文字层改异步
  并带加载进度提示

### Fixed
- fix(ocr): NVML/pynvml 不可用时 PDF 批量识别退化为逐张（4090 占用仅 20-40%）
  - `_read_free_vram_mb` NVML 失败时增加 `paddle.device.cuda` 二级兜底读取显存
  - `estimate_gpu_batch_size` 显存探测失败（free_mb=0）但 GPU 模式下返回
    `GPU_FALLBACK_BATCH_SIZE=4` 而非 1，避免大显存卡被迫逐张识别
- fix(ocr): PaddleOCR 3.x 构造时未传 `text_recognition_batch_size`，GPU 模式
  默认注入 8 以喂满显卡
- fix(pdf-service): 删除文字层改为词级 redact + 循环验证至清零，避免残留
- fix(pdf-service): 文档方向分类 90/270 文字层逆变换公式写反

### Changed
- feat(ocr): `OCROptions.use_doc_unwarping` 默认改为 `False`（PDF 文字层场景
  多无扭曲矫正需求，开启每页多跑一个矫正网络）
- feat(pdf-service): `save_with_rewrite` 按结构改动分流保存策略；
  `PdfDocument` 增加 `has_structural_change` 标志
- perf(pdf-service): OCR 文字层保存全量压缩 + 整文档共享子集字体，消除体积膨胀
- perf(pdf-service): `open_doc` 砍掉主线程 rotation 遍历

## [0.3.1] - 2026-06-29

### Added
- feat(packaging): WebEngine 按需下载 + 发布渠道由 CNB 迁移至 Gitee
- feat(ci): 实现 prune_cnb 子命令（仅删 Release 记录，保留 tag）
- feat(ci): 实现 prune_github 子命令（仅删 Release 记录，保留 tag）
- feat(ci): 实现 sync_cnb 子命令（CNB OpenAPI 三步上传 + 公告加 GitHub 下载地址）
- feat(ci): 新增 ci_release_sync 脚本骨架与纯函数（版本排序/清理选择/body 拼接）
- feat(update): GitHub 不可达时提示去 CNB 手动下载

### Fixed
- fix(test): 修复 3 个预存测试缺陷
- fix(packaging): 打包态只读资源路径解析 + CHANGELOG 打入构建
- fix(deps): 设置页重装联动重新检测 + MinerU 双层依赖检测/版本探测
- fix(about): 修正 GitHub 仓库链接大小写并补 CNB 入口

### Changed
- test(bump): 修复 13 个既存测试失败
- perf(build): 清理 WebEngine debug/devtools 资源与多余 locales
- ci(release): 取消 Gitee Release 上传，接入 CNB 镜像/sync_cnb/清理
- refactor(bump): 移除本地 Gitee 上传，--release 仅发 GitHub
- refactor(update): 更新检查改为仅 GitHub，返回 (info, fetch_ok)
- refactor(update): 更新检查仓库常量改用 GitHub FelixJI/VibeOCR

## [0.3.0] - 2026-06-28

### Added
- feat(startup): 无 CUDA GPU 或 CPU 后端时禁用文档解析与 VL 管道

### Fixed
- fix(updater): 缺失 SHA256 校验文件时拒绝更新而非放行
- fix(bump): 解耦 --no-edit 与发版/打包确认，新增 --yes 开关

### Changed
- perf(cpu): CPU 推理线程自适应 + oneDNN 安全探测替代硬编码
- ci(release): 升级 action-gh-release 至 v3 并为 Gitee 同步加重试

## [0.2.3] - 2026-06-28

### Fixed
- fix(updater): 修复更新静默失败——updater 写文件日志、解决 updater.exe 自替换锁死

### Changed
- ci(release): 发版后同步代码镜像与 Release 产物到 Gitee

## \[0.2.2] - 2026-06-28

### Fixed

* fix(release): CHANGELOG 改回 develop bump 时生成编辑提交；修 CI 内联 Python cp1252 崩溃

## \[0.2.1] - 2026-06-28

### Fixed

* fix(ci): 强制 stdout/stderr UTF-8，修复 Windows CI 中文打印崩溃；升级 checkout/setup-python 消 Node20 警告

### Changed

* docs(changelog): 整合 v0.2.0 发布条目（GitHub main 首次快照）

## \[0.2.0] - 2026-06-28

### Added

* feat(single-tab): 复制原始图片到剪贴板 + 浮层提示
* feat(single-tab): 复制图片按钮启用控制（有图启用/PDF禁用）
* feat(single-tab): 新增「复制图片」按钮（默认禁用）
* feat(preview): 暴露 original\_pixmap() getter 供复制原图
* feat(clipboard): 截图复制同时写入文件格式，支持粘贴到文件夹
* feat(ci): 增加 GitHub Actions 推送 tag 自动打包发版流程
* feat(mineru): 首次使用 PDF 文档解析时下载模型 + 进度提示
* feat(bump): 选项 5 未版本化警告 + 选项 1-4 串联合并提示
* feat(bump): 接线 merge 哨兵到 main() 交互式分支
* feat(bump): 实现 cmd\_to\_main 合并至 main 完整流程
* feat(bump): 交互式菜单新增选项 6 合并至 main
* feat(bump): 新增 update\_main\_changelog 顶部插入整合条目
* feat(bump): 新增 check\_unversioned\_commits 发版安全闸
* feat(bump): 新增 generate\_consolidated\_entry 整合生成单条 CHANGELOG
* feat(settings): 连接补充安装按钮，依赖状态表格填充版本与状态
* feat(ui): 设置页新增补充安装按钮与依赖状态表格
* feat(dialog): BackendChoiceDialog 透传 missing\_only + 失败弹窗提示重试
* feat(install): InstallWorker 支持 missing\_only 标志分流增量/全量安装
* feat(env): 新增 install\_missing\_dependencies/get\_dependency\_versions，失败 logger.error 全文
* feat(env): \_install\_paddle\_stack 支持 requirements\_override 增量子集
* feat(bump): 交互式菜单新增'仅打包当前版本'选项
* feat(settings): 应用设置页连接重装 Python/依赖按钮 + 状态刷新
* feat(ui): 应用设置页新增'环境维护'分组（重装按钮）
* feat(dialog): BackendChoiceDialog 透传 reinstall\_python
* feat(install): InstallWorker 加 reinstall\_python + 进度日志镜像
* feat(env): 新增 reinstall\_embedded\_python 强制删除后重装
* feat(pdf-preview): 预览窗口翻页工具栏+键盘+信号注入；块编辑刷新弹窗
* feat(pdf-tab): 缩略图增量更新——拖拽只移item不渲染、旋转逐页渲染
* feat(pdf-tab): 网格 ↔ 缩略图双向选中同步（重入保护 + 多选）
* feat(pdf-tab): OCR 逐页即时变绿 + 删除文字层逐页变灰，移除缩略图无谓渲染
* feat(pdf-tab): 文字层状态网格化（IconMode + delegate + 图例汇总）
* feat(about): 关于页卡片化重写——品牌/信息/日志三卡片 + token 配色
* feat(theme): main.py 加载全局浅色 QSS
* feat(theme): 新建浅色 token 模块 ui/theme.py + QSS 生成器
* feat(ui): apply new app icon across runtime, packaging, and About page
* feat(pdf-service): embed subset CJK font in text layer for cross-reader search
* feat(cjk-font): module singleton + atexit cleanup hook
* feat(cjk-font): fontTools subsetting + resolve/cleanup with charset cache
* feat(cjk-font): system CJK font detection with caching
* feat(ui): pipeline TTL spin + release heavy/all buttons in settings
* feat(rpc): worker RELEASE\_PIPELINES/SET\_TTL handlers + evict\_idle + subprocess client
* feat(rpc+config): RELEASE\_PIPELINES/SET\_TTL msg types + TTL/max\_heavy settings
* feat(ocr-service): integrate PipelineCacheManager (touch + FIFO on create)
* feat(cache): PipelineCacheManager with VRAM-tiered max\_heavy, FIFO eviction, TTL idle reclaim, explicit release
* feat(pipelines): mark heavy pipelines (PP-V3/VL/MinerU) in metadata
* feat(pdf): dynamic BATCH\_SIZE based on GPU VRAM / CPU RAM
* feat(utils): add estimate\_cpu\_batch\_size for RAM-based dynamic batching
* feat(gpu): add estimate\_gpu\_batch\_size pure function for PDF dynamic batching
* feat(utils): add system\_memory.get\_available\_ram\_mb (cross-platform, stdlib-only)
* feat(mineru): bind JobObjectGuard to mineru-api subprocess
* feat(worker): bind JobObjectGuard to PaddleX worker subprocess
* feat(job-object): implement close handle with idempotency
* feat(job-object): implement assign\_from\_popen process binding
* feat(job-object): implement CreateJobObjectW with kill-on-close flags
* feat(job-object): add JobObjectGuard non-Windows no-op skeleton
* feat: CUDA 运行时改用 torch/lib，统一 cu126，CPU 禁用 mkldnn
* feat(pdf): status-list context menu to add text layer for selected no-layer pages
* feat(pdf): soft-guard dialog when adding text layer to pages with existing layer
* feat(pdf): button to add text layer for pages without one
* feat(pdf): start\_ocr accepts overwrite; add get\_pages\_without\_text\_layer
* feat(pdf): add\_text\_layer guard against duplicate via overwrite flag
* feat: auto-preview and failure summary after text-layer OCR completion
* feat: nested resizable splitters, scrollable status, embedded preview in PdfTab
* feat: persist pdf splitter state in OCRPreferences
* feat: accumulate OCR write/skip stats and emit ocr\_stats\_ready
* feat: add ocr\_stats tracking to PdfSession
* feat: write Chinese text layer with china-s CID font, return write/skip counts
* feat: BackendChoiceDialog for first-launch GPU/CPU choice
* feat: MainWindow consumes pending\_backend on restart, shows SwitchDialog
* feat: SwitchDialog for restart-time backend switching
* feat: register '推理后端' settings page
* feat: BackendOptionsWidget for settings page backend switching
* feat: InstallWorker accepts force\_backend to override GPU/CPU auto-detect
* feat: resolve GPU/CPU at runtime via cache-first fallback
* feat: rename top tab title from "二维码生成" to "二维码"
* feat: implement QR decode behavior — paste/drop/select/recognize/open-url
* feat: build decode sub-panel UI with result list and DecodeResultWidget
* feat: add QrcodeDecodeService for QR/barcode decoding via pyzbar
* feat: migrate embedded Python to python-build-standalone
* feat: migrate MinerU backend names to 3.3 canonical + add effort option
* feat: restrict PDF text-layer pipelines to OCR/Table/Formula
* feat: PdfTab reads PDF OCR options from preferences
* feat: add PDF options page to settings
* feat: add PdfOptionsWidget with pipeline options and global settings
* feat: OCRPreferences adds 'pdf' source and PdfGlobalSettings persistence
* feat: PdfSessionManager passes PdfGlobalSettings to add\_text\_layer
* feat: add\_text\_layer uses inverse rotation and PdfGlobalSettings
* feat: add bbox inverse rotation transform and pixel conversion
* feat: add PdfGlobalSettings data model with DPI adjustment
* feat: wire PdfTab shutdown into MainWindow closeEvent
* feat: refactor PdfTab for multi-file support with PdfSessionManager
* feat: add PdfSessionManager for multi-file session orchestration
* feat: add PdfOcrWorker for async OCR processing
* feat: add PdfLoadWorker for async page loading
* feat: add PdfSession dataclass for multi-file PDF state
* feat(settings): add screenshot panel per-pipeline options page
* feat(screenshot-panel): read persisted options per-pipeline with tooltips
* feat(main-ui): per-pipeline option init and save on recognition start
* feat(preprocess-options): add pipeline\_switching/switched signals
* feat(ocr-preferences): per-pipeline options storage with v1→v2 migration
* feat(ui): 补齐表格识别和公式识别的完整配置控件及信号连接
* feat: 统一 OCROptions 补齐 TABLE\_RECOGNITION 和 FORMULA\_RECOGNITION 缺失字段
* feat: supported\_options 补齐表格识别和公式识别的完整选项声明
* feat(prefs): 支持 TABLE\_RECOGNITION / FORMULA\_RECOGNITION 持久化和首次使用提示
* feat(ui): PreprocessOptionsWidget 支持表格/公式识别管道选项
* feat(pipelines): 向后兼容 — OCRPipeline 枚举新增 TABLE\_RECOGNITION 和 FORMULA\_RECOGNITION
* feat(pipeline): 注册所有管道 PipelineSpec 并导出 get\_registry()
* feat(pipeline): 创建公式识别管道 (FORMULA\_RECOGNITION)
* feat(pipeline): add PipelineSpec and PipelineRegistry for pipeline registration
* feat(pipelines): 创建四个管道独立 Options 类 (Task 2)
* feat(pipeline-registry): add BasePipelineOptions base class
* feat: 坐标映射层重构 - 修复高DPI bbox偏移和预处理旋转归一化
* feat(mapper): 新增 screenshot\_dpr 和 logical\_to\_screenshot\_physical
* feat(screenshot): 新增 ScreenCoordinateMapper 多屏坐标映射器
* feat: 集成 PDF 处理标签页到主窗口
* feat(status): pipeline\_status 扩展 PADDLEOCR\_VL 管道追踪
* feat(ui): 文档类管道锁定改造，MineRU 和 PaddleOCR-VL 可互切换
* feat: 添加 PDF 标签页完整 UI
* feat(ui): PreprocessOptionsWidget 新增 PaddleOCR-VL 选项组
* feat(service): 子进程服务三路路由，PADDLEOCR\_VL 走 WorkerManager
* feat: 添加 PDF 独立预览窗口
* feat(service): OCRService 新增 PADDLEOCR\_VL 管道映射和识别方法
* feat: 添加 PdfService 删除文字层功能
* feat(options): OCROptions 新增 vl\_task 字段支持 PaddleOCR-VL 任务类型
* feat: 添加 PdfService 文字层写入功能
* feat(pipeline): 新增 PADDLEOCR\_VL 枚举，DOCUMENT\_PARSING 改名 MineRU（文档）
* feat: 添加 PdfService 页面操作（旋转/删除/插入/移动）
* feat: 添加 PdfService 基础功能（打开/保存/渲染/文字层检测）
* feat: 添加 PDF 文档数据模型
* feat(overlay): 连接填充颜色/透明度/联动信号到画布和标注项
* feat(canvas): 添加 fill\_opacity/fill\_linked 属性和 setter 方法
* feat(annotation): 扩展 RectAnnotation/EllipseAnnotation 支持独立填充色和透明度
* feat: paintEvent 绘制窗口检测高亮
* feat: mousePressEvent HOVER 点击选中 / DRAG 切换
* feat: mouseMoveEvent HOVER 子状态窗口检测
* feat: start\_capture 初始化 WindowDetector
* feat: ScreenCaptureOverlay 新增 HOVER/DRAG 子状态属性
* feat: WindowDetector.\_get\_control\_rect — IAccessible + EnumChildWindows 降级
* feat: WindowDetector.\_get\_window\_rect 实现
* feat: WindowDetector 基础框架 — \_hit\_test 窗口检测
* feat: 首次识别失败时提示保持网络畅通
* feat: 识别前显示首次使用提示
* feat: 添加 pipeline\_status 管道成功记录模块
* feat: UI 选项面板新增语言下拉和页码范围控件
* feat: MinerUService API 参数对齐 — 默认后端改为 hybrid、新增 lang\_list/页面范围参数、回退链反转、响应状态校验
* feat: add content\_list normalization layer (legacy + V2 formats)
* feat: DOCUMENT\_PARSING supported\_options 新增 lang\_list/start\_page\_id/end\_page\_id
* feat: 扩展 OCROptions 新增 lang\_list/start\_page\_id/end\_page\_id 字段，默认后端改为 hybrid-auto-engine
* feat: 连接右侧编辑信号并实现数据同步
* feat: 添加 ResultViewWidget.update\_block\_text 方法
* feat: 添加右侧结果视图的 contenteditable 编辑交互逻辑
* feat: 添加 Bridge blockEdited 信号和 ResultViewWidget block\_edited 信号
* feat: bump\_version 添加 --release 命令，支持 Gitee/GitHub 发布
* feat: bump\_version 增强 — version.json、updater.exe、zip 打包、SHA256
* feat: 集成更新检查到启动流程和关于页面
* feat: 新增 updater\_main.py 独立更新助手脚本
* feat: 新增 update\_service — 版本检查、下载校验、跳过版本、更新对话框、编排器
* feat: 新增 data/ 目录路径函数，用于更新系统
* feat: 引入 Pillow 替换马赛克/模糊效果实现
* feat: 工具栏添加"选择"按钮，默认选中选择模式
* feat: 连接属性条信号到选中标注项，支持实时属性修改
* feat: ToolPropertiesBar 支持选中项属性编辑，新增通用属性页
* feat: EditCanvas 集成 SelectionDecorator 生命周期管理
* feat: 创建 SelectionDecorator，支持 8 个手柄的选中态装饰器
* feat: MosaicItem/BlurItem 添加 resize 支持（\_resizing 标志 + regenerate 方法）
* feat: 为 RectAnnotation/EllipseAnnotation 添加属性 setter 方法，所有标注项启用 ItemSendsGeometryChanges
* feat: ScreenCaptureOverlay 集成 SelectionResizeFrame
* feat: InlineEditCanvas.update\_crop\_region 更新裁剪区域和平移标注
* feat: SelectionResizeFrame 选区拖拽手柄控件
* feat: MosaicItem/BlurItem 新增 update\_background 方法
* feat(toolbar): 用 Lucide SVG 图标替换 Unicode 按钮，统一 QToolButton
* feat(icons): 添加 Lucide SVG 图标模块用于工具栏按钮
* feat(log): 转发子进程日志到项目日志系统
* feat(log): 用 RotatingFileHandler 替换 FileHandler 并集成清理功能
* feat(log): 添加 \_cleanup\_old\_logs 函数及测试
* feat: 重写启动性能分析脚本（import/init/render 分层）
* feat(inline-editor): 将 ScreenCaptureOverlay 集成到 MainWindow
* feat(inline-editor): 添加带状态机的 ScreenCaptureOverlay
* feat(inline-editor): 添加带快捷按钮的 InlineRecognitionPanel
* feat(inline-editor): 添加支持标注的 InlineEditCanvas
* feat(inline-editor): 添加带图标按钮的 InlineToolbar
* feat(inline-editor): 添加磨砂玻璃浅色主题样式常量
* feat(toolbar): 添加专用拖拽手柄，文字替换为图标
* feat(main-window): 集成 show\_toolbar 开关和工具栏位置持久化
* feat(ui): 添加显示工具栏复选框及缩进子选项
* feat(toolbar): 添加 position\_changed 信号和 set\_initial\_position
* feat(settings): 添加 show\_toolbar 和 toolbar\_pos 属性，兼容旧配置
* feat(tabs): 添加预处理选项的双向偏好同步
* feat: 添加 NetworkDetector 支持并行端点探测
* feat(qrcode): 将 QrcodeTab 集成到 MainWindow
* feat(qrcode): 添加完整的 QrcodeTab UI 布局和预览
* feat(qrcode): 添加 QR 码 SVG 导出
* feat(qrcode): 添加文字标签叠加和颜色反转
* feat(qrcode): 添加 QR 码 logo 嵌入
* feat(qrcode): 添加条形码生成（Code128、Code39、EAN-13 等）
* feat(qrcode): 添加 QrcodeService 提供 QR 码生成
* feat(widgets): 添加预览组件支持图像显示和屏幕截图功能按钮
* feat(single-tab): 连接操作按钮 — 文件对话框和剪贴板粘贴
* feat(single-tab): 在预览上方添加截图/文件/粘贴操作按钮
* feat(preload): 识别请求排队等待预加载完成，替代抢占机制
* feat(tabs): 将 OCR 执行逻辑迁移到 SingleRecognitionTab
* feat(tabs): 创建 SingleRecognitionTab 基础结构
* feat(widgets): 为 PreviewWidget 添加 PDF、页面导航和 content\_list
* feat(main\_window): 为批量识别标签页添加 PaddleX 服务支持
* feat(ui): 添加文件预览组件和OCR服务功能
* feat(main): 添加主窗口视图逻辑和应用设置配置
* feat(ocr): 添加 OCR 服务和 Worker 进程管理功能
* feat(core): 添加模型缓存管理和OCR服务预加载功能
* feat: 统一文件对话框，支持所有 MineRU 格式和 Office 预览占位
* feat: 用 QWebEngineView、block registry 和 KaTeX 重写 result\_view\_widget
* feat: 添加离线 KaTeX 资源及更新脚本
* feat: 为 TextBlock 添加 page\_idx，标准化 bbox 到 \[0,1000]
* feat: 添加共享 MIME 类型映射和文件过滤器常量
* feat(ui): 在设置中添加工具页面用于模型下载
* feat: 添加关于标签页，含版本、更新日志和应用信息
* feat: 添加语义版本管理脚本
* feat: 为 OCR 结果块添加悬停提示标题属性，移除内联置信度显示
* feat(ocr): 添加 OCR 服务和子进程 worker 支持
* feat(subprocess): 双 WorkerManager 路由（PaddleX 和 MinerU）
* feat(worker): 添加 MinerU worker 子进程脚本
* feat(ui): 添加批量识别标签页和主窗口批量识别功能
* feat(core): 添加日志服务和主窗口视图
* feat(batch): 添加批量文件列表组件和批量识别标签页
* feat(export): 添加 OCR 结果导出功能和服务
* feat: 将 ModelDownloadDialog 集成到安装流程并添加菜单项
* feat: 添加 ModelDownloadDialog 模型下载进度 UI
* feat: 添加 ModelDownloadService 管理模型下载
* feat: 添加 GPU 环境下 PyTorch CUDA 版本支持
* feat(env): 更新依赖配置以支持 MinerU 和 PaddlePaddle 环境分离
* feat(env): 添加 torch 检测和 MinerU 模型下载
* feat(ui): 添加主窗口视图逻辑实现
* feat(core): 初始化核心模块和批量识别功能
* feat(batch): 创建 MinerUBatchService，绕过子进程层直接批量处理
* feat: MineRU 集成与 PaddlePaddle-GPU 移除
* feat(routing): DOCUMENT\_PARSING 流水线路由到 MinerUService
* feat(mineru): 实现 MinerU FastAPI 服务封装
* feat(main): 添加主窗口视图逻辑实现
* feat(ocr): 增强OCR服务的HTML和Markdown处理功能
* feat(ui): 实现 OCR 选项持久化及同步功能
* feat(core): 优化缓存刷新及预加载预热逻辑
* feat(extraction): 实现抽取功能的工作线程和进度管理
* feat(tray): 添加系统托盘与边缘工具栏功能集成
* feat(editor): 添加截图编辑窗口及图像编辑画布功能
* feat: 重构模型缓存管理器，使用配置文件管理管道模型
* feat: 统一管道定义和预处理选项，支持全部7个管道
* feat(config): 支持同时管理 MLLM 和 LLM 配置
* feat: 添加窗口和组件尺寸可拖动与持久化功能
* feat(ocr): 基于模型缓存状态的智能超时机制
* feat(ui): 添加 UI 编译脚本和生成的 Python UI 文件
* feat(services): 添加 OCRServiceBase 抽象基类
* feat(services): 添加 env\_config 模块用于环境配置
* feat(views): 添加 BaseOcrTab 轻量级基类
* feat(managers): 添加 SettingsManager 用于 LLM 配置和模板
* feat(managers): 添加 SubprocessManager 管理 OCR 子进程生命周期
* feat(ocr): 新增 PaddleOCR-VL 多模态文档解析管道支持
* feat(settings): 添加 LLM 配置和模板管理
* feat(extraction): 将抽取标签页集成到主窗口
* feat(extraction): 添加 ExtractionWorker 后台抽取
* feat(extraction): 实现 ExtractionTab 视图
* feat(extraction): 添加抽取标签页 UI 文件
* feat(extraction): 添加抽取功能数据模型
* feat(batch): 实现批量 OCR 识别功能
* feat(main\_window): 将 BatchRecognitionTab 集成到主窗口
* feat(views): 添加 BatchRecognitionTab 用于批量文件处理
* feat(widgets): 添加 BatchFileListWidget 用于文件管理
* feat(widgets): 添加 PreprocessOptionsWidget 用于批量预处理
* feat(service): 为 OCRServiceSubprocess 添加批量处理接口
* feat(worker): 集成 BatchQueueManager 用于批量处理
* feat(workers): 添加 BatchQueueManager 用于批量推理
* feat(models): 添加 BatchRequest 及相关数据模型
* feat(utils): 添加 GPUMemoryMonitor 用于动态批量大小估算
* feat(shared\_memory): 添加批量消息类型和序列化函数
* feat(main): 添加子进程启动进度反馈及预热机制
* feat(core): 增加子进程OCR服务与管道预加载支持
* feat(subprocess): 实现 OCR 子进程架构
* feat(main): 优化OCR线程和增加模型预加载设置界面
* feat(model\_cache): 新增模型缓存管理器以提升启动性能
* feat(utils): 导出 IndentProcessor 和 IndentConfig
* feat(converter): 集成 IndentProcessor 和 sane\_lists 扩展
* feat(converter): 添加中文缩进和列表嵌套 CSS 样式
* feat(indent): 实现 process\_markdown 中文段落换行
* feat(indent): 实现 is\_chinese\_text 检测方法
* feat(indent): 添加 IndentProcessor 基类和配置
* feat(core): 支持多种OCR管道预设及低置信度提示
* feat: 在主窗口添加刷新缓存菜单项
* feat: 将缓存集成到依赖检查中
* feat: 添加缓存验证和操作函数
* feat: 添加缓存路径和读写函数
* feat: 使用硬件信息生成机器 ID
* feat: 为关键操作添加日志
* feat: 将 ConsoleWidget 集成到主窗口并添加日志
* feat: 在主窗口 UI 中添加控制台容器
* feat: 添加基于表格的日志显示 ConsoleWidget
* feat: 添加带 QtLogHandler 的日志服务

### Fixed

* fix(subprocess): 修复启动卡顿30秒、SET\_TTL超时、无效管道警告
* fix(test): 修复依赖状态表格测试与 markdown 模块同步
* fix(overlay): start\_capture 前清空旧选区并提前重绘，消除「一闪而过」
* fix(ocr): 修正预处理图 RGB/BGR 误翻转导致 bbox 预览颜色异常
* fix(magnifier): 修复混合 DPR 多屏下放大镜取样错位
* fix(overlay): 修复截图编辑界面 QToolTip 黑色背景
* fix(worker): 修复打包态 OCR Worker 启动失败及相关问题
* fix(install): 修复依赖安装孤儿进程、缓存不刷新、markdown 误报缺失
* fix(env): 安装韧性增强 + 半成品清理 + 依赖状态不同步
* fix: 恢复 pyproject/**init** 版本号为 0.1.6
* fix(install): 安装完成后刷新设置页环境状态，设置页重装改非模态
* fix(install): 屏蔽依赖安装/OCR子进程的命令行弹窗
* fix(install): \_install\_paddle\_stack 兼容打包环境 paddlepaddle 键名，修复 KeyError
* fix(test): uv.lock 路径支持环境变量隔离，修复 10 个预存测试失败
* fix(打包): 修正方案A误删 Qt6Qml\*/Qt6Quick 导致 QtWebChannel 加载失败
* fix(发版): 发版时自动同步 uv.lock，修正版本号滞后漂移
* fix(打包): 修复进程递归卡死/体积臃肿/安装入口缺失/路径解析错误
* fix(pyright): 扩展检查范围至 scripts/tests/qa/examples 并清零 156 个 error
* fix(updater): 修复 item 可能未绑定导致的 Pyright 告警
* fix(pdf-text-layer): 修复带 /Rotate 页面文字层坐标旋转错位
* fix(日志): 修正推理硬件误报 + 预热输出设备信息 + 第三方库降噪
* fix(updater): 加固程序内更新链路
* fix(pdf-tab): 终审修复——OCR 完成不全量重建网格（保留用户选中）+ 新增选中保留测试
* fix(pdf-preview): Task 6 审查修复——切换文件/删页关闭预览窗避免失效索引 + 键盘翻页测试
* fix(pdf-tab): Task 5 审查修复——拖拽重排保留选中态 + 新增选中保留测试
* fix(pdf-tab): Task 4 审查修复——重建期间抑制同步 + 简化 role + 加强递归/双向断言
* fix(pdf-tab): Task 2 审查修复——hover 态描边 + delegate 顶层导入 + 直接 QSize + 新增颜色采样测试
* fix(pdf-tab): Task 1 代码审查修复——更新 \_on\_ocr\_stats\_ready 文档+清理内嵌预览残留测试
* fix(about): 修复 Logo 模糊 + 宽屏右侧大片空白
* fix(editor): QColorDialog 改用 Qt 自绘对话框，修复透明父窗口下黑底
* fix(toolbar): EdgeToolbar 改用 paintEvent 绘制浅色背景
* fix(cjk-font): address code review — temp leak, fontname regression test, dead property
* fix(mineru): \_start\_api 用 self.**class** 替代未定义的 cls（F821 生产崩溃）
* fix(ui): enable 重新识别 after screenshot + clarify option source
* fix(log): 折叠子进程裸 print，避免用户文档内容泄漏到日志
* fix(ui): PDF preview bbox follows zoom + add drag-to-pan
* fix(cache): CPU mode max\_heavy=1 + wire max\_heavy\_pipelines config override
* fix: PDF 批量 OCR 拆批（每批10页）+ 健康检查阈值修复
* fix: PDF 文字层预览改用 OCR 原始块（单一信源）+ 双击改字
* fix: 边缘隐身悬浮工具栏补 WA\_StyledBackground，浅色背景不再透明
* fix: 修复批量识别参数透传错误与 request\_id 不匹配导致结果丢失
* fix: PDF 文字层写入在窄/瘦高 bbox 时降级 insert\_text 兜底
* fix: PDF 文字层 OCR 改用真批量 predict(list)，子进程 RCBG 协议
* fix: CUDA 版本映射对齐真实 wheel（cu126 同源，弃用 cu129）
* fix: 文字层不丢块 + 预览状态列表联动缩略图
* fix: guard zero-write case, add embedded-preview E2E test, correct spec doc
* fix: correct misleading text-layer status wording to block count
* fix: log warning on None-bbox skip path in add\_text\_layer
* fix(portable): repair GPU install + lay groundwork for backend switching
* fix: restore cu13 nvidia deps + disable mkldnn on CPU for OCR
* fix: remove redundant nvidia-\* deps — paddle uses system CUDA driver
* fix: address final code review nits
* fix: correct text layer preview coordinates and add hover tooltips
* fix: resolve C1-C3 critical issues and I1 from code review
* fix(settings): 持久化预加载启用开关和单文件识别选项
* fix: PaddleOCR-VL 补传预处理参数 use\_doc\_orientation\_classify/use\_doc\_unwarping
* fix(ocr): 提取预处理后图像用于bbox归一化和预览显示
* fix(ocr): 预处理旋转90°/270°时交换归一化维度，修复bbox偏移
* fix(preview): 延迟 overlay 更新，确保布局稳定后再计算坐标
* fix(canvas): update\_crop\_region 改用 screenshot\_dpr
* fix(overlay): 截图坐标转换改用 screenshot\_dpr
* fix(bbox): 修复 bbox 覆盖层定位偏离
* fix(selection): 修复 constrain\_rect 边界约束优先于最小尺寸
* fix(vl): 修正管道名为 PaddleOCR-VL-1.5，替换选项为实际支持的布局/图表/印章开关
* fix: PDF 标签页使用 PaddleX 服务而非 MinerU 服务
* fix: 减小截图界面识别面板宽度 200→120
* fix: update tests for \_mineru\_manager -> \_mineru\_batch refactor
* fix: 修复右侧编辑同步问题（全量重建 raw\_text、同步 list/code 结构化字段、更新 text\_with\_scores、清理重复 docstring）
* fix: 初始化 cl\_idx 消除 Pyright possibly unbound 警告
* fix: 修复预热期间并发识别导致的竞态条件和 Web 视图空指针问题
* fix: 修正嵌入式 Python 版本为 3.13.0，与 pyproject.toml 一致
* fix: ResizeAnnotationCommand 支持 MosaicItem/BlurItem 重新生成效果
* fix: 修复截图编辑工具无法绘制的问题
* fix: 选区内部鼠标事件不再被拦截，仅边框区域可移动选区
* fix: 识别面板底部对齐选区下沿，高度仅容纳按钮
* fix: 修复截图选区移动时内部内容波纹和晃动问题
* fix: EDITING 模式下绘制冻结截图背景，避免实时桌面透出
* fix: SelectionResizeFrame 覆盖全屏避免拖拽闪烁
* fix(shm): 移除 \_is\_data\_ready 轮询中的重复 debug 日志
* fix(toolbar): 修复截图界面工具栏tooltip黑色背景问题
* fix(widgets): 修复高DPR屏幕下坐标映射和截图偏移问题
* fix(toolbar): 浅灰色背景带边框，隐藏空属性条，显示工具提示
* fix(toolbar): 通过 WA\_StyledBackground 实现不透明背景，紧凑识别按钮，移除所有阴影
* fix(panels): 在识别面板中使用 recognition\_button\_style 样式
* fix(screenshot): 修复高 DPI 下截图选区大小变化的问题
* fix: 修复 PropertiesBar 信号名称不匹配
* fix: 显式销毁 QWebEngineView 避免退出时崩溃 0xC0000409
* fix(qrcode): 修复 QR 码非对齐尺寸下的倾斜问题，改善文字清晰度
* fix(env): 使用 detect\_gpu() 进行准确的 GPU 和 CUDA 检测
* fix(toolbar): 操作按钮使用 PointingHandCursor
* fix(toolbar): 启用从按钮区域拖拽，添加事件过滤和光标反馈
* fix(tray): 仅在启用最小化到托盘时阻止关闭窗口退出程序
* fix(single-recognition): 修复剪贴板 bbox 偏移，添加手动启动按钮
* fix(export): 自动重命名输出文件，避免静默覆盖
* fix(preview,export): 识别 MinerU 标题块并包含表格标题
* fix(download): 改进对话框取消逻辑，使用管道枚举显示名称
* fix: 修复 bump\_version 在 Windows 上的编码问题，重连下载按钮，移除过期测试
* fix: 解决 bump\_version 脚本和测试中的 Pyright 类型问题
* fix(mineru\_service): 修复 MinerU 服务中的边界框坐标转换问题
* fix(worker): 为 MinerU worker 添加 --use-gpu/--no-gpu 兼容参数
* fix(cuda): 扫描所有 nvidia 包 bin 目录进行 DLL 注册
* fix: 将未缓存管道超时从 300s 增加到 600s 用于模型下载
* fix: 移除 ModelDownloadDialog 中未使用的 DownloadStatus 导入
* fix: 更新 download\_pipeline 和 download\_mineru\_models 中的 \_statuses
* fix(deps): 更新依赖配置和模型下载逻辑
* fix(env): 在安装函数中将 mineru\[all] 改为 mineru\[pipeline]
* fix(tray): 修复托盘菜单设置功能
* fix(batch): MinerUBatchService 改用延迟导入，修复 Pyright 警告
* fix: 修复共享内存批量操作 read-own-write race condition
* fix: 优化纯文本提取和日志过滤
* fix(worker): 优化 Worker 管理和OCR服务状态显示
* fix(core): 统一处理管道名称中的枚举类型
* fix(env\_manager): 添加缺失的导出函数和常量
* fix(screenshot): 优化截图窗口的透明背景和绘制逻辑
* fix(types): 修复 IndentProcessor 类型注解错误
* fix(console): 优化低置信度日志信号和详情收集
* fix: 添加缺失的 QWidget 和 QVBoxLayout 导入，修复 hatchling 包路径
* fix: 修复 QMainWindow 嵌套问题解决空白 UI
* fix: 用 QBuffer 替换 BytesIO 用于 QPixmap.save
* fix: 修正引擎延迟加载测试

### Changed

* refactor(release): cmd\_to\_main → cmd\_publish\_main 推送 GitHub 快照链
* refactor(single-tab): 文件打开图片分支统一走 set\_pixmap 更新复制按钮启用状态
* perf(shm): 共享内存 128MB→16MB 并统一默认值来源
* test(install): 补充取消机制/缓存写入/版本失效测试，迁移 run mock→Popen
* perf(pack): lxml/pydantic/chardet/aiohttp 等从 exe 包排除（省 \~14MB）
* perf(pack): scipy/pandas 从 exe 包排除（省 \~80MB）
* perf(pack): markdown 从 exe 包移至便携 Python 安装
* chore(repo): 取消跟踪 docs（保留本地文件）
* perf(pack): 去掉 UPX 压缩提速启动 + 工具栏说明 + compile\_ui 修复
* chore(changelog): develop 同步 main 的整合版 CHANGELOG（首次初始化）
* test(bump): 修复 ruff 未用变量 + 补充 main 首次初始化步骤
* refactor(bump): develop bump 瘦身，不再生成 CHANGELOG/打 tag
* refactor(bump): 抽出 \_collect\_commits/\_filter\_release\_commits 供合并复用
* docs(plan): develop→main 合并发版与 CHANGELOG 整合实施计划
* docs(spec): develop→main 合并发版与 CHANGELOG 整合设计
* refactor(env): 提取 \_build\_paddle\_requirements，消除 paddle 项构建重复
* docs(plan): 依赖增量安装实现计划（9 个任务，TDD）
* docs(spec): 依赖增量安装（断点续传语义）设计
* chore: lint 清理（安装日志 + 重装入口）
* refactor(env): 依赖安装/后端切换 report 闭包改用 logging
* refactor(env): install\_embedded\_python 改用 logging 落盘日志
* refactor(env): download\_file\_with\_progress 改用 logging 落盘日志
* docs(plan): 安装日志接入 logging + 设置页重装入口实施计划
* docs: 安装日志接入 logging + 设置页重装入口设计
* chore(换行符): 新增 .gitattributes 统一 LF，消除 autocrlf 警告
* refactor(质量): ruff/pyright 全量清零 + 版本测试永久免疫 bump
* refactor(env): 修正 torch 镜像源、移除无调用方的安装入口、修复更新后依赖降级
* chore(版权): 版权年份更新为 2025–2026，关于页年份改为运行时取系统日期
* refactor(pdf-tab): Task 3 审查修复——提取 \_layer\_cell\_tooltip 消除重复
* refactor(pdf-tab): 删除右分隔器与内嵌预览，操作面板直挂主 splitter
* revert(ui): 暂时禁用全局浅色 QSS，回到 Qt 原生控件风格
* test(constants): 适配 COLOR\_\* 迁移到 theme.Colors
* refactor(cleanup): A 类零星内联样式迁移到 theme token（batch/backend/preprocess/update）
* refactor(widgets): B 类内联样式迁移到 theme token（chat/preview/clipboard/qrcode）
* refactor(styles): 删除 editor\_styles/inline\_styles/styles 旧样式模块 + constants 旧色名 + 测试
* refactor(screen\_capture): 迁移 InlineStyles 尺寸常量到 theme.Layout
* refactor(inline): inline\_toolbar/recognition\_panel 迁移到 theme token
* refactor(tool\_properties\_bar): 迁移到 theme token（暗→浅色）
* refactor(edit\_toolbar): 迁移到 theme token（暗→浅色）
* refactor(recognition\_panel): 迁移到 theme token（暗→浅色）
* docs(plan): 浅色主题统一迁移实施计划（12 个 Task）
* docs(spec): 补 styles.py 第4色源 + Layout 尺寸 token（计划前发现）
* docs(spec): 适配近期提交——图标已落地、toolbar WA\_StyledBackground gotcha
* test(pdf): cross-reader searchability assertions (ToUnicode/FontFile/volume)
* build(deps): add fonttools for PDF text layer font subsetting
* docs(spec+plan): PDF text layer embedded font (fontTools subset + ToUnicode)
* test(integration): pipeline cache lifecycle e2e (release/set\_ttl flow)
* docs(plans): implementation plans for dynamic batch size + pipeline cache lifecycle
* docs(spec): pipeline cache lifecycle + dynamic batch size design
* docs: 浅色主题统一 + 关于页卡片化设计 spec
* refactor(pdf): extract \_load\_ocr\_prefs/\_begin\_ocr\_ui; add start\_ocr overwrite e2e test
* refactor: 收敛防御性代码，消除冗余/无效/风格不一致
* docs: mark PDF text layer fix as delivered
* perf: debounce splitter state save; test right-splitter persistence
* refactor: extract PreviewCanvas as public reusable class
* docs: implementation plan for PDF text layer fix
* docs: design spec for PDF text layer fix
* test: mock NetworkDetector in mineru tests to stop wmic Popen leak
* docs: mark backend choice UI as delivered (batch 2)
* refactor: unify env management — consolidate dep specs, dedupe install, remove dead code
* test: cover image-drop signal and generate-subtab drop rejection
* refactor: restructure QrcodeTab into nested generate/decode sub-tabs
* test: cover multi-code, blank, file/bytes, large-image, URL edge cases
* chore: add pyzbar dependency for QR/barcode decoding
* docs: add QR code decode feature implementation plan
* docs: add QR code decode feature design spec
* chore: track PyInstaller .spec files (build config versioning)
* docs: mark preproc\_angle resolved via pipeline restriction
* chore: default \_last\_pdf\_pipeline to OCR (matches PDF allowed set)
* refactor: add generic lock\_to\_pipelines; lock\_to\_document\_parsing wraps it
* test: add preproc\_angle=0 production path regression test
* ⚡️ perf(pdf): 用轻量占位页面替代 open\_doc 中的 build\_page\_infos 调用
* docs: add PDF OCR settings implementation plan
* docs: add PDF OCR settings and text layer fix design spec
* chore: checkpoint workspace changes on develop
* ♻️ refactor: 将 PdfService 重构为无状态工具层
* ♻️ refactor: 统一依赖版本管理，消除 env\_manager 与 env\_config 的常量重复
* 🐛 fix: 使用 uv override-dependencies 彻底排除 opencv-python
* 🐛 fix: 修复 opencv 包冲突导致 cv2.IMREAD\_COLOR 不可用
* 🎨 style: ruff format 自动格式化测试文件
* 🏷️ fix(types): 添加 get\_ocr\_service 返回类型标注及 is\_ready 方法
* ✅ test: 新增 120 个测试，提升 5 个模块覆盖率
* 🐛 fix: 修复 test\_bump\_version 编码问题、test\_update\_service 废弃API、export\_service HTML导出bug
* 🎨 style: 格式化 preprocess\_options\_widget 和 toolbar 代码行宽
* 🐛 fix(lint+types): 修复 ruff lint 错误与 mypy/pyright 类型检查问题
* 🐛 fix(types): 修复 64 项 Pyright 类型检查错误
* 🐛 fix(lint): 修复 187 项 ruff 代码检查问题
* 🎨 style: 全局代码格式化，统一行宽与 import 空行规范
* 🐛 fix(qa): 修正 subprocess.run 参数名 creation\_flags → creationflags
* 🐛 fix(build): 修复打包脚本多处不适配问题
* 🐛 fix(preferences): 初始化 OCRPreferences 单例，修复设置选项无法持久化的 bug
* ✨ feat(ui): 识别成功后按钮变为「重新识别」，加载新图片时恢复「开始识别」
* 🐛 fix(ui): 块类型模式下 bbox 根据置信度着色并标记低置信度
* 🔧 chore(log): 控制台日志开发环境设为 DEBUG，打包环境保持 WARNING
* 🐛 fix(pipelines): 修复 TABLE\_RECOGNITION/FORMULA\_RECOGNITION 预热失败 — 将枚举转换为字符串后再传给 get\_or\_create\_pipeline
* 🐛 fix(ocr): 修复预处理后 bbox 偏移 — 用 dict.get 替代 getattr 提取 doc\_preprocessor\_res
* ✅ test: 同步测试与代码变更
* 🐛 fix(tests): 同步测试断言到当前代码
* ♻️ refactor(tests): 重组测试目录结构与 src/vibeocr/ 镜像对齐
* docs: 添加按管道独立选项持久化设计文档
* 🐛 fix: 使用 doc\_preprocessor\_res\['output\_img'] 替换拼接可视化图
* 🐛 fix(toolbar): 修复截图界面悬浮工具栏变透明的问题
* ♻️ refactor(settings): 预加载管道复选框从注册表动态生成，移除硬编码映射
* ♻️ refactor(ui): 截图识别面板从管道注册表动态生成按钮，移除硬编码配置和更多按钮
* 🐛 fix(deps): 恢复 nvidia-cublas-cu12 依赖修复 GPU 推理失败
* test: 新增字段序列化往返和持久化测试
* test: 添加管道注册表端到端测试
* refactor(ocr): 改造 OCRService 使用注册表进行管道分发
* ✨ feat(pipelines): 创建表格识别管道 TableRecognitionOptions 和 TABLE\_RECOGNITION\_SPEC
* docs: 管道注册表模式实现计划
* docs: 管道注册表模式设计文档
* refactor(preview): 用 \_compute\_scale\_factor 替代 \_compute\_display\_rect
* docs: 坐标映射层重构设计文档
* docs: 坐标映射层重构实施计划
* refactor(screenshot): 清理废弃的 \_device\_pixel\_ratio 属性
* refactor(canvas): InlineEditCanvas 改用 ScreenCoordinateMapper
* refactor(window-detector): 改用 ScreenCoordinateMapper，添加结果裁剪
* refactor(magnifier): 放大镜改用 ScreenCoordinateMapper，尺寸改为 121px
* refactor(screenshot): 集成 ScreenCoordinateMapper 到 ScreenCaptureOverlay
* 🐛 fix(ocr): 归一化 content\_list bbox 坐标并提取显示矩形计算
* 🐛 fix: 修复退出崩溃、重复日志、AttributeError 及机器码重复检测
* 🐛 fix: 增强预加载与 Worker 通信的健壮性
* 🎨 style: ruff format 全量格式化与依赖版本升级
* 🐛 fix(deps): 保留 pyproject 版本上界约束，仅更新下界
* ♻️ refactor(ocr): 完成 PaddleX → PaddleOCR 3.x 迁移
* refactor(ocr): 迁移 PaddleX → PaddleOCR 3.x
* test: 更新测试以反映 PADDLEOCR\_VL 枚举和 MineRU 显示名变更
* style: 修复 Pyright 类型安全问题
* chore: 添加 pymupdf 依赖
* 📝 docs: 添加 PDF 处理标签页实现计划
* 📝 docs: 添加PDF处理标签页设计文档
* ✨ feat(tool-properties-bar): 添加填充色按钮、链接按钮和透明度滑块控件
* 📝 docs: 添加填充颜色与透明度控制实现计划
* 📝 docs: 添加截图工具填充颜色与透明度控制设计文档
* 🎨 style(toolbar): 启用边缘工具栏圆角显示
* 🐛 fix(toolbar): 修复截图工具栏调色盘黑色背景问题
* ✨ feat(inline-toolbar): 添加选择工具按钮
* 🐛 fix(toolbar): 启动时恢复位置后执行边缘检测，修复贴边不自动隐藏
* test: WindowDetector.detect\_at 坐标转换和缓存测试
* docs: 截图界面窗口识别框选功能实施计划
* docs: 截图界面窗口识别框选功能设计文档
* chore: 清理计划和设计文档
* refactor: 删除设置页的下载模型按钮
* refactor: 安装成功后不再弹模型下载窗口
* chore: 删除模型下载弹窗和 ModelDownloadService
* chore: 删除 model\_cache\_manager 和自定义管道 YAML 配置
* refactor: OCRService 超时判断改用 pipeline\_status
* refactor: 超时判断改用 pipeline\_status 替代 model\_cache\_manager
* docs: 管道识别成功记录设计文档
* ✨ feat(ui): 非图片文件自动锁定文档解析管道，表格识别输出 Markdown
* 🐛 fix(qrcode): 修复文字标签位置和预览高分屏适配
* 🎨 fix(qrcode): 提升二维码渲染清晰度
* 🐛 fix(shutdown): 修复退出时 QtWebEngine 崩溃 0xC0000409
* 🐛 fix(batch): 批量识别改为流式返回结果，完成一个即显示一个
* chore: 升级依赖并增强 upgrade\_deps.py CUDA 验证
* 🔥 chore: 删除废弃的 mineru\_worker.py — MineRU 管道已不再通过共享内存 worker 子进程处理
* refactor: OCRServiceSubprocess MineRU 管道从共享内存 IPC 改为直调 MinerUService 单例
* refactor: \_build\_ocr\_result 使用 normalize\_content\_list 正常化层
* 🔧 chore: bump pyside6 6.11.1, mineru 3.1.13, ruff 0.15.13, uv 0.11.14
* 🐛 fix: 恢复 overlay 为 viewport 子组件，修复 bbox 偏移问题
* refactor: 集中 DISCARDED\_BLOCK\_TYPES/normalize\_bbox 到 ocr\_result 模块，修复覆盖层坐标对齐
* refactor: 左侧编辑同步右侧改为增量更新避免全量重渲染
* 🐛 fix(ocr): 修复 MinerU 文档解析超时导致通信错误
* chore: 添加 MIT LICENSE，修正 ruff target-version 为 py313
* chore: 将 docs/ 目录添加到 .gitignore
* chore: 清理 .gitignore，移除 .qoder 等 AI 工具缓存追踪
* build: 升级 mineru 3.1.11, nvidia-cudnn 9.22.0.52, uv 0.11.13
* refactor: 移除截图编辑工具栏中的裁剪按钮及相关代码
* refactor: 将截图属性条从主工具栏分离为独立面板
* refactor: 识别面板点击管道按钮直接触发识别，移除工具栏识别按钮
* style: 优化截图界面按钮布局和交互体验
* refactor: 工具栏和识别面板按钮改为纯文字，工具栏靠选区右下角定位
* style(toolbar): 不透明面板背景，移除工具栏阴影，统一识别按钮样式
* style(toolbar): 更新按钮样式为透明默认，工具栏高度 48px
* refactor(log): 降低日常操作日志级别 info→debug
* perf(startup): 延迟初始化 QWebEngineView，用 uuid.getnode 替换 wmic，延迟导入 OCRService
* refactor(log): 精简状态栏关键字过滤为关键里程碑
* refactor(log): 将非里程碑日志降级为 debug（managers/utils/views 层）
* refactor(log): 将非里程碑日志降级为 debug（workers 层）
* refactor(log): 将非里程碑日志降级为 debug（services 层）
* refactor(log): 将非里程碑日志降级为 debug（main\_window）
* perf(qrcode): 优化 QR 码渲染，使用动态 box\_size 和 NEAREST 重采样
* chore: 更新依赖
* refactor: 移除旧的 ScreenshotWidget 和 ScreenshotEditWindow
* style(toolbar): 切换为白色背景和浅色主题
* chore(deps): 更新项目依赖版本
* chore: 移除已过时的模型源测试
* refactor: 将 install\_dialog 迁移到 NetworkDetector
* refactor: 将 ocr\_service 迁移到 NetworkDetector
* refactor: 将 mineru\_service 迁移到 NetworkDetector
* refactor: 将 model\_download\_service 迁移到 NetworkDetector
* refactor: 废弃旧的网络检测函数，委托给 NetworkDetector
* test(qrcode): 添加格式切换和预览的行为测试
* chore: 添加 qrcode\[pil] 和 python-barcode 依赖
* test: 更新测试
* test(export): 添加导出服务单元测试
* chore: 移除 FilePreviewWidget（已合并到 PreviewWidget）
* refactor(main-window): 精简 MainWindow，将 OCR 委托给 SingleRecognitionTab
* refactor(tabs): 重构 BatchRecognitionTab 使用 BaseOcrTab 共享逻辑
* refactor(tabs): 为 BaseOcrTab 添加悬停同步、文本编辑和偏好设置
* refactor(tabs): 为 BaseOcrTab 添加 \_build\_content\_list 和 \_display\_result
* refactor(tabs): 为 BaseOcrTab 添加共享状态和管道路由
* test(widgets): 添加 PreviewWidget 单元测试
* refactor(widgets): 在 PreviewWidget 中创建 UnifiedBBoxOverlay
* chore(build): 删除旧的 lint.py 脚本
* test: 添加多页 PDF 结果结构的集成测试
* refactor: 用共享 mime\_types 模块替换内联 MIME 映射
* refactor: 重写 \_build\_ocr\_result，修复纯文本提取和 TextBlock
* refactor: 移除菜单栏，添加关于标签页和键盘快捷键
* refactor: 在 main.py 中使用 **init**.py 的动态版本
* docs: 添加初始 CHANGELOG.md
* refactor(settings): 清理设置页面 — 移除废弃功能，重新设计为左右分栏
* refactor(settings\_manager): 移除 LLM/模板桥接，仅保留预加载
* refactor(config\_manager): 移除 LLM 配置和模板方法
* refactor: 移除废弃模型（ExtractionOptions、ExtractionTemplate、LLMConfig）
* chore: 添加导出配置和报告文件
* test(widgets): 添加 \_build\_block\_html 和 \_build\_text\_blocks\_html 标题属性的 TDD 测试
* test: 更新双 WorkerManager 架构的测试
* refactor(ocr\_service): 移除 \_recognize\_document 和 DOCUMENT\_PARSING 分支
* refactor(worker): 从 PaddleX worker 批量逻辑中移除 MinerU/DOCUMENT\_PARSING
* refactor(worker): 用 sys.meta\_path 导入钩子替换 torch 桩
* refactor(worker): WorkerManager 支持可配置的 worker\_module
* refactor(worker): OCRWorkerProcess 支持可配置的 worker\_module
* refactor(mineru): 用 python -m 替换 shutil.which 调用 mineru-api
* refactor(env): 将 OCR\_DEPENDENCIES 拆分为 PADDLE/MINERU 组
* docs: 删除部分文档
* chore: 清理所有 PaddleOCR-VL 和 GPU 相关残留引用
* chore: 切换 paddlepaddle-gpu 为 CPU 版，清理配置
* refactor(env): 简化为 CPU 安装，新增 MinerU 依赖
* refactor(services): 清理 GPU 代码和已删流水线引用
* refactor(ocr-service): 移除 GPU 代码和已删流水线，简化为 CPU 模式
* refactor(options): 移除 PaddleOCR-VL/PP-StructureV3 选项，新增 MineRU 选项
* refactor(config): 引入统一配置管理器并重构各配置模块
* chore(reports): 删除代码质量检查报告文件
* refactor(code): 优化代码结构和类型注解
* refactor(core): 重构代码结构和清理冗余代码
* refactor(editor): 优化多种标注功能和撤销堆栈支持
* refactor: 简化进度回调机制并优化UI组件
* style: 调整UI
* refactor(log): 优化日志表格显示，添加来源字段支持
* refactor(ocr): 优化worker进程重启和预热逻辑
* refactor(ui): 优化UI组件布局与日志状态处理
* style: 代码格式化
* chore: 更新 ruff 配置添加 pre-commit 忽略规则
* chore: 添加 pre-commit 钩子配置
* refactor(core): 优化模块导入顺序和类型注解
* chore(deps): 集成代码质量工具并完善配置
* refactor(views): 从 main\_window 提取 ClipboardController
* refactor(views): 使用 SettingsPageController 处理设置页面逻辑
* refactor(views): 使所有 OCR 标签页继承自 BaseOcrTab
* refactor(views): 在 main\_window 中使用 SubprocessManager
* refactor(phase1): 在 services 和 views 中使用 SingletonMeta 和 WindowsColors
* refactor(core): 重构信息抽取工作线程为批处理基类继承
* refactor(worker): 实现批量队列管理器延迟初始化
* test(extraction): 添加集成测试
* refactor(ui): 本地化批量识别相关界面文本
* test(batch\_recognition): 添加全面的集成测试
* test(test\_batch\_recognition): 添加批量识别集成测试
* refactor(ocr): 优化 OCR 子进程服务管理和主进程集成
* refactor(env\_manager): 移除便携式 Python 支持相关代码
* refactor(main\_window): 移除旧的 OCR 任务及相关测试代码
* refactor(utils): 优化 Markdown 缩进处理逻辑
* test: 添加 markdown 渲染集成测试
* chore: 将 .vibeocr 缓存目录添加到 gitignore
* docs: 初始化项目文档
* docs: 在 README 中添加测试说明
* test: 添加 MainWindow 集成测试
* test: 添加 ScreenshotWidget 测试
* test: 添加 PreviewWidget GUI 测试
* test: 添加 OCRTask 线程测试
* test: 添加 OCRService 单元测试
* test: 添加共享 pytest fixtures
* chore: 使用 pytest 搭建测试环境

## \[0.1.6] - 2026-06-26

### Added

* 项目初始化
* 截图 OCR 识别功能
* PaddleOCR 集成
* MinerU 文档解析集成
* 批量识别功能
* 应用设置页新增「环境维护」分组（重装 Python/依赖按钮）
* BackendChoiceDialog 透传 reinstall\_python
* InstallWorker 支持 reinstall\_python + 进度日志镜像
* reinstall\_embedded\_python 强制删除后重装
* 交互式菜单新增「仅打包当前版本」选项

### Changed

* 安装/下载日志接入 logging（替代 print）
* torch 镜像源修正、移除无调用方安装入口
* ruff/pyright 全量清零 + 版本测试永久免疫 bump
* 统一 LF 换行符（.gitattributes）
* 发版时自动同步 uv.lock

### Fixed

* updater 未绑定变量告警
* PDF 文字层旋转页面坐标错位
* 推理硬件误报 + 预热输出设备信息 + 第三方库降噪
* \_install\_paddle\_stack 兼容打包环境键名（KeyError）
* uv.lock 路径支持环境变量隔离
* 方案 A 打包误删 Qt6Qml/Quick 导致 QtWebChannel 加载失败
* 发版 uv.lock 版本号滞后漂移
* 打包进程递归卡死 / 体积臃肿 / 安装入口缺失 / 路径解析错误

