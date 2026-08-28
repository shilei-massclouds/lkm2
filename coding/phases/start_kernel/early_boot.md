# EarlyBoot 编码约束

本页对应
[`model/phases/start_kernel/early_boot.spec`](../../../model/phases/start_kernel/early_boot.spec)，
实现入口为 [`impl/phases/start_kernel.rs`](../../../impl/phases/start_kernel.rs)。

EarlyBoot 的固定运行顺序是：

1. 把固定 banner 记录到初始可用的 Printk FIFO；
2. 取得 ArchHead 发布的 DTB 物理/虚拟映射，严格校验 FDT 并复制唯一 bootargs；
3. 调用 `MemBlockMemory::derive_from_dtb`，完成 `parse_dtb` 对应的系统 RAM 发现；
4. 探测 SBI v2.0/DBCN capability；
5. 通过链接期 `EarlyConTable` 查询 backend，启用并注册 SbiConsole，回放 banner。

Memory 必须在 SBI probe 前成功，但不得在此处加入 KernelImage、DTB 自身或 DTB reservation
条目；这些属于随后的 `PagingInit::setup_bootmem`。任何一步失败都保持中断屏蔽并 fail-stop，
已经完成的前序值不回滚。

当前 Rust 只实现 DBCN，不实现模型保留的 SBI v0.1 fallback。BootCommandLine 固定最多 4096
字节，要求 NUL 结尾、无内嵌 NUL、UTF-8，并且恰好包含一个独立 `earlycon=sbi` token。backend
必须来自链接表唯一查询，不能因当前只有 SBI backend 而绕过 registry。

EarlyBoot 返回后的职责见 [`paging_init.md`](paging_init.md)。
