# VibeOCR Classic Velopack 更新方案

## 当前状态

VibeOCR Classic 已完全切换到 Velopack。安装版通过 `UpdateManager` 检查、下载并应用 full nupkg；
免安装版不自动下载或启动 Setup，而是在界面中提示用户从 Release 获取 Setup 或 Portable。

旧 ZIP 更新器、独立 `updater.exe`、replacer、启动健康文件和 Setup 下载桥接均已删除。由于项目仍处于
开发阶段，不再保留旧版本自动升级到 Velopack 安装版的兼容窗口。

## 保留的稳定接口

- UI 只依赖 `UpdateCoordinator`，不直接操作网络或 Velopack SDK。
- `UpdateTransport` 负责 direct、GitHub URL-prefix 代理与 HTTP(S) forward proxy 路由。
- forward proxy 无法由 SDK 直接使用时，先流式下载并校验 feed/full nupkg，再交给 Velopack 的
  `HttpSource` 应用。
- 稳定数据根迁移独立于应用更新：旧数据 copy/verify/promote，源目录不自动删除，marker 保证幂等。

## 运行态

1. installed：检查 feed；有更新时由 Velopack 下载并 apply，重启后生效。
2. portable/not-installed：返回可诊断提示，不自动下载 Setup，不退出当前应用。
3. 网络失败、校验失败、取消或空间不足：保留当前版本与已完成的数据迁移状态。

启动健康回退不是强制能力，本方案不额外实现应用级 backup/rollback；Velopack 自身负责安装事务。

## 发布契约

正式候选精确包含：

- `VibeOCRClassic-{version}-full.nupkg`
- `VibeOCRClassic-win-Setup.exe`
- `VibeOCRClassic-win-Setup.exe.sha256`
- `VibeOCRClassic-win-Portable.zip`
- `releases.win.json`
- `component-lock.json`
- `frontend-protocol-lock.json`
- `SBOM.spdx.json`

额外资产 fail closed。构建先生成并验证 PyInstaller onedir 闭包，再由固定的 Velopack CLI 打包；
release smoke 同时验证精确资产集合和 feed 对 full nupkg 的版本、大小与摘要绑定。

## 验收

- installed 的 check/apply/progress/cancel/error 路径通过聚焦测试。
- direct、URL-prefix、HTTP forward proxy 和 HTTPS CONNECT 路径通过真实 loopback 测试。
- portable 路径不触发 Setup 下载或进程启动。
- 发布候选不含 legacy ZIP、独立 updater 或健康回退文件。
- PR 的完整 quality、release build、frozen smoke 与 release smoke 通过。
