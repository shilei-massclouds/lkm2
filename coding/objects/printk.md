# Printk、Console 与 Banner 编码约束

本页对应 [`model/objects/printk.spec`](../../model/objects/printk.spec) 和
[`model/objects/early_console.spec`](../../model/objects/early_console.spec)。本轮只冻结编码
关系，不修改 Rust 实现。

## Banner 提交

`Banner.Transition::Enable` 映射为通过 Printk 提交内核 banner，对应 Linux sibling 的
`pr_notice("%s", linux_banner)`。该提交发生在 `DtbBlob.Enable` 和
`EarlyConsole.Enable` 之前，因此 Console 尚未 Online 是合法状态。Printk 初始 Online 只
保证日志入口能够接受该记录；Console 后续注册后的实际可见性由实现层的缓冲与 replay
机制决定，不进入本轮 model。

## 输出链边界

实现关系固定为：

```text
println! → Printk → registered Console → selected backend
```

当前 registered Console 是 `EarlyConsole: ConsoleType`，selected backend 是其从
`BootCommandLine` 与 `EarlyConTable` 查询并保存的 backend。Console 进入 Online 时必须已
同时建立 backend binding 和 `printk_console_registered(Printk, EarlyConsole)`。

格式化、ring buffer、记录序号、flush/replay、锁和 SBI 调用属于 coding/impl 机制。
Checkpoint debugcon 继续作为独立的极早期观测通道，不经过上述 Printk/Console 链，也不
因 Banner 生命周期而改变。model 不新增 `Printk.Action::Emit`、
`EarlyConsole.Action::Write(message)` 或消息级 predicate；只有未来需要验证缓冲、丢弃、
replay 或多 Console cursor 时，才引入相应抽象行为。
