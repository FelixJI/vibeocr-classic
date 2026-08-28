# VibeOCR Classic Velopack Portable 更新方案

## 当前状态

Classic 只发布 Velopack Portable。用户解压 `VibeOCRClassic-win-Portable.zip` 后从稳定
`RootAppDir` 启动；`current` 是 Velopack 可替换的只读内容目录，产品拥有的配置、日志、缓存、
模型与 Runtime 均位于 `RootAppDir/state`。不得把 `current` 当成稳定产品根，也不得回退
LocalAppData 或系统 Temp 保存产品状态。

旧 ZIP updater、独立 `updater.exe`、replacer、Setup 下载桥接与公开 Setup 资产均已删除。

## 稳定接口与迁移

- `VelopackRootResolver` 只在 `current/sq.version`、根级 `Update.exe` 与 `.portable` marker
  一致时把 frozen executable 映射到稳定 `RootAppDir`；含糊布局 fail closed。
- `AppPaths` 是所有产品可变目录的唯一接口，逐段拒绝 junction/symlink/reparse point。
- 旧 `current/state` 与预发布 `state/runtimes` 采用 copy/逐文件验证/atomic promote；源目录
  保留，目标存在时幂等复用。唯一正式 Runtime 路径是 `state/runtime`。
- 环境状态根 override 仅供 artifact smoke，必须同时满足显式 test mode、随机 nonce 与
  目标目录名绑定；普通启动环境不能重定向状态根。
- UI 只依赖 `UpdateCoordinator`。Runtime Installer 与 App Update 共享
  `ProductMaintenanceCoordinator`：更新开始前取消并等待 installer 子进程终态，持有更新
  owner 时 ensure/retry/repair fail closed，所有成功、失败和取消出口释放 owner/文件锁。

## 运行态

1. Portable 使用同一 Velopack `check/download/apply/restart` 流程；full/delta nupkg 和 feed 是机器
   更新资产，不是用户安装入口。
2. 更新只替换 `current`，`RootAppDir/state` 原样保留；移动整个 Portable 根后重新解析新位置。
3. 网络、校验、取消、空间或 installer 终态等待失败时保留当前版本并 fail closed。

## 发布与验收契约

正式候选精确包含：

- `VibeOCRClassic-{version}-full.nupkg`
- `VibeOCRClassic-{version}-delta.nupkg`（仅正式新版本候选；上一正式版到当前版单跳）
- `VibeOCRClassic-win-Portable.zip`
- `releases.win.json`
- `component-lock.json`
- `frontend-protocol-lock.json`
- `SBOM.spdx.json`

构建先验证 PyInstaller onedir 和 offline base Runtime，再以固定 Velopack CLI 生成真实旧、新
两个版本。新版本高于最新正式 Release 时，构建下载并校验上一 full 作为唯一 delta base；客户端
最多使用一个 delta，无本地 base、跨版本或重建失败均由 Velopack 回退当前 full。Portable E2E
分别覆盖 full fallback 与预置上一 full base 后的 delta 请求，通过 loopback HTTP feed 完成 check/download/apply，
等待重启后的新版本写出证据，并验证 `current` 被替换、`state/{config,logs,cache,models,runtime}`
保留；随后停止 feed、移动整个根并离线重启。静态 feed 或 mock 只用于单元契约，不能替代该 E2E。

`vpk` 生成 delta 后会在中间 feed 带入历史 full；构建必须先验证它与计划 base 一致，再把正式
`releases.win.json` 归一化为“当前 full + 当前 delta”。上一正式 full 不在当前 Release 重复发布或引用。
这既保留新客户端的 delta 选择，也让迁移前 forward-proxy materializer 看到唯一 full 并安全降级。

上线分为两个相邻 Release：迁移前已发布客户端的
`MaximumDeltasBeforeFallback=-1`，因此首个包含本实现的能力版本即使发布合法 delta，现网旧客户端也会
选择 full；该版本先建立 `MaximumDeltasBeforeFallback=1` 的客户端基线。再下一版开始，真实上一正式版
客户端才会请求单跳 delta。构建中的合成旧版本 Portable E2E 用于证明候选代码的 full/delta 选择、应用与
重启契约，不冒充迁移前公开版本会请求 delta 的证据；首个能力版本仍必须验证 full fallback。

完整门禁以 `.ci/project.json` 的 quality、e2e、release build 与 release smoke 为准。
