# Printk、Console 与 Banner 编码约束

本页对应 [`model/objects/printk.spec`](../../model/objects/printk.spec) 和
[`model/objects/early_console.spec`](../../model/objects/early_console.spec)。本轮只冻结编码
关系；M5 在不扩展冻结模型的前提下实现这些消息级机制。

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

## EarlyConTable 链接边界

`EarlyConTable.Link` 冻结为 Linux sibling 的链接期注册机制：

- `drivers/tty/serial/earlycon-riscv-sbi.c` 中
  `EARLYCON_DECLARE(sbi, early_sbi_setup)` 生成 name 为 `sbi`、setup 为
  `early_sbi_setup` 的 `struct earlycon_id` 条目；
- `include/linux/serial_core.h` 把该条目放入 `__earlycon_table` input section；
- `include/asm-generic/vmlinux.lds.h` 的 `EARLYCON_TABLE()` 把该 section 及其边界符号纳入
  最终 KernelImage。

正式模型当前只保留由此产生的
`EarlyConTable.contains("sbi", SbiConsole)`，并固定假设构建时启用
`CONFIG_SERIAL_EARLYCON_RISCV_SBI`。本轮不为该假设新增 Config 对象，也不展开 Linux 的
其他 earlycon backend。表遍历、section 起止地址、`struct earlycon_id` 布局、setup
函数指针和链接器布局均属于 coding/impl，不进入 model。

## SBI capability 与 Console transport 边界

Linux sibling 的生产者—消费者映射固定为：

1. `SbiCapability.Enable ↔ arch/riscv/kernel/sbi.c::sbi_init()`：先完成 SBI
   specification version 探测；仅当版本
   至少为 2.0 且 `sbi_probe_extension(SBI_EXT_DBCN) > 0` 时，才令
   `sbi_debug_console_available` 成立。Model 以
   `sbi_dbcn_available(SbiCapability)` 表示该有效输入，以
   `sbi_v01_console_available(SbiCapability)` 合并表达 legacy SBI 与
   `CONFIG_RISCV_SBI_V01` 共同形成的有效 fallback 输入；
2. `SbiConsole.Enable ↔ drivers/tty/serial/earlycon-riscv-sbi.c::early_sbi_setup()`：优先在
   DBCN 可用时选择
   `sbi_dbcn_console_write`；否则仅在 `CONFIG_RISCV_SBI_V01` 启用时选择
   `sbi_0_1_console_write`；两者均不可用时返回 `-ENODEV`，不得注册 Console。

固定 sibling 的默认 `.config` 关闭 `CONFIG_RISCV_SBI_V01`，因此默认成功轨迹只能选择
DBCN；v0.1 只作为替代构建配置下的测试路径。Model 中的
`sbi_console_uses_dbcn(SbiConsole)` 与 `sbi_console_uses_v01(SbiConsole)` 只记录上述 setup
完成后的互斥选择结果；它们不得代替前述 availability 输入。SBI 原始版本、
`SBI_EXT_DBCN` probe 结果、Config 对象、
`struct console.write` 函数指针、DBCN 逐次写入循环、v0.1 逐字符写入以及运行时写错误都留在
coding/impl 层，不进入本轮 model。M5 Rust 前缀只实现 DBCN，不实现 v0.1 fallback。

格式化、ring buffer、记录序号、flush/replay、锁和 SBI 调用属于 coding/impl 机制。
Checkpoint debugcon 继续作为独立的极早期观测通道，不经过上述 Printk/Console 链，也不
因 Banner 生命周期而改变。model 不新增 `Printk.Action::Emit`、
`EarlyConsole.Action::Write(message)` 或消息级 predicate；只有未来需要验证缓冲、丢弃、
replay 或多 Console cursor 时，才引入相应抽象行为。
