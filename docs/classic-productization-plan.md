# Classic Portable 产品化与门禁

## 产品边界

Classic 是 Windows x64、Portable-only 的 PySide6 shell。应用内容位于 Velopack `current`，稳定
产品根由 `VelopackRootResolver` 确认，所有产品可变状态位于 `<RootAppDir>/state`。Backend 推理、
Runtime 安装和 Protocol schema 均来自经过 Release/attestation 验证的外部组件，不从邻仓源码构建。

正式用户资产只有 `VibeOCRClassic-win-Portable.zip`；full、相邻版本 delta nupkg 与 `releases.win.json` 是内置
Velopack 更新器的机器资产。Setup、legacy ZIP 与独立 updater 不进入候选。

## Runtime 产品契约

- 组件 resolver 选择最新正式 Backend；最新版本缺任一 required capability 时 fail closed，不回退。
- 唯一 Runtime store 是 `state/runtime`；预发布 `state/runtimes` 只作为保留源执行一次性迁移。
- `runtime.maintenance.v2`、component/source selection 与 requested/effective 回显是发布门槛。
- base-only 必须可从绑定 runtime pack 在黑洞代理下安装，并完成 Supervisor ready、RapidOCR 固定样例
  与基础 PDF 固定样例。full/model acquisition 与断网推理依赖具备生产 model manifest 的正式 Backend。
- Runtime maintenance 和 App Update 共享单 owner；apply 前必须取消并等待 installer terminal。

## CI 阶段

`.ci/project.json` 是唯一权威入口：

1. bootstrap 解析并安装正式组件输入；
2. quality 运行完整非 slow pytest；
3. e2e 验证组件资产与 attestations；
4. release build 验证裸 frozen 闭包，再生成干净的 VPK input；
5. release build 同时生成使用候选代码的合成 0.0.1 与候选版本 Portable，通过 loopback feed 完成
   full/delta check/download/apply/restart，验证稳定 state、Runtime 复用、断网搬移与外写入审计；
6. release smoke 对精确候选资产与 feed/full nupkg 绑定 fail closed。

fixture/mock 测试只证明局部 contract。只有运行两个实际 `vpk pack` 输出和冻结 executable 的门禁才称为
packaged Portable E2E；静态 ZIP/feed 检查不得替代它。

delta 采用两阶段上线：首个能力版本由迁移前客户端通过 full 更新并建立最多使用一个 delta 的客户端
基线；从其下一相邻版本开始，真实上一正式版才具备请求 delta 的能力。合成旧版本 E2E 是候选代码能力
门禁，不替代首阶段真实旧客户端的 full fallback 事实。
