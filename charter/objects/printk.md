# Printk、Console 与 Banner 章程

## 生命周期与 ownership

`Printk` 是 Kernel-owned 的静态日志子系统，初始即为 Online，不具有 transition、消息
Action 或独立 buffer 对象。该状态只表示静态日志入口能够接受记录，不保证任何 Console
已经注册、记录已经输出，或某个 backend 已经完成写入。

`Banner` 同样由 Kernel 持有，初始为 Ready，且只有一次
`Enable: Ready → Online`。Banner Enable 只要求 Printk 已经 Online；它不依赖 Console，
也不把 banner 文本或写入行为放入 model。

## Console 注册边界

`EarlyConTable` 是 `KernelImage` 持有的静态 backend 注册表，初始为 Base，且只有一次
`Link: Base → Ready`。Link 必须建立
`EarlyConTable.contains("sbi", SbiConsole)`；Ready invariant 必须持续保持该注册事实。
这个生命周期表示固定构建配置下链接表已经形成，不表示运行时执行了动态注册、表扫描或
backend setup。`KernelImage` 自身的 Loaded 初态和随后由 ArchHead 驱动的 ClearBss 生命周期
不因子对象 Link 而改变。

`SbiCapability` 是 Kernel 持有的 SBI 探测结果，初始为 Ready，且只有一次
`Enable: Ready → Online`。Enable 默认建立 `sbi_dbcn_available(SbiCapability)`；另一项输入
`sbi_v01_console_available(SbiCapability)` 同时抽象 legacy SBI 与构建配置共同形成的有效
fallback。Online 只表示探测已经完成，不要求任何 transport 可用，也不禁止 DBCN 与 v0.1
同时可用。

`SbiConsole` 是 Kernel 持有的 SBI backend，初始为 Ready，且只有一次
`Enable: Ready → Online`。Enable 只在 `SbiCapability` 已经 Online 且至少一项 availability
事实成立时继续，默认建立 `sbi_console_uses_dbcn(SbiConsole)`。Online invariant 要求 DBCN
与 v0.1 恰好选择一个、每个选择都有对应的上游 availability 依据，并且仅在 DBCN 不可用时
允许选择 v0.1。该对象不得自行建立 availability 事实。

`EarlyConsole` 是通用 `ConsoleType` 的当前实例。其 Enable 先从 `BootCommandLine` 和
`EarlyConTable` 完成两次 binding，再静态驱动 `SbiConsole.Enable`，确保查询得到的 backend
已经 Online，最后原子提交 backend 字段、
`early_console_bound_from_registry(EarlyConsole, backend)` 与
`printk_console_registered(Printk, EarlyConsole)`。不得要求查询所得 backend 在 Enable
入口前已经 Online。正式表当前只含 `sbi → SbiConsole`，因此这里固定使用静态 drive；不得
把 binding 变量用作动态 signal target。

若命令行或注册表 binding 失败、SBI transport 缺失、同时选择两种 transport，或选择结果
没有上游依据，`EarlyConsole` 必须保持 Ready，且不得提交 backend 字段、backend binding 或
Printk Console
注册事实；后续 Scheduler Enable、interrupt Unmask 与 BootSetup 均不得执行。已经成功的
Banner、DtbBlob 或 `SbiCapability` 前序 drive 及其事实不回滚。只有未来出现多个共享特殊
early-console 协议的实例时，才引入派生的 early-console 类型。

Model 只描述 Printk 可用、Console 注册和 backend 绑定，不建模逐消息写入。格式化、ring
buffer、记录序号、flush/replay、锁、SBI 调用以及 Console cursor 都属于 coding/impl；只有
未来正式验证缓冲、丢弃、replay 或多 Console cursor 时，才评审抽象 Emit/Flush 行为。

临时模型映射：
[`model/objects/printk.spec`](../../model/objects/printk.spec) 与
[`model/objects/early_console.spec`](../../model/objects/early_console.spec)。
