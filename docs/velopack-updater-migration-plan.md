# VibeOCR Classic：Velopack 更新器迁移实施方案

## 1. 结论

VibeOCR Classic 采用 Velopack 1.2.0 Python SDK、同版本 `vpk` CLI 和现有 PyInstaller
`onedir` 产品闭包。迁移前先把 production mutable data 从应用内容目录分离，再通过双格式 Release
把旧客户端引导到 Velopack Setup。本迁移版本继续打包 legacy ZIP、独立 `updater.exe` 和
`update_replacer.py`，仅供更老客户端入站升级到 bridge；bridge 自身不再用旧链出站更新。删除旧资产
必须等至少两个正式 bridge 版本且满 90 天后另行实施。

这是三个产品中的第三实施顺位。原因不是 Velopack 不适用，而是 Classic 的 `data/` 还在安装根，
其中包含设置、缓存、输出和已安装 Runtime；数据边界迁移必须先于 `current/` 整体替换。

启动健康失败自动回退不是 required。真实冻结启动、组件闭包、数据保留、更新前失败不损坏旧版本仍是 required。

## 2. 固定决策

| 项目 | 决策 |
|---|---|
| Pack ID | `VibeOCRClassic` |
| 安装根 | `%LocalAppData%\VibeOCRClassic`（Velopack 默认） |
| 稳定数据根 | `%LocalAppData%\VibeOCRClassicData`，与安装根完全分离 |
| Channel | 默认 `win`，feed 为 `releases.win.json` |
| Feed | `https://github.com/FelixJI/vibeocr-classic/releases/latest/download/` |
| Package | 首版 full nupkg，不生成 delta |
| 新用户入口 | `VibeOCRClassic-win-Setup.exe`（按真实 `vpk@1.2.0` 输出） |
| 旧用户入口 | 桥接窗口继续发布 `VibeOCR-Classic-v{version}-win64.zip` |
| 代理 | 保留 domestic/overseas 候选顺序，增加显式 forward proxy 验收 |
| 健康回退 | 非强制；删除启动失败自动恢复旧目录的 required smoke |

稳定数据根仍由 Classic 自己拥有；Backend/Protocol/Runtime Installer 的 authority、component lock 和
profile 语义不变。Velopack 只替换产品程序闭包，不接管 Runtime 安装与模型缓存。

## 3. 目标 Module 与 Interface

保留 `vibeocr.classic.pyside.update` 作为 Qt Adapter，但它只依赖一个 UI-free Interface：

```python
class UpdateCoordinator(Protocol):
    async def check(self) -> UpdateCheckResult: ...
    async def download_and_apply(
        self,
        progress: Callable[[int], None] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> UpdateApplyResult: ...
```

`UpdateCheckResult` 表达 latest/available/not-installed/fetch-failed 与版本、release notes；
`UpdateApplyResult` 表达 downloaded/apply-started/cancelled/failed。portable/not-installed 返回
`setup-bridge` apply mode，先确认稳定数据迁移 marker，再经同一 Interface 下载并启动 Setup；installed
运行态只调用 Velopack apply。Qt 对话框不再知道 GitHub asset、
ZIP/SHA 文件、ready/health marker 或 backup 目录。

生产 Adapter 为 `VelopackUpdateCoordinator`，测试 Adapter 为 fake/本地 feed。代理 Adapter 与
File Toolbox 相同策略但不共享源码：

- direct 或 prefix 使用 Python `HttpSource`，base 指向 GitHub `releases/latest/download/`；
- `network_type=domestic` 为 prefix-first，`overseas` 为 direct-first；
- standard forward proxy 通过 `HTTP(S)_PROXY`/`NO_PROXY` 的 loopback CONNECT 测试确认；
- 如果 SDK 不使用 forward proxy，只把 feed/package 用现有 `httpx` transport 物化到本地 source；
  Velopack 仍独占版本选择、校验、apply、restart。

## 4. 数据迁移是独立前置 PR

legacy production 布局把 `data/`、`runtimes/`、`models/`、`output/`、`config/` 五个实际兄弟根放在
`install_root`。目标是把它们分别复制到 `%LocalAppData%\VibeOCRClassicData` 下的同名稳定根，旁路 profile
继续使用测试显式根，不污染正式数据。

迁移规则：

1. 新增 `DataRootResolver` Adapter；开发/测试可注入，production 固定返回稳定数据根。
2. legacy 布局首次启动时，如果稳定根为空且上述五个源中任一存在，执行 copy-only 迁移。
3. 先优雅停止 Supervisor/Runtime，再复制到临时 sibling，验证 runtime manifest、component identity、
   必需设置文件和文件闭包，最后原子提升为稳定根。
4. 首版只复制、不删除任何 legacy 源；本工作包不提供自动清理，后续也只能显式清理。
5. 若目标空间不足、复制取消或验证失败，删除临时目标并继续使用 legacy data；不得进入 Setup 迁移。
6. 写入 schema-versioned `data-location.json`，重跑幂等；不要以目录名猜测迁移完成。

这里验证的是重要用户数据边界，不是“新版本启动健康回退”。即使不做自动回退，也不能半迁移 Runtime。

## 5. 分 PR 实施

### PR 1：Python SDK、代理和大产品闭包 Spike

1. 用最小 PyInstaller onedir 证明 Python hook、Setup/Portable、N→N+1。
2. 用 Classic 真实 staged product root 打一个 full nupkg，确认 Backend wheel、Protocol lock、Python/runtime
   archives 和产品 manifest 都在同一 package 闭包中。
3. direct、prefix、CONNECT forward proxy 分别覆盖 feed 与 full package。
4. forward proxy 不生效时验证 remote materialize→local source fallback。
5. 验证 full package 超过现有体积时的进度、取消、磁盘不足与 checksum failure。
6. 验证应用退出前 Supervisor、Runtime Installer、WebEngine 子进程均已优雅停止。

退出条件：真实产品闭包能安装并启动；代理证据自动化；fallback 不重写 nupkg；数据目录不在 `current/`。

### PR 2：稳定数据根

文件级变更：

- 修改 `apps/vibeocr-pyside/src/vibeocr/classic/app_paths.py`；
- 修改 `update_config.py`、runtime installation、settings/output/log path 消费方；
- 新增 `data_migration.py` 与独立 migration result model；
- 修改 `main.py`/启动编排，在 Runtime 启动前完成 resolver 和必要迁移；
- 增加 legacy data、空目标、已迁移、空间不足、取消、失败重跑测试；
- 修改冻结 artifact smoke，断言用户数据与 Runtime 位于稳定外部根。

该 PR 仍使用旧 updater，目的是先把程序与可变数据边界分开。

### PR 3：新 Interface 与 Velopack Adapter

文件级变更：

- 新增 `services/update_coordinator.py`、`services/velopack_update.py`、`services/update_transport.py`；
- 修改 `pyside/update.py` 只负责 Qt 对话框/线程适配；
- 最早入口调用 `velopack.App().run()`，必须早于 PySide6、Supervisor 和 Runtime 初始化；
- 在 `apps/vibeocr-pyside/pyproject.toml` 与锁定环境加入 `velopack==1.2.0`；
- 修改 PyInstaller build 收集 Velopack native library；
- 让 legacy/not-installed 与 installed 两种运行态都实现同一 Coordinator Interface。

现有 `services/update_service.py` 可临时作为 legacy Adapter，但不再被 UI 直接调用。

### PR 4：双格式构建与桥接运行态

修改 `scripts/build-release.ps1`：

1. 保持组件解析、component lock、frontend protocol lock 与 Runtime Installer 绑定顺序；
2. 现有 `package_product_release.py` 继续产生唯一 staged product closure；
3. 同一 staged root 交给固定版本 `vpk pack --mainExe VibeOCR.exe`；
4. vpk 输出进入隔离 build 目录，只复制声明资产到 artifacts；
5. 同时生成 legacy ZIP，供旧 updater 更新到同版本 bridge binary；
6. Setup 生成 SHA-256 sidecar，SPDX SBOM 覆盖所有发布资产；
7. CD 不执行 vpk、不重建 feed、不重新解析 Backend/Protocol。

release build 的 Python 工具与 runtime 依赖由 `scripts/requirements-build.in` 声明，并由文件头记录的
`uv pip compile` 命令生成带 hash 的 `scripts/requirements-build.lock`；构建只在
`build/release/release-venv` 执行 `uv pip sync`，不向 automation 环境或系统 Python 安装依赖。

真实探针命令 `dnx --yes vpk@1.2.0 -- pack --channel win --runtime win-x64 ...` 在 Windows
实际生成 `VibeOCRClassic-win-Setup.exe`、`VibeOCRClassic-win-Portable.zip`、版本化 full nupkg、
`releases.win.json`，并额外生成不发布的 `assets.win.json`/`RELEASES`。以下 exact set 以探针结果为准：

- 现有 legacy ZIP、ZIP `.sha256`、`component-lock.json`、
  `frontend-protocol-lock.json`、`SBOM.spdx.json`；
- `VibeOCRClassic-{version}-full.nupkg`；
- `VibeOCRClassic-win-Setup.exe` 与 `.sha256`；
- `VibeOCRClassic-win-Portable.zip`；
- `releases.win.json`。

同步修改 `.ci/project.json`、`verify_release_assets.py`、`verify_pyside_artifact.py`、发布 contract 测试。
bridge portable/not-installed 运行态先从 latest feed 固定目标 `version`，随后把 direct/prefix URL 改写为
`/releases/download/v{version}/...`，下载同一 tag 的 Setup 与 SHA-256 sidecar，校验 Content-Length 和
SHA-256 后才启动。迁移失败、取消或空间不足时返回可诊断失败，不下载 Setup、不退出；旧目录和五个
legacy 数据源始终保留。

### 后续独立清理（不属于本迁移工作包）

1. 连续发布至少两个双格式正式版本，证明旧版本可跳到任一 bridge release 后迁移 Setup。
2. 桥接窗口必须同时满足至少两个正式版本和 90 天。
3. 窗口结束后删除 `scripts/updater_main.py`、`scripts/update_replacer.py`、独立 updater PyInstaller 阶段。
4. 删除 `update_config` 的 ZIP/SHA URL builder、`update_service` 的 GitHub Release parsing/download、
   ready/progress/startup health/backup 协议和相应测试。
5. `verify_pyside_artifact.py` 删除启动失败回退 smoke，新增 Velopack 本地 feed 更新 smoke。
6. 保留真实 frozen startup、Runtime inspect、PDF/WebEngine、component closure smoke。

## 6. 必须通过的验收

- legacy `data/` →稳定 data root 迁移成功、取消、空间不足、失败重跑均不删除源。
- 稳定 data root 的 settings、runtime、model cache、output 在 Setup 与 N+1 更新后保持不变。
- direct/prefix/forward proxy 覆盖 feed + full nupkg；domestic/overseas 候选顺序保持现有语义。
- 损坏 package、下载取消、并发锁、磁盘不足都不会破坏当前可启动版本。
- legacy ZIP→同版本 Setup→下一版本 full nupkg 的端到端链路通过。
- 安装/更新后真实冻结入口通过 Supervisor T6、Runtime、PDF、WebEngine smoke。
- 新版启动失败无需自动回退，但安装失败后旧 legacy 或旧 installed 入口仍可启动。
- Release 精确资产、component identity、SBOM、SHA 与 candidate source SHA 一致。

精确验证入口按 `.ci/project.json` 执行：

```powershell
uv sync --frozen --group dev --group build
uv run python -m pytest
uv run python scripts/verify_component_release_input.py --release-input build/automation/release-input
uv run python scripts/automation.py ci --event pull_request --source-sha <HEAD_SHA>
```

最后一条执行 `.ci/project.json` 的完整 bootstrap、quality、e2e、release build/smoke。组件解析与正式
build/smoke 需要 canonical automation 注入真实 release input；本地没有这些输入时必须
报告未执行，不能用 mock 结果冒充发布门禁。

## 7. 明确不做

- installed/Setup 出站路径不做启动健康失败自动回退、首版 delta、静默强更或跨仓共享 updater；
  legacy 健康握手代码仅在兼容窗口服务更老客户端入站，且不再是失败回退 required smoke。
- 不让 Velopack 在线解析或替换 Backend/Protocol 组件身份。
- 不在迁移成功时自动删除 legacy data 或 legacy 安装目录。
- 不修改公共 `scripts/automation_core.py`，不在 CD 重建任何资产。
