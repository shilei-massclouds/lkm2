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

`EarlyConsole` 是通用 `ConsoleType` 的当前实例。其 Enable 先从 `BootCommandLine` 和
`EarlyConTable` 选择并保存 backend，再同时建立 backend binding 与
`printk_console_registered(Printk, EarlyConsole)`。Online invariant 必须持续保持这两个
事实。只有未来出现多个共享特殊 early-console 协议的实例时，才引入派生的 early-console
类型。

Model 只描述 Printk 可用、Console 注册和 backend 绑定，不建模逐消息写入。格式化、ring
buffer、记录序号、flush/replay、锁、SBI 调用以及 Console cursor 都属于 coding/impl；只有
未来正式验证缓冲、丢弃、replay 或多 Console cursor 时，才评审抽象 Emit/Flush 行为。

临时模型映射：
[`model/objects/printk.spec`](../../model/objects/printk.spec) 与
[`model/objects/early_console.spec`](../../model/objects/early_console.spec)。
