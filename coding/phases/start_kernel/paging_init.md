# PagingInit 编码约束

本页对应
[`model/phases/start_kernel/paging_init.spec`](../../../model/phases/start_kernel/paging_init.spec)，
实现映射为
[`impl/phases/start_kernel/paging_init.rs`](../../../impl/phases/start_kernel/paging_init.rs)。

当前实现只覆盖 `paging_init()` 的 `setup_bootmem` 前缀。它接收 EarlyBoot 已发现的
`MemBlockMemory`、只读 DtbBlob、DTB 物理起点，以及 VM 已提交的 KernelImage 物理范围，调用
`MemBlock::setup_bootmem` 完成 Reserved 与父 MemBlock。调用点严格位于 EarlyConsole 注册/回放
之后。

成功后当前可执行前缀仍在中断屏蔽状态以 WFI 循环停驻；失败路径使用独立的 spin fail-stop，
使优化构建也必须保留成功的 `setup_bootmem` 路径。模型中的 `FinalPageTable.Enable`
（`setup_vm_final`）、Scheduler Enable、Unmask 与 BootSetup 尚无 Rust 实现；不得因模型已定义
这些后续状态而在 coding 文档中声称运行时已经完成。
