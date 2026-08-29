# EarlyBoot 页表与停驻编码约束

本页对应
[`model/phases/start_kernel/early_boot.spec`](../../../model/phases/start_kernel/early_boot.spec)，
实现映射为 [`impl/phases/start_kernel.rs`](../../../impl/phases/start_kernel.rs)。

当前实现覆盖 `setup_bootmem` 与 `setup_vm_final`。它接收 EarlyBoot Enter 已发现的
`MemBlockMemory`、只读 DtbBlob、DTB 物理起点，以及 VM 已提交的 KernelImage 物理范围，调用
`MemBlock::setup_bootmem` 完成 Reserved 与父 MemBlock。调用点严格位于 EarlyConsole 注册/回放
之后。

成功后当前可执行前缀在 `SwapperPageTable` Online 处以 WFI 循环停驻；失败路径使用独立的
spin fail-stop。Scheduler Enable、Unmask 与 BootSetup 仍只有正式模型定义，前两者在模型的
EarlyBoot Enter 中位于 SwapperPageTable 之后。
