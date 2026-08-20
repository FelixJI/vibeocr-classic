# Classic Runtime maintenance 契约

Classic 的 Runtime Installer 请求与状态投影由 UI-neutral seam 统一管理：

- `runtime.maintenance.v2` 是产品必需 capability；组件或下载源 selection 存在而 v2 不可用时
  fail closed。
- ensure 与 retry 共用 selection builder，严格保留 omitted、显式空 component 集与非空 source
  集的差异。
- UI 同时展示 requested/effective source ids；设置变化仅影响下一次 operation，运行中的请求按
  启动快照执行。
- `ProductMaintenanceCoordinator` 串行化 RuntimeMaintenance 与 AppUpdate。更新必须先取消并等待
  真实 installer operation 到达 terminal；更新持有 owner 时 ensure、retry、repair 均拒绝启动。
- offline base release smoke 在黑洞代理下执行 base-only ensure，并真实启动 Supervisor、显式选择
  RapidOCR 处理固定 PNG，再通过 PDF supervisor 完成一页 PDF 的 open/model/thumbnail；随后重复
  ensure 与模拟 app apply，验证 `state/runtime` 幂等复用。

权威 capability 与 requested/effective 字段来自绑定的 Protocol/Backend release；Classic 不重新
解释 source kind，展示后端返回的 effective truth。
